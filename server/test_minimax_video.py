"""Offline tests for the two MiniMax video generations (no network, no keys).

V1 (Hailuo) and V2 (H3) differ in request shape AND in how the result is polled,
so the version has to survive from create to query.

Run: python -B -m unittest -v server.test_minimax_video
"""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("mb_minimax_video", ROOT / "media_bridge.py")
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


class MiniMaxVideoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = {name: "" for name in m.KEY_NAMES}
        env[m.KEY_MINIMAX_PAYG] = "fake-key"
        # OUT_DIR is resolved at import time, so patching the environment here
        # would not move it: without this the suite writes into the real output folder.
        out = patch.object(m, "OUT_DIR", Path(self.tmp.name))
        out.start()
        self.addCleanup(out.stop)
        self.env = patch.dict(os.environ, env, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.config = patch.object(m, "runtime_config", return_value={})
        self.config.start()
        self.addCleanup(self.config.stop)
        self.urls = []
        m._jobs.clear()
        self.addCleanup(m._jobs.clear)

    def serve(self, handler):
        def fake(req, timeout=None):
            self.urls.append((req.full_url, req.data.decode("utf-8") if req.data else None))
            payload = handler(req.full_url)
            if req.full_url.endswith("out.mp4"):
                return Response(b"\x00\x00\x00\x18ftypmp42", "video/mp4")
            return Response(payload)
        network = patch.object(m, "urlopen", side_effect=fake)
        network.start()
        self.addCleanup(network.stop)

    def test_hailuo_is_refused_because_the_v1_protocol_is_gone(self):
        # MiniMax moved Hailuo into 历史模型; a stale name must be refused rather than
        # quietly posted to an endpoint we no longer support.
        self.serve(lambda url: {"task_id": "t-v1"})
        with self.assertRaises(m.TargetError) as ctx:
            m.t_generate_video({"prompt": "cat", "provider": "minimax-payg", "model": "MiniMax-Hailuo-02",
                                "duration": 6, "resolution": "768P"})
        self.assertEqual(ctx.exception.code, "model_not_supported")
        self.assertEqual(self.urls, [])

    def test_h3_uses_the_v2_protocol(self):
        self.serve(lambda url: {"task_id": "t-v2"})
        result = m.t_generate_video({"prompt": "cat", "provider": "minimax-payg", "model": "MiniMax-H3",
                                     "resolution": "2K", "duration": 5, "ratio": "9:16"})
        self.assertEqual((result["job_id"], result["state"]), ("t-v2", "queued"))
        url, body = self.urls[0]
        self.assertTrue(url.endswith("/v2/video_generation"))
        payload = json.loads(body)
        self.assertEqual(payload["content"], [{"type": "text", "text": "cat"}])
        self.assertEqual((payload["resolution"], payload["duration"], payload["ratio"]), ("2K", 5, "9:16"))
        self.assertNotIn("version", m._jobs["t-v2"], "only one protocol remains, so no tagging")

    def test_h3_options_are_validated_before_any_request(self):
        cases = (
            ({"model": "MiniMax-H3", "resolution": "480P"}, "resolution"),
            ({"model": "MiniMax-H3-Highspeed", "resolution": "2K"}, "2K not offered"),
            ({"model": "MiniMax-H3", "duration": 3}, "short duration"),
            ({"model": "MiniMax-H3-Max", "duration": 4}, "max needs 5s"),
            ({"model": "MiniMax-H3", "ratio": "5:4"}, "bad ratio"),
            ({"model": "MiniMax-H9"}, "unknown model"),
        )
        for override, why in cases:
            with self.subTest(reason=why):
                args = {"prompt": "cat", "provider": "minimax-payg", "resolution": "768P", "duration": 6}
                args.update(override)
                with self.assertRaises(m.TargetError):
                    m.t_generate_video(args)
        self.assertEqual(self.urls, [])

    def test_h3_poll_reads_task_content_url(self):
        def handler(url):
            if url.endswith("/v2/video_generation"):
                return {"task_id": "job-9"}
            return {"task": {"id": "job-9", "status": "succeeded",
                             "content": {"url": "https://cdn.example.com/out.mp4"}}}
        self.serve(handler)
        m.t_generate_video({"prompt": "cat", "provider": "minimax-payg", "model": "MiniMax-H3"})
        status = m.t_job_status({"job_id": "job-9"})
        self.assertEqual(status["state"], "SUCCEEDED")
        self.assertEqual(status["provider"], "minimax-payg")
        self.assertTrue(status["files"][0].endswith(".mp4"))
        query_url = [url for url, _ in self.urls if "/v2/query/video_generation/" in url]
        self.assertEqual(query_url, ["https://api.minimax.cn/v2/query/video_generation/job-9"])

    def test_h3_failure_surfaces_the_task_error(self):
        def handler(url):
            if url.endswith("/v2/video_generation"):
                return {"task_id": "job-8"}
            return {"task": {"id": "job-8", "status": "failed",
                             "error": {"code": "1026", "message": "video description contains sensitive content"}}}
        self.serve(handler)
        m.t_generate_video({"prompt": "cat", "provider": "minimax-payg", "model": "MiniMax-H3"})
        status = m.t_job_status({"job_id": "job-8"})
        self.assertEqual(status["state"], "FAILED")
        self.assertIn("sensitive content", status["error"])

    def test_tool_schema_offers_h3_models_and_2k(self):
        tool = next(t for t in m.TOOLS if t["name"] == "generate_video")
        properties = tool["inputSchema"]["properties"]
        self.assertIn("MiniMax-H3", properties["model"]["enum"])
        self.assertIn("2K", properties["resolution"]["enum"])



class DefaultModelTests(unittest.TestCase):
    def test_video_default_is_h3_lite(self):
        self.assertEqual(m.DEFAULT_MODELS["generate_video"]["minimax-payg"], "MiniMax-H3-Lite")

    def test_image_defaults_were_refreshed(self):
        self.assertEqual(m.DEFAULT_MODELS["generate_image"]["qwen-payg"], "qwen-image-3.0-pro")
        self.assertEqual(m.DEFAULT_MODELS["generate_image"]["minimax-payg"], "MiniMax-H3-Image")
        tool = next(t for t in m.TOOLS if t["name"] == "generate_image")
        self.assertIn("MiniMax-H3-Image", tool["inputSchema"]["properties"]["model"]["enum"])
        self.assertNotIn("qwen3-tts-flash", tool["inputSchema"]["properties"]["model"]["enum"])


if __name__ == "__main__":
    unittest.main()
