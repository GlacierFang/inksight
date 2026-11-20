"""画廊（CUSTOM_* 自建模式历史回顾）测试。

覆盖：
  - 收录清单：只有 7 个要留存的模式；
  - grid 形态：读 map / list 两种状态形状；
  - 只收真的留了整屏图的日子 —— 没图的日子不占位；
  - 字段全量带出，缺的如实标「历史缺失」；
  - 整屏图接口返回落盘的那张 PNG；
  - wide 形态：电子世界按日期现算，且不落盘；
  - 永久保留：图片清理逻辑不再删历史文件。
"""
from __future__ import annotations

import inspect
import json
import sys
from datetime import timedelta
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from core import gallery, history_render  # noqa: E402


# ── 收录清单 ────────────────────────────────────────────────
def test_gallery_covers_the_seven_kept_modes():
    ids = {item["mode_id"] for item in gallery.modes()}
    assert ids == {
        "CUSTOM_HAICUOTU", "CUSTOM_DANQING", "CUSTOM_SONGMENG",
        "CUSTOM_TANGPING", "CUSTOM_TIANGONG", "CUSTOM_ANT",
        "CUSTOM_EWORLD",
    }
    # 通用内置模式不得混进来
    assert not any(not mid.startswith("CUSTOM_") for mid in ids)


def test_gallery_forms_are_correct():
    forms = {item["mode_id"]: item["form"] for item in gallery.modes()}
    assert forms["CUSTOM_TIANGONG"] == "grid"
    assert forms["CUSTOM_TANGPING"] == "grid"
    assert forms["CUSTOM_ANT"] == "grid"
    assert forms["CUSTOM_EWORLD"] == "wide"


