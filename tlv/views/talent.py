"""Área privada del locutor."""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.db.models import Q, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone, translation
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from .. import catalogs, forms, legal
from ..models import (
    Contract,
    Dispute,
    Generation,
    LicenseRequest,
    Split,
    TalentProfile,
    Voice,
    VoiceSample,
    VoiceVerification,
)
from ..services import disputes, documents, insight, ledger, licenseflow, voiceflow
from ..services.notify import N_, notify_staff
from .common import flow, request_meta, split_of, talent_required


def _voice(request):
    """Cada locutor gestiona una voz en esta versión (el modelo admite varias)."""
    talent = request.user.talent
    voice = talent.voices.order_by("created_at").first()
    if voice is None:
        voice = Voice.objects.create(talent=talent, name=talent.stage_name)
    return voice


def _lang():
    return (translation.get_language() or "es")[:2]


@talent_required
def home(request):
    voice = _voice(request)
    talent = request.user.talent
    R = LicenseRequest.Status
    splits = Split.objects.filter(talent=talent)
    return render(request, "tlv/talent/home.html", {
        "voice": voice, "ready": voiceflow.readiness(voice),
        "pending": voice.requests.filter(status__in=[R.SUBMITTED, R.INFO]).select_related("client", "category")[:5],
        "pending_count": voice.requests.filter(status__in=[R.SUBMITTED, R.INFO]).count(),
        "earned": splits.aggregate(s=Sum("talent_amount"))["s"] or 0,
        "to_collect": splits.filter(talent_payout=Split.Payout.PENDING).aggregate(s=Sum("talent_amount"))["s"] or 0,
        "uses": voice.generations.filter(kind=Generation.Kind.FINAL).count(),
        "licenses": voice.requests.filter(status=R.ACTIVE).count(),
    })


@talent_required
def profile(request):
    voice = _voice(request)
    talent = request.user.talent
    pform = forms.TalentProfileForm(request.POST or None, instance=talent, prefix="p")
    vform = forms.VoiceForm(request.POST or None, instance=voice, prefix="v")
    if request.method == "POST" and pform.is_valid() and vform.is_valid():
        pform.save()
        vform.save()
        messages.success(request, _("Perfil guardado."))
        return redirect("talent_home")
    return render(request, "tlv/talent/profile.html", {"pform": pform, "vform": vform, "voice": voice})


@talent_required
def identity(request):
    talent = request.user.talent
    form = forms.IdentityForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid() and talent.identity_status != TalentProfile.Identity.VERIFIED:
        if talent.identity_document:
            talent.identity_document.delete(save=False)
        talent.identity_document = form.cleaned_data["document"]
        talent.identity_status = TalentProfile.Identity.SUBMITTED
        talent.identity_note = ""
        talent.save()
        ledger.record("identity.submitted", request.user)
        notify_staff(N_("Identidad pendiente de verificar: %(name)s"), "", f"/gestion/locutores/{talent.pk}/", name=talent.legal_name)
        messages.success(request, _("Documento recibido. Lo revisaremos en uno o dos días laborables."))
        return redirect("talent_home")
    return render(request, "tlv/talent/identity.html", {"form": form, "talent": talent})


@talent_required
def samples(request):
    voice = _voice(request)
    form = forms.SampleForm(request.POST or None, request.FILES or None)
    if request.method == "POST":
        action = request.POST.get("action", "upload")
        if action == "delete":
            s = get_object_or_404(VoiceSample, pk=request.POST.get("sample"), voice=voice)
            if s.kind == VoiceSample.Kind.TRAINING and not voice.editable:
                messages.error(request, _("No puedes borrar muestras mientras tu voz está en revisión o activa."))
            else:
                s.file.delete(save=False)
                s.delete()
                messages.success(request, _("Muestra eliminada."))
            return redirect("talent_samples")
        if action == "toggle":
            s = get_object_or_404(VoiceSample, pk=request.POST.get("sample"), voice=voice, kind=VoiceSample.Kind.SHOWCASE)
            s.public = not s.public and s.ok
            s.save()
            return redirect("talent_samples")
        if form.is_valid():
            d = form.cleaned_data
            if d["kind"] == "training" and not voice.editable:
                messages.error(request, _("Tu voz ya está enviada. Para añadir muestras de entrenamiento, escribe a soporte."))
            else:
                s = flow(request, voiceflow.add_sample, voice, d["file"], d["kind"], d["title"])
                if s and s.ok:
                    messages.success(request, _("Muestra añadida. Ha pasado el control de calidad."))
                elif s:
                    messages.warning(request, _("Muestra subida, pero no pasa el control de calidad. Mira los avisos y vuelve a grabarla."))
                return redirect("talent_samples")
    return render(request, "tlv/talent/samples.html", {
        "voice": voice, "form": form, "ready": voiceflow.readiness(voice),
        "training": voice.samples.filter(kind="training"), "showcase": voice.samples.filter(kind="showcase"),
        "guide": catalogs.RECORDING_GUIDE.get(_lang(), catalogs.RECORDING_GUIDE["es"]),
    })


