from __future__ import annotations

import re
import aiosqlite
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, Request
from fastapi.responses import JSONResponse

from api.shared import require_membership_access
from core.activity_store import log_user_activity
from core.auth import require_user, validate_mac_param
from core.config_store import (
    approve_access_request,
    bind_device,
    get_device_members,
    get_device_owner,
    get_pending_requests_for_owner,
    get_user_by_username,
    get_user_devices,
    reject_access_request,
    revoke_device_member,
    share_device_with_user,
    unbind_device,
)
from core.db import get_main_db
from core.email import send_verification_code, verify_code

router = APIRouter(tags=["user"])

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


@router.get("/user/devices")
async def list_user_devices(user_id: int = Depends(require_user)):
    return {"devices": await get_user_devices(user_id)}


@router.post("/user/devices")
async def bind_user_device(body: dict, request: Request, user_id: int = Depends(require_user)):
    mac = validate_mac_param((body.get("mac") or "").strip().upper())
    nickname = (body.get("nickname") or "").strip()
    if not mac:
        return JSONResponse({"error": "MAC 地址不能为空"}, status_code=400)
    result = await bind_device(user_id, mac, nickname)
    await log_user_activity(user_id, "device.bind", request=request, metadata={"mac": mac, "role": result.get("role")})
    return {"ok": True, **result}


@router.delete("/user/devices/{mac}")
async def unbind_user_device(mac: str, force: bool = False, user_id: int = Depends(require_user)):
    result = await unbind_device(user_id, mac.upper(), force=force)
    if result == "not_found":
        return JSONResponse({"error": "设备未绑定"}, status_code=404)
    if result == "owner_has_members":
        return JSONResponse({"error": "owner 仍有共享成员，无法解绑；添加 ?force=true 强制解绑并移除所有成员"}, status_code=409)
    return {"ok": True}


@router.get("/user/devices/requests")
async def list_device_requests(user_id: int = Depends(require_user)):
    return {"requests": await get_pending_requests_for_owner(user_id)}


@router.post("/user/devices/requests/{request_id}/approve")
async def approve_device_request(request_id: int, user_id: int = Depends(require_user)):
    membership = await approve_access_request(request_id, user_id)
    if not membership:
        return JSONResponse({"error": "请求不存在或无法批准"}, status_code=404)
    return {"ok": True, "membership": membership}


@router.post("/user/devices/requests/{request_id}/reject")
async def reject_device_request(request_id: int, user_id: int = Depends(require_user)):
    ok = await reject_access_request(request_id, user_id)
    if not ok:
        return JSONResponse({"error": "请求不存在或无法拒绝"}, status_code=404)
    return {"ok": True}


@router.get("/user/devices/{mac}/members")
async def list_device_members_route(
    mac: str,
    request: Request,
    ink_session: Optional[str] = Cookie(default=None),
):
    await require_membership_access(request, mac.upper(), ink_session)
    members = await get_device_members(mac.upper())
    owner = await get_device_owner(mac.upper())
    return {"mac": mac.upper(), "members": members, "owner_user_id": owner["user_id"] if owner else None}


@router.post("/user/devices/{mac}/share")
async def share_device_access(
    mac: str,
    body: dict,
    request: Request,
    ink_session: Optional[str] = Cookie(default=None),
):
    owner = await require_membership_access(request, mac.upper(), ink_session, owner_only=True)
    username = str(body.get("username") or "").strip()
    if not username:
        return JSONResponse({"error": "用户名不能为空"}, status_code=400)
    target_user = await get_user_by_username(username)
    if not target_user:
        return JSONResponse({"error": "目标用户不存在"}, status_code=404)
    return {"ok": True, **await share_device_with_user(owner["user_id"], mac.upper(), target_user["id"])}


@router.delete("/user/devices/{mac}/members/{target_user_id}")
async def remove_device_member(
    mac: str,
    target_user_id: int,
    request: Request,
    ink_session: Optional[str] = Cookie(default=None),
):
    owner = await require_membership_access(request, mac.upper(), ink_session, owner_only=True)
    ok = await revoke_device_member(owner["user_id"], mac.upper(), target_user_id)
    if not ok:
        return JSONResponse({"error": "成员不存在或无法移除"}, status_code=404)
    return {"ok": True}


@router.get("/user/profile")
async def get_user_profile(request: Request, user_id: int = Depends(require_user)):
    db = await get_main_db()

    cursor = await db.execute(
        "SELECT id, username, phone, email, role FROM users WHERE id = ?",
        (user_id,),
    )
    user_row = await cursor.fetchone()
    if not user_row:
        return JSONResponse({"error": "用户不存在"}, status_code=404)
    await log_user_activity(user_id, "profile.open", request=request)

    return {
        "user_id": user_row[0],
        "username": user_row[1],
        "phone": user_row[2] or "",
        "email": user_row[3] or "",
        "role": user_row[4] or "user",
    }


@router.post("/user/bind-email/send-code")
async def bind_email_send_code(body: dict, user_id: int = Depends(require_user)):
    """Send a verification code to the new email address."""
    email = (body.get("email") or "").strip().lower()
    if not email or not _EMAIL_RE.match(email):
        return JSONResponse({"error": "邮箱格式不正确"}, status_code=400)

    db = await get_main_db()
    cursor = await db.execute("SELECT id FROM users WHERE email = ? AND id != ?", (email, user_id))
    if await cursor.fetchone():
        return JSONResponse({"error": "该邮箱已被其他账号使用"}, status_code=409)

    ok, message = await send_verification_code(email)
    if not ok:
        return JSONResponse({"error": message}, status_code=429)
    return {"ok": True, "message": message}


@router.post("/user/bind-email")
async def bind_email(body: dict, user_id: int = Depends(require_user)):
    """Verify code and bind email to the current user."""
    email = (body.get("email") or "").strip().lower()
    code = (body.get("code") or "").strip()

    if not email or not _EMAIL_RE.match(email):
        return JSONResponse({"error": "邮箱格式不正确"}, status_code=400)
    if not code:
        return JSONResponse({"error": "验证码不能为空"}, status_code=400)
    if not verify_code(email, code):
        return JSONResponse({"error": "验证码无效或已过期"}, status_code=400)

    db = await get_main_db()
    cursor = await db.execute("SELECT id FROM users WHERE email = ? AND id != ?", (email, user_id))
    if await cursor.fetchone():
        return JSONResponse({"error": "该邮箱已被其他账号使用"}, status_code=409)

    await db.execute("UPDATE users SET email = ? WHERE id = ?", (email, user_id))
    await db.commit()
    return {"ok": True, "message": "邮箱绑定成功"}
