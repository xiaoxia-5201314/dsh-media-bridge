"""v0.9 guards: Qwen expressive parameters and the voice-enrollment tool.

Everything here is offline: http_json / http_bytes are patched, so no call leaves
the machine and nothing bills.
"""
import base64
import io
import importlib.util
import json
import os
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location('mb_v09', ROOT / 'media_bridge.py')
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)  # noqa: E402


def wav_bytes(seconds, rate=8000):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return buffer.getvalue()


class QwenControls(unittest.TestCase):
    def setUp(self):
        env = {name: "" for name in m.KEY_NAMES}
        env.update({m.KEY_QWEN_PLAN: "fake", m.KEY_QWEN_PAYG: "fake", m.KEY_MINIMAX_PLAN: "fake",
                    m.KEY_XIAOMI: "fake"})
        patch.dict(os.environ, env, clear=True).start()
        self.addCleanup(patch.stopall)
        self.sent = []
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patch.object(m, "OUT_DIR", Path(self.tmp.name)).start()

        def fake(url, **kwargs):
            self.sent.append((url, kwargs.get("body") or {}))
            if "SpeechSynthesizer" in url or "multimodal-generation" in url:
                return {"output": {"audio": {"url": "https://mock.invalid/a.wav"}}}
            return {}

        patch.object(m, "http_json", side_effect=fake).start()
        def fake_download(url, prefix):
            dest = Path(self.tmp.name) / (prefix + '.wav')
            dest.write_bytes(b'RIFF' + bytes(8) + b'WAVEfmt ')
            return dest

        patch.object(m, 'download', side_effect=fake_download).start()

    def args(self, **extra):
        base = {"text": "你好", "provider": "qwen-plan"}
        base.update(extra)
        return m.t_synthesize(base)

    def body(self):
        return self.sent[-1][1]["input"]

    def test_single_language_hint_is_forwarded(self):
        self.args(language_hints=["zh"])
        self.assertEqual(self.body()["language_hints"], ["zh"])

    def test_more_than_one_language_hint_is_refused_because_only_the_first_is_used(self):
        with self.assertRaises(m.TargetError) as ctx:
            self.args(language_hints=["zh", "en"])
        self.assertEqual(ctx.exception.code, "bad_request")
        self.assertEqual(self.sent, [])

    def test_unknown_language_hint_is_refused(self):
        with self.assertRaises(m.TargetError) as ctx:
            self.args(language_hints=["klingon"])
        self.assertEqual(ctx.exception.code, "bad_request")

    def test_hot_fix_shape_is_validated_then_forwarded(self):
        self.args(hot_fix={"pronunciation": [{"天气": "tian1 qi4"}], "replace": [{"今天": "金天"}]})
        self.assertEqual(self.body()["hot_fix"]["pronunciation"], [{"天气": "tian1 qi4"}])
        for broken in ({"pronunciation": [{"a": 1, "b": 2}]}, {"replace": "今天"}, {"typo": []}):
            with self.subTest(broken=broken):
                with self.assertRaises(m.TargetError) as ctx:
                    self.args(hot_fix=broken)
                self.assertEqual(ctx.exception.code, "bad_request")

    def test_volume_and_rate_land_in_the_request_and_out_of_range_is_refused(self):
        self.args(volume=80, rate=1.5)
        self.assertEqual((self.body()["volume"], self.body()["rate"]), (80, 1.5))
        for bad in ({"volume": 101}, {"volume": -1}, {"rate": 2.5}, {"rate": 0.4}):
            with self.subTest(bad=bad):
                with self.assertRaises(m.TargetError) as ctx:
                    self.args(**bad)
                self.assertEqual(ctx.exception.code, "bad_request")

    def test_parameters_the_other_family_lacks_are_reported_not_silently_dropped(self):
        result = m.t_synthesize({"text": "hi", "provider": "qwen-payg", "model": "qwen3-tts-flash",
                                 "volume": 80, "rate": 1.5})
        self.assertEqual(sorted(result["ignored_params"]), ["rate", "volume"])
        # but a knob that changes what is heard cannot be ignored at all
        with self.assertRaises(m.TargetError) as ctx:
            m.t_synthesize({"text": "hi", "provider": "qwen-payg", "model": "qwen3-tts-flash",
                             "language_hints": ["zh"]})
        self.assertEqual(ctx.exception.code, "model_not_supported")


    def test_sent_parameters_are_echoed_so_the_response_cannot_lie(self):
        result = m.t_synthesize({"text": "你好", "provider": "qwen-plan", "language_hints": ["zh"],
                                 "volume": 70, "rate": 1.4})
        self.assertEqual(result["params_used"]["language_hints"], ["zh"])
        self.assertEqual((result["params_used"]["volume"], result["params_used"]["rate"]), (70, 1.4))
        plain = m.t_synthesize({"text": "你好", "provider": "qwen-plan"})
        self.assertNotIn("params_used", plain)

