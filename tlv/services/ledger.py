"""
Registro de trazabilidad encadenado y firmado.

Cada hecho relevante (un consentimiento, una aprobación, un cobro, una locución
generada, una disputa...) se apunta como una entrada que incluye la huella
SHA-256 de la entrada anterior y una firma Ed25519. Si alguien modifica o borra
una fila de la base de datos, `verify()` lo detecta. No hace falta blockchain.
"""
import base64
import datetime
import hashlib
import json
from decimal import Decimal

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ..models import LedgerEntry

GENESIS = "0" * 64


def _private_key():
    seed = hashlib.sha256(settings.TLV_SIGNING_SEED.encode()).digest()
    return Ed25519PrivateKey.from_private_bytes(seed)


def public_key_pem():
    return _private_key().public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()


def key_id():
    raw = _private_key().public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return hashlib.sha256(raw).hexdigest()[:16]


def _clean(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_clean(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _digest(seq, created_iso, event, actor_id, payload, prev_hash):
    body = json.dumps(
        {"seq": seq, "created": created_iso, "event": event, "actor": actor_id, "payload": payload, "prev": prev_hash},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return hashlib.sha256(body.encode()).hexdigest()


def record(event, actor=None, **payload):
    """Añade una entrada al registro. Úsalo siempre dentro de la operación que apunta."""
    payload = _clean(payload)
    actor_id = getattr(actor, "pk", None)
    with transaction.atomic():
        last = LedgerEntry.objects.select_for_update().order_by("-seq").first()
        seq = (last.seq + 1) if last else 1
        prev_hash = last.hash if last else GENESIS
        created = timezone.now().replace(microsecond=0)
        digest = _digest(seq, created.isoformat(), event, actor_id, payload, prev_hash)
        signature = base64.b64encode(_private_key().sign(bytes.fromhex(digest))).decode()
        return LedgerEntry.objects.create(
            seq=seq, created_at=created, event=event, actor_id=actor_id, payload=payload,
            prev_hash=prev_hash, hash=digest, key_id=key_id(), signature=signature,
        )


def verify():
    """Recorre toda la cadena. Devuelve {"ok", "entries", "broken_at", "reason"}."""
    public = _private_key().public_key()
    current_key = key_id()
    prev_hash, expected_seq, count = GENESIS, 1, 0
    for entry in LedgerEntry.objects.order_by("seq").iterator():
        count += 1
        if entry.seq != expected_seq:
            return {"ok": False, "entries": count, "broken_at": expected_seq, "reason": "Falta una entrada."}
        if entry.prev_hash != prev_hash:
            return {"ok": False, "entries": count, "broken_at": entry.seq, "reason": "La cadena no enlaza."}
        digest = _digest(entry.seq, entry.created_at.isoformat(), entry.event, entry.actor_id, entry.payload, entry.prev_hash)
        if digest != entry.hash:
            return {"ok": False, "entries": count, "broken_at": entry.seq, "reason": "El contenido ha sido modificado."}
        if entry.key_id == current_key:
            try:
                public.verify(base64.b64decode(entry.signature), bytes.fromhex(entry.hash))
            except Exception:
                return {"ok": False, "entries": count, "broken_at": entry.seq, "reason": "La firma no es válida."}
        prev_hash, expected_seq = entry.hash, entry.seq + 1
    return {"ok": True, "entries": count, "broken_at": None, "reason": ""}
