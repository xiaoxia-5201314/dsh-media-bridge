#!/usr/bin/env python3
"""Media bridge — a dependency-free stdio MCP server for DSH.

Each call selects exactly one provider and model; failures are returned without
retrying another target. The configured chain determines default selection.

Secrets are NEVER read from files or arguments. They are injected by the DSH
MCP client as environment variables resolved from ${credential:REF} templates.
Every error string returned to the model is redacted before it leaves.
"""
from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import sys
import time
import uuid
import wave
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

VERSION = "0.9.2"
ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = Path(os.environ.get("MEDIA_BRIDGE_OUT") or (ROOT / "media-out"))

KEY_MINIMAX_PLAN = "MEDIA_BRIDGE_MINIMAX_PLAN"
KEY_QWEN_PLAN = "MEDIA_BRIDGE_QWEN_PLAN"
KEY_XIAOMI = "MEDIA_BRIDGE_XIAOMI"
KEY_DEEPSEEK = "MEDIA_BRIDGE_DEEPSEEK"
KEY_MINIMAX_PAYG = "MEDIA_BRIDGE_MINIMAX_PAYG"
KEY_QWEN_PAYG = "MEDIA_BRIDGE_QWEN_PAYG"
KEY_NAMES = (KEY_MINIMAX_PLAN, KEY_QWEN_PLAN, KEY_XIAOMI,
             KEY_DEEPSEEK, KEY_MINIMAX_PAYG, KEY_QWEN_PAYG)

QWEN_PLAN_BASE = "https://token-plan.maas.qianwenaiapi.com"
QWEN_PAYG_BASE = "https://maas.qianwenaiapi.com"
MINIMAX_BASE = "https://api.minimax.cn"
XIAOMI_BASE = "https://api.xiaomimimo.com/v1"
DEEPSEEK_BASE = "https://api.deepseek.com"

# Each provider's own model catalogue. Listing models costs nothing, and discovery
# never walks to another provider: only the path variants below are tried, all on
# the same host, so a failure stays attributable to that provider.
MODEL_LIST_PATHS = {
    "qwen-plan": ("/compatible-mode/v1/models", "/api/v1/models"),
    "qwen-payg": ("/compatible-mode/v1/models", "/api/v1/models"),
    "minimax-plan": ("/v1/models",),
    "minimax-payg": ("/v1/models",),
    "xiaomi": ("/models",),
    "deepseek": ("/models",),
}
DEFAULT_BASE_BY_PROVIDER = {
    "qwen-plan": QWEN_PLAN_BASE, "qwen-payg": QWEN_PAYG_BASE,
    "minimax-plan": MINIMAX_BASE, "minimax-payg": MINIMAX_BASE,
    "xiaomi": XIAOMI_BASE, "deepseek": DEEPSEEK_BASE,
}
# Name fragments used only to *suggest* where a newly discovered model belongs;
# the user or agent still decides the real order in Settings.
MODEL_CAPABILITY_HINTS = {
    "transcribe": ("asr", "stt", "whisper", "speech-to-text", "audio-understand"),
    "synthesize": ("tts", "voice", "speech-0", "speech-2"),
    # A name is classified by the first matching rule, so image and video never
    # both claim one model: an image name carrying "wan" must not land in video.
    "generate_image": ("image", "vision", "wanx", "diffusion", "text2img"),
    "generate_video": ("video", "i2v", "t2v", "s2v"),
}
# MiniMax and DeepSeek list only their text models, so an empty media suggestion
# means "not listed here", never "unusable".
PARTIAL_CATALOGUES = ("minimax-plan", "minimax-payg", "deepseek")

PROVIDER_KEY = {
    "minimax-plan": KEY_MINIMAX_PLAN,
    "minimax-payg": KEY_MINIMAX_PAYG,
    "qwen-plan": KEY_QWEN_PLAN,
    "qwen-payg": KEY_QWEN_PAYG,
    "xiaomi": KEY_XIAOMI,
    "deepseek": KEY_DEEPSEEK,
}

# plan first, then pay-as-you-go: xiaomi (free) -> deepseek -> minimax -> qwen.
# The Settings page / media_bridge_settings tool can reorder, disable, and
# override these defaults at runtime through the chain file (see runtime_config).
DEFAULT_CHAINS = {
    "transcribe": ("qwen-plan", "xiaomi", "deepseek", "qwen-payg", "minimax-plan", "minimax-payg"),
    "synthesize": ("minimax-plan", "qwen-plan", "xiaomi", "deepseek", "minimax-payg", "qwen-payg"),
    "generate_image": ("minimax-plan", "qwen-plan", "minimax-payg", "qwen-payg"),
    "generate_video": ("qwen-plan", "minimax-payg", "qwen-payg"),
}

# MiniMax: 1041 rate limited, 2056 plan usage cap, 2068 model outside plan.
RETRYABLE_MINIMAX = {1041, 2056, 2068}

MINIMAX_VOICES = (
    "male-qn-qingse", "female-shaonv", "male-qn-jingying", "female-tianmei",
    "presenter_male", "presenter_female", "audiobook_male_1", "audiobook_female_1",
    "Chinese (Mandarin)_Warm_Girl", "Chinese (Mandarin)_Sweet_Lady",
    "Chinese (Mandarin)_Gentleman", "Chinese (Mandarin)_Lyrical_Voice",
    "Wise_Woman", "Calm_Woman", "English_expressive_narrator",
)

_jobs: dict[str, dict] = {}

CONFIG_FILE = os.environ.get("MEDIA_BRIDGE_CHAINS_FILE") or os.path.join(
    os.environ.get("USERPROFILE") or os.path.expanduser("~"), ".dsh", "media-bridge.chains.json")
def runtime_config() -> dict:
    """Read the settings file fresh each call; degrade to defaults on any error."""
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        log(f"config load failed, using defaults: {exc}")
        return {}


def chain_for(capability: str) -> tuple:
    cfg = runtime_config()
    disabled = set(cfg.get("disabled") or [])
    order = [p for p in ((cfg.get("chains") or {}).get(capability) or DEFAULT_CHAINS[capability])
             if p in PROVIDER_KEY]
    for p in DEFAULT_CHAINS[capability]:
        if p not in order:
            order.append(p)
    return tuple(p for p in order if p not in disabled)


def base_for(provider: str, default: str) -> str:
    return (runtime_config().get("baseUrls") or {}).get(provider) or default


def provider_model(provider: str, capability: str, fallback: str, args: dict | None = None) -> str:
    explicit = (args or {}).get("model")
    if explicit:
        return str(explicit)
    configured = ((runtime_config().get("models") or {}).get(provider) or {}).get(capability)
    if isinstance(configured, list):
        configured = next((v for v in configured if isinstance(v, str) and v.strip()), None)
    return configured if isinstance(configured, str) and configured.strip() else fallback


def provider_voice(provider: str, fallback: str) -> str:
    return (runtime_config().get("voices") or {}).get(provider) or fallback




class Retryable(Exception):
    """Upstream is rate-limited or out of quota; never automatically retry."""


def log(msg: str) -> None:
    print(f"[media-bridge] {msg}", file=sys.stderr, flush=True)


def redact(text: str) -> str:
    for name in KEY_NAMES:
        value = os.environ.get(name)
        if value and len(value) >= 6:
            text = text.replace(value, "[redacted]")
    return text


def key(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"未配置凭据 {name}。")
    return value


def has(name: str) -> bool:
    return bool(os.environ.get(name, "").strip())


# ---------------------------------------------------------------- http


def _request(url: str, *, method: str, headers: dict | None = None,
             body: bytes | None = None, bearer: str | None = None) -> Request:
    req = Request(url, data=body, method=method)
    if bearer is not None:
        req.add_header("Authorization", "Bearer " + bearer)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    return req


def _classify_http(exc: HTTPError, provider: str) -> Exception:
    body = exc.read(2000).decode("utf-8", "replace")
    text = f"{provider} HTTP {exc.code}: {body[:600]}"
    if exc.code in (429, 503, 504):
        # 429 too many concurrent, 503 busy, 504 recognition timeout: all documented
        # as retry-later, and only the agent decides whether the retry is worth it.
        return Retryable(text)
    if exc.code == 403 and "insufficient_quota" in body:
        return Retryable(text)
    if provider.startswith("qwen") and any(t in body for t in ("Throttling", "AllocationQuota", "QuotaExceeded")):
        return Retryable(text)
    return RuntimeError(redact(text))


def http_json(url: str, *, provider: str, method: str = "GET", headers: dict | None = None,
              body: dict | None = None, timeout: int = 120, bearer: str | None = None) -> dict:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = _request(url, method=method, headers=headers, body=data, bearer=bearer)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read(16 * 1024 * 1024).decode("utf-8", "replace")
    except HTTPError as exc:
        raise _classify_http(exc, provider) from None
    except URLError as exc:
        raise RuntimeError(redact(f"{provider} 网络请求失败: {exc.reason}")) from None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError(redact(f"{provider} 返回非 JSON: {raw[:400]}")) from None


def http_bytes(url: str, *, provider: str, method: str = "POST", headers: dict | None = None,
               body: bytes | None = None, timeout: int = 180,
               bearer: str | None = None) -> bytes:
    req = _request(url, method=method, headers=headers, body=body, bearer=bearer)
    try:
        with urlopen(req, timeout=timeout) as resp:
            return resp.read(64 * 1024 * 1024)
    except HTTPError as exc:
        raise _classify_http(exc, provider) from None
    except URLError as exc:
        raise RuntimeError(redact(f"{provider} 网络请求失败: {exc.reason}")) from None


def minimax_check(result: dict, provider: str) -> dict:
    status = (result.get("base_resp") or {}).get("status_code")
    if status in RETRYABLE_MINIMAX:
        raise Retryable(f"{provider} {status}: {(result.get('base_resp') or {}).get('status_msg')}")
    if status not in (0, None):
        raise RuntimeError(redact(
            f"{provider} {status}: {json.dumps(result, ensure_ascii=False)[:400]}"))
    return result


def _media_extension(url: str, ctype: str, data: bytes) -> str:
    """Pick an extension from the response type, the URL path, then magic bytes.

    Signed CDN links carry a query string, so the suffix is read from the path only,
    and several hosts return application/octet-stream for audio - hence the sniff.
    """
    path = urlsplit(url).path.lower()
    kind = (ctype or "").lower()
    table = (
        ("png", ("image/png",), (".png",), b"\x89PNG"),
        ("jpg", ("image/jpeg", "image/jpg"), (".jpg", ".jpeg"), b"\xff\xd8\xff"),
        ("webp", ("image/webp",), (".webp",), None),
        ("mp4", ("video/mp4",), (".mp4",), None),
        ("wav", ("audio/wav", "audio/x-wav", "audio/wave", "audio/vnd.wave"), (".wav",), None),
        ("mp3", ("audio/mpeg", "audio/mp3"), (".mp3",), b"ID3"),
        ("flac", ("audio/flac", "audio/x-flac"), (".flac",), b"fLaC"),
        ("ogg", ("audio/ogg", "audio/opus"), (".ogg", ".opus"), b"OggS"),
    )
    for ext, types, suffixes, magic in table:
        if any(fragment in kind for fragment in types) or path.endswith(suffixes):
            return ext
        if magic and data.startswith(magic):
            return ext
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    return "bin"


def download(url: str, prefix: str) -> Path:
    req = Request(url, method="GET")
    try:
        with urlopen(req, timeout=180) as resp:
            data = resp.read(128 * 1024 * 1024)
            ctype = resp.headers.get("Content-Type", "")
    except (HTTPError, URLError) as exc:
        raise RuntimeError(redact(f"下载产物失败: {exc}")) from None
    ext = _media_extension(url, ctype, data)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dest = OUT_DIR / f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.{ext}"
    dest.write_bytes(data)
    return dest


def save_bytes(data: bytes, prefix: str, ext: str) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dest = OUT_DIR / f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.{ext}"
    dest.write_bytes(data)
    return dest


# ---------------------------------------------------------------- audio helpers


# After an ffmpeg pass the bytes are WAV no matter what the file on disk was called,
# and callers put this value into the vendor's `format` field.
MIME_FORMAT = {"audio/wav": "wav", "audio/mpeg": "mp3", "audio/ogg": "ogg"}


