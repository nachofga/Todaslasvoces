"""El conector de ElevenLabs, probado contra respuestas simuladas (sin gastar créditos)."""
import json
from unittest import mock

from django.test import SimpleTestCase, override_settings

from tlv.services.engines import ElevenLabsEngine, EngineError, get_engine


class FakeResponse:
    def __init__(self, status=200, body=None, content=b""):
        self.status_code, self._body, self.content = status, body, content
        self.text = json.dumps(body) if body is not None else ""

    def json(self):
        if self._body is None:
            raise ValueError
        return self._body


@override_settings(ELEVENLABS_API_KEY="sk_test", TLV_VOICE_ENGINE="elevenlabs", ELEVENLABS_BASE_URL="https://api.elevenlabs.io",
                   ELEVENLABS_MODEL_ID="eleven_multilingual_v2", ELEVENLABS_OUTPUT_FORMAT="mp3_44100_128")
class ElevenLabsTests(SimpleTestCase):
    def test_clonar_envia_las_muestras_y_devuelve_el_identificador(self):
        with mock.patch("tlv.services.engines.requests.request") as req:
            req.return_value = FakeResponse(body={"voice_id": "abc123", "requires_verification": False})
            out = get_engine().clone("Voz 1", [("m1.wav", b"RIFF1", "audio/wav"), ("m2.wav", b"RIFF2", "audio/wav")],
                                     description="d", labels={"language": "es"})
        self.assertEqual(out["voice_id"], "abc123")
        method, url = req.call_args.args
        kw = req.call_args.kwargs
        self.assertEqual((method, url), ("POST", "https://api.elevenlabs.io/v1/voices/add"))
        self.assertEqual(kw["headers"]["xi-api-key"], "sk_test")
        self.assertEqual([f[0] for f in kw["files"]], ["files", "files"])
        self.assertEqual(kw["data"]["name"], "Voz 1")
        self.assertEqual(json.loads(kw["data"]["labels"]), {"language": "es"})

    def test_generar_audio(self):
        with mock.patch("tlv.services.engines.requests.request") as req:
            req.return_value = FakeResponse(content=b"ID3audio")
            audio = get_engine().synthesize("abc123", "Hola mundo", language="es")
        self.assertEqual(audio, b"ID3audio")
        kw = req.call_args.kwargs
        self.assertTrue(req.call_args.args[1].endswith("/v1/text-to-speech/abc123"))
        self.assertEqual(kw["params"], {"output_format": "mp3_44100_128"})
        self.assertEqual(kw["json"], {
            "text": "Hola mundo", "model_id": "eleven_multilingual_v2",
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75, "speed": 1.0, "style": 0.0, "use_speaker_boost": True},
        })

    @override_settings(ELEVENLABS_EXPRESSIVE_MODEL="eleven_v3")
    def test_un_tono_usa_el_modelo_expresivo_y_marca_cada_parrafo(self):
        direction = {"tone": "excited", "speed": 1.1, "stability": 0.3, "similarity": 0.8, "style": 0.9, "boost": False}
        with mock.patch("tlv.services.engines.requests.request") as req:
            req.return_value = FakeResponse(content=b"ID3audio")
            engine = get_engine()
            engine.synthesize("abc123", "Primer párrafo.\n\nSegundo párrafo.", language="es", direction=direction)
        body = req.call_args.kwargs["json"]
        self.assertEqual(body["model_id"], "eleven_v3")
        self.assertEqual(body["text"], "[excited] Primer párrafo.\n\n[excited] Segundo párrafo.")
        self.assertEqual(body["language_code"], "es")
        # el modelo expresivo no admite estilo ni realce: no se envían
        self.assertEqual(body["voice_settings"], {"stability": 0.3, "similarity_boost": 0.8, "speed": 1.1})
        self.assertEqual(engine.last_model, "eleven_v3")

    def test_los_ajustes_fuera_de_rango_se_corrigen(self):
        from tlv.services import direction

        d = direction.clean({"dir_tone": "inventado", "dir_speed": "3", "dir_stability": "-1", "dir_style": "abc",
                             "dir_boost_sent": "1"}, "dir_")
        self.assertEqual(d, {"tone": "natural", "speed": 1.2, "stability": 0.0, "similarity": 0.75, "style": 0.0, "boost": False})
        self.assertEqual(direction.clean(None), direction.DEFAULT)
        self.assertEqual(direction.describe({"tone": "calm", "speed": 0.9}), "Calmado y relajado · velocidad 0.90")

    def test_transcribir_borrar_y_estado(self):
        with mock.patch("tlv.services.engines.requests.request") as req:
            req.return_value = FakeResponse(body={"text": "hola", "character_count": 10, "character_limit": 100, "tier": "starter"})
            engine = get_engine()
            self.assertEqual(engine.transcribe("f.wav", b"x", "es"), "hola")
            self.assertEqual(req.call_args.kwargs["data"], {"model_id": "scribe_v2", "language_code": "es"})
            self.assertTrue(engine.delete("abc123"))
            self.assertEqual(req.call_args.args[:2], ("DELETE", "https://api.elevenlabs.io/v1/voices/abc123"))
            self.assertEqual(engine.status()["tier"], "starter")

    def test_los_errores_del_proveedor_se_explican(self):
        with mock.patch("tlv.services.engines.requests.request") as req:
            req.return_value = FakeResponse(401, {"detail": {"status": "invalid_api_key", "message": "Invalid API key"}})
            with self.assertRaisesMessage(EngineError, "La clave de API no es válida"):
                get_engine().synthesize("v", "hola")
            req.return_value = FakeResponse(422, {"detail": [{"msg": "field required", "loc": ["body", "text"]}]})
            with self.assertRaisesMessage(EngineError, "field required"):
                get_engine().synthesize("v", "")

    @override_settings(ELEVENLABS_API_KEY="")
    def test_sin_clave_avisa(self):
        with self.assertRaises(EngineError):
            ElevenLabsEngine()


