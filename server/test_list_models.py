"""Offline tests for the model-list discovery tool (no network, no real keys).

Run: python -B -m unittest -v server.test_list_models
"""
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
spec = importlib.util.spec_from_file_location("mb_list_models", ROOT / "media_bridge.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        return self.payload


class ListModelsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = {name: "sk-secret-value-123456" for name in m.KEY_NAMES}
        env["MEDIA_BRIDGE_CHAINS_FILE"] = str(Path(self.tmp.name) / "chains.json")
        self.env_patch = patch.dict(os.environ, env, clear=True)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.config = patch.object(m, "runtime_config", return_value={})
        self.config.start()
        self.addCleanup(self.config.stop)
        self.urls = []

    def serve(self, by_suffix):
        """Answer only the listed suffixes; anything else is a 404."""
        def fake(req, timeout=None):
            url = req.full_url
            self.urls.append(url)
            # Longest suffix wins: /compatible-mode/v1/models also ends with /models.
            for suffix in sorted(by_suffix, key=len, reverse=True):
                if url.endswith(suffix):
                    return Response(by_suffix[suffix])
            raise HTTPError(url, 404, "no route", {}, io.BytesIO(b'{"error":"not found"}'))
        return patch.object(m, "urlopen", side_effect=fake)

    def test_openai_and_dashscope_shapes_are_normalised(self):
        openai = {"data": [{"id": "deepseek-v4"}, {"id": "deepseek-chat"}]}
        dashscope = {"output": {"models": [{"id": "qwen-audio-3.0-asr-flash"}, {"model": "wan3.0-video"}]}}
        with self.serve({"/models": openai, "/compatible-mode/v1/models": dashscope}):
            deepseek = m.t_list_models({"provider": "deepseek"})
            self.assertEqual(deepseek["models"], ["deepseek-v4", "deepseek-chat"])
            self.assertTrue(deepseek["endpoint"].endswith("/models"))
            qwen = m.t_list_models({"provider": "qwen-plan"})
            self.assertIn("qwen-audio-3.0-asr-flash", qwen["models"])
            self.assertIn("wan3.0-video", qwen["models"])

    def test_unknown_endpoint_is_reported_and_never_switches_provider(self):
        with self.serve({}):
            with self.assertRaises(m.TargetError) as caught:
                m.t_list_models({"provider": "minimax-plan"})
        self.assertEqual(caught.exception.provider, "minimax-plan")
        self.assertEqual(caught.exception.code, "model_list_unavailable")
        # only MiniMax hosts were probed: listing never falls through to another vendor
        self.assertTrue(all("minimax" in url for url in self.urls), self.urls)

    def test_credential_never_leaks_into_results_or_errors(self):
        with self.serve({}):
            with self.assertRaises(m.TargetError) as caught:
                m.t_list_models({"provider": "xiaomi"})
        self.assertNotIn("sk-secret-value-123456", str(caught.exception))
        self.assertNotIn("sk-secret-value-123456", json.dumps(self.urls))

    def test_capability_suggestions_group_discovered_models(self):
        openai = {"data": [{"id": "mimo-v2.5-asr"}, {"id": "mimo-v2.5-tts"}, {"id": "mimo-vision"}]}
        with self.serve({"/models": openai}):
            result = m.t_list_models({"provider": "xiaomi"})
        self.assertEqual(result["suggested"]["transcribe"], ["mimo-v2.5-asr"])
        self.assertEqual(result["suggested"]["synthesize"], ["mimo-v2.5-tts"])
        self.assertEqual(result["suggested"]["generate_image"], ["mimo-vision"])
        self.assertEqual(result["suggested"]["generate_video"], [])

    def test_provider_argument_is_validated(self):
        for bad in ({"provider": "not-real"}, {"provider": ""}, {"provider": " "}, {"provider": 5}):
            with self.assertRaises(m.TargetError):
                m.t_list_models(bad)

    def test_without_provider_lists_every_configured_provider(self):
        openai = {"data": [{"id": "deepseek-chat"}]}
        with self.serve({"/models": openai}):
            result = m.t_list_models({})
        names = [row["provider"] for row in result["providers"]]
        self.assertIn("deepseek", names)
        self.assertIn("xiaomi", names)
        # a provider without a catalogue route is reported, never used as a fallback
        self.assertTrue(all(row["provider"] not in names for row in result["failed"]), result["failed"])
        self.assertNotIn("endpoint", result["providers"][0])

    def test_missing_credentials_report_without_requesting_anything(self):
        os.environ.pop(m.KEY_XIAOMI)
        with self.serve({}):
            with self.assertRaises(m.TargetError) as caught:
                m.t_list_models({"provider": "xiaomi"})
        self.assertEqual(caught.exception.code, "no_credentials")
        self.assertEqual(self.urls, [])

    def test_tool_schema_exposes_the_capability(self):
        tool = next(t for t in m.TOOLS if t["name"] == "list_models")
        self.assertIn("provider", tool["inputSchema"]["properties"])
        self.assertIsNone(m.TOOL_CAPABILITY["list_models"])
        listed = m.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
        self.assertIn("list_models", [t["name"] for t in listed])


if __name__ == "__main__":
    unittest.main()
