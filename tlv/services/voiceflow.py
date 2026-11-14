"""
Ciclo de vida de una voz digital:

  muestras -> prueba de titularidad -> contrato -> revisión de gestión
  -> clonación en el motor -> el locutor escucha y activa -> pausa / revocación
"""
import difflib
import hashlib
import logging
import re
import unicodedata

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from .. import catalogs
from ..models import (
    Contract,
    DemoClip,
    Generation,
    LicenseRequest,
    PlatformSettings,
    UseCategory,
    Voice,
    VoiceConsent,
    VoiceSample,
    VoiceVerification,
)
from . import audio, ledger, speaker, studio
from .engines import EngineError, get_engine
from .notify import N_, notify, notify_staff

log = logging.getLogger("tlv")


class FlowError(Exception):
    """Operación no permitida en el estado actual; el mensaje se enseña al usuario."""


# --- Muestras ---------------------------------------------------------------
def add_sample(voice, uploaded, kind=VoiceSample.Kind.TRAINING, title=""):
    """Lee el fichero, pasa el control de calidad y lo guarda normalizado en WAV."""
    data = uploaded.read()
    try:
        arr, sr = audio.decode(data)
    except audio.AudioError as exc:
        raise FlowError(str(exc)) from exc
    minimum = 10.0 if kind == VoiceSample.Kind.TRAINING else 3.0
    report = audio.analyze(arr, sr, min_seconds=minimum)
    sample = VoiceSample(
        voice=voice, kind=kind, title=title[:120], original_name=getattr(uploaded, "name", "")[:200],
        duration=report["metrics"].get("seconds"), sample_rate=sr, checks=report, ok=report["ok"],
        public=(kind == VoiceSample.Kind.SHOWCASE and report["ok"]),
    )
    sample.file.save("muestra.wav", ContentFile(audio.encode_wav(arr, sr)), save=False)
    sample.save()
    return sample


# --- Prueba de titularidad --------------------------------------------------
def start_verification(voice, language="es"):
    voice.verifications.filter(status=VoiceVerification.Status.PENDING).delete()
    return VoiceVerification.objects.create(
        voice=voice, phrase=catalogs.challenge_phrase(language), language=language
    )


def _plain(text):
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    words = re.sub(r"[^a-z0-9ñ ]+", " ", text).split()
    digits = {"cero": "0", "uno": "1", "dos": "2", "tres": "3", "cuatro": "4", "cinco": "5", "seis": "6",
              "siete": "7", "ocho": "8", "nueve": "9", "zero": "0", "one": "1", "two": "2", "three": "3",
              "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9"}
    return " ".join(digits.get(w, w) for w in words)


