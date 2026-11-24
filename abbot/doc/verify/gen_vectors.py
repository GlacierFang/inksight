#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
05 §3.4 锁定向量组 —— 生成器（唯一产出源）

设计意图：
  05 §3.4 要求「两端必须输出逐字节一致的结果」，但历史上文档里手填的向量
  不可复现（端云地基是坏的）。本脚本把这组值变成**可执行产出**：

    NIST SP 800-38A F.5.1 官方向量自检   ← 证明本脚本自身的 AES-CTR 实现正确
              ↓
    生成本组锁定向量                    ← 唯一产出源
              ↓
    --update-doc 回写 05 文档正文       ← 文档不再是手抄物
              ↓
    verify_vectors.py 从文档抓取复算    ← 闭环校验（改文档不改实现必报 FAIL）

用法（从 abbot/ 目录执行）：
    ./.venv/bin/python doc/verify/gen_vectors.py --emit        # 打印向量组 Markdown
    ./.venv/bin/python doc/verify/gen_vectors.py --update-doc  # 回写 05 §3.4 向量区块
    ./.venv/bin/python doc/verify/gen_vectors.py --print       # 打印全部中间量（调试用）

硬约束（写进 05 §3.4 的规范里）：
    · NIST 自检不通过 → 直接退出，绝不产出向量；
    · 本组固定 IV 仅为测试对齐用，生产路径每个信封必须独立随机生成 IV。
