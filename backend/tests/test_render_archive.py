"""设备实际下发的画面必须落进回顾（画廊主图）。

背景（2026-11-02）：
「窗外」不可缓存（cacheable=false），设备每次轮播到它都重新出图。
两条出图路各自漏了存档：
- /render 的「推送预览」分支直接 return，走不到存档代码；
- /api/preview 的存档条件把「只有位置字段的 mode_override」当成临时改版式跳过，
  而「窗外」的城市恰恰存在 mode_overrides 里（settings_schema 的 location）。
结果：屏上那张从没进过回顾，回顾里存的是 daily_content 另跑的一张，图文对不上。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PIL import Image

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def test_location_only_override_counts_as_formal():
    """只有位置字段的 mode_override 仍算正式画面（要存档）。"""
    from api.routes.render import _is_location_only

    assert _is_location_only(None) is True
    assert _is_location_only({}) is True
    assert _is_location_only(
        {"city": "郑州", "latitude": 34.75, "longitude": 113.65, "timezone": "Asia/Shanghai"}
    ) is True
    # 临时改版式不算正式画面
    assert _is_location_only({"memo_text": "临时"}) is False


@pytest.mark.asyncio
async def test_device_render_archives_screen(monkeypatch, tmp_path):
    """设备走 /render 拿到的那张，要落成回顾主图。"""
    from api.routes import render as render_mod
    from core import screen_archive

    monkeypatch.setattr(screen_archive, "STATE_DIR", tmp_path)

    calls: list[str] = []

    async def _fake_build_image(*args, **kwargs):
        calls.append(kwargs.get("persona") or "")
        img = Image.new("RGB", (400, 300), (255, 255, 255))
        return img, "CUSTOM_WINDOW", False, False, False, False

    async def _no_op(*args, **kwargs):
        return None

    async def _owner(*args, **kwargs):
        return {"user_id": 1, "username": "tester"}

    monkeypatch.setattr(render_mod, "build_image", _fake_build_image)
    monkeypatch.setattr(render_mod, "require_device_token", _no_op)
    monkeypatch.setattr(render_mod, "get_active_config", _no_op)
    monkeypatch.setattr(render_mod, "get_device_owner", _owner)
    monkeypatch.setattr(render_mod, "log_render_stats", _no_op)
    monkeypatch.setattr(render_mod, "consume_pending_refresh", _no_op)
    monkeypatch.setattr(render_mod, "get_device_state", _no_op)
    monkeypatch.setattr(render_mod, "get_latest_heartbeat", _no_op)

    from httpx import ASGITransport, AsyncClient
    from api.index import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/render",
            params={"mac": "20:D5:C2:0D:8F:EC", "persona": "CUSTOM_WINDOW", "w": 400, "h": 300},
        )

    assert resp.status_code == 200
    saved = tmp_path / "window" / "screens" / f"{__import__('datetime').date.today().isoformat()}.png"
    assert saved.is_file(), f"设备实际拿到的整屏图没落盘：{saved}"

    # aiosqlite 的工作线程不是 daemon，不关连接 pytest 不会退出。
    from core.db import close_all

    await close_all()


@pytest.mark.asyncio
async def test_preview_with_location_override_archives(monkeypatch):
    """配置页预览「窗外」时带了城市 override —— 那种也要存档。"""
    from api import shared as shared_mod

    seen: dict = {}

    async def _fake_generate_and_render(persona, config, date_ctx, weather, battery_pct, **kwargs):
        seen.update(kwargs)
        return Image.new("RGB", (400, 300), (255, 255, 255)), {}

    monkeypatch.setattr(shared_mod, "generate_and_render", _fake_generate_and_render)

    from httpx import ASGITransport, AsyncClient
    from api.index import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/preview",
            params={
                "persona": "CUSTOM_WINDOW",
                "mode_override": '{"city": "郑州", "latitude": 34.75, "longitude": 113.65}',
            },
        )

    assert resp.status_code == 200
    assert seen.get("archive_screen") is True, "只有位置字段的 override 被当成临时改版式跳过了"

    from core.db import close_all

    await close_all()
