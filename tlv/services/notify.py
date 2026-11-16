"""
Avisos: cada aviso queda en la campana de la web y, además, se envía por correo
en el idioma del usuario. Si el correo falla, la operación sigue adelante.

Uso:  notify(user, N_("Título con %(dato)s"), N_("Cuerpo..."), url="/ruta/", dato="x")
"""
import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import translation
from django.utils.translation import gettext

from ..models import Notification, User

log = logging.getLogger("tlv")


def N_(text):
    """Marca un texto para traducir más tarde, en el idioma de quien lo recibe."""
    return text


def send_email(to, title, text, body="", link="", footer="", button=""):
    """Un correo con versión de texto y versión HTML sencilla."""
    html = render_to_string("tlv/email/notice.html", {
        "title": title, "body": body, "link": link, "footer": footer,
        "button": button or gettext("Abrir en Todas las Voces"),
    })
    msg = EmailMultiAlternatives(f"[Todas las Voces] {title}", text, settings.DEFAULT_FROM_EMAIL, [to])
    msg.attach_alternative(html, "text/html")
    msg.send()


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
        if email and settings.TLV_EMAIL_CONFIGURED:
            link = f"{settings.TLV_SITE_URL}{url}" if url.startswith("/") else url
            footer = gettext("Este es un aviso automático de Todas las Voces.")
            text = "\n\n".join(part for part in (body_txt, link, "—\n" + footer) if part)
            try:
                send_email(user.email, title_txt, text, body=body_txt, link=link, footer=footer)
                note.emailed = True
                note.save(update_fields=["emailed"])
            except Exception as exc:  # el aviso en la web ya está guardado
                log.warning("No se pudo enviar el correo a %s: %s", user.email, exc)
    return note


def notify_staff(title, body="", url="", **params):
    for user in User.objects.filter(is_active=True, role=User.Role.STAFF):
        notify(user, title, body, url, **params)