class MinimaxAsrLimits(unittest.TestCase):
    def setUp(self):
        env = {name: "" for name in m.KEY_NAMES}
        env[m.KEY_MINIMAX_PLAN] = "fake"
        patch.dict(os.environ, env, clear=True).start()
        self.addCleanup(patch.stopall)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patch.object(m, "OUT_DIR", Path(self.tmp.name)).start()
        self.sent = []

        def fake(url, **kwargs):
            self.sent.append((url, kwargs.get("body")))
            return json.dumps({"text": "ok", "duration": 3.0, "n_speakers": 1,
                               "segments": [{"speaker": "S1", "start": 0.1, "end": 2.0, "text": "ok"}]}).encode()

        patch.object(m, "http_bytes", side_effect=fake).start()

    def write(self, name, data):
        path = Path(self.tmp.name) / name
        path.write_bytes(data)
        return str(path)

    def test_overlong_wav_is_refused_before_upload(self):
        # 501 s at 8 kHz 16-bit mono is 8 MB, so the guard is exercised for real
        path = self.write("long.wav", wav_bytes(501))
        with self.assertRaises(m.TargetError) as ctx:
            m.t_transcribe({"audio_path": path, "provider": "minimax-plan"})
        self.assertEqual(ctx.exception.code, "bad_request")
        self.assertEqual(self.sent, [])

    def test_bare_pcm_is_refused_but_a_real_container_is_uploaded(self):
        with self.assertRaises(m.TargetError) as ctx:
            m.t_transcribe({"audio_path": self.write("raw.pcm", b"\x00" * 3200), "provider": "minimax-plan"})
        self.assertEqual(ctx.exception.code, "bad_request")
        result = m.t_transcribe({"audio_path": self.write("ok.wav", wav_bytes(2)), "provider": "minimax-plan", "response_format": "verbose_json"})
        self.assertEqual(result["text"], "ok")
        self.assertEqual(result["n_speakers"], 1)
        url, body = self.sent[-1]
        self.assertIn("speech_to_text", url)
        self.assertIn(b'name="model"', body)
        self.assertIn(b'name="response_format"', body)
        self.assertIn(b'name="file"', body)

    def test_subtitle_format_is_saved_as_a_file(self):
        with patch.object(m, "http_bytes", return_value=b"1\n00:00:00,000 --> 00:00:02,000\nhi\n"):
            result = m.t_transcribe({"audio_path": self.write("ok.wav", wav_bytes(2)),
                                     "provider": "minimax-plan", "response_format": "srt"})
        self.assertTrue(Path(result["subtitle_path"]).read_bytes().startswith(b"1\n"))
        self.assertTrue(result["subtitle_path"].endswith(".srt"))