# ── 测试脚手架 ──────────────────────────────────────────────
@pytest.fixture
def fake_store(tmp_path, monkeypatch):
    """造一份假的 runtime_cache + static 目录。"""
    state_dir = tmp_path / "runtime_cache"
    static_dir = tmp_path / "static"
    state_dir.mkdir()
    static_dir.mkdir()
    monkeypatch.setattr(gallery, "STATE_DIR", state_dir)
    monkeypatch.setattr(gallery, "STATIC_DIR", static_dir)
    monkeypatch.setattr(history_render, "STATE_DIR", state_dir)

    def write_state(name: str, data: dict) -> None:
        path = state_dir / name
        path.mkdir(parents=True, exist_ok=True)
        (path / "state.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def write_screen(name: str, day: str) -> Path:
        path = state_dir / name / "screens" / f"{day}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
        return path

    return type("Store", (), {"write_state": staticmethod(write_state),
                              "write_screen": staticmethod(write_screen),
                              "state_dir": state_dir, "static_dir": static_dir})


def test_grid_reads_map_shaped_state(fake_store):
    fake_store.write_state("tiangong", {"days": {
        "2026-10-23": {"index": 1, "名称": "葵口茶盏", "器形": "六瓣葵口", "款识": "绍兴壬午"},
    }})
    fake_store.write_screen("tiangong", "2026-10-23")

    rows = gallery.entries("CUSTOM_TIANGONG")
    assert len(rows) == 1
    assert rows[0]["date"] == "2026-10-23"
    assert rows[0]["title"] == "葵口茶盏"
    assert rows[0]["text"] == "六瓣葵口"
    assert rows[0]["meta"] == "绍兴壬午"
    assert rows[0]["has_screen"] is True


def test_grid_reads_list_shaped_state(fake_store):
    fake_store.write_state("songmeng", {"recent": [
        {"date": "2026-10-29", "scene": "蹴鞠场", "colophon": "粉墙下少年追着彩球跑"},
    ]})
    fake_store.write_screen("songmeng", "2026-10-29")

    rows = gallery.entries("CUSTOM_SONGMENG")
    assert len(rows) == 1
    assert rows[0]["title"] == "蹴鞠场"
    assert rows[0]["text"] == "粉墙下少年追着彩球跑"


def test_days_without_a_screen_do_not_take_a_slot(fake_store):
    """没留整屏图的那天不占位（没有空格子）。"""
    fake_store.write_state("tangping", {"recent": [
        {"date": "2026-10-28", "place": "石榴院", "line": "有图"},
        {"date": "2026-10-29", "place": "杏花院", "line": "没图"},
    ]})
    fake_store.write_screen("tangping", "2026-10-28")

    rows = gallery.entries("CUSTOM_TANGPING")
    assert [row["date"] for row in rows] == ["2026-10-28"]


def test_fields_are_all_carried_out(fake_store):
    """那一天的全部字段都带出来，缺的那项如实标「历史缺失」。"""
    fake_store.write_state("haicuotu", {"days": {
        "2026-10-29": {
            "index": 1, "名": "墨尾鲛姥", "异名": "云鳍娘", "尺寸": "长三尺",
            "产地": "雾藏沉渊湾", "特征": "身圆如瓜", "题识": "黑尾曳波轻旋",
        },
    }})
    fake_store.write_screen("haicuotu", "2026-10-29")

    rows = gallery.entries("CUSTOM_HAICUOTU")
    fields = {f["label"]: f for f in rows[0]["fields"]}
    assert set(fields) == {"名", "异名", "尺寸", "产地", "特征", "题识"}
    assert fields["名"]["value"] == "墨尾鲛姥"
    assert fields["名"]["missing"] is False


def test_missing_field_is_marked_not_invented(fake_store):
    fake_store.write_state("haicuotu", {"days": {
        "2026-10-29": {"index": 1, "名": "墨尾鲛姥"},
    }})
    fake_store.write_screen("haicuotu", "2026-10-29")

    rows = gallery.entries("CUSTOM_HAICUOTU")
    fields = {f["label"]: f for f in rows[0]["fields"]}
    assert fields["特征"]["missing"] is True
    assert fields["特征"]["value"] == ""
    # 有值的不能被标成缺失
    assert fields["名"]["missing"] is False


def test_ant_is_shown_as_a_screen_not_plain_text(fake_store):
    fake_store.write_state("ant", {"recent": [
        {"date": "2026-10-27", "title": "衔屑归巢", "lines": ["第一句", "第二句"]},
    ]})
    fake_store.write_screen("ant", "2026-10-27")

    rows = gallery.entries("CUSTOM_ANT")
    assert len(rows) == 1
    assert rows[0]["title"] == "衔屑归巢"
    assert rows[0]["has_screen"] is True


def test_screen_png_returns_the_saved_file(fake_store):
    fake_store.write_state("tiangong", {"days": {
        "2026-10-23": {"index": 1, "名称": "葵口茶盏"},
    }})
    fake_store.write_screen("tiangong", "2026-10-23")

    data = gallery.screen_png("CUSTOM_TIANGONG", "2026-10-23")
    assert data is not None and data.startswith(b"\x89PNG")
    assert gallery.screen_png("CUSTOM_TIANGONG", "2026-10-24") is None
    assert gallery.screen_png("CUSTOM_EWORLD", "2026-10-23") is None


def test_unknown_mode_returns_empty(fake_store):
    assert gallery.entries("CUSTOM_NOT_EXIST") == []
    assert gallery.entries("WEATHER") == []


# ── wide 形态：按日期现算 ────────────────────────────────────
@pytest.fixture
def no_real_cache(monkeypatch, tmp_path):
    """wide 形态会落盘：单测不许写进真实 runtime_cache。"""
    monkeypatch.setattr(history_render, "STATE_DIR", tmp_path / "runtime_cache")
    return tmp_path


@pytest.mark.asyncio
async def test_wide_eworld_computes_for_a_past_day(no_real_cache):
    entry = await gallery.render_wide("CUSTOM_EWORLD", "2026-10-28")
    assert entry is not None
    assert entry["date"] == "2026-10-28"
    assert entry["title"]
    assert entry["image"].startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_wide_rejects_bad_date(no_real_cache):
    assert await gallery.render_wide("CUSTOM_EWORLD", "not-a-date") is None
    assert await gallery.render_wide("CUSTOM_TIANGONG", "2026-10-28") is None


@pytest.mark.asyncio
async def test_wide_rejects_future_day(no_real_cache):
    """未来日期不存在：电子世界只演到今天。"""
    from datetime import date

    future = (date.today() + timedelta(days=1)).isoformat()
    assert await gallery.render_wide("CUSTOM_EWORLD", future) is None


@pytest.mark.asyncio
async def test_wide_rejects_day_before_epoch(no_real_cache):
    """起点之前的世界还没开始，不往前翻。"""
    epoch = gallery._eworld_epoch()
    before = (epoch - timedelta(days=1)).isoformat()
    assert await gallery.render_wide("CUSTOM_EWORLD", before) is None


@pytest.mark.asyncio
async def test_wide_same_day_is_deterministic(no_real_cache):
    day = gallery._eworld_epoch().isoformat()
    a = await gallery.render_wide("CUSTOM_EWORLD", day)
    b = await gallery.render_wide("CUSTOM_EWORLD", day)
    assert a["text"] == b["text"]
    assert a["image"] == b["image"]


# ── 永久保留：清理逻辑不得再删历史文件 ──────────────────────
@pytest.mark.parametrize("module_name", ["tiangong", "danqing", "haicuotu", "songmeng", "tangping"])
def test_image_cleanup_no_longer_deletes_history(module_name):
    mod = __import__(f"core.{module_name}", fromlist=["_cleanup_art"])
    fn = getattr(mod, "_cleanup_art", None)
    if fn is None:
        pytest.skip(f"{module_name} 没有 _cleanup_art")
    src = inspect.getsource(fn)
    assert "unlink" not in src, f"{module_name}._cleanup_art 仍在删图"


@pytest.mark.parametrize("module_name", ["rihen", "ant", "songmeng", "tangping"])
def test_text_history_records_are_not_truncated(module_name):
    """文字记录不得再按天数截断。"""
    mod = __import__(f"core.{module_name}", fromlist=["history"])
    src = inspect.getsource(mod)
    for bad in ("_history_limit", "history_limit", "keep_days=7", "[-7:", "[-30:", "[-14:"):
        assert bad not in src, f"{module_name} 仍带历史截断：{bad}"
