"""Pruebas de la web de punta a punta: lo que hace cada tipo de usuario desde el navegador."""
import json

from django.core import mail
from django.urls import reverse
from django.utils import translation

from tlv.models import (
    ClientProfile,
    Contract,
    DataRequest,
    Dispute,
    Generation,
    LicenseRequest,
    TalentProfile,
    User,
    Voice,
    VoiceSample,
)
from tlv.services import licenseflow, voiceflow

from .base import PASSWORD, READING, TlvTestCase, wav_upload


class SignupAndOnboardingTests(TlvTestCase):
    def test_alta_de_locutor_completa_hasta_publicar_la_voz(self):
        c = self.client
        # 1. Alta
        resp = c.post(reverse("signup_talent"), {
            "legal_name": "Elena Marín Soto", "stage_name": "Elena Marín", "email": "elena@test.local",
            "password": PASSWORD, "is_adult": "on", "accept_terms": "on",
        })
        self.assertRedirects(resp, reverse("talent_home"), fetch_redirect_response=False)
        user = User.objects.get(email="elena@test.local")
        self.assertIn("/confirmar/", mail.outbox[-1].body)
        # sin confirmar el correo no se entra al área privada
        self.assertRedirects(c.get(reverse("talent_home")), reverse("account"))
        token_url = [w for w in mail.outbox[-1].body.split() if "/confirmar/" in w][0]
        c.get(token_url.replace("http://localhost:8000", ""))
        user.refresh_from_db()
        self.assertTrue(user.email_verified)
        self.assertEqual(c.get(reverse("talent_home")).status_code, 200)
        voice = user.talent.voices.get()

        # 2. Perfil y voz
        from tlv.models import Language, Style
        resp = c.post(reverse("talent_profile"), {
            "p-stage_name": "Elena Marín", "p-legal_name": "Elena Marín Soto", "p-tax_id": "12345678Z", "p-country": "España",
            "v-name": "Elena Marín", "v-gender": "female", "v-age": "adult", "v-base_price": "150",
            "v-languages": [Language.objects.get(code="es").pk], "v-styles": [Style.objects.first().pk],
        })
        self.assertEqual(resp.status_code, 302, resp.content.decode()[:3000] if resp.status_code != 302 else "")

        # 3. Identidad
        from django.core.files.uploadedfile import SimpleUploadedFile
        resp = c.post(reverse("talent_identity"), {"document": SimpleUploadedFile("dni.pdf", b"%PDF-1.4 demo", "application/pdf")})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(TalentProfile.objects.get(user=user).identity_status, "submitted")

        # 4. Muestras (una mala y dos buenas)
        c.post(reverse("talent_samples"), {"action": "upload", "kind": "training", "file": wav_upload("e", "Hola.", "corta.wav")})
        self.assertFalse(voice.samples.get().ok)
        for i in range(2):
            c.post(reverse("talent_samples"), {"action": "upload", "kind": "training", "file": wav_upload("e", READING + f" {i}")})
        self.assertEqual(voice.samples.filter(ok=True).count(), 2)
        self.assertEqual(c.get(reverse("talent_samples")).status_code, 200)

        # 5. Prueba de titularidad
        c.post(reverse("talent_verification"), {"action": "new"})
        ver = voice.verifications.first()
        self.assertContains(c.get(reverse("talent_verification")), ver.phrase.split(".")[0])
        c.post(reverse("talent_verification"), {"recording": wav_upload("e", ver.phrase, "frase.wav")})
        ver.refresh_from_db()
        self.assertEqual(ver.status, "review")           # el motor simulado no transcribe: revisión manual

        # 6. Usos y contrato: 3 categorías autorizadas, firma con el nombre legal
        page = c.get(reverse("talent_contract"))
        self.assertContains(page, "Elena Marín Soto")
        data = {"action": "sign", "signed_name": "Otra Persona", "biometric_consent": "on", "accept_contract": "on",
                "allow-publicidad-digital": "on", "allow-documental": "on", "allow-podcast": "on", "price-documental": "300"}
        c.post(reverse("talent_contract"), data)
        self.assertFalse(Contract.objects.filter(voice=voice).exists())         # el nombre no coincide
        data["signed_name"] = "elena marin soto"
        self.assertRedirects(c.post(reverse("talent_contract"), data), reverse("talent_home"), fetch_redirect_response=False)
        contract = Contract.objects.get(voice=voice)
        self.assertEqual(sum(1 for x in contract.consent_snapshot if x["allowed"]), 3)
        self.assertEqual(len(contract.consent_snapshot), 17)
        self.assertEqual(c.get(reverse("talent_contract_pdf", args=[contract.pk])).content[:4], b"%PDF")

        # 7. Envío a revisión
        c.post(reverse("talent_voice_action", args=["enviar"]))
        voice.refresh_from_db()
        self.assertEqual(voice.status, Voice.Status.IN_REVIEW)

        # 8. Gestión verifica identidad, revisa la titularidad y aprueba (clona)
        c.logout()
        self.login(self.staff)
        self.assertEqual(c.get(reverse("staff_voice", args=[voice.pk])).status_code, 200)
        c.post(reverse("staff_voice", args=[voice.pk]), {"action": "approve"})
        voice.refresh_from_db()
        self.assertEqual(voice.status, Voice.Status.IN_REVIEW)      # sin identidad verificada no se clona
        c.post(reverse("staff_talent", args=[user.talent.pk]), {"action": "verify"})
        self.assertFalse(TalentProfile.objects.get(user=user).identity_document)      # el documento se borra
        c.post(reverse("staff_voice", args=[voice.pk]), {"action": "verification", "result": "ok"})
        c.post(reverse("staff_voice", args=[voice.pk]), {"action": "approve"})
        voice.refresh_from_db()
        self.assertEqual(voice.status, Voice.Status.APPROVED)
        self.assertTrue(voice.external_voice_id)
        self.assertEqual(voice.demo_clips.count(), 3)

        # 9. El locutor escucha y activa; la voz aparece en el catálogo
        c.logout()
        self.assertNotContains(c.get(reverse("catalog")), "Elena Marín")
        self.login(user)
        self.assertEqual(c.get(reverse("talent_listen")).status_code, 200)
        c.post(reverse("talent_voice_action", args=["activar"]))
        c.logout()
        self.assertContains(c.get(reverse("catalog")), "Elena Marín")
        page = c.get(reverse("voice", args=[voice.slug]))
        self.assertContains(page, "Documentales")
        self.assertContains(page, "420.00")                  # su tarifa propia (300) x 1.40 de la categoría
        clip = voice.demo_clips.first()
        audio_resp = c.get(reverse("media_generation", args=[clip.generation_id, "mp3"]))
        self.assertEqual(audio_resp.status_code, 200)
        self.assertEqual(audio_resp["Content-Type"], "audio/mpeg")

    def test_alta_de_cliente_y_validacion(self):
        c = self.client
        resp = c.post(reverse("signup_client"), {
            "company_name": "Agencia Norte", "tax_id": "B12345678", "contact_name": "Pablo", "email": "pablo@test.local",
            "password": PASSWORD, "billing_address": "Gran Vía 1", "postal_code": "28013", "city": "Madrid",
            "country": "España", "accept_terms": "on",
        })
        self.assertRedirects(resp, reverse("client_home"), fetch_redirect_response=False)
        cp = ClientProfile.objects.get(company_name="Agencia Norte")
        self.assertEqual(cp.status, "pending")
        c.logout()
        self.login(self.staff)
        self.assertContains(c.get(reverse("staff_clients")), "Agencia Norte")
        c.post(reverse("staff_clients"), {"client": cp.pk, "action": "verify"})
        cp.refresh_from_db()
        self.assertTrue(cp.verified)

    def test_correo_duplicado_y_clave_debil(self):
        self.make_client(9)
        resp = self.client.post(reverse("signup_talent"), {
            "legal_name": "X Y", "stage_name": "X", "email": "cliente9@test.local", "password": "123",
            "is_adult": "on", "accept_terms": "on"})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["form"].errors["email"] and resp.context["form"].errors["password"])


