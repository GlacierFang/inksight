"""相册接口：上传 / 列表 / 删除 / 原图读取。

相册按**账号**存（runtime_album/U<user_id>/），设备之间不各存一份：
- 设备作用域 /album/<MAC>/... → 解析这台设备 owner 的账号相册（设备配置页用）
- 账号作用域 /album/user/... → 当前登录用户的账号相册（无设备体验页用）
两者落到同一份数据：无设备体验页看到的照片，和真机显示的是同一批。

说明：
- 运行环境的服务 venv 没有装 python-multipart，FastAPI 的 File/UploadFile
  用不了，所以这里自己解析 multipart（沿用 api/routes/uploads.py 的做法：
  裸 body + 按 boundary 手工切段），不引入新依赖。
- 鉴权：设备作用域走 ensure_web_or_device_access（设备 token 或登录用户 Cookie /
  Bearer 头都行）；账号作用域要求登录用户。前端缩略图用 <img>，带不了
  Authorization 头，靠 ink_session Cookie 认证。
"""

from __future__ import annotations

import io
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Cookie, Header, Request
from fastapi.responses import JSONResponse, Response
from PIL import Image, UnidentifiedImageError

from api.shared import ensure_web_or_device_access, limiter
from core.album import (
    MAX_ALBUM_PHOTOS,
    account_bucket,
    add_photo,
    delete_photo,
    list_photos,
    read_photo,
)
from core.auth import validate_mac_param

router = APIRouter(tags=["album"])
logger = logging.getLogger(__name__)

# 服务端兜底上限：只防住异常大的请求体，不是产品层的上传限制。
_MAX_REQUEST_BYTES = 50 * 1024 * 1024


