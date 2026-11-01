"""小景（CUSTOM_XIAOJING）：风格由模型自由发挥，时刻由程序定。"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

MODE_SOURCE = BACKEND_DIR.parent / "modes-ours" / "xiaojing.json"
MODE_DEPLOY = BACKEND_DIR / "core" / "modes" / "builtin" / "custom_xiaojing.json"


# ── 模式文件 ────────────────────────────────────────────────────────────


def test_mode_json_validates():
    from core.mode_registry import _validate_mode_def_with_error

    for path in (MODE_SOURCE, MODE_DEPLOY):
        assert path.exists(), f"缺少模式文件：{path}"
        definition = json.loads(path.read_text(encoding="utf-8"))
        ok, err = _validate_mode_def_with_error(definition)
        assert ok, f"{path}: {err}"
        assert definition["mode_id"] == "CUSTOM_XIAOJING"
        assert definition["content"]["type"] == "computed"
        assert definition["content"]["provider"] == "xiaojing"


def test_mode_source_and_deploy_match():
    assert MODE_SOURCE.read_text(encoding="utf-8") == MODE_DEPLOY.read_text(encoding="utf-8")


def test_registered_in_catalog():
    from core.mode_catalog import BUILTIN_CATALOG

    item = next(i for i in BUILTIN_CATALOG if i.mode_id == "CUSTOM_XIAOJING")
    assert item.category == "custom"
    assert item.zh.name == "小景"


def test_gallery_collects_xiaojing():
    from core.gallery import modes

    ids = [m["mode_id"] for m in modes()]
    assert "CUSTOM_XIAOJING" in ids


def test_provider_dispatch_registered():
    from core.json_content import _generate_computed_content  # noqa: F401

    source = (BACKEND_DIR / "core" / "json_content.py").read_text(encoding="utf-8")
    assert 'provider == "xiaojing"' in source


# ── 时刻由程序定 ────────────────────────────────────────────────────────


def test_phase_by_sun():
    from core.xiaojing import _phase_from_sun

    sr = datetime(2026, 11, 1, 6, 30)
    ss = datetime(2026, 11, 1, 17, 50)

    def at(h, m):
        return _phase_from_sun(datetime(2026, 11, 1, h, m), sr, ss)

    assert at(3, 0) == "night"
    assert at(6, 0) == "dawn"
    assert at(7, 30) == "dawn"
    assert at(9, 0) == "day"
    assert at(15, 0) == "day"
    assert at(16, 30) == "dusk"
    assert at(17, 40) == "dusk"
    assert at(19, 30) == "night"


def test_phase_clock_fallback():
    from core.xiaojing import _phase_from_clock

    assert _phase_from_clock(datetime(2026, 11, 1, 6, 30)) == "dawn"
    assert _phase_from_clock(datetime(2026, 11, 1, 12, 0)) == "day"
    assert _phase_from_clock(datetime(2026, 11, 1, 17, 30)) == "dusk"
    assert _phase_from_clock(datetime(2026, 11, 1, 23, 0)) == "night"


def test_scene_hint_varies_by_day():
    from core.xiaojing import _scene_hint

    hints = {_scene_hint(f"2026-11-{d:02d}") for d in range(1, 16)}
    assert len(hints) > 1


# ── 调色 ────────────────────────────────────────────────────────────────


def test_grade_night_darkens():
    from PIL import Image

    from core.xiaojing import grade_for_phase

    src = Image.new("RGB", (16, 16), (120, 130, 150))
    night = grade_for_phase(src, "night")
    assert max(night.getpixel((0, 0))) < 60


def test_grade_dusk_keeps_red():
    from PIL import Image

    from core.xiaojing import grade_for_phase

    src = Image.new("RGB", (16, 16), (200, 180, 120))
    dusk = grade_for_phase(src, "dusk")
    r, g, b = dusk.getpixel((0, 0))
    assert r > g and g > b


def test_grade_day_brightens():
    from PIL import Image

    from core.xiaojing import grade_for_phase

    src = Image.new("RGB", (16, 16), (100, 100, 100))
    day = grade_for_phase(src, "day")
    dr, dg, db = day.getpixel((0, 0))
    sr, sg, sb = src.getpixel((0, 0))
    # 昼：红绿抬起来（走金黄墨），蓝压掉（屏上根本没有蓝墨）
    assert dr > sr and dg > sg
    assert db < sb


# ── 出图 ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_scene_reuses_existing(monkeypatch, tmp_path):
    from core import xiaojing

    monkeypatch.setattr(xiaojing, "ART_DIR", tmp_path)
    monkeypatch.setattr(xiaojing, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(xiaojing, "STATE_FILE", tmp_path / "state" / "state.json")
    monkeypatch.setattr(xiaojing, "LOCK_FILE", tmp_path / "state" / "generate.lock")

    async def _boom(*a, **k):
        raise AssertionError("不该调生图")

    monkeypatch.setattr(xiaojing, "_generate_art", _boom)

    art = tmp_path / "scene_20261101_dusk.png"
    from PIL import Image

    Image.new("RGB", (400, 264), (255, 255, 255)).save(art)

    record = await xiaojing.generate_scene("dusk", "2026-11-01")
    assert record["has_image"] == "yes"
    assert record["art_url"].endswith("scene_20261101_dusk.png")


@pytest.mark.asyncio
async def test_generate_scene_new_phase_generates(monkeypatch, tmp_path):
    from core import xiaojing

    monkeypatch.setattr(xiaojing, "ART_DIR", tmp_path)
    monkeypatch.setattr(xiaojing, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(xiaojing, "STATE_FILE", tmp_path / "state" / "state.json")
    monkeypatch.setattr(xiaojing, "LOCK_FILE", tmp_path / "state" / "generate.lock")

    calls = []

    async def _fake(phase, dest, hint, **kwargs):
        calls.append((phase, hint))
        from PIL import Image

        dest.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (400, 264), (255, 255, 255)).save(dest)
        return True, "fake"

    monkeypatch.setattr(xiaojing, "_generate_art", _fake)

    record = await xiaojing.generate_scene("dusk", "2026-11-01")
    assert record["has_image"] == "yes"
    assert calls and calls[0][0] == "dusk"


@pytest.mark.asyncio
async def test_build_content_history_does_not_generate(monkeypatch, tmp_path):
    from core import xiaojing

    monkeypatch.setattr(xiaojing, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(xiaojing, "STATE_FILE", tmp_path / "state" / "state.json")

    async def _boom(*a, **k):
        raise AssertionError("历史日不该出图")

    monkeypatch.setattr(xiaojing, "generate_scene", _boom)

    content = await xiaojing.build_xiaojing_content(
        fallback={"art_url": "", "has_image": "no", "phase_label": ""},
        date_ctx={"year": 2026, "month": 10, "day": 1},
    )
    assert content["has_image"] == "no"
