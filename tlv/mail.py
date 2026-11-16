"""
Envío de correo por API (HTTPS), sin SMTP.

Muchos alojamientos bloquean los puertos de correo (25, 465, 587). Estos
proveedores aceptan el correo por una llamada web normal, que nunca se bloquea:

    Brevo   -> variable BREVO_API_KEY   (plan gratuito: 300 correos al día)
    Resend  -> variable RESEND_API_KEY  (plan gratuito: 100 correos al día)

Django usa esta clase como «backend» de correo, así que el resto del código
sigue llamando a send_mail() como siempre.
"""
import logging
from email.utils import parseaddr

import requests
from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

log = logging.getLogger("tlv")


class MailError(Exception):
    pass


def _html_of(message):
    for content, mimetype in getattr(message, "alternatives", None) or []:
        if mimetype == "text/html":
            return content
    return None


def _send_brevo(message, key, timeout):
    name, email = parseaddr(message.from_email or settings.DEFAULT_FROM_EMAIL)
    body = {
        "sender": {"email": email, **({"name": name} if name else {})},
        "to": [{"email": parseaddr(a)[1]} for a in message.to],
        "subject": message.subject,
        "textContent": message.body or " ",
    }
    html = _html_of(message)
    if html:
        body["htmlContent"] = html
    if message.reply_to:
        body["replyTo"] = {"email": parseaddr(message.reply_to[0])[1]}
    return requests.post(
        "https://api.brevo.com/v3/smtp/email", json=body, timeout=timeout,
        headers={"api-key": key, "accept": "application/json"},
    )


def _send_resend(message, key, timeout):
    body = {
        "from": message.from_email or settings.DEFAULT_FROM_EMAIL,
        "to": [parseaddr(a)[1] for a in message.to],
        "subject": message.subject,
        "text": message.body or " ",
    }
    html = _html_of(message)
    if html:
        body["html"] = html
    if message.reply_to:
        body["reply_to"] = parseaddr(message.reply_to[0])[1]
    return requests.post(
        "https://api.resend.com/emails", json=body, timeout=timeout, headers={"Authorization": f"Bearer {key}"},
    )


PROVIDERS = {"brevo": ("BREVO_API_KEY", _send_brevo), "resend": ("RESEND_API_KEY", _send_resend)}


class ApiEmailBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        provider = settings.TLV_EMAIL_PROVIDER
        setting_name, sender = PROVIDERS[provider]
        key = getattr(settings, setting_name)
        sent = 0
        for message in email_messages or []:
            if not message.to:
                continue
            try:
                resp = sender(message, key, settings.EMAIL_TIMEOUT)
                if resp.status_code >= 300:
                    raise MailError(f"{provider} ({resp.status_code}): {resp.text[:300]}")
                sent += 1
            except (requests.RequestException, MailError) as exc:
                log.warning("Correo no enviado a %s: %s", message.to, exc)
                if not self.fail_silently:
                    raise MailError(str(exc)) from exc
        return sent
