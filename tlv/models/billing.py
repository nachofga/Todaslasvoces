"""Ajustes de la plataforma, cobros, facturas y reparto de ingresos."""
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .accounts import ClientProfile, Representative, TalentProfile, private_upload
from .licensing import LicenseRequest


def upload_receipt(instance, filename):
    return private_upload("justificantes")(instance, filename)


def default_territory_factors():
    return {"es": 1.0, "eu": 1.6, "latam": 1.5, "world": 2.5}


def default_duration_factors():
    return {"3": 0.6, "6": 0.8, "12": 1.0, "24": 1.6, "0": 3.0}


class PlatformSettings(models.Model):
    """Fila única con todo lo que gestión puede cambiar sin tocar código."""

    platform_fee_percent = models.DecimalField(_("comisión de la plataforma (%)"), max_digits=5, decimal_places=2, default=Decimal("20"))
    vat_percent = models.DecimalField(_("IVA (%)"), max_digits=5, decimal_places=2, default=Decimal("21"))
    territory_factors = models.JSONField(_("multiplicadores por territorio"), default=default_territory_factors)
    duration_factors = models.JSONField(_("multiplicadores por duración"), default=default_duration_factors)

    demo_max_chars = models.PositiveIntegerField(_("máximo de caracteres por demo"), default=220)
    demo_daily_limit = models.PositiveIntegerField(_("demos por cliente y día"), default=10)
    script_max_chars = models.PositiveIntegerField(_("máximo de caracteres por guion"), default=3000)
    default_max_takes = models.PositiveSmallIntegerField(_("tomas incluidas por licencia"), default=5)
    min_training_seconds = models.PositiveIntegerField(_("segundos mínimos de muestras"), default=60)
    require_client_validation = models.BooleanField(
        _("los clientes necesitan validación para pedir licencias"), default=True
    )

    company_name = models.CharField(_("razón social"), max_length=200, default="Todas las Voces, S.L. (en constitución)")
    company_tax_id = models.CharField(_("CIF"), max_length=30, blank=True)
    company_address = models.CharField(_("domicilio"), max_length=250, blank=True)
    bank_holder = models.CharField(_("titular de la cuenta"), max_length=200, blank=True)
    bank_iban = models.CharField(_("IBAN para transferencias"), max_length=42, blank=True)
    bank_name = models.CharField(_("banco"), max_length=120, blank=True)
    invoice_series = models.CharField(_("serie de facturación"), max_length=10, default="TLV")
    invoice_proforma = models.BooleanField(
        _("emitir como proforma"), default=True,
        help_text=_("Mantener activado hasta que el asesor fiscal valide la facturación."),
    )
    support_email = models.EmailField(_("correo de soporte"), blank=True)

    class Meta:
        verbose_name = _("ajustes de la plataforma")
        verbose_name_plural = _("ajustes de la plataforma")

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return "Ajustes"


class Payment(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", _("Pendiente de transferencia")
        REPORTED = "reported", _("Transferencia comunicada")
        CONFIRMED = "confirmed", _("Cobro confirmado")
        CANCELLED = "cancelled", _("Anulado")
        REFUNDED = "refunded", _("Devuelto")

    class Method(models.TextChoices):
        TRANSFER = "transfer", _("Transferencia bancaria")
        CARD = "card", _("Tarjeta (pasarela pendiente)")

    request = models.OneToOneField(LicenseRequest, on_delete=models.PROTECT, related_name="payment")
    reference = models.CharField(max_length=30, unique=True)
    method = models.CharField(max_length=10, choices=Method.choices, default=Method.TRANSFER)
    net = models.DecimalField(max_digits=10, decimal_places=2)
    vat_percent = models.DecimalField(max_digits=5, decimal_places=2)
    vat = models.DecimalField(max_digits=10, decimal_places=2)
    total = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    receipt = models.FileField(upload_to=upload_receipt, blank=True)
    reported_at = models.DateTimeField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    provider_ref = models.CharField(max_length=120, blank=True)  # para la futura pasarela
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("cobro")
        verbose_name_plural = _("cobros")

    def __str__(self):
        return self.reference


class Invoice(models.Model):
    payment = models.OneToOneField(Payment, on_delete=models.PROTECT, related_name="invoice")
    client = models.ForeignKey(ClientProfile, on_delete=models.PROTECT, related_name="invoices")
    series = models.CharField(max_length=10)
    year = models.PositiveSmallIntegerField()
    sequence = models.PositiveIntegerField()
    number = models.CharField(max_length=30, unique=True)
    proforma = models.BooleanField(default=True)
    issued_at = models.DateTimeField(default=timezone.now)
    data = models.JSONField(default=dict)
    net = models.DecimalField(max_digits=10, decimal_places=2)
    vat = models.DecimalField(max_digits=10, decimal_places=2)
    total = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        ordering = ["-issued_at"]
        unique_together = [("series", "year", "sequence")]
        verbose_name = _("factura")
        verbose_name_plural = _("facturas")

    def __str__(self):
        return self.number


class Split(models.Model):
    """Reparto de un cobro entre locutor, plataforma y representante."""

    class Payout(models.TextChoices):
        PENDING = "pending", _("Pendiente de pago")
        PAID = "paid", _("Pagado")

    payment = models.OneToOneField(Payment, on_delete=models.PROTECT, related_name="split")
    talent = models.ForeignKey(TalentProfile, on_delete=models.PROTECT, related_name="splits")
    representative = models.ForeignKey(
        Representative, null=True, blank=True, on_delete=models.SET_NULL, related_name="splits"
    )
    fee_percent = models.DecimalField(max_digits=5, decimal_places=2)
    representative_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    net = models.DecimalField(max_digits=10, decimal_places=2)
    platform_amount = models.DecimalField(max_digits=10, decimal_places=2)
    talent_amount = models.DecimalField(max_digits=10, decimal_places=2)
    representative_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    talent_payout = models.CharField(max_length=10, choices=Payout.choices, default=Payout.PENDING)
    talent_paid_at = models.DateTimeField(null=True, blank=True)
    talent_payout_ref = models.CharField(max_length=120, blank=True)
    representative_payout = models.CharField(max_length=10, choices=Payout.choices, default=Payout.PENDING)
    representative_paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("reparto")
        verbose_name_plural = _("repartos")
