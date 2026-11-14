"""Documentos PDF: certificado de licencia, factura, contrato firmado y justificante de liquidación."""
import io

from django.conf import settings
from django.utils import timezone, translation
from django.utils.translation import gettext as _
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from ..models import CHANNELS, DURATIONS, TERRITORIES, PlatformSettings

INK = colors.HexColor("#17153B")
MUTED = colors.HexColor("#5E5B7A")
LINE = colors.HexColor("#D9D6EA")


def eur(value):
    """1234.5 -> '1,234.50 €' (punto decimal, coma de miles)."""
    return f"{value:,.2f} €"


def _styles():
    base = getSampleStyleSheet()
    return {
        "h1": ParagraphStyle("h1", parent=base["Title"], fontName="Helvetica-Bold", fontSize=20, textColor=INK, alignment=0, spaceAfter=4),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=11.5, textColor=INK, spaceBefore=10, spaceAfter=4),
        "p": ParagraphStyle("p", parent=base["BodyText"], fontName="Helvetica", fontSize=9.5, leading=13.5, textColor=INK),
        "small": ParagraphStyle("small", parent=base["BodyText"], fontName="Helvetica", fontSize=8, leading=11, textColor=MUTED),
        "right": ParagraphStyle("right", parent=base["BodyText"], fontName="Helvetica", fontSize=9.5, alignment=TA_RIGHT, textColor=INK),
    }


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, 10 * mm, f"Todas las Voces · {settings.TLV_SITE_URL}")
    canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"{doc.page}")
    canvas.restoreState()


def _build(story, title):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=18 * mm,
        title=title, author="Todas las Voces",
    )
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()


def _grid(rows, widths=(48 * mm, 126 * mm)):
    st = _styles()
    data = [[Paragraph(f"<font color='#5E5B7A'>{k}</font>", st["p"]), Paragraph(str(v), st["p"])] for k, v in rows]
    table = Table(data, colWidths=widths)
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return table


def _qr(url, size=30 * mm):
    code = qr.QrCodeWidget(url)
    x0, y0, x1, y1 = code.getBounds()
    drawing = Drawing(size, size, transform=[size / (x1 - x0), 0, 0, size / (y1 - y0), 0, 0])
    drawing.add(code)
    return drawing


def verify_url(lic):
    return f"{settings.TLV_SITE_URL}/verificar/{lic.code}/"


def license_certificate(lic, language="es"):
    with translation.override(language):
        st = _styles()
        req = lic.request
        territory = dict(TERRITORIES).get(req.territory, req.territory)
        duration = dict(DURATIONS).get(req.duration_months, req.duration_months)
        until = lic.valid_until.strftime("%d/%m/%Y") if lic.valid_until else _("Sin límite de tiempo")
        head = Table(
            [[[Paragraph(_("Certificado de licencia de voz"), st["h1"]),
               Paragraph(f"{_('Licencia')} <b>{lic.code}</b> · {_('Solicitud')} {req.code}", st["p"]),
               Paragraph(_("Este documento acredita que la locución identificada se ha generado con inteligencia artificial "
                           "con la autorización expresa del locutor titular de la voz, en las condiciones que se indican."), st["small"])],
              _qr(verify_url(lic))]],
            colWidths=(140 * mm, 34 * mm),
        )
        head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
        story = [head, Spacer(1, 6)]
        story += [Paragraph(_("Partes"), st["h2"]), _grid([
            (_("Voz"), f"{req.voice.name} — {req.voice.talent.stage_name}"),
            (_("Licenciatario"), f"{req.client.company_name} · {req.client.tax_id}"),
            (_("Contrato del locutor"), lic.contract_code or "—"),
        ])]
        story += [Paragraph(_("Uso autorizado"), st["h2"]), _grid([
            (_("Proyecto"), req.project_name),
            (_("Categoría de uso"), req.category.name),
            (_("Finalidad"), req.purpose),
            (_("Sector o marca"), req.sector),
            (_("Territorio"), str(territory)),
            (_("Canales de difusión"), ", ".join(str(c) for c in req.channel_labels) or "—"),
            (_("Duración"), f"{duration} · {_('emitida el')} {timezone.localtime(lic.issued_at):%d/%m/%Y} · {_('válida hasta')}: {until}"),
            (_("Otras condiciones"), req.conditions or "—"),
            (_("Huella del guion (SHA-256)"), f"<font size=7.5>{lic.terms.get('script_hash', '')}</font>"),
        ])]
        gens = list(req.generations.filter(kind="final", ok=True).order_by("take"))
        if gens:
            rows = [(f"{_('Toma')} {g.take} · {timezone.localtime(g.created_at):%d/%m/%Y %H:%M}",
                     f"ID {g.watermark_id} · {g.duration:.1f} s · {g.engine} {g.engine_model}<br/>"
                     f"<font size=7>MP3 {g.sha_mp3}</font>") for g in gens]
            story += [Paragraph(_("Locuciones generadas con esta licencia"), st["h2"]), _grid(rows)]
        story += [Paragraph(_("Condiciones"), st["h2"]), Paragraph(_(
            "La licencia cubre únicamente el guion, el proyecto, el territorio, los canales y la duración indicados. "
            "Cualquier otro uso necesita una licencia nueva. No se permite usar la locución para entrenar modelos de IA "
            "ni para crear otras voces. Cuando la normativa lo exija, el licenciatario debe informar de que la voz "
            "se ha generado con inteligencia artificial. Cada audio lleva una marca de agua invisible que lo vincula con esta licencia."
        ), st["p"]), Spacer(1, 6), Paragraph(
            f"{_('Comprueba la validez de esta licencia en')} {verify_url(lic)}", st["small"])]
        return _build(story, f"Licencia {lic.code}")


