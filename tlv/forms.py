"""Formularios de la web. Todos heredan el mismo aspecto (clase CSS «input»)."""
from decimal import Decimal

from django import forms
from django.conf import settings
from django.contrib.auth import password_validation
from django.contrib.auth.forms import AuthenticationForm
from django.utils.translation import gettext_lazy as _

from .models import (
    CHANNELS,
    ClientProfile,
    LicenseRequest,
    PlatformSettings,
    TalentProfile,
    UseCategory,
    User,
    Voice,
)

AUDIO_ACCEPT = "audio/*,.wav,.flac,.mp3,.m4a,.ogg,.aiff,.aif,.webm"


class Styled:
    """Añade la clase CSS a cada campo según su tipo."""

    def _style(self):
        for field in self.fields.values():
            w = field.widget
            if isinstance(w, (forms.CheckboxInput, forms.CheckboxSelectMultiple, forms.RadioSelect)):
                continue
            w.attrs.setdefault("class", "input")
            if isinstance(w, forms.Textarea):
                w.attrs.setdefault("rows", 4)


class StyledForm(Styled, forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style()


class StyledModelForm(Styled, forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style()


def check_upload(f, kinds=None):
    if f and f.size > settings.TLV_MAX_UPLOAD_MB * 1024 * 1024:
        raise forms.ValidationError(_("El fichero supera el máximo de %(mb)s MB.") % {"mb": settings.TLV_MAX_UPLOAD_MB})
    if f and kinds:
        name = (f.name or "").lower()
        if not any(name.endswith(ext) for ext in kinds):
            raise forms.ValidationError(_("Formato no admitido. Usa: %(list)s") % {"list": ", ".join(kinds)})
    return f


DOC_KINDS = (".pdf", ".jpg", ".jpeg", ".png", ".webp")


# --- Alta y acceso ------------------------------------------------------------
class SignupBase(StyledForm):
    email = forms.EmailField(label=_("Correo electrónico"))
    password = forms.CharField(label=_("Contraseña"), widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
                               help_text=_("Mínimo 10 caracteres."))
    accept_terms = forms.BooleanField(label=_("Acepto las condiciones de uso y la política de privacidad."))

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError(_("Ya existe una cuenta con este correo."))
        return email

    def clean_password(self):
        password = self.cleaned_data["password"]
        password_validation.validate_password(password)
        return password


class TalentSignupForm(SignupBase):
    legal_name = forms.CharField(label=_("Nombre y apellidos (como en tu documento)"), max_length=200)
    stage_name = forms.CharField(label=_("Nombre profesional"), max_length=120)
    is_adult = forms.BooleanField(label=_("Soy mayor de edad y la voz que voy a subir es la mía."))
    field_order = ["legal_name", "stage_name", "email", "password", "is_adult", "accept_terms"]


class ClientSignupForm(SignupBase):
    company_name = forms.CharField(label=_("Empresa"), max_length=200)
    tax_id = forms.CharField(label=_("CIF / NIF / VAT"), max_length=30)
    contact_name = forms.CharField(label=_("Persona de contacto"), max_length=200)
    billing_address = forms.CharField(label=_("Dirección de facturación"), max_length=250)
    postal_code = forms.CharField(label=_("Código postal"), max_length=15)
    city = forms.CharField(label=_("Ciudad"), max_length=120)
    country = forms.CharField(label=_("País"), max_length=80, initial="España")
    field_order = ["company_name", "tax_id", "contact_name", "email", "password", "billing_address",
                   "postal_code", "city", "country", "accept_terms"]


class LoginForm(Styled, AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = _("Correo electrónico")
        self._style()


# --- Locutor ------------------------------------------------------------------
class TalentProfileForm(StyledModelForm):
    class Meta:
        model = TalentProfile
        fields = ["stage_name", "legal_name", "tax_id", "phone", "country", "city", "experience_years", "website",
                  "bio", "payout_holder", "payout_iban"]


class VoiceForm(StyledModelForm):
    class Meta:
        model = Voice
        fields = ["name", "tagline", "description", "gender", "age", "traits", "languages", "accents", "styles",
                  "project_types", "base_price", "restrictions"]
        widgets = {
            "languages": forms.CheckboxSelectMultiple, "accents": forms.CheckboxSelectMultiple,
            "styles": forms.CheckboxSelectMultiple, "project_types": forms.CheckboxSelectMultiple,
            "description": forms.Textarea(attrs={"rows": 4}), "restrictions": forms.Textarea(attrs={"rows": 3}),
        }

    def clean_languages(self):
        langs = self.cleaned_data["languages"]
        if not langs:
            raise forms.ValidationError(_("Elige al menos un idioma."))
        return langs

    def clean_base_price(self):
        price = self.cleaned_data["base_price"]
        if price < 10:
            raise forms.ValidationError(_("La tarifa mínima es de 10 €."))
        return price


class IdentityForm(StyledForm):
    document = forms.FileField(
        label=_("Documento de identidad (DNI, NIE o pasaporte)"),
        help_text=_("PDF o foto. Solo lo ve el equipo de verificación y se borra en cuanto se revisa."),
    )

    def clean_document(self):
        return check_upload(self.cleaned_data["document"], DOC_KINDS)


class SampleForm(StyledForm):
    file = forms.FileField(label=_("Grabación"), widget=forms.ClearableFileInput(attrs={"accept": AUDIO_ACCEPT}))
    kind = forms.ChoiceField(
        label=_("Para qué es"),
        choices=[("training", _("Para crear mi voz digital")), ("showcase", _("Muestra original para enseñar en el catálogo"))],
    )
    title = forms.CharField(label=_("Título (opcional)"), max_length=120, required=False)

    def clean_file(self):
        return check_upload(self.cleaned_data["file"])


class RecordingForm(forms.Form):
    recording = forms.FileField()

    def clean_recording(self):
        return check_upload(self.cleaned_data["recording"])


class SignContractForm(StyledForm):
    signed_name = forms.CharField(label=_("Escribe tu nombre y apellidos para firmar"), max_length=200)
    biometric_consent = forms.BooleanField(
        label=_("Consiento expresamente que Todas las Voces trate mis grabaciones y cree un modelo digital de mi voz con las finalidades del contrato."),
    )
    accept_contract = forms.BooleanField(label=_("He leído el contrato completo y lo acepto."))


class RevokeForm(StyledForm):
    reason = forms.CharField(label=_("Motivo (opcional)"), required=False, max_length=300)
    confirm = forms.BooleanField(label=_("Entiendo que se borrará mi voz digital y se cancelarán las solicitudes en curso."))


class DecisionForm(StyledForm):
    action = forms.ChoiceField(choices=[("approve", "approve"), ("reject", "reject"), ("info", "info")])
    price = forms.DecimalField(label=_("Precio (€, sin IVA)"), required=False, min_value=Decimal("1"), decimal_places=2)
    note = forms.CharField(label=_("Mensaje"), required=False, widget=forms.Textarea(attrs={"rows": 3}), max_length=2000)

    def clean(self):
        data = super().clean()
        if data.get("action") in ("reject", "info") and not (data.get("note") or "").strip():
            self.add_error("note", _("Escribe un mensaje para el cliente."))
        return data


# --- Cliente ------------------------------------------------------------------
class ClientProfileForm(StyledModelForm):
    class Meta:
        model = ClientProfile
        fields = ["company_name", "tax_id", "contact_name", "phone", "sector", "website", "billing_address",
                  "postal_code", "city", "country"]


class LicenseRequestForm(StyledModelForm):
    channels = forms.MultipleChoiceField(label=_("Canales de difusión"), choices=CHANNELS, widget=forms.CheckboxSelectMultiple)
    accept = forms.BooleanField(label=_("Me comprometo a usar la locución solo en las condiciones que apruebe el locutor."))

    class Meta:
        model = LicenseRequest
        fields = ["project_name", "category", "purpose", "sector", "script", "territory", "channels",
                  "duration_months", "conditions"]
        widgets = {"script": forms.Textarea(attrs={"rows": 8}), "purpose": forms.Textarea(attrs={"rows": 3}),
                   "conditions": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, voice=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.voice = voice
        allowed = voice.allowed_categories() if voice else UseCategory.objects.none()
        self.fields["category"].queryset = allowed
        self.fields["category"].empty_label = None
        self.fields["category"].label_from_instance = lambda c: c.name
        cfg = PlatformSettings.load()
        self.fields["script"].help_text = _("Texto exacto que se va a locutar. Máximo %(n)s caracteres.") % {"n": cfg.script_max_chars}
        self.fields["script"].widget.attrs.update({"maxlength": cfg.script_max_chars, "data-count": "1"})


class MessageForm(StyledForm):
    body = forms.CharField(label=_("Mensaje"), widget=forms.Textarea(attrs={"rows": 3}), max_length=3000)
    attachment = forms.FileField(label=_("Adjunto (opcional)"), required=False)

    def clean_attachment(self):
        return check_upload(self.cleaned_data.get("attachment"))


class TransferForm(StyledForm):
    receipt = forms.FileField(label=_("Justificante de la transferencia (opcional)"), required=False)

    def clean_receipt(self):
        return check_upload(self.cleaned_data.get("receipt"), DOC_KINDS)


class DisputeForm(StyledForm):
    description = forms.CharField(label=_("Qué ha pasado"), widget=forms.Textarea(attrs={"rows": 5}), max_length=4000)
    url = forms.URLField(label=_("Dónde lo has visto u oído (enlace)"), required=False, max_length=500)
    evidence = forms.FileField(
        label=_("Prueba (audio o documento)"), required=False,
        help_text=_("Si subes el audio, intentaremos identificarlo por su marca de agua."),
    )

    def clean_evidence(self):
        return check_upload(self.cleaned_data.get("evidence"))


class PublicReportForm(DisputeForm):
    reporter_email = forms.EmailField(label=_("Tu correo, para responderte"))
    field_order = ["reporter_email", "description", "url", "evidence"]


# --- Gestión ------------------------------------------------------------------
class SettingsForm(StyledModelForm):
    class Meta:
        model = PlatformSettings
        exclude = ["territory_factors", "duration_factors"]


class TalentAdminForm(StyledModelForm):
    class Meta:
        model = TalentProfile
        fields = ["fee_override", "representative"]


class CategoryForm(StyledModelForm):
    class Meta:
        model = UseCategory
        fields = ["name_es", "name_en", "description_es", "description_en", "sensitive", "price_factor", "active", "order"]


class NoteForm(StyledForm):
    note = forms.CharField(label=_("Nota"), widget=forms.Textarea(attrs={"rows": 3}), max_length=3000)


class AudioCheckForm(StyledForm):
    file = forms.FileField(label=_("Audio que quieres identificar"))

    def clean_file(self):
        return check_upload(self.cleaned_data["file"])
