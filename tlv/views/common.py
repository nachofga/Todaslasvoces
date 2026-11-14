"""Utilidades compartidas por las vistas: permisos por rol y helpers."""
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.utils.translation import gettext as _


def role_required(check, need_verified_email=True):
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapper(request, *args, **kwargs):
            user = request.user
            if not check(user):
                raise PermissionDenied
            if user.suspended and not user.is_manager:
                messages.error(request, _("Tu cuenta está suspendida. Escribe a soporte."))
                return redirect("account")
            if need_verified_email and not user.email_verified and not user.is_manager:
                messages.warning(request, _("Confirma tu correo electrónico para continuar."))
                return redirect("account")
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


talent_required = role_required(lambda u: u.is_talent and hasattr(u, "talent"))
client_required = role_required(lambda u: u.is_client and hasattr(u, "client"))
staff_required = role_required(lambda u: u.is_manager)


def client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR")) or None


def request_meta(request):
    return {"ip": client_ip(request), "ua": request.META.get("HTTP_USER_AGENT", "")}


def flow(request, func, *args, success=None, **kwargs):
    """Ejecuta una operación de negocio y convierte sus errores en mensajes para el usuario."""
    from ..services.voiceflow import FlowError

    try:
        result = func(*args, **kwargs)
    except FlowError as exc:
        messages.error(request, str(exc))
        return None
    if success:
        messages.success(request, success)
    return result if result is not None else True


def split_of(req):
    """Reparto de la solicitud, si ya se ha cobrado."""
    payment = getattr(req, "payment", None) if hasattr(req, "payment") else None
    if payment is not None and hasattr(payment, "split"):
        return payment.split
    return None
