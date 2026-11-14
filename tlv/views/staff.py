"""Panel de gestión de Todas las Voces."""
import csv
from collections import defaultdict

from django.contrib import messages
from django.core.cache import cache
from django.db.models import Count, Q, Sum
from django.forms import modelformset_factory
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _

from .. import forms
from ..models import (
    ClientProfile,
    DataRequest,
    Dispute,
    Generation,
    LedgerEntry,
    LicenseRequest,
    Payment,
    PlatformSettings,
    Split,
    TalentProfile,
    UseCategory,
    Voice,
)
from ..services import disputes as dispute_service
from ..services import documents, ledger, licenseflow, money, privacy, voiceflow
from ..services.engines import EngineError, get_engine
from ..services.notify import N_, notify
from .common import flow, split_of, staff_required


@staff_required
def home(request):
    R, P = LicenseRequest.Status, Payment.Status
    confirmed = Payment.objects.filter(status=P.CONFIRMED)
    month_start = timezone.localtime().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    queue = {
        "identities": TalentProfile.objects.filter(identity_status="submitted").count(),
        "clients": ClientProfile.objects.filter(status="pending").count(),
        "voices": Voice.objects.filter(status=Voice.Status.IN_REVIEW).count(),
        "payments": Payment.objects.filter(status__in=[P.PENDING, P.REPORTED], request__status=R.APPROVED).count(),
        "payouts": Split.objects.filter(talent_payout=Split.Payout.PENDING).count(),
        "disputes": Dispute.objects.filter(status__in=["open", "investigating"]).count(),
        "privacy": DataRequest.objects.filter(status="open").count(),
    }
    return render(request, "tlv/staff/home.html", {
        "queue": queue,
        "kpi": {
            "voices": Voice.objects.filter(status=Voice.Status.ACTIVE).count(),
            "talents": TalentProfile.objects.filter(user__is_active=True).count(),
            "clients": ClientProfile.objects.filter(user__is_active=True).count(),
            "licenses": LicenseRequest.objects.filter(status=R.ACTIVE).count(),
            "revenue": confirmed.aggregate(s=Sum("net"))["s"] or 0,
            "revenue_month": confirmed.filter(confirmed_at__gte=month_start).aggregate(s=Sum("net"))["s"] or 0,
            "platform": Split.objects.aggregate(s=Sum("platform_amount"))["s"] or 0,
            "generations": Generation.objects.count(),
        },
        "latest": LedgerEntry.objects.select_related("actor")[:12],
    })


# --- Usuarios -----------------------------------------------------------------
@staff_required
def talents(request):
    qs = TalentProfile.objects.select_related("user").annotate(n_voices=Count("voices"))
    if request.GET.get("estado"):
        qs = qs.filter(identity_status=request.GET["estado"])
    return render(request, "tlv/staff/talents.html", {"talents": qs.order_by("-created_at")[:300]})


@staff_required
def talent_detail(request, pk):
    talent = get_object_or_404(TalentProfile.objects.select_related("user"), pk=pk)
    form = forms.TalentAdminForm(request.POST or None, instance=talent)
    if request.method == "POST":
        action = request.POST.get("action")
        if action in ("verify", "reject"):
            talent.identity_status = "verified" if action == "verify" else "rejected"
            talent.identity_note = request.POST.get("note", "")[:300]
            talent.identity_reviewed_at, talent.identity_reviewed_by = timezone.now(), request.user
            if talent.identity_document:  # minimización de datos: el documento no se conserva
                talent.identity_document.delete(save=False)
            talent.save()
            ledger.record("identity.reviewed", request.user, talent=talent.pk, result=talent.identity_status)
            if action == "verify":
                notify(talent.user, N_("Identidad verificada"), "", "/locutor/")
            else:
                notify(talent.user, N_("No hemos podido verificar tu identidad"), talent.identity_note, "/locutor/identidad/")
            messages.success(request, _("Identidad revisada."))
        elif action == "suspend":
            talent.user.suspended = not talent.user.suspended
            talent.user.suspended_reason = request.POST.get("note", "")[:300]
            talent.user.save()
            ledger.record("account.suspension", request.user, user=talent.user_id, suspended=talent.user.suspended)
        elif form.is_valid():
            form.save()
            ledger.record("talent.terms_changed", request.user, talent=talent.pk, fee_override=talent.fee_override,
                          representative=talent.representative_id)
            messages.success(request, _("Condiciones guardadas."))
        return redirect("staff_talent", pk=pk)
    return render(request, "tlv/staff/talent_detail.html", {"talent": talent, "form": form, "voices": talent.voices.all()})


