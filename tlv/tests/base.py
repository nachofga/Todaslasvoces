"""Base de las pruebas: crea usuarios y voces recorriendo los mismos pasos que un usuario real."""
import io
import shutil
import tempfile

import numpy as np
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone, translation

from tlv import legal
from tlv.models import ClientProfile, Language, TalentProfile, UseCategory, User, Voice, VoiceVerification
from tlv.services import audio, licenseflow, voiceflow
from tlv.services.engines import SimulatedEngine

PASSWORD = "clave-de-pruebas-2026"
READING = (
    "Buenos días. Esta es una muestra de mi voz para Todas las Voces. Leo con mi tono natural, sin forzar. "
    "Al caer la tarde, la ciudad cambia de ritmo y las calles se llenan de luz. Descubre la nueva colección, "
    "solo esta semana, en tu tienda de siempre. Oye, ¿te has fijado en lo bien que suena esto cuando lo dices despacio? "
    "Durante siglos estas montañas han guardado historias que hoy por fin podemos contar con calma y con detalle."
)
MEDIA = tempfile.mkdtemp(prefix="tlv-test-")


def wav_upload(key, text, name="muestra.wav", noise=0.0012):
    arr, sr = audio.decode(SimulatedEngine().synthesize(key, text))
    arr = arr + noise * np.random.default_rng(len(text)).standard_normal(len(arr)).astype(np.float32)
    return SimpleUploadedFile(name, audio.encode_wav(arr, sr), content_type="audio/wav")


@override_settings(
    MEDIA_ROOT=MEDIA, TLV_VOICE_ENGINE="simulated", EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    STORAGES={"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
              "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}},
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
    SECURE_SSL_REDIRECT=False, TLV_EMAIL_CONFIGURED=True, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False,
)
class TlvTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("bootstrap", stdout=io.StringIO())
        cls.staff = User.objects.create_superuser("gestion@test.local", PASSWORD)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    # --- fábricas -----------------------------------------------------------
    @classmethod
    def make_talent(cls, n=1, legal_name="Ana Prueba Ruiz", verified=True):
        user = User.objects.create_user(f"locutor{n}@test.local", PASSWORD, role=User.Role.TALENT,
                                        email_verified_at=timezone.now(), terms_accepted_at=timezone.now())
        talent = TalentProfile.objects.create(
            user=user, legal_name=legal_name, stage_name=f"Voz {n}", tax_id="00000000T",
            identity_status="verified" if verified else "none",
        )
        voice = Voice.objects.create(talent=talent, name=f"Voz {n}", base_price=100)
        voice.languages.set(Language.objects.filter(code="es"))
        return user, voice

    @classmethod
    def make_client(cls, n=1, verified=True):
        user = User.objects.create_user(f"cliente{n}@test.local", PASSWORD, role=User.Role.CLIENT,
                                        email_verified_at=timezone.now(), terms_accepted_at=timezone.now())
        ClientProfile.objects.create(
            user=user, company_name=f"Empresa {n}, S.L.", tax_id=f"B0000000{n}", contact_name="Contacto",
            billing_address="Calle 1", postal_code="28001", city="Madrid",
            status="verified" if verified else "pending",
        )
        return user

    @classmethod
    def publish(cls, voice, deny=("politica",)):
        """Lleva una voz de borrador a activa por el camino real."""
        key = f"k-{voice.pk}"
        for i in range(2):
            voiceflow.add_sample(voice, wav_upload(key, READING + f" Toma {i}."))
        ver = voiceflow.start_verification(voice)
        voiceflow.complete_verification(ver, wav_upload(key, ver.phrase))
        VoiceVerification.objects.filter(pk=ver.pk).update(status="passed")
        cleaned = {c.category.code: {"allowed": c.category.code not in deny, "price": None, "notes": ""}
                   for c in voiceflow.ensure_consents(voice)}
        voiceflow.update_consents(voice, cleaned, voice.talent.user)
        voiceflow.sign_contract(voice, voice.talent.legal_name, legal.contract_text(voice.talent), "es",
                                {"ip": "127.0.0.1", "ua": "test"}, True)
        voiceflow.submit_for_review(voice)
        voiceflow.approve_and_clone(voice, cls.staff)
        voiceflow.activate(voice)
        voice.refresh_from_db()
        return voice

    @staticmethod
    def request_data(**over):
        data = dict(
            category=UseCategory.objects.get(code="publicidad-digital"), project_name="Campaña", purpose="Anuncio online",
            sector="Moda", script="Este otoño, vístete de ti. Nueva colección, ya en tiendas y en nuestra web.",
            territory="es", channels=["online"], duration_months=12, conditions="",
        )
        data.update(over)
        return data

    @classmethod
    def licensed(cls, client_user, voice):
        """Solicitud aprobada, pagada y con licencia activa."""
        req = licenseflow.create_request(client_user, voice, cls.request_data())
        licenseflow.talent_approve(req, voice.talent.user)
        req.refresh_from_db()
        licenseflow.confirm_payment(req.payment, cls.staff)
        req.refresh_from_db()
        return req

    def setUp(self):
        translation.activate("es")

    def login(self, user):
        self.client.force_login(user)