class VoiceManage(unittest.TestCase):
    def setUp(self):
        env = {name: "" for name in m.KEY_NAMES}
        env[m.KEY_QWEN_PLAN] = "fake"
        patch.dict(os.environ, env, clear=True).start()
        self.addCleanup(patch.stopall)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patch.object(m, "OUT_DIR", Path(self.tmp.name)).start()
        self.bodies = []

        def fake(url, **kwargs):
            body = kwargs.get("body") or {}
            self.bodies.append((url, body))
            action = (body.get("input") or {}).get("action")
            if action == "query_voice":
                return {"output": {"status": "OK"}}
            return {"output": {"voice_id": "dsh-voice-1", "preview_audio": {"data": "UklGRiA="}}}

        patch.object(m, "http_json", side_effect=fake).start()

    def test_clone_requires_a_target_model_and_a_prefix(self):
        for bad in ({"action": "create_clone"}, {"action": "create_clone", "target_model": "qwen-audio-3.0-tts-plus"},
                    {"action": "create_clone", "prefix": "p"}):
            with self.subTest(bad=bad):
                with self.assertRaises(m.TargetError) as ctx:
                    m.t_voice_manage(dict(bad, provider="qwen-plan"))
                self.assertEqual(ctx.exception.code, "bad_request")

    def test_sample_must_be_a_supported_container(self):
        path = Path(self.tmp.name) / "sample.flac"
        path.write_bytes(b"fLaC....")
        with self.assertRaises(m.TargetError) as ctx:
            m.t_voice_manage({"action": "create_clone", "provider": "qwen-plan",
                              "target_model": "qwen-audio-3.0-tts-plus", "prefix": "mine",
                              "audio_path": str(path)})
        self.assertEqual(ctx.exception.code, "bad_request")

    def test_too_long_or_too_low_sample_rate_is_refused(self):
        long = Path(self.tmp.name) / "long.wav"
        long.write_bytes(wav_bytes(61))
        with self.assertRaises(m.TargetError) as ctx:
            m.t_voice_manage({"action": "create_clone", "provider": "qwen-plan",
                              "target_model": "qwen-audio-3.0-tts-plus", "prefix": "mine",
                              "audio_path": str(long)})
        self.assertEqual(ctx.exception.code, "bad_request")
        low = Path(self.tmp.name) / "low.wav"
        low.write_bytes(wav_bytes(12, rate=8000))
        with self.assertRaises(m.TargetError) as ctx:
            m.t_voice_manage({"action": "create_clone", "provider": "qwen-plan",
                              "target_model": "qwen-audio-3.0-tts-plus", "prefix": "mine",
                              "audio_path": str(low)})
        self.assertIn("16000", str(ctx.exception))

    def test_clone_posts_a_data_uri_and_polls_until_the_voice_is_ready(self):
        sample = Path(self.tmp.name) / "ok.wav"
        sample.write_bytes(wav_bytes(12, rate=16000))
        result = m.t_voice_manage({"action": "create_clone", "provider": "qwen-plan",
                                   "target_model": "qwen-audio-3.0-tts-plus", "prefix": "mine",
                                   "audio_path": str(sample)})
        self.assertEqual(result["voice_id"], "dsh-voice-1")
        self.assertEqual(result["status"], "OK")
        create = self.bodies[0][1]
        self.assertEqual(create["model"], "voice-enrollment")
        self.assertEqual(create["input"]["target_model"], "qwen-audio-3.0-tts-plus")
        self.assertTrue(create["input"]["audio"].startswith("data:audio/wav;base64,"))
        self.b64decode_ok(create["input"]["audio"])
        self.assertEqual(self.bodies[1][1]["input"]["action"], "query_voice")

    def b64decode_ok(self, uri):
        base64.b64decode(uri.split(",", 1)[1])

    def test_design_validates_prompt_and_preview_lengths_and_saves_the_preview(self):
        base = {"action": "create_design", "provider": "qwen-plan",
                "target_model": "qwen-audio-3.0-tts-plus", "prefix": "announcer"}
        with self.assertRaises(m.TargetError):
            m.t_voice_manage(dict(base, voice_prompt="沉稳男声", preview_text="太短"))
        with self.assertRaises(m.TargetError):
            m.t_voice_manage(dict(base, voice_prompt="x" * 501, preview_text="各位听众朋友，大家好，欢迎收听晚间新闻。"))
        with self.assertRaises(m.TargetError):
            m.t_voice_manage(dict(base, voice_prompt="沉稳男声", preview_text="大家好。", language_hints=["zh", "en"]))
        result = m.t_voice_manage(dict(base, voice_prompt="沉稳的中年男性播音员，音色低沉浑厚。",
                                       preview_text="各位听众朋友，大家好，欢迎收听晚间新闻。",
                                       language_hints=["zh"]))
        self.assertEqual(Path(result["preview_path"]).suffix, ".wav")
        body = self.bodies[0][1]["input"]
        self.assertEqual(body["voice_prompt"], "沉稳的中年男性播音员，音色低沉浑厚。")
        self.assertEqual(body["language_hints"], ["zh"])

    def test_minimax_cloning_is_not_offered_and_says_why(self):
        with self.assertRaises(m.TargetError) as ctx:
            m.t_voice_manage({"action": "create_clone", "provider": "minimax-plan", "prefix": "p",
                              "target_model": "speech-2.8-hd"})
        self.assertEqual(ctx.exception.code, "unsupported_provider")
        self.assertIn("9.9", str(ctx.exception))

    def test_tool_is_registered_and_listed(self):
        names = [tool["name"] for tool in m.TOOLS]
        self.assertIn("voice_manage", names)
        listed = m.handle({"method": "tools/list"})["result"]["tools"]
        self.assertIn("voice_manage", [tool["name"] for tool in listed])


    def test_plan_tier_says_so_instead_of_an_anonymous_404(self):
        def boom(url, **kwargs):
            raise RuntimeError('qwen-plan HTTP 404: {"code":"InvalidParameter","message":"Model not exist."}')

        with patch.object(m, "http_json", side_effect=boom):
            with self.assertRaises(m.TargetError) as ctx:
                m.t_voice_manage({"action": "create_design", "provider": "qwen-plan",
                                  "target_model": "qwen-audio-3.0-tts-plus", "prefix": "p",
                                  "voice_prompt": "沉稳男声",
                                  "preview_text": "各位听众朋友晚上好，欢迎收听晚间新闻。"})
        self.assertEqual(ctx.exception.code, "provider_mismatch")
        self.assertEqual(ctx.exception.provider, "qwen-plan")
        self.assertIn("qwen-payg", str(ctx.exception))

    def test_list_and_delete_send_the_documented_actions(self):
        seen = []

        def fake(url, **kwargs):
            seen.append(kwargs.get("body") or {})
            return {"output": {"voice_list": [{"voice_id": "v1", "status": "OK",
                                               "gmt_create": "2026-11-17 22:56:55"}], "count": 1}}

        with patch.object(m, "http_json", side_effect=fake):
            listed = m.t_voice_manage({"action": "list", "provider": "qwen-plan",
                                       "prefix": "mine", "page_size": 5})
            gone = m.t_voice_manage({"action": "delete", "provider": "qwen-plan", "voice_id": "v1"})
        self.assertEqual(seen[0]["input"]["action"], "list_voice")
        self.assertEqual((seen[0]["input"]["page_size"], seen[0]["input"]["prefix"]), (5, "mine"))
        self.assertEqual(listed["voices"][0]["voice_id"], "v1")
        self.assertEqual(seen[1]["input"]["action"], "delete_voice")
        self.assertEqual(gone["deleted"], "v1")

    def test_list_and_delete_are_not_gated_behind_the_create_checks(self):
        with patch.object(m, "http_json", side_effect=lambda url, **kw: {"output": {"voice_list": []}}):
            self.assertEqual(m.t_voice_manage({"action": "list", "provider": "qwen-plan"})["count"], 0)
        with self.assertRaises(m.TargetError) as ctx:
            m.t_voice_manage({"action": "delete", "provider": "qwen-plan"})
        self.assertEqual(ctx.exception.code, "bad_request")

    def test_page_size_is_validated_before_the_request(self):
        sent = []
        with patch.object(m, "http_json", side_effect=lambda url, **kw: sent.append(1) or {"output": {}}):
            with self.assertRaises(m.TargetError) as ctx:
                m.t_voice_manage({"action": "list", "provider": "qwen-plan", "page_size": 5000})
        self.assertEqual(ctx.exception.code, "bad_request")
        self.assertEqual(sent, [])

if __name__ == "__main__":
    unittest.main()
