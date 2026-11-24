#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
校验 05 契约 §3.4 的联调向量组是否自洽。

做法：不信任文档里的数字，直接从 **文档正文** 抓取每个向量，
用标准实现（cryptography）复算一遍，逐项比对。

用法：
    python3 doc/verify/verify_vectors.py
"""
import base64
import hashlib
import hmac
import io
import os
import re
import sys

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

DOC05 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "05-云端接口与AI推理引擎契约.md")


def load_section():
    with io.open(DOC05, encoding="utf-8") as f:
        txt = f.read()
    i = txt.find("### 3.4")
    j = txt.find("### 3.5", i)
    assert i >= 0 and j > i, "未能在 05 文档中定位 §3.4 向量段"
    return txt[i:j]


def grab(sec, pat, group=0):
    """返回第 group 个捕获组；无 group 参数时返回全部捕获组的元组。"""
    m = re.search(pat, sec)
    assert m, "文档中未找到向量字段: %s" % pat
    if group == 0 and m.re.groups > 1:
        return m.groups()
    return m.group(group)


def main():
    sec = load_section()

    sec_hex = grab(sec, r"\*\*`device_secret` \(Hex\)\*\*: `([0-9a-f]+)`", 1)
    k_hex = grab(sec, r"\*\*AES-128 密钥 \(前 16B Hex\)\*\*: `([0-9a-f]+)`", 1)
    iv_hex = grab(sec, r"\*\*固定测试 `IV` \(16B Hex\)\*\*: `([0-9a-f]+)`", 1)
    ct_hex = grab(sec, r"\*\*期望密文 \(10B Hex\)\*\*: `([0-9a-f]+)`", 1)
    env_b64 = grab(sec, r"\*\*期望信封 `payload`[^*]*\*\*: `([A-Za-z0-9+/=]+)`", 1)
    nonce2 = grab(sec, r"计数器 `nonce_counter` \(16B Hex\): `([0-9a-f]+)`", 1)
    ct2_hex = grab(sec, r"在 `block_index = 2` 处的期望密文 \(10B Hex\): `([0-9a-f]+)`", 1)
    bs_hex = grab(sec, r"`RAW_BODY_SHA256`: `([0-9a-f]+)`", 1)
    sig_hex = grab(sec, r"期望请求签名 `X-Signature`: `([0-9a-f]+)`", 1)
    rbs_hex = grab(sec, r"`RAW_RESP_BODY_SHA256`: `([0-9a-f]+)`", 1)
    rsig_hex = grab(sec, r"期望响应签名 `X-Resp-Signature`: `([0-9a-f]+)`", 1)
    body_str, body_len_doc = grab(sec, r"`RAW_BODY='([^']+)'` \((\d+) 字节 ASCII\)")
    rbody_str, rbody_len_doc = grab(sec, r"`RAW_RESP_BODY='([^']+)'` \((\d+) 字节 ASCII\)")

    SEC = bytes.fromhex(sec_hex)
    K16 = bytes.fromhex(k_hex)
    IV = bytes.fromhex(iv_hex)
    PT = bytes.fromhex("7b2270696e67223a317d")  # {"ping":1}

    def enc(iv, pt):
        e = Cipher(algorithms.AES(K16), modes.CTR(iv)).encryptor()
        return e.update(pt) + e.finalize()

    def add(b, n):
        return (int.from_bytes(b, "big") + n).to_bytes(16, "big")

    def H(key, msg):
        return hmac.new(key, msg.encode(), hashlib.sha256).hexdigest()

    fails = []

    def chk(label, got, want):
        ok = got == want
        print(("  PASS  " if ok else "  FAIL  ") + label,
              "" if ok else ("\n         got  = %s\n         want = %s" % (got, want)))
        if not ok:
            fails.append(label)

    print("=== NIST SP800-38A F.5.1 CTR-AES128 实现自检 ===")
    nk = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
    ni = bytes.fromhex("f0f1f2f3f4f5f6f7f8f9fafbfcfdfeff")
    np_ = bytes.fromhex("6bc1bee22e409f96e93d7e117393172a")
    e = Cipher(algorithms.AES(nk), modes.CTR(ni)).encryptor()
    chk("NIST 官方向量", (e.update(np_) + e.finalize()).hex(), "874d6191b620e3261bef6864990db6ce")

    print("=== 从 05 文档正文抓取向量并复算 ===")
    chk("密钥前16B 与 device_secret 前16B 一致", SEC[:16].hex(), k_hex)
    chk("AES-CTR block0 密文", enc(IV, PT).hex(), ct_hex)
    chk("信封 base64", base64.b64encode(IV + enc(IV, PT)).decode(), env_b64)
    chk("block2 nonce 计数器", add(IV, 2).hex(), nonce2)
    chk("block2 密文", enc(add(IV, 2), PT).hex(), ct2_hex)

    body = body_str.encode()
    chk("请求体字节数", len(body), int(body_len_doc))
    chk("请求体 SHA-256", hashlib.sha256(body).hexdigest(), bs_hex)
    chk("X-Signature", H(SEC, "POST\n/api/v1/ping\n1795412000\n" + hashlib.sha256(body).hexdigest()), sig_hex)

    rb = rbody_str.encode()
    chk("响应体字节数", len(rb), int(rbody_len_doc))
    chk("响应体 SHA-256", hashlib.sha256(rb).hexdigest(), rbs_hex)
    chk("X-Resp-Signature", H(SEC, "1795412000\n" + hashlib.sha256(rb).hexdigest()), rsig_hex)

    print()
    if fails:
        print("RESULT: FAIL -> " + ", ".join(fails))
        return 1
    print("RESULT: ALL PASS — 05 §3.4 向量组与文档正文自洽")
    return 0


if __name__ == "__main__":
    sys.exit(main())