def audio_payload(path: Path) -> tuple[str, str, int]:
    """Return (data-URI, format, sample_rate). mp3/wav/ogg pass through;
    anything else needs ffmpeg, which may be absent on this machine."""
    suffix = path.suffix.lower()
    if suffix == ".wav":
        data = path.read_bytes()
        with wave.open(str(path), "rb") as w:
            rate = w.getframerate() or 24000
        mime = "audio/wav"
    elif suffix == ".mp3":
        data, rate, mime = path.read_bytes(), 32000, "audio/mpeg"
    elif suffix in (".opus", ".ogg"):
        data, rate, mime = path.read_bytes(), 16000, "audio/ogg"
    else:
        converted = convert_with_ffmpeg(path)
        if converted is None:
            raise RuntimeError(
                f"暂不支持的音频格式 {suffix}。请转成 mp3 或 16/24 kHz 的 16-bit 单声道 WAV；"
                "本机未安装 ffmpeg，无法自动转码。"
            )
        data = converted.read_bytes()
        with wave.open(str(converted), "rb") as w:
            rate = w.getframerate() or 16000
        converted.unlink(missing_ok=True)
        mime = "audio/wav"
    uri = f"data:{mime};base64," + base64.b64encode(data).decode("ascii")
    return uri, MIME_FORMAT.get(mime, suffix.lstrip(".")), rate


