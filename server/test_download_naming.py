"""download() naming rules: content type, signed URLs and octet-stream audio.

Run: python -B -m unittest -v server.test_download_naming
"""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("mb_download", ROOT / "media_bridge.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

WAV = b"RIFF" + b"\xff\xff\xff\x7f" + b"WAVE" + b"data" + b"\x00" * 8


class FakeResponse:
    def __init__(self, payload, ctype):
        self.payload = payload
        self.headers = {"Content-Type": ctype}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        return self.payload


class DownloadNamingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        out = patch.object(m, "OUT_DIR", Path(self.tmp.name))
        out.start()
        self.addCleanup(out.stop)

    def land(self, url, payload, ctype):
        with patch.object(m, "urlopen", return_value=FakeResponse(payload, ctype)):
            return m.download(url, "tts").name.rsplit(".", 1)[-1]

    def test_signed_wav_url_and_octet_stream_audio_both_become_wav(self):
        self.assertEqual(self.land("https://cdn.example.com/a/audio.wav?Expires=1&Signature=x", WAV, "application/octet-stream"), "wav")
        self.assertEqual(self.land("https://cdn.example.com/a/audio", WAV, "audio/wav"), "wav")

    def test_video_image_and_mp3_still_land_correctly(self):
        self.assertEqual(self.land("https://cdn.example.com/v.mp4?sig=1", b"\x00" * 32, "video/mp4"), "mp4")
        self.assertEqual(self.land("https://cdn.example.com/i.png?sig=1", b"\x89PNG\r\n", "image/png"), "png")
        self.assertEqual(self.land("https://cdn.example.com/a.mp3", b"ID3\x03\x00", "audio/mpeg"), "mp3")

    def test_unknown_payload_stays_bin(self):
        self.assertEqual(self.land("https://cdn.example.com/blob", b"whatever", "application/octet-stream"), "bin")


if __name__ == "__main__":
    unittest.main()
