import importlib.util
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('media_bridge_server_test', Path(__file__).with_name('media_bridge.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class SingleTargetTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {k: '' for k in m.KEY_NAMES})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.config = patch.object(m, 'runtime_config', return_value={})
        self.config.start()
        self.addCleanup(self.config.stop)
        m._jobs.clear()

    def test_model_legacy_and_ordered(self):
        for value, expected in [('old', 'old'), (['first', 'second'], 'first'), ([], 'fallback')]:
            with patch.object(m, 'runtime_config', return_value={'models': {'xiaomi': {'synthesize': value}}}):
                self.assertEqual(m.provider_model('xiaomi', 'synthesize', 'fallback'), expected)

    def test_one_target_on_failure_and_structured_error(self):
        os.environ[m.KEY_MINIMAX_PLAN] = 'fake-key-one'
        os.environ[m.KEY_XIAOMI] = 'fake-key-two'
        calls = []
        def fail(args):
            calls.append(args['model'])
            raise m.Retryable('quota fake-key-one')
        with patch.dict(m.DISPATCH['synthesize'], {'minimax-plan': fail, 'xiaomi': lambda a: self.fail('fallback called')}):
            response = m.handle({'id': 1, 'method': 'tools/call', 'params': {'name': 'synthesize', 'arguments': {'text': 'hello'}}})['result']
        self.assertEqual(calls, ['speech-2.8-hd'])
        self.assertTrue(response['isError'])
        error = json.loads(response['content'][0]['text'])['error']
        self.assertEqual((error['provider'], error['model'], error['retryable']), ('minimax-plan', 'speech-2.8-hd', True))
        self.assertNotIn('fake-key-one', error['message'])

    def test_explicit_and_unsupported(self):
        os.environ[m.KEY_XIAOMI] = 'test-key'
        with patch.dict(m.DISPATCH['synthesize'], {'xiaomi': lambda a: {'sent': a['model']}}):
            result = m.t_synthesize({'text': 'hi', 'provider': 'xiaomi', 'model': 'custom'})
        self.assertEqual((result['provider'], result['model'], result['sent']), ('xiaomi', 'custom', 'custom'))
        with self.assertRaises(m.TargetError) as missing:
            m.t_synthesize({'text': 'hi', 'provider': 'qwen-plan', 'model': 'custom'})
        self.assertEqual(missing.exception.code, 'no_credentials')
        for provider in ('deepseek', 'not-real'):
            with self.assertRaises(m.TargetError):
                m.t_synthesize({'text': 'hi', 'provider': provider, 'model': 'custom'})
        os.environ[m.KEY_DEEPSEEK] = 'test-key'
        with self.assertRaises(m.TargetError):
            m.t_synthesize({'text': 'hi', 'provider': 'deepseek', 'model': 'custom'})

    def test_disabled_and_unsupported_default(self):
        os.environ[m.KEY_QWEN_PLAN] = 'test-key'
        os.environ[m.KEY_XIAOMI] = 'test-key'
        with patch.object(m, 'runtime_config', return_value={'chains': {'synthesize': ['qwen-plan', 'xiaomi']}, 'disabled': ['xiaomi']}):
            with self.assertRaises(m.TargetError):
                m.t_synthesize({'text': 'hi'})
            with self.assertRaises(m.TargetError):
                m.t_synthesize({'text': 'hi', 'provider': 'xiaomi'})

    def test_status_never_probes_others(self):
        os.environ[m.KEY_QWEN_PLAN] = 'test-key'
        os.environ[m.KEY_MINIMAX_PAYG] = 'test-key'
        with patch.object(m, 'job_qwen', side_effect=m.Retryable('rate')) as q, patch.object(m, 'job_minimax_v2') as mm:
            with self.assertRaises(m.TargetError):
                m.t_job_status({'job_id': 'unknown'})
            m._jobs['known'] = {'provider': 'qwen-plan', 'files': []}
            with self.assertRaises(m.TargetError):
                m.t_job_status({'job_id': 'known', 'provider': 'minimax-payg'})
            with self.assertRaises(m.TargetError):
                m.t_job_status({'job_id': 'known'})
            self.assertEqual(q.call_count, 1)
            mm.assert_not_called()


    def test_qwen_tts_route_follows_the_model_family(self):
        multimodal = '/api/v1/services/aigc/multimodal-generation/generation'
        synthesizer = '/api/v1/services/audio/tts/SpeechSynthesizer'
        self.assertEqual(m.qwen_tts_route('qwen3-tts-flash'), multimodal)
        self.assertEqual(m.qwen_tts_route('qwen3-tts-instruct-flash'), multimodal)
        self.assertEqual(m.qwen_tts_route('qwen-audio-3.1-tts-flash'), synthesizer)
        # an unknown name must not land on the route that rejects it
        self.assertEqual(m.qwen_tts_route('brand-new-voice'), multimodal)

    def test_selection_failures_carry_distinct_codes(self):
        os.environ[m.KEY_QWEN_PLAN] = "test-key"
        os.environ[m.KEY_DEEPSEEK] = "test-key"
        cases = (
            # DeepSeek has no image capability at all, so this stays unsupported
            ("t_generate_image", {"prompt": "p", "provider": "deepseek"}, "unsupported_provider"),
            ("t_synthesize", {"text": "hi", "provider": "deepseek", "model": "x"}, "model_not_supported"),
            ("t_synthesize", {"text": "hi", "provider": "minimax-payg"}, "no_credentials"),
            ("t_synthesize", {"text": "hi", "provider": 5}, "bad_request"),
        )
        for name, arguments, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(m.TargetError) as caught:
                    getattr(m, name)(arguments)
                self.assertEqual(caught.exception.code, code)

    def test_minimax_joined_the_transcribe_chain_as_a_selectable_target(self):
        # asr-1.0 arrived in v0.8: up to 500 s and 50 MB with speaker labels, so it
        # must be reachable both explicitly and as a chain tail, without displacing
        # the plan-first default
        for provider in ("minimax-plan", "minimax-payg"):
            self.assertIn(provider, m.DISPATCH["transcribe"])
            self.assertIn(provider, m.DEFAULT_CHAINS["transcribe"])
            self.assertGreater(m.DEFAULT_CHAINS["transcribe"].index(provider),
                               m.DEFAULT_CHAINS["transcribe"].index("qwen-plan"))

    def test_schema(self):
        for cap in ('transcribe', 'synthesize', 'generate_image', 'generate_video'):
            tool = next(t for t in m.TOOLS if t['name'] == cap)
            self.assertTrue({'provider', 'model'} <= tool['inputSchema']['properties'].keys())

    def test_no_credentials_returns_structured_error_and_tool_is_listed(self):
        listed = m.handle({'method': 'tools/list'})['result']['tools']
        self.assertIn('synthesize', [tool['name'] for tool in listed])
        response = m.handle({'id': 9, 'method': 'tools/call', 'params': {'name': 'synthesize', 'arguments': {'text': 'hi'}}})
        self.assertIn('result', response)
        self.assertTrue(response['result']['isError'])
        error = json.loads(response['result']['content'][0]['text'])['error']
        self.assertEqual(error['type'], 'TargetError')
        self.assertIn('code', error)
    def test_every_dispatched_target_has_a_default_model(self):
        # call_chain resolves a target as DEFAULT_MODELS[capability][provider]; a
        # provider that joins a capability without an entry dies with a KeyError that
        # surfaces as upstream_failed with provider=null. That is exactly how asr-1.0
        # failed on its first live call, so the pairing is now asserted for real.
        for capability, table in m.DISPATCH.items():
            for provider in table:
                if provider == "deepseek":
                    continue  # that endpoint takes no model name at all
                with self.subTest(capability=capability, provider=provider):
                    self.assertIn(provider, m.DEFAULT_MODELS[capability])
                    self.assertIn(provider, m.DEFAULT_CHAINS[capability])

if __name__ == '__main__':
    unittest.main()
