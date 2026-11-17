"""Payload shapes the live probes demanded, checked offline.

voicedesign rejected audio.voice, voiceclone demanded a base64 data URL, and the
Qwen families answer on different routes - each rule here mirrors a real error we got.

Run: python -B -m unittest -v server.test_tts_variants
"""
import base64
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("mb_variants", ROOT / "media_bridge.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class FakeResponse:
    def __init__(self, payload, ctype="application/json"):
        self.payload = json.dumps(payload).encode()
        self.headers = {"Content-Type": ctype}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        return self.payload


class FakeBinary(FakeResponse):
    """Stand-in for the audio file the qwen routes hand back as a URL."""

    def __init__(self, payload, ctype):
        self.payload = payload
        self.headers = {"Content-Type": ctype}


class TtsVariantTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        out = patch.object(m, "OUT_DIR", Path(self.tmp.name))
        out.start()
        self.addCleanup(out.stop)
        env = {name: "" for name in m.KEY_NAMES}
        env.update({m.KEY_XIAOMI: "fake", m.KEY_QWEN_PLAN: "fake", m.KEY_QWEN_PAYG: "fake",
                    m.KEY_MINIMAX_PLAN: "fake", m.KEY_DEEPSEEK: "fake"})
        self.env = patch.dict(os.environ, env, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.config = patch.object(m, "runtime_config", return_value={})
        self.config.start()
        self.addCleanup(self.config.stop)
        self.sent = []
        audio = base64.b64encode(b"fake-wav").decode()
        self.network = patch.object(m, "urlopen", side_effect=self.capture(audio))
        self.network.start()
        self.addCleanup(self.network.stop)

    def capture(self, audio_b64):
        def fake(req, timeout=None):
            # only recorded POSTs matter: the audio download is a plain GET
            if not req.data:
                return FakeBinary(b"RIFF\x04\x00\x00\x00WAVE", "audio/wav")
            body = json.loads(req.data.decode("utf-8"))
            self.sent.append((req.full_url, body))
            if "chat/completions" in req.full_url:
                return FakeResponse({"choices": [{"message": {"audio": {"data": audio_b64}}}]})
            if "SpeechSynthesizer" in req.full_url or "multimodal-generation" in req.full_url:
                return FakeResponse({"output": {"audio": {"url": "https://mock.invalid/a.wav"}}})
            # the qwen families hand back a URL, so the download needs a typed body
            return FakeResponse({"base_resp": {"status_code": 0}, "data": {"audio": "00ff"}})
        return fake

    def test_voicedesign_puts_the_description_in_the_user_turn_and_drops_audio_voice(self):
        result = m.t_synthesize({"text": "你好", "provider": "xiaomi",
                                 "model": "mimo-v2.5-tts-voicedesign",
                                 "voice_description": "低沉温和的中年男声，语速偏慢"})
        url, body = self.sent[0]
        self.assertEqual(body["model"], "mimo-v2.5-tts-voicedesign")
        self.assertNotIn("voice", body["audio"])
        self.assertEqual(body["messages"][0]["content"], "低沉温和的中年男声，语速偏慢")
        self.assertEqual(body["messages"][1]["content"], "你好")
        self.assertEqual(result["voice_used"], "described")

    def test_voicedesign_without_a_description_fails_before_any_request(self):
        with self.assertRaises(m.TargetError) as caught:
            m.t_synthesize({"text": "你好", "provider": "xiaomi", "model": "mimo-v2.5-tts-voicedesign"})
        self.assertEqual(caught.exception.code, "bad_request")
        self.assertEqual(self.sent, [])

    def test_voiceclone_sends_a_base64_data_url_of_the_local_sample(self):
        sample = Path(self.tmp.name) / "reference.wav"
        sample.write_bytes(b"RIFF\x04\x00\x00\x00WAVEdata")
        m.t_synthesize({"text": "你好", "provider": "xiaomi", "model": "mimo-v2.5-tts-voiceclone",
                        "voice_reference_path": str(sample)})
        _, body = self.sent[0]
        self.assertTrue(body["audio"]["voice"].startswith("data:audio/wav;base64,"))
        self.assertEqual(base64.b64decode(body["audio"]["voice"].split(",", 1)[1]), sample.read_bytes())

    def test_voiceclone_rejects_missing_unreadable_or_oversized_samples(self):
        for bad in ({}, {"voice_reference_path": str(Path(self.tmp.name) / "nope.wav")}):
            with self.subTest(bad=bad):
                with self.assertRaises(m.TargetError) as caught:
                    m.t_synthesize({"text": "hi", "provider": "xiaomi",
                                    "model": "mimo-v2.5-tts-voiceclone", **bad})
                self.assertEqual(caught.exception.code, "bad_request")
        txt = Path(self.tmp.name) / "note.txt"
        txt.write_text("no audio here", encoding="utf-8")
        with self.assertRaises(m.TargetError):
            m.t_synthesize({"text": "hi", "provider": "xiaomi",
                            "model": "mimo-v2.5-tts-voiceclone", "voice_reference_path": str(txt)})
        self.assertEqual([url for url, _ in self.sent], [])

    def test_preset_voice_still_uses_the_preset_id(self):
        m.t_synthesize({"text": "你好", "provider": "xiaomi", "voice": "mimo_default"})
        _, body = self.sent[0]
        self.assertEqual(body["audio"]["voice"], "mimo_default")

    def test_each_qwen_family_defaults_to_its_own_voice(self):
        self.assertEqual(m.qwen_tts_default_voice('qwen-audio-3.0-tts-plus'), 'longanhuan_v3.6')
        self.assertEqual(m.qwen_tts_default_voice('qwen-audio-3.1-tts-flash'), 'longanhuan_v3.1')
        # the 3.0 flagship still has only two system voices of its own
        self.assertEqual(m.qwen_tts_default_voice('qwen-audio-3.0-tts-flash'), 'longanhuan_v3.6')
        self.assertEqual(m.qwen_tts_default_voice('qwen3-tts-flash'), 'Mia')
        self.assertEqual(m.qwen_tts_default_voice('qwen3-tts-instruct-flash'), 'Mia')

    def test_plan_synthesis_uses_the_plan_family_voice_by_default(self):
        m.t_synthesize({"text": "hi", "provider": "qwen-plan"})
        _, body = self.sent[0]
        self.assertEqual(body["input"]["voice"], "longanhuan_v3.6")

    def test_custom_minimax_voice_id_reaches_the_vendor_untouched(self):
        # Cloned and designed MiniMax voices are valid ids that no preset list can
        # contain, so nothing may silently swap them for a default timbre.
        m.t_synthesize({"text": "hi", "provider": "minimax-plan", "voice": "my-cloned-voice-77"})
        _, body = self.sent[0]
        self.assertEqual(body["voice_setting"]["voice_id"], "my-cloned-voice-77")

    def test_deepseek_still_maps_unknown_voices_because_its_list_is_closed(self):
        # DeepSeek documents exactly two voices, so the guard there is honest
        m.t_synthesize({"text": "hi", "provider": "deepseek", "voice": "nonsense"})
        _, body = self.sent[0]
        self.assertEqual(body["voice_id"], "stella")

    def test_qwen_families_take_the_route_matching_their_model(self):
        m.t_synthesize({"text": "hi", "provider": "qwen-plan"})
        m.t_synthesize({"text": "hi", "provider": "qwen-payg", "model": "qwen3-tts-flash"})
        plan_url, plan_body = self.sent[0]
        payg_url, payg_body = self.sent[1]
        self.assertIn("SpeechSynthesizer", plan_url)
        self.assertEqual(plan_body["model"], "qwen-audio-3.0-tts-plus")
        self.assertIn("multimodal-generation", payg_url)
        self.assertEqual(payg_body["model"], "qwen3-tts-flash")
        # nothing optional is sent unless the caller asks for it
        self.assertNotIn("format", plan_body["input"])
        self.assertNotIn("format", payg_body["input"])
        self.assertEqual(list(plan_body["input"]), ["text", "voice"])

    def test_minimax_defaults_to_the_newer_speech_model(self):
        m.t_synthesize({"text": "hi", "provider": "minimax-plan"})
        _, body = self.sent[0]
        self.assertEqual(body["model"], "speech-2.8-hd")

    def test_synthesis_can_now_use_the_plan_tier(self):
        self.assertIn("qwen-plan", m.DISPATCH["synthesize"])
        self.assertIn("qwen-plan", m.DEFAULT_CHAINS["synthesize"])


    def test_cosyvoice_uses_the_speech_synthesizer_route(self):
        # It answers on the same route as Qwen-Audio-TTS; falling through to the
        # qwen3-tts route is what produced the misleading "url error".
        self.assertEqual(m.qwen_tts_route("cosyvoice-v3-flash"),
                         "/api/v1/services/audio/tts/SpeechSynthesizer")
        self.assertEqual(m.qwen_tts_default_voice("cosyvoice-v3-plus"), "longanyang")

    def test_route_matching_requires_every_fragment(self):
        # "tts" alone must not drag an unrelated model onto the audio endpoint
        self.assertEqual(m.qwen_tts_route("some-future-model"),
                         "/api/v1/services/aigc/multimodal-generation/generation")
        self.assertEqual(m.qwen_tts_route("qwen-audio-3.0-tts-plus"),
                         "/api/v1/services/audio/tts/SpeechSynthesizer")


    def test_speak_text_turns_on_ssml_instead_of_reading_the_tags(self):
        m.t_synthesize({"text": "<speak rate=\"2\">你好</speak>", "provider": "qwen-plan"})
        url, body = self.sent[0]
        self.assertIn("SpeechSynthesizer", url)
        self.assertTrue(body["input"]["enable_ssml"])

    def test_ssml_is_refused_rather_than_dropped_for_qwen3_tts(self):
        with self.assertRaises(m.TargetError) as ctx:
            m.t_synthesize({"text": "<speak>你好</speak>", "provider": "qwen-payg", "model": "qwen3-tts-flash"})
        self.assertEqual(ctx.exception.code, "model_not_supported")
        self.assertEqual(self.sent, [])

    def test_optional_audio_parameters_land_in_input(self):
        m.t_synthesize({"text": "hi", "provider": "qwen-plan", "format": "mp3", "sample_rate": 24000})
        _, body = self.sent[0]
        self.assertEqual(body["input"]["format"], "mp3")
        self.assertEqual(body["input"]["sample_rate"], 24000)

    def test_flac_is_rejected_before_the_request_for_qwen(self):
        with self.assertRaises(m.TargetError) as ctx:
            m.t_synthesize({"text": "hi", "provider": "qwen-plan", "format": "flac"})
        self.assertEqual(ctx.exception.code, "bad_request")
        self.assertEqual(self.sent, [])

    def test_a_family_without_the_parameter_reports_it_instead_of_failing(self):
        # format has a schema default, so erroring on it would break calls that work today
        result = m.t_synthesize({"text": "hi", "provider": "qwen-payg", "model": "qwen3-tts-flash", "format": "mp3"})
        self.assertEqual(result["ignored_params"], ["format"])

    def test_instruction_reaches_the_request_and_cosyvoice_length_is_weighted(self):
        m.t_synthesize({"text": "hi", "provider": "qwen-plan", "instruction": "请讲上海话。"})
        _, body = self.sent[0]
        self.assertEqual(body["input"]["instruction"], "请讲上海话。")
        self.assertEqual(m.instruct_weight("汉a，"), 2 + 1 + 1)
        with self.assertRaises(m.TargetError) as ctx:
            m.t_synthesize({"text": "hi", "provider": "qwen-payg", "model": "cosyvoice-v3-flash",
                            "instruction": "汉" * 51})
        self.assertEqual(ctx.exception.code, "bad_request")

    def test_qwen3_tts_refuses_instruction_because_it_has_none(self):
        with self.assertRaises(m.TargetError) as ctx:
            m.t_synthesize({"text": "hi", "provider": "qwen-payg", "model": "qwen3-tts-flash", "instruction": "慢一点"})
        self.assertEqual(ctx.exception.code, "model_not_supported")

    def test_minimax_emotion_and_normalisation_are_forwarded(self):
        m.t_synthesize({"text": "hi", "provider": "minimax-plan", "emotion": "happy",
                        "text_normalization_mode": "quality"})
        _, body = self.sent[0]
        self.assertEqual(body["voice_setting"]["emotion"], "happy")
        self.assertEqual(body["text_normalization_mode"], "quality")

    def test_minimax_rejects_an_unknown_normalisation_mode(self):
        with self.assertRaises(m.TargetError) as ctx:
            m.t_synthesize({"text": "hi", "provider": "minimax-plan", "text_normalization_mode": "llm"})
        self.assertEqual(ctx.exception.code, "bad_request")

    def test_xiaomi_asr_checks_the_payload_it_actually_sends(self):
        load, payload = m.load_audio_arg, m.audio_payload
        try:
            m.load_audio_arg = lambda args: Path("ignored.bin")
            for uri in ("data:audio/ogg;base64,AAAA",
                        "data:audio/wav;base64," + "A" * (m.XIAOMI_ASR_MAX_B64 + 1)):
                m.audio_payload = lambda path, _u=uri: (_u, "wav", 16000)
                with self.assertRaises(m.TargetError) as ctx:
                    m.asr_xiaomi({"audio_path": "x"})
                self.assertEqual(ctx.exception.code, "bad_request")
            self.assertEqual(self.sent, [])
        finally:
            m.load_audio_arg, m.audio_payload = load, payload
    def test_payg_defaults_to_the_31_family_with_a_31_voice(self):
        # Measured: this account's qwen-audio-3.0-tts-plus free quota is exhausted
        # ("仅用免费额度"), while 3.1 answers and supports instruct, emotion tags, SSML
        # and designed voices. Voices are family-bound, so the default voice had to
        # move with the default model or every default call would hit Engine error 411.
        m.t_synthesize({"text": "hi", "provider": "qwen-payg"})
        url, body = self.sent[0]
        self.assertEqual(body["model"], "qwen-audio-3.1-tts-plus")
        self.assertEqual(body["input"]["voice"], "longanhuan_v3.1")
        self.assertIn("SpeechSynthesizer", url)

    def test_plan_tier_refuses_models_it_does_not_serve_before_the_request(self):
        # Both measured today: qwen-plan answers a bare 404 "Model not exist" for
        # 3.1 TTS, 3.1 ASR and CosyVoice. That becomes an actionable code instead.
        load, payload = m.load_audio_arg, m.audio_payload
        try:
            m.load_audio_arg = lambda args, **kw: Path("ignored.wav")
            m.audio_payload = lambda path: ("data:audio/wav;base64,AAAA", "wav", 16000)
            for call, arguments in (
                (m.t_synthesize, {"text": "hi", "provider": "qwen-plan", "model": "qwen-audio-3.1-tts-plus"}),
                (m.t_synthesize, {"text": "hi", "provider": "qwen-plan", "model": "cosyvoice-v3-flash"}),
                (m.t_transcribe, {"audio_path": "a.wav", "provider": "qwen-plan",
                                  "model": "qwen-audio-3.1-asr-flash"}),
            ):
                with self.subTest(model=arguments["model"]):
                    with self.assertRaises(m.TargetError) as ctx:
                        call(arguments)
                    self.assertEqual(ctx.exception.code, "provider_mismatch")
                    self.assertEqual(ctx.exception.provider, "qwen-plan")
            self.assertEqual(self.sent, [])
        finally:
            m.load_audio_arg, m.audio_payload = load, payload


if __name__ == "__main__":
    unittest.main()
