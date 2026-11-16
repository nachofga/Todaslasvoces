"""Área privada del cliente."""
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import translation
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from .. import forms
from ..models import Dispute, Generation, LicenseRequest, Payment, PlatformSettings, UseCategory, Voice
from ..services import direction as direction_service
from ..services import disputes, documents, insight, licenseflow, money, policy
from ..services.voiceflow import FlowError
from .common import client_required, flow


def _lang():
    return (translation.get_language() or "es")[:2]


@client_required
def home(request):
    client = request.user.client
    R = LicenseRequest.Status
    reqs = client.requests.select_related("voice", "category")
    return render(request, "tlv/client/home.html", {
        "client": client, "recent": reqs[:6],
        "to_pay": reqs.filter(status=R.APPROVED).count(), "to_answer": reqs.filter(status__in=[R.INFO, R.COUNTER]).count(),
        "active": reqs.filter(status=R.ACTIVE).count(), "waiting": reqs.filter(status=R.SUBMITTED).count(),
        "demos": Generation.objects.filter(user=request.user, kind=Generation.Kind.DEMO).select_related("voice")[:6],
        "cfg": PlatformSettings.load(),
    })


@client_required
def profile(request):
    form = forms.ClientProfileForm(request.POST or None, instance=request.user.client)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Datos de empresa guardados."))
        return redirect("client_home")
    return render(request, "tlv/client/profile.html", {"form": form, "client": request.user.client})


@client_required
@require_POST
def demo(request, slug):
    """Genera una demo de texto libre. Responde en JSON para el reproductor de la ficha."""
    voice = get_object_or_404(Voice, slug=slug)
    try:
        gen = licenseflow.generate_demo(voice, request.user, request.POST.get("text", ""),
                                        direction=direction_service.clean(request.POST, "dir_"))
    except FlowError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    return JsonResponse({"ok": True, "url": reverse("media_generation", args=[gen.pk, "mp3"]), "seconds": gen.duration})


@client_required
def quote(request, slug):
    voice = get_object_or_404(Voice, slug=slug)
    try:
        category = UseCategory.objects.get(pk=request.GET.get("category"))
        months = int(request.GET.get("duration_months", 12))
        territory = request.GET.get("territory", "es")
    except (UseCategory.DoesNotExist, ValueError, TypeError):
        return JsonResponse({"ok": False}, status=400)
    cfg = PlatformSettings.load()
    net = money.quote(voice, category, territory, months)
    vat = money.money(net * cfg.vat_percent / 100)
    fmt = lambda v: f"{v:,.2f} €"  # noqa: E731
    return JsonResponse({"ok": True, "net": fmt(net), "vat": fmt(vat), "total": fmt(net + vat)})


@client_required
def request_new(request, slug):
    voice = get_object_or_404(Voice, slug=slug)
    gate = policy.voice_available(voice)
    if not gate:
        messages.error(request, gate.message)
        return redirect("catalog")
    cfg = PlatformSettings.load()
    if cfg.require_client_validation and not request.user.client.verified:
        messages.warning(request, _("Estamos validando los datos de tu empresa. En cuanto estén validados podrás solicitar licencias."))
        return redirect("client_home")
    form = forms.LicenseRequestForm(request.POST or None, voice=voice, initial={"territory": "es", "duration_months": 12})
    direction = direction_service.clean(request.POST, "dir_") if request.method == "POST" else None
    if request.method == "POST" and form.is_valid():
        req = flow(request, licenseflow.create_request, request.user, voice, {**form.cleaned_data, "direction": direction},
                   success=_("Solicitud enviada al locutor. Te avisaremos cuando responda."))
        if req:
            return redirect("client_request", pk=req.pk)
    return render(request, "tlv/client/request_form.html", {
        "form": form, "voice": voice, "cfg": cfg, "dir": direction_service.form_context(direction),
    })