def invoice_pdf(invoice, language="es"):
    with translation.override(language):
        st = _styles()
        d = invoice.data
        title = _("Factura proforma") if invoice.proforma else _("Factura")
        story = [Paragraph(title, st["h1"]),
                 Paragraph(f"<b>{invoice.number}</b> · {timezone.localtime(invoice.issued_at):%d/%m/%Y}", st["p"]), Spacer(1, 8)]
        parties = Table([[
            [Paragraph(f"<b>{_('Emisor')}</b>", st["p"]), Paragraph(d["seller"]["name"], st["p"]),
             Paragraph(d["seller"].get("tax_id") or "", st["p"]), Paragraph(d["seller"].get("address") or "", st["p"])],
            [Paragraph(f"<b>{_('Cliente')}</b>", st["p"]), Paragraph(d["buyer"]["name"], st["p"]),
             Paragraph(d["buyer"].get("tax_id") or "", st["p"]), Paragraph(d["buyer"].get("address") or "", st["p"])],
        ]], colWidths=(87 * mm, 87 * mm))
        parties.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
        story += [parties, Spacer(1, 12)]
        rows = [[Paragraph(f"<b>{_('Concepto')}</b>", st["p"]), Paragraph(f"<b>{_('Importe')}</b>", st["right"])]]
        for line in d["lines"]:
            rows.append([Paragraph(line["concept"], st["p"]), Paragraph(eur(float(line["amount"])), st["right"])])
        rows += [
            [Paragraph(_("Base imponible"), st["right"]), Paragraph(eur(invoice.net), st["right"])],
            [Paragraph(f"IVA {d.get('vat_percent')} %", st["right"]), Paragraph(eur(invoice.vat), st["right"])],
            [Paragraph(f"<b>{_('Total')}</b>", st["right"]), Paragraph(f"<b>{eur(invoice.total)}</b>", st["right"])],
        ]
        table = Table(rows, colWidths=(134 * mm, 40 * mm))
        table.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -4), 0.4, LINE), ("LINEABOVE", (0, -1), (-1, -1), 0.8, INK),
                                   ("LEFTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 5),
                                   ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
        story += [table, Spacer(1, 10), Paragraph(
            f"{_('Forma de pago')}: {_('transferencia bancaria')} · {_('Concepto')}: {d.get('payment_reference', '')}", st["small"])]
        if invoice.proforma:
            story.append(Paragraph(_("Documento proforma sin validez fiscal. La factura definitiva se emitirá aparte."), st["small"]))
        return _build(story, invoice.number)


def contract_pdf(contract):
    st = _styles()
    story = [Paragraph("Contrato de cesión de voz digital" if contract.language == "es" else "Digital voice licence agreement", st["h1"]),
             Paragraph(f"<b>{contract.code}</b> · v{contract.version}", st["p"]), Spacer(1, 6)]
    for block in contract.body.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        style = st["h2"] if (len(block) < 80 and block[:1].isdigit()) else st["p"]
        story += [Paragraph(block.replace("\n", "<br/>"), style), Spacer(1, 3)]
    story += [Paragraph("Categorías de uso" if contract.language == "es" else "Use categories", st["h2"]), _grid([
        (c["name"], ("AUTORIZADA" if c["allowed"] else "NO AUTORIZADA") + (f" · {c['notes']}" if c.get("notes") else ""))
        for c in contract.consent_snapshot
    ], widths=(90 * mm, 84 * mm))]
    story += [Paragraph("Firma electrónica" if contract.language == "es" else "Electronic signature", st["h2"]), _grid([
        ("Firmado por", contract.signed_name),
        ("Fecha y hora", f"{timezone.localtime(contract.signed_at):%d/%m/%Y %H:%M:%S %Z}"),
        ("Dirección IP", contract.signed_ip or "—"),
        ("Huella del texto (SHA-256)", f"<font size=7.5>{contract.body_hash}</font>"),
        ("Consentimiento de tratamiento de voz", "Sí, expreso" if contract.biometric_consent else "No"),
    ])]
    return _build(story, contract.code)


def payout_statement(split, language="es"):
    with translation.override(language):
        st = _styles()
        cfg = PlatformSettings.load()
        req = split.payment.request
        rows = [
            (_("Proyecto"), f"{req.project_name} · {req.client.company_name}"),
            (_("Voz"), req.voice.name),
            (_("Licencia"), req.license.code if hasattr(req, "license") else "—"),
            (_("Cobro confirmado"), f"{timezone.localtime(split.payment.confirmed_at):%d/%m/%Y}" if split.payment.confirmed_at else "—"),
            (_("Importe de la licencia (sin IVA)"), eur(split.net)),
            (f"{_('Comisión de la plataforma')} ({split.fee_percent} %)", f"- {eur(split.platform_amount)}"),
        ]
        if split.representative_amount:
            rows.append((f"{_('Representante')} ({split.representative_percent} %)", f"- {eur(split.representative_amount)}"))
        rows += [
            (f"<b>{_('Tu parte')}</b>", f"<b>{eur(split.talent_amount)}</b>"),
            (_("Estado"), split.get_talent_payout_display() + (f" · {timezone.localtime(split.talent_paid_at):%d/%m/%Y}" if split.talent_paid_at else "")),
        ]
        story = [Paragraph(_("Justificante de liquidación"), st["h1"]),
                 Paragraph(f"{cfg.company_name} · {split.talent.legal_name} ({split.talent.stage_name})", st["p"]),
                 Spacer(1, 8), _grid(rows, widths=(90 * mm, 84 * mm)), Spacer(1, 8),
                 Paragraph(_("Documento informativo. No sustituye a la factura que corresponda según tu situación fiscal."), st["small"])]
        return _build(story, f"Liquidación {split.payment.reference}")
