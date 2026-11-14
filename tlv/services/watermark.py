"""
Marca de agua invisible para audio.

Cada locución lleva escondido un número de 24 bits (su identificador) repetido
cada 3.3 segundos. La marca son subidas y bajadas de volumen de menos de 1 dB
en franjas muy estrechas de frecuencia, siguiendo un patrón secreto que depende
de TLV_WATERMARK_KEY. Sin la clave no se puede leer.

Cómo funciona, paso a paso:
  1. El audio se trocea en ventanas de 23 ms (transformada de Fourier corta).
  2. El número + un código de comprobación (CRC de 12 bits) son 36 bits.
     Cada bit ocupa 8 ventanas seguidas; los 36 bits ocupan 288 ventanas.
  3. Cada bit tiene su propio patrón secreto: la mitad de las franjas entre
     500 y 6,500 Hz se sube un poco y la otra mitad se baja. Dentro del bit el
     patrón se invierte cada 2 ventanas (+ - + -). Así, lo que en la voz no
     cambia durante esas décimas de segundo se anula solo al leer, y queda la
     marca. Si el bit es 0 se hace todo al revés.
  4. Para leerla se mide, ventana a ventana, si las franjas "que suben" suenan
     más que las "que bajan". Como no se sabe dónde empieza el ciclo (el audio
     puede estar recortado), se prueban todos los desplazamientos posibles y se
     escoge el que da la señal más clara. El CRC confirma que la lectura es buena.

Resiste: MP3, cambio de frecuencia de muestreo, cambios de volumen, recortes
(hacen falta unos 4 s seguidos) y ruido moderado.
No resiste: cambios de velocidad o de tono, ni regrabar con altavoz y micrófono
en una sala con mucho eco.
"""
import hashlib

import numpy as np
from django.conf import settings
from scipy.fft import irfft, rfft

from .audio import TARGET_SR, resample

N_FFT = 1024
HOP = 512
BIN_LO, BIN_HI = 12, 152            # ~500 Hz a ~6,500 Hz
N_BINS = BIN_HI - BIN_LO
PAYLOAD_BITS = 24
CRC_BITS = 12
N_BITS = PAYLOAD_BITS + CRC_BITS    # 36
FRAMES_PER_BIT = 8                  # 4 tramos de 2 ventanas con signo + - + -
CYCLE = N_BITS * FRAMES_PER_BIT     # 288 ventanas = 3.34 s
TILE_SIGN = np.array([1, 1, -1, -1, 1, 1, -1, -1] * N_BITS, dtype=np.float32)
MAX_ID = (1 << PAYLOAD_BITS) - 1
MIN_SECONDS = CYCLE * HOP / TARGET_SR
BLOCK = 1024                        # ventanas por bloque (limita el uso de memoria)
MAX_DETECT_SECONDS = 180            # al leer, bastan los primeros 3 minutos


def _crc12(value: int) -> int:
    crc = 0
    for i in range(PAYLOAD_BITS - 1, -1, -1):
        bit = (value >> i) & 1
        top = (crc >> 11) & 1
        crc = (crc << 1) & 0xFFF
        if top ^ bit:
            crc ^= 0x80F
    return crc


def _bits(payload: int):
    word = (payload << CRC_BITS) | _crc12(payload)
    return np.array([(word >> (N_BITS - 1 - i)) & 1 for i in range(N_BITS)], dtype=np.int8)


def _from_bits(bits):
    word = 0
    for b in bits:
        word = (word << 1) | int(b)
    payload, crc = word >> CRC_BITS, word & 0xFFF
    return payload, _crc12(payload) == crc


_pattern_cache = {}