@client_required
def requests_list(request):
    status = request.GET.get("estado", "")
    qs = request.user.client.requests.select_related("voice", "category")
    if status:
        qs = qs.filter(status=status)
    return render(request, "tlv/shared/requests.html", {
        "requests": qs[:200], "role": "client", "status": status, "statuses": LicenseRequest.Status.choices,
    })


@client_required
def request_detail(request, pk):
    req = get_object_or_404(
        LicenseRequest.objects.select_related("client", "category", "voice__talent"), pk=pk, client=request.user.client
    )
    msg_form, transfer_form = forms.MessageForm(), forms.TransferForm()
    if request.method == "POST":
        kind = request.POST.get("form")
        if kind == "message":
            msg_form = forms.MessageForm(request.POST, request.FILES)
            if msg_form.is_valid():
                flow(request, licenseflow.post_message, req, request.user, msg_form.cleaned_data["body"],
                     msg_form.cleaned_data.get("attachment"), success=_("Mensaje enviado."))
                return redirect("client_request", pk=req.pk)
        elif kind == "counter":
            flow(request, licenseflow.client_answer_counter, req, request.user, request.POST.get("answer") == "accept",
                 success=_("Respuesta registrada."))
            return redirect("client_request", pk=req.pk)
        elif kind == "cancel":
            flow(request, licenseflow.client_cancel, req, request.user, success=_("Solicitud cancelada."))
            return redirect("client_request", pk=req.pk)
        elif kind == "transfer":
            transfer_form = forms.TransferForm(request.POST, request.FILES)
            if transfer_form.is_valid():
                flow(request, licenseflow.report_transfer, req, request.user, transfer_form.cleaned_data.get("receipt"),
                     success=_("Gracias. Activaremos la licencia en cuanto veamos la transferencia."))
                return redirect("client_request", pk=req.pk)
        elif kind == "take":
            flow(request, licenseflow.generate_take, req, request.user,
                 direction=direction_service.clean(request.POST, "dir_"), success=_("Toma generada."))
            return redirect("client_request", pk=req.pk)
    payment = getattr(req, "payment", None) if hasattr(req, "payment") else None
    return render(request, "tlv/shared/request_detail.html", {
        "req": req, "role": "client", "msg_form": msg_form, "transfer_form": transfer_form,
        "thread": req.messages.select_related("author"), "payment": payment, "cfg": PlatformSettings.load(),
        "takes": req.generations.filter(kind="final", ok=True), "can_take": policy.check_final(req, request.user),
        "can_download": policy.can_download(req, request.user),
        "pay_states": (Payment.Status.PENDING, Payment.Status.REPORTED),
        "stage": insight.stage_of(req), "dir": direction_service.form_context(req.direction),
    })


@client_required
def certificate(request, pk):
    req = get_object_or_404(LicenseRequest, pk=pk, client=request.user.client)
    if not hasattr(req, "license"):
        return redirect("client_request", pk=pk)
    pdf = documents.license_certificate(req.license, _lang())
    resp = HttpResponse(pdf, content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="licencia-{req.license.code}.pdf"'
    return resp


@client_required
def invoice(request, pk):
    req = get_object_or_404(LicenseRequest, pk=pk, client=request.user.client)
    if not (hasattr(req, "payment") and hasattr(req.payment, "invoice")):
        return redirect("client_request", pk=pk)
    inv = req.payment.invoice
    resp = HttpResponse(documents.invoice_pdf(inv, _lang()), content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="{inv.number}.pdf"'
    return resp


@client_required
def disputes_view(request):
    form = forms.DisputeForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        dispute = disputes.open_dispute(request.user, d["description"], d.get("url", ""), d.get("evidence"))
        messages.success(request, _("Incidencia %(code)s abierta. Nuestro equipo la revisará.") % {"code": dispute.code})
        return redirect("client_disputes")
    return render(request, "tlv/shared/disputes.html", {
        "form": form, "role": "client",
        "disputes": Dispute.objects.filter(reporter=request.user).prefetch_related("notes"),
    })
