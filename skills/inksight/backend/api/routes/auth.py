from __future__ import annotations

import re
from datetime import datetime

import aiosqlite
import phonenumbers
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from phonenumbers.phonenumberutil import NumberParseException

from core.activity_store import log_user_activity
from core.auth import clear_session_cookie, create_session_token, require_user, set_session_cookie
from core.config_store import authenticate_user, _hash_password
from core.db import get_main_db
from core.email import send_verification_code, verify_code

router = APIRouter(tags=["auth"])


_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")

_COMMON_PHONE_REGIONS = {
    "CN", "US", "CA", "GB", "JP", "KR", "SG", "HK", "TW", "DE", "FR", "AU", "IN",
}


def _normalize_phone(phone: str, phone_region: str = "") -> str:
    raw = (phone or "").strip()
    if not raw:
        return ""
    region = (phone_region or "").strip().upper()
    if region and region not in _COMMON_PHONE_REGIONS:
        raise ValueError("unsupported_region")
    default_region = region or "CN"
    try:
        parsed = phonenumbers.parse(raw, None if raw.startswith("+") else default_region)
    except NumberParseException as exc:
        raise ValueError("invalid_phone") from exc
    if not phonenumbers.is_valid_number(parsed):
        raise ValueError("invalid_phone")
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def _phone_lookup_candidates(normalized_phone: str) -> tuple[str, ...]:
    if not normalized_phone:
        return ()
    candidates = {normalized_phone}
    if normalized_phone.startswith("+86"):
        candidates.add(normalized_phone[3:])
    return tuple(candidates)


async def _phone_exists(db, normalized_phone: str) -> bool:
    if not normalized_phone:
        return False
    candidates = _phone_lookup_candidates(normalized_phone)
    cursor = await db.execute(
        f"SELECT 1 FROM users WHERE phone IN ({','.join('?' for _ in candidates)}) LIMIT 1",
        tuple(candidates),
    )
    return await cursor.fetchone() is not None


@router.post("/auth/register")
async def auth_register(body: dict, response: Response, request: Request = None):
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    phone = (body.get("phone") or "").strip()
    phone_region = (body.get("phone_region") or "").strip()
    email = (body.get("email") or "").strip()

    if not username or len(username) < 2 or len(username) > 30:
        return JSONResponse({"error": "用户名长度须为 2-30 字符"}, status_code=400)
    if len(password) < 4:
        return JSONResponse({"error": "密码至少 4 位"}, status_code=400)

    if not email:
        return JSONResponse({"error": "邮箱为必填项"}, status_code=400)
    if not _EMAIL_RE.match(email):
        return JSONResponse({"error": "邮箱格式不正确"}, status_code=400)
    normalized_phone = ""
    if phone:
        try:
            normalized_phone = _normalize_phone(phone, phone_region)
        except ValueError:
            return JSONResponse({"error": "手机号格式不正确"}, status_code=400)

    db = await get_main_db()
    now = datetime.now().isoformat()
    pw_hash, _ = _hash_password(password)

    try:
        # 显式开启事务，确保「创建用户」原子完成
        await db.execute("BEGIN")
        if normalized_phone and await _phone_exists(db, normalized_phone):
            await db.rollback()
            return JSONResponse({"error": "用户名或手机号/邮箱已存在"}, status_code=409)

        # 1) 创建用户记录（用户名 + 手机/邮箱）
        cursor = await db.execute(
            """
            INSERT INTO users (username, password_hash, phone, email, role, created_at)
            VALUES (?, ?, ?, ?, 'user', ?)
            """,
            (
                username,
                pw_hash,
                normalized_phone or None,
                email or None,
                now,
            ),
        )
        user_id = cursor.lastrowid

        await db.commit()
    except aiosqlite.IntegrityError:
        # 用户名 / 手机号 / 邮箱任一唯一字段冲突
        await db.rollback()
        return JSONResponse({"error": "用户名或手机号/邮箱已存在"}, status_code=409)
    except Exception:
        await db.rollback()
        raise

    token = create_session_token(user_id, username)
    set_session_cookie(response, token)
    await log_user_activity(user_id, "auth.register", request=request, metadata={"username": username})
    return {"ok": True, "user_id": user_id, "username": username, "token": token}


@router.post("/auth/login")
async def auth_login(body: dict, response: Response, request: Request = None):
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    user = await authenticate_user(username, password)
    if not user:
        return JSONResponse({"error": "用户名或密码错误"}, status_code=401)
    token = create_session_token(user["id"], user["username"])
    set_session_cookie(response, token)
    await log_user_activity(user["id"], "auth.login", request=request)
    return {"ok": True, "user_id": user["id"], "username": user["username"], "token": token}


@router.post("/auth/reset-password/send-code")
async def auth_reset_send_code(body: dict):
    """Step 1: send a verification code to the user's registered email."""
    email = (body.get("email") or "").strip().lower()
    if not email or not _EMAIL_RE.match(email):
        return JSONResponse({"error": "邮箱格式不正确"}, status_code=400)

    db = await get_main_db()
    cursor = await db.execute("SELECT id FROM users WHERE email = ? LIMIT 1", (email,))
    if not await cursor.fetchone():
        return JSONResponse({"error": "该邮箱未注册"}, status_code=404)

    ok, message = await send_verification_code(email)
    if not ok:
        return JSONResponse({"error": message}, status_code=429)
    return {"ok": True, "message": message}


@router.post("/auth/reset-password")
async def auth_reset_password(body: dict):
    """Step 2: verify code and set new password."""
    email = (body.get("email") or "").strip().lower()
    code = (body.get("code") or "").strip()
    password = body.get("password") or ""

    if not email or not _EMAIL_RE.match(email):
        return JSONResponse({"error": "邮箱格式不正确"}, status_code=400)
    if not code:
        return JSONResponse({"error": "验证码不能为空"}, status_code=400)
    if len(password) < 4:
        return JSONResponse({"error": "密码至少 4 位"}, status_code=400)

    if not verify_code(email, code):
        return JSONResponse({"error": "验证码无效或已过期"}, status_code=400)

    db = await get_main_db()
    cursor = await db.execute("SELECT id FROM users WHERE email = ? LIMIT 1", (email,))
    row = await cursor.fetchone()
    if not row:
        return JSONResponse({"error": "该邮箱未注册"}, status_code=404)

    pw_hash, _ = _hash_password(password)
    await db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (pw_hash, row[0]))
    await db.commit()
    return {"ok": True, "message": "密码已重置，请重新登录"}


@router.get("/auth/me")
async def auth_me(user_id: int = Depends(require_user)):
    from core.db import get_main_db

    db = await get_main_db()
    cursor = await db.execute("SELECT id, username, created_at FROM users WHERE id = ?", (user_id,))
    row = await cursor.fetchone()
    if not row:
        return JSONResponse({"error": "用户不存在"}, status_code=404)
    return {"user_id": row[0], "username": row[1], "created_at": row[2]}


@router.post("/auth/logout")
async def auth_logout(response: Response):
    clear_session_cookie(response)
    return {"ok": True}
