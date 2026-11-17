"""Offline tests for MiniMax's two image generations (no network, no keys).

H3-Image answers on /v1/images/generations with per-image errors inside a 200,
while image-01 keeps the legacy /v1/image_generation + base_resp envelope.

Run: python -B -m unittest -v server.test_minimax_image
"""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("mb_minimax_image", ROOT / "media_bridge.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class Response:
    def __init__(self, payload, ctype="application/json"):
        self.payload = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.headers = {"Content-Type": ctype}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        return self.payload


class MiniMaxImageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        out = patch.object(m, "OUT_DIR", Path(self.tmp.name))
        out.start()
        self.addCleanup(out.stop)
        env = {name: "" for name in m.KEY_NAMES}
        env[m.KEY_MINIMAX_PAYG] = "fake-key-never-echoed"
        self.env = patch.dict(os.environ, env, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.config = patch.object(m, "runtime_config", return_value={})
        self.config.start()
        self.addCleanup(self.config.stop)
        self.urls = []

    def serve(self, handler):
        def fake(req, timeout=None):
            body = req.data.decode("utf-8") if req.data else None
            self.urls.append((req.full_url, body))
            if req.full_url.startswith("https://cdn.example.com"):
                return Response(b"\x89PNG\r\n", "image/png")
            return Response(handler(req.full_url, json.loads(body or "{}")))
        network = patch.object(m, "urlopen", side_effect=fake)
        network.start()
        self.addCleanup(network.stop)

    def test_h3_lands_on_the_openai_style_endpoint_with_size_only(self):
        self.serve(lambda url, payload: {"data": [{"url": "https://cdn.example.com/a.jpg", "size": "1024x1024"}]})
        result = m.t_generate_image({"prompt": "orange cat", "provider": "minimax-payg",
                                     "model": "MiniMax-H3-Image", "size": "1024x1024"})
        url, body = self.urls[0]
        self.assertTrue(url.endswith("/v1/images/generations"))
        sent = json.loads(body)
        self.assertEqual(sent["model"], "MiniMax-H3-Image")
        self.assertEqual(sent["size"], "1024x1024")
        # the server rejects size together with aspect_ratio/area, so send one or the other
        self.assertNotIn("aspect_ratio", sent)
        self.assertNotIn("area", sent)
        self.assertEqual(len(result["files"]), 1)

    def test_h3_with_an_explicit_ratio_uses_area_instead_of_size(self):
        self.serve(lambda url, payload: {"data": [{"url": "https://cdn.example.com/a.jpg"}]})
        m.t_generate_image({"prompt": "tall", "provider": "minimax-payg", "model": "MiniMax-H3-Image",
                            "size": "auto", "aspect_ratio": "9:16"})
        sent = json.loads(self.urls[0][1])
        self.assertEqual(sent["aspect_ratio"], "9:16")
        self.assertEqual(sent["area"], "1K")
        self.assertNotIn("size", sent)

    def test_h3_reports_per_image_failures_from_a_200_body(self):
        self.serve(lambda url, payload: {"data": [
            {"url": "https://cdn.example.com/ok.jpg"},
            {"error": {"code": "1027", "message": "output image failed content safety check"}}]})
        result = m.t_generate_image({"prompt": "two", "provider": "minimax-payg", "model": "MiniMax-H3-Image"})
        self.assertEqual(result["count"], 1)
        self.assertIn("1027", result["partial_failures"][0])

    def test_h3_all_images_failed_is_an_error_without_the_key(self):
        self.serve(lambda url, payload: {"data": [{"error": {"code": "1026", "message": "prompt failed safety check"}}]})
        with self.assertRaises(Exception) as caught:
            m.t_generate_image({"prompt": "bad", "provider": "minimax-payg", "model": "MiniMax-H3-Image"})
        self.assertNotIn("fake-key-never-echoed", str(caught.exception))

    def test_empty_prompt_is_refused_before_any_request(self):
        # t_generate_image checks the prompt first, so nothing reaches the vendor
        # and nothing can be billed.
        self.serve(lambda url, payload: {})
        with self.assertRaises(RuntimeError):
            m.t_generate_image({"prompt": "   ", "provider": "minimax-payg", "model": "MiniMax-H3-Image"})
        self.assertEqual(self.urls, [])
        # the adapter keeps its own guard for callers that go straight to it
        with self.assertRaises(m.TargetError) as caught:
            m.image_minimax({"prompt": " ", "provider": "minimax-payg", "model": "MiniMax-H3-Image"}, "minimax-payg")
        self.assertEqual(caught.exception.code, "bad_request")
        self.assertEqual(self.urls, [])

    def test_image01_still_uses_the_legacy_envelope(self):
        self.serve(lambda url, payload: {"base_resp": {"status_code": 0},
                                         "data": {"image_urls": ["https://cdn.example.com/legacy.png"]}})
        result = m.t_generate_image({"prompt": "legacy", "provider": "minimax-payg", "model": "image-01"})
        self.assertTrue(self.urls[0][0].endswith("/v1/image_generation"))
        self.assertEqual(json.loads(self.urls[0][1])["model"], "image-01")
        self.assertEqual(len(result["files"]), 1)

    def test_h3_client_timeout_stays_under_the_documented_600s_wait(self):
        # the endpoint is non-idempotent: a client that gives up early is still billed
        self.serve(lambda url, payload: {"data": [{"url": "https://cdn.example.com/a.jpg"}]})
        seen = {}
        real_http_json = m.http_json

        def spy(url, **kwargs):
            seen["timeout"] = kwargs.get("timeout")
            return real_http_json(url, **kwargs)
        with patch.object(m, "http_json", side_effect=spy):
            m.t_generate_image({"prompt": "cat", "provider": "minimax-payg", "model": "MiniMax-H3-Image"})
        self.assertEqual(seen["timeout"], 580)


if __name__ == "__main__":
    unittest.main()
