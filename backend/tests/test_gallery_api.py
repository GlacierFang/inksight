"""回顾接口（账号作用域）测试。

覆盖：
  - /api/gallery 不带 mode → 收录的模式清单；
  - /api/gallery?mode=... → 该模式的往期条目；
  - /api/gallery/image → 整屏 PNG；
  - 不带 MAC 参数也能用（回顾本来就不按设备隔离）；
  - 未登录一律拒绝。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from httpx import AsyncClient

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from api.index import app  # noqa: E402


def _client():
    try:
        from httpx import ASGITransport  # type: ignore

        transport = ASGITransport(app=app)
        return AsyncClient(transport=transport, base_url="http://test")
    except Exception:
        return AsyncClient(app=app, base_url="http://test")


@pytest.fixture
def auth_headers():
    from core.auth import create_session_token

    return {"Authorization": f"Bearer {create_session_token(9999, 'tester')}"}


@pytest.mark.asyncio
async def test_gallery_lists_modes_without_mac(auth_headers):
    async with _client() as client:
        resp = await client.get("/api/gallery", headers=auth_headers)

    assert resp.status_code == 200
    data = resp.json()
    ids = {item["mode_id"] for item in data["modes"]}
    assert "CUSTOM_HAICUOTU" in ids
    assert "CUSTOM_EWORLD" in ids
    # MAC 不该出现在响应里
    assert "mac" not in data


@pytest.mark.asyncio
async def test_gallery_unknown_mode_is_404(auth_headers):
    async with _client() as client:
        resp = await client.get(
            "/api/gallery", params={"mode": "CUSTOM_NOPE"}, headers=auth_headers
        )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_gallery_entries_shape(auth_headers):
    async with _client() as client:
        resp = await client.get(
            "/api/gallery", params={"mode": "CUSTOM_TIANGONG"}, headers=auth_headers
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "CUSTOM_TIANGONG"
    assert data["form"] == "grid"
    assert isinstance(data["entries"], list)
    assert "mac" not in data


@pytest.mark.asyncio
async def test_gallery_image_requires_login():
    async with _client() as client:
        resp = await client.get(
            "/api/gallery/image",
            params={"mode": "CUSTOM_TIANGONG", "day": "2026-10-23"},
        )

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_gallery_requires_login():
    async with _client() as client:
        resp = await client.get("/api/gallery")

    assert resp.status_code == 401
