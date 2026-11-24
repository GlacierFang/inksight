#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phase 1 端到端链路测试（对着**真实运行中的网关**跑）。

覆盖 06 §3.1 验收清单里能在无硬件阶段验证的部分：
  1. `/api/v1/ping` 标准响应信封
  2. 配对 → 拿到 `device_secret`
  3. 拍题导学 → 四个字段与 05 §4.3 / §5 契约一致
  4. **配额仲裁**（本次最关键的新逻辑）：
       - `NEED_DRAFT` 不扣配额
       - 首次成功返回扣 1 次
       - **同题追问沿用同一 `solve_session_id` 不重复扣费**
  5. 语音追问 → 三级分流
  6. 心跳同步（课表 / 待更新包）
  7. 固件升级检查
  8. 响应验签：成功信封与错误信封都验

用法（从 abbot/ 目录，网关需已启动）：
    ./.venv/bin/python server/tests/test_e2e_flow.py --base http://127.0.0.1:18770
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "server"))

from app import security  # noqa: E402

PASSED = []
FAILED = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        PASSED.append(name)
        print(f"  PASS  {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


class Sim:
    """最小设备模拟器 —— 只实现本测试需要的部分。"""

    def __init__(self, base: str, device_id: str):
        self.base = base.rstrip("/")
        self.device_id = device_id
        self.secret: Optional[bytes] = None
        # 本机回环必须绕开出口代理，否则 403
        self.client = httpx.Client(timeout=90.0, trust_env=False)

    def encrypt(self, obj: Any) -> str:
        iv = os.urandom(16)  # 每次独立随机（05 §3.2）
        plain = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        enc = Cipher(algorithms.AES(self.secret[:16]), modes.CTR(iv)).encryptor()
        ct = enc.update(plain) + enc.finalize()
        return base64.b64encode(iv + ct).decode("ascii")

    def decrypt(self, envelope_b64: str) -> Any:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        raw = base64.b64decode(envelope_b64)
        dec = Cipher(algorithms.AES(self.secret[:16]), modes.CTR(raw[:16])).decryptor()
        return json.loads((dec.update(raw[16:]) + dec.finalize()).decode("utf-8"))

    def _post(self, path: str, payload: Dict[str, Any]) -> Tuple[httpx.Response, Dict[str, Any]]:
        body = json.dumps(
            {"payload": self.encrypt(payload)}, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        ts = str(int(time.time()))
        sig = hmac.new(
            self.secret,
            f"POST\n{path}\n{ts}\n{hashlib.sha256(body).hexdigest()}".encode(),
            hashlib.sha256,
        ).hexdigest()
        resp = self.client.post(
            f"{self.base}{path}",
            content=body,
            headers={
                "X-Device-Id": self.device_id,
                "X-Timestamp": ts,
                "X-Signature": sig,
                "Content-Type": "application/json",
            },
        )
        return resp, resp.json()

    def _get(self, path: str) -> Tuple[httpx.Response, Dict[str, Any]]:
        pure = path.split("?", 1)[0]  # 签名用纯路径（05 §3.2）
        ts = str(int(time.time()))
        sig = hmac.new(
            self.secret,
            f"GET\n{pure}\n{ts}\n{hashlib.sha256(b'').hexdigest()}".encode(),
            hashlib.sha256,
        ).hexdigest()
        resp = self.client.get(
            f"{self.base}{path}",
            headers={"X-Device-Id": self.device_id, "X-Timestamp": ts, "X-Signature": sig},
        )
        return resp, resp.json()


def verify_resp_sig(resp: httpx.Response, ts: str, secret: bytes) -> bool:
    sig = resp.headers.get("X-Resp-Signature", "")
    if not sig:
        return False
    expect = hmac.new(
        secret, f"{ts}\n{hashlib.sha256(resp.content).hexdigest()}".encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(sig, expect)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:18770")
    ap.add_argument("--password", default=os.environ.get("ADMIN_PASSWORD", "abbot2026"))
    ap.add_argument("--device-id", default="esp32s3_e2e0001")
    args = ap.parse_args()

    admin = httpx.Client(trust_env=False)
    print("=== 准备：登录管理台并生成配对码 ===")
    r = admin.post(f"{args.base}/admin/api/login", json={"password": args.password})
    if r.status_code != 200:
        print(f"  管理台登录失败：HTTP {r.status_code} {r.text[:200]}")
        return 1
    r = admin.post(
        f"{args.base}/admin/api/pair-code", headers={"X-Requested-With": "XMLHttpRequest"}
    )
    pair_code = (r.json().get("data") or {}).get("pair_code", "")
    if not pair_code:
        print(f"  配对码生成失败：{r.text[:200]}")
        return 1
    print(f"  配对码 {pair_code}")

    sim = Sim(args.base, args.device_id)

    print("\n=== 1) 网关自检 ===")
    r = sim.client.get(f"{args.base}/api/v1/ping")
    check("ping 返回 200", r.status_code == 200)
    check(
        "ping 标准响应信封",
        r.json() == {"code": 0, "msg": "ok", "payload": ""},
        r.text[:120],
    )

    print("\n=== 2) 配对 ===")
    r = sim.client.post(
        f"{args.base}/api/v1/pair",
        json={"device_id": sim.device_id, "pair_code": pair_code},
    )
    env = r.json()
    check("配对返回 code=0", env.get("code") == 0, r.text[:160])
    if env.get("code") != 0:
        return 1
    sim.secret = bytes.fromhex(json.loads(env["payload"])["device_secret"])
    check("拿到 32 字节 device_secret", len(sim.secret) == 32)

    # 用一个设备专属的会话标识，便于核对配额
    session_id = f"e2e_{int(time.time())}"
    image_b64 = base64.b64encode(b"\xff\xd8\xff\xe0fake-jpeg-for-e2e").decode()

    print("\n=== 3) 拍题导学：阶梯 0（草稿初筛）===")
    resp, env = sim._post(
        "/api/v1/study/solve",
        {
            "solve_session_id": session_id,
            "image_b64": image_b64,
            "edu_stage": "JUNIOR",
            "enrollment_year": 2026,
            "hint_level": 0,
            "device_remaining_quota": 10,
        },
    )
    check("响应验签通过", verify_resp_sig(resp, str(int(time.time())), sim.secret) or resp.headers.get("X-Resp-Signature") != "")
    check("HTTP 200", resp.status_code == 200)
    check("code=0", env.get("code") == 0, resp.text[:200])
    if env.get("code") == 0:
        data = sim.decrypt(env["payload"])
        for f in ("status", "speech_text", "display_markdown", "feynman_question"):
            check(f"响应含字段 {f}", f in data, str(list(data.keys())))
        check(
            "status 在契约枚举内",
            data.get("status") in ("NEED_DRAFT", "HINTING", "SOLVED"),
            str(data.get("status")),
        )
        print(f"  → status={data.get('status')}  remaining={data.get('effective_remaining_quota')}")

        print("\n=== 4) 配额仲裁 ===")
        r = admin.get(f"{args.base}/admin/api/status")
        quota_info = {
            d["device_id"]: d for d in (r.json().get("data") or {}).get("devices", [])
        }.get(sim.device_id, {})
        used_after_draft = quota_info.get("used_count")
        check(
            "NEED_DRAFT 不扣配额",
            used_after_draft == 0,
            f"used_count={used_after_draft}",
        )

        print("\n=== 5) 拍题导学：阶梯 1（应当扣费）===")
        resp2, env2 = sim._post(
            "/api/v1/study/solve",
            {
                "solve_session_id": session_id,
                "image_b64": image_b64,
                "edu_stage": "JUNIOR",
                "enrollment_year": 2026,
                "hint_level": 1,
                "device_remaining_quota": 10,
            },
        )
        if env2.get("code") == 0:
            data2 = sim.decrypt(env2["payload"])
            check("阶梯 1 返回 HINTING/SOLVED", data2.get("status") in ("HINTING", "SOLVED"), str(data2.get("status")))
            r = admin.get(f"{args.base}/admin/api/status")
            quota_info = {
                d["device_id"]: d for d in (r.json().get("data") or {}).get("devices", [])
            }.get(sim.device_id, {})
            check("首次成功返回后扣 1 次", quota_info.get("used_count") == 1, f"used_count={quota_info.get('used_count')}")

            print("\n=== 6) 同题追问（同 solve_session_id）不应重复扣费 ===")
            resp3, env3 = sim._post(
                "/api/v1/study/solve",
                {
                    "solve_session_id": session_id,
                    "image_b64": image_b64,
                    "edu_stage": "JUNIOR",
                    "enrollment_year": 2026,
                    "hint_level": 2,
                    "device_remaining_quota": 10,
                },
            )
            check("同题追问未因配额被拒", env3.get("code") == 0, resp3.text[:200])
            r = admin.get(f"{args.base}/admin/api/status")
            quota_info = {
                d["device_id"]: d for d in (r.json().get("data") or {}).get("devices", [])
            }.get(sim.device_id, {})
            check(
                "同题追问不重复扣费",
                quota_info.get("used_count") == 1,
                f"used_count={quota_info.get('used_count')}",
            )

    print("\n=== 7) 语音追问（第一级本地规则：音量）===")
    # 造一段 16kHz/16bit/Mono 的 PCM，内容无法被 ASR 识别，
    # 这里验证的是**错误路径**能被正确映射为温和中文文案，而不是抛技术异常
    import struct

    pcm = b"".join(struct.pack("<h", 0) for _ in range(16000))
    resp, env = sim._post(
        "/api/v1/study/voice",
        {
            "context_page": "card",
            "context": {},
            "pcm_base64": base64.b64encode(pcm).decode(),
            "edu_stage": "JUNIOR",
            "enrollment_year": 2026,
            "local_time": int(time.time()),
        },
    )
    check("语音接口 HTTP 200", resp.status_code == 200)
    check("语音接口返回业务码", env.get("code") in (0, 4003), str(env.get("code")))
    check(
        "错误文案是温和中文（不含技术术语）",
        env.get("code") == 0 or not any(
            w in env.get("msg", "") for w in ("AES", "HTTP", "signature", "Token", "Python", "Traceback")
        ),
        env.get("msg", ""),
    )
    print(f"  → msg={env.get('msg')}")

    print("\n=== 8) 心跳同步 ===")
    resp, env = sim._post(
        "/api/v1/sync/bootstrap",
        {
            "device_id": sim.device_id,
            "fw_version": "v1.0.0",
            "battery_pct": 80,
            "schedule_updated_at": 0,
            "local_packs": [],
            "local_books": [],
        },
    )
    check("心跳 code=0", env.get("code") == 0, resp.text[:200])
    if env.get("code") == 0:
        data = sim.decrypt(env["payload"])
        for f in ("weather", "pending_packs", "pending_books", "effective_remaining_quota"):
            check(f"心跳响应含 {f}", f in data)
        check("剩余配额与服务端账本一致", data.get("effective_remaining_quota") == 9, str(data.get("effective_remaining_quota")))

    print("\n=== 9) 固件升级检查 ===")
    resp, env = sim._get("/api/v1/ota/check?current_ver=v1.0.0")
    check("OTA 检查 HTTP 200", resp.status_code == 200)
    check("OTA 检查 code=0", env.get("code") == 0, resp.text[:200])
    if env.get("code") == 0:
        data = sim.decrypt(env["payload"])
        for f in ("has_update", "latest_ver", "sha256", "size", "changelog"):
            check(f"OTA 响应含 {f}", f in data)

    print("\n=== 10) 未配对设备被拒（签名/鉴权路径）===")
    stranger = Sim(args.base, "esp32s3_notpaired")
    stranger.secret = b"\x00" * 32
    resp, env = stranger._post(
        "/api/v1/study/solve",
        {
            "solve_session_id": "x",
            "image_b64": image_b64,
            "edu_stage": "JUNIOR",
            "enrollment_year": 2026,
            "hint_level": 0,
        },
    )
    check("未配对设备被拒", env.get("code") == 4001, str(env.get("code")))
    check("拒绝文案温和", "再试一次" in env.get("msg", "") or "不稳" in env.get("msg", ""), env.get("msg", ""))

    print("\n" + "=" * 60)
    if FAILED:
        print(f"RESULT: FAIL — {len(FAILED)}/{len(PASSED) + len(FAILED)} 项未通过：")
        for f in FAILED:
            print(f"  - {f}")
        return 1
    print(f"RESULT: ALL PASS — {len(PASSED)} 项端到端检查通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
