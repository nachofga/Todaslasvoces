"""
Ciclo de una licencia:

  cliente solicita -> locutor aprueba / rechaza / pide información / contraoferta
  -> cliente paga por transferencia -> gestión confirma -> licencia activa
  -> el cliente genera y descarga las tomas -> caducidad
"""
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from ..models import Generation, License, LicenseRequest, Payment, PlatformSettings, RequestMessage
from . import direction as direction_service
from . import ledger, money, policy, studio
from .engines import EngineError
from .notify import N_, notify, notify_staff
from .voiceflow import FlowError

R = LicenseRequest.Status


def _url(req, role):
    return f"/{role}/solicitudes/{req.pk}/"


def _system(req, text, author=None):
    RequestMessage.objects.create(request=req, author=author, body=text, system=True)


@transaction.atomic
def create_request(user, voice, data):
    decision = policy.check_request(user, voice, data["category"], data["script"])
    if not decision:
        raise FlowError(decision.message)
    cfg = PlatformSettings.load()
    req = LicenseRequest.objects.create(
        client=user.client, voice=voice, category=data["category"], project_name=data["project_name"],
        purpose=data["purpose"], sector=data["sector"], script=policy.normalize_script(data["script"]),
        territory=data["territory"], channels=list(data["channels"]), duration_months=int(data["duration_months"]),
        conditions=data.get("conditions", ""), max_takes=cfg.default_max_takes,
        direction=direction_service.clean(data.get("direction")),
        quoted_price=money.quote(voice, data["category"], data["territory"], int(data["duration_months"])),
    )
    ledger.record(
        "request.created", user, request=req.code, voice=voice.slug, client=req.client_id,
        category=req.category.code, territory=req.territory, channels=req.channels,
        duration_months=req.duration_months, script_hash=policy.script_hash(req.script), quote=req.quoted_price,
    )
    notify(
        voice.talent.user, N_("Nueva solicitud de licencia para %(voice)s"),
        N_("%(client)s quiere usar tu voz en «%(project)s». Revisa el guion y las condiciones y decide."),
        _url(req, "locutor"), voice=voice.name, client=req.client.company_name, project=req.project_name,
    )
    return req


def _talent_guard(req, user):
    if not (user.is_talent and req.voice.talent.user_id == user.pk):
        raise FlowError(_("Esta solicitud no es tuya."))
    if not req.open_for_talent:
        raise FlowError(_("Esta solicitud ya está decidida."))


def _approve(req, actor, price):
    req.agreed_price, req.status, req.decided_at = money.money(price), R.APPROVED, timezone.now()
    req.save()
    payment = money.open_payment(req)
    ledger.record("request.approved", actor, request=req.code, price=req.agreed_price, payment=payment.reference)
    notify(
        req.client.user, N_("Solicitud %(code)s aprobada: ya puedes pagar"),
        N_("El locutor ha aprobado el uso de su voz. Haz la transferencia con el concepto %(ref)s para activar la licencia."),
        _url(req, "cliente"), code=req.code, ref=payment.reference,
    )
    return payment


@transaction.atomic
def talent_approve(req, user, price=None):
    _talent_guard(req, user)
    if not policy.voice_available(req.voice):
        raise FlowError(_("Tu voz no está activa. Actívala antes de aprobar solicitudes."))
    if price is not None and money.money(price) != req.quoted_price:
        req.counter_price, req.status = money.money(price), R.COUNTER
        req.save()
        _system(req, f"Contraoferta del locutor: {req.counter_price} € (presupuesto inicial {req.quoted_price} €).", user)
        ledger.record("request.counter", user, request=req.code, price=req.counter_price)
        notify(
            req.client.user, N_("El locutor propone otro precio para %(code)s"),
            N_("Propone %(price)s € en lugar del presupuesto inicial. Acepta o rechaza la propuesta."),
            _url(req, "cliente"), code=req.code, price=req.counter_price,
        )
        return req
    _approve(req, user, req.quoted_price)
    _system(req, "El locutor ha aprobado la solicitud.", user)
    return req


@transaction.atomic
def talent_reject(req, user, reason):
    _talent_guard(req, user)
    req.status, req.decision_note, req.decided_at = R.REJECTED, reason[:2000], timezone.now()
    req.save()
    _system(req, f"El locutor ha rechazado la solicitud. Motivo: {reason}", user)
    ledger.record("request.rejected", user, request=req.code, reason=reason)
    notify(req.client.user, N_("Solicitud %(code)s rechazada"), reason, _url(req, "cliente"), code=req.code)
    return req


@transaction.atomic
def talent_ask_info(req, user, question):
    _talent_guard(req, user)
    req.status = R.INFO
    req.save()
    RequestMessage.objects.create(request=req, author=user, body=question)
    ledger.record("request.info_requested", user, request=req.code)
    notify(req.client.user, N_("El locutor pide más información sobre %(code)s"), question, _url(req, "cliente"), code=req.code)
    return req


def post_message(req, user, body, attachment=None):
    """Mensaje en el hilo. Si el cliente responde a una petición de información, vuelve al locutor."""
    is_client = user.is_client and req.client.user_id == user.pk
    is_talent = user.is_talent and req.voice.talent.user_id == user.pk
    if not (is_client or is_talent or user.is_manager):
        raise FlowError(_("No participas en esta solicitud."))
    msg = RequestMessage(request=req, author=user, body=body)
    if attachment:
        msg.attachment = attachment
    msg.save()
    if is_client:
        if req.status == R.INFO:
            req.status = R.SUBMITTED
            req.save()
        notify(req.voice.talent.user, N_("Nuevo mensaje en la solicitud %(code)s"), body[:500], _url(req, "locutor"), code=req.code)
    elif is_talent:
        notify(req.client.user, N_("Nuevo mensaje en la solicitud %(code)s"), body[:500], _url(req, "cliente"), code=req.code)
    return msg


