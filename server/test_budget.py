"""Spend cap: the guard has to stop the request, and the tiers have to be independent.

Everything here is offline; `http_json` is patched, so "no request was sent" is a
meaningful assertion rather than a hope.
"""
import importlib.util
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("mb_budget", ROOT / "media_bridge.py")
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)


class BudgetTests(unittest.TestCase):
    def setUp(self):
        env = {name: "" for name in m.KEY_NAMES}
        env.update({m.KEY_MINIMAX_PAYG: "k", m.KEY_QWEN_PAYG: "k", m.KEY_XIAOMI: "k"})
        self._env = patch.dict(os.environ, env, clear=True)
        self._env.start()
        self.addCleanup(self._env.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["MEDIA_BRIDGE_CHAINS_FILE"] = str(Path(self.tmp.name) / "chains.json")
        out = patch.object(m, "OUT_DIR", Path(self.tmp.name))
        out.start()
        self.addCleanup(out.stop)
        self.budget = {"daily_limit_cny": 10.0}
        cfg = patch.object(m, "runtime_config", side_effect=lambda: {"budget": self.budget})
        cfg.start()
        self.addCleanup(cfg.stop)
        self.sent = []

        def fake(url, **kwargs):
            self.sent.append(url)
            if "images/generations" in url:
                return {"data": [{"url": "https://cdn.example.com/a.jpg", "size": "1024x1024"}]}
            return {"task_id": "job-1", "status": "queued"}

        net = patch.object(m, "http_json", side_effect=fake)
        net.start()
        self.addCleanup(net.stop)

        def land(url, prefix):
            dest = Path(self.tmp.name) / f"{prefix}-{len(self.sent)}.bin"
            dest.write_bytes(b"x" * 64)
            return dest

        dl = patch.object(m, "download", side_effect=land)
        dl.start()
        self.addCleanup(dl.stop)

    def video(self, **extra):
        args = {"prompt": "cat", "provider": "minimax-payg", "model": "MiniMax-H3-Lite",
                "duration": 10, "resolution": "768P"}
        args.update(extra)
        return m.t_generate_video(args)

    # ---- estimation -------------------------------------------------------
    def test_confirmed_prices_are_used_not_the_fallback(self):
        self.assertAlmostEqual(m.estimate_cost("generate_video", "minimax-payg", "MiniMax-H3-Lite",
                                               {"duration": 10, "resolution": "768P"}), 2.6)
        self.assertAlmostEqual(m.estimate_cost("generate_image", "minimax-payg", "MiniMax-H3-Image",
                                               {"area": "1K", "n": 2}), 0.286)

    def test_unpriced_models_fall_back_upwards_not_downwards(self):
        self.assertGreater(m.estimate_cost("generate_video", "qwen-payg", "wan3.0-video", {"duration": 5}), 2.0)
        self.assertEqual(m.estimate_cost("synthesize", "xiaomi", "mimo-v2.5-tts", {"text": "免费"}), 0.0)

    def test_price_overrides_beat_the_table(self):
        self.budget = {"daily_limit_cny": 10.0, "prices_cny": {"minimax-payg:MiniMax-H3-Lite": 0.01}}
        self.assertAlmostEqual(m.estimate_cost("generate_video", "minimax-payg", "MiniMax-H3-Lite",
                                               {"duration": 10}), 0.01)

    # ---- enforcement ------------------------------------------------------
    def test_daily_cap_stops_the_third_call_without_sending_it(self):
        # 2.60 x 2 = 5.20 fits under this; a third would reach 7.80 and be refused
        self.budget = {"daily_limit_cny": 6.0}
        self.video()
        self.video()
        self.assertEqual(len(self.sent), 2)
        with self.assertRaises(m.TargetError) as ctx:
            self.video()
        self.assertEqual(ctx.exception.code, "budget_exhausted")
        self.assertEqual(len(self.sent), 2, "the refused call must not reach the network")
        self.assertIn("今日总额", str(ctx.exception))

    def test_provider_cap_is_independent_of_the_daily_total(self):
        self.budget = {"daily_limit_cny": 100.0, "provider_limit_cny": {"minimax-payg": 1.0}}
        with self.assertRaises(m.TargetError) as ctx:
            self.video()
        self.assertIn("品牌 minimax-payg", str(ctx.exception))
        self.assertEqual(self.sent, [])

    def test_model_cap_accepts_qualified_and_bare_keys(self):
        for table in ({"minimax-payg:MiniMax-H3-Lite": 1.0}, {"MiniMax-H3-Lite": 1.0}):
            with self.subTest(table=table):
                self.budget = {"daily_limit_cny": 100.0, "model_limit_cny": table}
                with self.assertRaises(m.TargetError):
                    self.video()

    def test_zero_closes_a_class_and_negative_means_unlimited(self):
        self.budget = {"daily_limit_cny": 0.0}
        with self.assertRaises(m.TargetError):
            self.video()
        self.budget = {"daily_limit_cny": -1.0}
        for _ in range(4):
            self.video()
        self.assertEqual(len(self.sent), 4)

    def test_free_providers_never_consume_budget(self):
        # MiMo TTS is billed free by the vendor, so a closed daily cap must not stop it
        self.assertEqual(m.estimate_cost("synthesize", "xiaomi", "mimo-v2.5-tts", {"text": "免费" * 50}), 0.0)
        self.assertIsNone(m.budget_block("synthesize", "xiaomi", "mimo-v2.5-tts", 0.0))
        self.budget = {"daily_limit_cny": 0.0}
        self.assertEqual(len(self.sent), 0)
        self.assertEqual(m.budget_record("synthesize", "xiaomi", "mimo-v2.5-tts", 0.0), 0.0)
        self.assertFalse(Path(m.usage_file()).exists(), "a free call must not even open the ledger")

    def test_ledger_accumulates_and_rolls_over_on_a_new_date(self):
        self.video()
        used = m.usage_today()
        self.assertAlmostEqual(used["total"], 2.6)
        self.assertEqual(used["calls"], 1)
        stamp = used.copy()
        stamp["date"] = "2000-01-01"
        Path(m.usage_file()).write_text(json.dumps(stamp), encoding="utf-8")
        self.assertEqual(m.usage_today()["total"], 0.0, "yesterday's spend must not block today")

    def test_result_reports_the_estimate_and_running_total(self):
        result = self.video()
        self.assertAlmostEqual(result["cost_estimate_cny"], 2.6)
        self.assertAlmostEqual(result["budget_used_today_cny"], 2.6)

    def test_an_unwritable_ledger_refuses_to_spend_for_any_user(self):
        # A parent that is a regular file fails the write for root as well, so this
        # tests the guard rather than the filesystem's permission model.
        blocker = Path(self.tmp.name) / "blocker"
        blocker.write_text("not a directory", encoding="utf-8")
        saved = os.environ.get("MEDIA_BRIDGE_CHAINS_FILE")
        os.environ["MEDIA_BRIDGE_CHAINS_FILE"] = str(blocker / "nested" / "chains.json")
        try:
            with self.assertRaises(m.TargetError) as ctx:
                self.video()
            self.assertEqual(ctx.exception.code, "budget_unavailable")
            self.assertEqual(self.sent, [], "nothing may leave the process without a ledger")
        finally:
            if saved is not None:
                os.environ["MEDIA_BRIDGE_CHAINS_FILE"] = saved
            else:
                os.environ.pop("MEDIA_BRIDGE_CHAINS_FILE", None)


if __name__ == "__main__":
    unittest.main()
