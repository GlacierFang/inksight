"""相册：用户自己上传的照片集合，每天换一张。

与相框（MY_ADAPTIVE）的区别：
- 相框是「刷新一次换一张」，由设备端游标 photo_frame_index 推进；
- 相册是「每天换一张」，同一天内刷新多少次都是同一张，第二天自动换下一张。

选图规则（确定性，不依赖任何服务端游标）：
- 相册里记一个 anchor_date（第一张照片上传那天的日期）；
- 第 N 天显示第 N 张（(今天 - anchor_date).days % 张数），循环往复；
- 增删照片只改列表长度，取模自动跟随，不影响「同一天同一张」。

存储（**按账号，一套**）：
    runtime_album/U<user_id>/manifest.json   照片清单 + anchor_date
    runtime_album/U<user_id>/<id>.<ext>       原图字节

相册属于**账号**、不属于设备：
- 设备配置页上传 → 写进这台设备 owner 的账号相册；
- 「无设备体验」页上传 → 写进当前登录账号的相册；
- 设备渲染时显示的也是 owner 账号的相册。
所以无设备页体验到的照片，和真机显示的是同一批——体验即交付。
设备之间不再各存一份，也不需要额外字段记录「这台屏显示谁的相册」。

相册不配文字：只出图，不带标题、备注、日期等任何文案。
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
ALBUM_DIR = _BACKEND_ROOT / "runtime_album"

# 用户最多上传 7 张
MAX_ALBUM_PHOTOS = 7

# 账号相册的目录前缀：runtime_album/U<user_id>/
ACCOUNT_PREFIX = "U"

_EXT_BY_TYPE = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
    "image/tiff": ".tif",
    "image/avif": ".avif",
}


# ── 路径 ──────────────────────────────────────────────────────────


def _account_key(account: str) -> str:
    """把账号标识规整成可作目录名的 key（去掉分隔符，统一大写）。"""
    cleaned = "".join(ch for ch in str(account or "").upper() if ch.isalnum())
    return cleaned or "UNKNOWN"


def account_bucket(user_id: int) -> str:
    """账号相册桶：U<user_id>。"""
    return f"{ACCOUNT_PREFIX}{int(user_id)}"


def album_dir(account: str) -> Path:
    return ALBUM_DIR / _account_key(account)


def _manifest_path(account: str) -> Path:
    return album_dir(account) / "manifest.json"


def _photo_path(account: str, filename: str) -> Path:
    return album_dir(account) / filename


# ── 清单读写 ──────────────────────────────────────────────────────


def load_manifest(account: str) -> dict:
    path = _manifest_path(account)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {"photos": []}
    if not isinstance(data, dict):
        return {"photos": []}
    photos = data.get("photos")
    data["photos"] = [p for p in photos if isinstance(p, dict) and p.get("id")] if isinstance(photos, list) else []
    return data


def save_manifest(account: str, data: dict) -> None:
    directory = album_dir(account)
    directory.mkdir(parents=True, exist_ok=True)
    target = _manifest_path(account)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, target)


def _public_photo(photo: dict) -> dict:
    """照片的对外字段。取图 URL 由接口层按访问方式补，不在存储层绑定。"""
    return {
        "id": photo.get("id", ""),
        "filename": photo.get("filename", ""),
        "content_type": photo.get("content_type", "image/jpeg"),
        "uploaded_at": photo.get("uploaded_at", ""),
    }


def list_photos(account: str) -> list[dict]:
    manifest = load_manifest(account)
    return [_public_photo(p) for p in manifest.get("photos", [])]


def photo_count(account: str) -> int:
    return len(load_manifest(account).get("photos", []))


# ── 增删 ──────────────────────────────────────────────────────────


def add_photo(account: str, payload: bytes, content_type: str, filename: str = "") -> dict:
    """写入一张照片。超过上限直接拒绝，不做静默丢弃。"""
    manifest = load_manifest(account)
    photos = manifest.get("photos", [])
    if len(photos) >= MAX_ALBUM_PHOTOS:
        raise ValueError(f"album is full (max {MAX_ALBUM_PHOTOS})")

    photo_id = uuid.uuid4().hex
    ext = _EXT_BY_TYPE.get((content_type or "").lower(), ".jpg")
    stored_name = f"{photo_id}{ext}"
    directory = album_dir(account)
    directory.mkdir(parents=True, exist_ok=True)
    _photo_path(account, stored_name).write_bytes(payload)

    entry = {
        "id": photo_id,
        "filename": filename or stored_name,
        "content_type": content_type or "image/jpeg",
        "uploaded_at": datetime.now().isoformat(timespec="seconds"),
        "stored_name": stored_name,
    }
    photos.append(entry)
    manifest["photos"] = photos
    if not manifest.get("anchor_date"):
        manifest["anchor_date"] = date.today().isoformat()
    save_manifest(account, manifest)
    return _public_photo(entry)


def delete_photo(account: str, photo_id: str) -> bool:
    manifest = load_manifest(account)
    photos = manifest.get("photos", [])
    target = next((p for p in photos if p.get("id") == photo_id), None)
    if target is None:
        return False
    photos = [p for p in photos if p.get("id") != photo_id]
    manifest["photos"] = photos
    if not photos:
        manifest.pop("anchor_date", None)
    save_manifest(account, manifest)
    stored_name = target.get("stored_name") or f"{photo_id}.jpg"
    try:
        _photo_path(account, stored_name).unlink(missing_ok=True)
    except OSError:
        logger.warning("[ALBUM] Failed to remove photo file %s", stored_name, exc_info=True)
    return True


def read_photo(account: str, photo_id: str) -> tuple[bytes, str] | None:
    manifest = load_manifest(account)
    for photo in manifest.get("photos", []):
        if photo.get("id") != photo_id:
            continue
        stored_name = photo.get("stored_name") or f"{photo_id}.jpg"
        path = _photo_path(account, stored_name)
        try:
            return path.read_bytes(), photo.get("content_type", "image/jpeg")
        except OSError:
            logger.warning("[ALBUM] Failed to read photo %s", photo_id, exc_info=True)
            return None
    return None


# ── 选图 ──────────────────────────────────────────────────────────


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    for fmt, candidate in (
        ("%Y-%m-%d", text[:10]),
        ("%Y-%m-%dT%H:%M:%S", text[:19]),
    ):
        try:
            return datetime.strptime(candidate, fmt).date()
        except ValueError:
            continue
    return None


def select_photo(account: str, on_date: date | None = None) -> dict | None:
    """按日期选出当天该显示的那张照片。同一天永远同一张。"""
    manifest = load_manifest(account)
    photos = manifest.get("photos", [])
    if not photos:
        return None
    today = on_date or date.today()
    anchor = _parse_date(manifest.get("anchor_date"))
    if anchor is None:
        anchor = _parse_date(photos[0].get("uploaded_at")) or today
    offset = (today - anchor).days
    index = offset % len(photos)
    return photos[index]


def _today_from_kwargs(kwargs: dict) -> date:
    """优先用渲染上下文里的日期，保证设备端与预览端同一天出同一张。"""
    date_ctx = kwargs.get("date_ctx") or {}
    if isinstance(date_ctx, dict):
        year = date_ctx.get("year")
        day_of_year = date_ctx.get("day_of_year")
        try:
            if year and day_of_year:
                return date(int(year), 1, 1) + timedelta(days=int(day_of_year) - 1)
        except (TypeError, ValueError):
            pass
    return date.today()


async def build_album_content(fallback: dict, **kwargs) -> dict:
    """computed provider：产出当天那张照片。

    约定：
    - image_url 指向相册原始图接口（设备渲染走设备路由，无设备预览走 user 路由，
      两者最终落到同一个账号相册）；
    - _prefetched_image_url 直接带上字节，渲染器优先用它，不再走网络回环。

    账号来源：album_bucket（U<user_id>）。没有就出不了图——
    相册只按账号存，不再拿 MAC 当桶。
    """
    content = dict(fallback or {})
    account = str(kwargs.get("album_bucket") or "").strip().upper()
    if not account:
        return content

    photo = select_photo(account, _today_from_kwargs(kwargs))
    if not photo:
        return content

    data = read_photo(account, photo.get("id", ""))
    if not data:
        return content
    payload, _content_type = data

    mac = str(kwargs.get("mac") or "").strip().upper()
    url_prefix = f"/api/album/{mac}" if mac else "/api/album/user"
    content["image_url"] = f"{url_prefix}/photos/{photo.get('id', '')}/raw"
    content["_prefetched_image_url"] = payload
    return content