def convert_with_ffmpeg(path: Path) -> Path | None:
    dest = OUT_DIR / f"norm_{uuid.uuid4().hex[:8]}.wav"
    try:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(
            ["ffmpeg", "-y", "-i", str(path), "-ac", "1", "-ar", "16000",
             "-c:a", "pcm_s16le", str(dest)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0 or not dest.exists():
        return None
    return dest


def multipart(filename: str, content: bytes, ctype: str,
              fields: dict | None = None) -> tuple[bytes, str]:
    boundary = "----mediabridge" + uuid.uuid4().hex
    head = b"".join(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
        for key, value in (fields or {}).items()
    )
    head += (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {ctype}\r\n\r\n"
    ).encode()
    return head + content + f"\r\n--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


def load_audio_arg(args: dict, max_bytes: int = 20 * 1024 * 1024) -> Path:
    source = args.get("audio_path")
    if not source:
        raise RuntimeError("需要 audio_path（本地音频文件路径）。")
    path = Path(source).expanduser()
    if not path.is_file():
        raise RuntimeError(f"找不到音频文件: {path}")
    if path.stat().st_size > max_bytes:
        raise RuntimeError(f"音频超过 {max_bytes // 1048576} MiB 上限，请先裁剪或切段。")
    return path


# ---------------------------------------------------------------- provider calls: transcribe


def asr_qwen(args: dict, base: str, provider: str) -> dict:
    path = load_audio_arg(args)
    uri, fmt, rate = audio_payload(path)
    default_model = "qwen-audio-3.0-asr-flash" if provider == "qwen-plan" else "qwen-audio-3.1-asr-flash"
    model = provider_model(provider, "transcribe", default_model, args)
    reject_plan_gap(provider, model, "语音识别")
    payload = {
        "model": model,
        "input": {"messages": [{"role": "user", "content": [
            {"type": "input_audio", "input_audio": {"data": uri}}]}]},
        "parameters": {"format": fmt, "sample_rate": str(rate)},
    }
    result = http_json(base + "/api/v1/services/aigc/multimodal-generation/generation",
                       provider=provider, method="POST",
                       headers={"X-DashScope-SSE": "disable"}, body=payload,
                       timeout=240, bearer=key(PROVIDER_KEY[provider]))
    out = result.get("output") or {}
    text = out.get("text") or (out.get("sentence") or {}).get("text") or ""
    if not text:
        raise RuntimeError(redact(f"{provider} 识别未返回文本: {json.dumps(result, ensure_ascii=False)[:400]}"))
    return {"text": text, "duration_ms": (out.get("sentence") or {}).get("end_time")}


XIAOMI_ASR_MIMES = ("audio/wav", "audio/mpeg")
XIAOMI_ASR_MAX_B64 = 10 * 1024 * 1024


def asr_xiaomi(args: dict) -> dict:
    path = load_audio_arg(args)
    uri, _fmt, _rate = audio_payload(path)
    # Xiaomi documents wav/mp3 only, and a base64 payload up to 10 MB. Checking the
    # payload we actually send (post-transcode) beats a suffix check, and beats an
    # opaque vendor error.
    mime = uri.split(";", 1)[0].removeprefix("data:")
    if mime not in XIAOMI_ASR_MIMES:
        raise TargetError(f"xiaomi 语音识别只接受 wav / mp3，转码后得到 {mime}",
                          provider="xiaomi", model="mimo-v2.5-asr", code="bad_request")
    if len(uri) > XIAOMI_ASR_MAX_B64:
        raise TargetError(f"xiaomi 语音识别的 base64 载荷约 {len(uri) // 1048576} MiB，超过 10 MB 上限，请先压缩或截短音频",
                          provider="xiaomi", model="mimo-v2.5-asr", code="bad_request")
    payload = {
        "model": provider_model("xiaomi", "transcribe", "mimo-v2.5-asr", args),
        "messages": [{"role": "user", "content": [
            {"type": "input_audio", "input_audio": {"data": uri}}]}],
        "asr_options": {"language": args.get("language", "auto")},
    }
    result = http_json(base_for("xiaomi", XIAOMI_BASE) + "/chat/completions", provider="xiaomi", method="POST",
                       body=payload, timeout=240, bearer=key(KEY_XIAOMI))
    text = ((result.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    if not text:
        raise RuntimeError(redact(f"xiaomi 识别未返回文本: {json.dumps(result, ensure_ascii=False)[:400]}"))
    return {"text": text, "duration_ms": None}


# MiniMax asr-1.0: one request may carry up to 500 seconds and 50 MB, and
# verbose_json adds speaker labels while srt/vtt hand back a subtitle file.
MINIMAX_ASR_MODEL = "asr-1.0"
MINIMAX_ASR_MAX_BYTES = 50 * 1024 * 1024
MINIMAX_ASR_MAX_SECONDS = 500
MINIMAX_ASR_LANGUAGES = ("zh", "yue", "ja", "ko", "th", "vi", "id", "ms", "fil", "en", "fr", "de",
                         "es", "it", "pt", "pl", "ru", "uk", "ar", "tr")
MINIMAX_ASR_FORMATS = ("json", "verbose_json", "srt", "vtt")
MINIMAX_ASR_TYPES = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".alac": "audio/mp4",
                     ".aiff": "audio/aiff", ".aif": "audio/aiff", ".flac": "audio/flac",
                     ".aac": "audio/aac", ".opus": "audio/opus", ".ogg": "audio/ogg"}


def asr_minimax(args: dict, provider: str) -> dict:
    path = load_audio_arg(args, MINIMAX_ASR_MAX_BYTES)
    suffix = path.suffix.lower()
    ctype = MINIMAX_ASR_TYPES.get(suffix)
    if ctype is None:
        raise TargetError(f"MiniMax 语音识别需要带容器的音频（{'/'.join(sorted(MINIMAX_ASR_TYPES))}），"
                          "裸 PCM 不接受", provider=provider, model=MINIMAX_ASR_MODEL, code="bad_request")
    response_format = (args.get("response_format") or "json").strip()
    if response_format not in MINIMAX_ASR_FORMATS:
        raise TargetError(f"response_format 只能是 {'/'.join(MINIMAX_ASR_FORMATS)}", provider=provider,
                          model=MINIMAX_ASR_MODEL, code="bad_request")
    data = path.read_bytes()
    if suffix == ".wav":
        facts = _wav_facts(data)
        seconds = facts.get("seconds") or 0
        if seconds > MINIMAX_ASR_MAX_SECONDS:
            raise TargetError(f"音频约 {seconds:.0f} 秒，超过 MiniMax 单次 {MINIMAX_ASR_MAX_SECONDS} 秒上限"
                              "（超出会被拒绝而不是截断），请先切段", provider=provider,
                              model=MINIMAX_ASR_MODEL, code="bad_request")
    model = provider_model(provider, "transcribe", MINIMAX_ASR_MODEL, args)
    headers = {}
    language = (args.get("language") or "").strip()
    if language:
        # MiniMax reads the language from a header, not from the form body
        if language not in MINIMAX_ASR_LANGUAGES:
            raise TargetError(f"language 只能是 {'/'.join(MINIMAX_ASR_LANGUAGES)}，或不传用自动混合识别",
                              provider=provider, model=model, code="bad_request")
        headers["language"] = language
    body, content_type = multipart(path.name, data, ctype,
                                   {"model": model, "response_format": response_format})
    headers["Content-Type"] = content_type
    raw = http_bytes(base_for(provider, MINIMAX_BASE) + "/v1/speech_to_text", provider=provider,
                     headers=headers, body=body, timeout=300, bearer=key(PROVIDER_KEY[provider]))
    if response_format in ("srt", "vtt"):
        if not raw.strip():
            raise RuntimeError(redact(f"{provider} 未返回字幕内容"))
        dest = save_bytes(raw, "subtitle", response_format)
        return {"subtitle_path": str(dest), "format": response_format, "bytes": len(raw),
                "provider": provider, "model": model}
    result = minimax_check(json.loads(raw.decode("utf-8", "replace")), provider)
    text = result.get("text") or ""
    if not text:
        raise RuntimeError(redact(f"{provider} 识别未返回文本: {raw[:400]!r}"))
    out = {"text": text, "duration_ms": int(result["duration"] * 1000) if result.get("duration") else None,
           "provider": provider, "model": model}
    if response_format == "verbose_json":
        # diarisation is the reason to ask for the verbose shape at all
        out["n_speakers"] = result.get("n_speakers")
        out["segments"] = [{"speaker": seg.get("speaker"), "start": seg.get("start"),
                            "end": seg.get("end"), "text": seg.get("text")}
                           for seg in (result.get("segments") or [])]
    if result.get("trace_id"):
        out["trace_id"] = result["trace_id"]
    return out


DEEPSEEK_ASR_RATES = (8000, 16000, 22050, 24000, 32000, 44100, 48000)
DEEPSEEK_ASR_MAX_BYTES = 8 * 1024 * 1024
DEEPSEEK_ASR_MAX_SECONDS = 120


def _wav_facts(data: bytes) -> dict:
    """Read WAV facts from the chunks themselves.

    Streamed synthesis often hands back a WAV whose RIFF and data sizes are the
    0x7FFFFFFF placeholder written while the length was still unknown; wave.open()
    turns that placeholder into an absurd frame count, so the duration is measured
    from the bytes that are actually present.
    """
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not a RIFF/WAVE file")
    audio_format = channels = rate = block = bits = None
    usable = 0
    pos = 12
    while pos + 8 <= len(data):
        # RIFF chunks are tag first, then the 4-byte little-endian length
        tag = data[pos:pos + 4]
        size = int.from_bytes(data[pos + 4:pos + 8], "little")
        body = pos + 8
        end = min(body + size, len(data))
        if tag == b"fmt " and end - body >= 16:
            audio_format = int.from_bytes(data[body:body + 2], "little")
            channels = int.from_bytes(data[body + 2:body + 4], "little")
            rate = int.from_bytes(data[body + 4:body + 8], "little")
            block = int.from_bytes(data[body + 12:body + 14], "little") or 1
            bits = int.from_bytes(data[body + 14:body + 16], "little")
        elif tag == b"data":
            # clamp: a placeholder size is bigger than the file, real payloads are not
            usable = max(0, end - body)
        pos = body + size + (size % 2)
    if audio_format is None or rate in (None, 0):
        raise ValueError("unusable fmt chunk")
    return {"container": "wav", "sample_rate": rate, "channels": channels,
            "bit_depth": bits * 1 if bits else 0, "pcm": audio_format == 1,
            "seconds": round(usable / (block * rate), 2)}


def deepseek_asr_payload(path: Path) -> tuple:
    """Return (bytes, part filename, facts) in a shape DeepSeek accepts.

    The vendor sniffs content rather than trusting the multipart header, so the
    checks happen here on real bytes: an unusable file fails instantly instead of
    costing an upload that is certain to be rejected.
    """
    data = path.read_bytes()
    if len(data) > DEEPSEEK_ASR_MAX_BYTES:
        raise TargetError(f"audio exceeds the {DEEPSEEK_ASR_MAX_BYTES // (1024 * 1024)} MiB DeepSeek ASR limit; trim it first",
                          provider="deepseek", code="bad_request")
    if data[:4] == b"OggS":
        # Opus in Ogg: duration is not readable without a demuxer, the vendor enforces it
        return data, "speech.ogg", {"container": "opus"}
    facts = None
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        try:
            facts = _wav_facts(data)
        except Exception:
            facts = None
        if facts and facts.get("pcm") and facts["bit_depth"] == 16 \
                and facts["sample_rate"] in DEEPSEEK_ASR_RATES and facts["channels"] in (1, 2):
            if facts["seconds"] > DEEPSEEK_ASR_MAX_SECONDS:
                raise TargetError(f"audio is {facts['seconds']} s; DeepSeek ASR allows at most {DEEPSEEK_ASR_MAX_SECONDS} s",
                                  provider="deepseek", code="bad_request")
            return data, "speech.wav", facts
    converted = convert_with_ffmpeg(path)
    if converted is None:
        detail = ("the file is not 16-bit PCM WAV or Ogg Opus" if facts is None
                  else f"it is {'PCM' if facts.get('pcm') else 'compressed'} {facts['bit_depth']}-bit "
                       f"{facts['sample_rate']} Hz / {facts['channels']} ch")
        raise TargetError(f"deepseek ASR needs 16-bit PCM WAV (8000-48000 Hz) or Ogg Opus; {detail}, and ffmpeg is unavailable to convert it",
                          provider="deepseek", code="bad_request")
    try:
        out = converted.read_bytes()
        out_facts = _wav_facts(out)
    finally:
        converted.unlink(missing_ok=True)
    if len(out) > DEEPSEEK_ASR_MAX_BYTES:
        raise TargetError("converted audio still exceeds the 8 MiB DeepSeek ASR limit; trim it first",
                          provider="deepseek", code="bad_request")
    if out_facts["seconds"] > DEEPSEEK_ASR_MAX_SECONDS:
        raise TargetError(f"audio is {out_facts['seconds']} s; DeepSeek ASR allows at most {DEEPSEEK_ASR_MAX_SECONDS} s",
                          provider="deepseek", code="bad_request")
    return out, "speech.wav", out_facts


def asr_deepseek(args: dict) -> dict:
    path = load_audio_arg(args)
    audio, part_name, facts = deepseek_asr_payload(path)
    body, content_type = multipart(part_name, audio, "audio/wav" if part_name.endswith(".wav") else "audio/ogg")
    req = _request(base_for("deepseek", DEEPSEEK_BASE) + "/v1/tools/asr", method="POST",
                   headers={"Content-Type": content_type}, body=body, bearer=key(KEY_DEEPSEEK))
    try:
        with urlopen(req, timeout=180) as resp:
            result = json.loads(resp.read(8 * 1024 * 1024).decode("utf-8", "replace"))
    except HTTPError as exc:
        raise _classify_http(exc, "deepseek") from None
    except URLError as exc:
        raise RuntimeError(redact(f"deepseek 网络请求失败: {exc.reason}")) from None
    out = {"text": result.get("text", ""), "duration_ms": result.get("duration_ms"), "audio": facts}
    return out


# ---------------------------------------------------------------- provider calls: synthesize


def tts_minimax(args: dict, provider: str) -> dict:
    text = args.get("text", "")
    voice = args.get("voice") or provider_voice(provider, "male-qn-qingse")
    # MINIMAX_VOICES is a UI convenience list, not a whitelist: MiniMax also serves
    # cloned and designed voice ids. Rewriting anything unlisted to a default timbre
    # made the page promise one voice and deliver another, so send it as given.
    used = voice
    fmt = args.get("format", "mp3")
    if fmt not in ("mp3", "pcm", "flac", "wav"):
        raise RuntimeError("format 只能是 mp3 / pcm / flac / wav。")
    sample_rate = int(args.get("sample_rate", 32000))
    wire_format = "pcm" if fmt == "wav" else fmt
    payload = {
        "model": provider_model(provider, "synthesize", "speech-2.8-hd", args),
        "text": text,
        "stream": False,
        "voice_setting": {"voice_id": used, "speed": float(args.get("speed", 1.0)),
                          "vol": float(args.get("vol", 1.0)), "pitch": int(args.get("pitch", 0))},
        "audio_setting": {"sample_rate": sample_rate, "bitrate": 128000,
                          "format": wire_format, "channel": 1},
    }
    emotion = (args.get("emotion") or "").strip()
    if emotion:
        payload["voice_setting"]["emotion"] = emotion
    # quality is the LLM+rule pass over numbers, dates and currency; MiniMax degrades
    # to basic for unsupported models rather than erroring, so say what was asked.
    normalize = (args.get("text_normalization_mode") or "").strip()
    if normalize:
        if normalize not in ("basic", "quality"):
            raise TargetError("text_normalization_mode 只能是 basic 或 quality", provider=provider,
                              model=payload["model"], code="bad_request")
        payload["text_normalization_mode"] = normalize
    result = minimax_check(http_json(base_for(provider, MINIMAX_BASE) + "/v1/t2a_v2", provider=provider, method="POST",
                                    body=payload, timeout=180, bearer=key(PROVIDER_KEY[provider])),
                           provider)
    hex_audio = (result.get("data") or {}).get("audio") or ""
    if not hex_audio:
        raise RuntimeError(redact(f"{provider} 未返回音频: {json.dumps(result, ensure_ascii=False)[:300]}"))
    audio = bytes.fromhex(hex_audio)
    if wire_format == "pcm":
        dest = OUT_DIR / f"tts_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.wav"
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        with wave.open(str(dest), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(audio)
    else:
        dest = save_bytes(audio, "tts", fmt)
    return {"audio_path": str(dest), "bytes": len(audio), "voice_used": used,
            "voice_overridden": False}


def mimo_voice_reference(args: dict) -> str:
    """Read a local sample and wrap it the way voiceclone expects: a base64 data URL."""
    reference = args.get("voice_reference_path")
    if not isinstance(reference, str) or not reference.strip():
        raise TargetError("mimo-v2.5-tts-voiceclone needs voice_reference_path (a local audio file)",
                          provider="xiaomi", code="bad_request")
    source = Path(reference).expanduser()
    if not source.is_file():
        raise TargetError("voice_reference_path is not a readable file", provider="xiaomi", code="bad_request")
    data = source.read_bytes()
    if len(data) > 10 * 1024 * 1024:
        raise TargetError("voice_reference_path is larger than 10 MB", provider="xiaomi", code="bad_request")
    suffix = source.suffix.lower()
    # Xiaomi documents mp3 and wav as the only clone sample formats
    media = {".wav": "audio/wav", ".mp3": "audio/mpeg"}.get(suffix)
    if media is None:
        raise TargetError("voice_reference_path must be a wav or mp3 sample", provider="xiaomi", code="bad_request")
    return f"data:{media};base64,{base64.b64encode(data).decode()}"


def tts_xiaomi(args: dict) -> dict:
    text = args.get("text", "")
    model = provider_model("xiaomi", "synthesize", "mimo-v2.5-tts", args)
    audio = {"format": "wav"}
    if model.endswith("voicedesign"):
        # The description of the voice goes in the user turn; audio.voice is rejected.
        description = args.get("voice_description") or ""
        if not str(description).strip():
            raise TargetError("mimo-v2.5-tts-voicedesign needs voice_description",
                              provider="xiaomi", code="bad_request")
        opening = str(description).strip()
    elif model.endswith("voiceclone"):
        audio["voice"] = mimo_voice_reference(args)
        opening = "用自然、清晰的语气朗读下面这句话。"
    else:
        audio["voice"] = args.get("voice") or provider_voice("xiaomi", "mimo_default")
        opening = "用自然、清晰的语气朗读下面这句话。"
    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": opening},
            {"role": "assistant", "content": text},
        ],
        "audio": audio,
    }
    result = http_json(base_for("xiaomi", XIAOMI_BASE) + "/chat/completions", provider="xiaomi", method="POST",
                       body=payload, timeout=180, bearer=key(KEY_XIAOMI))
    msg = ((result.get("choices") or [{}])[0].get("message") or {})
    b64 = (msg.get("audio") or {}).get("data") or ""
    if not b64:
        raise RuntimeError(redact(f"xiaomi 未返回音频: {json.dumps(result, ensure_ascii=False)[:300]}"))
    audio = base64.b64decode(b64)
    dest = save_bytes(audio, "tts", "wav")
    return {"audio_path": str(dest), "bytes": len(audio),
            "voice_used": payload["audio"].get("voice", "described" if model.endswith("voicedesign") else "cloned"),
            "model_used": model, "voice_overridden": False}


def tts_deepseek(args: dict) -> dict:
    text = args.get("text", "")
    voice = args.get("voice") or provider_voice("deepseek", "stella")
    used = voice if voice in ("stella", "tide") else "stella"
    payload = {"text": text, "voice_id": used, "format": "wav", "filter_markdown": True}
    audio = http_bytes(base_for("deepseek", DEEPSEEK_BASE) + "/v1/tools/tts", provider="deepseek",
                       headers={"Content-Type": "application/json"},
                       body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                       bearer=key(KEY_DEEPSEEK))
    dest = save_bytes(audio, "tts", "wav")
    return {"audio_path": str(dest), "bytes": len(audio), "voice_used": used,
            "voice_overridden": used != (voice or used)}


# Two Qwen speech families answer on two different routes, and the wrong one
# comes back as "InvalidParameter: url error" rather than a model-specific message.
QWEN_TTS_ROUTES = (
    # (model-name fragments, path). "Engine error [411]" once looked like the endpoint
    # rejecting extra fields; it was really a voice from the other family.
    (("qwen3-tts",), "/api/v1/services/aigc/multimodal-generation/generation"),
    # CosyVoice shares the Qwen-Audio-TTS endpoint; its name matches neither
    # fragment, so without this row it fell through and answered "url error".
    (("cosyvoice",), "/api/v1/services/audio/tts/SpeechSynthesizer"),
    (("audio", "tts"), "/api/v1/services/audio/tts/SpeechSynthesizer"),
)


def qwen_tts_route(model: str) -> str:
    name = (model or "").lower()
    for fragments, route in QWEN_TTS_ROUTES:
        if all(fragment in name for fragment in fragments):
            return route
    return QWEN_TTS_ROUTES[0][1]


# Each Qwen speech family has its own system voices; handing one family the
# other's voice id is rejected by the engine without saying why.
QWEN_TTS_DEFAULT_VOICES = (
    (("qwen3-tts",), "Mia"),
    # System voices are per model family; longanyang is CosyVoice v3's flagship one.
    (("cosyvoice",), "longanyang"),
    # 3.1 and 3.0 do not share voice ids, so the newer family needs its own row
    (("audio", "3.1"), "longanhuan_v3.1"),
    (("audio", "tts"), "longanhuan_v3.6"),
)


def qwen_tts_default_voice(model: str) -> str:
    name = (model or "").lower()
    for fragments, voice in QWEN_TTS_DEFAULT_VOICES:
        if all(fragment in name for fragment in fragments):
            return voice
    return QWEN_TTS_DEFAULT_VOICES[0][1]


QWEN_TTS_FORMATS = ("wav", "mp3", "pcm", "opus")
QWEN_TTS_RATES = (8000, 12000, 16000, 22050, 24000, 44100, 48000)
# Documented language hints. "other" covers the long tail for 3.1-tts-flash only.
QWEN_LANGUAGE_HINTS = ("zh", "en", "fr", "de", "ja", "ko", "ru", "pt", "th", "id", "vi", "es", "it",
                       "ms", "fil", "ar", "other")
QWEN_HOT_FIX_KEYS = ("pronunciation", "replace")


def instruct_weight(text: str) -> int:
    """CosyVoice counts each CJK ideograph as two characters, everything else as one."""
    return sum(2 if ("\u3400" <= ch <= "\u4dbf" or "\u4e00" <= ch <= "\u9fff"
                     or "\uf900" <= ch <= "\ufaff") else 1 for ch in text)


# Verified live: the token-plan tier does not serve these, and answers a bare 404
# "Model not exist". Naming the tier turns an anonymous upstream error into a switch.
PLAN_MISSING_MODELS = ("qwen-audio-3.1", "cosyvoice", "voice-enrollment")


def reject_plan_gap(provider: str, model: str, capability: str) -> None:
    if provider == "qwen-plan" and model.startswith(PLAN_MISSING_MODELS):
        raise TargetError(f"千问套餐端不含 {model}（实测 Model not exist），{capability}请用 "
                          "provider=qwen-payg", provider=provider, model=model,
                          code="provider_mismatch")


def tts_qwen(args: dict, provider: str) -> dict:
    text = args.get("text", "")
    model = provider_model(provider, "synthesize", DEFAULT_MODELS["synthesize"][provider], args)
    route = qwen_tts_route(model)
    reject_plan_gap(provider, model, "语音合成")
    expressive = route.endswith("SpeechSynthesizer")  # Qwen-Audio-TTS and CosyVoice
    audio_input = {"text": text, "voice": args.get("voice") or provider_voice(provider, qwen_tts_default_voice(model))}
    ignored: list[str] = []

    # SSML changes what is heard. Sending <speak> without the flag makes the service
    # read the markup as words and report nothing, so either turn it on or say why not.
    ssml = bool(args.get("ssml")) or "<speak" in text
    if ssml:
        if not expressive:
            raise TargetError("该模型不支持 SSML（仅 Qwen-Audio-TTS / CosyVoice 支持）", provider=provider,
                              model=model, code="model_not_supported")
        audio_input["enable_ssml"] = True
    instruction = (args.get("instruction") or "").strip()
    if instruction:
        if not expressive:
            raise TargetError("该模型不支持 instruction 指令控制（Qwen3-TTS 无指令控制能力）", provider=provider,
                              model=model, code="model_not_supported")
        limit = instruct_weight(instruction)
        if "cosyvoice" in model.lower() and limit > 100:
            raise TargetError(f"CosyVoice 的 instruction 限 100 字符（汉字按 2 计），当前 {limit}",
                              provider=provider, model=model, code="bad_request")
        audio_input["instruction"] = instruction
    # language_hints and hot_fix change what is heard, so an unsupported family gets a
    # refusal instead of a request that quietly does nothing.
    hints = args.get("language_hints")
    if hints:
        if not expressive:
            raise TargetError("该模型不支持 language_hints（仅 Qwen-Audio-TTS / CosyVoice）", provider=provider,
                              model=model, code="model_not_supported")
        # Official note: the parameter is an array but only the first element is used.
        if not isinstance(hints, list) or len(hints) != 1:
            raise TargetError("language_hints 只接受一个元素：当前版本只处理数组的第一个值，多传会被忽略",
                              provider=provider, model=model, code="bad_request")
        hint = str(hints[0]).strip()
        if hint not in QWEN_LANGUAGE_HINTS:
            raise TargetError(f"language_hints 只能是 {'/'.join(QWEN_LANGUAGE_HINTS)}", provider=provider,
                              model=model, code="bad_request")
        audio_input["language_hints"] = [hint]
    hot_fix = args.get("hot_fix")
    if hot_fix:
        if not expressive:
            raise TargetError("该模型不支持 hot_fix", provider=provider, model=model, code="model_not_supported")
        if not isinstance(hot_fix, dict) or not (set(hot_fix) and set(hot_fix) <= set(QWEN_HOT_FIX_KEYS)):
            raise TargetError('hot_fix 只能包含 pronunciation / replace，形如 {"天气": "tian1 qi4"} 的数组',
                              provider=provider, model=model, code="bad_request")
        for slot, entries in hot_fix.items():
            if not isinstance(entries, list) or not all(isinstance(one, dict) and len(one) == 1 for one in entries):
                raise TargetError(f"hot_fix.{slot} 需要数组，每项是单键对象，例如 [{{\"今天\": \"金天\"}}]",
                                  provider=provider, model=model, code="bad_request")
        audio_input["hot_fix"] = hot_fix
    for field, (low, high) in (("volume", (0, 100)), ("rate", (0.5, 2.0))):
        value = args.get(field)
        if value is None:
            continue
        if not expressive:
            ignored.append(field)  # the other family has no such knob; report, do not fail
            continue
        number = int(value) if field == "volume" else float(value)
        if not low <= number <= high:
            raise TargetError(f"{field} 取值范围是 {low}~{high}，收到 {value}", provider=provider, model=model,
                              code="bad_request")
        audio_input[field] = number
    for field in ("format", "sample_rate"):
        value = args.get(field)
        if not value:
            continue
        if not expressive:
            ignored.append(field)  # Qwen3-TTS picks its own container; not an error today
            continue
        if field == "format" and value not in QWEN_TTS_FORMATS:
            raise TargetError(f"qwen 语音合成支持 {', '.join(QWEN_TTS_FORMATS)}，不支持 {value}",
                              provider=provider, model=model, code="bad_request")
        audio_input[field] = int(value) if field == "sample_rate" else value
    payload = {"model": model, "input": audio_input}
    result = http_json(base_for(provider, QWEN_PLAN_BASE if provider == "qwen-plan" else QWEN_PAYG_BASE) + route,
                       provider=provider, method="POST", body=payload,
                       timeout=180, bearer=key(PROVIDER_KEY[provider]))
    url = ((result.get("output") or {}).get("audio") or {}).get("url")
    if not url:
        raise RuntimeError(redact(f"qwen-payg 未返回音频 URL: {json.dumps(result, ensure_ascii=False)[:300]}"))
    dest = download(url, "tts")
    out = {"audio_path": str(dest), "bytes": dest.stat().st_size,
           "voice_used": payload["input"]["voice"], "model_used": model, "route": route,
           "voice_overridden": False}
    sent = {name: audio_input[name] for name in ("enable_ssml", "instruction", "language_hints",
                                                 "hot_fix", "volume", "rate", "format", "sample_rate")
            if name in audio_input}
    if sent:
        out["params_used"] = sent
    if ssml:
        out["ssml"] = True
    if instruction:
        out["instruction_used"] = instruction
    if ignored:
        out["ignored_params"] = ignored
    return out


# ---------------------------------------------------------------- provider calls: image


def _aspect_ratio(size: str | None) -> str:
    if not size or "x" not in size.lower():
        return "1:1"
    try:
        w, h = (int(part) for part in size.lower().split("x", 1))
    except ValueError:
        return "1:1"
    ratio = w / h if h else 1.0
    options = {"1:1": 1.0, "16:9": 16 / 9, "4:3": 4 / 3, "3:2": 1.5,
               "2:3": 2 / 3, "3:4": 3 / 4, "9:16": 9 / 16, "21:9": 21 / 9}
    return min(options.items(), key=lambda item: abs(item[1] - ratio))[0]


MINIMAX_H3_IMAGE_MODELS = ("MiniMax-H3-Image", "MiniMax-H3-Image-Highspeed")
MINIMAX_H3_AREAS = ("1K", "2K", "4K")


def image_minimax_h3(args: dict, provider: str, model: str) -> dict:
    """Text-to-image on the OpenAI-style H3 endpoint.

    The endpoint is documented as non-idempotent: a client timeout still bills the
    finished image, so this function never retries and the row's tool timeout must
    stay above the 600 s the server is willing to wait.
    """
    payload: dict = {"model": model, "prompt": args.get("prompt", ""), "n": int(args.get("n", 1)),
                     "response_format": "url", "output_format": args.get("output_format") or "jpeg"}
    size = args.get("size")
    aspect = args.get("aspect_ratio")
    if size and size.lower() != "auto":
        payload["size"] = size  # with WxH the server rejects aspect_ratio/area
    else:
        if aspect:
            payload["aspect_ratio"] = aspect
        area = args.get("area") or "1K"
        if area not in MINIMAX_H3_AREAS:
            raise TargetError(f"area must be one of {', '.join(MINIMAX_H3_AREAS)}",
                              provider=provider, model=model, code="bad_request")
        payload["area"] = area
    if not str(payload["prompt"]).strip():
        raise TargetError("prompt is required", provider=provider, model=model, code="bad_request")
    path = "/v1/images/generations"
    references = args.get("images") or []
    if references:
        # Editing takes 1..16 reference pictures. Local files go out as data URIs;
        # the whole request body must stay under 64 MiB, and base64 costs 4/3 of the
        # bytes on disk, so the limit is checked against what we actually send.
        path = "/v1/images/edits"
        if not isinstance(references, list) or not 1 <= len(references) <= 16:
            raise TargetError("images 需要 1~16 项（本地路径或 URL）", provider=provider, model=model,
                              code="bad_request")
        items, encoded = [], 0
        for ref in references:
            if not isinstance(ref, str) or not ref.strip():
                raise TargetError("images 每一项必须是非空字符串", provider=provider, model=model, code="bad_request")
            ref = ref.strip()
            if ref.startswith(("http://", "https://", "data:", "mm_file://")):
                items.append({"image_url": ref})
                encoded += len(ref)
                continue
            source = Path(ref).expanduser()
            if not source.is_file():
                raise TargetError(f"找不到参考图: {source}", provider=provider, model=model, code="bad_request")
            mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                    ".webp": "image/webp", ".heic": "image/heic", ".heif": "image/heif"}.get(source.suffix.lower())
            if mime is None:
                raise TargetError(f"参考图格式 {source.suffix or '(无扩展名)'} 不被支持，请用 JPG/PNG/WEBP/HEIC/HEIF",
                                  provider=provider, model=model, code="bad_request")
            raw = source.read_bytes()
            if len(raw) > 64 * 1024 * 1024:
                raise TargetError(f"参考图 {source.name} 超过单张 64 MiB 上限", provider=provider, model=model,
                                  code="bad_request")
            data_uri = f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")
            encoded += len(data_uri)
            items.append({"image_url": data_uri})
        if encoded > 64 * 1024 * 1024:
            raise TargetError(f"参考图 base64 后约 {encoded // 1048576} MiB，超过请求体 64 MiB 上限；"
                              "请改用公网 URL", provider=provider, model=model, code="bad_request")
        payload["images"] = items
    result = http_json(base_for(provider, MINIMAX_BASE) + path, provider=provider,
                       method="POST", body=payload, timeout=580, bearer=key(PROVIDER_KEY[provider]))
    items = result.get("data") or []
    files, failures = [], []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        error = item.get("error") or {}
        if error:
            failures.append(f"{error.get('code')}: {error.get('message')}")
        elif item.get("url"):
            files.append(str(download(item["url"], "image")))
    if not files:
        detail = " | ".join(failures) if failures else json.dumps(result, ensure_ascii=False)[:300]
        raise RuntimeError(redact(f"{provider} 未返回图像: {detail}"))
    out = {"files": files, "count": len(files), "model": model}
    if failures:
        out["partial_failures"] = failures  # a 200 can still carry per-image errors
    return out


def image_minimax(args: dict, provider: str) -> dict:
    model = provider_model(provider, "generate_image", DEFAULT_MODELS["generate_image"][provider], args)
    if model in MINIMAX_H3_IMAGE_MODELS:
        return image_minimax_h3(args, provider, model)
    payload = {
        "model": model,
        "prompt": args.get("prompt", ""),
        "aspect_ratio": args.get("aspect_ratio") or _aspect_ratio(args.get("size")),
        "response_format": "url",
        "n": int(args.get("n", 1)),
    }
    result = minimax_check(http_json(base_for(provider, MINIMAX_BASE) + "/v1/image_generation", provider=provider,
                                     method="POST", body=payload, timeout=240,
                                     bearer=key(PROVIDER_KEY[provider])), provider)
    urls = (result.get("data") or {}).get("image_urls") or []
    files = [str(download(url, "image")) for url in urls]
    if not files:
        raise RuntimeError(redact(f"{provider} 未返回图像: {json.dumps(result, ensure_ascii=False)[:400]}"))
    return {"files": files, "count": len(files)}


def image_qwen_plan(args: dict) -> dict:
    raw = (args.get("size") or "1024x1024").replace("x", "*").replace("X", "*")
    if "*" not in raw:
        raw = "1024*1024"
    payload = {
        "model": provider_model("qwen-plan", "generate_image", "qwen-image-3.0-pro", args),
        "input": {"messages": [{"role": "user", "content": [{"text": args.get("prompt", "")}]}]},
        "parameters": {"size": raw, "n": int(args.get("n", 1))},
    }
    result = http_json(base_for("qwen-plan", QWEN_PLAN_BASE) + "/api/v1/services/aigc/multimodal-generation/generation",
                       provider="qwen-plan", method="POST", body=payload,
                       timeout=300, bearer=key(KEY_QWEN_PLAN))
    urls: list[str] = []
    for choice in (result.get("output") or {}).get("choices") or []:
        for part in (choice.get("message") or {}).get("content") or []:
            if isinstance(part, dict) and part.get("image"):
                urls.append(str(part["image"]))
    files = [str(download(url, "image")) for url in urls]
    if not files:
        raise RuntimeError(redact(f"qwen-plan 未返回图像: {json.dumps(result, ensure_ascii=False)[:400]}"))
    return {"files": files, "count": len(files)}


def image_qwen_payg(args: dict) -> dict:
    payload = {
        "model": provider_model("qwen-payg", "generate_image", "qwen-image-2.1-turbo", args),
        "prompt": args.get("prompt", ""),
        "size": args.get("size") or "1024x1024",
        "n": int(args.get("n", 1)),
    }
    result = http_json(base_for("qwen-payg", QWEN_PAYG_BASE) + "/compatible-mode/v1/images/generations",
                       provider="qwen-payg", method="POST", body=payload,
                       timeout=300, bearer=key(KEY_QWEN_PAYG))
    urls = [item["url"] for item in (result.get("data") or [])
            if isinstance(item, dict) and item.get("url")]
    files = [str(download(url, "image")) for url in urls]
    if not files:
        raise RuntimeError(redact(f"qwen-payg 未返回图像: {json.dumps(result, ensure_ascii=False)[:400]}"))
    return {"files": files, "count": len(files)}


# ---------------------------------------------------------------- provider calls: video


def video_qwen(args: dict, base: str, provider: str) -> dict:
    duration = int(args.get("duration", 5))
    resolution = args.get("resolution", "720P")
    if duration != -1 and not 2 <= duration <= 30:
        raise RuntimeError("duration 需在 2~30 秒之间（或 -1 由模型自动决定）。")
    if resolution not in ("480P", "720P", "1080P"):
        raise RuntimeError("resolution 只能是 480P / 720P / 1080P。")
    parameters = {"duration": duration, "resolution": resolution}
    ratio = args.get("ratio")
    if ratio:
        if ratio not in ("21:9", "16:9", "4:3", "1:1", "3:4", "9:16"):
            raise RuntimeError("ratio 只能是 21:9 / 16:9 / 4:3 / 1:1 / 3:4 / 9:16。")
        parameters["ratio"] = ratio
    payload = {
        "model": provider_model(provider, "generate_video", "wan3.0-video", args),
        "input": {"prompt": args.get("prompt", "")},
        "parameters": parameters,
    }
    result = http_json(base + "/api/v1/services/aigc/video-generation/video-synthesis",
                       provider=provider, method="POST",
                       headers={"X-DashScope-Async": "enable"}, body=payload,
                       timeout=120, bearer=key(PROVIDER_KEY[provider]))
    out = result.get("output") or {}
    job_id = out.get("task_id")
    if not job_id:
        raise RuntimeError(redact(f"{provider} 未拿到 task_id: {json.dumps(result, ensure_ascii=False)[:400]}"))
    _jobs[job_id] = {"provider": provider, "files": []}
    return {"job_id": job_id, "state": out.get("task_status", "PENDING")}


MINIMAX_V2_PREFIX = "minimax-h3"
MINIMAX_V2_RESOLUTIONS = {"MiniMax-H3": ("768P", "2K"),
                          "MiniMax-H3-Highspeed": ("480P", "768P"),
                          "MiniMax-H3-Lite": ("480P", "768P"),
                          "MiniMax-H3-Max": ("480P", "768P")}
MINIMAX_V2_RATIOS = ("21:9", "16:9", "4:3", "1:1", "3:4", "9:16")


def is_minimax_v2(model: str) -> bool:
    return (model or "").lower().startswith(MINIMAX_V2_PREFIX)


def video_minimax_v2(args: dict, provider: str, model: str) -> dict:
    """Text-to-video on the H3 protocol: content[] in, task_id out."""
    resolution = args.get("resolution") or "768P"
    allowed = MINIMAX_V2_RESOLUTIONS.get(model)
    if allowed is None:
        raise TargetError(f"{model} is not a supported H3 model", provider=provider, model=model,
                          code="unsupported_model")
    if resolution not in allowed:
        raise TargetError(f"{model} resolution must be one of {', '.join(allowed)}",
                          provider=provider, model=model, code="bad_request")
    duration = int(args.get("duration", 5))
    low = 5 if model == "MiniMax-H3-Max" else 4
    if not low <= duration <= 15:
        raise TargetError(f"{model} duration must be between {low} and 15 seconds",
                          provider=provider, model=model, code="bad_request")
    ratio = args.get("ratio") or "16:9"  # a text-only job needs a concrete ratio
    if ratio not in MINIMAX_V2_RATIOS:
        raise TargetError(f"ratio must be one of {', '.join(MINIMAX_V2_RATIOS)}",
                          provider=provider, model=model, code="bad_request")
    payload = {"model": model, "content": [{"type": "text", "text": args.get("prompt", "")}],
               "resolution": resolution, "duration": duration, "ratio": ratio}
    result = http_json(base_for(provider, MINIMAX_BASE) + "/v2/video_generation", provider=provider,
                       method="POST", body=payload, timeout=120, bearer=key(PROVIDER_KEY[provider]))
    task_id = result.get("task_id") or (result.get("task") or {}).get("id")
    if not task_id:
        raise RuntimeError(redact(f"{provider} 未拿到 task_id: {json.dumps(result, ensure_ascii=False)[:400]}"))
    _jobs[str(task_id)] = {"provider": provider, "files": []}
    return {"job_id": str(task_id), "state": "queued", "model": model}


def video_minimax_payg(args: dict) -> dict:
    """MiniMax video speaks H3 only.

    The legacy V1 protocol existed for Hailuo, which MiniMax moved into 历史模型, so a
    stale model name is now refused rather than routed to a dead endpoint.
    """
    model = provider_model("minimax-payg", "generate_video", DEFAULT_MODELS["generate_video"]["minimax-payg"], args)
    if not is_minimax_v2(model):
        raise TargetError("MiniMax 视频只保留 H3 一族，"
                          f"{model or '(空)'} 走的是已下线的 V1 协议；请用 "
                          "MiniMax-H3 / -Lite / -Max / -Highspeed",
                          provider="minimax-payg", model=model, code="model_not_supported")
    return video_minimax_v2(args, "minimax-payg", model)


def job_qwen(job_id: str, base: str, provider: str) -> dict:
    result = http_json(base + f"/api/v1/tasks/{job_id}", provider=provider,
                       timeout=60, bearer=key(PROVIDER_KEY[provider]))
    out = result.get("output") or {}
    if not out and "code" in result and result.get("code") not in (None, "success"):
        raise RuntimeError(redact(f"{provider} 查询失败: {json.dumps(result, ensure_ascii=False)[:300]}"))
    state = out.get("task_status")
    if state is None:
        raise RuntimeError(f"{provider} 不认识该任务")
    entry = _jobs.setdefault(job_id, {"provider": provider, "files": []})
    url = out.get("video_url") or out.get("url")
    if state == "SUCCEEDED" and url and not entry["files"]:
        entry["files"].append(str(download(url, "video")))
    return {"state": state, "files": entry["files"],
            "error": out.get("code") or out.get("message") if state == "FAILED" else None}


def job_minimax_v2(job_id: str, provider: str) -> dict:
    result = http_json(base_for(provider, MINIMAX_BASE) + f"/v2/query/video_generation/{job_id}",
                       provider=provider, timeout=60, bearer=key(PROVIDER_KEY[provider]))
    task = result.get("task") or result
    status = task.get("status")
    if not status:
        raise RuntimeError("MiniMax H3 不认识该任务")
    entry = _jobs.setdefault(job_id, {"provider": provider, "files": []})
    state = {"succeeded": "SUCCEEDED", "failed": "FAILED", "cancelled": "FAILED"}.get(status, status.upper())
    url = ((task.get("content") or {}).get("url")) or ""
    if state == "SUCCEEDED" and url and not entry["files"]:
        entry["files"].append(str(download(url, "video")))
    failure = task.get("error") or {}
    return {"state": state, "files": entry["files"],
            "error": (failure.get("message") or failure.get("code")) if state == "FAILED" else None}


# ---------------------------------------------------------------- chain runner


DEFAULT_MODELS = {
    "transcribe": {"qwen-plan": "qwen-audio-3.0-asr-flash", "qwen-payg": "qwen-audio-3.1-asr-flash", "xiaomi": "mimo-v2.5-asr",
                   "minimax-plan": MINIMAX_ASR_MODEL, "minimax-payg": MINIMAX_ASR_MODEL},
    "synthesize": {"minimax-plan": "speech-2.8-hd", "minimax-payg": "speech-2.8-hd", "xiaomi": "mimo-v2.5-tts",
                 # payg defaults to 3.1: it supports instruct, emotion tags, SSML and
                 # cloned/designed voices, and it is the family that still has quota here.
                 "qwen-plan": "qwen-audio-3.0-tts-plus", "qwen-payg": "qwen-audio-3.1-tts-plus"},
    "generate_image": {"minimax-plan": "MiniMax-H3-Image", "minimax-payg": "MiniMax-H3-Image",
                       "qwen-plan": "qwen-image-3.0-pro", "qwen-payg": "qwen-image-3.0-pro"},
    # Verified on 2026-11-17: the M Plan tier serves MiniMax-H3-Image, and MiniMax's own
    # pricing page moved Hailuo into 历史模型, so video defaults to H3-Lite (the cheapest
    # current H3: 480P at 0.16 元/s, 768P at 0.26 元/s). The legacy V1 Hailuo protocol has
    # since been removed, so MiniMax video is H3-only.
    "generate_video": {"qwen-plan": "wan3.0-video", "qwen-payg": "wan3.0-video", "minimax-payg": "MiniMax-H3-Lite"},
}


# ---------------------------------------------------------------- spend budget
# The cap is applied before dispatch on purpose: the point is to stop a call, not to
# record that it happened. Every estimate is returned to the caller as
# cost_estimate_cny / budget_used_today_cny so the number is never a black box.

BUDGET_DEFAULTS = {"daily_limit_cny": 10.0, "provider_limit_cny": {}, "model_limit_cny": {},
                   "prices_cny": {}}
# Confirmed against the vendors' pricing pages or this account on 2026-11-17.
MINIMAX_VIDEO_PER_SECOND = {("MiniMax-H3-Lite", "480P"): 0.16, ("MiniMax-H3-Lite", "768P"): 0.26,
                            ("MiniMax-H3", "768P"): 0.50, ("MiniMax-H3", "2K"): 0.80,
                            ("MiniMax-H3-Max", "768P"): 0.50, ("MiniMax-H3-Max", "2K"): 0.80}
MINIMAX_IMAGE_PER_SHOT = {("MiniMax-H3-Image", "1K"): 0.143, ("MiniMax-H3-Image", "2K"): 0.57,
                          ("MiniMax-H3-Image", "4K"): 2.28,
                          ("MiniMax-H3-Image-Highspeed", "1K"): 0.232,
                          ("MiniMax-H3-Image-Highspeed", "2K"): 0.93,
                          ("MiniMax-H3-Image-Highspeed", "4K"): 3.71}
MINIMAX_TTS_PER_CHAR = {"speech-2.8-hd": 0.00035, "speech-2.6-hd": 0.00035, "speech-02-hd": 0.00035,
                        "speech-2.8-turbo": 0.0002, "speech-2.6-turbo": 0.0002, "speech-02-turbo": 0.0002}
DEEPSEEK_ASR_PER_SECOND = 0.78 / 3600.0
# MiMo TTS/ASR is billed "free for a limited time" per Xiaomi's own doc; if that ends,
# put a price in budget.prices_cny instead of editing code.
FREE_PROVIDERS = frozenset({"xiaomi"})
UNKNOWN_COST = {"generate_image": 0.30, "video_per_second": 0.60, "tts_per_char": 0.0005,
                "asr_per_second": 0.0010}
UNKNOWN_AUDIO_SECONDS = 120.0


def budget_settings() -> dict:
    out = {"daily_limit_cny": BUDGET_DEFAULTS["daily_limit_cny"], "provider_limit_cny": {},
           "model_limit_cny": {}, "prices_cny": {}}
    raw = runtime_config().get("budget")
    if not isinstance(raw, dict):
        return out
    if isinstance(raw.get("daily_limit_cny"), (int, float)):
        out["daily_limit_cny"] = float(raw["daily_limit_cny"])
    for key in ("provider_limit_cny", "model_limit_cny", "prices_cny"):
        value = raw.get(key)
        if isinstance(value, dict):
            out[key] = {str(k): float(v) for k, v in value.items() if isinstance(v, (int, float))}
    return out


def usage_file() -> Path:
    chains = os.environ.get("MEDIA_BRIDGE_CHAINS_FILE")
    base = Path(chains).parent if chains else OUT_DIR
    return Path(base) / "media-bridge.usage.json"


def _fresh_day() -> dict:
    return {"date": time.strftime("%Y-%m-%d"), "total": 0.0, "by_provider": {}, "by_model": {}, "calls": 0}


def usage_today() -> dict:
    """Today's ledger in local dates; a stale file rolls over implicitly."""
    try:
        data = json.loads(Path(usage_file()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _fresh_day()
    if not isinstance(data, dict) or data.get("date") != time.strftime("%Y-%m-%d"):
        return _fresh_day()
    fresh = _fresh_day()
    for key, default in fresh.items():
        if key not in data or not isinstance(data[key], type(default)):
            data[key] = default
    return data


def save_usage(data: dict) -> None:
    path = Path(usage_file())
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    except OSError as exc:
        raise TargetError(f"花费账本无法写入 {path}: {exc}；为避免无上限扣费，本次调用已拒绝",
                          code="budget_unavailable") from None


def media_seconds(path) -> float:
    try:
        source = Path(str(path)).expanduser()
        if source.suffix.lower() == ".wav" and source.is_file():
            return float(_wav_facts(source.read_bytes()).get("seconds") or UNKNOWN_AUDIO_SECONDS)
    except Exception:
        pass
    return UNKNOWN_AUDIO_SECONDS


def estimate_cost(capability: str, provider: str, model: str, args: dict) -> float:
    settings = budget_settings()
    for key in (f"{provider}:{model}", model):
        if key in settings["prices_cny"]:
            return max(0.0, float(settings["prices_cny"][key]))
    if provider in FREE_PROVIDERS:
        return 0.0
    if capability == "generate_image":
        shots = max(1, int(args.get("n", 1) or 1))
        area = str(args.get("area") or "1K").upper()
        known = MINIMAX_IMAGE_PER_SHOT.get((model, area))
        return (known if known is not None else UNKNOWN_COST["generate_image"]) * shots
    if capability == "generate_video":
        try:
            seconds = int(args.get("duration", 5) or 5)
        except (TypeError, ValueError):
            seconds = 5
        if seconds < 0:
            seconds = 10  # "-1 lets the vendor choose"; assume a mid-length clip
        known = MINIMAX_VIDEO_PER_SECOND.get((model, str(args.get("resolution") or "768P")))
        return (known if known is not None else UNKNOWN_COST["video_per_second"]) * max(1, seconds)
    if capability == "synthesize":
        chars = max(1, len(str(args.get("text") or "")))
        return MINIMAX_TTS_PER_CHAR.get(model, UNKNOWN_COST["tts_per_char"]) * chars
    if capability == "transcribe":
        rate = DEEPSEEK_ASR_PER_SECOND if provider == "deepseek" else UNKNOWN_COST["asr_per_second"]
        return rate * media_seconds(args.get("audio_path"))
    return 0.0


def budget_preflight(cost: float) -> None:
    """Refuse to spend when the ledger cannot be written.

    Recording happens after the vendor call, so checking only at record time would let
    an uncounted request through. This probe costs one zero-byte append.
    """
    if cost <= 0:
        return
    path = Path(usage_file())
    try:
        Path(path.parent).mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8"):
            pass
    except OSError as exc:
        raise TargetError(f"花费账本不可写（{path}）：{exc}；请求未发出、未产生费用。"
                          "宁可拒绝，也不要在无记账状态下扣费。",
                          code="budget_unavailable") from None


def budget_block(capability: str, provider: str, model: str, cost: float):
    """Return a refusal message when any tier would be exceeded, else None."""
    if cost <= 0:
        return None
    settings = budget_settings()
    used = usage_today()
    model_key = f"{provider}:{model}"
    limit_model = settings["model_limit_cny"].get(model_key, settings["model_limit_cny"].get(model))
    for label, limit, spent in (
            ("今日总额", settings["daily_limit_cny"], float(used["total"])),
            (f"品牌 {provider}", settings["provider_limit_cny"].get(provider), float(used["by_provider"].get(provider, 0.0))),
            (f"模型 {model}", limit_model, float(used["by_model"].get(model_key, 0.0)))):
        if limit is None or not isinstance(limit, (int, float)) or limit < 0:
            continue  # unset or negative means "no cap for this tier"
        if spent + cost > float(limit):
            return (f"{label}花费上限 ¥{float(limit):.2f}：今日已用 ¥{spent:.2f}，本次预估 ¥{cost:.2f}。"
                    "请求未发出、未产生费用。上限可改 budget.daily_limit_cny / provider_limit_cny / "
                    "model_limit_cny，价目表可改 budget.prices_cny；负数表示不限，0 表示关闭该类调用。")
    return None


def budget_record(capability: str, provider: str, model: str, cost: float) -> float:
    if cost <= 0:
        return 0.0
    data = usage_today()
    model_key = f"{provider}:{model}"
    data["total"] = round(float(data["total"]) + cost, 6)
    data["by_provider"][provider] = round(float(data["by_provider"].get(provider, 0.0)) + cost, 6)
    data["by_model"][model_key] = round(float(data["by_model"].get(model_key, 0.0)) + cost, 6)
    data["calls"] = int(data["calls"]) + 1
    save_usage(data)
    return data["total"]


class TargetError(Exception):
    def __init__(self, message: str, *, provider=None, model=None, retryable=False, code='target_failed'):
        super().__init__(message)
        self.provider = provider
        self.model = model
        self.retryable = retryable
        self.code = code


def call_chain(capability: str, args: dict) -> dict:
    explicit = args.get("provider")
    model = args.get("model")
    if explicit is not None and (not isinstance(explicit, str) or not explicit.strip()):
        raise TargetError("provider must be a non-empty string", provider=explicit, model=model,
                          code="bad_request")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise TargetError("model must be a non-empty string", provider=explicit, model=model,
                          code="bad_request")
    available = DISPATCH[capability]
    if explicit:
        if explicit not in available:
            raise TargetError(f"provider {explicit} does not support {capability}", provider=explicit,
                              model=model, code="unsupported_provider")
        if explicit in (runtime_config().get("disabled") or []):
            raise TargetError(f"provider {explicit} is disabled", provider=explicit, model=model,
                              code="provider_disabled")
        if not has(PROVIDER_KEY[explicit]):
            raise TargetError(f"provider {explicit} has no credentials", provider=explicit, model=model,
                              code="no_credentials")
        provider = explicit
    else:
        provider = next((p for p in chain_for(capability) if p in available and has(PROVIDER_KEY[p])
                         and not (p == "deepseek" and model)), None)
        if provider is None:
            raise TargetError(f"No available provider for {capability}", model=model, code="no_target")
    if provider == "deepseek" and model:
        raise TargetError("deepseek endpoint does not support model selection", provider=provider,
                          model=model, code="model_not_supported")
    selected_args = dict(args)
    if provider != "deepseek":
        model = provider_model(provider, capability, DEFAULT_MODELS[capability][provider], args)
        selected_args["model"] = model
    cost = estimate_cost(capability, provider, model, selected_args)
    budget_preflight(cost)
    refusal = budget_block(capability, provider, model, cost)
    if refusal:
        raise TargetError(refusal, provider=provider, model=model, code="budget_exhausted")
    try:
        result = available[provider](selected_args)
    except TargetError:
        # The adapter refused before sending anything: keep its stage and code.
        raise
    except Exception as exc:
        raise TargetError(redact(str(exc)), provider=provider, model=model,
                          retryable=isinstance(exc, Retryable), code="upstream_failed") from None
    spent = budget_record(capability, provider, model, cost)
    result["provider"] = provider
    if model is not None:
        result["model"] = model
    if cost > 0:
        result["cost_estimate_cny"] = round(cost, 4)
        result["budget_used_today_cny"] = spent
    return result


DISPATCH: dict[str, dict[str, object]] = {
    "transcribe": {
        "qwen-plan": lambda a: asr_qwen(a, base_for("qwen-plan", QWEN_PLAN_BASE), "qwen-plan"),
        "qwen-payg": lambda a: asr_qwen(a, base_for("qwen-payg", QWEN_PAYG_BASE), "qwen-payg"),
        "xiaomi": asr_xiaomi,
        "deepseek": asr_deepseek,
        "minimax-plan": lambda a: asr_minimax(a, "minimax-plan"),
        "minimax-payg": lambda a: asr_minimax(a, "minimax-payg"),
    },
    "synthesize": {
        "minimax-plan": lambda a: tts_minimax(a, "minimax-plan"),
        "minimax-payg": lambda a: tts_minimax(a, "minimax-payg"),
        "xiaomi": tts_xiaomi,
        "deepseek": tts_deepseek,
        "qwen-plan": lambda a: tts_qwen(a, "qwen-plan"),
        "qwen-payg": lambda a: tts_qwen(a, "qwen-payg"),
    },
    "generate_image": {
        "minimax-plan": lambda a: image_minimax(a, "minimax-plan"),
        "minimax-payg": lambda a: image_minimax(a, "minimax-payg"),
        "qwen-plan": image_qwen_plan,
        "qwen-payg": image_qwen_payg,
    },
    "generate_video": {
        "qwen-plan": lambda a: video_qwen(a, base_for("qwen-plan", QWEN_PLAN_BASE), "qwen-plan"),
        "qwen-payg": lambda a: video_qwen(a, base_for("qwen-payg", QWEN_PAYG_BASE), "qwen-payg"),
        "minimax-payg": video_minimax_payg,
    },
}


def _model_rows(payload: dict) -> list[str]:
    """Normalise the known list shapes (OpenAI-style and DashScope-style)."""
    rows = payload.get("data")
    if not isinstance(rows, list):
        output = payload.get("output")
        rows = output.get("models") if isinstance(output, dict) else None
    ids: list[str] = []
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, str):
            value = row
        elif isinstance(row, dict):
            value = row.get("id") or row.get("model") or row.get("name")
        else:
            continue
        if isinstance(value, str) and value.strip():
            ids.append(value.strip())
    seen: set[str] = set()
    unique: list[str] = []
    for value in ids:
        if value not in seen:
            seen.add(value)
            unique.append(value)
    return unique


def list_models_for(provider: str) -> dict:
    """Ask one provider for its catalogue. Free, single-provider, no fallback."""
    if not isinstance(provider, str) or not provider.strip():
        raise TargetError("provider must be a non-empty string", code="bad_request")
    if provider not in MODEL_LIST_PATHS:
        raise TargetError(f"provider {provider} has no model catalogue", provider=provider,
                          code="unsupported_provider")
    if not has(PROVIDER_KEY[provider]):
        raise TargetError(f"provider {provider} has no credentials", provider=provider,
                          code="no_credentials")
    base = base_for(provider, DEFAULT_BASE_BY_PROVIDER[provider])
    failures: list[str] = []
    for path in MODEL_LIST_PATHS[provider]:
        url = base.rstrip("/") + path
        try:
            payload = http_json(url, provider=provider, method="GET", timeout=30,
                                bearer=key(PROVIDER_KEY[provider]))
        except Exception as exc:
            failures.append(f"{path}: {redact(str(exc))[:160]}")
            continue
        models = _model_rows(payload)
        if not models:
            failures.append(f"{path}: empty catalogue")
            continue
        unclaimed = list(models)
        suggested: dict[str, list[str]] = {}
        for capability in ("transcribe", "synthesize", "generate_image", "generate_video"):
            hits = [name for name in unclaimed if any(hint in name.lower() for hint in MODEL_CAPABILITY_HINTS[capability])]
            suggested[capability] = hits
            unclaimed = [name for name in unclaimed if name not in hits]
        note = ("清单按名字归类，一个模型只归入第一个匹配的能力。"
                if provider not in PARTIAL_CATALOGUES else
                "该供应商的清单只含文本模型：媒体模型不在这里出现，不代表不可用。")
        return {"provider": provider, "endpoint": url, "total": len(models),
                "models": models, "suggested": suggested, "note": note}
    raise TargetError(f"{provider} model catalogue unavailable: " + " | ".join(failures),
                      provider=provider, code="model_list_unavailable")


def t_list_models(args: dict) -> dict:
    provider = args.get("provider")
    capability = args.get("capability")
    if provider is not None and (not isinstance(provider, str) or not provider.strip()):
        raise TargetError("provider must be a non-empty string", code="bad_request")
    if capability is not None and capability not in MODEL_CAPABILITY_HINTS:
        raise TargetError("capability must be one of " + ", ".join(MODEL_CAPABILITY_HINTS), code="bad_request")
    if provider:
        result = list_models_for(provider)
        if capability:
            return {"provider": provider, "capability": capability, "total": result["total"],
                    "models": result["suggested"][capability], "note": result["note"]}
        return result
    found: list[dict] = []
    failed: list[dict] = []
    for name in PROVIDER_KEY:
        try:
            result = list_models_for(name)
            result.pop("endpoint", None)
            found.append(result)
        except TargetError as exc:
            if exc.code == "no_credentials":
                continue
            failed.append({"provider": name, "error": redact(str(exc))[:200]})
    return {"providers": found, "failed": failed,
            "note": "只读取各家自己的模型清单，不产生任何费用；失败项按供应商列出。"}


# ---------------------------------------------------------------- tools


def t_check_config(_args: dict) -> dict:
    return {
        "credentials": {name: has(name) for name in KEY_NAMES},
        "chains": {cap: [p for p in chain_for(cap) if has(PROVIDER_KEY[p])]
                   for cap in DEFAULT_CHAINS},
        "config_file": CONFIG_FILE,
        "config_loaded": bool(runtime_config()),
        "output_dir": str(OUT_DIR),
        "note": "chains 列出当前有凭据的候选上游；每次只请求第一个可用目标，不自动重试。",
    }


def t_transcribe(args: dict) -> dict:
    return call_chain("transcribe", args)


def t_synthesize(args: dict) -> dict:
    text = args.get("text", "")
    if not text.strip():
        raise RuntimeError("需要非空 text。")
    if len(text) > 10000:
        raise RuntimeError("text 超出 10000 字符上限，请分段合成。")
    return call_chain("synthesize", args)


def t_generate_image(args: dict) -> dict:
    if not args.get("prompt", "").strip():
        raise RuntimeError("需要非空 prompt。")
    result = call_chain("generate_image", args)
    result["note"] = "云端 URL 会过期，产物已落盘，请只使用这些本地路径。"
    return result


def t_generate_video(args: dict) -> dict:
    if not args.get("prompt", "").strip():
        raise RuntimeError("需要非空 prompt。")
    result = call_chain("generate_video", args)
    result["note"] = "视频要几分钟，请用 job_status 查询结果；产物会自动落盘。"
    return result


def t_job_status(args: dict) -> dict:
    job_id = args.get("job_id")
    if not job_id:
        raise RuntimeError("需要 job_id。")
    known = _jobs.get(job_id, {}).get("provider")
    explicit = args.get("provider")
    if known and explicit and known != explicit:
        raise TargetError("provider does not match the task owner", provider=explicit,
                          code="provider_mismatch")
    provider = known or explicit
    if provider not in DISPATCH["generate_video"]:
        raise TargetError("Unknown task: specify its video provider", provider=provider,
                          code="unknown_task")
    if not has(PROVIDER_KEY[provider]):
        raise TargetError("Task provider has no credentials", provider=provider,
                          code="no_credentials")
    try:
        if provider == "minimax-payg":
            result = job_minimax_v2(job_id, provider)
        else:
            base = base_for(provider, QWEN_PLAN_BASE if provider == "qwen-plan" else QWEN_PAYG_BASE)
            result = job_qwen(job_id, base, provider)
    except Exception as exc:
        raise TargetError(redact(str(exc)), provider=provider, retryable=isinstance(exc, Retryable),
                          code="upstream_failed") from None
    result["provider"] = provider
    result["note"] = "云端产物 24 小时过期，成功后已下载到本地。"
    return result



TOOLS = [
    {
        "name": "check_config",
        "description": "Report which credential references are configured, the eligible provider order for each capability, and where generated files are saved. Returns booleans only, never key values.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "transcribe",
        "description": "Turn one local audio file into text. One selected provider/model is called; errors do not trigger fallback. DeepSeek needs 16-bit PCM WAV (8000-48000 Hz) or Ogg Opus, max 8 MiB and 120 s; other providers accept mp3/wav/ogg directly and remaining formats need ffmpeg.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "audio_path": {"type": "string", "description": "Absolute path of a local audio file."},
                "language": {"type": "string", "description": "Optional BCP-47 language hint. Xiaomi MiMo ASR accepts auto/zh/en; MiniMax asr-1.0 accepts zh, yue, ja, ko, th, vi, id, ms, fil, en, fr, de, es, it, pt, pl, ru, uk, ar, tr and reads it as a header. DeepSeek and Qwen ignore it."},
                "response_format": {"type": "string", "enum": ["json", "verbose_json", "srt", "vtt"], "description": "MiniMax asr-1.0 only. verbose_json adds n_speakers and per-segment speaker and timing; srt and vtt are saved as a local subtitle file and returned as subtitle_path. Other providers ignore it."},
            },
            "required": ["audio_path"], "additionalProperties": False,
        },
    },
    {
        "name": "synthesize",
        "description": "Turn text into speech and save a local audio file. One selected provider/model is called; errors do not trigger fallback. Voice IDs are provider-specific.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to speak, at most 10000 characters."},
                "voice": {"type": "string", "description": "Provider voice id (MiniMax ids like female-shaonv; Xiaomi's mimo_default; Qwen's Mia). Omitted = provider default."},
                "instruction": {"type": "string", "description": "Qwen-Audio-TTS and CosyVoice only: natural-language control over language, emotion or pace, e.g. 请用河南话表达。 CosyVoice counts CJK ideographs as 2 characters and caps the result at 100. Rejected on the Qwen3-TTS family, which has no instruct support."},
                                "language_hints": {"type": "array", "items": {"type": "string"}, "description": "Qwen-Audio-TTS and CosyVoice only: target language for reading numbers, abbreviations and symbols (zh/en/fr/de/ja/ko/ru/pt/th/id/vi/es/it/ms/fil/ar, plus other for qwen-audio-3.1-tts-flash). Pass exactly one value: the vendor processes only the first array element."},
                "hot_fix": {"type": "object", "description": "Qwen-Audio-TTS and CosyVoice only: per-word pronunciation and text replacement applied before synthesis, e.g. {\"pronunciation\": [\"...\"] } shaped as {\"pronunciation\": [{\"天气\": \"tian1 qi4\"}], \"replace\": [{\"今天\": \"金天\"}]}. Use it to fix proper nouns."},
                "volume": {"type": "integer", "description": "Qwen-Audio-TTS and CosyVoice only: 0-100, default 50. Ignored (and reported in ignored_params) on Qwen3-TTS."},
                "rate": {"type": "number", "description": "Qwen-Audio-TTS and CosyVoice only: speaking rate 0.5-2.0, default 1.0. Ignored (and reported in ignored_params) on Qwen3-TTS."},
                "ssml": {"type": "boolean", "description": "Qwen-Audio-TTS and CosyVoice only: interpret <speak> markup (pauses, rate, pronunciation). Auto-enabled when text contains <speak>, because sending the markup without the flag makes the service read the tags aloud."},
                "emotion": {"type": "string", "description": "MiniMax only: named delivery emotion; the docs example is \"happy\". Unknown values surface as MiniMax's own error rather than being rewritten."},
                "text_normalization_mode": {"type": "string", "enum": ["basic", "quality"], "description": "MiniMax only: how numbers, dates and currency are spoken. quality adds a 30-100 ms LLM pass and is documented for speech-2.8/2.6 non-stream; other models fall back to basic upstream."},
                "format": {"type": "string", "enum": ["mp3", "pcm", "flac", "wav"], "default": "mp3", "description": "MiniMax accepts mp3/pcm/flac/wav and pcm is wrapped into a WAV locally. Qwen-Audio-TTS and CosyVoice accept wav/mp3/pcm/opus; anything else is rejected before the call. Qwen3-TTS ignores this argument and the result reports it in ignored_params."},
                "sample_rate": {"type": "integer", "enum": [8000, 12000, 16000, 22050, 24000, 32000, 44100, 48000], "default": 32000, "description": "MiniMax output sample rate."},
                "speed": {"type": "number", "minimum": 0.5, "maximum": 2.0, "default": 1.0, "description": "MiniMax speaking speed."},
                "voice_description": {"type": "string", "description": "Voice design prompt for mimo-v2.5-tts-voicedesign; required with that model, ignored by others."},
                "voice_reference_path": {"type": "string", "description": "Absolute path to a local wav/mp3/flac/ogg sample for mimo-v2.5-tts-voiceclone."},
            },
            "required": ["text"], "additionalProperties": False,
        },
    },
    {
        "name": "generate_image",
        "description": "Generate images and save local paths. One selected provider/model is called; errors do not trigger fallback.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Image description, or the edit instruction when images are given."},
                "images": {"type": "array", "items": {"type": "string"}, "description": "MiniMax H3-Image only: 1-16 reference pictures, so the call goes to /v1/images/edits instead of generating from text. Each item is a local path (sent as a data URI), an http(s) URL, or an mm_file:// reference. Request body stays under 64 MiB; per file 128-8192 px and ratio within 4:1."},
                "size": {"type": "string", "default": "1024x1024", "description": "WidthxHeight. MiniMax H3-Image takes it directly (area 655,360-20,000,000 px, ratio within 1:3-3:1) and then rejects aspect_ratio; legacy MiniMax maps it to the nearest aspect ratio; Qwen uses it directly."},
                "aspect_ratio": {"type": "string", "enum": ["1:1", "16:9", "4:3", "3:2", "2:3", "3:4", "9:16", "21:9"], "description": "MiniMax aspect ratio; takes precedence over size."},
                "n": {"type": "integer", "minimum": 1, "maximum": 9, "default": 1, "description": "Number of images."},
            },
            "required": ["prompt"], "additionalProperties": False,
        },
    },
    {
        "name": "generate_video",
        "description": "Submit one asynchronous video task without automatic fallback. Query its provider with job_status.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Video description."},
                "duration": {"type": "integer", "minimum": -1, "maximum": 30, "default": 5, "description": "Seconds. MiniMax H3 accepts 4-15 (H3-Max 5-15); -1 lets Qwen pick."},
                "resolution": {"type": "string", "enum": ["480P", "720P", "768P", "1080P", "2K"], "default": "720P", "description": "Qwen accepts 480P/720P/1080P; MiniMax H3 accepts 768P/2K, while H3-Lite/Max/Highspeed offer only 480P/768P."},
                "ratio": {"type": "string", "enum": ["21:9", "16:9", "4:3", "1:1", "3:4", "9:16"], "description": "Qwen aspect ratio; omitted by default."},
            },
            "required": ["prompt"], "additionalProperties": False,
        },
    },
    {
        "name": "list_models",
        "description": "Fetch each provider's own model catalogue with a free listing request (never a generation). Omit provider to query every provider that has credentials; pass one provider to get its ids plus a suggested grouping per capability, then add the new models to the Settings order.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "provider": {"type": "string", "enum": ["minimax-plan", "minimax-payg", "qwen-plan", "qwen-payg", "xiaomi", "deepseek"], "description": "Optional; omit to query all configured providers."},
                "capability": {"type": "string", "enum": ["transcribe", "synthesize", "generate_image", "generate_video"], "description": "Optional; with a provider, returns only the models suggested for that capability."},
            },
            "required": [], "additionalProperties": False,
        },
    },
    {
        "name": "job_status",
        "description": "Query only the task owner; for unknown tasks specify provider. Downloads successful results locally.",
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string", "description": "task_id returned by generate_video."}, "provider": {"type": "string", "enum": ["qwen-plan", "qwen-payg", "minimax-payg"], "description": "Required for tasks unknown to this server process."}},
            "required": ["job_id"], "additionalProperties": False,
        },
    },
]