@staff_required
def clients(request):
    if request.method == "POST":
        client = get_object_or_404(ClientProfile, pk=request.POST.get("client"))
        action = request.POST.get("action")
        if action in ("verify", "reject"):
            client.status = "verified" if action == "verify" else "rejected"
            client.status_note = request.POST.get("note", "")[:300]
            client.reviewed_at, client.reviewed_by = timezone.now(), request.user
            client.save()
            ledger.record("client.reviewed", request.user, client=client.pk, result=client.status)
            if action == "verify":
                notify(client.user, N_("Empresa validada: ya puedes solicitar licencias"), "", "/voces/")
            else:
                notify(client.user, N_("No hemos podido validar los datos de tu empresa"), client.status_note, "/cliente/empresa/")
        elif action == "block":
            client.blocked = not client.blocked
            client.blocked_reason = request.POST.get("note", "")[:300]
            client.save()
            ledger.record("client.block", request.user, client=client.pk, blocked=client.blocked)
        return redirect("staff_clients")
    qs = ClientProfile.objects.select_related("user").annotate(n=Count("requests"))
    return render(request, "tlv/staff/clients.html", {"clients": qs.order_by("status", "-created_at")[:300]})


# --- Voces --------------------------------------------------------------------
@staff_required
def voices(request):
    qs = Voice.objects.select_related("talent")
    status = request.GET.get("estado", "")
    if status:
        qs = qs.filter(status=status)
    return render(request, "tlv/staff/voices.html", {
        "voices": qs.order_by("-submitted_at", "-created_at")[:300], "status": status, "statuses": Voice.Status.choices,
    })


@staff_required
def voice_detail(request, pk):
    voice = get_object_or_404(Voice.objects.select_related("talent__user"), pk=pk)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "approve":
            result = flow(request, voiceflow.approve_and_clone, voice, request.user)
            if result:
                _voice, errors = result
                if errors:
                    messages.warning(request, _("Voz clonada, pero alguna demo no se ha generado: %(e)s") % {"e": errors[0]})
                else:
                    messages.success(request, _("Voz aprobada y clonada. El locutor ya puede escucharla y activarla."))
        elif action == "changes":
            flow(request, voiceflow.request_changes, voice, request.user, request.POST.get("note", ""),
                 success=_("Cambios solicitados al locutor."))
        elif action == "demos":
            errors = voiceflow.build_demo_clips(voice, request.user)
            (messages.warning if errors else messages.success)(request, errors[0] if errors else _("Demos regeneradas."))
        elif action == "block":
            voice.blocked = not voice.blocked
            voice.blocked_reason = request.POST.get("note", "")[:300] if voice.blocked else ""
            voice.save()
            ledger.record("voice.block", request.user, voice=voice.slug, blocked=voice.blocked)
        elif action == "verification":
            v = voice.verifications.first()
            if v:
                v.status = "passed" if request.POST.get("result") == "ok" else "failed"
                v.detail = _("Revisada por gestión.")
                v.save()
                ledger.record("voice.verification_reviewed", request.user, voice=voice.slug, status=v.status)
        return redirect("staff_voice", pk=pk)
    return render(request, "tlv/staff/voice_detail.html", {
        "voice": voice, "ready": voiceflow.readiness(voice), "samples": voice.samples.all(),
        "verification": voice.verifications.first(), "contract": voice.current_contract(),
        "consents": voice.consents.select_related("category"), "clips": voice.demo_clips.select_related("generation"),
    })


@staff_required
def contract_pdf(request, pk):
    from ..models import Contract

    return HttpResponse(documents.contract_pdf(get_object_or_404(Contract, pk=pk)), content_type="application/pdf")


# --- Solicitudes, cobros y pagos ---------------------------------------------
@staff_required
def requests_list(request):
    status = request.GET.get("estado", "")
    qs = LicenseRequest.objects.select_related("client", "voice", "category")
    if status:
        qs = qs.filter(status=status)
    return render(request, "tlv/shared/requests.html", {
        "requests": qs[:300], "role": "staff", "status": status, "statuses": LicenseRequest.Status.choices,
    })


