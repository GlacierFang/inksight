"""
05 §3 端云双向安全信封：`app/security.py`。

三件事：
1. 请求验签（HMAC-SHA256 over `METHOD\\nPATH\\nX-Timestamp\\nSHA256(body)`）；
2. 应用层信封加解密（`Base64(IV_16B || AES-128-CTR ciphertext)`）；
3. 响应验签 + 下载 Token 签发校验。

**IV 与计数器作用域（05 §3.2 强制）**：每个信封独立生成随机 16 字节 `IV`，
计数器作用域限于单个信封、从 0 起。严禁固定值、严禁计数器派生、严禁跨包复用。
本模块的 `encrypt` / `decrypt` 都接受显式 `iv` 参数 —— 那是**给单测复现
05 §3.4 固定向量用的**，生产路径一律走不传 `iv` 的默认分支（随机生成）。

密钥派生（05 §3.1）：AES-128 密钥 = `device_secret` 前 16 字节；
`secret_bytes` = 完整 32 字节 `device_secret`，用于 HMAC。
"""

import base64
import hashlib
import hmac
import os
import time
from typing import Optional, Tuple

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# 时间戳允许偏差（秒）—— 05 §3.2 规定 ±60s
TIMESTAMP_TOLERANCE = 60
# 下载 Token 有效期（秒）—— 05 §3.3 规定 10 分钟
TOKEN_TTL = 600


class SecurityError(Exception):
    """安全校验失败。`code` 为 05 §3.5 业务状态码。"""

    def __init__(self, message: str, code: int = 4001):
        super().__init__(message)
        self.code = code


def secret_bytes(secret_hex: str) -> bytes:
    return bytes.fromhex(secret_hex)


def aes_key(secret_hex: str) -> bytes:
    """AES-128 密钥 = `device_secret` 前 16 字节（05 §3.1 双用途派生）。"""
    return bytes.fromhex(secret_hex)[:16]


# ------------------------------------------------------------------ 计数器
def add_counter(iv: bytes, n: int) -> bytes:
    """大端序 128-bit 加法进位（04 §4.2 的 nonce_counter 公式）。"""
    return ((int.from_bytes(iv, "big") + n) % (1 << 128)).to_bytes(16, "big")


# ------------------------------------------------------------------ 加解密
def encrypt(plaintext: bytes, secret_hex: str, iv: Optional[bytes] = None) -> str:
    """
    加密业务明文，返回 `Base64(IV_16B || Ciphertext)`。

    `iv` 仅供单测复现固定向量；生产路径必须让其为 None（随机生成）。
    """
    if iv is None:
        iv = os.urandom(16)
    key = aes_key(secret_hex)
    enc = Cipher(algorithms.AES(key), modes.CTR(iv)).encryptor()
    ct = enc.update(plaintext) + enc.finalize()
    return base64.b64encode(iv + ct).decode("ascii")


def decrypt(envelope_b64: str, secret_hex: str) -> bytes:
    """解密 `Base64(IV_16B || Ciphertext)` 信封，返回业务明文。"""
    try:
        raw = base64.b64decode(envelope_b64, validate=True)
    except Exception as exc:
        raise SecurityError(f"信封 Base64 解码失败：{exc}", 4001)
    if len(raw) < 16:
        raise SecurityError("信封长度不足（IV 必须 16 字节）", 4001)
    iv, ct = raw[:16], raw[16:]
    key = aes_key(secret_hex)
    dec = Cipher(algorithms.AES(key), modes.CTR(iv)).decryptor()
    return dec.update(ct) + dec.finalize()


def encrypt_json(obj: object, secret_hex: str, iv: Optional[bytes] = None) -> str:
    import json

    return encrypt(json.dumps(obj, ensure_ascii=False).encode("utf-8"), secret_hex, iv=iv)


