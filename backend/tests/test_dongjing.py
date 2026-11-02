"""《东京梦华录》模式（CUSTOM_DONGJING）测试。

覆盖：
  - 数据集完整性：每条引文 / 条目都是原文的逐字片段（原文在位时校验）；
  - 走读节奏：一天一站，按日期推进，走满一轮回到起点；
  - 节令匹配：今日节令与倒计时；
  - 路线条已删除：内容里不再有 route_url；
  - 引文补足：prose 站的引文从原文续到句子边界，且封顶；
  - 兜底：数据集缺失时返回 fallback，不产出空卡；
  - 模式 JSON：注册表校验通过 + provider 接线正常。
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from core import dongjing  # noqa: E402
from core.dongjing import (  # noqa: E402
    _festival_text,
    build_dongjing_content,
    load_dataset,
)

DATA_PATH = BACKEND_DIR / "core" / "data" / "dongjing.json"
MODE_SOURCE = BACKEND_DIR.parent / "modes-ours" / "dongjing.json"
MODE_DEPLOY = BACKEND_DIR / "core" / "modes" / "builtin" / "custom_dongjing.json"
SOURCE_TXT = BACKEND_DIR.parent.parent.parent / "library" / "books" / "东京梦华录.txt"

EPOCH = date(2026, 10, 27)


def _ctx(day: date) -> dict:
    return {"year": day.year, "month": day.month, "month_cn": f"{day.month}月", "day": day.day}


# ── 数据集 ──────────────────────────────────────────────────
def test_dataset_shape():
    data = load_dataset()
    stations = data["stations"]
    assert len(stations) == 83
    assert sum(1 for s in stations if s["axis"] == "space") == 41
    assert sum(1 for s in stations if s["axis"] == "season") == 42
    assert data["epoch"] == "2026-10-27"
    assert len(data["festivals"]) >= 20


def test_dataset_quotes_are_verbatim():
    """引文与名录条目必须是原文的逐字片段（拼接处的「……」除外）。"""
    if not SOURCE_TXT.exists():
        pytest.skip("原文不在本机，跳过逐字校验")
    raw = SOURCE_TXT.read_text(encoding="utf-8")
    data = load_dataset()
    for st in data["stations"]:
        where = f"{st['vol']} · {st['title']}"
        for part in [p for p in st["quote"].split("……") if p]:
            assert part in raw, f"{where}: 引文非原文逐字片段 {part!r}"
        for part in [p for p in st["lead"].split("……") if p]:
            assert part in raw, f"{where}: 引导语非原文逐字片段 {part!r}"
        for item in st["items"]:
            for side in [s for s in item.split(" · ") if s]:
                assert side in raw, f"{where}: 名录条目非原文逐字片段 {item!r}"


def test_dataset_builder_reproduces_same_json():
    """重新跑构建器，产物必须与入库的 JSON 一致（防止手工改坏生成物）。"""
    from core.data.build_dongjing_dataset import build

    data, errors = build(SOURCE_TXT)
    assert errors == [], errors
    stored = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    assert data["stations"] == stored["stations"]
    assert data["festivals"] == stored["festivals"]


# ── 走读节奏 ────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_walk_advances_one_station_per_day():
    data = load_dataset()
    stations = data["stations"]

    first = await build_dongjing_content({}, date_ctx=_ctx(EPOCH))
    assert first["title"] == stations[0]["title"]
    assert "第 1 / 83 站" in first["subtitle"]

    second = await build_dongjing_content({}, date_ctx=_ctx(EPOCH + timedelta(days=1)))
    assert second["title"] == stations[1]["title"]
    assert "第 2 / 83 站" in second["subtitle"]


@pytest.mark.asyncio
async def test_walk_wraps_after_full_lap():
    data = load_dataset()
    stations = data["stations"]
    last = await build_dongjing_content({}, date_ctx=_ctx(EPOCH + timedelta(days=82)))
    assert last["title"] == stations[82]["title"]
    assert "第 83 / 83 站" in last["subtitle"]

    wrapped = await build_dongjing_content({}, date_ctx=_ctx(EPOCH + timedelta(days=83)))
    assert wrapped["title"] == stations[0]["title"]
    assert "第 1 / 83 站" in wrapped["subtitle"]


@pytest.mark.asyncio
async def test_season_axis_stations_are_marked():
    data = load_dataset()
    season_index = next(i for i, s in enumerate(data["stations"]) if s["axis"] == "season")
    content = await build_dongjing_content({}, date_ctx=_ctx(EPOCH + timedelta(days=season_index)))
    assert "· 岁 ·" in content["subtitle"]
    assert content["festival_line"]


# ── 节令匹配 ────────────────────────────────────────────────
def test_festival_today():
    line, hint = _festival_text(date(2026, 11, 7))  # 立冬
    assert line == "今日 · 立冬"
    assert "西御园进冬菜" in hint


def test_festival_countdown():
    line, hint = _festival_text(date(2026, 10, 27))
    assert line.startswith("距 立冬 还有 ")
    assert "11 天" in line
    assert "西御园进冬菜" in hint


def test_festival_new_year():
    line, _ = _festival_text(date(2027, 2, 6))  # 农历正月初一
    assert line == "今日 · 正旦大朝会"


def test_festival_new_year_eve_uses_last_day_of_lunar_month():
    # 农历 2026 年腊月只有 29 天，除夕即 12 月 29 日 → 2027-02-05
    line, hint = _festival_text(date(2027, 2, 5))
    assert line == "今日 · 除夕"
    assert "禁中呈大傩仪" in hint


def test_festival_never_raises_for_any_day():
    """全年每一天都要能给出角标。"""
    day = date(2026, 1, 1)
    for _ in range(366):
        line, _hint = _festival_text(day)
        assert line
        day += timedelta(days=1)


# ── 路线条（已删）──────────────────────────────────────────
@pytest.mark.asyncio
async def test_content_carries_no_route_strip():
    content = await build_dongjing_content({}, date_ctx=_ctx(EPOCH))
    assert "route_url" not in content
    assert not any(k.startswith("_prefetched_route") for k in content)


# ── 引文补足 ────────────────────────────────────────────────
def test_prose_quotes_extended_and_capped():
    from core.data.build_dongjing_dataset import QUOTE_MAX_CHARS, QUOTE_TARGET_CHARS

    stations = load_dataset()["stations"]
    prose = [s for s in stations if s["kind"] == "prose" and s["quote"]]
    assert prose
    for s in prose:
        assert len(s["quote"]) <= QUOTE_MAX_CHARS
    long_enough = [s for s in prose if len(s["quote"]) >= QUOTE_TARGET_CHARS - 12]
    assert len(long_enough) >= len(prose) * 0.6


# ── 兜底 ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_fallback_when_dataset_missing(monkeypatch):
    monkeypatch.setattr(dongjing, "load_dataset", lambda: {})
    content = await build_dongjing_content(
        {"title": "汴京", "quote": "凡百所卖饮食之人"},
        date_ctx=_ctx(EPOCH),
    )
    assert content["title"] == "汴京"
    assert content["quote"] == "凡百所卖饮食之人"


# ── 模式定义 ────────────────────────────────────────────────
def test_mode_json_validates():
    from core.mode_registry import _validate_mode_def_with_error

    for path in (MODE_SOURCE, MODE_DEPLOY):
        assert path.exists(), f"缺少模式文件：{path}"
        definition = json.loads(path.read_text(encoding="utf-8"))
        ok, err = _validate_mode_def_with_error(definition)
        assert ok, f"{path}: {err}"
        assert definition["mode_id"] == "CUSTOM_DONGJING"
        assert definition["content"]["provider"] == "dongjing"
        assert definition["cacheable"] is False


@pytest.mark.asyncio
async def test_provider_is_wired():
    from core.json_content import _generate_computed_content

    mode_def = json.loads(MODE_DEPLOY.read_text(encoding="utf-8"))
    content = await _generate_computed_content(mode_def, mode_def["content"], {})
    assert content["title"]
    assert content["subtitle"]


def test_mode_source_and_deploy_match():
    assert MODE_SOURCE.read_text(encoding="utf-8") == MODE_DEPLOY.read_text(encoding="utf-8")
