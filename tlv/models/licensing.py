"""Solicitudes de licencia, licencias, mensajes y locuciones generadas."""
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .accounts import ClientProfile, private_upload
from .voices import UseCategory, Voice

TERRITORIES = [
    ("es", _("España")),
    ("eu", _("Unión Europea")),
    ("latam", _("Latinoamérica")),
    ("world", _("Todo el mundo")),
]
CHANNELS = [
    ("tv", _("Televisión")),
    ("radio", _("Radio")),
    ("online", _("Web y vídeo online")),
    ("social", _("Redes sociales")),
    ("cinema", _("Cine")),
    ("podcast", _("Pódcast y audio digital")),
    ("internal", _("Uso interno")),
    ("phone", _("Telefonía e IVR")),
    ("app", _("Apps y dispositivos")),
    ("events", _("Eventos y punto de venta")),
]
DURATIONS = [
    (3, _("3 meses")),
    (6, _("6 meses")),
    (12, _("12 meses")),
    (24, _("24 meses")),
    (0, _("Sin límite de tiempo")),
]


def upload_generation(instance, filename):
    return private_upload("locuciones")(instance, filename)


def upload_attachment(instance, filename):
    return private_upload("adjuntos")(instance, filename)


def _code(prefix):
    return f"{prefix}-{timezone.now():%y}{secrets.token_hex(4).upper()}"


class LicenseRequest(models.Model):
    class Status(models.TextChoices):
        SUBMITTED = "submitted", _("Pendiente del locutor")
        INFO = "info", _("El locutor pide más información")
        COUNTER = "counter", _("Contraoferta pendiente del cliente")
        APPROVED = "approved", _("Aprobada, pendiente de pago")
        ACTIVE = "active", _("Licencia activa")
        REJECTED = "rejected", _("Rechazada")
        CANCELLED = "cancelled", _("Cancelada")
        EXPIRED = "expired", _("Licencia caducada")

    code = models.CharField(max_length=20, unique=True, editable=False)
    client = models.ForeignKey(ClientProfile, on_delete=models.PROTECT, related_name="requests")
    voice = models.ForeignKey(Voice, on_delete=models.PROTECT, related_name="requests")
    category = models.ForeignKey(UseCategory, on_delete=models.PROTECT, verbose_name=_("categoría de uso"))

    project_name = models.CharField(_("nombre del proyecto"), max_length=160)
    purpose = models.TextField(_("finalidad"), max_length=1500)
    sector = models.CharField(_("sector o marca anunciante"), max_length=160)
    script = models.TextField(_("guion"))
    territory = models.CharField(_("territorio"), max_length=10, choices=TERRITORIES, default="es")
    channels = models.JSONField(_("canales de difusión"), default=list)
    duration_months = models.PositiveSmallIntegerField(_("duración"), choices=DURATIONS, default=12)
    conditions = models.TextField(_("otras condiciones"), blank=True, max_length=1500)
    # Tono y ajustes de interpretación que pide el cliente (ver services/direction.py).
    direction = models.JSONField(_("dirección de voz"), default=dict, blank=True)

    quoted_price = models.DecimalField(max_digits=10, decimal_places=2)
    agreed_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    counter_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    max_takes = models.PositiveSmallIntegerField(default=5)

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.SUBMITTED)
    decision_note = models.TextField(blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    blocked = models.BooleanField(default=False)
    blocked_reason = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("solicitud de licencia")
        verbose_name_plural = _("solicitudes de licencia")

    def __str__(self):
        return f"{self.code} · {self.project_name}"

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = _code("SL")
        super().save(*args, **kwargs)

    @property
    def price(self):
        return self.agreed_price if self.agreed_price is not None else self.quoted_price

    @property
    def channel_labels(self):
        labels = dict(CHANNELS)
        return [labels.get(c, c) for c in self.channels]

    @property
    def open_for_talent(self):
        return self.status in (self.Status.SUBMITTED, self.Status.INFO)

    @property
    def takes_used(self):
        return self.generations.filter(kind=Generation.Kind.FINAL, ok=True).count()


class RequestMessage(models.Model):
    request = models.ForeignKey(LicenseRequest, on_delete=models.CASCADE, related_name="messages")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    body = models.TextField(max_length=3000)
    system = models.BooleanField(default=False)
    attachment = models.FileField(upload_to=upload_attachment, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]


class License(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", _("Vigente")
        SUSPENDED = "suspended", _("Suspendida")
        EXPIRED = "expired", _("Caducada")
        REVOKED = "revoked", _("Anulada")

    request = models.OneToOneField(LicenseRequest, on_delete=models.PROTECT, related_name="license")
    code = models.CharField(max_length=20, unique=True, editable=False)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    issued_at = models.DateTimeField(default=timezone.now)
    valid_until = models.DateField(null=True, blank=True)
    terms = models.JSONField(default=dict)
    contract_code = models.CharField(max_length=30, blank=True)

    class Meta:
        ordering = ["-issued_at"]
        verbose_name = _("licencia")
        verbose_name_plural = _("licencias")

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = _code("LIC")
        super().save(*args, **kwargs)

    def __str__(self):
        return self.code

    @property
    def in_force(self):
        if self.status != self.Status.ACTIVE:
            return False
        return self.valid_until is None or self.valid_until >= timezone.localdate()


class Generation(models.Model):
    """Cada audio generado: demo, prueba interna o locución final con licencia."""

    class Kind(models.TextChoices):
        DEMO = "demo", _("Demostración")
        PREVIEW = "preview", _("Prueba interna")
        FINAL = "final", _("Locución final")

    kind = models.CharField(max_length=10, choices=Kind.choices)
    voice = models.ForeignKey(Voice, on_delete=models.PROTECT, related_name="generations")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    request = models.ForeignKey(
        LicenseRequest, null=True, blank=True, on_delete=models.PROTECT, related_name="generations"
    )
    text = models.TextField()
    text_hash = models.CharField(max_length=64)
    chars = models.PositiveIntegerField(default=0)
    take = models.PositiveSmallIntegerField(default=1)
    engine = models.CharField(max_length=30)
    engine_model = models.CharField(max_length=60, blank=True)
    external_voice_id = models.CharField(max_length=120, blank=True)
    contract_code = models.CharField(max_length=30, blank=True)
    watermark_id = models.PositiveIntegerField(unique=True, null=True)
    file_wav = models.FileField(upload_to=upload_generation, blank=True)
    file_mp3 = models.FileField(upload_to=upload_generation, blank=True)
    sha_wav = models.CharField(max_length=64, blank=True, db_index=True)
    sha_mp3 = models.CharField(max_length=64, blank=True, db_index=True)
    duration = models.FloatField(null=True, blank=True)
    ok = models.BooleanField(default=True)
    error = models.CharField(max_length=300, blank=True)
    direction = models.JSONField(default=dict, blank=True)
    downloads = models.PositiveIntegerField(default=0)
    first_download_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("locución generada")
        verbose_name_plural = _("locuciones generadas")