@talent_required
def verification(request):
    voice = _voice(request)
    current = voice.verifications.first()
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "new" or current is None or current.expired:
            if not voice.training_samples().filter(ok=True).exists():
                messages.error(request, _("Sube primero tus muestras: la frase se compara con ellas."))
                return redirect("talent_samples")
            voiceflow.start_verification(voice, _lang())
            return redirect("talent_verification")
        form = forms.RecordingForm(request.POST, request.FILES)
        if form.is_valid() and current.status == VoiceVerification.Status.PENDING:
            result = flow(request, voiceflow.complete_verification, current, form.cleaned_data["recording"])
            if result:
                level = messages.success if result.status == "passed" else (messages.error if result.status == "failed" else messages.info)
                level(request, result.detail)
        else:
            messages.error(request, _("No hemos recibido la grabación."))
        return redirect("talent_verification")
    return render(request, "tlv/talent/verification.html", {"voice": voice, "v": current})


@talent_required
def contract(request):
    voice = _voice(request)
    talent = request.user.talent
    consents = list(voiceflow.ensure_consents(voice))
    current = voice.current_contract()
    lang = _lang()
    body = legal.contract_text(talent, lang)
    sign_form = forms.SignContractForm(request.POST or None)
    if request.method == "POST":
        cleaned = {}
        for c in consents:
            code = c.category.code
            raw_price = (request.POST.get(f"price-{code}") or "").strip().replace(",", ".")
            try:
                price = Decimal(raw_price).quantize(Decimal("0.01")) if raw_price else None
            except InvalidOperation:
                price = c.price
            cleaned[code] = {"allowed": request.POST.get(f"allow-{code}") == "on", "price": price,
                             "notes": (request.POST.get(f"notes-{code}") or "")[:300]}
        voiceflow.update_consents(voice, cleaned, request.user)
        if request.POST.get("action") == "sign":
            if not talent.tax_id:
                messages.error(request, _("Añade tu NIF en el perfil antes de firmar."))
                return redirect("talent_profile")
            if sign_form.is_valid():
                signed = flow(
                    request, voiceflow.sign_contract, voice, sign_form.cleaned_data["signed_name"], body, lang,
                    request_meta(request), sign_form.cleaned_data["biometric_consent"], success=_("Contrato firmado."),
                )
                if signed:
                    return redirect("talent_home")
            consents = list(voiceflow.ensure_consents(voice))
        else:
            messages.success(request, _("Autorizaciones guardadas. Se aplican ya a los usos nuevos."))
            return redirect("talent_contract")
    return render(request, "tlv/talent/contract.html", {
        "voice": voice, "consents": consents, "contract": current, "body": body, "form": sign_form,
        "allowed_count": sum(1 for c in consents if c.allowed),
    })


@talent_required
def contract_pdf(request, pk):
    c = get_object_or_404(Contract, pk=pk, talent=request.user.talent)
    return HttpResponse(documents.contract_pdf(c), content_type="application/pdf")


@talent_required
@require_POST
def voice_action(request, action):
    voice = _voice(request)
    if action == "enviar":
        flow(request, voiceflow.submit_for_review, voice, success=_("Voz enviada a revisión. Te avisaremos por correo."))
    elif action == "activar":
        flow(request, voiceflow.activate, voice, success=_("Tu voz está activa y publicada en el catálogo."))
    elif action == "pausar":
        flow(request, voiceflow.pause, voice, success=_("Voz en pausa. No admite usos nuevos hasta que la reactives."))
    elif action == "revocar":
        form = forms.RevokeForm(request.POST)
        if form.is_valid():
            flow(request, voiceflow.revoke, voice, form.cleaned_data["reason"], request.user,
                 success=_("Autorización revocada. Hemos eliminado tu voz digital."))
        else:
            messages.error(request, _("Marca la casilla de confirmación para revocar."))
    else:
        raise Http404
    return redirect("talent_listen" if action == "activar" else "talent_home")


