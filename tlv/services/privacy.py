"""Derechos RGPD: copia de los datos personales y supresión de la cuenta."""
import secrets

from django.db import transaction
from django.utils import timezone

from ..models import DataRequest, Generation, LicenseRequest, Voice
from . import ledger, voiceflow


def export_user_data(user):
    """Todo lo que la plataforma guarda sobre una persona, en un diccionario (se descarga como JSON)."""
    data = {
        "generado": timezone.now().isoformat(),
        "cuenta": {
            "correo": user.email, "nombre": user.get_full_name(), "rol": user.role, "idioma": user.language,
            "alta": user.date_joined.isoformat(),
            "correo_verificado": user.email_verified_at.isoformat() if user.email_verified_at else None,
            "condiciones_aceptadas": user.terms_accepted_at.isoformat() if user.terms_accepted_at else None,
        },
        "avisos": [{"fecha": n.created_at.isoformat(), "titulo": n.title} for n in user.notifications.all()[:500]],
    }
    if hasattr(user, "talent"):
        t = user.talent
        data["locutor"] = {
            "nombre_legal": t.legal_name, "nombre_profesional": t.stage_name, "nif": t.tax_id, "telefono": t.phone,
            "pais": t.country, "ciudad": t.city, "presentacion": t.bio, "identidad": t.identity_status,
            "titular_cuenta": t.payout_holder, "iban": t.payout_iban,
        }
        data["voces"] = [
            {
                "nombre": v.name, "estado": v.status, "motor": v.engine, "creada": v.created_at.isoformat(),
                "muestras": [{"fichero": s.original_name, "segundos": s.duration, "subida": s.created_at.isoformat()} for s in v.samples.all()],
                "consentimientos": [{"categoria": c.category.name_es, "autorizada": c.allowed} for c in v.consents.select_related("category")],
                "contratos": [{"codigo": c.code, "firmado": c.signed_at.isoformat(), "estado": c.status, "ip": c.signed_ip} for c in v.contracts.all()],
                "usos": v.generations.count(),
            }
            for v in t.voices.all()
        ]
        data["ingresos"] = [
            {"cobro": s.payment.reference, "importe": str(s.talent_amount), "estado": s.talent_payout} for s in t.splits.select_related("payment")
        ]
    if hasattr(user, "client"):
        c = user.client
        data["cliente"] = {
            "empresa": c.company_name, "cif": c.tax_id, "contacto": c.contact_name, "telefono": c.phone,
            "direccion": f"{c.billing_address}, {c.postal_code} {c.city}, {c.country}", "estado": c.status,
        }
        data["solicitudes"] = [
            {"codigo": r.code, "proyecto": r.project_name, "voz": r.voice.name, "estado": r.status,
             "fecha": r.created_at.isoformat(), "guion": r.script}
            for r in c.requests.select_related("voice")
        ]
    data["locuciones_generadas"] = [
        {"fecha": g.created_at.isoformat(), "tipo": g.kind, "voz": g.voice.name, "texto": g.text}
        for g in Generation.objects.filter(user=user).select_related("voice")[:2000]
    ]
    return data


@transaction.atomic
def delete_account(user, actor=None):
    """
    Supresión: se retiran las voces (y se borran del proveedor), se eliminan
    grabaciones y documentos, y se anonimiza la cuenta. Se conservan, sin datos
    de contacto, las facturas y el registro de trazabilidad (obligación legal y
    defensa frente a reclamaciones).
    """
    if hasattr(user, "client"):
        R = LicenseRequest.Status
        for req in user.client.requests.filter(status__in=[R.SUBMITTED, R.INFO, R.COUNTER, R.APPROVED]):
            req.status = R.CANCELLED
            req.save()
        c = user.client
        c.contact_name, c.phone, c.website = "(suprimido)", "", ""
        c.save()
    if hasattr(user, "talent"):
        t = user.talent
        for voice in t.voices.all():
            if voice.status != Voice.Status.REVOKED:
                voiceflow.revoke(voice, "Supresión de la cuenta", actor or user)
            for sample in voice.samples.all():
                sample.file.delete(save=False)
                sample.delete()
            for ver in voice.verifications.all():
                ver.recording.delete(save=False)
                ver.delete()
            voice.name, voice.description, voice.tagline = f"Voz retirada {voice.pk}", "", ""
            voice.save()
        if t.identity_document:
            t.identity_document.delete(save=False)
        t.legal_name, t.stage_name = "(suprimido)", f"Locutor {t.pk}"
        t.tax_id = t.phone = t.bio = t.city = t.website = t.payout_holder = t.payout_iban = ""
        t.save()
    ledger.record("account.deleted", actor or user, user=user.pk, role=user.role)
    user.email = f"suprimido-{user.pk}-{secrets.token_hex(4)}@invalid.local"
    user.first_name = user.last_name = ""
    user.is_active, user.deleted_at = False, timezone.now()
    user.set_unusable_password()
    user.save()
    user.notifications.all().delete()
    DataRequest.objects.filter(user=user, status=DataRequest.Status.OPEN).update(
        status=DataRequest.Status.DONE, resolved_at=timezone.now()
    )
    return user