class LicenseWebTests(TlvTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.talent_user, voice = cls.make_talent(1)
        cls.voice = cls.publish(voice)
        cls.client_user = cls.make_client(1)
        cls.other_client = cls.make_client(2)

    def test_ciclo_completo_de_una_licencia(self):
        c = self.client
        self.login(self.client_user)
        # demo de texto libre
        resp = c.post(reverse("client_demo", args=[self.voice.slug]), {"text": "Hola, esto es una prueba."})
        self.assertTrue(resp.json()["ok"])
        self.assertEqual(c.get(resp.json()["url"]).status_code, 200)
        self.assertFalse(c.post(reverse("client_demo", args=[self.voice.slug]), {"text": "x" * 900}).json()["ok"])
        # presupuesto y solicitud
        cat = self.request_data()["category"]
        q = c.get(reverse("client_quote", args=[self.voice.slug]), {"category": cat.pk, "territory": "eu", "duration_months": 24}).json()
        self.assertEqual(q["net"], "384.00 €")
        form_url = reverse("client_request_new", args=[self.voice.slug])
        self.assertEqual(c.get(form_url).status_code, 200)
        resp = c.post(form_url, {
            "project_name": "Spot de primavera", "category": cat.pk, "purpose": "Anuncio de 20 s", "sector": "Moda",
            "script": "Llega la primavera. Llega tu momento.", "territory": "es", "channels": ["online", "social"],
            "duration_months": 12, "accept": "on"})
        req = LicenseRequest.objects.get(project_name="Spot de primavera")
        self.assertRedirects(resp, reverse("client_request", args=[req.pk]))
        self.assertEqual(str(req.quoted_price), "150.00")
        self.assertIn("locutor1@test.local", mail.outbox[-1].to)

        # el locutor pide información, el cliente responde, el locutor hace contraoferta, el cliente acepta
        c.logout(); self.login(self.talent_user)
        url_t = reverse("talent_request", args=[req.pk])
        self.assertContains(c.get(url_t), "Llega la primavera")
        c.post(url_t, {"action": "info", "note": "¿Para qué marca es?"})
        c.logout(); self.login(self.client_user)
        url_c = reverse("client_request", args=[req.pk])
        c.post(url_c, {"form": "message", "body": "Para Moda Albor."})
        req.refresh_from_db(); self.assertEqual(req.status, "submitted")
        c.logout(); self.login(self.talent_user)
        c.post(url_t, {"action": "approve", "price": "200"})
        req.refresh_from_db(); self.assertEqual(req.status, "counter")
        c.logout(); self.login(self.client_user)
        c.post(url_c, {"form": "counter", "answer": "accept"})
        req.refresh_from_db()
        self.assertEqual(req.status, "approved")
        self.assertEqual(str(req.payment.total), "242.00")           # 200 + 21 % de IVA
        page = c.get(url_c)
        self.assertContains(page, req.payment.reference)
        # todavía no se puede generar
        c.post(url_c, {"form": "take"})
        self.assertEqual(Generation.objects.filter(request=req, kind="final").count(), 0)
        c.post(url_c, {"form": "transfer"})

        # gestión confirma el cobro
        c.logout(); self.login(self.staff)
        self.assertContains(c.get(reverse("staff_payments")), req.payment.reference)
        c.post(reverse("staff_payments"), {"payment": req.payment.pk})
        req.refresh_from_db()
        self.assertEqual(req.status, "active")
        split = req.payment.split
        self.assertEqual((str(split.platform_amount), str(split.talent_amount)), ("40.00", "160.00"))

        # el cliente genera, descarga y obtiene certificado y factura
        c.logout(); self.login(self.client_user)
        self.assertContains(c.get(url_c), "dir_tone")
        c.post(url_c, {"form": "take", "dir_tone": "calm", "dir_speed": "0.9", "dir_boost_sent": "1", "dir_boost": "on"})
        gen = Generation.objects.get(request=req, kind="final")
        self.assertEqual(gen.text, "Llega la primavera. Llega tu momento.")
        self.assertEqual((gen.direction["tone"], gen.direction["speed"], gen.direction["boost"]), ("calm", 0.9, True))
        from tlv.services import insight
        self.assertEqual(insight.stage_of(req)["key"], "generated")
        wav = c.get(reverse("media_generation", args=[gen.pk, "wav"]) + "?descargar=1")
        gen.refresh_from_db()
        self.assertEqual(gen.downloads, 1)
        self.assertEqual(insight.stage_of(req)["key"], "delivered")
        self.assertEqual(wav.status_code, 200)
        self.assertIn("attachment", wav["Content-Disposition"])
        self.assertEqual(b"".join(wav.streaming_content)[:4], b"RIFF")
        ranged = c.get(reverse("media_generation", args=[gen.pk, "mp3"]), HTTP_RANGE="bytes=0-99")
        self.assertEqual((ranged.status_code, ranged["Content-Length"]), (206, "100"))
        self.assertEqual(c.get(reverse("client_certificate", args=[req.pk])).content[:4], b"%PDF")
        self.assertEqual(c.get(reverse("client_invoice", args=[req.pk])).content[:4], b"%PDF")
        self.assertContains(c.get(url_c), req.license.code)

        # verificación pública de la licencia
        c.logout()
        self.assertContains(c.get(reverse("verify_code", args=[req.license.code])), "Empresa 1")
        self.assertContains(c.get(reverse("verify") + "?codigo=LIC-FALSA"), "LIC-FALSA")

        # otro cliente y un anónimo no pueden ver ni descargar nada
        self.assertEqual(c.get(reverse("media_generation", args=[gen.pk, "wav"])).status_code, 404)
        self.login(self.other_client)
        self.assertEqual(c.get(url_c).status_code, 404)
        self.assertEqual(c.get(reverse("media_generation", args=[gen.pk, "mp3"])).status_code, 404)
        self.assertEqual(c.get(reverse("client_certificate", args=[req.pk])).status_code, 404)

        # el locutor ve sus ingresos y puede oír (no descargar en WAV) el audio hecho con su voz
        c.logout(); self.login(self.talent_user)
        self.assertContains(c.get(reverse("talent_earnings")), "160.00")
        self.assertEqual(c.get(reverse("media_generation", args=[gen.pk, "mp3"])).status_code, 200)
        self.assertEqual(c.get(reverse("media_generation", args=[gen.pk, "wav"])).status_code, 404)
        self.assertEqual(c.get(reverse("talent_statement", args=[split.pk])).content[:4], b"%PDF")

        # gestión paga al locutor, y ve la solicitud entregada, su historial y el audio
        c.logout(); self.login(self.staff)
        page = c.get(reverse("staff_request", args=[req.pk]))
        self.assertContains(page, "Audio descargado")
        self.assertContains(page, "Calmado y relajado")
        self.assertContains(page, "Cobro confirmado")
        self.assertContains(c.get(reverse("staff_requests") + "?fase=delivered"), req.code)
        self.assertNotContains(c.get(reverse("staff_requests") + "?fase=requested"), req.code)
        self.assertContains(c.get(reverse("staff_client", args=[req.client_id])), "descargada 1 vez")
        self.assertContains(c.get(reverse("staff_audios") + "?tipo=final"), reverse("media_generation", args=[gen.pk, "mp3"]))
        self.assertEqual(c.get(reverse("media_generation", args=[gen.pk, "mp3"])).status_code, 200)
        gen.refresh_from_db()
        self.assertEqual(gen.downloads, 1)      # escuchar o descargar desde gestión no cuenta como entrega
        c.post(reverse("staff_payouts"), {"split": split.pk, "reference": "TR-1"})
        split.refresh_from_db()
        self.assertEqual(split.talent_payout, "paid")

    def test_rechazo_y_cancelacion(self):
        c = self.client
        req = licenseflow.create_request(self.client_user, self.voice, self.request_data())
        self.login(self.talent_user)
        c.post(reverse("talent_request", args=[req.pk]), {"action": "reject", "note": ""})
        req.refresh_from_db(); self.assertEqual(req.status, "submitted")      # sin motivo no se rechaza
        c.post(reverse("talent_request", args=[req.pk]), {"action": "reject", "note": "No encaja con mi voz."})
        req.refresh_from_db(); self.assertEqual(req.status, "rejected")
        req2 = licenseflow.create_request(self.client_user, self.voice, self.request_data(project_name="Dos"))
        c.logout(); self.login(self.client_user)
        c.post(reverse("client_request", args=[req2.pk]), {"form": "cancel"})
        req2.refresh_from_db(); self.assertEqual(req2.status, "cancelled")

    def test_los_roles_no_se_cruzan(self):
        c = self.client
        self.assertEqual(c.get(reverse("staff_home")).status_code, 302)       # anónimo -> entrar
        self.login(self.client_user)
        for name in ("staff_home", "staff_payments", "talent_home", "talent_earnings"):
            self.assertEqual(c.get(reverse(name)).status_code, 403, name)
        c.logout(); self.login(self.talent_user)
        for name in ("staff_home", "client_home", "staff_ledger"):
            self.assertEqual(c.get(reverse(name)).status_code, 403, name)

    def test_disputa_con_audio_bloqueo_y_resolucion(self):
        c = self.client
        req = self.licensed(self.client_user, self.voice)
        gen = licenseflow.generate_take(req, self.client_user)
        with gen.file_mp3.open("rb") as fh:
            data = fh.read()
        from django.core.files.uploadedfile import SimpleUploadedFile
        # denuncia pública, sin cuenta
        resp = c.post(reverse("report"), {"reporter_email": "vecino@test.local", "description": "Lo he oído en un anuncio que no es de esa marca.",
                                          "url": "https://example.com/anuncio", "evidence": SimpleUploadedFile("anuncio.mp3", data, "audio/mpeg")})
        self.assertEqual(resp.status_code, 200)
        d = Dispute.objects.get()
        self.assertEqual((d.detection["match"], d.generation_id, d.request_id, d.voice_id), ("hash", gen.pk, req.pk, self.voice.pk))
        # gestión bloquea la licencia: el cliente ya no puede generar ni descargar
        self.login(self.staff)
        self.assertEqual(c.get(reverse("staff_dispute", args=[d.pk])).status_code, 200)
        c.post(reverse("staff_dispute", args=[d.pk]), {"action": "blocks", "block_request": "on"})
        c.logout(); self.login(self.client_user)
        self.assertEqual(c.get(reverse("media_generation", args=[gen.pk, "wav"])).status_code, 404)
        c.post(reverse("client_request", args=[req.pk]), {"form": "take"})
        self.assertEqual(req.generations.filter(kind="final").count(), 1)
        # se resuelve sin incumplimiento: se levanta el bloqueo
        c.logout(); self.login(self.staff)
        c.post(reverse("staff_dispute", args=[d.pk]), {"action": "dismiss", "note": "El uso estaba cubierto por la licencia."})
        d.refresh_from_db(); req.refresh_from_db()
        self.assertEqual(d.status, "dismissed")
        self.assertFalse(req.blocked)
        # herramienta de identificación
        resp = c.post(reverse("staff_tools"), {"file": SimpleUploadedFile("a.mp3", data, "audio/mpeg")})
        self.assertEqual(resp.context["result"]["generation"].pk, gen.pk)

    def test_el_locutor_pausa_y_revoca_desde_su_panel(self):
        c = self.client
        self.login(self.talent_user)
        c.post(reverse("talent_voice_action", args=["pausar"]))
        self.voice.refresh_from_db(); self.assertEqual(self.voice.status, "paused")
        c.logout()
        self.assertEqual(c.get(reverse("voice", args=[self.voice.slug])).status_code, 404)
        self.login(self.talent_user)
        c.post(reverse("talent_voice_action", args=["activar"]))
        c.post(reverse("talent_voice_action", args=["revocar"]), {"reason": "cambio de planes"})       # falta confirmar
        self.voice.refresh_from_db(); self.assertEqual(self.voice.status, "active")
        c.post(reverse("talent_voice_action", args=["revocar"]), {"reason": "cambio de planes", "confirm": "on"})
        self.voice.refresh_from_db(); self.assertEqual(self.voice.status, "revoked")


class PagesAndPrivacyTests(TlvTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.talent_user, voice = cls.make_talent(1)
        cls.voice = cls.publish(voice)
        cls.client_user = cls.make_client(1)
        cls.req = cls.licensed(cls.client_user, cls.voice)
        licenseflow.generate_take(cls.req, cls.client_user)

    PUBLIC = ["home", "catalog", "how", "verify", "transparency", "report", "signup", "signup_talent", "signup_client",
              "login", "password_reset"]
    TALENT = ["talent_home", "talent_profile", "talent_identity", "talent_samples", "talent_verification", "talent_contract",
              "talent_listen", "talent_requests", "talent_earnings", "talent_disputes", "notifications", "account"]
    CLIENT = ["client_home", "client_profile", "client_requests", "client_disputes", "notifications", "account"]
    STAFF = ["staff_home", "staff_talents", "staff_clients", "staff_audios", "staff_voices", "staff_requests", "staff_payments", "staff_payouts",
             "staff_settings", "staff_categories", "staff_disputes", "staff_ledger", "staff_reports", "staff_tools", "staff_privacy"]

    def _all_ok(self, names, lang):
        for name in names:
            with translation.override(lang):
                url = reverse(name)
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_todas_las_paginas_cargan_en_espanol_y_en_ingles(self):
        for lang in ("es", "en"):
            def url(name, *args):
                with translation.override(lang):
                    return reverse(name, args=args)

            self.client.logout()
            self._all_ok(self.PUBLIC, lang)
            for page in ("condiciones", "privacidad", "cookies", "ia"):
                self.assertEqual(self.client.get(url("legal", page)).status_code, 200)
            self.assertEqual(self.client.get(url("voice", self.voice.slug)).status_code, 200)
            self.login(self.talent_user); self._all_ok(self.TALENT, lang)
            self.assertEqual(self.client.get(url("talent_request", self.req.pk)).status_code, 200)
            self.login(self.client_user); self._all_ok(self.CLIENT, lang)
            self.assertEqual(self.client.get(url("client_request", self.req.pk)).status_code, 200)
            self.login(self.staff); self._all_ok(self.STAFF, lang)
            for name, pk in (("staff_request", self.req.pk), ("staff_voice", self.voice.pk), ("staff_talent", self.voice.talent_id),
                             ("staff_client", self.req.client_id)):
                self.assertEqual(self.client.get(url(name, pk)).status_code, 200, name)

    def test_la_interfaz_esta_traducida_al_ingles(self):
        html = self.client.get("/en/voces/").content.decode()
        self.assertIn("Voice catalogue", html)
        self.assertNotIn("Catálogo de voces", html)
        self.assertIn("with their signed permission", self.client.get("/en/").content.decode())

    def test_filtros_del_catalogo(self):
        c = self.client
        self.assertContains(c.get(reverse("catalog"), {"idioma": "es", "uso": "publicidad-digital"}), self.voice.name)
        self.assertNotContains(c.get(reverse("catalog"), {"uso": "politica"}), self.voice.name)
        self.assertNotContains(c.get(reverse("catalog"), {"idioma": "fr"}), self.voice.name)
        self.assertNotContains(c.get(reverse("catalog"), {"precio": "50"}), self.voice.name)
        self.assertContains(c.get(reverse("catalog"), {"q": "voz"}), self.voice.name)

    def test_gestion_cambia_comision_y_categorias(self):
        self.login(self.staff)
        from tlv.models import PlatformSettings
        cfg = PlatformSettings.load()
        resp = self.client.get(reverse("staff_settings"))
        data = {k: v for k, v in resp.context["form"].initial.items() if v is not None}
        data.update(platform_fee_percent="30", invoice_proforma="on", require_client_validation="on")
        self.assertEqual(self.client.post(reverse("staff_settings"), data).status_code, 302)
        cfg.refresh_from_db()
        self.assertEqual(str(cfg.platform_fee_percent), "30.00")
        self.assertEqual(self.client.get(reverse("staff_reports") + "?formato=csv")["Content-Type"], "text/csv; charset=utf-8")

    def test_registro_integro_tras_toda_la_actividad(self):
        self.login(self.staff)
        resp = self.client.post(reverse("staff_ledger"))
        self.assertTrue(resp.context["status"]["ok"])
        self.assertGreater(resp.context["status"]["entries"], 8)

    def test_copia_de_datos_y_supresion_de_cuenta(self):
        c = self.client
        self.login(self.talent_user)
        export = json.loads(c.post(reverse("account"), {"action": "export"}).content)
        self.assertEqual(export["cuenta"]["correo"], "locutor1@test.local")
        self.assertEqual(len(export["voces"][0]["consentimientos"]), 17)
        c.post(reverse("account"), {"action": "delete", "confirm": "on"})
        dr = DataRequest.objects.get()
        c.logout(); self.login(self.staff)
        c.post(reverse("staff_privacy"), {"request": dr.pk, "action": "execute"})
        user = User.objects.get(pk=self.talent_user.pk)
        self.assertFalse(user.is_active)
        self.assertNotIn("locutor1", user.email)
        self.assertEqual(Voice.objects.get(pk=self.voice.pk).status, "revoked")
        self.assertEqual(VoiceSample.objects.filter(voice=self.voice).count(), 0)
        self.assertEqual(TalentProfile.objects.get(user=user).legal_name, "(suprimido)")
        self.assertTrue(self.req.payment.invoice.number)          # la factura se conserva

    def test_entrar_y_salir(self):
        c = self.client
        self.assertEqual(c.post(reverse("login"), {"username": "cliente1@test.local", "password": "mala"}).status_code, 200)
        resp = c.post(reverse("login"), {"username": "cliente1@test.local", "password": PASSWORD})
        self.assertRedirects(resp, reverse("dashboard"), fetch_redirect_response=False)
        self.assertRedirects(c.get(reverse("dashboard")), reverse("client_home"))
        c.post(reverse("logout"))
        self.assertEqual(c.get(reverse("client_home")).status_code, 302)


class NoEmailServerTests(TlvTestCase):
    def test_sin_servidor_de_correo_las_cuentas_nuevas_pueden_entrar(self):
        from django.test import override_settings

        with override_settings(TLV_EMAIL_CONFIGURED=False):
            self.client.post(reverse("signup_talent"), {
                "legal_name": "Sin Correo Ruiz", "stage_name": "Sin Correo", "email": "sincorreo@test.local",
                "password": PASSWORD, "is_adult": "on", "accept_terms": "on"})
            self.assertTrue(User.objects.get(email="sincorreo@test.local").email_verified)
            self.assertEqual(self.client.get(reverse("talent_home")).status_code, 200)
            self.client.logout(); self.login(self.staff)
            self.assertContains(self.client.get(reverse("staff_home")), "El correo no está configurado")
