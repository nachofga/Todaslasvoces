"""Pruebas de las piezas que hacen fiable la plataforma: marca de agua, reparto, registro y control de usos."""
import random
from decimal import Decimal

import numpy as np

from tlv.models import LedgerEntry, UseCategory, Voice
from tlv.services import audio, disputes, ledger, licenseflow, money, policy, voiceflow, watermark
from tlv.services.engines import SimulatedEngine
from tlv.services.voiceflow import FlowError

from .base import TlvTestCase, wav_upload

LONG = ("Hola, esta es una locución de prueba para la plataforma Todas las Voces. Queremos comprobar que la marca "
        "de agua se puede leer después de comprimir el audio, recortarlo o cambiarle el volumen.")


class WatermarkTests(TlvTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.arr, cls.sr = audio.decode(SimulatedEngine().synthesize("sim_test", LONG))
        cls.marked = watermark.embed(cls.arr, cls.sr, 0xABCDE1)
        cls.others = [int(x) for x in np.random.default_rng(1).integers(1, 2**24, 500)]

    def test_la_marca_no_cambia_el_audio_de_forma_apreciable(self):
        noise = self.marked - self.arr[: len(self.marked)]
        snr = 10 * np.log10(np.sum(self.arr ** 2) / np.sum(noise ** 2))
        self.assertGreater(snr, 24)

    def test_se_lee_en_el_audio_limpio(self):
        res = watermark.detect(self.marked, 44100, candidates=self.others + [0xABCDE1])
        self.assertTrue(res["found"])
        self.assertEqual(res["payload"], 0xABCDE1)

    def test_resiste_mp3_recorte_volumen_y_cambio_de_frecuencia(self):
        cand = self.others + [0xABCDE1]
        variants = {
            "mp3 128k": audio.decode(audio.encode_mp3(self.marked, 44100, "128k")),
            "recorte": (self.marked[int(1.37 * 44100):], 44100),
            "volumen y 22 kHz": (audio.resample(audio.resample(self.marked * 0.3, 44100, 22050), 22050, 48000), 48000),
        }
        for name, (arr, sr) in variants.items():
            res = watermark.detect(arr, sr, candidates=cand)
            self.assertTrue(res["found"] and res["payload"] == 0xABCDE1, f"no se lee tras: {name} ({res})")

    def test_un_audio_sin_marca_no_da_falsos_positivos(self):
        self.assertFalse(watermark.detect(self.arr, self.sr, candidates=self.others)["found"])
        noise = np.random.default_rng(3).standard_normal(44100 * 12).astype(np.float32) * 0.1
        self.assertFalse(watermark.detect(noise, 44100, candidates=self.others)["found"])

    def test_identificador_fuera_de_rango(self):
        with self.assertRaises(ValueError):
            watermark.embed(self.arr, self.sr, 2 ** 24)


class AudioQualityTests(TlvTestCase):
    def test_muestra_correcta_pasa(self):
        arr, sr = audio.decode(wav_upload("q", LONG).read())
        self.assertTrue(audio.analyze(arr, sr)["ok"])

    def test_detecta_saturacion_ruido_y_duracion(self):
        arr, sr = audio.decode(wav_upload("q", LONG).read())
        codes = lambda a, s=sr: {i["code"] for i in audio.analyze(a, s)["issues"]}  # noqa: E731
        self.assertIn("clipping", codes(np.clip(arr * 30, -1, 1)))
        self.assertIn("noisy", codes(arr + 0.2 * np.random.default_rng(0).standard_normal(len(arr)).astype(np.float32)))
        self.assertIn("short", codes(arr[: sr * 4]))
        self.assertIn("low_rate", codes(audio.resample(arr, sr, 8000), 8000))
        self.assertIn("quiet", codes(arr * 0.01))


class MoneyTests(TlvTestCase):
    def test_el_reparto_siempre_suma_el_neto(self):
        rng = random.Random(7)
        for _ in range(2000):
            net = Decimal(rng.randint(1, 2_000_000)) / 100
            fee = Decimal(rng.randint(0, 6000)) / 100
            rep = Decimal(rng.randint(0, 5000)) / 100
            platform, talent, representative = money.split_amounts(net, fee, rep)
            self.assertEqual(platform + talent + representative, net)
            self.assertTrue(platform >= 0 and talent >= 0 and representative >= 0)

    def test_ejemplo_de_reparto(self):
        self.assertEqual(money.split_amounts("1000", "20", "10"), (Decimal("200.00"), Decimal("720.00"), Decimal("80.00")))

    def test_presupuesto(self):
        _u, voice = self.make_talent(50)
        cat = UseCategory.objects.get(code="publicidad-digital")          # x1.50
        self.assertEqual(money.quote(voice, cat, "es", 12), Decimal("150.00"))
        self.assertEqual(money.quote(voice, cat, "eu", 24), Decimal("384.00"))  # 100 x 1.5 x 1.6 x 1.6


class LedgerTests(TlvTestCase):
    def test_cadena_integra_y_detecta_manipulacion(self):
        for i in range(5):
            ledger.record("prueba", None, n=i, importe=Decimal("10.50"))
        self.assertTrue(ledger.verify()["ok"])
        entry = LedgerEntry.objects.order_by("seq")[2]
        LedgerEntry.objects.filter(pk=entry.pk).update(payload={"n": 999})
        result = ledger.verify()
        self.assertFalse(result["ok"])
        self.assertEqual(result["broken_at"], entry.seq)

    def test_detecta_borrado(self):
        for i in range(4):
            ledger.record("prueba", None, n=i)
        LedgerEntry.objects.order_by("seq")[1].delete()
        self.assertFalse(ledger.verify()["ok"])


class PolicyTests(TlvTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.talent_user, voice = cls.make_talent(1)
        cls.voice = cls.publish(voice)
        cls.client_user = cls.make_client(1)

    def test_categoria_no_autorizada_se_bloquea(self):
        politica = UseCategory.objects.get(code="politica")
        with self.assertRaises(FlowError):
            licenseflow.create_request(self.client_user, self.voice, self.request_data(category=politica))

    def test_cliente_sin_validar_no_puede_solicitar(self):
        other = self.make_client(2, verified=False)
        self.assertEqual(policy.check_request(other, self.voice, self.request_data()["category"], "hola mundo").code, "client_unverified")

    def test_sin_licencia_activa_no_hay_audio_final(self):
        req = licenseflow.create_request(self.client_user, self.voice, self.request_data())
        self.assertEqual(policy.check_final(req, self.client_user).code, "not_active")
        licenseflow.talent_approve(req, self.talent_user)
        req.refresh_from_db()
        self.assertEqual(policy.check_final(req, self.client_user).code, "not_active")   # aprobada pero sin pagar
        licenseflow.confirm_payment(req.payment, self.staff)
        req.refresh_from_db()
        self.assertTrue(policy.check_final(req, self.client_user))

    def test_pausa_bloquea_usos_nuevos_y_reactivar_los_permite(self):
        req = self.licensed(self.client_user, self.voice)
        voiceflow.pause(self.voice)
        self.assertEqual(policy.check_final(req, self.client_user).code, "voice_paused")
        self.assertEqual(policy.check_demo(self.client_user, self.voice, "hola").code, "voice_paused")
        voiceflow.activate(self.voice)
        req = type(req).objects.get(pk=req.pk)
        self.assertTrue(policy.check_final(req, self.client_user))

    def test_retirar_una_categoria_bloquea_las_tomas_nuevas(self):
        req = self.licensed(self.client_user, self.voice)
        voiceflow.update_consents(self.voice, {"publicidad-digital": {"allowed": False, "price": None, "notes": ""}}, self.talent_user)
        self.assertEqual(policy.check_final(req, self.client_user).code, "category_denied")

    def test_limite_de_tomas_y_de_demos(self):
        req = self.licensed(self.client_user, self.voice)
        req.max_takes = 1
        req.save()
        licenseflow.generate_take(req, self.client_user)
        with self.assertRaises(FlowError):
            licenseflow.generate_take(req, self.client_user)
        self.assertEqual(policy.check_demo(self.client_user, self.voice, "x" * 500).code, "demo_too_long")

    def test_licencia_caducada(self):
        from django.utils import timezone

        req = self.licensed(self.client_user, self.voice)
        req.license.valid_until = timezone.localdate() - timezone.timedelta(days=1)
        req.license.save()
        self.assertEqual(policy.check_final(req, self.client_user).code, "license_expired")
        self.assertEqual(licenseflow.expire_licenses(), 1)

    def test_revocar_borra_el_modelo_y_cancela_lo_pendiente(self):
        pending = licenseflow.create_request(self.client_user, self.voice, self.request_data(project_name="Otra"))
        paid = self.licensed(self.client_user, self.voice)
        voiceflow.revoke(self.voice, "ya no quiero", self.talent_user)
        self.voice.refresh_from_db()
        pending.refresh_from_db()
        self.assertEqual(self.voice.status, Voice.Status.REVOKED)
        self.assertEqual(self.voice.external_voice_id, "")
        self.assertEqual(pending.status, "cancelled")
        self.assertEqual(policy.check_final(paid, self.client_user).code, "voice_revoked")
        self.assertTrue(policy.can_download(paid, self.client_user))      # lo ya entregado se conserva

    def test_cada_audio_queda_identificado_y_la_disputa_lo_encuentra(self):
        req = self.licensed(self.client_user, self.voice)
        gen = licenseflow.generate_take(req, self.client_user)
        with gen.file_mp3.open("rb") as fh:
            exact = fh.read()
        self.assertEqual(disputes.identify_audio(exact)["match"], "hash")
        arr, sr = audio.decode(exact)
        recompressed = audio.encode_mp3(arr[sr // 2:] * 0.5, sr, "96k")      # recortado, más bajo y recomprimido
        info = disputes.identify_audio(recompressed, voice=self.voice)
        self.assertEqual(info["match"], "watermark")
        self.assertEqual(info["generation"].pk, gen.pk)
        self.assertTrue(LedgerEntry.objects.filter(event="audio.generated", payload__generation=gen.pk).exists())