@staff_required
def request_detail(request, pk):
    req = get_object_or_404(LicenseRequest.objects.select_related("client", "category", "voice__talent"), pk=pk)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "block":
            req.blocked = not req.blocked
            req.blocked_reason = request.POST.get("note", "")[:300] if req.blocked else ""
            req.save()
            ledger.record("request.block", request.user, request=req.code, blocked=req.blocked)
        elif action == "revoke" and hasattr(req, "license"):
            req.license.status = "revoked"
            req.license.save()
            ledger.record("license.revoked", request.user, license=req.license.code, note=request.POST.get("note", ""))
            notify(req.client.user, N_("La licencia %(code)s ha sido anulada"), request.POST.get("note", ""),
                   f"/cliente/solicitudes/{req.pk}/", code=req.license.code)
        return redirect("staff_request", pk=pk)
    payment = getattr(req, "payment", None) if hasattr(req, "payment") else None
    return render(request, "tlv/shared/request_detail.html", {
        "req": req, "role": "staff", "thread": req.messages.select_related("author"), "payment": payment,
        "split": split_of(req), "takes": req.generations.filter(kind="final"), "cfg": PlatformSettings.load(),
    })


@staff_required
def payments(request):
    if request.method == "POST":
        payment = get_object_or_404(Payment, pk=request.POST.get("payment"))
        flow(request, licenseflow.confirm_payment, payment, request.user, success=_("Cobro confirmado y licencia emitida."))
        return redirect("staff_payments")
    qs = Payment.objects.select_related("request__client", "request__voice")
    return render(request, "tlv/staff/payments.html", {
        "open": qs.filter(status__in=["pending", "reported"], request__status="approved"),
        "done": qs.filter(status="confirmed")[:100],
    })


@staff_required
def payouts(request):
    if request.method == "POST":
        split = get_object_or_404(Split, pk=request.POST.get("split"))
        money.mark_talent_paid(split, request.POST.get("reference", ""), request.user)
        notify(split.talent.user, N_("Te hemos pagado %(amount)s €"),
               N_("Hemos ordenado la transferencia de tu parte. Puedes descargar el justificante en tu panel de ingresos."),
               "/locutor/ingresos/", amount=f"{split.talent_amount:,.2f}")
        messages.success(request, _("Pago al locutor registrado."))
        return redirect("staff_payouts")
    qs = Split.objects.select_related("talent", "payment__request", "representative")
    return render(request, "tlv/staff/payouts.html", {
        "open": qs.filter(talent_payout="pending"), "done": qs.filter(talent_payout="paid")[:100],
        "owed": qs.filter(talent_payout="pending").aggregate(s=Sum("talent_amount"))["s"] or 0,
    })


# --- Ajustes ------------------------------------------------------------------
@staff_required
def settings_view(request):
    cfg = PlatformSettings.load()
    form = forms.SettingsForm(request.POST or None, instance=cfg)
    if request.method == "POST" and form.is_valid():
        form.save()
        ledger.record("settings.changed", request.user, fee=cfg.platform_fee_percent, vat=cfg.vat_percent,
                      changed=list(form.changed_data))
        messages.success(request, _("Ajustes guardados."))
        return redirect("staff_settings")
    return render(request, "tlv/staff/settings.html", {"form": form, "cfg": cfg})


@staff_required
def categories(request):
    FormSet = modelformset_factory(UseCategory, form=forms.CategoryForm, extra=1)
    formset = FormSet(request.POST or None, queryset=UseCategory.objects.all())
    if request.method == "POST" and formset.is_valid():
        for form in formset:
            if not form.has_changed():
                continue
            obj = form.save(commit=False)
            if not obj.pk:
                from django.utils.text import slugify

                obj.code = slugify(obj.name_es)[:40] or f"cat-{UseCategory.objects.count() + 1}"
            obj.save()
        ledger.record("categories.changed", request.user, count=UseCategory.objects.filter(active=True).count())
        messages.success(request, _("Categorías guardadas."))
        return redirect("staff_categories")
    return render(request, "tlv/staff/categories.html", {"formset": formset})


# --- Disputas -----------------------------------------------------------------
@staff_required
def disputes(request):
    qs = Dispute.objects.select_related("voice", "request", "reporter")
    return render(request, "tlv/staff/disputes.html", {"disputes": qs[:300]})


@staff_required
def dispute_detail(request, pk):
    dispute = get_object_or_404(Dispute.objects.select_related("voice", "request__client", "generation"), pk=pk)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "blocks":
            dispute_service.apply_blocks(
                dispute, request.user, block_voice=request.POST.get("block_voice") == "on",
                block_request=request.POST.get("block_request") == "on", block_client=request.POST.get("block_client") == "on",
            )
            messages.success(request, _("Bloqueos actualizados."))
        elif action == "note" and request.POST.get("note"):
            dispute_service.add_note(dispute, request.user, request.POST["note"], request.FILES.get("file"),
                                     internal=request.POST.get("internal") == "on")
        elif action in ("uphold", "dismiss") and request.POST.get("note"):
            dispute_service.resolve(dispute, request.user, action == "uphold", request.POST["note"],
                                    keep_blocks=request.POST.get("keep") == "on")
            messages.success(request, _("Disputa resuelta."))
        return redirect("staff_dispute", pk=pk)
    return render(request, "tlv/staff/dispute_detail.html", {"d": dispute, "notes": dispute.notes.select_related("author")})