# 音色复刻与设计。只有 create_voice 与 query_voice 有公开文档依据，
# 列表和删除接口没有照抄的样本，所以不凭猜测实现；要清理去控制台。
VOICE_ENDPOINT = "/api/v1/services/audio/tts/customization"
VOICE_ACTIONS = ("create_clone", "create_design", "status", "list", "delete")
VOICE_PROVIDERS = ("qwen-plan", "qwen-payg")
VOICE_CLONE_TYPES = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4"}
VOICE_CLONE_MAX_BYTES = 10 * 1024 * 1024
VOICE_CLONE_MAX_SECONDS = 60
VOICE_CLONE_MIN_RATE = 16000
VOICE_PROMPT_MAX = 500
VOICE_PREVIEW_RANGE = (15, 200)

TOOLS.append({
    "name": "voice_manage",
    "description": ("Create or query a custom Qwen voice. create_clone needs a 10-20 s (max 60 s) clean "
                    "speech sample as wav/mp3/m4a of at least 16 kHz; create_design takes a text description "
                    "instead of audio and returns a preview clip. Creation is free; synthesizing with the "
                    "resulting voice bills as the target model does. The voice is bound to target_model, so "
                    "synthesize must pass the same model. Qwen keeps at most 1000 custom voices and deletes "
                    "any unused for a year; MiMo cloning already exists on synthesize for free, and MiniMax "
                    "cloning is intentionally not wired because it charges a 9.9 CNY unlock. "
                    "Verified live: the token-plan tier does not carry voice-enrollment at all "
                    "(it answers Model not exist), so create voices with provider=qwen-payg."),
    "inputSchema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(VOICE_ACTIONS), "description": "list and status are free to call; delete_voice follows the documented action naming but has not been exercised by a live call."},
            "provider": {"type": "string", "enum": list(VOICE_PROVIDERS),
                         "description": "Which Qwen endpoint owns the voice; omitted means qwen-plan."},
            "target_model": {"type": "string",
                             "description": "Required when creating: the synthesis model this voice will be used with."},
            "prefix": {"type": "string", "description": "Voice name prefix, e.g. announcer."},
            "audio_path": {"type": "string", "description": "create_clone: absolute path to a local wav/mp3/m4a sample."},
            "audio_url": {"type": "string", "description": "create_clone: public URL, used instead of audio_path."},
            "voice_prompt": {"type": "string",
                             "description": "create_design: up to 500 characters describing the voice (gender, age, pitch, pace, emotion, texture, use case). Chinese or English."},
            "preview_text": {"type": "string",
                             "description": "create_design: 15-200 characters the preview clip reads aloud; Chinese or English only."},
            "voice_id": {"type": "string", "description": "status/delete: a voice id from create or list."},
            "page_size": {"type": "integer", "description": "list only: entries per page, 1-1000, default 50."},
            "page_index": {"type": "integer", "description": "list only: page index, from 0."},
            "language_hints": {"type": "array", "items": {"type": "string"},
                               "description": "create_design: source language of the described voice, zh or en."},
        },
        "required": ["action"],
        "additionalProperties": False,
    },
})