def decrypt_json(envelope_b64: str, secret_hex: str) -> dict:
    import json

    plain = decrypt(envelope_b64, secret_hex)
    try:
        return json.loads(plain.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SecurityError(f"业务明文不是合法 JSON：{exc}", 4001)


# ------------------------------------------------------------------ 签名
def sign_request(method: str, path: str, timestamp: str, raw_body: bytes, secret_hex: str) -> str:
    """`X-Signature` = HMAC-SHA256(secret, `METHOD\\nPATH\\nTS\\nSHA256(body)`)。"""
    body_sha = hashlib.sha256(raw_body).hexdigest()
    sign_str = f"{method}\n{path}\n{timestamp}\n{body_sha}".encode("utf-8")
    return hmac.new(secret_bytes(secret_hex), sign_str, hashlib.sha256).hexdigest()


def verify_request(
    method: str, path: str, timestamp: str, raw_body: bytes, signature: str, secret_hex: str
) -> None:
    """
    校验请求签名与时间戳窗口。失败抛 `SecurityError`（code 4001）。

    路径必须是**不含域名**的纯路径（如 `/api/v1/study/solve`）——
    设备端签名用的就是纯路径，服务端必须同口径。
    """
    if not timestamp:
        raise SecurityError("缺少 X-Timestamp", 4001)
    try:
        ts_int = int(timestamp)
    except (TypeError, ValueError):
        raise SecurityError("X-Timestamp 非十进制整数", 4001)
    if abs(int(time.time()) - ts_int) > TIMESTAMP_TOLERANCE:
        raise SecurityError("时间戳偏差超过 60 秒", 4001)
    if not signature:
        raise SecurityError("缺少 X-Signature", 4001)
    expected = sign_request(method, path, timestamp, raw_body, secret_hex)
    if not hmac.compare_digest(expected, signature):
        raise SecurityError("请求签名校验失败", 4001)


def sign_response(req_timestamp: str, raw_resp_body: bytes, secret_hex: str) -> str:
    """`X-Resp-Signature` = HMAC-SHA256(secret, `REQ_TS\\nSHA256(resp_body)`)。"""
    body_sha = hashlib.sha256(raw_resp_body).hexdigest()
    msg = f"{req_timestamp}\n{body_sha}".encode("utf-8")
    return hmac.new(secret_bytes(secret_hex), msg, hashlib.sha256).hexdigest()


def verify_response(req_timestamp: str, raw_resp_body: bytes, signature: str, secret_hex: str) -> bool:
    if not signature:
        return False
    expected = sign_response(req_timestamp, raw_resp_body, secret_hex)
    return hmac.compare_digest(expected, signature)


# ------------------------------------------------------------------ 下载 Token
def issue_token(device_id: str, rel_path: str, secret_hex: str, ttl: int = TOKEN_TTL) -> str:
    """
    签发短时下载 Token（05 §3.3）：
    `raw_token = "{device_id}|{expire_ts}|{rel_path}"` → HMAC → urlsafe_base64。
    """
    expire_ts = int(time.time()) + ttl
    raw_token = f"{device_id}|{expire_ts}|{rel_path}"
    sig = hmac.new(
        secret_bytes(secret_hex), raw_token.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return base64.urlsafe_b64encode(f"{raw_token}|{sig}".encode("utf-8")).decode("ascii")


def verify_token(token: str, device_id: str, secret_hex: str) -> str:
    """
    校验 Token 并返回 `rel_path`。失败抛 `SecurityError`（code 4001）。

    安全要点：
    * 校验签名用 `hmac.compare_digest`（常数时间，防时序侧信道）；
    * 校验过期；
    * **路径白名单防穿越** —— 解析出的相对路径不得含 `..`、不得是绝对路径；
    * 校验 `device_id` 归属，防止拿 A 设备的 Token 下载 B 设备的内容。
    """
    try:
        padded = token + "=" * (-len(token) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except Exception as exc:
        raise SecurityError(f"Token 解码失败：{exc}", 4001)
    # decoded = "{device_id}|{expire_ts}|{rel_path}|{sig}"
    # 用 rsplit 切最后一次 `|` 拿签名，其余按 maxsplit=2 拆 —— rel_path 自身含 `|` 也不会被误拆
    raw_token, sig = decoded.rsplit("|", 1)
    parts = raw_token.split("|", 2)
    if len(parts) != 3:
        raise SecurityError("Token 格式错误", 4001)
    tok_device, expire_str, rel_path = parts
    try:
        expire_ts = int(expire_str)
    except ValueError:
        raise SecurityError("Token 过期时间非法", 4001)
    if int(time.time()) > expire_ts:
        raise SecurityError("Token 已过期", 4001)
    if tok_device != device_id:
        raise SecurityError("Token 与设备不匹配", 4001)
    expected = hmac.new(
        secret_bytes(secret_hex), raw_token.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise SecurityError("Token 签名校验失败", 4001)
    # 路径白名单防穿越
    if rel_path.startswith("/") or ".." in rel_path.split("/"):
        raise SecurityError("Token 路径非法", 4001)
    return rel_path


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
