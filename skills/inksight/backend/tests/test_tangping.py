"""躺平巴拉 prompt 层回归测试。

只测判重、限流重试与保底轮换，不碰生图接口。

2026-11-17 12:23 冰河大人定：删掉誊抄型测试（7 个）。
这里原来有一批「断言代码常量等于字面量」的测试（STYLE == "..."、"地点：" in DREAM_PROMPT），
它们只是把源码抄回测试里 —— 改代码忘了改测试就红、没改就绿，推翻不了任何结论。
画风和 prompt 的真凭据是出图效果，不是断言文本。
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
