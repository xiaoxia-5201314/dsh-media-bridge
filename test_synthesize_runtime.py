"""Offline integration tests for the real synthesize dispatcher and adapters.

Run: python -B -m unittest -v test_synthesize_runtime
Only synthetic credentials and a temporary configuration/output directory are used.
"""
import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError


ROOT = Path(__file__).resolve().parent
ORDER = ["minimax-plan", "xiaomi", "deepseek", "minimax-payg", "qwen-payg"]
KEYS = ["MEDIA_BRIDGE_MINIMAX_PLAN", "MEDIA_BRIDGE_XIAOMI", "MEDIA_BRIDGE_DEEPSEEK",
        "MEDIA_BRIDGE_MINIMAX_PAYG", "MEDIA_BRIDGE_QWEN_PAYG"]


class Response:
    def __init__(self, content, content_type="application/json"):
        self.content = content
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        return self.content


class SynthesizeRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = Path(self.tmp.name) / "chains.json"
        # clear=True guarantees the production process sees no inherited credentials.
        env = {name: "synthetic-test-token" for name in KEYS}
        env.update(MEDIA_BRIDGE_CHAINS_FILE=str(self.config),
                   MEDIA_BRIDGE_OUT=str(Path(self.tmp.name) / "out"))
        env_patch = patch.dict(os.environ, env, clear=True)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        spec = importlib.util.spec_from_file_location("media_bridge_test", ROOT / "server" / "media_bridge.py")
        self.bridge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.bridge)
        self.calls = []
        self.success = None
        network = patch.object(self.bridge, "urlopen", side_effect=self.fake_urlopen)
        network.start()
        self.addCleanup(network.stop)

    def write_config(self, cfg):
        self.config.write_text(json.dumps(cfg), encoding="utf-8")

    def fake_urlopen(self, req, timeout=None):
        url = req.full_url
        if url == "https://mock.invalid/audio.wav":
            return Response(b"fake-audio", "audio/wav")
        # Test-only base URLs unambiguously identify both MiniMax providers.
        provider = url.split("/")[2].split(".")[0]
        if provider not in ORDER:
            raise AssertionError("Unexpected network URL: " + url)
        payload = json.loads(req.data.decode("utf-8"))
        self.calls.append((provider, payload))
        if provider != self.success:
            raise HTTPError(url, 429, "synthetic limit", {}, io.BytesIO(b"mock rate limit"))
        if provider.startswith("minimax"):
            result = {"base_resp": {"status_code": 0}, "data": {"audio": "0102"}}
        elif provider == "xiaomi":
            result = {"choices": [{"message": {"audio": {"data": base64.b64encode(b"fake").decode()}}}]}
        elif provider == "deepseek":
            return Response(b"fake-wav", "audio/wav")
        else:
            result = {"output": {"audio": {"url": "https://mock.invalid/audio.wav"}}}
        return Response(json.dumps(result).encode())

    def config_for(self, order):
        return {
            "chains": {"synthesize": order},
            "baseUrls": {p: "https://" + p + ".invalid" for p in ORDER},
            "models": {p: {"synthesize": "model-" + p} for p in ORDER},
            "voices": {"minimax-plan": "female-shaonv", "xiaomi": "xiaomi-test-voice",
                       "deepseek": "tide", "minimax-payg": "female-tianmei", "qwen-payg": "qwen-test-voice"},
        }

    def assert_payload(self, provider, payload, voice):
        if provider.startswith("minimax"):
            self.assertEqual(payload["voice_setting"]["voice_id"], voice)
        elif provider == "xiaomi":
            self.assertEqual(payload["audio"]["voice"], voice)
        elif provider == "deepseek":
            self.assertEqual(payload["voice_id"], voice)
            self.assertNotIn("model", payload)  # adapter exposes no TTS model field
        else:
            self.assertEqual(payload["input"]["voice"], voice)
        if provider != "deepseek":
            self.assertEqual(payload["model"], "model-" + provider)

    def test_all_adapters_use_explicit_models_and_provider_voices(self):
        order = ["qwen-payg", "minimax-payg", "deepseek", "xiaomi", "minimax-plan"]
        cfg = self.config_for(order)
        self.write_config(cfg)
        # Each explicit target makes exactly one request; no fallback on failure.
        for target in order:
            with self.subTest(target=target):
                self.calls.clear()
                self.success = target
                result = self.bridge.t_synthesize({"text": "offline test", "provider": target})
                self.assertEqual([p for p, _ in self.calls], [target])
                self.assertEqual(result["provider"], target)
                self.assertEqual(result["voice_used"], cfg["voices"][target])
                self.assertNotIn("fallback_from", result)
                self.assertTrue(Path(result["audio_path"]).is_file())
                for provider, payload in self.calls:
                    self.assert_payload(provider, payload, cfg["voices"][provider])

    def test_failed_target_does_not_retry_another_provider(self):
        order = ["minimax-plan", "xiaomi", "deepseek", "minimax-payg", "qwen-payg"]
        cfg = self.config_for(order)
        self.write_config(cfg)
        self.calls.clear()
        self.success = "xiaomi"
        with self.assertRaises(self.bridge.TargetError) as caught:
            self.bridge.t_synthesize({"text": "offline test", "provider": "minimax-plan"})
        self.assertEqual([p for p, _ in self.calls], ["minimax-plan"])
        self.assertEqual(caught.exception.provider, "minimax-plan")

    def test_next_call_reads_changed_config(self):
        cfg = self.config_for(ORDER)
        self.write_config(cfg)
        self.success = "minimax-plan"
        first = self.bridge.t_synthesize({"text": "first"})
        self.assertEqual((first["provider"], first["voice_used"]), ("minimax-plan", "female-shaonv"))
        self.assertEqual(self.calls[0][1]["model"], "model-minimax-plan")
        cfg["chains"]["synthesize"] = ["xiaomi", "minimax-plan"]
        cfg["voices"]["xiaomi"] = "updated-xiaomi-voice"
        cfg["models"]["xiaomi"]["synthesize"] = "updated-xiaomi-model"
        self.write_config(cfg)
        self.calls.clear()
        self.success = "xiaomi"
        second = self.bridge.t_synthesize({"text": "second"})
        self.assertEqual([p for p, _ in self.calls], ["xiaomi"])
        self.assertEqual((second["provider"], second["voice_used"]), ("xiaomi", "updated-xiaomi-voice"))
        self.assertEqual(self.calls[0][1]["model"], "updated-xiaomi-model")


if __name__ == "__main__":
    unittest.main()
