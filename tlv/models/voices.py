"""Voces digitales: catálogo, muestras, verificación, consentimiento y contrato."""
import hashlib
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _

from .accounts import TalentProfile, private_upload


def upload_sample(instance, filename):
    return private_upload("muestras")(instance, filename)


def upload_challenge(instance, filename):
    return private_upload("verificacion")(instance, filename)


class Localized(models.Model):
    """Base para listas con nombre en español e inglés."""

    code = models.SlugField(max_length=40, unique=True)
    name_es = models.CharField(max_length=120)
    name_en = models.CharField(max_length=120)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        abstract = True
        ordering = ["order", "name_es"]

    @property
    def name(self):
        return self.name_en if (get_language() or "es").startswith("en") else self.name_es

    def __str__(self):
        return self.name_es


class Language(Localized):
    class Meta(Localized.Meta):
        verbose_name = _("idioma")
        verbose_name_plural = _("idiomas")


class Accent(Localized):
    class Meta(Localized.Meta):
        verbose_name = _("acento")
        verbose_name_plural = _("acentos")


class Style(Localized):
    class Meta(Localized.Meta):
        verbose_name = _("estilo")
        verbose_name_plural = _("estilos")


class ProjectType(Localized):
    """Especialidades del locutor y tipo de proyecto del buscador."""

    class Meta(Localized.Meta):
        verbose_name = _("tipo de proyecto")
        verbose_name_plural = _("tipos de proyecto")


class UseCategory(Localized):
    """Las 17 categorías de uso que el locutor acepta o rechaza una a una."""

    description_es = models.CharField(max_length=300, blank=True)
    description_en = models.CharField(max_length=300, blank=True)
    sensitive = models.BooleanField(
        default=False, help_text=_("Uso delicado: se desmarca por defecto y se avisa al locutor.")
    )
    price_factor = models.DecimalField(
        max_digits=5, decimal_places=2, default=1, help_text=_("Multiplicador sobre la tarifa base.")
    )
    active = models.BooleanField(default=True)

    class Meta(Localized.Meta):
        verbose_name = _("categoría de uso")
        verbose_name_plural = _("categorías de uso")

    @property
    def description(self):
        return self.description_en if (get_language() or "es").startswith("en") else self.description_es


