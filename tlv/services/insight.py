"""
Visibilidad para gestión: en qué fase está cada solicitud, a quién se espera y
cómo va la plataforma. Aquí solo se lee; nada de este módulo cambia datos.
"""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Max, Q, Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from ..models import (
    ClientProfile,
    Generation,
    LedgerEntry,
    LicenseRequest,
    Payment,
    Split,
    TalentProfile,
    User,
    Voice,
)

# Las cinco fases por las que pasa una solicitud hasta que el cliente tiene su audio.
STAGES = [
    ("requested", _("Solicitada")),
    ("approved", _("Aprobada por el locutor")),
    ("paid", _("Pagada, con licencia")),
    ("generated", _("Audio generado")),
    ("delivered", _("Audio descargado")),
]
STAGE_LABELS = dict(STAGES)
WAITING = {
    "talent": _("Locutor"), "client": _("Cliente"), "staff": _("Gestión"), "": "—",
}
R = LicenseRequest.Status


def with_progress(qs):
    """Añade a cada solicitud cuántas tomas tiene y cuántas veces se han descargado."""
    final = Q(generations__kind=Generation.Kind.FINAL, generations__ok=True)
    return qs.annotate(
        n_takes=Count("generations", filter=final, distinct=True),
        n_downloads=Sum("generations__downloads", filter=final),
        last_take_at=Max("generations__created_at", filter=final),
    )


def stage_of(req):
    """Fase de una solicitud. Devuelve un diccionario listo para la plantilla."""
    takes = getattr(req, "n_takes", None)
    if takes is None:
        finals = req.generations.filter(kind=Generation.Kind.FINAL, ok=True)
        takes = finals.count()
        downloads = finals.aggregate(s=Sum("downloads"))["s"] or 0
    else:
        downloads = getattr(req, "n_downloads", 0) or 0
    payment = getattr(req, "payment", None) if hasattr(req, "payment") else None
    status = req.status
    closed, waiting, tone = False, "", "info"

    if status == R.SUBMITTED:
        index, waiting, todo = 0, "talent", _("El locutor tiene que decidir")
    elif status == R.INFO:
        index, waiting, todo = 0, "client", _("El cliente tiene que responder al locutor")
    elif status == R.COUNTER:
        index, waiting, todo = 0, "client", _("El cliente tiene que aceptar el nuevo precio")
    elif status == R.APPROVED:
        index = 1
        if payment is not None and payment.status == Payment.Status.REPORTED:
            waiting, todo, tone = "staff", _("Transferencia avisada: hay que confirmar el cobro"), "warn"
        else:
            waiting, todo = "client", _("El cliente tiene que pagar")
    elif status == R.ACTIVE:
        if not takes:
            index, waiting, todo = 2, "client", _("El cliente todavía no ha generado la locución")
        elif not downloads:
            index, waiting, todo = 3, "client", _("Audio generado, aún sin descargar")
        else:
            index, todo, tone = 4, _("Entregada: el cliente ya tiene el audio"), "ok"
    else:
        closed, tone = True, "muted"
        index = 4 if downloads else 3 if takes else 2 if status == R.EXPIRED else 1 if payment is not None else 0
        todo = req.get_status_display()
        if status == R.REJECTED:
            tone = "bad"
    since = req.updated_at or req.created_at
    return {
        "index": index, "key": STAGES[index][0], "label": STAGES[index][1], "waiting": waiting,
        "waiting_label": WAITING[waiting], "todo": todo, "closed": closed, "tone": tone,
        "takes": takes, "downloads": downloads, "days": max((timezone.now() - since).days, 0),
        "steps": [
            {"label": label, "state": "done" if i < index or (i == index and (index == 4 or closed))
             else "now" if i == index else "todo"}
            for i, (_key, label) in enumerate(STAGES)
        ],
    }


def attach_stage(requests):
    requests = list(requests)
    for req in requests:
        req.stage = stage_of(req)
    return requests


def open_requests():
    qs = LicenseRequest.objects.filter(status__in=[R.SUBMITTED, R.INFO, R.COUNTER, R.APPROVED, R.ACTIVE])
    return with_progress(qs.select_related("client", "voice__talent", "category", "payment"))


def funnel():
    """Solicitudes vivas agrupadas por fase, con su importe."""
    rows = [{"key": key, "label": label, "n": 0, "amount": Decimal("0"), "index": i} for i, (key, label) in enumerate(STAGES)]
    waiting = {"talent": 0, "client": 0, "staff": 0}
    for req in open_requests():
        st = stage_of(req)
        rows[st["index"]]["n"] += 1
        rows[st["index"]]["amount"] += req.price
        if st["waiting"]:
            waiting[st["waiting"]] += 1
    top = max((r["n"] for r in rows), default=0) or 1
    for r in rows:
        r["share"] = round(100 * r["n"] / top)
    return rows, waiting