def voice_request(provider: str, body: dict, target_model: str = "") -> dict:
    base = base_for(provider, QWEN_PLAN_BASE if provider == "qwen-plan" else QWEN_PAYG_BASE)
    try:
        return http_json(base + VOICE_ENDPOINT, provider=provider, method="POST", body=body,
                         timeout=180, bearer=key(PROVIDER_KEY[provider]))
    except Exception as exc:
        # Verified live: the token-plan tier does not carry voice-enrollment and answers
        # a bare 404 "Model not exist", which otherwise reads as an anonymous failure.
        if provider == "qwen-plan" and "Model not exist" in str(exc):
            raise TargetError("千问套餐端不含 voice-enrollment（Model not exist）；音色复刻与设计请改用 "
                              "provider=qwen-payg", provider=provider, model=target_model or None,
                              code="provider_mismatch") from None
        raise


def t_voice_manage(args: dict) -> dict:
    action = (args.get("action") or "").strip()
    if action not in VOICE_ACTIONS:
        raise TargetError(f"action 只能是 {'/'.join(VOICE_ACTIONS)}", code="bad_request")
    provider = (args.get("provider") or "qwen-plan").strip()
    if provider not in VOICE_PROVIDERS:
        raise TargetError("音色复刻与设计只接了千问两家：小米的复刻在 synthesize 里免费可用，"
                          "MiniMax 复刻首次使用要扣 9.9 元解锁费，所以没有接进来",
                          provider=provider, code="unsupported_provider")
    if not has(PROVIDER_KEY[provider]):
        raise TargetError(f"provider {provider} has no credentials", provider=provider, code="no_credentials")
    model = (args.get("target_model") or "").strip()
    if action == "status":
        voice_id = (args.get("voice_id") or "").strip()
        if not voice_id:
            raise TargetError("status 需要 voice_id", provider=provider, code="bad_request")
        result = voice_request(provider, {"model": "voice-enrollment",
                                          "input": {"action": "query_voice", "voice_id": voice_id}},
                                 args.get("target_model") or "")
        out = result.get("output") or {}
        return {"voice_id": voice_id, "status": out.get("status") or out.get("voice_status"),
                "provider": provider, "raw": {k: out[k] for k in out if k != "preview_audio"}}
    if action == "list":
        # Free and documented: this is how you find the voice ids you own.
        page_size = int(args.get("page_size") or 50)
        if not 1 <= page_size <= 1000:
            raise TargetError("page_size 取值范围是 1~1000", provider=provider, code="bad_request")
        listing = voice_request(provider, {"model": "voice-enrollment", "input": {
            "action": "list_voice", "prefix": (args.get("prefix") or "").strip(),
            "page_size": page_size, "page_index": int(args.get("page_index") or 0)}})
        voices = ((listing.get("output") or {}).get("voice_list")) or []
        return {"voices": [{"voice_id": one.get("voice_id"), "status": one.get("status"),
                            "gmt_create": one.get("gmt_create"), "gmt_modified": one.get("gmt_modified")}
                           for one in voices if isinstance(one, dict)],
                "count": len(voices), "provider": provider}
    if action == "delete":
        doomed = (args.get("voice_id") or "").strip()
        if not doomed:
            raise TargetError("delete 需要 voice_id", provider=provider, code="bad_request")
        voice_request(provider, {"model": "voice-enrollment",
                                 "input": {"action": "delete_voice", "voice_id": doomed}},
                      args.get("target_model") or "")
        return {"deleted": doomed, "provider": provider}

    if not model:
        raise TargetError("创建音色必须指定 target_model：复刻与设计音色绑定模型，"
                          "合成时换模型会直接失败", provider=provider, code="bad_request")
    prefix = (args.get("prefix") or "").strip()
    if not prefix:
        raise TargetError("需要一个 prefix 作为音色名前缀", provider=provider, model=model, code="bad_request")
    input_fields: dict = {"action": "create_voice", "target_model": model, "prefix": prefix}
    saved_preview = None
    if action == "create_clone":
        audio = (args.get("audio_url") or "").strip()
        if not audio:
            path = load_audio_arg(args, VOICE_CLONE_MAX_BYTES)
            mime = VOICE_CLONE_TYPES.get(path.suffix.lower())
            if mime is None:
                raise TargetError("复刻样本只支持 wav / mp3 / m4a（官方限制）", provider=provider, model=model,
                                  code="bad_request")
            data = path.read_bytes()
            if path.suffix.lower() == ".wav":
                facts = _wav_facts(data)
                if facts.get("sample_rate") and int(facts["sample_rate"]) < VOICE_CLONE_MIN_RATE:
                    raise TargetError(f"样本采样率 {facts['sample_rate']} Hz 低于复刻要求的 {VOICE_CLONE_MIN_RATE} Hz",
                                      provider=provider, model=model, code="bad_request")
                if facts.get("seconds") and facts["seconds"] > VOICE_CLONE_MAX_SECONDS:
                    raise TargetError(f"样本约 {facts['seconds']:.0f} 秒，超过复刻上限 {VOICE_CLONE_MAX_SECONDS} 秒"
                                      "（推荐 10~20 秒）", provider=provider, model=model, code="bad_request")
            audio = "data:" + mime + ";base64," + base64.b64encode(data).decode("ascii")
        input_fields["audio"] = audio
    else:
        prompt = (args.get("voice_prompt") or "").strip()
        if not prompt:
            raise TargetError("create_design 需要 voice_prompt（想要什么样的声音）", provider=provider,
                              model=model, code="bad_request")
        if len(prompt) > VOICE_PROMPT_MAX:
            raise TargetError(f"voice_prompt 最长 {VOICE_PROMPT_MAX} 字符，当前 {len(prompt)}",
                              provider=provider, model=model, code="bad_request")
        preview = (args.get("preview_text") or "").strip()
        low, high = VOICE_PREVIEW_RANGE
        if not low <= len(preview) <= high:
            raise TargetError(f"preview_text 长度需在 {low}~{high} 字符之间，当前 {len(preview)}",
                              provider=provider, model=model, code="bad_request")
        input_fields.update({"voice_prompt": prompt, "preview_text": preview,
                             "response_format": "wav", "sample_rate": 24000})
        hints = args.get("language_hints")
        if hints:
            if not isinstance(hints, list) or len(hints) != 1 or str(hints[0]).strip() not in ("zh", "en"):
                raise TargetError("language_hints 只接受一个值，且只能是 zh 或 en", provider=provider,
                                  model=model, code="bad_request")
            input_fields["language_hints"] = [str(hints[0]).strip()]
    result = voice_request(provider, {"model": "voice-enrollment", "input": input_fields}, model)
    out = result.get("output") or {}
    voice_id = out.get("voice_id") or out.get("voice")
    if not voice_id:
        raise RuntimeError(redact(f"{provider} 创建音色未返回 voice_id: {json.dumps(result, ensure_ascii=False)[:300]}"))
    preview_audio = ((out.get("preview_audio") or {}).get("data")) or ""
    if preview_audio:
        saved_preview = str(save_bytes(base64.b64decode(preview_audio), "voice_preview", "wav"))
    # status is DEPLOYING right after creation; the documented poll loop waits for OK
    status = None
    for _ in range(30):
        checked = voice_request(provider, {"model": "voice-enrollment",
                                           "input": {"action": "query_voice", "voice_id": voice_id}}, model)
        status = ((checked.get("output") or {}).get("status"))
        if status in (None, "OK", "DEPLOYED"):
            break
        if status not in ("DEPLOYING", "PROCESSING"):
            break
        time.sleep(1)
    out_fields = {"voice_id": voice_id, "target_model": model, "provider": provider, "status": status}
    if saved_preview:
        out_fields["preview_path"] = saved_preview
    return out_fields


