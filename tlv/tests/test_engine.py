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
        self.assertEqual(kw["json"], {"text": "Hola mundo", "model_id": "eleven_multilingual_v2"})

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
