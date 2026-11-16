"""
Motores de voz. La plataforma habla siempre con la misma interfaz:

    engine = get_engine()
    engine.clone(name, samples, ...)      -> {"voice_id", "requires_verification"}
    engine.synthesize(voice_id, text)     -> bytes de audio (cualquier formato)
    engine.transcribe(filename, data)     -> texto, o None si el motor no transcribe
    engine.delete(voice_id)
    engine.status()                       -> dict con el estado de la cuenta

Para cambiar de proveedor (Cartesia, Fish Audio, un modelo propio...) basta con
escribir otra clase con estos cinco métodos y registrarla en ENGINES.
"""
import hashlib
import json
import logging

import numpy as np
import requests
from django.conf import settings
from scipy.signal import lfilter

from . import direction as direction_service
from .audio import TARGET_SR, encode_wav

log = logging.getLogger("tlv")


class EngineError(Exception):
    """Error del proveedor de voz, con un mensaje que se puede enseñar a gestión."""


# ---------------------------------------------------------------------------
# ElevenLabs
# ---------------------------------------------------------------------------
class ElevenLabsEngine:
    name = "elevenlabs"
    label = "ElevenLabs"
    can_transcribe = True

    def __init__(self):
        self.key = settings.ELEVENLABS_API_KEY
        self.base = settings.ELEVENLABS_BASE_URL
        self.model = settings.ELEVENLABS_MODEL_ID
        self.timeout = settings.ELEVENLABS_TIMEOUT
        if not self.key:
            raise EngineError("Falta ELEVENLABS_API_KEY en la configuración del servidor.")

    def _request(self, method, path, **kwargs):
        headers = {"xi-api-key": self.key, **kwargs.pop("headers", {})}
        try:
            resp = requests.request(method, self.base + path, headers=headers, timeout=self.timeout, **kwargs)
        except requests.RequestException as exc:
            raise EngineError(f"No se ha podido conectar con ElevenLabs: {exc.__class__.__name__}") from exc
        if resp.status_code >= 400:
            raise EngineError(self._error_text(resp))
        return resp

    @staticmethod
    def _error_text(resp):
        try:
            detail = resp.json().get("detail")
        except Exception:
            detail = None
        if isinstance(detail, dict):
            text = detail.get("message") or detail.get("status") or json.dumps(detail)[:200]
        elif isinstance(detail, list) and detail:
            text = "; ".join(str(d.get("msg", d)) for d in detail[:3] if isinstance(d, dict)) or str(detail)[:200]
        else:
            text = (detail or resp.text or "")[:200]
        hints = {
            401: "La clave de API no es válida.",
            402: "El plan de ElevenLabs no incluye esta función o no quedan créditos.",
            429: "ElevenLabs está saturado o se ha alcanzado el límite del plan. Inténtalo en unos minutos.",
        }
        hint = hints.get(resp.status_code, "")
        return f"ElevenLabs ({resp.status_code}): {text} {hint}".strip()

    def clone(self, name, samples, description="", labels=None, remove_noise=False):
        """samples: lista de (nombre_de_fichero, bytes, tipo_mime)."""
        files = [("files", (fn, data, mime or "audio/wav")) for fn, data, mime in samples]
        data = {"name": name[:100], "remove_background_noise": "true" if remove_noise else "false"}
        if description:
            data["description"] = description[:500]
        if labels:
            data["labels"] = json.dumps(labels)
        body = self._request("POST", "/v1/voices/add", data=data, files=files).json()
        return {
            "voice_id": body["voice_id"],
            "requires_verification": bool(body.get("requires_verification")),
            "model": self.model,
        }

    def plan(self, direction=None):
        """Traduce una dirección de voz a (modelo, ajustes, indicación de tono) de ElevenLabs.

        - Tono «natural»: modelo estándar, que admite los cinco ajustes.
        - Cualquier otro tono: modelo expresivo, que entiende indicaciones entre
          corchetes al principio del texto y solo admite estabilidad, similitud
          y velocidad.
        """
        d = direction_service.clean(direction)
        tag = direction_service.TONE_TAGS[d["tone"]]
        model = settings.ELEVENLABS_EXPRESSIVE_MODEL if tag else self.model
        voice_settings = {"stability": d["stability"], "similarity_boost": d["similarity"], "speed": d["speed"]}
        if "multilingual_v2" in model:
            voice_settings["style"] = d["style"]
            voice_settings["use_speaker_boost"] = d["boost"]
        return model, voice_settings, tag

    def synthesize(self, voice_id, text, language=None, direction=None):
        model, voice_settings, tag = self.plan(direction)
        if tag:  # la indicación se repite en cada párrafo para que el tono no se pierda
            text = "\n\n".join(f"[{tag}] {part.strip()}" for part in text.split("\n\n") if part.strip())
        payload = {"text": text, "model_id": model, "voice_settings": voice_settings}
        # language_code no lo admiten los modelos multilingual_v2: solo se envía en el resto.
        if language and "multilingual_v2" not in model:
            payload["language_code"] = language
        resp = self._request(
            "POST", f"/v1/text-to-speech/{voice_id}",
            params={"output_format": settings.ELEVENLABS_OUTPUT_FORMAT}, json=payload,
        )
        self.last_model = model
        return resp.content

    def transcribe(self, filename, data, language=None):
        form = {"model_id": settings.ELEVENLABS_STT_MODEL}
        if language:
            form["language_code"] = language
        body = self._request("POST", "/v1/speech-to-text", data=form, files={"file": (filename, data)}).json()
        return body.get("text", "")

    def delete(self, voice_id):
        try:
            self._request("DELETE", f"/v1/voices/{voice_id}")
        except EngineError as exc:
            if "(404)" not in str(exc) and "(400)" not in str(exc):
                raise
        return True

    def status(self):
        body = self._request("GET", "/v1/user/subscription").json()
        return {
            "engine": self.label, "model": self.model, "tier": body.get("tier"),
            "characters_used": body.get("character_count"), "characters_limit": body.get("character_limit"),
            "voices_used": body.get("voice_slots_used"), "voices_limit": body.get("voice_limit"),
            "status": body.get("status"),
        }


