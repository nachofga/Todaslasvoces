"""
Incidencias y disputas por usos no autorizados.

Al abrir una disputa con un audio como prueba, el sistema intenta identificarlo
de dos formas: por la huella exacta del fichero (si es el mismo que se entregó)
y por la marca de agua invisible (si lo han recortado, comprimido o mezclado).
"""
import hashlib
import logging

from django.db.models import Q
from django.utils import timezone

from ..models import Dispute, DisputeNote, Generation
from . import audio, ledger, watermark
from .notify import N_, notify, notify_staff

log = logging.getLogger("tlv")


def identify_audio(data: bytes, voice=None):
    """Devuelve {"match": "hash"|"watermark"|None, "generation": Generation|None, ...detalles}."""
    info = {"match": None, "generation": None, "sha256": hashlib.sha256(data).hexdigest()}
    gen = Generation.objects.filter(Q(sha_wav=info["sha256"]) | Q(sha_mp3=info["sha256"])).first()
    if gen:
        info.update(match="hash", generation=gen)
        return info
    try:
        arr, sr = audio.decode(data)
    except audio.AudioError:
        info["error"] = "No es un audio legible."
        return info
    qs = Generation.objects.exclude(watermark_id=None)
    # Primero contra todo; si no hay lectura clara y se conoce la voz, solo contra sus audios.
    attempts = [list(qs.values_list("watermark_id", flat=True))]
    if voice is not None:
        attempts.append(list(qs.filter(voice=voice).values_list("watermark_id", flat=True)))
    for candidates in attempts:
        if not candidates:
            continue
        result = watermark.detect(arr, sr, candidates=candidates)
        info.update(score=result["score"], rho=result["rho"], seconds=result["seconds"], method=result["method"])
        if result["found"]:
            info.update(match="watermark", generation=Generation.objects.filter(watermark_id=result["payload"]).first())
            break
    return info


def _summary(info):
    gen = info.get("generation")
    out = {k: v for k, v in info.items() if k not in ("generation",)}
    if gen:
        out.update(
            generation=gen.pk, kind=gen.kind, voice=gen.voice.name, created=gen.created_at.isoformat(),
            request=gen.request.code if gen.request else None,
            client=gen.request.client.company_name if gen.request else (gen.user.email if gen.user else None),
        )
    return out


def open_dispute(reporter, description, url="", evidence=None, voice=None, request=None, reporter_email=""):
    dispute = Dispute(
        reporter=reporter if (reporter and reporter.is_authenticated) else None,
        reporter_email=reporter_email, voice=voice, request=request, description=description, url=url,
    )
    if evidence:
        data = evidence.read()
        evidence.seek(0)
        dispute.evidence = evidence
        try:
            info = identify_audio(data, voice=voice)
            dispute.detection = _summary(info)
            gen = info.get("generation")
            if gen:
                dispute.generation = gen
                dispute.voice = dispute.voice or gen.voice
                dispute.request = dispute.request or gen.request
        except Exception as exc:  # la disputa se abre igualmente
            log.warning("No se pudo analizar la prueba: %s", exc)
    dispute.save()
    ledger.record("dispute.opened", dispute.reporter, dispute=dispute.code,
                  voice=dispute.voice.slug if dispute.voice else None,
                  request=dispute.request.code if dispute.request else None, detection=dispute.detection)
    notify_staff(N_("Nueva disputa %(code)s"), description[:400], f"/gestion/disputas/{dispute.pk}/", code=dispute.code)
    if dispute.voice and (not dispute.reporter or dispute.reporter_id != dispute.voice.talent.user_id):
        notify(dispute.voice.talent.user, N_("Se ha abierto una incidencia sobre tu voz (%(code)s)"),
               N_("Nuestro equipo la está revisando. Te avisaremos de cualquier novedad."), "/locutor/disputas/", code=dispute.code)
    return dispute


def apply_blocks(dispute, staff, block_voice=None, block_request=None, block_client=None):
    """Bloqueo temporal de usos nuevos mientras se investiga."""
    reason = f"Disputa {dispute.code}"
    changed = {}
    if block_voice is not None and dispute.voice:
        dispute.block_voice = block_voice
        dispute.voice.blocked, dispute.voice.blocked_reason = block_voice, reason if block_voice else ""
        dispute.voice.save()
        changed["voice"] = block_voice
    if block_request is not None and dispute.request:
        dispute.block_request = block_request
        dispute.request.blocked, dispute.request.blocked_reason = block_request, reason if block_request else ""
        dispute.request.save()
        changed["request"] = block_request
    if block_client is not None and dispute.request:
        client = dispute.request.client
        dispute.block_client = block_client
        client.blocked, client.blocked_reason = block_client, reason if block_client else ""
        client.save()
        changed["client"] = block_client
    if dispute.status == Dispute.Status.OPEN:
        dispute.status = Dispute.Status.INVESTIGATING
    dispute.handled_by = staff
    dispute.save()
    if changed:
        ledger.record("dispute.blocks", staff, dispute=dispute.code, **changed)
    return dispute


def add_note(dispute, author, body, file=None, internal=False):
    note = DisputeNote(dispute=dispute, author=author, body=body, internal=internal)
    if file:
        note.file = file
    note.save()
    return note


def resolve(dispute, staff, upheld, resolution, keep_blocks=False):
    dispute.status = Dispute.Status.UPHELD if upheld else Dispute.Status.DISMISSED
    dispute.resolution, dispute.resolved_at, dispute.handled_by = resolution, timezone.now(), staff
    dispute.save()
    if not keep_blocks:
        apply_blocks(dispute, staff, block_voice=False, block_request=False, block_client=False)
        dispute.status = Dispute.Status.UPHELD if upheld else Dispute.Status.DISMISSED
        dispute.save()
    ledger.record("dispute.resolved", staff, dispute=dispute.code, upheld=upheld, keep_blocks=keep_blocks)
    for user in {dispute.reporter, dispute.voice.talent.user if dispute.voice else None,
                 dispute.request.client.user if dispute.request else None}:
        if user:
            notify(user, N_("Disputa %(code)s resuelta"), resolution[:800], "", code=dispute.code)
    return dispute
