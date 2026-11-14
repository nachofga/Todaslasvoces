"""
Precios, cobros, facturas y reparto de ingresos.

Fórmulas (todas con dos decimales, redondeo al céntimo más cercano):

  presupuesto = tarifa de la voz para la categoría
                x multiplicador de la categoría
                x multiplicador del territorio
                x multiplicador de la duración

  IVA   = neto x IVA% / 100
  total = neto + IVA

  parte de la plataforma  = neto x comisión% / 100
  resto para el locutor   = neto - parte de la plataforma
  parte del representante = resto x %representante / 100
  parte del locutor       = resto - parte del representante

Las restas garantizan que las tres partes suman exactamente el neto.
"""
import secrets
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from ..models import Invoice, License, LicenseRequest, Payment, PlatformSettings, Split, VoiceConsent
from . import ledger
from .policy import script_hash

CENT = Decimal("0.01")


def money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def quote(voice, category, territory, duration_months):
    cfg = PlatformSettings.load()
    consent = VoiceConsent.objects.filter(voice=voice, category=category).first()
    base = consent.price if consent and consent.price is not None else voice.base_price
    territory_factor = Decimal(str(cfg.territory_factors.get(territory, 1)))
    duration_factor = Decimal(str(cfg.duration_factors.get(str(duration_months), 1)))
    return money(Decimal(base) * Decimal(category.price_factor) * territory_factor * duration_factor)


def fee_percent_for(talent):
    cfg = PlatformSettings.load()
    return talent.fee_override if talent.fee_override is not None else cfg.platform_fee_percent


def split_amounts(net, fee_percent, representative_percent=0):
    """Devuelve (plataforma, locutor, representante). Siempre suman `net`."""
    net = money(net)
    platform = money(net * Decimal(fee_percent) / 100)
    rest = net - platform
    representative = money(rest * Decimal(representative_percent) / 100)
    talent = rest - representative
    return platform, talent, representative


def open_payment(req: LicenseRequest):
    """Crea el cobro pendiente cuando la solicitud queda aprobada."""
    cfg = PlatformSettings.load()
    net = money(req.price)
    vat = money(net * cfg.vat_percent / 100)
    payment, _created = Payment.objects.get_or_create(
        request=req,
        defaults=dict(
            reference=f"TLV{timezone.now():%y%m}{secrets.token_hex(3).upper()}",
            net=net, vat_percent=cfg.vat_percent, vat=vat, total=net + vat,
        ),
    )
    return payment


def _next_invoice_number(cfg):
    year = timezone.localdate().year
    last = Invoice.objects.filter(series=cfg.invoice_series, year=year).aggregate(m=Max("sequence"))["m"] or 0
    seq = last + 1
    prefix = "PRO-" if cfg.invoice_proforma else ""
    return year, seq, f"{prefix}{cfg.invoice_series}-{year}-{seq:05d}"


@transaction.atomic
def confirm_payment(payment: Payment, staff=None):
    """
    Gestión confirma que la transferencia ha llegado. En un solo paso:
    cobro confirmado -> licencia emitida -> factura -> reparto apuntado.
    """
    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    if payment.status == Payment.Status.CONFIRMED:
        return payment
    req = payment.request
    cfg = PlatformSettings.load()
    now = timezone.now()

    payment.status = Payment.Status.CONFIRMED
    payment.confirmed_at = now
    payment.confirmed_by = staff
    payment.save()

    valid_until = None
    if req.duration_months:
        month = now.month - 1 + req.duration_months
        year, month = now.year + month // 12, month % 12 + 1
        valid_until = now.date().replace(year=year, month=month, day=min(now.day, 28))
    contract = req.voice.current_contract()
    lic = License.objects.create(
        request=req, valid_until=valid_until, contract_code=contract.code if contract else "",
        terms={
            "voice": req.voice.name, "talent": req.voice.talent.stage_name, "client": req.client.company_name,
            "client_tax_id": req.client.tax_id, "project": req.project_name, "category": req.category.name_es,
            "category_en": req.category.name_en, "purpose": req.purpose, "sector": req.sector,
            "territory": req.territory, "channels": req.channels, "duration_months": req.duration_months,
            "conditions": req.conditions, "script_hash": script_hash(req.script),
            "price_net": str(payment.net),
        },
    )
    req.status = LicenseRequest.Status.ACTIVE
    req.save(update_fields=["status", "updated_at"])

    year, seq, number = _next_invoice_number(cfg)
    client = req.client
    Invoice.objects.create(
        payment=payment, client=client, series=cfg.invoice_series, year=year, sequence=seq, number=number,
        proforma=cfg.invoice_proforma, net=payment.net, vat=payment.vat, total=payment.total,
        data={
            "seller": {"name": cfg.company_name, "tax_id": cfg.company_tax_id, "address": cfg.company_address},
            "buyer": {
                "name": client.company_name, "tax_id": client.tax_id,
                "address": f"{client.billing_address}, {client.postal_code} {client.city}, {client.country}",
            },
            "lines": [{
                "concept": f"Licencia de uso de voz {lic.code} · {req.voice.name} · {req.project_name}",
                "amount": str(payment.net),
            }],
            "vat_percent": str(payment.vat_percent), "payment_reference": payment.reference,
        },
    )

    talent = req.voice.talent
    rep = talent.representative if talent.representative and talent.representative.active else None
    fee = fee_percent_for(talent)
    rep_percent = rep.share_percent if rep else Decimal("0")
    platform, talent_amount, rep_amount = split_amounts(payment.net, fee, rep_percent)
    Split.objects.create(
        payment=payment, talent=talent, representative=rep, fee_percent=fee, representative_percent=rep_percent,
        net=payment.net, platform_amount=platform, talent_amount=talent_amount, representative_amount=rep_amount,
    )
    ledger.record(
        "payment.confirmed", staff, request=req.code, license=lic.code, payment=payment.reference,
        net=payment.net, total=payment.total, platform=platform, talent=talent_amount, representative=rep_amount,
        invoice=number,
    )
    return payment


def mark_talent_paid(split: Split, reference="", staff=None):
    split.talent_payout = Split.Payout.PAID
    split.talent_paid_at = timezone.now()
    split.talent_payout_ref = reference[:120]
    if split.representative_amount:
        split.representative_payout = Split.Payout.PAID
        split.representative_paid_at = split.talent_paid_at
    split.save()
    ledger.record("payout.paid", staff, payment=split.payment.reference, talent=split.talent_amount, ref=reference)
    return split
