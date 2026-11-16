"""Páginas públicas: portada, catálogo, ficha de voz, verificación de licencias y denuncias."""
from django.contrib import messages
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _

from .. import forms
from ..models import (
    Accent,
    Language,
    License,
    PlatformSettings,
    ProjectType,
    Style,
    UseCategory,
    Voice,
)
from ..services import direction as direction_service
from ..services import disputes, ledger, money, policy
from .common import client_ip

LEGAL_PAGES = {"condiciones", "privacidad", "cookies", "ia"}


def public_voices():
    return (
        Voice.objects.filter(status=Voice.Status.ACTIVE, blocked=False)
        .select_related("talent")
        .prefetch_related("languages", "accents", "styles", "demo_clips")
    )


def home(request):
    voices = list(public_voices()[:5])
    return render(request, "tlv/public/home.html", {"voices": voices, "total": public_voices().count()})


def how(request):
    return render(request, "tlv/public/how.html")


def catalog(request):
    g = request.GET
    voices = public_voices()
    if g.get("q"):
        q = g["q"].strip()
        voices = voices.filter(
            Q(name__icontains=q) | Q(tagline__icontains=q) | Q(traits__icontains=q) | Q(description__icontains=q)
        )
    for param, field in (("idioma", "languages__code"), ("acento", "accents__code"), ("estilo", "styles__code"),
                         ("proyecto", "project_types__code"), ("genero", "gender"), ("edad", "age")):
        if g.get(param):
            voices = voices.filter(**{field: g[param]})
    if g.get("uso"):
        voices = voices.filter(consents__category__code=g["uso"], consents__allowed=True)
    if g.get("precio"):
        try:
            voices = voices.filter(base_price__lte=float(g["precio"]))
        except ValueError:
            pass
    voices = voices.distinct().order_by("base_price" if g.get("orden") == "precio" else "-activated_at")
    page = Paginator(voices, 12).get_page(g.get("pagina"))
    filters = [
        ("idioma", _("Idioma"), [(x.code, x.name) for x in Language.objects.all()]),
        ("acento", _("Acento"), [(x.code, x.name) for x in Accent.objects.all()]),
        ("genero", _("Género vocal"), Voice.Gender.choices),
        ("edad", _("Edad vocal"), Voice.Age.choices),
        ("estilo", _("Estilo"), [(x.code, x.name) for x in Style.objects.all()]),
        ("proyecto", _("Tipo de proyecto"), [(x.code, x.name) for x in ProjectType.objects.all()]),
        ("uso", _("Uso permitido"), [(x.code, x.name) for x in UseCategory.objects.filter(active=True)]),
    ]
    active = any(g.get(k) for k in ("q", "idioma", "acento", "genero", "edad", "estilo", "proyecto", "uso", "precio"))
    return render(request, "tlv/public/catalog.html", {"page": page, "filters": filters, "has_filters": active})


def voice_detail(request, slug):
    voice = get_object_or_404(Voice.objects.select_related("talent__user"), slug=slug)
    user = request.user
    owner = user.is_authenticated and (user.is_manager or voice.talent.user_id == user.pk)
    if not voice.is_public and not owner:
        raise Http404
    cfg = PlatformSettings.load()
    consents = voice.consents.select_related("category").filter(category__active=True)
    allowed = [(c.category, money.quote(voice, c.category, "es", 12)) for c in consents if c.allowed]
    denied = [c.category for c in consents if not c.allowed]
    demo_gate = policy.check_demo(user, voice, "texto de prueba") if user.is_authenticated else None
    return render(request, "tlv/public/voice.html", {
        "voice": voice, "clips": voice.demo_clips.select_related("generation"),
        "samples": voice.samples.filter(public=True, ok=True),
        "allowed": allowed, "denied": denied, "cfg": cfg, "owner_preview": not voice.is_public,
        "demo_gate": demo_gate, "from_price": min((p for _c, p in allowed), default=voice.base_price),
        "dir": direction_service.form_context(),
    })


def verify(request, code=None):
    code = (code or request.GET.get("codigo") or "").strip().upper()
    lic = None
    if code:
        lic = License.objects.select_related("request__voice__talent", "request__client", "request__category").filter(code=code).first()
    return render(request, "tlv/public/verify.html", {"code": code, "lic": lic})


def legal(request, page):
    if page not in LEGAL_PAGES:
        raise Http404
    return render(request, "tlv/public/legal.html", {"page": page, "cfg": PlatformSettings.load()})


def transparency(request):
    status = cache.get("tlv-ledger-status")
    if status is None:
        status = ledger.verify()
        cache.set("tlv-ledger-status", status, 600)
    from ..models import LedgerEntry

    return render(request, "tlv/public/transparency.html", {
        "status": status, "last": LedgerEntry.objects.first(), "public_key": ledger.public_key_pem(),
        "key_id": ledger.key_id(),
    })


def report(request):
    """Denuncia pública de un uso no autorizado (no hace falta tener cuenta)."""
    form = forms.PublicReportForm(request.POST or None, request.FILES or None)
    if request.method == "POST":
        key = f"tlv-report-{client_ip(request)}"
        if request.POST.get("website"):  # trampa para robots: campo oculto que una persona no rellena
            return redirect("home")
        if cache.get(key, 0) >= 5:
            messages.error(request, _("Has enviado varias denuncias seguidas. Espera una hora o escríbenos."))
        elif form.is_valid():
            cache.set(key, cache.get(key, 0) + 1, 3600)
            d = form.cleaned_data
            dispute = disputes.open_dispute(
                request.user if request.user.is_authenticated else None, d["description"], d.get("url", ""),
                d.get("evidence"), reporter_email=d["reporter_email"],
            )
            return render(request, "tlv/public/report.html", {"sent": dispute})
    return render(request, "tlv/public/report.html", {"form": form})


def health(request):
    """Comprobación de vida para el proveedor de alojamiento: responde si hay base de datos."""
    from django.db import connection
    from django.http import JsonResponse

    with connection.cursor() as cur:
        cur.execute("SELECT 1")
    return JsonResponse({"ok": True})
