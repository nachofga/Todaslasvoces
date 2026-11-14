"""Alta, acceso, confirmación de correo, avisos y cuenta (incluidos los derechos RGPD)."""
import json

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.cache import cache
from django.core.mail import send_mail
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from .. import forms
from ..models import ClientProfile, DataRequest, TalentProfile, User, Voice
from ..services import ledger, privacy
from ..services.notify import N_, notify_staff
from .common import client_ip

EMAIL_SALT = "tlv-confirmar-correo"


def send_verification(request, user):
    if not settings.TLV_EMAIL_CONFIGURED:
        if not user.email_verified:
            user.email_verified_at = timezone.now()
            user.save(update_fields=["email_verified_at"])
            ledger.record("account.email_verified", user, automatic=True, reason="sin servidor de correo")
        return
    token = signing.dumps({"u": user.pk, "e": user.email}, salt=EMAIL_SALT)
    link = settings.TLV_SITE_URL + reverse("verify_email", args=[token])
    with translation.override(user.language):
        body = _("Hola:\n\nConfirma tu correo para activar tu cuenta en Todas las Voces:\n\n%(link)s\n\nEl enlace caduca en 3 días.") % {"link": link}
        try:
            send_mail(_("[Todas las Voces] Confirma tu correo"), body, settings.DEFAULT_FROM_EMAIL, [user.email])
        except Exception:
            messages.warning(request, _("No hemos podido enviarte el correo de confirmación. Inténtalo desde tu cuenta."))


def signup(request):
    return render(request, "tlv/auth/signup.html")


def _lang(request):
    return (translation.get_language() or "es")[:2]


def signup_talent(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = forms.TalentSignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        with transaction.atomic():
            user = User.objects.create_user(d["email"], d["password"], role=User.Role.TALENT, language=_lang(request),
                                            terms_accepted_at=timezone.now())
            talent = TalentProfile.objects.create(user=user, legal_name=d["legal_name"], stage_name=d["stage_name"])
            Voice.objects.create(talent=talent, name=d["stage_name"])
            ledger.record("account.created", user, role="talent", terms=True, adult_statement=True, ip=client_ip(request))
        send_verification(request, user)
        auth_login(request, user)
        messages.success(request, _("Cuenta creada. Te hemos enviado un correo para confirmarla.") if settings.TLV_EMAIL_CONFIGURED else _("Cuenta creada."))
        return redirect("talent_home")
    return render(request, "tlv/auth/signup_form.html", {"form": form, "kind": "talent"})


def signup_client(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = forms.ClientSignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        with transaction.atomic():
            user = User.objects.create_user(d["email"], d["password"], role=User.Role.CLIENT, language=_lang(request),
                                            terms_accepted_at=timezone.now())
            ClientProfile.objects.create(
                user=user, company_name=d["company_name"], tax_id=d["tax_id"], contact_name=d["contact_name"],
                billing_address=d["billing_address"], postal_code=d["postal_code"], city=d["city"], country=d["country"],
            )
            ledger.record("account.created", user, role="client", terms=True, ip=client_ip(request))
        notify_staff(N_("Nuevo cliente pendiente de validar: %(name)s"), "", "/gestion/clientes/", name=d["company_name"])
        send_verification(request, user)
        auth_login(request, user)
        messages.success(request, _("Cuenta creada. Te hemos enviado un correo para confirmarla.") if settings.TLV_EMAIL_CONFIGURED else _("Cuenta creada."))
        return redirect("client_home")
    return render(request, "tlv/auth/signup_form.html", {"form": form, "kind": "client"})


def login_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    key = f"tlv-login-{client_ip(request)}"
    blocked = cache.get(key, 0) >= 8
    form = forms.LoginForm(request, data=request.POST or None)
    if request.method == "POST":
        if blocked:
            messages.error(request, _("Demasiados intentos. Espera 15 minutos."))
        elif form.is_valid():
            user = form.get_user()
            auth_login(request, user)
            cache.delete(key)
            translation.activate(user.language)
            nxt = request.POST.get("next") or request.GET.get("next") or ""
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else "dashboard")
        else:
            cache.set(key, cache.get(key, 0) + 1, 900)
    return render(request, "tlv/auth/login.html", {"form": form, "next": request.GET.get("next", "")})


@require_POST
def logout_view(request):
    auth_logout(request)
    return redirect("home")


def verify_email(request, token):
    try:
        data = signing.loads(token, salt=EMAIL_SALT, max_age=3 * 24 * 3600)
        user = User.objects.get(pk=data["u"], email=data["e"])
    except Exception:
        messages.error(request, _("El enlace no es válido o ha caducado. Pide uno nuevo desde tu cuenta."))
        return redirect("login")
    if not user.email_verified:
        user.email_verified_at = timezone.now()
        user.save(update_fields=["email_verified_at"])
        ledger.record("account.email_verified", user)
    messages.success(request, _("Correo confirmado."))
    return redirect("dashboard" if request.user.is_authenticated else "login")


@login_required
@require_POST
def resend_verification(request):
    if not request.user.email_verified:
        send_verification(request, request.user)
        messages.success(request, _("Te hemos enviado otro correo de confirmación."))
    return redirect("account")


@login_required
def dashboard(request):
    user = request.user
    if user.is_manager:
        return redirect("staff_home")
    return redirect("talent_home" if user.is_talent else "client_home")


@login_required
def notifications(request):
    notes = list(request.user.notifications.all()[:100])
    request.user.notifications.filter(read_at=None).update(read_at=timezone.now())
    return render(request, "tlv/auth/notifications.html", {"notes": notes})


@login_required
def account(request):
    user = request.user
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "language" and request.POST.get("language") in dict(settings.LANGUAGES):
            user.language = request.POST["language"]
            user.save(update_fields=["language"])
            translation.activate(user.language)
            response = redirect("account")
            response.set_cookie(settings.LANGUAGE_COOKIE_NAME, user.language, max_age=365 * 24 * 3600)
            return response
        if action == "export":
            payload = json.dumps(privacy.export_user_data(user), ensure_ascii=False, indent=2)
            ledger.record("privacy.export", user)
            response = HttpResponse(payload, content_type="application/json; charset=utf-8")
            response["Content-Disposition"] = 'attachment; filename="mis-datos-todaslasvoces.json"'
            return response
        if action == "delete" and request.POST.get("confirm") == "on":
            if not user.data_requests.filter(kind=DataRequest.Kind.DELETE, status=DataRequest.Status.OPEN).exists():
                DataRequest.objects.create(user=user, kind=DataRequest.Kind.DELETE, note=request.POST.get("note", "")[:1000])
                ledger.record("privacy.delete_requested", user)
                notify_staff(N_("Solicitud de supresión de cuenta: %(email)s"), "", "/gestion/privacidad/", email=user.email)
            messages.success(request, _("Hemos recibido tu solicitud de supresión. La atenderemos en un máximo de 30 días."))
            return redirect("account")
    return render(request, "tlv/auth/account.html", {
        "pending_delete": user.data_requests.filter(kind=DataRequest.Kind.DELETE, status=DataRequest.Status.OPEN).exists(),
    })
