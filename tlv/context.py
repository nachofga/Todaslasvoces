from django.conf import settings

from .services.engines import engine_name


def site(request):
    """Datos comunes a todas las páginas."""
    user = getattr(request, "user", None)
    unread = 0
    if user is not None and user.is_authenticated:
        unread = user.notifications.filter(read_at=None).count()
    return {
        "SITE_URL": settings.TLV_SITE_URL,
        "ENGINE_SIMULATED": engine_name() == "simulated",
        "unread_count": unread,
        "EMAIL_CONFIGURED": settings.TLV_EMAIL_CONFIGURED,
    }
