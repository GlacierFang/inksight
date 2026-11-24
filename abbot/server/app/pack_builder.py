"""
05 §2.2 `app/pack_builder.py`：原始文本 → 结构化 `source.json` → 加密 `cards.enc` + `index.idx`。

加密与索引格式严格按 `04` 文档第 4.1 / 4.2 节：

* `cards.enc` = `IV(16B) || AES-128-CTR(整个明文 JSONL 流)`；
  CTR 计数器**贯穿整个文件**连续递增（文件作用域），因此第 `i` 卡的密文起点
  可由字节偏移直接推块号：`block_index = (start_offset - 16) / 16`。
* `index.idx`（全部大端序 Big-Endian）：
  - `Byte 0~3`：魔数 `"IDX1"`
  - `Byte 4~5`：`uint16` 版本号（当前 1）
  - `Byte 6~7`：`uint16` 卡片总数 `N`
  - `Byte 8~11`：`uint32` 索引偏移区 `CRC32`
  - `Byte 12~`：N 个 `uint32` 起始字节偏移量（第 0 卡恒为 16，跳过 IV）
* `meta.json`：`key_type` 为 `"demo"`（固定 16 字节 ASCII 密钥）或 `"device"`
  （`device_secret` 前 16 字节）。

**IV 必须逐包随机生成**（04 §4.2）：同一密钥下复用 IV 会造成 CTR 密钥流重用，
两个包同偏移密文可直接异或还原。严禁把固定 IV 写死成打包默认值。
"""

import hashlib
import json
import os
import struct
import time
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

IDX_MAGIC = b"IDX1"
IDX_VERSION = 1
DEMO_KEY = b"STUDY_DEMO_KEY_16"  # 16 字节 ASCII，demo 包固定密钥


class PackBuildError(RuntimeError):
    """打包失败。"""


def derive_key(key_type: str, device_secret_hex: Optional[str] = None) -> bytes:
    """`key_type` 密钥路由（04 §3.3）。"""
    if key_type == "demo":
        return DEMO_KEY
    if key_type == "device":
        if not device_secret_hex:
            raise PackBuildError("key_type=device 时必须提供 device_secret")
        return bytes.fromhex(device_secret_hex)[:16]
    raise PackBuildError(f"未知 key_type：{key_type}")


def parse_source_text(raw: str) -> List[Dict[str, Any]]:
    """
    原始文本 → 结构化卡片列表（**零 AI 调用**，纯确定性解析）。

    约定格式（每张卡一块，空行分隔）：

        ## 题目：题干文字
        答案：最终答案
        提示1：第一步提示
        提示2：第二步提示
        解析：完整解析

    未标注的字段留空字符串。无法识别的块按「题干=整块」处理，保证不丢内容。
    """
    cards: List[Dict[str, Any]] = []
    for block in raw.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        card: Dict[str, Any] = {
            "id": "",
            "question": "",
            "answer": "",
            "hint1": "",
            "hint2": "",
            "analysis": "",
        }
        rest_lines: List[str] = []
        for line in block.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("##"):
                body = line.lstrip("#").strip()
                if body.startswith("题目："):
                    card["question"] = body[3:].strip()
                else:
                    rest_lines.append(line)
                continue
            for prefix, key in (
                ("答案：", "answer"),
                ("提示1：", "hint1"),
                ("提示2：", "hint2"),
                ("解析：", "analysis"),
            ):
                if line.startswith(prefix):
                    card[key] = line[len(prefix):].strip()
                    break
            else:
                rest_lines.append(line)
        if not card["question"] and rest_lines:
            card["question"] = rest_lines[0]
            rest_lines = rest_lines[1:]
        if not card["question"]:
            continue
        card["analysis"] = (card["analysis"] + "\n" + "\n".join(rest_lines)).strip() if rest_lines else card["analysis"]
        cards.append(card)
    for idx, card in enumerate(cards):
        card["id"] = f"c{idx:04d}"
    return cards


def encrypt_cards(cards: List[Dict[str, Any]], key: bytes, iv: Optional[bytes] = None) -> bytes:
    """
    卡片列表 → `cards.enc` 字节流（`IV || CTR(明文 JSONL)`）。

    `iv` 仅供单测复现固定向量；生产路径必须为 None（逐包随机）。
    """
    if iv is None:
        iv = os.urandom(16)
    plaintext = "".join(
        json.dumps(c, ensure_ascii=False, separators=(",", ":")) + "\n" for c in cards
    ).encode("utf-8")
    enc = Cipher(algorithms.AES(key), modes.CTR(iv)).encryptor()
    ct = enc.update(plaintext) + enc.finalize()
    return iv + ct