def complete_verification(verification, uploaded):
    """
    El locutor ha grabado la frase. Se comprueba:
      1. que se dice la frase pedida (transcripción automática, si el motor la ofrece);
      2. que la voz se parece a la de las muestras (indicador orientativo).
    Si algo no se puede comprobar solo, queda para revisión manual de gestión.
    """
    if verification.expired:
        raise FlowError(_("La frase ha caducado. Pide una nueva y grábala antes de 15 minutos."))
    data = uploaded.read()
    try:
        arr, sr = audio.decode(data)
    except audio.AudioError as exc:
        raise FlowError(str(exc)) from exc
    if audio.duration(arr, sr) < 3:
        raise FlowError(_("La grabación es demasiado corta. Lee la frase completa."))
    wav = audio.encode_wav(arr, sr)
    verification.recording.save("frase.wav", ContentFile(wav), save=False)

    engine = get_engine()
    text_score, transcript = None, ""
    try:
        transcript = engine.transcribe("frase.wav", wav, language=verification.language) or ""
    except EngineError as exc:
        log.warning("Transcripción no disponible: %s", exc)
    if transcript:
        text_score = round(100 * difflib.SequenceMatcher(None, _plain(verification.phrase), _plain(transcript)).ratio(), 1)

    reference = None
    training = [s for s in verification.voice.training_samples() if s.ok][:3]
    if training:
        chunks = []
        for s in training:
            with s.file.open("rb") as fh:
                a, r = audio.decode(fh.read())
            chunks.append(audio.resample(a, r, 16000)[: 16000 * 40])
        import numpy as np

        reference = speaker.profile(np.concatenate(chunks), 16000)
    speaker_score = speaker.similarity(speaker.profile(arr, sr), reference) if reference else None

    S = VoiceVerification.Status
    if text_score is not None and text_score < 60:
        status, detail = S.FAILED, _("Lo que se oye no coincide con la frase pedida.")
    elif speaker_score is not None and speaker_score < 35:
        status, detail = S.REVIEW, _("La voz de la frase no se parece a la de las muestras. Lo revisará una persona.")
    elif text_score is not None and text_score >= 80 and (speaker_score is None or speaker_score >= 55):
        status, detail = S.PASSED, _("Frase y voz comprobadas.")
    else:
        status, detail = S.REVIEW, _("Grabación recibida. Una persona del equipo la escuchará antes de aprobar la voz.")
    verification.transcript = transcript[:2000]
    verification.text_score, verification.speaker_score = text_score, speaker_score
    verification.status, verification.detail = status, str(detail)
    verification.completed_at = timezone.now()
    verification.save()
    ledger.record(
        "voice.verification", verification.voice.talent.user, voice=verification.voice.slug, status=status,
        text_score=text_score, speaker_score=speaker_score,
        recording_sha=hashlib.sha256(wav).hexdigest(),
    )
    return verification


# --- Contrato y consentimiento ----------------------------------------------
def ensure_consents(voice):
    """Crea una fila de consentimiento por categoría (sin autorizar) si falta."""
    existing = set(voice.consents.values_list("category_id", flat=True))
    for cat in UseCategory.objects.filter(active=True):
        if cat.pk not in existing:
            VoiceConsent.objects.create(voice=voice, category=cat, allowed=False)
    return voice.consents.select_related("category").filter(category__active=True)


@transaction.atomic
def sign_contract(voice, signed_name, body, language, request_meta, biometric_consent):
    """Firma electrónica simple: nombre tecleado + fecha + IP + huella del texto firmado."""
    talent = voice.talent
    if not biometric_consent:
        raise FlowError(_("Para crear tu voz digital necesitamos tu consentimiento expreso para tratar tu voz."))
    if _plain(signed_name) != _plain(talent.legal_name):
        raise FlowError(_("El nombre de la firma debe coincidir con tu nombre legal: %(name)s") % {"name": talent.legal_name})
    snapshot = [
        {"code": c.category.code, "name": c.category.name_es, "allowed": c.allowed,
         "price": str(c.price) if c.price is not None else None, "notes": c.notes}
        for c in ensure_consents(voice)
    ]
    if not any(c["allowed"] for c in snapshot):
        raise FlowError(_("Autoriza al menos una categoría de uso antes de firmar."))
    voice.contracts.filter(status=Contract.Status.ACTIVE).update(
        status=Contract.Status.SUPERSEDED, ended_at=timezone.now()
    )
    from django.conf import settings

    contract = Contract.objects.create(
        voice=voice, talent=talent, version=settings.TLV_CONTRACT_VERSION, language=language, body=body,
        body_hash=hashlib.sha256(body.encode()).hexdigest(), consent_snapshot=snapshot,
        biometric_consent=True, signed_name=signed_name.strip(),
        signed_ip=request_meta.get("ip"), signed_user_agent=request_meta.get("ua", "")[:300],
    )
    ledger.record(
        "contract.signed", talent.user, contract=contract.code, voice=voice.slug, version=contract.version,
        body_hash=contract.body_hash, allowed=[c["code"] for c in snapshot if c["allowed"]],
        denied=[c["code"] for c in snapshot if not c["allowed"]],
    )
    return contract