class MailApiTests(SimpleTestCase):
    def _send(self):
        from django.core.mail import EmailMultiAlternatives

        msg = EmailMultiAlternatives("Asunto", "Texto", "Todas las Voces <hola@example.com>", ["ana@example.com"])
        msg.attach_alternative("<p>Texto</p>", "text/html")
        return msg.send()

    @override_settings(EMAIL_BACKEND="tlv.mail.ApiEmailBackend", TLV_EMAIL_PROVIDER="brevo", BREVO_API_KEY="xkeysib-test", EMAIL_TIMEOUT=5)
    def test_brevo(self):
        with mock.patch("tlv.mail.requests.post") as post:
            post.return_value = FakeResponse(201, {"messageId": "1"})
            self.assertEqual(self._send(), 1)
        self.assertEqual(post.call_args.args[0], "https://api.brevo.com/v3/smtp/email")
        self.assertEqual(post.call_args.kwargs["headers"]["api-key"], "xkeysib-test")
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["sender"], {"email": "hola@example.com", "name": "Todas las Voces"})
        self.assertEqual((body["to"], body["subject"], body["htmlContent"]), ([{"email": "ana@example.com"}], "Asunto", "<p>Texto</p>"))

    @override_settings(EMAIL_BACKEND="tlv.mail.ApiEmailBackend", TLV_EMAIL_PROVIDER="resend", RESEND_API_KEY="re_test", EMAIL_TIMEOUT=5)
    def test_resend_y_error_del_proveedor(self):
        from tlv.mail import MailError

        with mock.patch("tlv.mail.requests.post") as post:
            post.return_value = FakeResponse(200, {"id": "1"})
            self.assertEqual(self._send(), 1)
            self.assertEqual(post.call_args.kwargs["headers"], {"Authorization": "Bearer re_test"})
            self.assertEqual(post.call_args.kwargs["json"]["to"], ["ana@example.com"])
            post.return_value = FakeResponse(401, {"message": "clave no válida"})
            with self.assertRaises(MailError):
                self._send()