def build_index(offsets: List[int]) -> bytes:
    """`index.idx` 二进制结构（04 §4.1，全部大端序）。"""
    if not offsets:
        offsets = []
    offset_area = b"".join(struct.pack(">I", off) for off in offsets)
    crc = zlib.crc32(offset_area) & 0xFFFFFFFF
    header = IDX_MAGIC + struct.pack(">HHI", IDX_VERSION, len(offsets), crc)
    return header + offset_area


def compute_offsets(plaintext_lines: List[bytes]) -> List[int]:
    """各卡片明文行在**密文文件**中的起始字节偏移（第 0 卡恒为 16，跳过 IV）。"""
    offsets: List[int] = []
    pos = 16
    for line in plaintext_lines:
        offsets.append(pos)
        pos += len(line)
    return offsets


def build_pack(
    pack_id: str,
    name: str,
    cards: List[Dict[str, Any]],
    key_type: str = "demo",
    device_secret_hex: Optional[str] = None,
    version: int = 1,
    iv: Optional[bytes] = None,
) -> Dict[str, Any]:
    """
    构建学科包，返回 `{cards.enc, index.idx, meta.json, source.json}` 四个字节/字典。

    调用方负责落盘（推荐交给 `storage.write_pack_file`）。
    """
    if not cards:
        raise PackBuildError("卡片列表为空")
    key = derive_key(key_type, device_secret_hex)
    lines = [
        (json.dumps(c, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        for c in cards
    ]
    plaintext = b"".join(lines)
    offsets = compute_offsets(lines)

    # 加密：整个明文流用同一个 IV、CTR 计数器连续递增（文件作用域）
    if iv is None:
        iv = os.urandom(16)
    enc = Cipher(algorithms.AES(key), modes.CTR(iv)).encryptor()
    ct = enc.update(plaintext) + enc.finalize()
    cards_enc = iv + ct

    index_idx = build_index(offsets)

    source = {
        "schema_version": 1,
        "pack_id": pack_id,
        "name": name,
        "version": version,
        "key_type": key_type,
        "cards": cards,
    }
    meta = {
        "schema_version": 1,
        "pack_id": pack_id,
        "name": name,
        "total_cards": len(cards),
        "version": version,
        "key_type": key_type,
        "enc_sha256": hashlib.sha256(cards_enc).hexdigest(),
        "updated_at": int(time.time()),
    }
    return {
        "cards.enc": cards_enc,
        "index.idx": index_idx,
        "meta.json": json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"),
        "source.json": json.dumps(source, ensure_ascii=False, indent=2).encode("utf-8"),
        "meta": meta,
        "iv_hex": iv.hex(),
    }


def verify_index(index_bytes: bytes, cards_enc_size: int) -> Dict[str, Any]:
    """
    校验 `index.idx` 结构与 CRC32，并做越界检查（打包后自检）。

    返回 `{"ok": bool, "reason": str, "count": int}`。
    """
    if len(index_bytes) < 12:
        return {"ok": False, "reason": "index.idx 长度不足 12 字节", "count": 0}
    if index_bytes[:4] != IDX_MAGIC:
        return {"ok": False, "reason": "index.idx 魔数不是 IDX1", "count": 0}
    version, count, crc = struct.unpack(">HHI", index_bytes[4:12])
    if version != IDX_VERSION:
        return {"ok": False, "reason": f"index.idx 版本不支持：{version}", "count": count}
    area = index_bytes[12:12 + 4 * count]
    if len(area) != 4 * count:
        return {"ok": False, "reason": "index.idx 偏移区长度不足", "count": count}
    if (zlib.crc32(area) & 0xFFFFFFFF) != crc:
        return {"ok": False, "reason": "index.idx CRC32 校验失败", "count": count}
    offsets = list(struct.unpack(f">{count}I", area))
    for off in offsets:
        if off < 16 or off >= cards_enc_size:
            return {"ok": False, "reason": f"偏移越界：{off}", "count": count}
    return {"ok": True, "reason": "", "count": count}