def update_consents(voice, cleaned, actor):
    """Cambia autorizaciones por categoría. Tiene efecto inmediato sobre usos NUEVOS."""
    changes = []
    for consent in ensure_consents(voice):
        data = cleaned.get(consent.category.code)
        if data is None:
            continue
        new = (bool(data["allowed"]), data.get("price"), data.get("notes", ""))
        if (consent.allowed, consent.price, consent.notes) != new:
            consent.allowed, consent.price, consent.notes = new
            consent.save()
            changes.append({"category": consent.category.code, "allowed": consent.allowed})
    if changes:
        ledger.record("consent.updated", actor, voice=voice.slug, changes=changes)
    return changes


# --- Revisión y clonación ---------------------------------------------------
def readiness(voice):
    """Lista de pasos de alta con su estado. La usa el panel del locutor."""
    cfg = PlatformSettings.load()
    talent = voice.talent
    verification = voice.verifications.exclude(status=VoiceVerification.Status.PENDING).first()
    secs = voice.training_seconds()
    S = VoiceVerification.Status
    steps = [
        ("profile", bool(talent.stage_name and talent.legal_name and voice.languages.exists())),
        ("identity", talent.identity_status in (talent.Identity.SUBMITTED, talent.Identity.VERIFIED)),
        ("samples", secs >= cfg.min_training_seconds),
        ("verification", bool(verification and verification.status in (S.PASSED, S.REVIEW))),
        ("contract", voice.current_contract() is not None),
    ]
    return {"steps": steps, "ready": all(done for _k, done in steps), "seconds": secs,
            "needed": cfg.min_training_seconds, "verification": verification}


def submit_for_review(voice):
    if not voice.editable:
        raise FlowError(_("Esta voz ya está enviada a revisión."))
    if not readiness(voice)["ready"]:
        raise FlowError(_("Todavía faltan pasos por completar."))
    voice.status = Voice.Status.IN_REVIEW
    voice.submitted_at = timezone.now()
    voice.save()
    ledger.record("voice.submitted", voice.talent.user, voice=voice.slug)
    notify_staff(N_("Voz pendiente de revisión: %(voice)s"), "", f"/gestion/voces/{voice.pk}/", voice=voice.name)
    return voice


def request_changes(voice, staff, note):
    voice.status = Voice.Status.CHANGES
    voice.review_note, voice.reviewed_by, voice.reviewed_at = note, staff, timezone.now()
    voice.save()
    ledger.record("voice.changes_requested", staff, voice=voice.slug, note=note)
    notify(voice.talent.user, N_("Tu voz necesita cambios antes de aprobarse"), note, "/locutor/")
    return voice


def approve_and_clone(voice, staff):
    """Gestión aprueba: se crea el modelo en el motor de voz y las demos del catálogo."""
    talent = voice.talent
    if not talent.identity_verified:
        raise FlowError(_("Verifica primero la identidad del locutor."))
    if not voice.current_contract():
        raise FlowError(_("El locutor no tiene contrato firmado."))
    samples = [s for s in voice.training_samples() if s.ok]
    if not samples:
        raise FlowError(_("No hay muestras válidas."))
    engine = get_engine()
    files = []
    for i, s in enumerate(samples[:25]):
        with s.file.open("rb") as fh:
            files.append((f"muestra-{i + 1}.wav", fh.read(), "audio/wav"))
    labels = {"gender": voice.gender, "age": voice.age}
    first_lang = voice.languages.first()
    if first_lang:
        labels["language"] = first_lang.code
    try:
        result = engine.clone(
            f"TLV {voice.pk} {voice.name}"[:100], files,
            description=f"Todas las Voces · voz {voice.pk} · contrato {voice.current_contract().code}", labels=labels,
        )
    except EngineError as exc:
        raise FlowError(str(exc)) from exc
    voice.engine, voice.engine_model = engine.name, result.get("model", "")
    voice.external_voice_id, voice.cloned_at = result["voice_id"], timezone.now()
    voice.status = Voice.Status.APPROVED
    voice.review_note = (
        _("El proveedor de voz pide una verificación adicional de esta voz.") if result.get("requires_verification") else ""
    )
    voice.reviewed_by, voice.reviewed_at = staff, timezone.now()
    voice.save()
    ledger.record(
        "voice.cloned", staff, voice=voice.slug, engine=engine.name, model=voice.engine_model,
        external_voice=voice.external_voice_id, samples=[s.pk for s in samples],
        contract=voice.current_contract().code,
    )
    errors = build_demo_clips(voice, staff)
    notify(
        voice.talent.user, N_("Tu voz digital está lista para que la escuches"),
        N_("Hemos creado tu voz digital. Escucha las demostraciones y, si te convence, actívala para publicarla en el catálogo."),
        "/locutor/voz/escuchar/",
    )
    return voice, errors


