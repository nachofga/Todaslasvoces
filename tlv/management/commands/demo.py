"""
Datos de demostración para enseñar la plataforma sin tocar nada real.

    python manage.py demo

Crea tres locutores con su voz publicada, un cliente validado, una licencia
pagada con su locución y una solicitud pendiente. Recorre los mismos pasos que
un usuario real (muestras, contrato, revisión, clonación...), así que también
sirve para comprobar que todo funciona.

Solo se ejecuta con el motor de voz simulado, para no gastar créditos ni crear
voces de mentira en el proveedor real.
"""
import io

import numpy as np
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from tlv import legal
from tlv.models import (
    Accent,
    ClientProfile,
    Language,
    ProjectType,
    Style,
    TalentProfile,
    UseCategory,
    User,
    Voice,
    VoiceVerification,
)
from tlv.services import audio, licenseflow, voiceflow
from tlv.services.engines import SimulatedEngine

PASSWORD = "demo-TodasLasVoces-2026"

TALENTS = [
    dict(email="lucia@demo.todaslasvoces.test", legal="Lucía Ferrer Montalbán", stage="Lucía Ferrer", gender="female", age="adult",
         tagline="Cálida y cercana, para marcas que hablan de tú", traits="cálida, luminosa, sonriente", price=180,
         styles=["calido", "cercano", "narrativo"], types=["publicidad", "elearning", "corporativo"], accents=["castellano"],
         langs=["es", "en"], deny=["politica", "salud-finanzas", "asistentes"]),
    dict(email="andres@demo.todaslasvoces.test", legal="Andrés Colmenar Ruiz", stage="Andrés Colmenar", gender="male", age="mature",
         tagline="Grave y sobria, de documental y tráiler", traits="grave, rotunda, pausada", price=240,
         styles=["sobrio", "epico", "institucional"], types=["documental", "doblaje", "publicidad"], accents=["castellano"],
         langs=["es"], deny=["politica", "redes-creadores"]),
    dict(email="marta@demo.todaslasvoces.test", legal="Marta Quiroga Salas", stage="Marta Quiroga", gender="female", age="young",
         tagline="Enérgica y actual, para redes y videojuegos", traits="brillante, ágil, expresiva", price=140,
         styles=["energico", "juvenil", "comico"], types=["videojuegos", "animacion", "podcast"], accents=["neutro-latam"],
         langs=["es"], deny=["politica", "salud-finanzas", "ivr"]),
]
READING = (
    "Buenos días. Me llamo {name} y esta es una muestra de mi voz para Todas las Voces. "
    "Leo con mi tono natural, sin forzar, a la distancia habitual del micrófono. "
    "Primero una parte narrativa: al caer la tarde, la ciudad cambia de ritmo y las calles se llenan de luz. "
    "Después una parte comercial: descubre la nueva colección, solo esta semana, en tu tienda de siempre. "
    "Y para terminar, una parte conversacional: oye, ¿te has fijado en lo bien que suena esto cuando lo dices despacio?"
)


def _wav(voice_key, text, name):
    raw = SimulatedEngine().synthesize(voice_key, text)
    arr, sr = audio.decode(raw)
    rng = np.random.default_rng(len(text))
    arr = arr + 0.0012 * rng.standard_normal(len(arr)).astype(np.float32)   # ruido de sala realista
    return SimpleUploadedFile(name, audio.encode_wav(arr, sr), content_type="audio/wav")


