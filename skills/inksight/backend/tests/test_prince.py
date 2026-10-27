"""小王子模式 provider 的纯逻辑测试（不联网、不碰 DB）。"""
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from core.prince import (  # noqa: E402
    EMOTION_CN,
    _FALLBACK_ZH,
    _clean_text,
    _pick_fallback,
    _too_similar,
    find_character_file,
    list_character_files,
    pick_emotion,
)


# ── 情绪选择 ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        (dict(hour=2, weather_code=0), "sleepy"),
        (dict(hour=14, weather_code=0), "sleepy"),
        (dict(hour=10, weather_code=0), "happy"),
        (dict(hour=10, weather_code=3), "calm"),
        (dict(hour=10, weather_code=61), "sad"),
        (dict(hour=10, weather_code=95), "grumpy"),
        (dict(hour=15, weather_code=0), "curious"),
        (dict(hour=10, weather_code=0, festival="国庆节"), "excited"),
        (dict(hour=10, weather_code=0, solar_term="霜降"), "excited"),
        (dict(hour=10, weather_code=0, days_until_holiday=1), "excited"),
    ],
)
def test_pick_emotion(kwargs, expected):
    assert pick_emotion(**kwargs) == expected


def test_pick_emotion_priority_severe_weather_beats_clear_sky():
    """恶劣天气 + 晴天标签时，仍按恶劣天气算。"""
    assert pick_emotion(hour=10, weather_code=95) == "grumpy"


# ── 角色图 ────────────────────────────────────────────────────


def test_character_files_found():
    files = list_character_files("U5")
    assert files, "runtime_characters/U5 下应当有角色图"
    assert all(p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".bmp") for p in files)


def test_find_character_file_by_emotion():
    for emotion in EMOTION_CN:
        found = find_character_file("U5", emotion)
        assert found is not None, f"{emotion} 应能选到图"
        assert found.exists()


def test_find_character_file_unknown_emotion_falls_back():
    """情绪认不出的文件名时，退回目录里第一张，不能空。"""
    found = find_character_file("U5", "not_an_emotion")
    assert found is not None


def test_find_character_file_missing_account():
    assert find_character_file("U_DOES_NOT_EXIST", "happy") is None


# ── 文案处理 ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("小王子：今天天气不错。", "今天天气不错。"),
        ("「今天天气不错。」", "今天天气不错。"),
        ("今天天气不错。\n这是解释", "今天天气不错。"),
        ("  今天天气不错。  ", "今天天气不错。"),
        ("", ""),
    ],
)
def test_clean_text(raw, expected):
    assert _clean_text(raw) == expected


def test_too_similar_detects_repeats():
    assert _too_similar("今天的云跑得比我还快。", "今天的云跑得比我还快")
    assert _too_similar("我想去追一朵云", "我想去追一朵云呀")
    assert not _too_similar("今天的云跑得比我还快。", "风把树叶吹成了小铃铛。")


def test_pick_fallback_is_deterministic_per_day():
    a = _pick_fallback("happy", "2026-10-26:AA")
    b = _pick_fallback("happy", "2026-10-26:AA")
    c = _pick_fallback("happy", "2026-10-27:AA")
    assert a == b
    assert a in _FALLBACK_ZH["happy"]


def test_pick_fallback_covers_all_emotions():
    for emotion in EMOTION_CN:
        line = _pick_fallback(emotion, "seed")
        assert line and line.strip()
