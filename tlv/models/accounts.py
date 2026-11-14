"""Usuarios, perfiles de locutor y de cliente, representantes y derechos RGPD."""
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


def private_upload(folder):
    """Devuelve una función de ruta para ficheros privados: <folder>/<año>/<nombre>."""

    def _path(instance, filename):
        import uuid
        from pathlib import PurePosixPath

        ext = PurePosixPath(filename).suffix.lower()[:8]
        return f"{folder}/{timezone.now():%Y/%m}/{uuid.uuid4().hex}{ext}"

    _path.__qualname__ = f"private_upload_{folder}"
    return _path


def upload_identity(instance, filename):
    return private_upload("identidad")(instance, filename)


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create(self, email, password, **extra):
        if not email:
            raise ValueError("El correo es obligatorio")
        user = self.model(email=self.normalize_email(email).lower(), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.update(is_staff=True, is_superuser=True, role=User.Role.STAFF)
        extra.setdefault("email_verified_at", timezone.now())
        return self._create(email, password, **extra)


class User(AbstractUser):
    class Role(models.TextChoices):
        TALENT = "talent", _("Locutor")
        CLIENT = "client", _("Cliente")
        STAFF = "staff", _("Gestión")

    username = None
    email = models.EmailField(_("correo electrónico"), unique=True)
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.CLIENT)
    language = models.CharField(max_length=5, default="es")
    email_verified_at = models.DateTimeField(null=True, blank=True)
    terms_accepted_at = models.DateTimeField(null=True, blank=True)
    suspended = models.BooleanField(default=False)
    suspended_reason = models.CharField(max_length=300, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []
    objects = UserManager()

    class Meta:
        verbose_name = _("usuario")
        verbose_name_plural = _("usuarios")

    def __str__(self):
        return self.email

    @property
    def is_talent(self):
        return self.role == self.Role.TALENT

    @property
    def is_client(self):
        return self.role == self.Role.CLIENT

    @property
    def is_manager(self):
        return self.role == self.Role.STAFF or self.is_staff

    @property
    def email_verified(self):
        return self.email_verified_at is not None

    @property
    def display_name(self):
        if self.is_talent and hasattr(self, "talent"):
            return self.talent.stage_name or self.get_full_name() or self.email
        if self.is_client and hasattr(self, "client"):
            return self.client.company_name or self.get_full_name() or self.email
        return self.get_full_name() or self.email


class Representative(models.Model):
    """Representante o estudio asociado que cobra una parte de lo del locutor."""

    name = models.CharField(_("nombre"), max_length=200)
    email = models.EmailField(blank=True)
    tax_id = models.CharField(_("NIF/CIF"), max_length=30, blank=True)
    share_percent = models.DecimalField(
        _("% sobre la parte del locutor"), max_digits=5, decimal_places=2, default=0
    )
    payout_details = models.TextField(_("datos de pago"), blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        verbose_name = _("representante")
        verbose_name_plural = _("representantes")

    def __str__(self):
        return self.name


class TalentProfile(models.Model):
    class Identity(models.TextChoices):
        NONE = "none", _("Sin enviar")
        SUBMITTED = "submitted", _("En revisión")
        VERIFIED = "verified", _("Verificada")
        REJECTED = "rejected", _("Rechazada")

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="talent")
    legal_name = models.CharField(_("nombre y apellidos (legal)"), max_length=200)
    stage_name = models.CharField(_("nombre profesional"), max_length=120)
    tax_id = models.CharField(_("NIF/NIE"), max_length=30, blank=True)
    phone = models.CharField(_("teléfono"), max_length=40, blank=True)
    country = models.CharField(_("país"), max_length=80, default="España")
    city = models.CharField(_("ciudad"), max_length=120, blank=True)
    bio = models.TextField(_("presentación"), blank=True, max_length=1500)
    experience_years = models.PositiveSmallIntegerField(_("años de experiencia"), null=True, blank=True)
    website = models.URLField(_("web o demo reel"), blank=True)

    identity_status = models.CharField(max_length=12, choices=Identity.choices, default=Identity.NONE)
    identity_document = models.FileField(upload_to=upload_identity, blank=True)
    identity_note = models.CharField(max_length=300, blank=True)
    identity_reviewed_at = models.DateTimeField(null=True, blank=True)
    identity_reviewed_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    representative = models.ForeignKey(
        Representative, null=True, blank=True, on_delete=models.SET_NULL, related_name="talents"
    )
    fee_override = models.DecimalField(
        _("comisión de plataforma específica (%)"), max_digits=5, decimal_places=2, null=True, blank=True
    )
    payout_holder = models.CharField(_("titular de la cuenta"), max_length=200, blank=True)
    payout_iban = models.CharField(_("IBAN para cobros"), max_length=42, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("locutor")
        verbose_name_plural = _("locutores")

    def __str__(self):
        return self.stage_name or self.legal_name

    @property
    def identity_verified(self):
        return self.identity_status == self.Identity.VERIFIED


class ClientProfile(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", _("Pendiente de validar")
        VERIFIED = "verified", _("Validado")
        REJECTED = "rejected", _("Rechazado")

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="client")
    company_name = models.CharField(_("empresa"), max_length=200)
    tax_id = models.CharField(_("CIF/NIF/VAT"), max_length=30)
    contact_name = models.CharField(_("persona de contacto"), max_length=200)
    phone = models.CharField(_("teléfono"), max_length=40, blank=True)
    sector = models.CharField(_("sector"), max_length=120, blank=True)
    website = models.URLField(_("web"), blank=True)
    billing_address = models.CharField(_("dirección de facturación"), max_length=250)
    postal_code = models.CharField(_("código postal"), max_length=15)
    city = models.CharField(_("ciudad"), max_length=120)
    country = models.CharField(_("país"), max_length=80, default="España")

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    status_note = models.CharField(max_length=300, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    blocked = models.BooleanField(default=False)
    blocked_reason = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("cliente")
        verbose_name_plural = _("clientes")

    def __str__(self):
        return self.company_name

    @property
    def verified(self):
        return self.status == self.Status.VERIFIED


class DataRequest(models.Model):
    """Solicitudes de derechos RGPD: copia de los datos o supresión de la cuenta."""

    class Kind(models.TextChoices):
        EXPORT = "export", _("Copia de mis datos")
        DELETE = "delete", _("Supresión de la cuenta")

    class Status(models.TextChoices):
        OPEN = "open", _("Abierta")
        DONE = "done", _("Atendida")
        REJECTED = "rejected", _("Denegada")

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="data_requests")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
