"""
Control de usos permitidos.

TODA decisión sobre si se puede pedir una licencia o generar un audio pasa por
este fichero. Catálogo, demos, solicitudes y generación preguntan aquí, de modo
que una voz pausada, una categoría no autorizada o una licencia caducada se
bloquean siempre de la misma manera.
"""
import hashlib
import re
from dataclasses import dataclass

from django.utils import timezone
from django.utils.translation import gettext as _

from ..models import Generation, License, LicenseRequest, PlatformSettings, Voice, VoiceConsent


@dataclass
class Decision:
    ok: bool
    code: str = "ok"
    message: str = ""

    def __bool__(self):
        return self.ok


def deny(code, message):
    return Decision(False, code, message)


ALLOW = Decision(True)


def normalize_script(text):
    return re.sub(r"\s+", " ", (text or "").strip())


def script_hash(text):
    return hashlib.sha256(normalize_script(text).encode()).hexdigest()


def voice_available(voice):
    """¿Admite la voz usos nuevos ahora mismo?"""
    if voice.status == Voice.Status.PAUSED:
        return deny("voice_paused", _("El locutor ha pausado esta voz. No admite usos nuevos por ahora."))
    if voice.status == Voice.Status.REVOKED:
        return deny("voice_revoked", _("El locutor ha retirado la autorización de esta voz."))
    if voice.status != Voice.Status.ACTIVE:
        return deny("voice_inactive", _("Esta voz todavía no está activa."))
    if voice.blocked:
        return deny("voice_blocked", _("Esta voz está bloqueada temporalmente por una incidencia."))
    if not voice.external_voice_id:
        return deny("voice_no_model", _("Esta voz no tiene un modelo de IA disponible."))
    if voice.talent.user.suspended or not voice.current_contract():
        return deny("no_contract", _("Esta voz no tiene un contrato vigente."))
    return ALLOW


def client_can_operate(user):
    if not user.is_authenticated or not user.is_client or not hasattr(user, "client"):
        return deny("not_client", _("Necesitas una cuenta de cliente."))
    if not user.email_verified:
        return deny("email", _("Confirma tu correo electrónico para continuar."))
    if user.suspended or user.client.blocked:
        return deny("client_blocked", _("Tu cuenta está bloqueada. Contacta con soporte."))
    return ALLOW


def category_allowed(voice, category):
    if not category.active:
        return deny("category_inactive", _("Esta categoría de uso no está disponible."))
    consent = VoiceConsent.objects.filter(voice=voice, category=category, allowed=True).exists()
    if not consent:
        return deny("category_denied", _("El locutor no autoriza su voz para esta categoría de uso."))
    return ALLOW


def check_demo(user, voice, text):
    """Demo de texto libre: solo clientes verificados, texto corto y cupo diario."""
    cfg = PlatformSettings.load()
    for decision in (client_can_operate(user), voice_available(voice)):
        if not decision:
            return decision
    text = normalize_script(text)
    if len(text) < 3:
        return deny("empty", _("Escribe el texto que quieres escuchar."))
    if len(text) > cfg.demo_max_chars:
        return deny("demo_too_long", _("Las demos admiten como máximo %(n)s caracteres.") % {"n": cfg.demo_max_chars})
    since = timezone.now() - timezone.timedelta(hours=24)
    used = Generation.objects.filter(user=user, kind=Generation.Kind.DEMO, created_at__gte=since).count()
    if used >= cfg.demo_daily_limit:
        return deny("demo_quota", _("Has alcanzado el límite diario de demos. Vuelve mañana o solicita una licencia."))
    return ALLOW


def check_request(user, voice, category, script):
    """¿Puede este cliente pedir una licencia de esta voz para esta categoría?"""
    cfg = PlatformSettings.load()
    for decision in (client_can_operate(user), voice_available(voice), category_allowed(voice, category)):
        if not decision:
            return decision
    if cfg.require_client_validation and not user.client.verified:
        return deny("client_unverified", _("Estamos validando los datos de tu empresa. Te avisaremos por correo."))
    script = normalize_script(script)
    if len(script) < 3:
        return deny("empty", _("Añade el guion que se va a locutar."))
    if len(script) > cfg.script_max_chars:
        return deny("script_too_long", _("El guion supera el máximo de %(n)s caracteres.") % {"n": cfg.script_max_chars})
    return ALLOW


def check_final(req: LicenseRequest, user):
    """¿Se puede generar ahora una toma final de esta solicitud?"""
    if not (user.is_authenticated and hasattr(user, "client") and req.client_id == user.client.pk):
        return deny("not_owner", _("Esta licencia no es tuya."))
    if req.blocked:
        return deny("request_blocked", _("Esta licencia está bloqueada por una incidencia abierta."))
    if req.status != LicenseRequest.Status.ACTIVE or not hasattr(req, "license"):
        return deny("not_active", _("La licencia todavía no está activa. Falta la aprobación o el pago."))
    lic = req.license
    if lic.status != License.Status.ACTIVE:
        return deny("license_status", _("La licencia no está vigente."))
    if not lic.in_force:
        return deny("license_expired", _("La licencia ha caducado."))
    for decision in (client_can_operate(user), voice_available(req.voice), category_allowed(req.voice, req.category)):
        if not decision:
            return decision
    if req.takes_used >= req.max_takes:
        return deny("no_takes", _("Has usado todas las tomas incluidas en esta licencia."))
    return ALLOW


def can_download(req: LicenseRequest, user):
    """Descargar audios ya generados: el titular, mientras la licencia no esté anulada ni bloqueada."""
    if user.is_authenticated and user.is_manager:
        return ALLOW
    if not (user.is_authenticated and hasattr(user, "client") and req.client_id == user.client.pk):
        return deny("not_owner", _("Esta licencia no es tuya."))
    if req.blocked:
        return deny("request_blocked", _("Esta licencia está bloqueada por una incidencia abierta."))
    if not hasattr(req, "license") or req.license.status in (License.Status.REVOKED, License.Status.SUSPENDED):
        return deny("license_status", _("La licencia no está vigente."))
    return ALLOW
