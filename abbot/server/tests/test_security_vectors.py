# -*- coding: utf-8 -*-
"""
`app/security.py` 是否逐字节复现 05 §3.4 锁定向量组。

**这是 Phase 1 的地基闸门** —— security.py 与文档向量组任何一方分叉，
本测试必报 FAIL。

直接读取 `doc/05-云端接口与AI推理引擎契约.md` 正文抓取向量（**不信任代码里的常量**），
再用 `app.security` 的实现复算。改文档而不改实现（或反之）必报 FAIL。

运行（从 abbot/ 目录）：
    ./.venv/bin/python server/tests/test_security_vectors.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "server"))

from app import security  # noqa: E402

DOC = ROOT / "doc" / "05-云端接口与AI推理引擎契约.md"

FAILURES = []
CHECKS = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if ok:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))
        FAILURES.append(name)


def grab(section: str, pattern: str, group: int = 0) -> str:
    import re

    m = re.search(pattern, section)
    if not m:
        raise AssertionError(f"文档中未找到匹配：{pattern}")
    return m.group(group)


def section_of(text: str, header: str) -> str:
    start = text.index(header)
    rest = text[start + len(header):]
    end = rest.find("\n### ")
    return rest if end < 0 else rest[:end]


def main() -> int:
    if not DOC.exists():
        print(f"找不到契约文档：{DOC}")
        return 1
    text = DOC.read_text(encoding="utf-8")
    sec = section_of(text, "### 3.4 ")

    # ---- 从文档正文抓取 ----
    sec_hex = grab(sec, r"\*\*`device_secret` \(Hex\)\*\*: `([0-9a-f]+)`", 1)
    k_hex = grab(sec, r"\*\*AES-128 密钥 \(前 16B Hex\)\*\*: `([0-9a-f]+)`", 1)
    iv_hex = grab(sec, r"\*\*固定测试 `IV` \(16B Hex\)\*\*: `([0-9a-f]+)`", 1)
    pt_hex = grab(sec, r"\*\*明文 JSON\*\*: `[^`]+`（10 字节 ASCII：`([0-9a-f]+)`）", 1)
    ct_hex = grab(sec, r"\*\*期望密文 \(10B Hex\)\*\*: `([0-9a-f]+)`", 1)
    env_b64 = grab(sec, r"\*\*期望信封 `payload`[^*]*\*\*: `([A-Za-z0-9+/=]+)`", 1)
    nonce2 = grab(sec, r"计数器 `nonce_counter` \(16B Hex\): `([0-9a-f]+)`", 1)
    ct2_hex = grab(sec, r"在 `block_index = 2` 处的期望密文 \(10B Hex\): `([0-9a-f]+)`", 1)
    bs_hex = grab(sec, r"`RAW_BODY_SHA256`: `([0-9a-f]+)`", 1)
    sig_hex = grab(sec, r"期望请求签名 `X-Signature`: `([0-9a-f]+)`", 1)
    rbs_hex = grab(sec, r"`RAW_RESP_BODY_SHA256`: `([0-9a-f]+)`", 1)
    rsig_hex = grab(sec, r"期望响应签名 `X-Resp-Signature`: `([0-9a-f]+)`", 1)
    body_str = grab(sec, r"`RAW_BODY='([^']+)'` \(\d+ 字节 ASCII\)", 1)
    rbody_str = grab(sec, r"`RAW_RESP_BODY='([^']+)'` \(\d+ 字节 ASCII\)", 1)

    plaintext = bytes.fromhex(pt_hex)
    fixed_iv = bytes.fromhex(iv_hex)

    print("=== app/security.py 对 05 §3.4 锁定向量的复算 ===")

    # ---- 密钥派生 ----
    check(
        "密钥前16B 与 device_secret 前16B 一致",
        security.aes_key(sec_hex).hex() == k_hex,
        f"impl={security.aes_key(sec_hex).hex()} doc={k_hex}",
    )

    # ---- AES-CTR block0 ----
    got_ct = security.encrypt(plaintext, sec_hex, iv=fixed_iv)
    # encrypt 返回 base64(IV||CT)，取后 10 字节即密文
    import base64

    raw = base64.b64decode(got_ct)
    check("AES-CTR block0 密文", raw[16:].hex() == ct_hex, f"impl={raw[16:].hex()}")

    # ---- 信封 base64 ----
    check("信封 base64", got_ct == env_b64, f"impl={got_ct}")

    # ---- block2 偏移 ----
    nonce2_impl = security.add_counter(fixed_iv, 2)
    check("block2 nonce 计数器", nonce2_impl.hex() == nonce2, f"impl={nonce2_impl.hex()}")
    ct2_impl = base64.b64decode(security.encrypt(plaintext, sec_hex, iv=nonce2_impl))[16:]
    check("block2 密文", ct2_impl.hex() == ct2_hex, f"impl={ct2_impl.hex()}")

    # ---- 签名 ----
    import hashlib

    body_bytes = body_str.encode("ascii")
    check("请求体字节数", len(body_bytes) == 50, f"len={len(body_bytes)}")
    check(
        "请求体 SHA-256",
        hashlib.sha256(body_bytes).hexdigest() == bs_hex,
    )
    sig_impl = security.sign_request("POST", "/api/v1/ping", "1795412000", body_bytes, sec_hex)
    check("X-Signature", sig_impl == sig_hex, f"impl={sig_impl}")

    rbody_bytes = rbody_str.encode("ascii")
    check(
        "响应体 SHA-256",
        hashlib.sha256(rbody_bytes).hexdigest() == rbs_hex,
    )
    rsig_impl = security.sign_response("1795412000", rbody_bytes, sec_hex)
    check("X-Resp-Signature", rsig_impl == rsig_hex, f"impl={rsig_impl}")

    # ---- 解密回环 ----
    check("解密回环", security.decrypt(env_b64, sec_hex) == plaintext)

    # ---- 随机 IV 路径（生产路径）：两次加密必须不同 ----
    a = security.encrypt(plaintext, sec_hex)
    b = security.encrypt(plaintext, sec_hex)
    check("生产路径 IV 随机（两次密文不同）", a != b)

    print()
    if FAILURES:
        print(f"RESULT: FAIL — {len(FAILURES)}/{CHECKS} 项未通过：{', '.join(FAILURES)}")
        return 1
    print(f"RESULT: ALL PASS — {CHECKS} 项，security.py 与 05 §3.4 向量组一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