TOOL_CAPABILITY = {
    "check_config": None,
    "transcribe": "transcribe",
    "synthesize": "synthesize",
    "generate_image": "generate_image",
    "generate_video": "generate_video",
    "job_status": "generate_video",
    "list_models": None,
    "voice_manage": None,
}

HANDLERS = {
    "check_config": t_check_config,
    "transcribe": t_transcribe,
    "synthesize": t_synthesize,
    "generate_image": t_generate_image,
    "generate_video": t_generate_video,
    "job_status": t_job_status,
    "list_models": t_list_models,
    "voice_manage": t_voice_manage,
}

for tool in TOOLS:
    if tool["name"] in ("transcribe", "synthesize", "generate_image", "generate_video"):
        props = tool["inputSchema"]["properties"]
        props["provider"] = {"type": "string", "enum": list(DISPATCH[tool["name"]]),
                             "description": "Explicit provider; omitted selects the first available provider in the configured chain."}
        props["model"] = {"type": "string", "description": "Explicit model for the selected provider. DeepSeek endpoints do not support model selection; MiniMax video accepts H3 models only."};
        if tool["name"] == "generate_video":
            props["model"]["enum"] = ["wan3.0-video", "MiniMax-H3", "MiniMax-H3-Highspeed", "MiniMax-H3-Lite", "MiniMax-H3-Max"]
        if tool["name"] == "generate_image":
            props["model"]["enum"] = ["image-01", "MiniMax-H3-Image", "MiniMax-H3-Image-Highspeed",
                                      "qwen-image-3.0-pro", "qwen-image-2.1-pro", "z-image-turbo"]
            props["area"] = {"type": "string", "enum": list(MINIMAX_H3_AREAS),
                             "description": "MiniMax H3-Image output area tier when size is auto; ignored elsewhere."}