def conversion():
    """De cada 100 solicitudes, cuántas llegan a cada paso (histórico completo)."""
    total = LicenseRequest.objects.count()
    if not total:
        return None
    approved = LicenseRequest.objects.filter(status__in=[R.APPROVED, R.ACTIVE, R.EXPIRED]).count()
    paid = Payment.objects.filter(status=Payment.Status.CONFIRMED).count()
    with_audio = (
        Generation.objects.filter(kind=Generation.Kind.FINAL, ok=True).values("request").distinct().count()
    )
    pct = lambda n: round(100 * n / total)  # noqa: E731
    return {
        "total": total, "approved": approved, "paid": paid, "with_audio": with_audio,
        "approved_pct": pct(approved), "paid_pct": pct(paid), "audio_pct": pct(with_audio),
        "rejected": LicenseRequest.objects.filter(status=R.REJECTED).count(),
        "cancelled": LicenseRequest.objects.filter(status=R.CANCELLED).count(),
    }


def kpis():
    now = timezone.now()
    month = timezone.localtime().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last30 = now - timedelta(days=30)
    confirmed = Payment.objects.filter(status=Payment.Status.CONFIRMED)
    gens = Generation.objects.filter(ok=True)
    return {
        "voices": Voice.objects.filter(status=Voice.Status.ACTIVE).count(),
        "voices_total": Voice.objects.count(),
        "talents": TalentProfile.objects.filter(user__is_active=True).count(),
        "talents_new": TalentProfile.objects.filter(created_at__gte=last30).count(),
        "clients": ClientProfile.objects.filter(user__is_active=True).count(),
        "clients_new": ClientProfile.objects.filter(created_at__gte=last30).count(),
        "licenses": LicenseRequest.objects.filter(status=R.ACTIVE).count(),
        "revenue": confirmed.aggregate(s=Sum("net"))["s"] or 0,
        "revenue_month": confirmed.filter(confirmed_at__gte=month).aggregate(s=Sum("net"))["s"] or 0,
        "platform": Split.objects.aggregate(s=Sum("platform_amount"))["s"] or 0,
        "owed": Split.objects.filter(talent_payout=Split.Payout.PENDING).aggregate(s=Sum("talent_amount"))["s"] or 0,
        "pending_amount": Payment.objects.filter(
            status__in=[Payment.Status.PENDING, Payment.Status.REPORTED], request__status=R.APPROVED
        ).aggregate(s=Sum("net"))["s"] or 0,
        "finals": gens.filter(kind=Generation.Kind.FINAL).count(),
        "demos": gens.filter(kind=Generation.Kind.DEMO).count(),
        "chars_month": gens.filter(created_at__gte=month).aggregate(s=Sum("chars"))["s"] or 0,
    }


# --- Clientes y locutores -----------------------------------------------------
def clients_overview():
    qs = ClientProfile.objects.select_related("user").annotate(
        n_requests=Count("requests", distinct=True),
        n_active=Count("requests", filter=Q(requests__status=R.ACTIVE), distinct=True),
        n_open=Count("requests", filter=Q(requests__status__in=[R.SUBMITTED, R.INFO, R.COUNTER, R.APPROVED]), distinct=True),
        last_request_at=Max("requests__created_at"),
    )
    spent = dict(
        Payment.objects.filter(status=Payment.Status.CONFIRMED).values_list("request__client").annotate(s=Sum("net"))
    )
    latest = {}
    for req in with_progress(LicenseRequest.objects.select_related("payment").order_by("created_at")):
        latest[req.client_id] = req          # la última de cada cliente gana
    demos = dict(
        Generation.objects.filter(kind=Generation.Kind.DEMO).values_list("user").annotate(n=Count("id"))
    )
    out = []
    for c in qs.order_by("-created_at")[:500]:
        c.spent = spent.get(c.pk, 0)
        c.n_demos = demos.get(c.user_id, 0)
        req = latest.get(c.pk)
        c.latest = req
        if req is not None:
            req.stage = stage_of(req)
            c.where = req.stage["todo"] if not req.stage["closed"] else req.get_status_display()
        elif c.status != "verified":
            c.where = _("Pendiente de validar la empresa") if c.status == "pending" else _("Empresa rechazada")
        elif c.n_demos:
            c.where = _("Probando voces: ha generado demos pero no ha pedido licencia")
        else:
            c.where = _("Registrado, sin actividad")
        out.append(c)
    return out


def talent_step(talent, voice):
    """En qué punto del alta está un locutor."""
    if voice is None:
        return _("Sin voz creada"), "muted"
    S = Voice.Status
    if voice.status == S.ACTIVE:
        return _("Voz publicada en el catálogo"), "ok"
    if voice.status == S.IN_REVIEW:
        return _("Esperando la revisión de gestión"), "warn"
    if voice.status == S.PAUSED:
        return _("Voz pausada por el locutor"), "muted"
    if voice.status == S.REVOKED:
        return _("Voz retirada"), "bad"
    if voice.external_voice_id:
        return _("Clonada: el locutor tiene que escucharla y activarla"), "info"
    if voice.status == S.CHANGES:
        return _("Gestión ha pedido cambios"), "warn"
    if talent.identity_status == "none":
        return _("Falta subir el documento de identidad"), "muted"
    if not voice.samples.filter(ok=True).exists():
        return _("Falta subir muestras de voz"), "muted"
    if not voice.verifications.filter(status="passed").exists():
        return _("Falta la prueba de titularidad"), "muted"
    if voice.current_contract() is None:
        return _("Falta firmar el contrato"), "muted"
    return _("Listo para enviar a revisión"), "info"


