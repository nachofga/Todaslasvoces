"""
Dirección de voz: cómo quiere el cliente que se interprete el guion.

Una «dirección» es un diccionario pequeño que viaja con la solicitud y con cada
locución generada:

    {"tone": "excited", "speed": 1.0, "stability": 0.5, "similarity": 0.75,
     "style": 0.0, "boost": True}

- `tone` es el tono de la interpretación. «natural» usa el modelo estándar; el
  resto usa el modelo expresivo del motor, que entiende indicaciones de tono.
- Los otros cinco valores son los mismos ajustes que ofrece ElevenLabs en su
  web: velocidad, estabilidad, similitud, exageración de estilo y realce de voz.

Este módulo no sabe nada del proveedor: solo valida y describe. Cada motor
traduce la dirección a su propia API (ver engines.py).
"""
from django.utils.translation import gettext_lazy as _

# código, nombre visible, indicación que entiende el modelo expresivo
TONES = [
    ("natural", _("Natural, como habla el locutor"), ""),
    ("warm", _("Cálido y cercano"), "warm"),
    ("cheerful", _("Alegre"), "cheerful"),
    ("excited", _("Entusiasta"), "excited"),
    ("energetic", _("Enérgico, de anuncio"), "energetic"),
    ("calm", _("Calmado y relajado"), "calm"),
    ("serious", _("Serio y formal"), "serious"),
    ("confident", _("Seguro y convincente"), "confident"),
    ("dramatic", _("Dramático"), "dramatic"),
    ("mysterious", _("Misterioso"), "mysterious"),
    ("sad", _("Emotivo, con tristeza"), "sad"),
    ("whisper", _("Susurrado"), "whispers"),
]
TONE_CHOICES = [(code, label) for code, label, _tag in TONES]
TONE_TAGS = {code: tag for code, _label, tag in TONES}
TONE_LABELS = dict(TONE_CHOICES)

# nombre: (mínimo, máximo, valor por defecto, paso)
RANGES = {
    "speed": (0.7, 1.2, 1.0, 0.05),
    "stability": (0.0, 1.0, 0.5, 0.05),
    "similarity": (0.0, 1.0, 0.75, 0.05),
    "style": (0.0, 1.0, 0.0, 0.05),
}
DEFAULT = {"tone": "natural", "speed": 1.0, "stability": 0.5, "similarity": 0.75, "style": 0.0, "boost": True}


def _number(value, name):
    low, high, default, _step = RANGES[name]
    try:
        value = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return default
    if value != value:  # NaN
        return default
    return round(min(max(value, low), high), 2)


def _flag(value):
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "on", "yes", "si", "sí")
    return bool(value)


def clean(data, prefix=""):
    """Convierte lo que llega de un formulario (o de la base de datos) en una dirección válida.

    Nunca falla: lo que no se entiende se sustituye por su valor por defecto.
    Con `prefix` lee las claves «prefijo + nombre» (los campos de un formulario).
    """
    data = data or {}
    get = lambda key: data.get(prefix + key)  # noqa: E731
    tone = get("tone")
    out = {"tone": tone if tone in TONE_TAGS else DEFAULT["tone"]}
    for name in RANGES:
        raw = get(name)
        out[name] = _number(raw, name) if raw not in (None, "") else RANGES[name][2]
    # Una casilla sin marcar no viaja en el formulario: por eso el formulario
    # manda además «boost_sent» para distinguir «desmarcada» de «no enviada».
    if get("boost") is None and not get("boost_sent"):
        out["boost"] = DEFAULT["boost"]
    else:
        out["boost"] = _flag(get("boost"))
    return out


def is_default(direction):
    return clean(direction) == DEFAULT


def tone_label(direction):
    return TONE_LABELS.get((direction or {}).get("tone"), TONE_LABELS["natural"])


def describe(direction):
    """Resumen de una línea, para fichas y listados: «Entusiasta · velocidad 1.10»."""
    d = clean(direction)
    parts = [str(TONE_LABELS[d["tone"]])]
    labels = {"speed": _("velocidad"), "stability": _("estabilidad"), "similarity": _("similitud"), "style": _("estilo")}
    for name, label in labels.items():
        if d[name] != DEFAULT[name]:
            parts.append(f"{label} {d[name]:.2f}")
    if not d["boost"]:
        parts.append(str(_("sin realce de voz")))
    return " · ".join(parts)


def form_context(direction=None):
    """Datos para pintar los controles (plantilla parts/direction.html)."""
    d = clean(direction)
    sliders = [
        ("speed", _("Velocidad"), _("Más lenta"), _("Más rápida")),
        ("stability", _("Estabilidad"), _("Más variación y emoción"), _("Más uniforme")),
        ("similarity", _("Similitud"), _("Más libre"), _("Más fiel a la voz original")),
        ("style", _("Exageración de estilo"), _("Ninguna"), _("Muy marcada")),
    ]
    return {
        "tones": TONE_CHOICES,
        "value": d,
        "sliders": [
            {"name": name, "label": label, "low": low, "high": high, "min": RANGES[name][0], "max": RANGES[name][1],
             "step": RANGES[name][3], "value": d[name], "default": RANGES[name][2]}
            for name, label, low, high in sliders
        ],
    }