# ---------------------------------------------------------------------------
# Motor simulado: una voz sintética sencilla para probar toda la plataforma
# sin coste y sin conexión. No clona nada: solo cambia el tono según la voz.
# ---------------------------------------------------------------------------
_VOWELS = {
    "a": (800, 1250, 2600), "e": (480, 1900, 2550), "i": (300, 2300, 3000),
    "o": (500, 900, 2500), "u": (330, 800, 2400),
}
_ACCENTS = str.maketrans("áéíóúüàèìòùâêîôû", "aeiouuaeiouaeiou")
_VOICED = {"m": (280, 1100, 2400), "n": (300, 1500, 2500), "l": (360, 1300, 2700), "r": (420, 1300, 1700),
           "y": (300, 2100, 2900), "w": (330, 800, 2300), "ñ": (300, 1900, 2600),
           "b": (250, 900, 2300), "d": (250, 1600, 2500), "g": (250, 1400, 2300), "v": (250, 1000, 2300)}
_NOISE = {"s": (4500, 7500), "z": (4000, 7000), "f": (1500, 6500), "j": (1500, 4000), "x": (1500, 4500),
          "c": (3500, 7000), "h": (800, 3000)}
_STOPS = {"p": 900, "t": 3800, "k": 2000, "q": 2000}


def _resonator(x, freq, bw, sr):
    r = np.exp(-np.pi * bw / sr)
    theta = 2 * np.pi * freq / sr
    a = [1.0, -2 * r * np.cos(theta), r * r]
    return lfilter([1 - r], a, x)


