"""
Avisos: cada aviso queda en la campana de la web y, además, se envía por correo
en el idioma del usuario. Si el correo falla, la operación sigue adelante.

Uso:  notify(user, N_("Título con %(dato)s"), N_("Cuerpo..."), url="/ruta/", dato="x")
"""
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.utils import translation
from django.utils.translation import gettext

from ..models import Notification, User

log = logging.getLogger("tlv")


def N_(text):
    """Marca un texto para traducir más tarde, en el idioma de quien lo recibe."""
    return text


def notify(user, title, body="", url="", email=True, **params):
    if user is None or user.deleted_at:
        return None
    lang = user.language or "es"
    if url.startswith("/") and lang != "es" and not url.startswith(f"/{lang}/"):
        url = f"/{lang}{url}"          # las páginas en inglés viven bajo /en/
    with translation.override(lang):
        title_txt = gettext(title) % params if params else gettext(title)
        body_txt = (gettext(body) % params if params else gettext(body)) if body else ""
        note = Notification.objects.create(user=user, title=title_txt[:200], body=body_txt, url=url)
        if email:
            link = f"{settings.TLV_SITE_URL}{url}" if url.startswith("/") else url
            footer = gettext("Este es un aviso automático de Todas las Voces.")
            text = "\n\n".join(part for part in (body_txt, link, "—\n" + footer) if part)
            try:
                send_mail(f"[Todas las Voces] {title_txt}", text, settings.DEFAULT_FROM_EMAIL, [user.email])
                note.emailed = True
                note.save(update_fields=["emailed"])
            except Exception as exc:  # el aviso en la web ya está guardado
                log.warning("No se pudo enviar el correo a %s: %s", user.email, exc)
    return note


def notify_staff(title, body="", url="", **params):
    for user in User.objects.filter(is_active=True, role=User.Role.STAFF):
        notify(user, title, body, url, **params)
