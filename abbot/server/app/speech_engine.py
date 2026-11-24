"""
05 §2.2 `app/speech_engine.py`：纯云端 ASR 识别与纯云端 TTS 合成。

铁律（05 §4.4）：
* **零本地合成** —— 严禁在树莓派运行本地 TTS 模型或爬虫脚本；
* 强制输出 `16000Hz / 16bit / Mono` WAV；
* **编码避坑**：非流式接口返回 `Hex` 字符串，必须 `bytes.fromhex()` 解码
  （严禁误用 Base64 解码 —— 05 §4.4 明确点名这个坑）；
* **缓存键** `tts_cache_key = md5(f"{provider}:{voice_id}:wav16k:{speech_text}")`，
  缓存至 `storage/cache/tts/<tts_cache_key>.wav`；
* **静默降级**：TTS 超时（4 秒硬超时）或限流时**不报错**，置 `has_audio: false`
  下发纯文本，设备端播放本地 `ding.wav`。

厂商与模型名一律来自 `config.yaml`，**严禁硬编码**。
"""

import hashlib
import io
import struct
import wave
from typing import Any, Dict, Optional, Tuple

import httpx

from app.config import settings

# 05 §4.4：TTS 硬超时 4 秒；ASR 超时 20 秒（设备端 HTTP 总超时 20s）
TTS_HARD_TIMEOUT = 4.0
ASR_TIMEOUT = 20.0

SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2  # 16bit
CHANNELS = 1  # Mono


class SpeechError(RuntimeError):
    """语音链路失败。`code` 为 05 §3.5 业务状态码（4003 = ASR 失败）。"""

    def __init__(self, message: str, code: int = 4003):
        super().__init__(message)
        self.code = code


def pcm_to_wav(pcm: bytes) -> bytes:
    """
    给裸 PCM 套 44 字节 WAV 头（16kHz / 16bit / Mono）。

    05 §4.1 要求网关在转发 ASR 前自动封装 WAV 头。
    """
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm)
    return buf.getvalue()


def wav_header_only(pcm_len: int) -> bytes:
    """只生成 44 字节 WAV 头（用于流式场景，本模块暂未使用）。"""
    byte_rate = SAMPLE_RATE * SAMPLE_WIDTH * CHANNELS
    block_align = SAMPLE_WIDTH * CHANNELS
    return b"".join(
        [
            b"RIFF",
            struct.pack("<I", 36 + pcm_len),
            b"WAVE",
            b"fmt ",
            struct.pack("<IHHIIHH", 16, 1, CHANNELS, SAMPLE_RATE, byte_rate, block_align, 16),
            b"data",
            struct.pack("<I", pcm_len),
        ]
    )


def tts_cache_key(provider: str, voice_id: str, speech_text: str) -> str:
    """`md5(f"{provider}:{voice_id}:wav16k:{speech_text}")`（05 §4.4 强制格式）。"""
    raw = f"{provider}:{voice_id}:wav16k:{speech_text}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def decode_audio_payload(payload: str, response_format: str) -> bytes:
    """
    按配置解码音频载荷。

    `response_format == "hex"` → `bytes.fromhex()`（**严禁**误用 Base64）；
    `response_format == "base64"` → `base64.b64decode()`；
    `response_format == "url"` → 由调用方下载后传入原始字节。
    """
    fmt = (response_format or "hex").lower()
    if fmt == "hex":
        try:
            return bytes.fromhex(payload.strip())
        except ValueError as exc:
            raise SpeechError(f"TTS 返回不是合法 Hex：{exc}", 5001) from exc
    if fmt == "base64":
        import base64

        try:
            return base64.b64decode(payload)
        except Exception as exc:
            raise SpeechError(f"TTS 返回不是合法 Base64：{exc}", 5001) from exc
    raise SpeechError(f"未知的音频响应格式：{response_format}", 5001)


