# -*- coding: utf-8 -*-
"""
设备端网络层桥接（管理台「真机画面」面板用）。

**为什么放在服务端而不是浏览器里**
  * 管理台跑在明文 HTTP 上（``http://ruohan.site/abbot``），浏览器不在安全上下文，
    ``crypto.subtle`` 不可用 —— 设备端要的 HMAC-SHA256 签名与 AES-128-CTR 信封
    在浏览器里根本算不出来。
  * 设备协议必须与固件共用同一份口径，服务端只留一份实现，避免两处漂移。

**但协议是真的**
  * 签名串格式、纯路径口径、加密信封、响应验签全部按 ``05`` §3.2 走；
  * 请求经回环 HTTP 打到本机真实设备 API（``/api/v1/*``），因此配额账本、
    AI 路由、OTA 检查跑的都是生产实现，不是假数据。

浏览器只负责屏显与摇杆状态机 —— 那是设备侧职责。

**必须用异步客户端**
  回环请求打的是同一个 uvicorn 进程。若用同步 ``httpx.Client``，事件循环会被
  阻塞，服务器反而处理不了这个回环请求 —— 直接死锁。WiFi 扫描同理，
  ``subprocess.run`` 会把整个服务卡住十几秒，故一律走 asyncio 子进程。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx

from app import security

# 虚拟设备允许访问的设备端路径白名单（防越权调管理端接口）
ALLOWED_PATHS = (
    "/api/v1/ping",
    "/api/v1/sync/bootstrap",
    "/api/v1/study/solve",
    "/api/v1/study/voice",
    "/api/v1/ota/check",
    "/api/v1/content/download",
)

_client: Optional[httpx.AsyncClient] = None


def get_client() -> httpx.AsyncClient:
    """共享的回环异步客户端（进程内单例）。"""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=90.0, trust_env=False)
    return _client


class BridgeError(RuntimeError):
    """设备端调用失败（对外只给温和文案，细节留在服务端日志里）。"""


class DeviceBridge:
    """一台虚拟设备（= 管理台里那台「真机」）的网络层。"""

    def __init__(
        self,
        base_url: str,
        device_id: str,
        secret_provider: Callable[[], Optional[str]],
    ):
        self.base_url = base_url.rstrip("/")
        self.device_id = device_id
        self._secret_provider = secret_provider

    def _secret(self) -> str:
        secret = self._secret_provider()
        if not secret:
            raise BridgeError("这台虚拟设备还没配对")
        return secret

    # ------------------------------------------------------------------ 配对
    async def pair(self, pair_code: str) -> Dict[str, Any]:
        """6 位配对码换密钥（明文 JSON 请求，此时还没有密钥，签不了名）。"""
        body = json.dumps(
            {"device_id": self.device_id, "pair_code": pair_code}, ensure_ascii=False
        ).encode("utf-8")
        r = await get_client().post(
            f"{self.base_url}/api/v1/pair",
            content=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            env = r.json()
        except (json.JSONDecodeError, ValueError):
            raise BridgeError("配对没成功，请稍后再试")
        if env.get("code") != 0:
            raise BridgeError(str(env.get("msg") or "配对没成功"))
        raw = env.get("payload") or ""
        try:
            data = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except (json.JSONDecodeError, TypeError):
            data = {}
        if not data.get("device_secret"):
            raise BridgeError("配对没有拿到设备密钥")
        return data

    # ------------------------------------------------------------------ 调用
    async def call(
        self, method: str, path: str, payload: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """按设备端协议发一次真实请求，返回解密后的业务数据 + 调试层信息。"""
        method = method.upper()
        pure, _, query = path.partition("?")
        if pure not in ALLOWED_PATHS:
            raise BridgeError("这个接口不允许从模拟器调用")
        secret = self._secret()
        ts = str(int(time.time()))

        if method == "GET":
            raw = b""
            url = f"{self.base_url}{pure}" + (f"?{query}" if query else "")
        else:
            # 信封：{"payload": Base64(IV||CTR(明文))}，密钥 = device_secret 前 16 字节
            raw = json.dumps(
                {"payload": security.encrypt_json(payload or {}, secret)},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            url = f"{self.base_url}{pure}"

        sig = security.sign_request(method, pure, ts, raw, secret)
        headers = {
            "X-Device-Id": self.device_id,
            "X-Timestamp": ts,
            "X-Signature": sig,
        }
        if method != "GET":
            headers["Content-Type"] = "application/json"

        r = await get_client().request(method, url, content=raw or None, headers=headers)
        body = r.content
        resp_sig = r.headers.get("X-Resp-Signature", "")
        out: Dict[str, Any] = {
            "http": r.status_code,
            "code": None,
            "msg": "",
            "data": {},
            "raw": {
                "method": method,
                "path": pure,
                "query": query,
                "ts": ts,
                "req_sig": sig,
                "resp_sig": resp_sig,
                # ping 这类无载荷存活探针按契约不附响应签名（05 §3.2 说的是
                # 「任何**业务响应**」）。把「没带」和「带了但验不过」分开报，
                # 否则调试层会把正常行为显示成失败。
                "resp_sig_present": bool(resp_sig),
                # 真实验签：响应签名用**请求时**的时间戳校验
                "resp_verified": security.verify_response(ts, body, resp_sig, secret),
                "body_len": len(body),
                "body_sha256": hashlib.sha256(body).hexdigest(),
            },
        }

        # 二进制资产下载（课表 / 学科包 / 电子书 / 固件）
        if pure == "/api/v1/content/download":
            out["data"] = {
                "size": len(body),
                "sha256": r.headers.get("X-File-Sha256", ""),
                "text": body.decode("utf-8", "replace"),
            }
            return out

        try:
            env = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            out["msg"] = "网关返回了看不懂的内容"
            return out
        out["code"] = env.get("code")
        out["msg"] = str(env.get("msg") or "")
        blob = env.get("payload") or ""
        if blob:
            try:
                out["data"] = security.decrypt_json(blob, secret)
            except security.SecurityError:
                out["data"] = {"_undecrypted": str(blob)[:400]}
        return out


# ---------------------------------------------------------------------- 学科包
def read_pack_cards(storage_root: Path, pack_id: str, secret_hex: str) -> List[Dict[str, Any]]:
    """解出学科包卡片（设备侧本该做的事，这里代劳给模拟器看）。

    ``cards.enc`` = ``IV(16B) || AES-128-CTR(明文 JSONL 流)``，密钥来源由
    ``meta.json`` 的 ``key_type`` 决定（``demo`` 固定密钥 / ``device`` 派生子设备密钥）。
    """
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    from app.pack_builder import derive_key

    pack_dir = Path(storage_root) / "packs" / pack_id
    meta_path = pack_dir / "meta.json"
    enc_path = pack_dir / "cards.enc"
    if not meta_path.is_file() or not enc_path.is_file():
        raise FileNotFoundError("这个学科包还没下载")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    key = derive_key(str(meta.get("key_type") or "demo"), secret_hex)
    raw = enc_path.read_bytes()
    if len(raw) < 16:
        raise ValueError("学科包文件不完整")
    dec = Cipher(algorithms.AES(key), modes.CTR(raw[:16])).decryptor()
    plain = dec.update(raw[16:]) + dec.finalize()
    cards: List[Dict[str, Any]] = []
    for line in plain.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            cards.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return cards


# ---------------------------------------------------------------------- WiFi
async def _run(cmd: List[str], timeout: float) -> str:
    """跑一条只读命令（异步子进程，避免阻塞事件循环）。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError:
        return ""
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return ""
    return out.decode("utf-8", "replace")


