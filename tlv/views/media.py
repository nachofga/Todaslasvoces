"""
Entrega de audios y documentos. Nada de lo que hay en el almacenamiento tiene
URL pública: cada descarga pasa por aquí y se comprueba quién la pide.
"""
import mimetypes
import re

from django.http import Http404, HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404

from ..models import (
    DemoClip,
    Dispute,
    DisputeNote,
    Generation,
    Payment,
    RequestMessage,
    TalentProfile,
    VoiceSample,
    VoiceVerification,
)
from ..services import policy

CHUNK = 64 * 1024


def serve(request, fieldfile, content_type=None, download_name=None):
    """Devuelve un fichero privado, con soporte de saltos (Range) para el reproductor."""
    if not fieldfile:
        raise Http404
    try:
        size = fieldfile.size
    except Exception as exc:
        raise Http404 from exc
    content_type = content_type or mimetypes.guess_type(fieldfile.name)[0] or "application/octet-stream"
    start, end, status = 0, size - 1, 200
    match = re.match(r"bytes=(\d*)-(\d*)$", request.META.get("HTTP_RANGE", "").strip())
    if match and (match.group(1) or match.group(2)):
        if match.group(1):
            start = int(match.group(1))
            end = min(int(match.group(2)), size - 1) if match.group(2) else size - 1
        else:
            start = max(size - int(match.group(2)), 0)
        if start > end or start >= size:
            resp = HttpResponse(status=416)
            resp["Content-Range"] = f"bytes */{size}"
            return resp
        status = 206

    def stream():
        remaining = end - start + 1
        with fieldfile.open("rb") as fh:
            fh.seek(start)
            while remaining > 0:
                data = fh.read(min(CHUNK, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

    resp = StreamingHttpResponse(stream(), status=status, content_type=content_type)
    resp["Content-Length"] = str(end - start + 1)
    resp["Accept-Ranges"] = "bytes"
    resp["Cache-Control"] = "private, max-age=0, no-store"
    resp["X-Content-Type-Options"] = "nosniff"
    if status == 206:
        resp["Content-Range"] = f"bytes {start}-{end}/{size}"
    if download_name:
        resp["Content-Disposition"] = f'attachment; filename="{download_name}"'
    return resp


def _owns_voice(user, voice):
    return user.is_authenticated and (user.is_manager or (user.is_talent and voice.talent.user_id == user.pk))


def sample(request, pk):
    s = get_object_or_404(VoiceSample.objects.select_related("voice__talent"), pk=pk)
    if not ((s.public and s.voice.is_public) or _owns_voice(request.user, s.voice)):
        raise Http404
    return serve(request, s.file, "audio/wav")


def verification(request, pk):
    v = get_object_or_404(VoiceVerification.objects.select_related("voice__talent"), pk=pk)
    if not _owns_voice(request.user, v.voice):
        raise Http404
    return serve(request, v.recording, "audio/wav")


def generation(request, pk, fmt):
    gen = get_object_or_404(Generation.objects.select_related("voice__talent", "request__client"), pk=pk)
    user = request.user
    owner = _owns_voice(user, gen.voice)
    download = None
    if gen.kind == Generation.Kind.FINAL:
        allowed = bool(gen.request and policy.can_download(gen.request, user))
        if allowed and request.GET.get("descargar"):
            download = f"{gen.request.code}-toma{gen.take}.{fmt}"
        if not allowed and not owner:
            raise Http404
        if not allowed and fmt != "mp3":
            raise Http404
    elif gen.kind == Generation.Kind.DEMO:
        if not (user.is_authenticated and (gen.user_id == user.pk or user.is_manager)):
            raise Http404
    else:
        in_catalog = DemoClip.objects.filter(generation=gen).exists() and gen.voice.is_public
        if not (in_catalog or owner):
            raise Http404
    if fmt == "wav":
        return serve(request, gen.file_wav, "audio/wav", download)
    return serve(request, gen.file_mp3, "audio/mpeg", download)


def private_file(request, kind, pk):
    """Documentos adjuntos: identidad, justificantes, pruebas de disputas y adjuntos de mensajes."""
    user = request.user
    if not user.is_authenticated:
        raise Http404
    if kind == "identidad":
        obj = get_object_or_404(TalentProfile, pk=pk)
        ok, field = user.is_manager, obj.identity_document
    elif kind == "justificante":
        obj = get_object_or_404(Payment.objects.select_related("request__client"), pk=pk)
        ok, field = user.is_manager or obj.request.client.user_id == user.pk, obj.receipt
    elif kind == "prueba":
        obj = get_object_or_404(Dispute, pk=pk)
        ok, field = user.is_manager or obj.reporter_id == user.pk, obj.evidence
    elif kind == "nota":
        obj = get_object_or_404(DisputeNote.objects.select_related("dispute"), pk=pk)
        ok, field = user.is_manager or (not obj.internal and obj.dispute.reporter_id == user.pk), obj.file
    elif kind == "adjunto":
        obj = get_object_or_404(RequestMessage.objects.select_related("request__client", "request__voice__talent"), pk=pk)
        req = obj.request
        ok = user.is_manager or req.client.user_id == user.pk or req.voice.talent.user_id == user.pk
        field = obj.attachment
    else:
        raise Http404
    if not ok or not field:
        raise Http404
    return serve(request, field, download_name=field.name.rsplit("/", 1)[-1])