def ensure_wav_16k_mono(audio_bytes: bytes) -> bytes:
    """
    归一化为 `16kHz / 16bit / Mono` WAV。

    云端可能返回其它采样率或立体声；设备端只认这一种格式。
    """
    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
            channels = wf.getnchannels()
            width = wf.getsampwidth()
            rate = wf.getframerate()
            frames = wf.readframes(wf.getnframes())
    except (wave.Error, EOFError):
        # 不是 WAV —— 原样返回（可能是裸 PCM 或 MP3，交给设备端判断）
        return audio_bytes

    if channels == CHANNELS and width == SAMPLE_WIDTH and rate == SAMPLE_RATE:
        return audio_bytes

    # 立体声 → 单声道：取左声道
    if channels == 2 and width == SAMPLE_WIDTH:
        frames = b"".join(
            frames[i * 4 : i * 4 + 2] for i in range(len(frames) // 4)
        )
    elif width != SAMPLE_WIDTH:
        # 8bit → 16bit 粗略放大
        if width == 1:
            frames = b"".join(struct.pack("<h", (b - 128) * 256) for b in frames)
        else:
            return audio_bytes

    # 采样率重采样（线性插值）
    if rate != SAMPLE_RATE and rate > 0:
        src_samples = len(frames) // SAMPLE_WIDTH
        dst_samples = int(src_samples * SAMPLE_RATE / rate)
        if dst_samples <= 0:
            return audio_bytes
        out = bytearray()
        for i in range(dst_samples):
            src_idx = int(i * rate / SAMPLE_RATE)
            next_idx = min(src_idx + 1, src_samples - 1)
            ratio = (i * rate / SAMPLE_RATE) - src_idx
            s1 = struct.unpack_from("<h", frames, src_idx * SAMPLE_WIDTH)[0]
            s2 = struct.unpack_from("<h", frames, next_idx * SAMPLE_WIDTH)[0]
            out += struct.pack("<h", int(s1 + (s2 - s1) * ratio))
        frames = bytes(out)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(frames)
    return buf.getvalue()


class SpeechEngine:
    """云端 ASR / TTS。厂商与模型名全部来自 config.yaml。"""

    def __init__(self, storage=None):
        # 属性名**不得**与方法名 `_storage` 同名 —— 实例属性会遮蔽方法，
        # 导致 `self._storage()` 变成对 None 调用。
        self._storage_ref = storage

    # ------------------------------------------------------------ 工具
    def _storage(self):
        if self._storage_ref is not None:
            return self._storage_ref
        from app.storage import get_storage

        return get_storage()

    def _api_key(self, provider: str) -> str:
        """API Key 一律来自环境变量（.env 由部署注入），不落配置文件。"""
        import os

        for name in (
            f"{provider.upper()}_API_KEY",
            f"{provider.upper()}_KEY",
            "MINIMAX_API_KEY",
        ):
            val = os.environ.get(name, "")
            if val:
                return val
        return ""

    # ------------------------------------------------------------ ASR
    def transcribe(self, pcm_base64: str) -> str:
        """
        云端 ASR 转写。失败抛 `SpeechError(code=4003)`。

        `pcm_base64` 是设备上传的 `16kHz/16bit/Mono` 裸 PCM 的 Base64。
        """
        import base64

        cfg = settings.speech.get("asr", {}) or {}
        if not cfg.get("enabled", False):
            raise SpeechError("语音识别服务尚未启用", 4003)
        provider = cfg.get("provider", "")
        base_url = (cfg.get("base_url") or "").rstrip("/")
        path = cfg.get("path") or "/v1/audio/transcriptions"
        model = cfg.get("model", "")
        if not base_url or not model:
            raise SpeechError("语音识别服务配置不完整", 4003)
        api_key = self._api_key(provider)
        if not api_key:
            raise SpeechError("语音识别服务缺少密钥", 4003)

        try:
            pcm = base64.b64decode(pcm_base64, validate=True)
        except Exception as exc:
            raise SpeechError(f"音频数据损坏：{exc}", 4003) from exc
        if len(pcm) < SAMPLE_RATE * SAMPLE_WIDTH // 2:  # 短于 0.5 秒
            raise SpeechError("录音太短，没有听清", 4003)

        wav = pcm_to_wav(pcm)
        timeout = float(cfg.get("timeout_seconds", ASR_TIMEOUT))
        headers = {"Authorization": f"Bearer {api_key}"}
        language = (cfg.get("language") or "").strip()
        if language:
            headers["language"] = language
        try:
            with httpx.Client(timeout=timeout, trust_env=False) as client:
                resp = client.post(
                    f"{base_url}{path}",
                    headers=headers,
                    files={"file": ("audio.wav", wav, "audio/wav")},
                    data={"model": model, "response_format": "json"},
                )
        except httpx.TimeoutException:
            raise SpeechError("语音识别超时了", 4003) from None
        except httpx.HTTPError as exc:
            raise SpeechError(f"语音识别请求失败：{exc}", 4003) from exc

        if resp.status_code >= 400:
            raise SpeechError(f"语音识别服务返回错误：{resp.status_code}", 4003)
        try:
            data = resp.json()
        except ValueError:
            raise SpeechError("语音识别返回内容无法解析", 4003) from None
        base = data.get("base_resp") or {}
        if base.get("status_code") not in (None, 0):
            raise SpeechError("语音识别服务暂时不可用", 4003)
        text = (data.get("text") or "").strip()
        if not text:
            raise SpeechError("没有听清，再说一次好吗", 4003)
        return text

    # ------------------------------------------------------------ TTS
    def synthesize(self, speech_text: str) -> Tuple[Optional[str], Optional[bytes]]:
        """
        云端 TTS 合成。

        返回 `(tts_cache_key, wav_bytes)`：
        * 命中缓存 → 直接返回缓存键与音频字节；
        * 云端超时/限流/出错 → **静默降级**，返回 `(None, None)`（**不抛异常**），
          调用方置 `has_audio: false`；
        * 缓存键无论成败都返回（便于设备端对账），但无音频时为 None。

        注意：返回的 `tts_cache_key` 是**纯文本的键**，不含音频内容 ——
        设备端用同一公式本地算键命中自己的缓存池。
        """
        provider = (settings.speech.get("tts", {}) or {}).get("provider", "")
        voice_id = (settings.speech.get("tts", {}) or {}).get("voice_id", "")
        key = tts_cache_key(provider, voice_id, speech_text)

        cached = self._storage().tts_cache_hit(key)
        if cached is not None:
            return key, cached

        cfg = settings.speech.get("tts", {}) or {}
        if not cfg.get("enabled", False):
            return key, None
        base_url = (cfg.get("base_url") or "").rstrip("/")
        model = cfg.get("model", "")
        if not base_url or not model:
            return key, None
        api_key = self._api_key(provider)
        if not api_key:
            return key, None

        body = {
            "model": model,
            "text": speech_text,
            "stream": False,
            "voice_setting": {
                "voice_id": voice_id,
                "speed": 1.0,
                "vol": 1.0,
                "pitch": 0,
            },
            "audio_setting": {
                "sample_rate": SAMPLE_RATE,
                "bitrate": 32000,
                "format": "wav",
                "channel": CHANNELS,
            },
        }
        tts_path = cfg.get("path") or "/v1/t2a_v2"
        try:
            with httpx.Client(timeout=TTS_HARD_TIMEOUT, trust_env=False) as client:
                resp = client.post(
                    f"{base_url}{tts_path}",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=body,
                )
        except (httpx.TimeoutException, httpx.HTTPError) as exc:
            print(f"[tts] 云端不可用，静默降级：{exc}")
            return key, None

        if resp.status_code >= 400:
            print(f"[tts] 服务返回错误 {resp.status_code}，静默降级")
            return key, None
        try:
            data = resp.json()
        except ValueError:
            return key, None

        # MiniMax 风格：数据在 data.audio（Hex 字符串）
        payload = ""
        base_resp = data.get("base_resp") or {}
        if base_resp.get("status_code") not in (None, 0):
            print(f"[tts] 上游报错 {base_resp}")
            return key, None
        if isinstance(data.get("data"), dict):
            payload = data["data"].get("audio") or ""
        payload = payload or data.get("audio") or ""
        if not payload:
            return key, None

        try:
            audio = decode_audio_payload(payload, cfg.get("response_format", "hex"))
        except SpeechError as exc:
            print(f"[tts] {exc}")
            return key, None

        audio = ensure_wav_16k_mono(audio)
        self._storage().store_tts_cache(key, audio)
        return key, audio


_engine: Optional[SpeechEngine] = None


def get_speech_engine() -> SpeechEngine:
    global _engine
    if _engine is None:
        _engine = SpeechEngine()
    return _engine
