from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def eur(value):
    """1234.5 -> 1,234.50 € (punto decimal, coma de miles)."""
    try:
        return f"{float(value):,.2f} €"
    except (TypeError, ValueError):
        return "—"


@register.filter
def num(value, decimals=0):
    try:
        return f"{float(value):,.{int(decimals)}f}"
    except (TypeError, ValueError):
        return "—"


@register.filter
def dur(seconds):
    try:
        s = int(round(float(seconds)))
    except (TypeError, ValueError):
        return "—"
    return f"{s // 60}:{s % 60:02d}"


@register.filter
def get(mapping, key):
    try:
        return mapping.get(key)
    except AttributeError:
        return None


_TONES = {
    "active": "ok", "verified": "ok", "confirmed": "ok", "paid": "ok", "passed": "ok", "done": "ok", "dismissed": "muted",
    "approved": "info", "in_review": "info", "submitted": "info", "reported": "info", "investigating": "info",
    "review": "warn", "counter": "warn", "info": "warn", "changes": "warn", "pending": "warn", "open": "warn",
    "paused": "warn", "draft": "muted", "none": "muted", "cancelled": "muted", "expired": "muted",
    "superseded": "muted", "suspended": "bad", "rejected": "bad", "revoked": "bad", "failed": "bad",
    "upheld": "bad", "refunded": "muted", "terminated": "muted",
}


@register.filter
def tone(status):
    return _TONES.get(str(status), "muted")


@register.simple_tag
def voiceprint(voice, size=""):
    """La «huella» de la voz: 28 barras cuya altura es propia de cada voz."""
    bars = "".join(f'<i style="--b:{b}"></i>' for b in voice.print_bars)
    return mark_safe(f'<span class="print {size}" style="--h:{voice.hue}" aria-hidden="true">{bars}</span>')


@register.simple_tag(takes_context=True)
def qs(context, **kwargs):
    """Devuelve la query string actual cambiando los parámetros indicados."""
    params = context["request"].GET.copy()
    for k, v in kwargs.items():
        if v in (None, ""):
            params.pop(k, None)
        else:
            params[k] = v
    return params.urlencode()


@register.filter
def active_nav(request, prefix):
    path = request.path
    for lang in ("/en/",):
        if path.startswith(lang):
            path = path[len(lang) - 1:]
    return "is-active" if (path == prefix or (prefix != "/" and path.startswith(prefix))) else ""


@register.filter
def event_label(event):
    """Nombre legible de un evento del registro."""
    from ..services.insight import event_label as label

    return label(event)


@register.filter
def direction(value):
    """Resumen de una dirección de voz: «Entusiasta · velocidad 1.10»."""
    from ..services.direction import describe

    return describe(value)


@register.filter
def tone_name(value):
    from ..services.direction import tone_label

    return tone_label(value)