def talents_overview(status=""):
    qs = TalentProfile.objects.select_related("user").prefetch_related("voices")
    if status:
        qs = qs.filter(identity_status=status)
    earned = dict(Split.objects.values_list("talent").annotate(s=Sum("talent_amount")))
    pending = dict(
        LicenseRequest.objects.filter(status__in=[R.SUBMITTED, R.INFO]).values_list("voice__talent").annotate(n=Count("id"))
    )
    total = dict(LicenseRequest.objects.values_list("voice__talent").annotate(n=Count("id")))
    out = []
    for t in qs.order_by("-created_at")[:500]:
        voices = list(t.voices.all())
        t.main_voice = voices[0] if voices else None
        t.step, t.step_tone = talent_step(t, t.main_voice)
        t.earned, t.n_pending, t.n_requests = earned.get(t.pk, 0), pending.get(t.pk, 0), total.get(t.pk, 0)
        out.append(t)
    return out


# --- Actividad ----------------------------------------------------------------
EVENTS = {
    "account.created": _("Cuenta creada"), "account.email_verified": _("Correo confirmado"),
    "account.suspension": _("Cambio de suspensión de la cuenta"), "account.deleted": _("Cuenta suprimida"),
    "identity.submitted": _("Documento de identidad enviado"), "identity.reviewed": _("Identidad revisada"),
    "client.reviewed": _("Empresa revisada"), "client.block": _("Bloqueo de cliente"),
    "voice.verification": _("Prueba de titularidad grabada"),
    "voice.submitted": _("Voz enviada a revisión"), "voice.changes_requested": _("Cambios pedidos al locutor"),
    "voice.cloned": _("Voz clonada"), "voice.activated": _("Voz publicada"), "voice.paused": _("Voz pausada"),
    "voice.revoked": _("Voz retirada"), "voice.block": _("Bloqueo de voz"), "voice.model_deleted": _("Modelo de voz borrado"),
    "voice.verification_reviewed": _("Prueba de titularidad revisada"),
    "contract.signed": _("Contrato firmado"), "consent.updated": _("Usos permitidos actualizados"),
    "request.created": _("Solicitud de licencia creada"), "request.approved": _("Solicitud aprobada por el locutor"),
    "request.rejected": _("Solicitud rechazada"), "request.info_requested": _("El locutor pide más información"),
    "request.counter": _("El locutor propone otro precio"), "request.counter_answered": _("El cliente responde a la contraoferta"),
    "request.cancelled": _("Solicitud cancelada"), "request.block": _("Bloqueo de solicitud"),
    "request.message": _("Mensaje en la solicitud"),
    "payment.reported": _("El cliente avisa de la transferencia"), "payment.confirmed": _("Cobro confirmado"),
    "license.issued": _("Licencia emitida"), "license.expired": _("Licencia caducada"), "license.revoked": _("Licencia anulada"),
    "audio.generated": _("Audio generado"), "audio.blocked": _("Generación de audio bloqueada"),
    "audio.downloaded": _("Audio descargado"), "payout.paid": _("Pago al locutor"),
    "dispute.opened": _("Incidencia abierta"), "dispute.resolved": _("Incidencia resuelta"), "dispute.blocks": _("Bloqueos por incidencia"),
    "settings.changed": _("Ajustes cambiados"), "categories.changed": _("Categorías cambiadas"),
    "talent.terms_changed": _("Condiciones del locutor cambiadas"),
    "privacy.export": _("Exportación de datos"), "privacy.delete_requested": _("Petición de supresión de datos"),
}


def event_label(event):
    return EVENTS.get(event, event)


def request_timeline(req):
    """Todo lo que ha pasado con una solicitud, del registro firmado, en orden."""
    codes = Q(payload__request=req.code)
    if hasattr(req, "license"):
        codes |= Q(payload__license=req.license.code)
    return LedgerEntry.objects.filter(codes).select_related("actor").order_by("created_at")


def user_activity(user, limit=40):
    return LedgerEntry.objects.filter(actor=user).order_by("-created_at")[:limit]


def recent_audio(limit=8, **filters):
    return Generation.objects.filter(ok=True, **filters).select_related(
        "voice", "user", "request__client"
    ).order_by("-created_at")[:limit]


def signups(days=30):
    since = timezone.now() - timedelta(days=days)
    return User.objects.filter(date_joined__gte=since).exclude(role=User.Role.STAFF).order_by("-date_joined")[:12]
