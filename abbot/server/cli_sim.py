#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
05 §2.2 `cli_sim.py`：**内置控制台设备模拟器**。

在终端里扮演一台 ESP32 设备，直连本机网关，验证端云链路：

    1. 配对（明文 JSON 换密钥）
    2. 拍题图片上传 → 阶梯解析 JSON
    3. 长按语音追问音频上传 → 意图分流结果
    4. 课表拉取
    5. 设备状态上报（心跳同步）
    6. 固件升级检查

设备侧严格按契约实现：
* 每次请求独立生成随机 16 字节 `IV`，AES-CTR 计数器从 0 起（05 §3.2）；
* 签名串 `METHOD\\nPATH\\nX-Timestamp\\nSHA256(raw_body)`（05 §3.2）；
* 响应先验 `X-Resp-Signature` 再解密（05 §3.2）。

用法（从 abbot/ 目录）：
    ./.venv/bin/python server/cli_sim.py --base http://127.0.0.1:18843
    ./.venv/bin/python server/cli_sim.py --all
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import struct
import sys
import time
import wave
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import httpx

try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
except ImportError:
    sys.exit("缺少依赖 cryptography")


class SimDevice:
    """模拟一台 ESP32-S3 设备。"""

    def __init__(self, base_url: str, device_id: str = "esp32s3_sim0001"):
        self.base_url = base_url.rstrip("/")
        self.device_id = device_id
        self.secret: Optional[bytes] = None
        self.client = httpx.Client(timeout=90.0, trust_env=False)  # 本机回环必须绕开出口代理

    # ------------------------------------------------------------ 加密信封
    def _aes_key(self) -> bytes:
        return self.secret[:16]

    def encrypt(self, obj: Any, iv: Optional[bytes] = None) -> str:
        """业务明文 → `Base64(IV||CT)`。IV 每次独立随机（生产路径口径）。"""
        if iv is None:
            iv = os.urandom(16)
        plain = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        enc = Cipher(algorithms.AES(self._aes_key()), modes.CTR(iv)).encryptor()
        ct = enc.update(plain) + enc.finalize()
        return base64.b64encode(iv + ct).decode("ascii")

    def decrypt(self, envelope_b64: str) -> Any:
        raw = base64.b64decode(envelope_b64)
        dec = Cipher(algorithms.AES(self._aes_key()), modes.CTR(raw[:16])).decryptor()
        return json.loads((dec.update(raw[16:]) + dec.finalize()).decode("utf-8"))

    # ------------------------------------------------------------ 签名
    def sign(self, method: str, path: str, ts: str, raw_body: bytes) -> str:
        body_sha = hashlib.sha256(raw_body).hexdigest()
        msg = f"{method}\n{path}\n{ts}\n{body_sha}".encode("utf-8")
        return hmac.new(self.secret, msg, hashlib.sha256).hexdigest()

    def _headers(self, method: str, path: str, raw_body: bytes, ts: str) -> Dict[str, str]:
        """
        构造签名请求头。

        `ts` **必须由调用方传入** —— 签名与响应验签必须用**同一个** `X-Timestamp`：
        服务端按 05 §3.2 用请求头里的原值计算 `X-Resp-Signature`，
        客户端若用另一个时间戳验签，必然差 1 秒而失败。
        """
        return {
            "X-Device-Id": self.device_id,
            "X-Timestamp": ts,
            "X-Signature": self.sign(method, path, ts, raw_body),
            "Content-Type": "application/json",
        }

    def _verify_resp(self, resp: httpx.Response, ts: str) -> None:
        """
        先验响应签名，再解密 —— 顺序不能反（05 §3.2）。

        签名头缺失时的处理：05 §3.2 要求「任何业务响应」都带 `X-Resp-Signature`，
        但**请求鉴权就失败**的响应网关无从签名（此时还没确定用哪把密钥）。
        因此规则是：
          * 有签名头 → 必须验过，否则网关不可信；
          * 无签名头 → 只接受错误信封（`code != 0`）；成功信封缺签名一律判失败。
        """
        sig = resp.headers.get("X-Resp-Signature", "")
        if not sig:
            try:
                code = resp.json().get("code")
            except ValueError:
                raise RuntimeError("响应既无签名又非合法 JSON") from None
            if code == 0:
                raise RuntimeError("成功响应缺少响应签名 —— 网关不可信")
            return
        expect = hmac.new(
            self.secret,
            f"{ts}\n{hashlib.sha256(resp.content).hexdigest()}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(sig, expect):
            raise RuntimeError("响应验签失败 —— 网关不可信")

    # ------------------------------------------------------------ 请求
    def _post_encrypted(self, path: str, payload: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        body = json.dumps(
            {"payload": self.encrypt(payload)}, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        ts = str(int(time.time()))
        headers = self._headers("POST", path, body, ts)
        resp = self.client.post(f"{self.base_url}{path}", content=body, headers=headers)
        if resp.status_code != 200:
            return resp.status_code, {"code": -1, "msg": resp.text[:200]}
        self._verify_resp(resp, ts)
        env = resp.json()
        if env.get("code") != 0:
            return resp.status_code, {"code": env.get("code"), "msg": env.get("msg")}
        data = self.decrypt(env["payload"]) if env.get("payload") else {}
        return resp.status_code, data

    def _get_signed(self, path: str) -> Tuple[int, Dict[str, Any]]:
        """
        GET 请求（签名）。

        **签名用的 PATH 必须是纯路径**（05 §3.2：「PATH 为不含域名的纯路径」）——
        不得包含 query string，否则两端拼出的签名串不一致，必然验签失败。
        """
        raw = b""
        ts = str(int(time.time()))
        pure_path = path.split("?", 1)[0]
        headers = self._headers("GET", pure_path, raw, ts)
        resp = self.client.get(f"{self.base_url}{path}", headers=headers)
        if resp.status_code != 200:
            return resp.status_code, {"code": -1, "msg": resp.text[:200]}
        self._verify_resp(resp, ts)
        env = resp.json()
        if env.get("code") != 0:
            return resp.status_code, {"code": env.get("code"), "msg": env.get("msg")}
        data = self.decrypt(env["payload"]) if env.get("payload") else {}
        return resp.status_code, data

    # ------------------------------------------------------------ 业务动作
    def pair(self, pair_code: str) -> bool:
        """明文 JSON 配对（此时还没有密钥，无法签名）。"""
        resp = self.client.post(
            f"{self.base_url}/api/v1/pair",
            json={"device_id": self.device_id, "pair_code": pair_code},
        )
        if resp.status_code != 200:
            print(f"  配对失败：HTTP {resp.status_code} {resp.text[:160]}")
            return False
        env = resp.json()
        if env.get("code") != 0:
            print(f"  配对失败：{env.get('msg')}")
            return False
        data = json.loads(env["payload"])
        self.secret = bytes.fromhex(data["device_secret"])
        print(f"  配对成功，device_secret 前 8 位 {data['device_secret'][:8]}…")
        return True

    def solve(self, image_path: Optional[Path], hint_level: int = 0, voice_text: str = "",
              session_id: Optional[str] = None) -> Dict[str, Any]:
        """拍题导学。"""
        image_b64 = ""
        if image_path and image_path.exists():
            image_b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
        payload = {
            "solve_session_id": session_id or f"sess_{int(time.time())}",
            "image_b64": image_b64,
            "edu_stage": "JUNIOR",
            "enrollment_year": 2026,
            "hint_level": hint_level,
            "device_remaining_quota": 8,
        }
        if voice_text:
            payload["voice_text"] = voice_text
        return self._post_encrypted("/api/v1/study/solve", payload)

    def voice(self, wav_path: Optional[Path], context: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """长按语音追问（上传 16kHz/16bit/Mono 音频）。"""
        pcm_b64 = ""
        if wav_path and wav_path.exists():
            with wave.open(str(wav_path), "rb") as wf:
                pcm = wf.readframes(wf.getnframes())
            pcm_b64 = base64.b64encode(pcm).decode("ascii")
        payload = {
            "context_page": "card",
            "context": context or {},
            "pcm_base64": pcm_b64,
            "edu_stage": "JUNIOR",
            "enrollment_year": 2026,
            "local_time": int(time.time()),
        }
        return self._post_encrypted("/api/v1/study/voice", payload)

    def bootstrap(self) -> Dict[str, Any]:
        """心跳同步（课表 / 天气 / 待更新包）。"""
        payload = {
            "device_id": self.device_id,
            "fw_version": "v1.0.0",
            "battery_pct": 78,
            "schedule_updated_at": 0,
            "local_packs": [],
            "local_books": [],
        }
        return self._post_encrypted("/api/v1/sync/bootstrap", payload)

    def ota_check(self, current_ver: str = "v1.0.0") -> Dict[str, Any]:
        return self._get_signed(f"/api/v1/ota/check?current_ver={current_ver}")


def make_tone_wav(path: Path, seconds: float = 1.0) -> Path:
    """生成一段 16kHz/16bit/Mono 测试音频（无真实语音，仅用于链路自检）。"""
    import math

    path.parent.mkdir(parents=True, exist_ok=True)
    rate = 16000
    n = int(rate * seconds)
    frames = bytearray()
    for i in range(n):
        val = int(8000 * math.sin(2 * math.pi * 440 * i / rate))
        frames += struct.pack("<h", val)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(bytes(frames))
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description="abbot 控制台设备模拟器")
    ap.add_argument("--base", default="http://127.0.0.1:18843", help="网关地址")
    ap.add_argument("--device-id", default="esp32s3_sim0001")
    ap.add_argument("--pair-code", default="", help="6 位配对码")
    ap.add_argument("--image", default="", help="题目照片路径")
    ap.add_argument("--audio", default="", help="语音 WAV 路径")
    ap.add_argument("--all", action="store_true", help="跑完整链路")
    args = ap.parse_args()

    dev = SimDevice(args.base, args.device_id)

    print("=" * 62)
    print("abbot 设备模拟器")
    print("=" * 62)

    # ---- 0) 网关连通性 ----
    try:
        r = dev.client.get(f"{args.base}/api/v1/ping")
        print(f"[0] 网关自检 /api/v1/ping -> HTTP {r.status_code} {r.text.strip()}")
    except httpx.HTTPError as exc:
        print(f"[0] 网关连不上：{exc}")
        print(f"    先启动服务端：cd server && uvicorn app.main:app --port {args.base.rsplit(':', 1)[-1]}")
        return 1

    if not args.pair_code:
        print("\n[!] 需要 --pair-code。先在管理台生成 6 位配对码，再回来运行。")
        print("    生成方式：打开 http://127.0.0.1:18843/ → 登录 → 「生成配对码」")
        return 2

    print("\n[1] 配对")
    if not dev.pair(args.pair_code):
        return 1

    print("\n[2] 拍题导学 /api/v1/study/solve")
    img = Path(args.image) if args.image else None
    code, data = dev.solve(img, hint_level=0)
    print(f"  HTTP {code}")
    print(f"  {json.dumps(data, ensure_ascii=False, indent=2)[:900]}")

    print("\n[3] 语音追问 /api/v1/study/voice")
    wav = Path(args.audio) if args.audio else make_tone_wav(Path("/tmp/_abbot_tone.wav"))
    code, data = dev.voice(wav)
    print(f"  HTTP {code}")
    print(f"  {json.dumps(data, ensure_ascii=False, indent=2)[:900]}")

    print("\n[4] 心跳同步 /api/v1/sync/bootstrap")
    code, data = dev.bootstrap()
    print(f"  HTTP {code}")
    print(f"  {json.dumps(data, ensure_ascii=False, indent=2)[:900]}")

    print("\n[5] 固件升级检查 /api/v1/ota/check")
    code, data = dev.ota_check()
    print(f"  HTTP {code}")
    print(f"  {json.dumps(data, ensure_ascii=False, indent=2)[:600]}")

    print("\n" + "=" * 62)
    print("链路自检结束。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
