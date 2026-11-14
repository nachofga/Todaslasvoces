"""Registro de trazabilidad, avisos y disputas."""
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .accounts import private_upload
from .licensing import Generation, LicenseRequest
from .voices import Voice


def upload_evidence(instance, filename):
    return private_upload("disputas")(instance, filename)


class LedgerEntry(models.Model):
    """
    Registro encadenado: cada entrada guarda la huella de la anterior y va
    firmada. Si alguien modifica o borra una fila, la cadena deja de cuadrar.
    """

    seq = models.PositiveIntegerField(unique=True)
    created_at = models.DateTimeField(default=timezone.now)
    event = models.CharField(max_length=60, db_index=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    payload = models.JSONField(default=dict)
    prev_hash = models.CharField(max_length=64)
    hash = models.CharField(max_length=64, unique=True)
    key_id = models.CharField(max_length=16)
    signature = models.CharField(max_length=128)

    class Meta:
        ordering = ["-seq"]
        verbose_name = _("entrada del registro")
        verbose_name_plural = _("registro de trazabilidad")

    def __str__(self):
        return f"#{self.seq} {self.event}"


class Notification(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    url = models.CharField(max_length=300, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    emailed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class Dispute(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", _("Abierta")
        INVESTIGATING = "investigating", _("En investigación")
        UPHELD = "upheld", _("Resuelta: uso no autorizado confirmado")
        DISMISSED = "dismissed", _("Resuelta: sin incumplimiento")

    code = models.CharField(max_length=20, unique=True, editable=False)
    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="disputes"
    )
    reporter_email = models.EmailField(_("tu correo"), blank=True)
    voice = models.ForeignKey(Voice, null=True, blank=True, on_delete=models.SET_NULL, related_name="disputes")
    request = models.ForeignKey(
        LicenseRequest, null=True, blank=True, on_delete=models.SET_NULL, related_name="disputes"
    )
    generation = models.ForeignKey(Generation, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    description = models.TextField(_("qué ha pasado"), max_length=4000)
    url = models.URLField(_("dónde se ha publicado"), blank=True, max_length=500)
    evidence = models.FileField(_("prueba (audio o documento)"), upload_to=upload_evidence, blank=True)
    detection = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=14, choices=Status.choices, default=Status.OPEN)
    block_voice = models.BooleanField(default=False)
    block_request = models.BooleanField(default=False)
    block_client = models.BooleanField(default=False)
    resolution = models.TextField(blank=True)
    handled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("disputa")
        verbose_name_plural = _("disputas")

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = f"DSP-{timezone.now():%y}{secrets.token_hex(3).upper()}"
        super().save(*args, **kwargs)

    def __str__(self):
        return self.code

    @property
    def is_open(self):
        return self.status in (self.Status.OPEN, self.Status.INVESTIGATING)


class DisputeNote(models.Model):
    dispute = models.ForeignKey(Dispute, on_delete=models.CASCADE, related_name="notes")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    body = models.TextField(max_length=3000)
    file = models.FileField(upload_to=upload_evidence, blank=True)
    internal = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