def build_demo_clips(voice, user=None):
    """Genera las demostraciones de IA del catálogo en los idiomas de la voz (máx. 2)."""
    voice.demo_clips.all().delete()
    codes = [c for c in voice.languages.values_list("code", flat=True) if c in catalogs.DEMO_TEXTS][:2] or ["es"]
    errors = []
    for code in codes:
        for title, text in catalogs.DEMO_TEXTS[code]:
            try:
                gen = studio.render(voice, text, Generation.Kind.PREVIEW, user=user, language=code)
                DemoClip.objects.create(voice=voice, title=title, language=code, generation=gen)
            except Exception as exc:
                log.warning("Demo no generada (%s): %s", voice.slug, exc)
                errors.append(str(exc))
    return errors


# --- Activar, pausar, revocar -----------------------------------------------
def activate(voice):
    if voice.status not in (Voice.Status.APPROVED, Voice.Status.PAUSED):
        raise FlowError(_("Esta voz no se puede activar ahora."))
    voice.status = Voice.Status.ACTIVE
    voice.activated_at = voice.activated_at or timezone.now()
    voice.paused_at = None
    voice.save()
    ledger.record("voice.activated", voice.talent.user, voice=voice.slug)
    return voice


def pause(voice):
    if voice.status != Voice.Status.ACTIVE:
        raise FlowError(_("Solo se puede pausar una voz activa."))
    voice.status, voice.paused_at = Voice.Status.PAUSED, timezone.now()
    voice.save()
    ledger.record("voice.paused", voice.talent.user, voice=voice.slug)
    return voice


@transaction.atomic
def revoke(voice, reason="", actor=None):
    """
    Retira la autorización para usos nuevos: se borra el modelo del motor de
    voz, se cancelan las solicitudes en curso y la voz sale del catálogo.
    Las licencias ya pagadas conservan los audios entregados, pero no admiten
    tomas nuevas.
    """
    if voice.status == Voice.Status.REVOKED:
        return voice
    external, engine_name = voice.external_voice_id, voice.engine
    voice.status, voice.revoked_at = Voice.Status.REVOKED, timezone.now()
    voice.revoke_reason, voice.external_voice_id = reason[:300], ""
    voice.save()
    voice.contracts.filter(status=Contract.Status.ACTIVE).update(
        status=Contract.Status.TERMINATED, ended_at=timezone.now()
    )
    R = LicenseRequest.Status
    pending = voice.requests.filter(status__in=[R.SUBMITTED, R.INFO, R.COUNTER, R.APPROVED])
    for req in pending:
        req.status, req.decision_note = R.CANCELLED, "Voz retirada por el locutor."
        req.save()
        notify(req.client.user, N_("Solicitud cancelada: el locutor ha retirado su voz"),
               N_("La solicitud %(code)s se ha cancelado porque el locutor ha retirado la autorización de su voz."),
               f"/cliente/solicitudes/{req.pk}/", code=req.code)
    ledger.record("voice.revoked", actor or voice.talent.user, voice=voice.slug, reason=reason,
                  external_voice=external, cancelled=[r.code for r in pending])
    if external:
        try:
            get_engine(engine_name or None).delete(external)
            ledger.record("voice.model_deleted", actor, voice=voice.slug, external_voice=external)
        except Exception as exc:
            log.error("No se pudo borrar la voz %s del motor: %s", external, exc)
            notify_staff(N_("Hay que borrar a mano una voz del proveedor: %(voice)s"), str(exc), "", voice=external)
    return voice