INSTRUCTIONS = (
    "Each media call selects one provider and model, using the first available configured target by default. "
    "Failures are returned without automatic retries or fallback; an agent may decide whether to retry. "
    "Generated files are saved locally. Settings control provider order, models, voices and endpoints. "
    "list_models reads each provider\u0027s catalogue for free so newly released models can be added."
)


def tool_available(name: str) -> bool:
    capability = TOOL_CAPABILITY.get(name)
    if capability is None:
        return True
    return any(p in DISPATCH[capability] and has(PROVIDER_KEY[p]) for p in chain_for(capability))


def result(req_id, payload: dict) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": payload}


def error(req_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": redact(message)}}


def handle(msg: dict) -> dict | None:
    method = msg.get("method")
    req_id = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        return result(req_id, {
            "protocolVersion": params.get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "media-bridge", "version": VERSION},
            "instructions": INSTRUCTIONS,
        })
    if method == "ping":
        return result(req_id, {})
    if method == "tools/list":
        return result(req_id, {"tools": list(TOOLS)})
    if method == "resources/list":
        return result(req_id, {"resources": []})
    if method == "prompts/list":
        return result(req_id, {"prompts": []})
    if method == "tools/call":
        name = params.get("name")
        handler = HANDLERS.get(name)
        if handler is None:
            return error(req_id, -32601, f"未知工具: {name}")

        try:
            payload = handler(params.get("arguments") or {})
            text = json.dumps(payload, ensure_ascii=False)
            return result(req_id, {"content": [{"type": "text", "text": text}], "isError": False})
        except Exception as exc:
            detail = {"error": {"type": type(exc).__name__, "code": getattr(exc, "code", "upstream_failed"), "message": redact(str(exc)),
                                "provider": getattr(exc, "provider", None),
                                "model": getattr(exc, "model", None),
                                "retryable": getattr(exc, "retryable", False)}}
            return result(req_id, {
                "content": [{"type": "text", "text": json.dumps(detail, ensure_ascii=False)}],
                "isError": True,
            })
    if method and method.startswith("notifications/"):
        return None
    if req_id is None:
        return None
    return error(req_id, -32601, f"未实现的方法: {method}")


def main() -> None:
    chains = {cap: list(chain_for(cap)) for cap in DEFAULT_CHAINS}
    log(f"started, output_dir={OUT_DIR}, chains={chains}")
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    for raw in stdin:
        try:
            line = raw.decode("utf-8").strip()
        except UnicodeDecodeError:
            continue
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle(msg)
        if response is not None:
            stdout.write(json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n")
            stdout.flush()


if __name__ == "__main__":
    main()
