"""
Indicador orientativo de "¿es la misma voz?".

Compara dos grabaciones por su tono medio (F0) y por el color de la voz
(coeficientes MFCC). NO es un sistema de verificación biométrica: sirve para
ordenar la cola de revisión y avisar a gestión de casos sospechosos. La decisión
final es siempre de una persona, que escucha las dos grabaciones.
"""
import numpy as np
from scipy.fft import dct

from .audio import resample

SR = 16000
FRAME, STEP, N_MEL = 400, 160, 26


def _mel_filters():
    def mel(f):
        return 2595 * np.log10(1 + f / 700)

    pts = 700 * (10 ** (np.linspace(mel(100), mel(7600), N_MEL + 2) / 2595) - 1)
    bins = np.floor((512 + 1) * pts / SR).astype(int)
    fb = np.zeros((N_MEL, 257))
    for i in range(N_MEL):
        a, b, c = bins[i], bins[i + 1], bins[i + 2]
        if b > a:
            fb[i, a:b] = (np.arange(a, b) - a) / (b - a)
        if c > b:
            fb[i, b:c] = (c - np.arange(b, c)) / (c - b)
    return fb


_FB = _mel_filters()


def profile(arr, sr):
    """Resume una grabación en {"f0": Hz, "mfcc": vector de 12}. None si no hay voz suficiente."""
    x = resample(np.asarray(arr, dtype=np.float32), sr, SR)
    if len(x) < SR:
        return None
    x = np.append(x[0], x[1:] - 0.97 * x[:-1])
    n = (len(x) - FRAME) // STEP + 1
    idx = np.arange(FRAME)[None, :] + STEP * np.arange(n)[:, None]
    frames = x[idx] * np.hamming(FRAME)
    energy = np.log(np.sum(frames ** 2, axis=1) + 1e-10)
    voiced = energy > (energy.max() - 4.0)
    if voiced.sum() < 20:
        return None
    power = np.abs(np.fft.rfft(frames[voiced], 512, axis=1)) ** 2
    mfcc = dct(np.log(power @ _FB.T + 1e-10), type=2, axis=1, norm="ortho")[:, 1:13]

    # Tono: pico de la autocorrelación entre 70 y 350 Hz en cada ventana con voz.
    lo, hi = SR // 350, SR // 70
    f0s = []
    for fr in frames[voiced][::3]:
        ac = np.correlate(fr, fr, mode="full")[FRAME - 1:]
        if ac[0] <= 0:
            continue
        k = lo + int(np.argmax(ac[lo:hi]))
        if ac[k] / ac[0] > 0.35:
            f0s.append(SR / k)
    f0 = float(np.median(f0s)) if len(f0s) >= 5 else 0.0
    return {"f0": f0, "mfcc": mfcc.mean(axis=0)}


def similarity(a, b):
    """Devuelve 0-100. Por encima de 60, compatible; por debajo de 40, sospechoso."""
    if not a or not b:
        return None
    dist = float(np.linalg.norm(a["mfcc"] - b["mfcc"]) / np.sqrt(len(a["mfcc"])))
    timbre = float(np.exp(-((dist / 3.0) ** 2)))
    if a["f0"] > 0 and b["f0"] > 0:
        pitch = float(np.exp(-(np.log(a["f0"] / b["f0"]) ** 2) / (2 * 0.13 ** 2)))
    else:
        pitch = timbre
    return round(100 * (0.5 * timbre + 0.5 * pitch), 1)