@talent_required
def listen(request):
    voice = _voice(request)
    return render(request, "tlv/talent/listen.html", {
        "voice": voice, "clips": voice.demo_clips.select_related("generation"), "revoke_form": forms.RevokeForm(),
        "recent": voice.generations.select_related("request__client")[:20],
    })


@talent_required
def requests_list(request):
    voice = _voice(request)
    status = request.GET.get("estado", "")
    qs = voice.requests.select_related("client", "category")
    if status:
        qs = qs.filter(status=status)
    return render(request, "tlv/shared/requests.html", {
        "requests": qs[:200], "role": "talent", "status": status, "statuses": LicenseRequest.Status.choices,
    })


@talent_required
def request_detail(request, pk):
    voice = _voice(request)
    req = get_object_or_404(LicenseRequest.objects.select_related("client", "category", "voice"), pk=pk, voice=voice)
    decision = forms.DecisionForm(request.POST or None, initial={"price": req.quoted_price})
    msg_form = forms.MessageForm()
    if request.method == "POST":
        if request.POST.get("form") == "message":
            msg_form = forms.MessageForm(request.POST, request.FILES)
            if msg_form.is_valid():
                flow(request, licenseflow.post_message, req, request.user, msg_form.cleaned_data["body"],
                     msg_form.cleaned_data.get("attachment"), success=_("Mensaje enviado."))
                return redirect("talent_request", pk=req.pk)
        elif decision.is_valid():
            d = decision.cleaned_data
            if d["action"] == "approve":
                flow(request, licenseflow.talent_approve, req, request.user, d.get("price"), success=_("Decisión registrada."))
            elif d["action"] == "reject":
                flow(request, licenseflow.talent_reject, req, request.user, d["note"], success=_("Solicitud rechazada."))
            else:
                flow(request, licenseflow.talent_ask_info, req, request.user, d["note"], success=_("Pregunta enviada al cliente."))
            return redirect("talent_request", pk=req.pk)
    return render(request, "tlv/shared/request_detail.html", {
        "req": req, "role": "talent", "decision": decision, "msg_form": msg_form,
        "thread": req.messages.select_related("author"), "split": split_of(req),
        "takes": req.generations.filter(kind="final", ok=True), "stage": insight.stage_of(req),
    })


@talent_required
def earnings(request):
    talent = request.user.talent
    splits = Split.objects.filter(talent=talent).select_related("payment__request__client", "payment__request__voice")
    total = splits.aggregate(s=Sum("talent_amount"))["s"] or 0
    paid = splits.filter(talent_payout=Split.Payout.PAID).aggregate(s=Sum("talent_amount"))["s"] or 0
    voice = _voice(request)
    by_month = {}
    for s in splits:
        key = timezone.localtime(s.created_at).strftime("%Y-%m")
        by_month[key] = by_month.get(key, 0) + s.talent_amount
    return render(request, "tlv/talent/earnings.html", {
        "splits": splits, "total": total, "paid": paid, "pending": total - paid,
        "uses": voice.generations.filter(kind=Generation.Kind.FINAL).count(),
        "demos": voice.generations.filter(kind=Generation.Kind.DEMO).count(),
        "by_month": sorted(by_month.items(), reverse=True)[:12], "talent": talent,
    })


@talent_required
def statement_pdf(request, pk):
    split = get_object_or_404(Split, pk=pk, talent=request.user.talent)
    return HttpResponse(documents.payout_statement(split, _lang()), content_type="application/pdf")


@talent_required
def disputes_view(request):
    voice = _voice(request)
    form = forms.DisputeForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        dispute = disputes.open_dispute(request.user, d["description"], d.get("url", ""), d.get("evidence"), voice=voice)
        messages.success(request, _("Incidencia %(code)s abierta. Nuestro equipo la revisará.") % {"code": dispute.code})
        return redirect("talent_disputes")
    return render(request, "tlv/shared/disputes.html", {
        "form": form, "role": "talent",
        "disputes": Dispute.objects.filter(Q(voice=voice) | Q(reporter=request.user)).distinct().prefetch_related("notes"),
    })