"""

import argparse
import base64
import hashlib
import hmac
import sys
from pathlib import Path

try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
except ImportError:
    sys.exit("缺少依赖 cryptography —— 用项目 venv 执行：./.venv/bin/python doc/verify/gen_vectors.py")

DOC_PATH = Path(__file__).resolve().parents[1] / "05-云端接口与AI推理引擎契约.md"
BEGIN_MARK = "<!-- VECTORS:BEGIN -->"
END_MARK = "<!-- VECTORS:END -->"

# --------------------------------------------------------------------------
# 一、输入常量（与 05 契约一致）
# --------------------------------------------------------------------------
DEVICE_SECRET = bytes.fromhex(
    "00112233445566778899aabbccddeeff0123456789abcdef0123456789abcdef"
)
AES_KEY = DEVICE_SECRET[:16]           # 密钥 = device_secret 前 16B
TEST_IV = bytes.fromhex("0102030405060708090a0b0c0d0e0f10")   # 仅测试用
PLAINTEXT = b'{"ping":1}'               # 10 字节 ASCII
BLOCK_INDEX = 2                        # cards.enc 局部块偏移测试用

REQ_METHOD = "POST"
REQ_PATH = "/api/v1/ping"
REQ_TS = "1795412000"
RESP_BODY = b'{"code":0,"msg":"ok","payload":""}'
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()

# --------------------------------------------------------------------------
# 二、NIST SP 800-38A F.5.1 CTR-AES128 官方向量（自检用，不属于本组向量）
# --------------------------------------------------------------------------
NIST_KEY = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
NIST_IV = bytes.fromhex("f0f1f2f3f4f5f6f7f8f9fafbfcfdfeff")
NIST_PAIRS = [
    ("6bc1bee22e409f96e93d7e117393172a", "874d6191b620e3261bef6864990db6ce"),
    ("ae2d8a571e03ac9c9eb76fac45af8e51", "9806f66b7970fdff8617187bb9fffdff"),
    ("30c81c46a35ce411e5fbc1191a0a52ef", "5ae4df3edbd5d35e5b4f09020db03eab"),
    ("f69f2445df4f9b17ad2b417be66c3710", "1e031dda2fbe03d1792170a0f3009cee"),
]


def ctr_xor(key: bytes, iv: bytes, data: bytes) -> bytes:
    """AES-128-CTR：counter 以 16 字节大端序整数整体递增。"""
    enc = Cipher(algorithms.AES(key), modes.CTR(iv)).encryptor()
    return enc.update(data) + enc.finalize()


def add_counter(iv: bytes, n: int) -> bytes:
    """大端序 128-bit 加法进位（04 §4.2 的 nonce_counter 公式）。"""
    return ((int.from_bytes(iv, "big") + n) % (1 << 128)).to_bytes(16, "big")


def nist_selfcheck() -> None:
    """先过 NIST 官方向量，确认本实现正确，再谈产出本组向量。"""
    print("=== NIST SP 800-38A F.5.1 CTR-AES128 实现自检 ===")
    ok = True
    for i, (pt_hex, ct_hex) in enumerate(NIST_PAIRS, start=1):
        # 每个 block 的 counter = NIST_IV + (i-1)
        iv = add_counter(NIST_IV, i - 1)
        got = ctr_xor(NIST_KEY, iv, bytes.fromhex(pt_hex)).hex()
        good = got == ct_hex
        ok = ok and good
        print(f"  {'PASS' if good else 'FAIL'}  block#{i}  {got}")
    if not ok:
        sys.exit("NIST 自检失败 —— 本脚本实现有误，拒绝产出向量组。")


# --------------------------------------------------------------------------
# 三、产出本组锁定向量
# --------------------------------------------------------------------------
def build_vectors() -> dict:
    cipher0 = ctr_xor(AES_KEY, TEST_IV, PLAINTEXT)
    envelope = base64.b64encode(TEST_IV + cipher0).decode("ascii")

    nonce2 = add_counter(TEST_IV, BLOCK_INDEX)
    cipher2 = ctr_xor(AES_KEY, nonce2, PLAINTEXT)

    req_body = ('{"payload":"%s"}' % envelope).encode("ascii")
    req_sha = hashlib.sha256(req_body).hexdigest()
    sign_str = f"{REQ_METHOD}\n{REQ_PATH}\n{REQ_TS}\n{req_sha}".encode("utf-8")
    req_sig = hmac.new(DEVICE_SECRET, sign_str, hashlib.sha256).hexdigest()

    resp_sha = hashlib.sha256(RESP_BODY).hexdigest()
    resp_sig = hmac.new(
        DEVICE_SECRET, f"{REQ_TS}\n{resp_sha}".encode("utf-8"), hashlib.sha256
    ).hexdigest()

    return {
        "device_secret": DEVICE_SECRET.hex(),
        "aes_key": AES_KEY.hex(),
        "test_iv": TEST_IV.hex(),
        "plaintext": PLAINTEXT.hex(),
        "cipher0": cipher0.hex(),
        "envelope": envelope,
        "nonce2": nonce2.hex(),
        "cipher2": cipher2.hex(),
        "req_body": req_body.decode("ascii"),
        "req_body_len": len(req_body),
        "req_sha": req_sha,
        "req_sig": req_sig,
        "resp_body": RESP_BODY.decode("ascii"),
        "resp_body_len": len(RESP_BODY),
        "resp_sha": resp_sha,
        "resp_sig": resp_sig,
        "empty_sha": EMPTY_SHA256,
    }


def render(v: dict) -> str:
    """渲染为 05 §3.4 的 Markdown 行（供 --emit / --update-doc 使用）。"""
    lines = [
        f"* **`device_secret` (Hex)**: `{v['device_secret']}`",
        f"* **AES-128 密钥 (前 16B Hex)**: `{v['aes_key']}`",
        f"* **固定测试 `IV` (16B Hex)**: `{v['test_iv']}`",
        f"* **明文 JSON**: `{{\"ping\":1}}`（10 字节 ASCII：`{v['plaintext']}`）",
        f"* **期望密文 (10B Hex)**: `{v['cipher0']}`",
        f"* **期望信封 `payload` (`Base64(IV + Ciphertext)`)**: `{v['envelope']}`",
        "* **局部块偏移解密测试向量（验证 `cards.enc` 第 `block_index = 2` 块按需解密）**：",
        f"  * 沿用同一 `AES-128` 密钥与 `IV_base` `{v['test_iv']}`；",
        f"  * 大端序 128-bit 推进 `block_index = {BLOCK_INDEX}` 后的计数器 `nonce_counter` (16B Hex): `{v['nonce2']}`；",
        f"  * 明文 `{{\"ping\":1}}`（10B Hex：`{v['plaintext']}`）在 `block_index = {BLOCK_INDEX}` 处的期望密文 (10B Hex): `{v['cipher2']}`",
        f"* **当 `METHOD=\"{REQ_METHOD}\"`, `PATH=\"{REQ_PATH}\"`, `X-Timestamp=\"{REQ_TS}\"`, `RAW_BODY='{v['req_body']}'` ({v['req_body_len']} 字节 ASCII) 时**：",
        f"  * `RAW_BODY_SHA256`: `{v['req_sha']}`",
        f"  * 期望请求签名 `X-Signature`: `{v['req_sig']}`",
        f"* **当响应体 `RAW_RESP_BODY='{v['resp_body']}'` ({v['resp_body_len']} 字节 ASCII) 且 `REQ_X_TIMESTAMP=\"{REQ_TS}\"` 时**：",
        f"  * `RAW_RESP_BODY_SHA256`: `{v['resp_sha']}`",
        f"  * 期望响应签名 `X-Resp-Signature`: `{v['resp_sig']}`",
        f"* **空请求体 `SHA-256`（`GET` 或无 Body 场景）**: `{v['empty_sha']}`",
    ]
    return "\n".join(lines)


def update_doc(block: str) -> bool:
    """把向量区块回写 05 文档（要求文档已带 BEGIN/END 标记）。"""
    if not DOC_PATH.exists():
        sys.exit(f"找不到文档：{DOC_PATH}")
    text = DOC_PATH.read_text(encoding="utf-8")
    if BEGIN_MARK not in text or END_MARK not in text:
        sys.exit(
            "05 文档缺少向量区块标记 —— 请先在 §3.4 插入：\n"
            f"  {BEGIN_MARK}\n  {END_MARK}"
        )
    head, rest = text.split(BEGIN_MARK, 1)
    _, tail = rest.split(END_MARK, 1)
    DOC_PATH.write_text(
        f"{head}{BEGIN_MARK}\n{block}\n{END_MARK}{tail}", encoding="utf-8"
    )
    print(f"已回写：{DOC_PATH}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="05 §3.4 锁定向量组生成器")
    ap.add_argument("--emit", action="store_true", help="打印向量组 Markdown")
    ap.add_argument("--update-doc", action="store_true", help="回写 05 §3.4 向量区块")
    ap.add_argument("--print", dest="dump", action="store_true", help="打印全部中间量")
    args = ap.parse_args()

    nist_selfcheck()
    v = build_vectors()

    if args.dump:
        print("\n=== 全部中间量 ===")
        for k, val in v.items():
            print(f"  {k:16s} = {val}")

    if args.update_doc:
        update_doc(render(v))

    if args.emit or not (args.update_doc or args.dump):
        print("\n=== 05 §3.4 向量组 ===")
        print(render(v))

    return 0


if __name__ == "__main__":
    sys.exit(main())
