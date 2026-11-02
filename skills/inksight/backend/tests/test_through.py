"""透屏模式测试。

透屏 = 窗外换个方向：同一块玻璃，往里看是电路板。
固定下来的是：透屏（从屏背面往里看）、正交俯视平摊、电路板、四色。
不固定的是：板上有什么元件、构图。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def test_through_spec_mentions_screen_and_orthographic_view():
    """「透屏」不能丢 —— 从屏背面往里看，且是垂直俯视的正投影平面图。"""
    from core.through import THROUGH_SPEC, THROUGH_SPEC_RETRY

    for text in (THROUGH_SPEC, THROUGH_SPEC_RETRY):
        assert "屏" in text
        assert "俯视" in text
        assert "垂直" in text or "正交" in text
        # 旧版要求的透视纵深已废弃
        assert "没入暗处" not in text
        assert "纵深" not in text


def test_prompt_fixes_view_but_not_components():
    """视角定死（垂直俯视的正投影），板上有什么交给 AI：不预设元件种类、不预设构图。"""
    from core.through import THROUGH_SPEC, THROUGH_SPEC_RETRY, PALETTE_SPEC

    for text in (THROUGH_SPEC, THROUGH_SPEC_RETRY):
        assert "俯视" in text
        assert "电阻" not in text
        assert "芯片" not in text
        assert "特写" not in text
    assert "四色" in PALETTE_SPEC or "颜色" in PALETTE_SPEC


def test_scene_prompt_has_no_real_world_data():
    """透屏里没有可读的系统数据 —— 城市天气一律不进 prompt。"""
    from core.through import SCENE_PROMPT

    assert "城市" not in SCENE_PROMPT
    assert "天气" not in SCENE_PROMPT


def test_parse_two_lines():
    from core.through import _parse_two

    scene, silk = _parse_two("近处一枚贴片电阻，焊点亮着\nR47 10kΩ")
    assert "贴片电阻" in scene
    assert silk == "R47 10kΩ"

    # 前缀要剥掉
    scene, silk = _parse_two("第一行：一排电容\n丝印：C12 50V")
    assert scene == "一排电容"
    assert silk == "C12 50V"

    # 少一行也不炸
    scene, silk = _parse_two("只有一行")
    assert scene == "只有一行"
    assert silk == ""


def test_image_prompt_contains_scene_but_not_silkscreen():
    """丝印不进生图提示词 —— 让模型画字会糊，版画里也不该有字。"""
    from core.through import _image_prompt

    prompt = _image_prompt("近处一枚贴片电阻，焊点亮着")
    assert "近处一枚贴片电阻" in prompt
    assert "屏" in prompt
    assert "10kΩ" not in prompt


@pytest.mark.asyncio
async def test_build_content_records_day(monkeypatch, tmp_path):
    from core import through

    monkeypatch.setattr(through, "ART_DIR", tmp_path)
    monkeypatch.setattr(through, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(through, "STATE_FILE", tmp_path / "state" / "state.json")

    async def _fake_scene(**kwargs):
        return "近处一枚贴片电阻", "R47 10kΩ"

    async def _fake_art(dest, scene, **kwargs):
        from PIL import Image

        dest.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (400, 270), (255, 255, 255)).save(dest)
        return True, "fake"

    monkeypatch.setattr(through, "_llm_scene", _fake_scene)
    monkeypatch.setattr(through, "_generate_art", _fake_art)

    content = await through.build_through_content(
        fallback={"art_url": "", "has_image": "no", "silkscreen": "", "scene": ""},
        date_ctx={"year": 2026, "month": 11, "day": 2},
    )

    assert content["has_image"] == "yes"
    assert content["silkscreen"] == "R47 10kΩ"
    assert content["art_url"].startswith("/static/through/")

    import json

    state = json.loads((tmp_path / "state" / "state.json").read_text(encoding="utf-8"))
    assert state["days"]["2026-11-02"]["silkscreen"] == "R47 10kΩ"


@pytest.mark.asyncio
async def test_build_content_no_scene_falls_back(monkeypatch, tmp_path):
    """拿不到景象时如实降级，不硬造一张图。"""
    from core import through

    monkeypatch.setattr(through, "ART_DIR", tmp_path)
    monkeypatch.setattr(through, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(through, "STATE_FILE", tmp_path / "state" / "state.json")

    async def _no_scene(**kwargs):
        return "", ""

    async def _boom(dest, scene, **kwargs):
        raise AssertionError("没景象就不该出图")

    monkeypatch.setattr(through, "_llm_scene", _no_scene)
    monkeypatch.setattr(through, "_generate_art", _boom)

    content = await through.build_through_content(
        fallback={"art_url": "", "has_image": "no"},
        date_ctx={"year": 2026, "month": 11, "day": 2},
    )
    assert content["has_image"] == "no"
