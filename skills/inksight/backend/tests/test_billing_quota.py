"""
测试账号基础功能：
- 用户注册（带/不带邀请码）
- 密码重置

注：计费与额度体系（免费额度 / 邀请码兑换）已整体下线，不再有对应测试。
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from api import shared as shared_api

from core.config_store import (
    init_db,
    create_user,
    authenticate_user,
)
from core.db import get_main_db


@pytest.fixture(autouse=True)
async def use_memory_db(tmp_path):
    """Redirect all DB operations to an isolated temp file per test."""
    from core import db as db_mod

    db_path = str(tmp_path / "test.db")
    await db_mod.close_all()
    with patch.object(db_mod, "_MAIN_DB_PATH", db_path), \
         patch("core.config_store.DB_PATH", db_path), \
         patch("core.stats_store.DB_PATH", db_path):
        yield db_path
    await db_mod.close_all()


class TestUserRegistration:
    """测试用户注册功能"""

    @pytest.mark.asyncio
    async def test_register_basic(self):
        """测试注册成功返回用户 ID"""
        await init_db()
        
        from api.routes.auth import auth_register
        from fastapi import Response
        
        body = {
            "username": "testuser2",
            "password": "testpass123",
            "email": "test@example.com",
        }
        response = Response()
        result = await auth_register(body, response)
        
        assert result["ok"] is True
        assert result["user_id"]

    @pytest.mark.asyncio
    async def test_register_phone_email_validation(self):
        """测试手机号和邮箱格式验证"""
        await init_db()
        
        from api.routes.auth import auth_register
        from fastapi import Response
        from fastapi.responses import JSONResponse
        
        # 测试无效邮箱
        body1 = {
            "username": "testuser5",
            "password": "testpass123",
            "email": "invalid-email",
        }
        response1 = Response()
        result1 = await auth_register(body1, response1)
        assert isinstance(result1, JSONResponse)
        assert result1.status_code == 400
        assert "邮箱格式不正确" in result1.body.decode()

        # 测试邮箱未提供
        body2 = {
            "username": "testuser6",
            "password": "testpass123",
        }
        response2 = Response()
        result2 = await auth_register(body2, response2)
        assert isinstance(result2, JSONResponse)
        assert result2.status_code == 400
        assert "邮箱为必填项" in result2.body.decode()

        # 测试无效手机号（email 有效但 phone 无效）
        body3 = {
            "username": "testuser7",
            "password": "testpass123",
            "email": "valid@example.com",
            "phone": "1234567890",
        }
        response3 = Response()
        result3 = await auth_register(body3, response3)
        assert isinstance(result3, JSONResponse)
        assert result3.status_code == 400
        assert "手机号格式不正确" in result3.body.decode()

    @pytest.mark.asyncio
    async def test_register_supports_phone_region_and_normalizes(self):
        await init_db()

        from api.routes.auth import auth_register
        from fastapi import Response

        response = Response()
        result = await auth_register(
            {
                "username": "intluser",
                "password": "testpass123",
                "email": "intluser@example.com",
                "phone": "415 555 2671",
                "phone_region": "US",
            },
            response,
        )
        assert result["ok"] is True

        db = await get_main_db()
        cursor = await db.execute("SELECT phone FROM users WHERE id = ?", (result["user_id"],))
        row = await cursor.fetchone()
        assert row[0] == "+14155552671"

    @pytest.mark.asyncio
    async def test_register_keeps_legacy_cn_input_compatible(self):
        await init_db()

        from api.routes.auth import auth_register
        from fastapi import Response

        response = Response()
        result = await auth_register(
            {
                "username": "cnuser",
                "password": "testpass123",
                "email": "cnuser@example.com",
                "phone": "13800138000",
            },
            response,
        )
        assert result["ok"] is True

        db = await get_main_db()
        cursor = await db.execute("SELECT phone FROM users WHERE id = ?", (result["user_id"],))
        row = await cursor.fetchone()
        assert row[0] == "+8613800138000"

    @pytest.mark.asyncio
    async def test_register_blocks_duplicate_against_legacy_cn_storage(self):
        await init_db()

        user_id = await create_user("legacyuser", "testpass123", phone="13800138000")
        assert user_id is not None

        from api.routes.auth import auth_register
        from fastapi import Response
        from fastapi.responses import JSONResponse

        response = Response()
        result = await auth_register(
            {
                "username": "newuser",
                "password": "testpass123",
                "email": "newuser@example.com",
                "phone": "+8613800138000",
            },
            response,
        )
        assert isinstance(result, JSONResponse)
        assert result.status_code == 409


class TestPasswordReset:
    @pytest.mark.asyncio
    async def test_reset_password_with_email_verification(self):
        await init_db()

        from api.routes.auth import auth_register, auth_reset_send_code, auth_reset_password
        from core.email import _pending_codes
        from fastapi import Response

        register_response = Response()
        register_result = await auth_register(
            {
                "username": "resetuser",
                "password": "oldpass123",
                "email": "resetuser@example.com",
            },
            register_response,
        )
        assert register_result["ok"] is True

        send_result = await auth_reset_send_code({"email": "resetuser@example.com"})
        assert send_result["ok"] is True

        code = _pending_codes["resetuser@example.com"][0]

        reset_result = await auth_reset_password(
            {"email": "resetuser@example.com", "code": code, "password": "newpass123"}
        )
        assert reset_result["ok"] is True

        user = await authenticate_user("resetuser", "newpass123")
        assert user is not None

    @pytest.mark.asyncio
    async def test_reset_password_wrong_code_rejected(self):
        await init_db()

        from api.routes.auth import auth_register, auth_reset_send_code, auth_reset_password
        from fastapi import Response
        from fastapi.responses import JSONResponse

        register_response = Response()
        await auth_register(
            {
                "username": "resetuser2",
                "password": "oldpass123",
                "email": "resetuser2@example.com",
            },
            register_response,
        )
        await auth_reset_send_code({"email": "resetuser2@example.com"})

        reset_result = await auth_reset_password(
            {"email": "resetuser2@example.com", "code": "000000", "password": "newpass123"}
        )
        assert isinstance(reset_result, JSONResponse)
        assert reset_result.status_code == 400