# --- Registro, informes y herramientas ---------------------------------------
@staff_required
def ledger_view(request):
    status = None
    if request.method == "POST":
        status = ledger.verify()
        cache.set("tlv-ledger-status", status, 600)
    qs = LedgerEntry.objects.select_related("actor")
    if request.GET.get("evento"):
        qs = qs.filter(event__startswith=request.GET["evento"])
    return render(request, "tlv/staff/ledger.html", {"entries": qs[:200], "status": status, "key_id": ledger.key_id()})


def _report_rows():
    rows = defaultdict(lambda: {"n": 0, "net": 0, "platform": 0, "talent": 0, "rep": 0})
    for s in Split.objects.select_related("payment"):
        key = timezone.localtime(s.payment.confirmed_at or s.created_at).strftime("%Y-%m")
        r = rows[key]
        r["n"] += 1
        r["net"] += s.net
        r["platform"] += s.platform_amount
        r["talent"] += s.talent_amount
        r["rep"] += s.representative_amount
    return sorted(rows.items(), reverse=True)


@staff_required
def reports(request):
    rows = _report_rows()
    if request.GET.get("formato") == "csv":
        resp = HttpResponse(content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = 'attachment; filename="ingresos-por-mes.csv"'
        writer = csv.writer(resp)
        writer.writerow(["mes", "licencias", "neto", "plataforma", "locutores", "representantes"])
        for month, r in rows:
            writer.writerow([month, r["n"], r["net"], r["platform"], r["talent"], r["rep"]])
        return resp
    by_voice = (
        Split.objects.values("payment__request__voice__name").annotate(n=Count("id"), net=Sum("net"), talent=Sum("talent_amount"))
        .order_by("-net")[:20]
    )
    by_category = (
        Split.objects.values("payment__request__category__name_es").annotate(n=Count("id"), net=Sum("net")).order_by("-net")
    )
    usage = Generation.objects.values("kind").annotate(n=Count("id"), chars=Sum("chars"))
    return render(request, "tlv/staff/reports.html", {
        "rows": rows, "by_voice": by_voice, "by_category": by_category, "usage": usage,
        "funnel": LicenseRequest.objects.values("status").annotate(n=Count("id")),
    })


@staff_required
def tools(request):
    form = forms.AudioCheckForm(request.POST or None, request.FILES or None)
    result = None
    if request.method == "POST" and form.is_valid():
        result = dispute_service.identify_audio(form.cleaned_data["file"].read())
    engine_status, engine_error = None, ""
    if request.GET.get("motor"):
        try:
            engine_status = get_engine().status()
        except EngineError as exc:
            engine_error = str(exc)
    if request.GET.get("motor"):
        import subprocess

        from ..services import audio

        try:
            out = subprocess.run([audio.ffmpeg_bin(), "-version"], capture_output=True, timeout=20).stdout.decode()[:60]
            engine_status = {**(engine_status or {}), "conversor de audio (ffmpeg)": out.splitlines()[0] if out else "no responde"}
        except Exception as exc:
            engine_status = {**(engine_status or {}), "conversor de audio (ffmpeg)": f"NO DISPONIBLE: {exc}"}
    return render(request, "tlv/staff/tools.html", {
        "form": form, "result": result, "engine_status": engine_status, "engine_error": engine_error,
    })


@staff_required
def privacy_requests(request):
    if request.method == "POST":
        dr = get_object_or_404(DataRequest, pk=request.POST.get("request"))
        if request.POST.get("action") == "execute" and dr.kind == DataRequest.Kind.DELETE:
            privacy.delete_account(dr.user, request.user)
            messages.success(request, _("Cuenta suprimida y anonimizada."))
        else:
            dr.status, dr.resolved_at = DataRequest.Status.REJECTED, timezone.now()
            dr.note = (dr.note + "\n" + request.POST.get("note", "")).strip()
            dr.save()
        return redirect("staff_privacy")
    return render(request, "tlv/staff/privacy.html", {"requests": DataRequest.objects.select_related("user")[:200]})