class Command(BaseCommand):
    help = "Crea datos de demostración (solo con el motor de voz simulado)."

    def add_arguments(self, parser):
        parser.add_argument("--quiet", action="store_true")

    def handle(self, *args, **options):
        if settings.TLV_VOICE_ENGINE != "simulated":
            raise CommandError("Los datos de demostración solo se crean con el motor simulado (TLV_VOICE_ENGINE=simulated).")
        if not UseCategory.objects.exists():
            raise CommandError("Ejecuta antes: python manage.py bootstrap")
        if User.objects.filter(email=TALENTS[0]["email"]).exists():
            self.stdout.write("Los datos de demostración ya existen.")
            return

        staff = User.objects.filter(role=User.Role.STAFF).first() or User.objects.create_superuser(
            "gestion@demo.todaslasvoces.test", PASSWORD
        )
        voices = []
        for t in TALENTS:
            user = User.objects.create_user(t["email"], PASSWORD, role=User.Role.TALENT, email_verified_at=timezone.now(),
                                            terms_accepted_at=timezone.now())
            talent = TalentProfile.objects.create(
                user=user, legal_name=t["legal"], stage_name=t["stage"], tax_id="00000000T", city="Madrid",
                bio="Perfil de demostración.", identity_status=TalentProfile.Identity.VERIFIED, payout_iban="ES00 0000 0000 0000 0000 0000",
                payout_holder=t["legal"],
            )
            voice = Voice.objects.create(talent=talent, name=t["stage"], tagline=t["tagline"], gender=t["gender"], age=t["age"],
                                         traits=t["traits"], base_price=t["price"],
                                         description="Voz de demostración creada con el motor simulado.")
            voice.languages.set(Language.objects.filter(code__in=t["langs"]))
            voice.accents.set(Accent.objects.filter(code__in=t["accents"]))
            voice.styles.set(Style.objects.filter(code__in=t["styles"]))
            voice.project_types.set(ProjectType.objects.filter(code__in=t["types"]))

            key = f"demo-{voice.slug}"
            for i in range(2):
                voiceflow.add_sample(voice, _wav(key, READING.format(name=t["stage"]) + f" Toma {i + 1}.", f"toma-{i + 1}.wav"))
            ver = voiceflow.start_verification(voice)
            voiceflow.complete_verification(ver, _wav(key, ver.phrase, "frase.wav"))
            VoiceVerification.objects.filter(pk=ver.pk).update(status=VoiceVerification.Status.PASSED, detail="Demo")

            cleaned = {c.category.code: {"allowed": c.category.code not in t["deny"], "price": None, "notes": ""}
                       for c in voiceflow.ensure_consents(voice)}
            voiceflow.update_consents(voice, cleaned, user)
            voiceflow.sign_contract(voice, t["legal"], legal.contract_text(talent), "es", {"ip": "127.0.0.1", "ua": "demo"}, True)
            voiceflow.submit_for_review(voice)
            voiceflow.approve_and_clone(voice, staff)
            voiceflow.activate(voice)
            voices.append(voice)
            if not options["quiet"]:
                self.stdout.write(f"  voz publicada: {voice.name}")

        cuser = User.objects.create_user("cliente@demo.todaslasvoces.test", PASSWORD, role=User.Role.CLIENT,
                                         email_verified_at=timezone.now(), terms_accepted_at=timezone.now())
        ClientProfile.objects.create(
            user=cuser, company_name="Productora Albor, S.L.", tax_id="B00000000", contact_name="Irene Castaño",
            billing_address="Calle de la Demo, 1", postal_code="28001", city="Madrid", status=ClientProfile.Status.VERIFIED,
        )
        base = dict(territory="es", channels=["online", "social"], duration_months=12, conditions="")
        req = licenseflow.create_request(cuser, voices[0], dict(
            base, category=UseCategory.objects.get(code="publicidad-digital"), project_name="Campaña de otoño",
            purpose="Anuncio de 20 segundos para la campaña de otoño de una marca de moda.", sector="Moda",
            script="Este otoño, vístete de ti. Nueva colección, ya en tiendas y en nuestra web.",
        ))
        licenseflow.talent_approve(req, voices[0].talent.user)
        req.refresh_from_db()
        licenseflow.report_transfer(req, cuser)
        licenseflow.confirm_payment(req.payment, staff)
        req.refresh_from_db()
        licenseflow.generate_take(req, cuser)
        licenseflow.create_request(cuser, voices[1], dict(
            base, category=UseCategory.objects.get(code="documental"), project_name="Documental «Ríos de piedra»",
            purpose="Narración de un documental de 50 minutos para plataformas.", sector="Audiovisual",
            script="Durante millones de años, el agua ha tallado estas montañas con la paciencia de quien no tiene prisa.",
            territory="eu", channels=["online", "tv"],
        ))
        self.stdout.write(self.style.SUCCESS(
            "Datos de demostración creados.\n"
            f"  Contraseña de todas las cuentas: {PASSWORD}\n"
            f"  Gestión:  {staff.email}\n"
            "  Locutor:  lucia@demo.todaslasvoces.test\n"
            "  Cliente:  cliente@demo.todaslasvoces.test"
        ))
