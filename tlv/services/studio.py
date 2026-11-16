"""
El "estudio": convierte texto en una locución con marca de agua y la deja
apuntada en el registro. Es el ÚNICO sitio desde el que se llama al motor de voz
para generar audio, de modo que no existe ningún audio sin identificar.
"""
import hashlib
import secrets

from django.core.files.base import ContentFile

from ..models import Generation
from . import audio, ledger, watermark
from . import direction as direction_service
from .engines import get_engine
from .policy import normalize_script, script_hash


def new_watermark_id():
    while True:
        wid = secrets.randbelow(watermark.MAX_ID - 1) + 1
        if not Generation.objects.filter(watermark_id=wid).exists():
            return wid


def render(voice, text, kind, user=None, request=None, language=None, direction=None):
    """Genera, marca y guarda una locución. Lanza EngineError o AudioError si algo falla."""
    text = normalize_script(text)
    engine = get_engine(voice.engine or None)
    direction = direction_service.clean(direction)
    raw = engine.synthesize(voice.external_voice_id, text, language=language, direction=direction)
    model = getattr(engine, "last_model", "") or voice.engine_model
    arr, sr = audio.decode(raw)
    wid = new_watermark_id()
    marked = watermark.embed(arr, sr, wid)
    contract = voice.current_contract()
    take = 1
    if request is not None:
        take = request.generations.filter(kind=kind).count() + 1
    label = {"demo": "DEMO sin derechos de uso", "final": "Licencia"}.get(kind, "Prueba interna")
    ref = request.license.code if (request is not None and hasattr(request, "license")) else "-"
    tags = {
        "title": f"{voice.name} · {label}",
        "artist": f"{voice.name} (voz generada por IA)",
        "comment": f"Voz sintética generada por IA con autorización del locutor. Todas las Voces. "
                   f"Ref {ref}. ID {wid}.",
        "copyright": "Uso sujeto a licencia. todaslasvoces",
    }
    wav = audio.encode_wav(marked)
    mp3 = audio.encode_mp3(marked, tags=tags)
    gen = Generation(
        kind=kind, voice=voice, user=user, request=request, text=text, text_hash=script_hash(text),
        chars=len(text), take=take, engine=engine.name, engine_model=model, direction=direction,
        external_voice_id=voice.external_voice_id, contract_code=contract.code if contract else "",
        watermark_id=wid, duration=round(audio.duration(marked, audio.TARGET_SR), 2),
        sha_wav=hashlib.sha256(wav).hexdigest(), sha_mp3=hashlib.sha256(mp3).hexdigest(),
    )
    base = f"{voice.slug}-{kind}-{wid}"
    if kind == Generation.Kind.FINAL:
        gen.file_wav.save(f"{base}.wav", ContentFile(wav), save=False)
    gen.file_mp3.save(f"{base}.mp3", ContentFile(mp3), save=False)
    gen.save()
    ledger.record(
        "audio.generated", user, generation=gen.pk, kind=kind, voice=voice.slug, talent=voice.talent_id,
        client=request.client_id if request is not None else None,
        request=request.code if request is not None else None, license=ref, contract=gen.contract_code,
        engine=engine.name, model=gen.engine_model, external_voice=gen.external_voice_id,
        watermark=wid, text_hash=gen.text_hash, chars=gen.chars, sha_mp3=gen.sha_mp3, direction=direction,
    )
    return gen
