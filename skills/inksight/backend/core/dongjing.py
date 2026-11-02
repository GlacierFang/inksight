"""《东京梦华录》模式 provider：每天走一点点的城。

两轴走读：
  - 空间轴（卷一至卷五，41 站）：从东都外城一路走进市井 —— 城门、河道、御街、瓦子、
    行当、名录、物价、婚育；
  - 时间轴（卷六至卷十，42 节）：正月到除夕，一年的节令。

走读节奏：一天一站，按当天日期推进（无需任何持久化状态，也不依赖设备按键）。
角标：按当天真实日期匹配《东京梦华录》里写明的节令（农历月日 + 节气），标出
「今日 · 某节令」或「距 某节令 还有 N 天」。

内容全部来自本地纯文本《东京梦华录》，引文由原文逐字片段拼成，
不联网、不调 LLM、不补写原文没有的字句。
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, time as dt_time
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import SOLAR_TERMS

logger = logging.getLogger(__name__)

try:
    from zhdate import ZhDate
except Exception:  # pragma: no cover - 农历依赖缺失时只影响角标
    ZhDate = None  # type: ignore[assignment]

_DATA_PATH = Path(__file__).resolve().parent / "data" / "dongjing.json"

_SPACE_WORD = "城"
_SEASON_WORD = "岁"


# ── 数据集 ──────────────────────────────────────────────────
@lru_cache(maxsize=1)
def load_dataset() -> dict:
    """读取数据集；缺失或损坏时返回空字典（provider 回落到 fallback）。"""
    try:
        data = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning("[Dongjing] 数据集缺失：%s", _DATA_PATH)
        return {}
    except (OSError, ValueError) as exc:
        logger.warning("[Dongjing] 数据集读取失败：%s", exc)
        return {}
    if not isinstance(data, dict):
        return {}
    return data


# ── 日期 ────────────────────────────────────────────────────
def _resolve_today(date_ctx: dict | None) -> date:
    """优先用 date_ctx（和顶栏日期同源），取不到就用本机当天。"""
    now = datetime.now()
    ctx = date_ctx or {}
    try:
        year = int(ctx.get("year"))
        day = int(ctx.get("day"))
    except (TypeError, ValueError):
        return now.date()
    month_cn = str(ctx.get("month_cn") or "")
    month = now.month
    for idx, name in enumerate((
        "1月", "2月", "3月", "4月", "5月", "6月",
        "7月", "8月", "9月", "10月", "11月", "12月",
    ), start=1):
        if month_cn == name:
            month = idx
            break
    else:
        try:
            month = int(ctx.get("month"))
        except (TypeError, ValueError):
            month = now.month
    try:
        return date(year, month, day)
    except ValueError:
        return now.date()


def _lunar_to_gregorian(lunar_year: int, month: int, day: Any) -> date | None:
    """农历日期 → 公历日期；day="last" 取该月最后一天（除夕）。"""
    if ZhDate is None:
        return None
    if day == "last":
        for probe in (30, 29):
            try:
                z = ZhDate(lunar_year, month, probe)
            except Exception:
                continue
            if z.lunar_month == month and z.lunar_day == probe:
                return z.to_datetime().date()
        return None
    try:
        return ZhDate(lunar_year, month, int(day)).to_datetime().date()
    except Exception:
        return None


def _lunar_year_of(today: date) -> int:
    """今天属于哪个农历年（除夕当天算上一年的腊月，不能用 from_datetime）。"""
    if ZhDate is None:
        return today.year
    for ly in (today.year, today.year - 1):
        try:
            cny = ZhDate(ly, 1, 1).to_datetime().date()
            next_cny = ZhDate(ly + 1, 1, 1).to_datetime().date()
        except Exception:
            continue
        if cny <= today < next_cny:
            return ly
    return today.year


def _festival_candidates(today: date) -> list[tuple[date, dict]]:
    """列出节令表里各节令在今天前后最近的两次公历日期。"""
    out: list[tuple[date, dict]] = []
    festivals = load_dataset().get("festivals") or []
    if not festivals:
        return out

    lunar_year = _lunar_year_of(today)

    for fest in festivals:
        if fest.get("type") == "lunar":
            for ly in (lunar_year, lunar_year + 1):
                d = _lunar_to_gregorian(ly, int(fest.get("month", 1)), fest.get("day"))
                if d:
                    out.append((d, fest))
        else:
            term = fest.get("term")
            for year in (today.year, today.year + 1):
                hits = [
                    date(year, m, dd)
                    for (yy, m, dd), name in SOLAR_TERMS.items()
                    if yy == year and name == term
                ]
                if hits:
                    out.append((min(hits), fest))
                    break
    return out


def _festival_text(today: date) -> tuple[str, str]:
    """返回 (角标行, 提示语)。"""
    candidates = _festival_candidates(today)
    if not candidates:
        return "今日 · 岁序平常", ""

    today_hits = [f for d, f in candidates if d == today]
    if today_hits:
        fest = today_hits[0]
        return f"今日 · {fest.get('name', '')}", _join_hint(fest)

    future = sorted(
        ((d, f) for d, f in candidates if d > today),
        key=lambda item: item[0],
    )
    if future:
        d, fest = future[0]
        days = (d - today).days
        return f"距 {fest.get('name', '')} 还有 {days} 天", _join_hint(fest)

    return "今日 · 岁序平常", ""


def _join_hint(fest: dict) -> str:
    hints = [h for h in (fest.get("hint") or []) if h]
    return "……".join(hints)


# ── 内容 ────────────────────────────────────────────────────
def _station_content(stations: list[dict], index: int, today: date) -> dict:
    station = stations[index]
    total = len(stations)
    n = index + 1
    axis = station.get("axis", "space")
    word = _SPACE_WORD if axis == "space" else _SEASON_WORD
    kind = station.get("kind", "prose")
    items = list(station.get("items") or [])
    return {
        "title": station.get("title", ""),
        "subtitle": f"{station.get('vol', '')} · {word} · 第 {n} / {total} 站",
        "kind": "list" if (kind == "list" and items) else "prose",
        "quote": station.get("quote", ""),
        "lead": station.get("lead", ""),
        "items": items,
        "festival_line": "",
        "festival_hint": "",
    }


def _fallback_content(fallback: dict | None) -> dict:
    fb = fallback if isinstance(fallback, dict) else {}
    return {
        "title": fb.get("title", "汴京"),
        "subtitle": fb.get("subtitle", "《东京梦华录》"),
        "kind": fb.get("kind", "prose"),
        "quote": fb.get("quote", ""),
        "lead": fb.get("lead", ""),
        "items": list(fb.get("items") or []),
        "festival_line": fb.get("festival_line", "今日 · 岁序平常"),
        "festival_hint": fb.get("festival_hint", ""),
    }


async def build_dongjing_content(fallback: dict | None = None, **kwargs: Any) -> dict:
    """《东京梦华录》模式内容。纯本地计算，无外部依赖。"""
    fb = _fallback_content(fallback)
    data = load_dataset()
    stations = data.get("stations") or []
    if not stations:
        return fb

    today = _resolve_today(kwargs.get("date_ctx") or {})
    try:
        epoch = date.fromisoformat(str(data.get("epoch") or "2026-10-27"))
    except ValueError:
        epoch = date(2026, 10, 27)

    span = (today - epoch).days
    index = span % len(stations)

    content = _station_content(stations, index, today)
    line, hint = _festival_text(today)
    content["festival_line"] = line
    content["festival_hint"] = hint

    return content