class Voice(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", _("En preparación")
        IN_REVIEW = "in_review", _("En revisión")
        CHANGES = "changes", _("Cambios solicitados")
        APPROVED = "approved", _("Aprobada, pendiente de activar")
        ACTIVE = "active", _("Activa")
        PAUSED = "paused", _("En pausa")
        REVOKED = "revoked", _("Revocada")

    class Gender(models.TextChoices):
        FEMALE = "female", _("Femenina")
        MALE = "male", _("Masculina")
        NEUTRAL = "neutral", _("Neutra")

    class Age(models.TextChoices):
        YOUNG = "young", _("Joven")
        ADULT = "adult", _("Adulta")
        MATURE = "mature", _("Madura")
        SENIOR = "senior", _("Sénior")

    talent = models.ForeignKey(TalentProfile, on_delete=models.CASCADE, related_name="voices")
    name = models.CharField(_("nombre público de la voz"), max_length=120)
    slug = models.SlugField(max_length=140, unique=True)
    tagline = models.CharField(_("frase de presentación"), max_length=160, blank=True)
    description = models.TextField(_("descripción"), blank=True, max_length=2000)
    gender = models.CharField(_("género vocal"), max_length=10, choices=Gender.choices, default=Gender.NEUTRAL)
    age = models.CharField(_("edad vocal"), max_length=10, choices=Age.choices, default=Age.ADULT)
    traits = models.CharField(
        _("características vocales"), max_length=200, blank=True,
        help_text=_("Separadas por comas: grave, cálida, rasgada…"),
    )
    languages = models.ManyToManyField(Language, verbose_name=_("idiomas"), blank=True)
    accents = models.ManyToManyField(Accent, verbose_name=_("acentos"), blank=True)
    styles = models.ManyToManyField(Style, verbose_name=_("estilos"), blank=True)
    project_types = models.ManyToManyField(ProjectType, verbose_name=_("especialidades"), blank=True)
    base_price = models.DecimalField(
        _("tarifa base orientativa (€)"), max_digits=9, decimal_places=2, default=150,
        help_text=_("Precio de partida de una licencia de 12 meses en España, sin IVA."),
    )
    restrictions = models.TextField(
        _("restricciones"), blank=True, max_length=1500,
        help_text=_("Marcas, sectores o contenidos para los que no quieres que se use tu voz."),
    )

    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    review_note = models.TextField(blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    paused_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoke_reason = models.CharField(max_length=300, blank=True)

    # Modelo de IA (lo rellena el motor al clonar).
    engine = models.CharField(max_length=30, blank=True)
    engine_model = models.CharField(max_length=60, blank=True)
    external_voice_id = models.CharField(max_length=120, blank=True)
    cloned_at = models.DateTimeField(null=True, blank=True)

    # Bloqueo temporal por una disputa (no admite usos nuevos mientras dure).
    blocked = models.BooleanField(default=False)
    blocked_reason = models.CharField(max_length=300, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-activated_at", "-created_at"]
        verbose_name = _("voz")
        verbose_name_plural = _("voces")

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name)[:120] or "voz"
            slug, n = base, 2
            while Voice.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug, n = f"{base}-{n}", n + 1
            self.slug = slug
        super().save(*args, **kwargs)

    # --- Estado ---
    @property
    def is_public(self):
        return self.status == self.Status.ACTIVE and not self.blocked

    @property
    def accepts_new_uses(self):
        return self.status == self.Status.ACTIVE and not self.blocked and bool(self.external_voice_id)

    @property
    def editable(self):
        return self.status in (self.Status.DRAFT, self.Status.CHANGES)

    # --- Identidad visual: cada voz tiene su color y su "huella" de barras ---
    @property
    def hue(self):
        digest = hashlib.sha256(f"voz-{self.pk}-{self.slug}".encode()).digest()
        palette = [8, 28, 46, 96, 162, 192, 222, 262, 292, 332]
        return palette[digest[0] % len(palette)]

    @property
    def print_bars(self):
        """28 alturas (18-100) deterministas para dibujar la huella de la voz."""
        digest = hashlib.sha256(f"huella-{self.slug}".encode()).digest()
        return [18 + (b * 82) // 255 for b in digest[:28]]

    @property
    def trait_list(self):
        return [t.strip() for t in self.traits.split(",") if t.strip()]

    def current_contract(self):
        return self.contracts.filter(status=Contract.Status.ACTIVE).order_by("-signed_at").first()

    def allowed_categories(self):
        return UseCategory.objects.filter(consents__voice=self, consents__allowed=True, active=True)

    def training_samples(self):
        return self.samples.filter(kind=VoiceSample.Kind.TRAINING)

    def training_seconds(self):
        return sum(s.duration or 0 for s in self.training_samples() if s.ok)


class VoiceSample(models.Model):
    class Kind(models.TextChoices):
        TRAINING = "training", _("Muestra para crear la voz")
        SHOWCASE = "showcase", _("Muestra original para el catálogo")

    voice = models.ForeignKey(Voice, on_delete=models.CASCADE, related_name="samples")
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.TRAINING)
    file = models.FileField(upload_to=upload_sample)
    original_name = models.CharField(max_length=200, blank=True)
    title = models.CharField(_("título"), max_length=120, blank=True)
    duration = models.FloatField(null=True, blank=True)
    sample_rate = models.PositiveIntegerField(null=True, blank=True)
    checks = models.JSONField(default=dict, blank=True)
    ok = models.BooleanField(default=False)
    public = models.BooleanField(_("visible en el catálogo"), default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    @property
    def issues(self):
        return self.checks.get("issues", [])


def _challenge_expiry():
    return timezone.now() + timezone.timedelta(minutes=15)


class VoiceVerification(models.Model):
    """Prueba de titularidad: el locutor graba en directo una frase aleatoria."""

    class Status(models.TextChoices):
        PENDING = "pending", _("Pendiente de grabar")
        PASSED = "passed", _("Superada")
        REVIEW = "review", _("Pendiente de revisión manual")
        FAILED = "failed", _("No superada")

    voice = models.ForeignKey(Voice, on_delete=models.CASCADE, related_name="verifications")
    phrase = models.CharField(max_length=300)
    language = models.CharField(max_length=5, default="es")
    recording = models.FileField(upload_to=upload_challenge, blank=True)
    transcript = models.TextField(blank=True)
    text_score = models.FloatField(null=True, blank=True)
    speaker_score = models.FloatField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    detail = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(default=_challenge_expiry)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def expired(self):
        return self.status == self.Status.PENDING and timezone.now() > self.expires_at


class VoiceConsent(models.Model):
    """Autorización del locutor para una categoría de uso concreta."""

    voice = models.ForeignKey(Voice, on_delete=models.CASCADE, related_name="consents")
    category = models.ForeignKey(UseCategory, on_delete=models.PROTECT, related_name="consents")
    allowed = models.BooleanField(default=False)
    price = models.DecimalField(
        _("tarifa propia para esta categoría (€)"), max_digits=9, decimal_places=2, null=True, blank=True
    )
    notes = models.CharField(_("condiciones"), max_length=300, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("voice", "category")]
        ordering = ["category__order"]


class Contract(models.Model):
    """Contrato marco firmado electrónicamente, con la foto del consentimiento."""

    class Status(models.TextChoices):
        ACTIVE = "active", _("Vigente")
        SUPERSEDED = "superseded", _("Sustituido")
        TERMINATED = "terminated", _("Resuelto")

    voice = models.ForeignKey(Voice, on_delete=models.CASCADE, related_name="contracts")
    talent = models.ForeignKey(TalentProfile, on_delete=models.CASCADE, related_name="contracts")
    code = models.CharField(max_length=30, unique=True)
    version = models.CharField(max_length=20)
    language = models.CharField(max_length=5, default="es")
    body = models.TextField()
    body_hash = models.CharField(max_length=64)
    consent_snapshot = models.JSONField(default=list)
    biometric_consent = models.BooleanField(default=False)
    signed_name = models.CharField(max_length=200)
    signed_at = models.DateTimeField(default=timezone.now)
    signed_ip = models.GenericIPAddressField(null=True, blank=True)
    signed_user_agent = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-signed_at"]

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = f"CT-{timezone.now():%Y}-{secrets.token_hex(4).upper()}"
        super().save(*args, **kwargs)


class DemoClip(models.Model):
    """Demostración generada por IA que se enseña en el catálogo."""

    voice = models.ForeignKey(Voice, on_delete=models.CASCADE, related_name="demo_clips")
    title = models.CharField(max_length=120)
    language = models.CharField(max_length=5, default="es")
    generation = models.ForeignKey("tlv.Generation", on_delete=models.CASCADE, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
