"""躺平巴拉 prompt 层回归测试。

只测文本组装、判重、限流重试与保底轮换，不碰生图接口。

2026-11-17 改版记录：
- 11-14 那版把画风换成赛璐璐平涂、删掉「四色套色版画质感 / 不要写实材质纹理 / 棕色毛茸茸」，
  结果水豚画成了红黄卡通吉祥物。已回到旧版画风，本测试锁住这三样。
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest


def _mod():
    import sys

    backend = Path(__file__).resolve().parent.parent
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    from core import tangping

    return tangping


def test_dream_prompt_asks_for_place_line_and_scene():
    """地点 + 短句 + 画面提示词三样都要。

    画面那一项不能删：只给「地点+短句」两个短语，模型会画成图标。
    """
    tangping = _mod()
    assert "地点：" in tangping.DREAM_PROMPT
    assert "短句：" in tangping.DREAM_PROMPT
    assert "画面：" in tangping.DREAM_PROMPT
    assert tangping.DREAM_KEYS == ("地点", "短句", "画面")


def test_dream_prompt_keeps_subject_open():
    """题材放开：不再禁现代都市，也不强制「一点甜」。"""
    tangping = _mod()
    assert "不要现代都市" not in tangping.DREAM_PROMPT
    assert "一点点甜" not in tangping.DREAM_PROMPT


def test_palette_spec_keeps_old_art_direction():
    """旧版画风的三根命根子必须留着（2026-11-17 冰河大人定：画风回到旧版）。"""
    tangping = _mod()
    assert "四色套色版画质感" in tangping.PALETTE_SPEC
    assert "不要写实材质纹理" in tangping.PALETTE_SPEC
    assert "不要出现第五种色相" in tangping.PALETTE_SPEC
    assert "用明暗表现体积与柔和光影" in tangping.PALETTE_SPEC
    assert "水彩" not in tangping.PALETTE_SPEC


def test_subject_is_positive_plain_description():
    """主体全正向白描：不指定色相与毛发质感，也不写否定。

    2026-11-17 09:30 冰河大人定：白描效果更好，去掉「棕色/毛茸茸」。
    """
    tangping = _mod()
    assert "棕色" not in tangping.SUBJECT
    assert "毛茸茸" not in tangping.SUBJECT
    assert "不要" not in tangping.SUBJECT


def test_image_prompt_order_is_scene_subject_palette():
    """顺序照旧版：场景 → 主体 → 色盘。"""
    tangping = _mod()
    prompt = tangping._image_prompt("月下旧桥，薄雾贴着水面")
    assert prompt.startswith("月下旧桥")
    assert prompt.index("水豚") < prompt.index("金黄 #E8B000")
    assert prompt.index("金黄 #E8B000") < prompt.index("四色套色版画质感")


def test_image_prompt_retry_keeps_palette_and_adds_ink_weight():
    """重试版保留色阶与色盘，只加强墨线与轮廓。"""
    tangping = _mod()
    retry = tangping._image_prompt("云上", retry=True)
    assert "深金黄到淡金黄" in retry
    assert "墨黑 #000000 勾线要粗而清晰" in retry
    assert "不要大片留白" not in retry


def test_duplicate_detection():
    """撞地点或撞短句都要被抓出来。"""
    tangping = _mod()
    recent = [{"place": "石榴院", "line": "晚风把石榴甜香送进梦里"}]
    assert tangping._is_duplicate({"地点": "石榴院", "短句": "新的"}, recent) == "地点"
    assert (
        tangping._is_duplicate({"地点": "新地方", "短句": "晚风把石榴甜香送进梦里"}, recent)
        == "短句"
    )
    assert tangping._is_duplicate({"地点": "新地方", "短句": "新句子"}, recent) == ""


def test_generator_never_reuses_previous_day_image():
    """不复用昨天的图：复用会让昨天的地点、句子、图原样上屏。"""
    tangping = _mod()
    source = inspect.getsource(tangping._generate_once)
    assert "prev_art" not in source
    assert "_local_art_exists" not in source


def test_image_generation_retries_on_rate_limit():
    """429 是限流不是真出错，必须退避重试，不能直接交无图卡。"""
    tangping = _mod()
    assert tangping.IMAGE_RETRIES >= 3
    assert 429 in tangping.RETRY_STATUS
    assert tangping.RETRY_BACKOFF > 0
    source = inspect.getsource(tangping._generate_image)
    assert "RETRY_STATUS" in source
    assert "asyncio.sleep" in source


def test_fallback_pool_rotates():
    """保底文案轮换，别让保底卡自己天天撞车。"""
    tangping = _mod()
    assert len(tangping.FALLBACK_DREAMS) >= 3
    recent = [{"place": tangping.FALLBACK_DREAMS[0]["地点"], "line": "x"}]
    picked = tangping._pick_fallback(recent)
    assert picked["地点"] != tangping.FALLBACK_DREAMS[0]["地点"]


def test_display_size_matches_mode_json():
    """上屏尺寸必须与模式 JSON 的 image 块一致，否则渲染器二次缩放会平均掉细节。"""
    tangping = _mod()
    workspace = Path(__file__).resolve().parents[4]
    mode = json.loads(
        (workspace / "skills/inksight/modes-ours/tangping.json").read_text(encoding="utf-8")
    )
    body = mode["layout"]["body"]

    def _images(nodes):
        found = []
        for node in nodes:
            if node.get("type") == "image":
                found.append(node)
            for key in ("children", "conditions"):
                value = node.get(key)
                if isinstance(value, list):
                    found.extend(_images(value))
        return found

    blocks = _images(body)
    assert blocks, "模式 JSON 里没有 image 块"
    assert (blocks[0]["width"], blocks[0]["height"]) == tangping.CARD_DISPLAY
