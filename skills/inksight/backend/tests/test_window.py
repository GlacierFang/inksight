"""窗外（CUSTOM_WINDOW）：窗外是什么交给 AI，天气必须对上，窗必须存在。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

MODE_SOURCE = BACKEND_DIR.parent / "modes-ours" / "window.json"
MODE_DEPLOY = BACKEND_DIR / "core" / "modes" / "builtin" / "custom_window.json"


# ── 模式文件 ────────────────────────────────────────────────────────────

def test_mode_json_validates():
    from core.mode_registry import _validate_mode_def_with_error

    for path in (MODE_SOURCE, MODE_DEPLOY):
        assert path.exists(), f"缺少模式文件：{path}"
        definition = json.loads(path.read_text(encoding="utf-8"))
        ok, err = _validate_mode_def_with_error(definition)
        assert ok, f"{path}: {err}"
        assert definition["mode_id"] == "CUSTOM_WINDOW"
        assert definition["content"]["type"] == "computed"
        assert definition["content"]["provider"] == "window"


def test_mode_source_and_deploy_match():
    assert MODE_SOURCE.read_text(encoding="utf-8") == MODE_DEPLOY.read_text(encoding="utf-8")


def test_registered_in_catalog():
    from core.mode_catalog import BUILTIN_CATALOG

    item = next(i for i in BUILTIN_CATALOG if i.mode_id == "CUSTOM_WINDOW")
    assert item.category == "custom"
    assert item.zh.name == "窗外"


def test_provider_dispatch_registered():
    from core.json_content import _generate_computed_content  # noqa: F401

    source = (BACKEND_DIR / "core" / "json_content.py").read_text(encoding="utf-8")
    assert 'provider == "window"' in source


def test_gallery_collects_window():
    from core.gallery import modes

    ids = [m["mode_id"] for m in modes()]
    assert "CUSTOM_WINDOW" in ids


# ── 窗：必须在提示词里 ──────────────────────────────────────────────────

def test_window_spec_mentions_window():
    """「窗」不能丢 —— 提示词里必须写明是从屋里向外看的窗。"""
    from core.window import WINDOW_SPEC, WINDOW_SPEC_RETRY

    assert "窗" in WINDOW_SPEC
    assert "窗" in WINDOW_SPEC_RETRY


def test_prompt_has_no_composition_constraints():
    """窗外是什么交给 AI：不预设地点、不预设时段、不预设构图。"""
    from core.window import WINDOW_SPEC, WINDOW_SPEC_RETRY, PALETTE_SPEC

    for text in (WINDOW_SPEC, WINDOW_SPEC_RETRY):
        assert "地平线" not in text
        assert "日出" not in text
        assert "日落" not in text
        assert "夜" not in text
    # 只约束颜色，不约束画什么
    assert "四色" in PALETTE_SPEC or "颜色" in PALETTE_SPEC


def test_image_prompt_contains_scene():
    from core.window import _image_prompt

    prompt = _image_prompt("窗外是一场细雨打湿了屋檐")
    assert "窗外是一场细雨打湿了屋檐" in prompt
    assert "窗" in prompt


def test_scene_prompt_has_no_city():
    """城市名不许进 prompt —— 进了模型就照着那座城写，窗外就不再随机。"""
    from core.window import SCENE_PROMPT

    assert "{city}" not in SCENE_PROMPT
    assert "现在的天气" in SCENE_PROMPT


# ── 城市与天气 ──────────────────────────────────────────────────────────

def test_city_from_config():
    from core.window import _city_from_config

    assert _city_from_config({"city": "郑州"}) == "郑州"
    assert _city_from_config({}) == ""


def test_weather_fact_desc_and_temp():
    from core.window import _weather_fact

    desc, temp = _weather_fact(61, "15°C", "zh")
    assert "雨" in desc
    assert temp == "15°C"


def test_clean_scene_strips_prefix():
    from core.window import _clean_scene

    assert _clean_scene("景象：一片雪原") == "一片雪原"
    assert _clean_scene("远处有山") == "远处有山"


# ── 内容装配 ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_build_content_uses_city_and_weather(monkeypatch, tmp_path):
    from core import window

    monkeypatch.setattr(window, "ART_DIR", tmp_path)
    monkeypatch.setattr(window, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(window, "STATE_FILE", tmp_path / "state" / "state.json")

    async def _fake_scene(desc, temp, **kwargs):
        return "雨打在窗玻璃上"

    async def _fake_art(dest, scene, **kwargs):
        from PIL import Image

        dest.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (400, 270), (255, 255, 255)).save(dest)
        return True, "fake"

    monkeypatch.setattr(window, "_llm_scene", _fake_scene)
    monkeypatch.setattr(window, "_generate_art", _fake_art)

    content = await window.build_window_content(
        fallback={"art_url": "", "has_image": "no", "city_name": "", "weather_text": ""},
        config={"city": "郑州"},
        weather_code=61,
        weather_str="15°C",
        language="zh",
        date_ctx={"year": 2026, "month": 11, "day": 2},
    )

    assert content["has_image"] == "yes"
    assert content["city_name"] == "郑州"
    assert "雨" in content["weather_text"]
    assert content["art_url"].startswith("/static/window/")


@pytest.mark.asyncio
async def test_build_content_no_scene_falls_back(monkeypatch, tmp_path):
    """拿不到窗外景象时如实降级，不硬造一张图。"""
    from core import window

    monkeypatch.setattr(window, "ART_DIR", tmp_path)
    monkeypatch.setattr(window, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(window, "STATE_FILE", tmp_path / "state" / "state.json")

    async def _no_scene(desc, temp, **kwargs):
        return ""

    async def _boom(dest, scene, **kwargs):
        raise AssertionError("没景象就不该出图")

    monkeypatch.setattr(window, "_llm_scene", _no_scene)
    monkeypatch.setattr(window, "_generate_art", _boom)

    content = await window.build_window_content(
        fallback={"art_url": "", "has_image": "no"},
        config={"city": "郑州"},
        weather_code=61,
        weather_str="15°C",
        date_ctx={"year": 2026, "month": 11, "day": 2},
    )
    assert content["has_image"] == "no"
