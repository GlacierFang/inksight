"""狸行记（CUSTOM_MIAOLI）：喵狸的日漫一日。

极简：一次 LLM 写画面，一次生图出一张日漫图。屏上一句话都没有。
固定下来的是：角色设定、黑白日漫风格、版式（整版图占满，页脚只有署名）。
不固定的是：今天画什么、签名。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

MODE_SOURCE = BACKEND_DIR.parent / "modes-ours" / "miaoli.json"
MODE_DEPLOY = BACKEND_DIR / "core" / "modes" / "builtin" / "custom_miaoli.json"


# ── 模式文件 ────────────────────────────────────────────────────────────


def test_mode_json_validates():
    from core.mode_registry import _validate_mode_def_with_error

    for path in (MODE_SOURCE, MODE_DEPLOY):
        assert path.exists(), f"缺少模式文件：{path}"
        definition = json.loads(path.read_text(encoding="utf-8"))
        ok, err = _validate_mode_def_with_error(definition)
        assert ok, f"{path}: {err}"
        assert definition["mode_id"] == "CUSTOM_MIAOLI"
        assert definition["content"]["type"] == "computed"
        assert definition["content"]["provider"] == "miaoli"


def test_mode_source_and_deploy_match():
    assert MODE_SOURCE.read_text(encoding="utf-8") == MODE_DEPLOY.read_text(encoding="utf-8")


def test_registered_in_catalog():
    from core.mode_catalog import BUILTIN_CATALOG

    item = next(i for i in BUILTIN_CATALOG if i.mode_id == "CUSTOM_MIAOLI")
    assert item.category == "custom"
    assert item.zh.name == "狸行记"


def test_gallery_collects_miaoli():
    from core.gallery import modes

    ids = [m["mode_id"] for m in modes()]
    assert "CUSTOM_MIAOLI" in ids


def test_provider_dispatch_registered():
    from core.json_content import _generate_computed_content  # noqa: F401


# ── 版式：屏上只有图和页脚 ────────────────────────────────────────────


def test_screen_has_no_body_text():
    """出图的那条分支屏上一句话都没有 —— 标题和描述只进 prompt 和回顾页字段。

    没图时允许一句兜底提示（透屏的「未见电路」同理）。
    """
    definition = json.loads(MODE_SOURCE.read_text(encoding="utf-8"))
    body = definition["layout"]["body"]

    def _walk(nodes):
        kinds = set()
        for node in nodes:
            kinds.add(node.get("type"))
            for key in ("children", "body"):
                if isinstance(node.get(key), list):
                    kinds |= _walk(node[key])
            if node.get("type") == "conditional":
                for cond in node.get("conditions") or []:
                    kinds |= _walk(cond.get("children") or [])
        return kinds

    yes_branch = None
    for node in body:
        if node.get("type") != "conditional":
            continue
        for cond in node.get("conditions") or []:
            if cond.get("op") == "eq" and cond.get("value") == "yes":
                yes_branch = cond.get("children") or []

    assert yes_branch is not None, "找不到 has_image=yes 分支"
    kinds = _walk(yes_branch)
    assert "text" not in kinds, f"出图时屏上不该有文字块：{kinds}"
    assert "speech_bubble" not in kinds, f"出图时屏上不该有气泡：{kinds}"
    assert "image" in kinds


def test_footer_shows_name_and_signature():
    definition = json.loads(MODE_SOURCE.read_text(encoding="utf-8"))
    footer = definition["layout"]["footer"]
    assert footer["label"] == "狸行记"
    assert "{name}" in footer["attribution_template"]
    assert "{signature}" in footer["attribution_template"]


def test_status_bar_hidden():
    definition = json.loads(MODE_SOURCE.read_text(encoding="utf-8"))
    assert definition["layout"]["status_bar"].get("hidden") is True


# ── 解析 ──────────────────────────────────────────────────────────────


def test_parse_day():
    from core.miaoli import _parse_day

    raw = (
        "标题：檐上\n"
        "画面：喵狸蹲在旧粮店的檐砖上，风掀乱它的胡子\n"
        "签名：此路有我，概不收租。\n"
    )
    card = _parse_day(raw)
    assert card["title"] == "檐上"
    assert "旧粮店" in card["scene"]
    assert card["signature"] == "此路有我，概不收租。"


def test_parse_day_reads_two_panels():
    """新的分镜格式：第一格起势，第二格爆发（冰河大人 2026-11-02 定的）。"""
    from core.miaoli import _parse_day

    raw = (
        "标题：扑蝶\n"
        "第一格：喵狸压低身子，耳朵先动，眼睛盯住白蝴蝶\n"
        "第二格：喵狸后腿一蹬扑出去，胡须被风拉直\n"
        "签名：来此一爪。\n"
    )
    card = _parse_day(raw)
    assert card["title"] == "扑蝶"
    assert card["panel1"].startswith("喵狸压低身子")
    assert card["panel2"].startswith("喵狸后腿一蹬")
    assert "→" in card["scene"]


def test_parse_day_requires_scene():
    from core.miaoli import _parse_day

    assert _parse_day("标题：只有标题") == {}


def test_signature_does_not_repeat_cat_name():
    """页脚是「喵狸·签名」——签名里再写一遍就成了「喵狸·喵狸来此一爪」。"""
    from core.miaoli import _parse_day, _strip_name

    assert _strip_name("喵狸来此一爪") == "来此一爪"
    assert _strip_name("此路有我，概不收租。") == "此路有我，概不收租。"

    card = _parse_day("画面：喵狸在破碗边捞鱼" + chr(10) + "签名：喵狸来此一爪")
    assert card["signature"] == "来此一爪"


def test_parse_day_defaults_signature():
    from core.miaoli import DEFAULT_SIGNATURE, _parse_day

    card = _parse_day("画面：喵狸在墙根下蹲着")
    assert card["signature"] == DEFAULT_SIGNATURE


# ── 提示词 ────────────────────────────────────────────────────────────


def test_image_prompt_fixes_cat_and_manga_style():
    """角色设定和风格每次复用同一段 —— 2 色屏上的一致性锚点。"""
    from core.miaoli import CAT_SPEC, MANGA_SPEC, _image_prompt

    prompt = _image_prompt("喵狸蹲在旧粮店的檐砖上")
    assert "狸花" in prompt
    assert "M 字纹" in prompt
    assert "漫画" in prompt
    assert "不要颜色" in prompt
    assert CAT_SPEC in prompt
    assert MANGA_SPEC in prompt


def test_image_prompt_has_equal_panels_and_motion():
    """左右等宽双格 + 动感 —— 冰河大人 2026-11-02 定的两件事。"""
    from core.miaoli import DYNAMIC_SPEC, PANEL_SPEC, _image_prompt

    prompt = _image_prompt(
        "喵狸压低身子 → 喵狸扑出去",
        panel1="喵狸压低身子，耳朵先动",
        panel2="喵狸扑出去，胡须被风拉直",
    )
    assert PANEL_SPEC in prompt
    assert DYNAMIC_SPEC in prompt
    assert "等宽" in prompt
    assert "速度线" in prompt
    # 两格分镜各占一行，模型才分得清哪句是哪格
    assert "喵狸压低身子，耳朵先动" in prompt
    assert "喵狸扑出去，胡须被风拉直" in prompt


def test_image_prompt_forbids_portrait_style():
    """之前跑偏成素描插画 —— 排除项要钉死。"""
    from core.miaoli import NEGATIVE_SPEC, _image_prompt

    prompt = _image_prompt("某个场景")
    assert NEGATIVE_SPEC in prompt
    assert "动物肖像插画" in prompt
    assert "静态坐姿摆拍" in prompt


def test_day_prompt_asks_for_two_beats():
    """LLM 要写两拍：起势 + 爆发，否则画出来还是摆拍。"""
    from core.miaoli import DAY_PROMPT

    assert "第一格" in DAY_PROMPT
    assert "第二格" in DAY_PROMPT
    assert "同一个动作的接续" in DAY_PROMPT
    assert "画面要有速度" in DAY_PROMPT


def test_image_prompt_forbids_text_and_bubbles():
    """屏上的字由程序排；模型画的字在 2 色屏上会糊成一团。"""
    from core.miaoli import _image_prompt

    prompt = _image_prompt("某个场景", )
    assert "不要有任何文字" in prompt
    assert "气泡框" in prompt
    assert "对话框" in prompt


def test_day_prompt_has_no_real_system_data():
    """喵狸不读天气、日程等真实数据 —— 它是虚拟生命体，不是仪表盘。"""
    from core.miaoli import DAY_PROMPT

    assert "天气" not in DAY_PROMPT
    assert "电量" not in DAY_PROMPT
    assert "日程" not in DAY_PROMPT


def test_cat_name_is_fixed():
    """名字是喵狸，固定的 —— 不让模型自封。"""
    from core.miaoli import CAT_NAME

    assert CAT_NAME == "喵狸"


# ── 内容装配 ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_build_content_history_day_is_not_invented(monkeypatch, tmp_path):
    """历史日没生成过就是没生成过 —— 如实标「历史缺失」，不补生成。"""
    from core import miaoli

    monkeypatch.setattr(miaoli, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(miaoli, "STATE_FILE", tmp_path / "state" / "state.json")

    async def _boom(*args, **kwargs):
        raise AssertionError("历史日不该触发生成")

    monkeypatch.setattr(miaoli, "generate_today_art", _boom)

    content = await miaoli.build_miaoli_content(
        fallback={},
        date_ctx={"year": 2026, "month": 11, "day": 1},
    )
    assert content["has_image"] == "no"
    assert content["title"] == "历史缺失"


@pytest.mark.asyncio
async def test_generate_today_records_day(monkeypatch, tmp_path):
    from core import miaoli

    monkeypatch.setattr(miaoli, "ART_DIR", tmp_path / "art")
    monkeypatch.setattr(miaoli, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(miaoli, "STATE_FILE", tmp_path / "state" / "state.json")
    monkeypatch.setattr(miaoli, "LOCK_FILE", tmp_path / "state" / "generate.lock")

    async def _fake_day(**kwargs):
        return {
            "title": "檐上",
            "scene": "喵狸蹲在旧粮店的檐砖上，风掀乱它的胡子",
            "signature": "此路有我，概不收租。",
        }

    async def _fake_art(dest, scene, **kwargs):
        from PIL import Image

        dest.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", miaoli.ART_DISPLAY, (255, 255, 255)).save(dest)
        return True, "fake"

    monkeypatch.setattr(miaoli, "_llm_day", _fake_day)
    monkeypatch.setattr(miaoli, "_generate_art", _fake_art)

    record = await miaoli.generate_today_art("2026-11-02")
    assert record is not None
    assert record["title"] == "檐上"
    assert record["art_url"].startswith("/static/miaoli/")
    assert record["has_image"] == "yes"

    state = json.loads((tmp_path / "state" / "state.json").read_text(encoding="utf-8"))
    assert state["days"]["2026-11-02"]["title"] == "檐上"


@pytest.mark.asyncio
async def test_build_content_today_uses_record(monkeypatch, tmp_path):
    from core import miaoli

    monkeypatch.setattr(miaoli, "ART_DIR", tmp_path / "art")
    monkeypatch.setattr(miaoli, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(miaoli, "STATE_FILE", tmp_path / "state" / "state.json")

    async def _fake_art(date_key, **kwargs):
        return {
            "date": date_key,
            "title": "檐上",
            "scene": "喵狸蹲在旧粮店的檐砖上",
            "signature": "此路有我，概不收租。",
            "art_url": "/static/miaoli/miaoli_20261102.png",
            "has_image": "yes",
        }

    monkeypatch.setattr(miaoli, "generate_today_art", _fake_art)

    content = await miaoli.build_miaoli_content(
        fallback={},
        date_ctx={"year": 2026, "month": 11, "day": 2},
    )
    assert content["title"] == "檐上"
    assert "旧粮店" in content["scene"]
    assert content["signature"] == "此路有我，概不收租。"
    assert content["name"] == "喵狸"
    assert content["has_image"] == "yes"
