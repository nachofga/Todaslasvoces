"""
Lectura, conversión y control de calidad de audio.

Todo el audio se trabaja en memoria como un array mono de float32 entre -1 y 1.
"""
import io
import shutil
import subprocess

import numpy as np
import soundfile as sf
from django.conf import settings
from scipy.signal import resample_poly, welch

TARGET_SR = 44100


class AudioError(Exception):
    pass


def ffmpeg_bin():
    if getattr(settings, "FFMPEG_BIN", ""):
        return settings.FFMPEG_BIN
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # pragma: no cover
        raise AudioError("No se encuentra ffmpeg en el servidor.") from exc


def decode(data: bytes):
    """Devuelve (array mono float32, frecuencia de muestreo) de cualquier formato."""
    try:
        arr, sr = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
        return arr.mean(axis=1), int(sr)
    except Exception:
        pass
    # Formatos que libsndfile no abre (webm/opus del navegador, m4a, aac...).
    proc = subprocess.run(
        [ffmpeg_bin(), "-v", "error", "-i", "pipe:0", "-f", "f32le", "-ac", "1", "-ar", str(TARGET_SR), "pipe:1"],
        input=data, capture_output=True, timeout=120,
    )
    if proc.returncode != 0 or not proc.stdout:
        raise AudioError("No se ha podido leer el audio. Usa WAV, FLAC, MP3, M4A u OGG.")
    return np.frombuffer(proc.stdout, dtype="<f4").copy(), TARGET_SR


def resample(arr, sr, target=TARGET_SR):
    if sr == target:
        return arr
    g = np.gcd(int(sr), int(target))
    return resample_poly(arr, target // g, sr // g).astype(np.float32)


def encode_wav(arr, sr=TARGET_SR) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, np.clip(arr, -1.0, 1.0), sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def encode_mp3(arr, sr=TARGET_SR, bitrate="192k", tags=None) -> bytes:
    """MP3 con metadatos (título, comentario) que identifican el audio como IA."""
    cmd = [ffmpeg_bin(), "-v", "error", "-f", "f32le", "-ar", str(sr), "-ac", "1", "-i", "pipe:0"]
    for key, value in (tags or {}).items():
        cmd += ["-metadata", f"{key}={value}"]
    cmd += ["-codec:a", "libmp3lame", "-b:a", bitrate, "-f", "mp3", "pipe:1"]
    proc = subprocess.run(
        cmd, input=np.clip(arr, -1.0, 1.0).astype("<f4").tobytes(), capture_output=True, timeout=180
    )
    if proc.returncode != 0 or not proc.stdout:
        raise AudioError("No se ha podido codificar el MP3.")
    return proc.stdout


def duration(arr, sr):
    return float(len(arr)) / float(sr) if sr else 0.0


def _db(x):
    return float(20 * np.log10(max(float(x), 1e-9)))


def analyze(arr, sr, min_seconds=10.0):
    """
    Control de calidad de una muestra. Devuelve métricas y una lista de avisos:
    {"metrics": {...}, "issues": [{"code", "level": "error"|"warning", "text"}], "ok": bool}
    """
    issues = []

    def add(code, level, text):
        issues.append({"code": code, "level": level, "text": text})

    secs = duration(arr, sr)
    metrics = {"seconds": round(secs, 2), "sample_rate": int(sr)}
    if secs < 1.0 or len(arr) < 2048:
        add("too_short", "error", "La grabación está vacía o dura menos de un segundo.")
        return {"metrics": metrics, "issues": issues, "ok": False}

    peak = float(np.max(np.abs(arr)))
    clipped = float(np.mean(np.abs(arr) >= 0.999))
    frame = int(sr * 0.03)
    n = len(arr) // frame
    rms = np.sqrt(np.mean(arr[: n * frame].reshape(n, frame) ** 2, axis=1) + 1e-12)
    rms_db = 20 * np.log10(rms + 1e-9)
    speech_level = float(np.percentile(rms_db, 90))
    noise_floor = float(np.percentile(rms_db, 8))
    snr = speech_level - noise_floor
    silence = float(np.mean(rms_db < speech_level - 35))

    # Ancho de banda real: frecuencia más alta con energía a menos de 55 dB del máximo.
    freqs, psd = welch(arr[: min(len(arr), sr * 60)], fs=sr, nperseg=2048)
    psd_db = 10 * np.log10(np.convolve(psd, np.ones(9) / 9, mode="same") + 1e-20)
    above = np.nonzero(psd_db >= psd_db.max() - 55)[0]
    bandwidth = float(freqs[above[-1]]) if len(above) else 0.0

    metrics.update(
        peak_db=round(_db(peak), 1), clipped_percent=round(clipped * 100, 3),
        level_db=round(speech_level, 1), noise_db=round(noise_floor, 1), snr_db=round(snr, 1),
        silence_percent=round(silence * 100, 1), bandwidth_hz=int(bandwidth),
    )

    if secs < min_seconds:
        add("short", "error", f"Dura {secs:.0f} s. Cada muestra debe durar al menos {min_seconds:.0f} s.")
    if sr < 22050:
        add("low_rate", "error", f"Frecuencia de muestreo de {sr} Hz. Graba a 44,100 Hz o más.")
    elif sr < 44100:
        add("mid_rate", "warning", f"Frecuencia de muestreo de {sr} Hz. Lo recomendable es 44,100 Hz o más.")
    if bandwidth < 5000:
        add("narrow", "error", "El audio suena a llamada telefónica (sin agudos). Usa un micrófono de estudio.")
    elif bandwidth < 9000:
        add("dull", "warning", "Al audio le faltan agudos. Revisa el micrófono o el formato de exportación.")
    if clipped > 0.001:
        add("clipping", "error", "La grabación satura (está distorsionada). Baja la ganancia y repite.")
    elif clipped > 0.0001:
        add("near_clip", "warning", "Hay picos al límite. Baja un poco la ganancia.")
    if peak < 0.05:
        add("quiet", "error", "La grabación está demasiado baja. Sube la ganancia o acércate al micrófono.")
    elif speech_level < -35:
        add("low_level", "warning", "El nivel de voz es bajo.")
    if snr < 20:
        add("noisy", "error", "Hay demasiado ruido de fondo. Graba en una sala tratada y sin música.")
    elif snr < 30:
        add("some_noise", "warning", "Se aprecia algo de ruido de fondo.")
    if silence > 0.6:
        add("silence", "warning", "Más de la mitad de la grabación es silencio.")

    ok = not any(i["level"] == "error" for i in issues)
    return {"metrics": metrics, "issues": issues, "ok": ok}