async def scan_wifi() -> List[Dict[str, Any]]:
    """扫周边热点。

    优先用 ``iw`` 拿真实 RSSI（``03`` 的信号分级按 dBm 判定）；拿不到再退回
    ``nmcli`` 的信号质量百分比 —— 那时明确标注「信号质量」而不是「信号强度」，
    免得把两种量纲混为一谈。
    """
    out: List[Dict[str, Any]] = []
    text = await _run(["iw", "dev", "wlan0", "scan"], timeout=15)
    if text:
        ssid = ""
        rssi: Optional[int] = None
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("SSID:"):
                ssid = s[5:].strip()
            elif s.startswith("signal:"):
                try:
                    rssi = int(s.split()[1])
                except (ValueError, IndexError):
                    rssi = None
            elif s.startswith("BSS ") and ssid and rssi is not None:
                out.append({"ssid": ssid, "rssi": rssi, "level": _level(rssi), "unit": "dBm"})
                ssid, rssi = "", None

    if not out:
        text = await _run(
            ["nmcli", "-t", "-f", "SSID,SIGNAL", "device", "wifi", "list", "--rescan", "yes"],
            timeout=20,
        )
        for line in text.splitlines():
            if ":" not in line:
                continue
            ssid, _, sig = line.partition(":")
            ssid, sig = ssid.strip(), sig.strip()
            if not ssid:
                continue
            try:
                q = int(sig)
            except ValueError:
                q = 0
            out.append(
                {
                    "ssid": ssid,
                    "rssi": None,
                    "quality": q,
                    "level": "信号强" if q >= 70 else ("信号中" if q >= 40 else "信号弱"),
                    "unit": "%",
                }
            )

    # 去重（同名多 AP 取信号最好的那个）
    best: Dict[str, Dict[str, Any]] = {}
    for ap in out:
        key = ap["ssid"]
        prev = best.get(key)
        if prev is None:
            best[key] = ap
        elif ap.get("rssi") is not None and (
            prev.get("rssi") is None or ap["rssi"] > prev["rssi"]
        ):
            best[key] = ap
    return sorted(best.values(), key=lambda a: -(a.get("rssi") or 0))


def _level(rssi: int) -> str:
    """``03`` 的信号分级：> -60dBm 强，-60~-75 中，< -75 弱。"""
    if rssi > -60:
        return "信号强"
    if rssi >= -75:
        return "信号中"
    return "信号弱"