def _pattern(key=None):
    """Matriz secreta (36 bits x 140 franjas) de +1/-1, mitad y mitad."""
    key = key or settings.TLV_WATERMARK_KEY
    if key not in _pattern_cache:
        seed = int.from_bytes(hashlib.sha256(("tlv-wm-" + key).encode()).digest()[:8], "big")
        rng = np.random.default_rng(seed)
        base = np.array([1] * (N_BINS // 2) + [-1] * (N_BINS - N_BINS // 2), dtype=np.float32)
        _pattern_cache[key] = np.stack([rng.permutation(base) for _ in range(N_BITS)])
    return _pattern_cache[key]


def _window():
    return np.sqrt(np.hanning(N_FFT + 1)[:-1]).astype(np.float32)


def embed(arr, sr, payload: int, strength=None, key=None):
    """Devuelve el audio (a 44,100 Hz) con el identificador escondido."""
    if not 0 <= payload <= MAX_ID:
        raise ValueError("El identificador de marca de agua debe caber en 24 bits.")
    strength = settings.TLV_WATERMARK_STRENGTH if strength is None else strength
    x = resample(np.asarray(arr, dtype=np.float32), sr, TARGET_SR)
    n = len(x)
    pad = (-(n + N_FFT) % HOP) + N_FFT
    xp = np.concatenate([np.zeros(N_FFT, np.float32), x, np.zeros(pad, np.float32)])
    win = _window()
    n_frames = (len(xp) - N_FFT) // HOP + 1
    bits = _bits(payload)
    pattern, depth = _pattern(key), np.float32(np.log1p(strength))
    out = np.zeros(len(xp), np.float32)
    # Se procesa por bloques de ventanas para que un audio largo no llene la memoria.
    for start in range(0, n_frames, BLOCK):
        frames_idx = np.arange(start, min(start + BLOCK, n_frames))
        idx = (np.arange(N_FFT, dtype=np.int32)[None, :] + HOP * frames_idx[:, None]).astype(np.int32)
        spec = rfft(xp[idx] * win, axis=1)
        q = frames_idx % CYCLE
        bit_of = q // FRAMES_PER_BIT
        sign = np.where(bits[bit_of] == 1, 1.0, -1.0).astype(np.float32) * TILE_SIGN[q]
        spec[:, BIN_LO:BIN_HI] *= np.exp(depth * sign[:, None] * pattern[bit_of])
        frames = irfft(spec, n=N_FFT, axis=1).astype(np.float32) * win
        np.add.at(out, idx, frames)
    out = out[N_FFT:N_FFT + n]
    peak = float(np.max(np.abs(out))) if n else 0.0
    if peak > 0.999:
        out *= 0.999 / peak
    return out


def detect(arr, sr, candidates=None, key=None):
    """
    Busca la marca. Devuelve un dict con:
      found    True si se ha identificado un número con garantías
      payload  el número leído (o None)
      method   "crc" (lectura limpia, validada con el código de comprobación) o
               "match" (lectura con ruido, casada con uno de los `candidates`)
      score    claridad de la señal: ~0.8 en audio sin marca, 2 o más con marca
      rho      parecido (0-1) entre lo leído y el candidato elegido

    `candidates` es la lista de identificadores que existen en la base de datos.
    Con ella se puede identificar un audio aunque algunos bits lleguen dañados.
    """
    x = resample(np.asarray(arr, dtype=np.float32), sr, TARGET_SR)[: TARGET_SR * MAX_DETECT_SECONDS]
    result = {"found": False, "payload": None, "method": "", "score": 0.0, "rho": 0.0,
              "seconds": round(len(x) / TARGET_SR, 2)}
    if len(x) < N_FFT * 8:
        return result
    win = _window()
    step = HOP // 4                                   # 4 fases para afinar el encaje
    n_fine = (len(x) - N_FFT) // step + 1
    kernel = np.ones(17, np.float32) / 17
    pattern_t = _pattern(key).T
    stats, energies = [], []
    for start in range(0, n_fine, BLOCK * 2):
        idx = (np.arange(N_FFT, dtype=np.int32)[None, :] + step * np.arange(start, min(start + BLOCK * 2, n_fine))[:, None]).astype(np.int32)
        mag = np.abs(rfft(x[idx] * win, axis=1))[:, BIN_LO - 8:BIN_HI + 8]
        logm = np.log(mag + 1e-7)
        # Se resta la forma general del espectro (media móvil de 17 franjas): queda el detalle fino.
        smooth = np.apply_along_axis(lambda row: np.convolve(row, kernel, mode="same"), 1, logm)
        core = logm[:, 8:-8]
        white = np.clip((logm - smooth)[:, 8:-8], -1.5, 1.5)
        # Se descartan las franjas que la compresión ha dejado vacías.
        white = white * (core > (np.median(core, axis=1, keepdims=True) - 4.6))
        stats.append(white @ pattern_t)               # (ventanas, 36): encaje con el patrón de cada bit
        energies.append(core.mean(axis=1))
    energy = np.concatenate(energies)
    active = energy > (np.percentile(energy, 95) - 7.0)      # se ignoran los silencios
    stat = np.concatenate(stats) * active[:, None]
    best = None
    r = np.arange(CYCLE, dtype=np.int32)
    for phase in range(4):
        g = stat[phase::4]
        m = np.arange(len(g), dtype=np.int32)
        if len(m) < FRAMES_PER_BIT * 4:
            continue
        pos = (m[:, None] + r[None, :]) % CYCLE       # posición del ciclo si el desfase es r
        bit_idx = pos // FRAMES_PER_BIT
        vals = np.take_along_axis(g, bit_idx, axis=1) * TILE_SIGN[pos]
        sigma = float(g.std()) + 1e-9
        counts = active[phase::4].astype(np.float32)
        for rr in range(CYCLE):
            sums = np.bincount(bit_idx[:, rr], weights=vals[:, rr], minlength=N_BITS)
            cnt = np.bincount(bit_idx[:, rr], weights=counts, minlength=N_BITS)
            z = sums / (sigma * np.sqrt(np.maximum(cnt, 1.0)))
            score = float(np.mean(np.abs(z)))
            if best is None or score > best[0]:
                best = (score, z)
    if best is None:
        return result
    score, z = best
    result["score"] = round(score, 2)

    # 1) Lectura limpia: los 36 bits cuadran con su código de comprobación.
    payload, crc_ok = _from_bits((z > 0).astype(np.int8))
    known = set(int(c) for c in candidates) if candidates is not None else None
    if crc_ok and score >= 1.3 and (known is None or payload in known):
        result.update(found=True, payload=payload, method="crc", rho=1.0)
        return result

    # 2) Lectura con ruido: se compara con cada identificador existente y se
    #    acepta el más parecido solo si el parecido no puede ser casualidad.
    if known:
        ids = np.fromiter(known, dtype=np.int64)
        words = np.array([_bits(int(c)) for c in ids], dtype=np.float32) * 2 - 1
        rho = (words @ z) / (np.sqrt(N_BITS) * (np.linalg.norm(z) + 1e-9))
        k = int(np.argmax(rho))
        threshold = min(0.9, (np.sqrt(2 * np.log(max(len(ids), 2))) + 1.2) / np.sqrt(N_BITS))
        result["rho"] = round(float(rho[k]), 3)
        if rho[k] >= threshold:
            result.update(found=True, payload=int(ids[k]), method="match")
    return result