def _err(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": code, "message": message}, status_code=status)


def _with_url(photo: dict, url_prefix: str) -> dict:
    """补上取图 URL。URL 跟访问方式走（设备路由 / 用户路由），存储只有一份。"""
    return {**photo, "url": f"{url_prefix}/photos/{photo.get('id', '')}/raw"}


def _validate_image_payload(payload: bytes, claimed_content_type: str) -> str:
    try:
        with Image.open(io.BytesIO(payload)) as img:
            img.verify()
        with Image.open(io.BytesIO(payload)) as img:
            detected = Image.MIME.get(img.format or "", "")
    except (UnidentifiedImageError, OSError):
        raise ValueError("invalid image payload")

    content_type = detected or claimed_content_type
    if not content_type.startswith("image/"):
        raise ValueError("only image/* is allowed")
    return content_type


def _extract_multipart_file(
    payload: bytes, content_type_header: str,
) -> tuple[bytes, str, str]:
    """从 multipart/form-data 里取出名为 file 的那一段。

    返回 (文件字节, 该段声明的 content-type, 原始文件名)。
    """
    content_type = (content_type_header or "").strip()
    if "multipart/form-data" not in content_type.lower():
        raise ValueError("unsupported content type")

    boundary_key = "boundary="
    lower = content_type.lower()
    idx = lower.find(boundary_key)
    if idx < 0:
        raise ValueError("missing multipart boundary")
    boundary = content_type[idx + len(boundary_key):].strip().strip('"')
    if not boundary:
        raise ValueError("missing multipart boundary")

    marker = f"--{boundary}".encode()
    for part in payload.split(marker):
        # 只剥掉分段边界自带的 CRLF，不能用 strip()——那会把图片首尾的空白字节也吃掉。
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        if b"\r\n\r\n" not in part:
            continue
        header_blob, body = part.split(b"\r\n\r\n", 1)
        body = body.rstrip(b"\r\n")
        headers: dict[str, str] = {}
        for raw_line in header_blob.split(b"\r\n"):
            if b":" not in raw_line:
                continue
            name, value = raw_line.split(b":", 1)
            headers[name.decode("utf-8", errors="ignore").strip().lower()] = (
                value.decode("utf-8", errors="ignore").strip()
            )

        disposition = headers.get("content-disposition", "")
        if 'name="file"' not in disposition:
            continue

        part_content_type = headers.get("content-type", "").strip().lower()
        if not part_content_type.startswith("image/"):
            raise ValueError("only image/* is allowed")
        if not body:
            raise ValueError("missing file")

        filename = ""
        if "filename=" in disposition:
            raw = disposition.split("filename=", 1)[1].strip()
            filename = raw.strip('"').split("\\")[-1].split("/")[-1]
        return body, part_content_type, filename

    raise ValueError("missing file")


class _UploadError(Exception):
    """上传解析失败。"""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


async def _process_upload(request: Request) -> tuple[bytes, str, str]:
    """从请求体里取出 (图片字节, content-type, 文件名)。"""
    payload = await request.body()
    if not payload:
        raise _UploadError("invalid_request", "missing file")
    if len(payload) > _MAX_REQUEST_BYTES:
        raise _UploadError("file_too_large", "file too large", 413)

    content_type_header = (request.headers.get("content-type") or "").strip()
    claimed_content_type = (request.headers.get("x-upload-content-type") or "").strip().lower()

    if claimed_content_type.startswith("image/"):
        image_payload = payload
        filename = ""
    elif "multipart/form-data" in content_type_header.lower():
        try:
            image_payload, claimed_content_type, filename = _extract_multipart_file(
                payload, content_type_header,
            )
        except ValueError as exc:
            raise _UploadError("invalid_request", str(exc)) from exc
    else:
        raise _UploadError("invalid_file", "only image/* is allowed")

    try:
        content_type = _validate_image_payload(image_payload, claimed_content_type)
    except ValueError as exc:
        raise _UploadError("invalid_file", str(exc)) from exc

    return image_payload, content_type, filename


async def _device_account(mac: str) -> Optional[str]:
    """设备作用域 → 这台设备 owner 的账号相册。设备没绑账号就返回 None。"""
    from core.config_store import get_device_owner

    owner = await get_device_owner(mac)
    if not owner:
        return None
    try:
        user_id = int(owner.get("user_id"))
    except (TypeError, ValueError):
        return None
    return account_bucket(user_id)


async def _self_account(request: Request, ink_session: Optional[str]) -> str:
    """账号作用域 → 当前登录用户的账号相册。未登录直接 401。"""
    from core.auth import require_user

    user_id = await require_user(request, ink_session)
    return account_bucket(user_id)


# ── 账号作用域（无设备体验页）───────────────────────────────────────
# 注意：这几条必须注册在 /album/{mac}/... 之前，
# 否则 "user" 会被当成 MAC 匹配掉（validate_mac_param 会直接 400）。

@router.get("/album/user/photos")
@limiter.limit("60/minute")
async def list_user_album_photos(
    request: Request,
    ink_session: Optional[str] = Cookie(default=None),
):
    account = await _self_account(request, ink_session)
    photos = [_with_url(p, "/api/album/user") for p in list_photos(account)]
    return JSONResponse(
        {
            "ok": True,
            "scope": "user",
            "max_photos": MAX_ALBUM_PHOTOS,
            "count": len(photos),
            "photos": photos,
        }
    )


@router.post("/album/user/photos")
@limiter.limit("30/minute")
async def upload_user_album_photo(
    request: Request,
    ink_session: Optional[str] = Cookie(default=None),
):
    account = await _self_account(request, ink_session)

    try:
        image_payload, content_type, filename = await _process_upload(request)
    except _UploadError as exc:
        return _err(exc.code, exc.message, exc.status)

    try:
        photo = add_photo(account, image_payload, content_type, filename)
    except ValueError as exc:
        return _err("album_full", str(exc), 400)

    count = len(list_photos(account))
    logger.info("[ALBUM] Uploaded photo %s for account %s (%d/%d)", photo.get("id"), account, count, MAX_ALBUM_PHOTOS)
    return JSONResponse({"ok": True, "photo": _with_url(photo, "/api/album/user"), "count": count})


@router.delete("/album/user/photos/{photo_id}")
@limiter.limit("60/minute")
async def remove_user_album_photo(
    photo_id: str,
    request: Request,
    ink_session: Optional[str] = Cookie(default=None),
):
    account = await _self_account(request, ink_session)

    try:
        uuid.UUID(photo_id)
    except ValueError:
        return _err("not_found", "photo not found", 404)

    if not delete_photo(account, photo_id):
        return _err("not_found", "photo not found", 404)

    logger.info("[ALBUM] Deleted photo %s for account %s", photo_id, account)
    return JSONResponse({"ok": True, "count": len(list_photos(account))})


@router.get("/album/user/photos/{photo_id}/raw")
@limiter.limit("120/minute")
async def get_user_album_photo_raw(
    photo_id: str,
    request: Request,
    ink_session: Optional[str] = Cookie(default=None),
):
    account = await _self_account(request, ink_session)

    try:
        uuid.UUID(photo_id)
    except ValueError:
        return _err("not_found", "photo not found", 404)

    found = read_photo(account, photo_id)
    if not found:
        return _err("not_found", "photo not found", 404)
    payload, content_type = found
    return Response(
        content=payload,
        media_type=content_type or "image/jpeg",
        headers={"Cache-Control": "private, max-age=0, must-revalidate"},
    )


# ── 设备作用域（设备配置页）──────────────────────────────────────────
# 注意：这几条必须注册在 /album/user/... 之后，
# 否则 "user" 会被当成 MAC 匹配掉（validate_mac_param 会直接 400）。

@router.get("/album/{mac}/photos")
@limiter.limit("60/minute")
async def list_album_photos(
    mac: str,
    request: Request,
    x_device_token: Optional[str] = Header(default=None),
    ink_session: Optional[str] = Cookie(default=None),
):
    mac = validate_mac_param(mac)
    await ensure_web_or_device_access(request, mac, x_device_token, ink_session)
    account = await _device_account(mac)
    if not account:
        return _err("device_unbound", "device has no owner account yet", 409)
    photos = [_with_url(p, f"/api/album/{mac}") for p in list_photos(account)]
    return JSONResponse(
        {
            "ok": True,
            "scope": "device",
            "mac": mac,
            "max_photos": MAX_ALBUM_PHOTOS,
            "count": len(photos),
            "photos": photos,
        }
    )


@router.post("/album/{mac}/photos")
@limiter.limit("30/minute")
async def upload_album_photo(
    mac: str,
    request: Request,
    x_device_token: Optional[str] = Header(default=None),
    ink_session: Optional[str] = Cookie(default=None),
):
    mac = validate_mac_param(mac)
    await ensure_web_or_device_access(request, mac, x_device_token, ink_session)
    account = await _device_account(mac)
    if not account:
        return _err("device_unbound", "device has no owner account yet", 409)

    try:
        image_payload, content_type, filename = await _process_upload(request)
    except _UploadError as exc:
        return _err(exc.code, exc.message, exc.status)

    try:
        photo = add_photo(account, image_payload, content_type, filename)
    except ValueError as exc:
        return _err("album_full", str(exc), 400)

    count = len(list_photos(account))
    logger.info("[ALBUM] Uploaded photo %s for device %s (account %s, %d/%d)", photo.get("id"), mac, account, count, MAX_ALBUM_PHOTOS)
    return JSONResponse({"ok": True, "photo": _with_url(photo, f"/api/album/{mac}"), "count": count})


@router.delete("/album/{mac}/photos/{photo_id}")
@limiter.limit("60/minute")
async def remove_album_photo(
    mac: str,
    photo_id: str,
    request: Request,
    x_device_token: Optional[str] = Header(default=None),
    ink_session: Optional[str] = Cookie(default=None),
):
    mac = validate_mac_param(mac)
    await ensure_web_or_device_access(request, mac, x_device_token, ink_session)
    account = await _device_account(mac)
    if not account:
        return _err("device_unbound", "device has no owner account yet", 409)

    try:
        uuid.UUID(photo_id)
    except ValueError:
        return _err("not_found", "photo not found", 404)

    if not delete_photo(account, photo_id):
        return _err("not_found", "photo not found", 404)

    logger.info("[ALBUM] Deleted photo %s for device %s", photo_id, mac)
    return JSONResponse({"ok": True, "count": len(list_photos(account))})


@router.get("/album/{mac}/photos/{photo_id}/raw")
@limiter.limit("120/minute")
async def get_album_photo_raw(
    mac: str,
    photo_id: str,
    request: Request,
    x_device_token: Optional[str] = Header(default=None),
    ink_session: Optional[str] = Cookie(default=None),
):
    mac = validate_mac_param(mac)
    await ensure_web_or_device_access(request, mac, x_device_token, ink_session)
    account = await _device_account(mac)
    if not account:
        return _err("device_unbound", "device has no owner account yet", 409)

    try:
        uuid.UUID(photo_id)
    except ValueError:
        return _err("not_found", "photo not found", 404)

    found = read_photo(account, photo_id)
    if not found:
        return _err("not_found", "photo not found", 404)
    payload, content_type = found
    return Response(
        content=payload,
        media_type=content_type or "image/jpeg",
        headers={"Cache-Control": "private, max-age=0, must-revalidate"},
    )
