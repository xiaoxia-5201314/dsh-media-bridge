"""DeepSeek ASR pre-flight rules, from the vendor's own limits.

Run: python -B -m unittest -v server.test_deepseek_asr
"""
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("mb_ds_asr", ROOT / "media_bridge.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def wav_bytes(rate=16000, seconds=1, channels=1, width=2):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * (rate * seconds * channels * (width // 2)))
    return buf.getvalue()


class Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()
        self.headers = {"Content-Type": "application/json"}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        return self.payload


class DeepseekAsrTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        env = {name: "" for name in m.KEY_NAMES}
        env[m.KEY_DEEPSEEK] = "fake-key-never-echoed"
        self.env = patch.dict(os.environ, env, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.config = patch.object(m, "runtime_config", return_value={})
        self.config.start()
        self.addCleanup(self.config.stop)
        self.sent = []
        no_ffmpeg = patch.object(m, "convert_with_ffmpeg", return_value=None)
        no_ffmpeg.start()
        self.addCleanup(no_ffmpeg.stop)

    def write(self, name, data):
        path = self.dir / name
        path.write_bytes(data)
        return path

    def network(self):
        def fake(req, timeout=None):
            self.sent.append((req.full_url, req.data))
            return Response({"request_id": "r1", "text": "你好", "duration_ms": 1000})
        patcher = patch.object(m, "urlopen", side_effect=fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_compliant_wav_is_sent_as_is(self):
        self.network()
        path = self.write("good.wav", wav_bytes(rate=24000, seconds=1))
        result = m.t_transcribe({"audio_path": str(path), "provider": "deepseek"})
        self.assertEqual(result["text"], "你好")
        self.assertEqual(result["audio"], {"container": "wav", "sample_rate": 24000, "channels": 1,
                                           "bit_depth": 16, "pcm": True, "seconds": 1.0})
        url, body = self.sent[0]
        self.assertTrue(url.endswith("/v1/tools/asr"))
        self.assertIn(b'name="file"; filename="speech.wav"', body)

    def test_stereo_wav_is_accepted_because_only_the_left_channel_is_used(self):
        self.network()
        path = self.write("stereo.wav", wav_bytes(rate=16000, seconds=1, channels=2))
        self.assertEqual(m.t_transcribe({"audio_path": str(path), "provider": "deepseek"})["audio"]["channels"], 2)

    def test_ogg_opus_passes_through(self):
        self.network()
        path = self.write("voice.ogg", b"OggS\x00\x02" + b"\x00" * 64)
        result = m.t_transcribe({"audio_path": str(path), "provider": "deepseek"})
        self.assertEqual(result["audio"], {"container": "opus"})
        self.assertIn(b'filename="speech.ogg"', self.sent[0][1])

    def test_bad_sample_rate_or_bit_depth_never_reaches_the_vendor(self):
        for name, data in (("11k.wav", wav_bytes(rate=11025)), ("24bit.wav", wav_bytes(width=3))):
            with self.subTest(name=name):
                path = self.write(name, data)
                with self.assertRaises(m.TargetError) as caught:
                    m.t_transcribe({"audio_path": str(path), "provider": "deepseek"})
                self.assertEqual(caught.exception.code, "bad_request")
        self.assertEqual(self.sent, [])

    def test_mp3_without_ffmpeg_fails_before_uploading(self):
        path = self.write("song.mp3", b"ID3\x03\x00" + b"\x00" * 128)
        with self.assertRaises(m.TargetError) as caught:
            m.t_transcribe({"audio_path": str(path), "provider": "deepseek"})
        self.assertIn("ffmpeg", str(caught.exception))
        self.assertEqual(self.sent, [])

    def test_oversize_and_too_long_are_refused_up_front(self):
        big = self.write("big.wav", wav_bytes(rate=8000, seconds=1) + b"\x00" * m.DEEPSEEK_ASR_MAX_BYTES)
        with self.assertRaises(m.TargetError) as caught:
            m.t_transcribe({"audio_path": str(big), "provider": "deepseek"})
        self.assertIn("8 MiB", str(caught.exception))
        long = self.write("long.wav", wav_bytes(rate=8000, seconds=121))
        with self.assertRaises(m.TargetError) as again:
            m.t_transcribe({"audio_path": str(long), "provider": "deepseek"})
        self.assertIn("120", str(again.exception))
        self.assertEqual(self.sent, [])

    def test_busy_and_timeout_are_marked_retryable(self):
        from urllib.error import HTTPError
        for code in (503, 504, 429):
            exc = HTTPError("https://api.deepseek.com/v1/tools/asr", code, "busy", {}, io.BytesIO(b"{}"))
            self.assertIsInstance(m._classify_http(exc, "deepseek"), m.Retryable, code)

    def test_streamed_wav_with_placeholder_sizes_measures_real_duration(self):
        # DeepSeek's own TTS hands back exactly this shape: 0x7FFFFFFF in the RIFF
        # size and a data chunk that is smaller than its declared length.
        self.network()
        real = wav_bytes(rate=24000, seconds=2)
        stamped = bytearray(real)
        stamped[4:8] = (0x7FFFFFFF).to_bytes(4, "little")
        offset = stamped.find(b"data")
        stamped[offset + 4:offset + 8] = (0x7FFFFFFF).to_bytes(4, "little")
        path = self.write("streamed.wav", bytes(stamped))
        result = m.t_transcribe({"audio_path": str(path), "provider": "deepseek"})
        self.assertEqual(result["audio"]["seconds"], 2.0)
        self.assertEqual(result["text"], "你好")

    def test_non_pcm_wav_is_not_mistaken_for_16_bit_pcm(self):
        floatish = bytearray(wav_bytes(rate=16000))
        floatish[20:22] = (3).to_bytes(2, "little")  # fmt audio_format = IEEE float
        path = self.write("float.wav", bytes(floatish))
        with self.assertRaises(m.TargetError) as caught:
            m.t_transcribe({"audio_path": str(path), "provider": "deepseek"})
        self.assertEqual(caught.exception.code, "bad_request")
        self.assertEqual(self.sent, [])

    def test_the_key_never_leaks_into_a_rejection(self):
        path = self.write("nope.mp3", b"ID3\x03\x00")
        with self.assertRaises(m.TargetError) as caught:
            m.t_transcribe({"audio_path": str(path), "provider": "deepseek"})
        self.assertNotIn("fake-key-never-echoed", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
