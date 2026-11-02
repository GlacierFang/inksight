"""二十四节气倒计时 provider（CUSTOM_SOLAR_TERMS）：当年 24 个节气的倒计时。

纯本地计算，不联网、不调 LLM —— 节气是确定数据，模型只会加废话。

数据源：core/config.py 的 SOLAR_TERMS（2024–2030，每年 24 个）。
超出表的年份（2031 年起）用寿星公式推算，误差 1 天左右；
公式与 core/year_progress.py 的清明推算同源。

排版口径（冰河大人 2026-11-02 定的「B 方案」）：
两列各 12 个，左列是上半年 1–12 个，右列是下半年 13–24 个，
每列自上而下按时间顺序。过去的显示「已过 N 天」，将来的显示「还有 N 天」，
当天正好是节气则显示「今日」。
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from .config import SOLAR_TERMS

logger = logging.getLogger(__name__)

# 寿星公式的 C 常数（21 世纪）：(节气名, 月份, C)
_TERM_C: tuple[tuple[str, int, float], ...] = (
    ("小寒", 1, 5.4055),
    ("大寒", 1, 20.12),
    ("立春", 2, 3.87),
    ("雨水", 2, 18.73),
    ("惊蛰", 3, 5.63),
    ("春分", 3, 20.646),
    ("清明", 4, 4.81),
    ("谷雨", 4, 20.1),
    ("立夏", 5, 5.52),
    ("小满", 5, 21.04),
    ("芒种", 6, 5.678),
    ("夏至", 6, 21.37),
    ("小暑", 7, 7.108),
    ("大暑", 7, 22.83),
    ("立秋", 8, 7.5),
    ("处暑", 8, 23.13),
    ("白露", 9, 7.646),
    ("秋分", 9, 23.042),
    ("寒露", 10, 8.318),
    ("霜降", 10, 23.438),
    ("立冬", 11, 7.438),
    ("小雪", 11, 22.36),
    ("大雪", 12, 7.18),
    ("冬至", 12, 21.94),
)


def _last_day_of_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (date(year, month + 1, 1) - date(year, month, 1)).days


def _formula_terms(year: int) -> list[tuple[date, str]]:
    """寿星公式推算全年 24 个节气。

    日 = floor(Y * 0.2422 + C) - floor((Y - 1) / 4)，Y = year % 100。
    公式只给到日，误差 ±1 天 —— 与 year_progress.py 的清明推算同源同精度。
    """
    y = year % 100
    out: list[tuple[date, str]] = []
    for name, month, c in _TERM_C:
        day = int(y * 0.2422 + c) - int((y - 1) / 4)
        day = max(1, min(day, _last_day_of_month(year, month)))
        out.append((date(year, month, day), name))
    return out


def terms_of_year(year: int) -> list[tuple[date, str]]:
    """某年的 24 个节气，按时间升序。表内用表，表外用公式。"""
    rows = sorted(
        (date(y, m, d), name)
        for (y, m, d), name in SOLAR_TERMS.items()
        if y == year
    )
    if len(rows) == 24:
        return rows
    return _formula_terms(year)


def _resolve_today(date_ctx: dict | None) -> date:
    ctx = date_ctx or {}
    try:
        return date(int(ctx["year"]), int(ctx["month"]), int(ctx["day"]))
    except (KeyError, TypeError, ValueError):
        return datetime.now().date()


def _days_text(delta: int, en: bool) -> str:
    if delta == 0:
        return "today" if en else "今日"
    if delta > 0:
        return f"in {delta} days" if en else f"还有{delta}天"
    return f"{-delta} days ago" if en else f"已过{-delta}天"


async def build_solar_terms_content(fallback: dict | None = None, **kwargs: Any) -> dict[str, Any]:
    """全年 24 个节气的倒计时内容。全量本地实时计算。"""
    fallback = fallback if isinstance(fallback, dict) else {}
    lang = str(kwargs.get("language") or "zh")
    en = lang == "en"

    today = _resolve_today(kwargs.get("date_ctx"))
    year = today.year
    terms = terms_of_year(year)

    past = [item for item in terms if item[0] <= today]
    future = [item for item in terms if item[0] > today]
    current_date, current_name = past[-1] if past else terms[0]
    if future:
        next_date, next_name = future[0]
    else:
        # 年末：下一个节气是次年第一个
        next_date, next_name = terms_of_year(year + 1)[0]

    names = [name for _, name in terms]
    index = names.index(current_name) + 1

    cells = []
    for day, name in terms:
        cells.append({
            "name": name,
            "md": f"{day.month}/{day.day}",
            "days": _days_text((day - today).days, en),
        })

    half = len(cells) // 2
    rows = []
    for i in range(half):
        left = cells[i]
        right = cells[i + half]
        rows.append({
            "l_name": left["name"],
            "l_md": left["md"],
            "l_days": left["days"],
            "r_name": right["name"],
            "r_md": right["md"],
            "r_days": right["days"],
        })

    return {
        **fallback,
        "title": "Solar Terms" if en else "二十四节气",
        "year": str(year),
        "current_name": current_name,
        "current_index": f"{index}/{len(terms)}",
        "next_name": next_name,
        "next_days": (next_date - today).days,
        "rows": rows,
    }