def _client_guard(req, user):
    if not (user.is_client and req.client.user_id == user.pk):
        raise FlowError(_("Esta solicitud no es tuya."))


@transaction.atomic
def client_answer_counter(req, user, accept):
    _client_guard(req, user)
    if req.status != R.COUNTER:
        raise FlowError(_("No hay ninguna contraoferta pendiente."))
    if accept:
        _approve(req, user, req.counter_price)
        _system(req, "El cliente ha aceptado la contraoferta.", user)
        notify(req.voice.talent.user, N_("El cliente ha aceptado tu precio para %(code)s"), "", _url(req, "locutor"), code=req.code)
    else:
        req.status, req.decided_at = R.CANCELLED, timezone.now()
        req.save()
        _system(req, "El cliente ha rechazado la contraoferta. Solicitud cancelada.", user)
        ledger.record("request.cancelled", user, request=req.code, reason="counter_declined")
        notify(req.voice.talent.user, N_("El cliente no ha aceptado tu precio para %(code)s"), "", _url(req, "locutor"), code=req.code)
    return req


@transaction.atomic
def client_cancel(req, user):
    _client_guard(req, user)
    if req.status not in (R.SUBMITTED, R.INFO, R.COUNTER, R.APPROVED):
        raise FlowError(_("Esta solicitud ya no se puede cancelar."))
    if hasattr(req, "payment") and req.payment.status == Payment.Status.REPORTED:
        raise FlowError(_("Has comunicado una transferencia. Escribe a soporte para cancelar."))
    req.status = R.CANCELLED
    req.save()
    if hasattr(req, "payment"):
        req.payment.status = Payment.Status.CANCELLED
        req.payment.save()
    _system(req, "El cliente ha cancelado la solicitud.", user)
    ledger.record("request.cancelled", user, request=req.code, reason="client")
    notify(req.voice.talent.user, N_("Solicitud %(code)s cancelada por el cliente"), "", _url(req, "locutor"), code=req.code)
    return req


def report_transfer(req, user, receipt=None):
    _client_guard(req, user)
    if req.status != R.APPROVED or not hasattr(req, "payment"):
        raise FlowError(_("Esta solicitud no está pendiente de pago."))
    payment = req.payment
    payment.status, payment.reported_at = Payment.Status.REPORTED, timezone.now()
    if receipt:
        payment.receipt = receipt
    payment.save()
    ledger.record("payment.reported", user, request=req.code, payment=payment.reference)
    notify_staff(N_("Transferencia comunicada: %(ref)s"), "", f"/gestion/cobros/", ref=payment.reference)
    return payment


def confirm_payment(payment, staff):
    """Gestión confirma el cobro: se emite la licencia y se avisa a las dos partes."""
    if payment.request.status != R.APPROVED:
        raise FlowError(_("La solicitud no está pendiente de pago."))
    money.confirm_payment(payment, staff)
    req = LicenseRequest.objects.get(pk=payment.request_id)
    _system(req, f"Pago confirmado. Licencia {req.license.code} emitida.", staff)
    notify(
        req.client.user, N_("Licencia %(code)s activa: ya puedes generar tu locución"),
        N_("Hemos confirmado tu pago. Entra en la solicitud para generar y descargar el audio y el certificado."),
        _url(req, "cliente"), code=req.license.code,
    )
    notify(
        req.voice.talent.user, N_("Licencia %(code)s pagada"),
        N_("El cliente ha pagado «%(project)s». Tu parte queda pendiente de liquidación en tu panel de ingresos."),
        "/locutor/ingresos/", code=req.license.code, project=req.project_name,
    )
    return req


def generate_take(req, user, direction=None):
    """Genera una toma final. Antes pasa SIEMPRE por el control de usos.

    `direction` permite cambiar el tono de esta toma; si no se indica, se usa el
    que el cliente pidió en la solicitud.
    """
    decision = policy.check_final(req, user)
    if not decision:
        ledger.record("audio.blocked", user, request=req.code, reason=decision.code)
        raise FlowError(decision.message)
    try:
        lang = req.voice.languages.values_list("code", flat=True).first()
        return studio.render(req.voice, req.script, Generation.Kind.FINAL, user=user, request=req, language=lang,
                             direction=direction or req.direction)
    except (EngineError, Exception) as exc:
        if isinstance(exc, FlowError):
            raise
        raise FlowError(_("No se ha podido generar el audio: %(error)s") % {"error": exc}) from exc


def generate_demo(voice, user, text, direction=None):
    decision = policy.check_demo(user, voice, text)
    if not decision:
        raise FlowError(decision.message)
    try:
        lang = voice.languages.values_list("code", flat=True).first()
        return studio.render(voice, text, Generation.Kind.DEMO, user=user, language=lang, direction=direction)
    except Exception as exc:
        raise FlowError(_("No se ha podido generar el audio: %(error)s") % {"error": exc}) from exc


def expire_licenses():
    """Tarea diaria: marca como caducadas las licencias vencidas."""
    today = timezone.localdate()
    count = 0
    for lic in License.objects.filter(status=License.Status.ACTIVE, valid_until__lt=today).select_related("request"):
        lic.status = License.Status.EXPIRED
        lic.save()
        lic.request.status = R.EXPIRED
        lic.request.save()
        ledger.record("license.expired", None, license=lic.code)
        notify(lic.request.client.user, N_("La licencia %(code)s ha caducado"),
               N_("Si necesitas seguir usando la locución, solicita una nueva licencia."),
               _url(lic.request, "cliente"), code=lic.code)
        count += 1
    return count