class SimulatedEngine:
    name = "simulated"
    label = "Simulado (sin clonación real)"
    can_transcribe = False

    def clone(self, name, samples, description="", labels=None, remove_noise=False):
        digest = hashlib.sha256(b"".join(s[1][:4096] for s in samples) + name.encode()).hexdigest()[:20]
        return {"voice_id": f"sim_{digest}", "requires_verification": False, "model": "formantes-v1"}

    def synthesize(self, voice_id, text, language=None, direction=None):
        sr = TARGET_SR
        d = direction_service.clean(direction)
        self.last_model = "formantes-v1"
        seed = int(hashlib.sha256(voice_id.encode()).hexdigest()[:8], 16)
        voice_rng = np.random.default_rng(seed)
        f0_base = float(voice_rng.uniform(95, 225))
        tract = float(voice_rng.uniform(0.9, 1.16))
        rate = float(voice_rng.uniform(0.9, 1.1)) * d["speed"]
        rng = np.random.default_rng(seed ^ int(hashlib.sha256(text.encode()).hexdigest()[:8], 16))

        chunks, phase = [], 0.0
        letters = text.lower().translate(_ACCENTS)[:6000]
        total = max(len(letters), 1)
        for pos, ch in enumerate(letters):
            decl = 1.08 - 0.16 * (pos % 60) / 60.0            # la entonación cae a lo largo de la frase
            f0 = f0_base * decl * (1 + 0.03 * rng.standard_normal())
            if ch in _VOWELS or ch in _VOICED:
                formants = _VOWELS.get(ch) or _VOICED[ch]
                dur = (0.085 if ch in _VOWELS else 0.055) / rate * (1 + 0.15 * rng.standard_normal())
                n = max(int(sr * dur), 256)
                ph = phase + 2 * np.pi * f0 * np.arange(n) / sr
                phase = float(ph[-1] % (2 * np.pi))
                src = ((ph / (2 * np.pi)) % 1.0) * 2 - 1          # diente de sierra = pulso glotal
                src = np.diff(src, prepend=src[0]) * 6 + 0.02 * rng.standard_normal(n)
                seg = sum(
                    _resonator(src, f * tract, bw, sr) * g
                    for f, bw, g in zip(formants + (3500, 4600), (90, 110, 170, 250, 320), (1.0, 0.6, 0.35, 0.2, 0.12))
                )
                seg *= 0.9 if ch in _VOWELS else 0.5
            elif ch in _NOISE:
                lo, hi = _NOISE[ch]
                n = int(sr * 0.07 / rate)
                noise = rng.standard_normal(n)
                seg = (_resonator(noise, (lo + hi) / 2, hi - lo, sr)) * 0.25
            elif ch in _STOPS:
                n = int(sr * 0.06 / rate)
                seg = np.zeros(n)
                burst = rng.standard_normal(n // 4)
                seg[n // 2:n // 2 + len(burst)] = _resonator(burst, _STOPS[ch], 1500, sr) * 0.35
            elif ch in ".!?;:\n":
                seg = np.zeros(int(sr * 0.32))
            elif ch == ",":
                seg = np.zeros(int(sr * 0.16))
            elif ch == " ":
                seg = np.zeros(int(sr * 0.045 / rate))
            else:
                continue
            fade = min(220, len(seg) // 3)
            if fade > 1 and seg.any():
                ramp = np.linspace(0, 1, fade)
                seg[:fade] *= ramp
                seg[-fade:] *= ramp[::-1]
            chunks.append(seg)
        audio = np.concatenate(chunks) if chunks else np.zeros(sr // 2)
        audio = np.concatenate([np.zeros(int(sr * 0.12)), audio, np.zeros(int(sr * 0.2))])
        peak = float(np.max(np.abs(audio))) or 1.0
        return encode_wav((audio / peak * 0.6).astype(np.float32), sr)

    def transcribe(self, filename, data, language=None):
        return None

    def delete(self, voice_id):
        return True

    def status(self):
        return {"engine": self.label, "model": "formantes-v1", "tier": "—", "status": "ok"}


ENGINES = {"elevenlabs": ElevenLabsEngine, "simulated": SimulatedEngine}


def get_engine(name=None):
    name = name or settings.TLV_VOICE_ENGINE
    if name not in ENGINES:
        raise EngineError(f"Motor de voz desconocido: {name}")
    return ENGINES[name]()


def engine_name():
    return settings.TLV_VOICE_ENGINE
