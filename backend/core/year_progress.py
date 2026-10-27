"""年度进度 provider：年/季/月/周/今天五级实时进度 + 全年周格热力图 + 节日倒计时。

纯本地计算，不联网、不调 LLM。所有数值都按设备刷新当天的真实日期与时间实时算出，
不绑定任何固定基准日（换一天刷新，百分比、网格、倒计时全部跟着走）。

节日口径：只认 7 个固定节日当天 —— 元旦、春节、清明节、劳动节、端午节、中秋节、国庆节。
不算调休上班日、不把周末当假日、不按放假区间铺色。

网格口径：7 行（周一→周日）× N 列（每列一周），整年从第一周周一到末周周日铺满。
色块含义：已过=黑实心 / 今天=朱红实心 / 假日=金黄实心 / 未来=白底描边。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from .config import MONTH_CN, SOLAR_TERMS

logger = logging.getLogger(__name__)

try:  # 农历转换，缺失时退化到公历固定节日
    from zhdate import ZhDate
except Exception:  # pragma: no cover - 依赖缺失不影响公历部分
    ZhDate = None  # type: ignore[assignment]

# ── 网格状态 ────────────────────────────────────────────────
STATE_PAST = "past"
STATE_TODAY = "today"
STATE_HOLIDAY = "holiday"
STATE_FUTURE = "future"
STATE_NONE = ""          # 不属于本年（首列补头、末列补尾）

# 7 个固定节日：(显示名, 类型, 月, 日)
#   solar      —— 公历固定日期
#   lunar      —— 农历反推公历
#   solar_term —— 节气（清明）
_HOLIDAY_SPECS: tuple[tuple[str, str, int, int], ...] = (
    ("元旦", "solar", 1, 1),
    ("春节", "lunar", 1, 1),
    ("清明节", "solar_term", 4, 0),
    ("劳动节", "solar", 5, 1),
    ("端午节", "lunar", 5, 5),
    ("中秋节", "lunar", 8, 15),
    ("国庆节", "solar", 10, 1),
)

_ZH_MONTHS = ("1月", "2月", "3月", "4月", "5月", "6月",
              "7月", "8月", "9月", "10月", "11月", "12月")
_EN_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

_ZH_WEEKDAY_ROWS = ("一", "二", "三", "四", "五", "六", "日")
_EN_WEEKDAY_ROWS = ("M", "T", "W", "T", "F", "S", "S")


def _is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (date(year, month + 1, 1) - date(year, month, 1)).days


def _qingming_date(year: int) -> date:
    """清明：优先用节气表，缺表时用 21 世纪通用寿星公式（C=4.81）。"""
    for (y, m, d), name in SOLAR_TERMS.items():
        if y == year and name == "清明":
            return date(y, m, d)
    yy = year % 100
    day = int(yy * 0.2422 + 4.81) - int(yy / 4)
    return date(year, 4, max(1, min(30, day)))


def _lunar_to_solar(year: int, month: int, day: int) -> date | None:
    if ZhDate is None:
        return None
    try:
        return ZhDate(year, month, day).to_datetime().date()
    except Exception as exc:  # pragma: no cover - 农历边界异常
        logger.warning("[YearProgress] lunar %d-%d-%d failed: %s", year, month, day, exc)
        return None


def holidays_of_year(year: int) -> list[tuple[date, str]]:
    """返回该公历年内 7 个固定节日（按日期升序）。"""
    out: list[tuple[date, str]] = []
    for name, kind, month, day in _HOLIDAY_SPECS:
        if kind == "solar":
            out.append((date(year, month, day), name))
        elif kind == "solar_term":
            out.append((_qingming_date(year), name))
        else:
            solar = _lunar_to_solar(year, month, day)
            if solar is not None:
                out.append((solar, name))
    out.sort(key=lambda item: item[0])
    return out


def upcoming_holidays(today: date, count: int = 4) -> list[tuple[date, str]]:
    """从今天往后取接下来 count 个固定节日（跨年自动接续到下一年）。"""
    pool: list[tuple[date, str]] = []
    for year in (today.year, today.year + 1):
        pool.extend(holidays_of_year(year))
    future = [(d, n) for d, n in pool if d > today]
    future.sort(key=lambda item: item[0])
    return future[:count]


def _build_grid(today: date) -> dict[str, Any]:
    """全年周格：7 行（周一→周日）× N 列（每列一周）。"""
    year = today.year
    jan1 = date(year, 1, 1)
    dec31 = date(year, 12, 31)
    start = jan1 - timedelta(days=jan1.weekday())        # 第一周周一
    end = dec31 + timedelta(days=6 - dec31.weekday())     # 末周周日
    weeks = ((end - start).days // 7) + 1
    holiday_map = {d: n for d, n in holidays_of_year(year)}

    # cells 为行主序：cells[行 * 列数 + 列]，行 0 = 周一，行 6 = 周日
    cells: list[str] = []
    for r in range(7):
        for w in range(weeks):
            d = start + timedelta(days=w * 7 + r)
            if d.year != year:
                cells.append(STATE_NONE)
            elif d == today:
                cells.append(STATE_TODAY)
            elif d in holiday_map:
                cells.append(STATE_HOLIDAY)
            elif d < today:
                cells.append(STATE_PAST)
            else:
                cells.append(STATE_FUTURE)

    months = []
    for m in range(1, 13):
        first = date(year, m, 1)
        months.append({"col": (first - start).days // 7, "month": m})

    return {
        "cols": weeks,
        "rows": 7,
        "cells": cells,
        "months": months,
        "weekday_rows": [0, 2, 4, 6],
    }


def _level(label: str, value: int, total: int, remain_text: str) -> dict[str, Any]:
    total = max(1, total)
    pct = value / total * 100.0
    return {
        "label": label,
        "value": int(value),
        "max": int(total),
        "pct": round(pct, 1),
        "pct_text": f"{pct:.0f}%",
        "remain_text": remain_text,
    }


def _build_levels(today: date, now: datetime, lang: str) -> list[dict[str, Any]]:
    en = lang == "en"
    year = today.year
    days_in_year = 366 if _is_leap(year) else 365
    day_of_year = today.timetuple().tm_yday

    q = (today.month - 1) // 3 + 1
    q_start_month = (q - 1) * 3 + 1
    q_start = date(year, q_start_month, 1)
    q_end_month = q_start_month + 3
    if q_end_month > 12:
        q_end = date(year, 12, 31)
    else:
        q_end = date(year, q_end_month, 1) - timedelta(days=1)
    q_total = (q_end - q_start).days + 1
    q_passed = (today - q_start).days + 1

    m_total = _days_in_month(year, today.month)
    m_passed = today.day

    w_total = 7
    w_passed = today.weekday() + 1

    seconds_now = now.hour * 3600 + now.minute * 60 + now.second
    d_total = 86400
    hours_left = max(0, (d_total - seconds_now + 3599) // 3600)

    if en:
        labels = ("Year", "Quarter", "Month", "Week", "Today")
        day_unit, hour_unit = "d", "h"
    else:
        labels = ("年", "季", "月", "周", "今天")
        day_unit, hour_unit = "天", "小时"

    def _remain(days: int) -> str:
        if en:
            return f"{days} {day_unit} left"
        return f"还剩 {days} {day_unit}"

    return [
        _level(labels[0], day_of_year, days_in_year, _remain(days_in_year - day_of_year)),
        _level(labels[1], q_passed, q_total, _remain(q_total - q_passed)),
        _level(labels[2], m_passed, m_total, _remain(m_total - m_passed)),
        _level(labels[3], w_passed, w_total, _remain(w_total - w_passed)),
        _level(labels[4], seconds_now, d_total,
               (f"{hours_left} {hour_unit} left" if en else f"还剩 {hours_left} {hour_unit}")),
    ]


def _resolve_today(date_ctx: dict) -> date:
    """优先用 date_ctx（和顶栏日期同源），取不到就用本机当天。"""
    now = datetime.now()
    try:
        year = int(date_ctx.get("year"))
        day = int(date_ctx.get("day"))
        month_cn = str(date_ctx.get("month_cn") or "")
        month = now.month
        # date_ctx 的 month_cn 来自 config.MONTH_CN（「一月」…「十二月」），
        # 与本页用于显示的 _ZH_MONTHS（「1月」…「12月」）格式不同，两边都要认。
        for idx, name in enumerate(_ZH_MONTHS, start=1):
            if month_cn in (name, f"{idx}月"):
                month = idx
                break
        else:
            for idx, name in enumerate(MONTH_CN, start=1):
                if month_cn == name:
                    month = idx
                    break
        return date(year, month, day)
    except (TypeError, ValueError):
        return now.date()


async def build_year_progress_content(fallback: dict | None = None, **kwargs: Any) -> dict[str, Any]:
    """年度进度内容。全量本地实时计算。"""
    fallback = fallback if isinstance(fallback, dict) else {}
    date_ctx = kwargs.get("date_ctx") or {}
    lang = str(kwargs.get("language") or "zh")
    en = lang == "en"

    now = datetime.now()
    today = _resolve_today(date_ctx)
    levels = _build_levels(today, now, lang)
    grid = _build_grid(today)

    month_labels = _EN_MONTHS if en else _ZH_MONTHS
    grid["months"] = [
        {"col": m["col"], "label": month_labels[m["month"] - 1]} for m in grid["months"]
    ]
    grid["weekday_labels"] = list(_EN_WEEKDAY_ROWS if en else _ZH_WEEKDAY_ROWS)

    countdowns = []
    for d, name in upcoming_holidays(today, 4):
        days = (d - today).days
        countdowns.append({
            "name": name,
            "days": days,
            "days_text": (f"{days} d" if en else f"{days} 天"),
        })

    if en:
        legend = [
            {"label": "Past", "state": STATE_PAST},
            {"label": "Today", "state": STATE_TODAY},
            {"label": "Holiday", "state": STATE_HOLIDAY},
            {"label": "Future", "state": STATE_FUTURE},
        ]
    else:
        legend = [
            {"label": "已过", "state": STATE_PAST},
            {"label": "今天", "state": STATE_TODAY},
            {"label": "假日", "state": STATE_HOLIDAY},
            {"label": "未来", "state": STATE_FUTURE},
        ]

    year_levels = levels[0]
    if en:
        title = "Year Progress"
        subtitle = f"{today.year} · Week {today.isocalendar().week}"
        big_label = f"{today.year}"
        elapsed_text = f"{year_levels['value']} days in"
        remaining_text = f"{year_levels['max'] - year_levels['value']} days left"
    else:
        title = "年度进度"
        subtitle = f"{today.year}年 第{today.isocalendar().week}周"
        big_label = f"{today.year}年"
        elapsed_text = f"已过 {year_levels['value']} 天"
        remaining_text = f"还剩 {year_levels['max'] - year_levels['value']} 天"

    content = {
        "title": title,
        "subtitle": subtitle,
        "year": today.year,
        "big_pct": year_levels["pct_text"],
        "big_label": big_label,
        "elapsed_text": elapsed_text,
        "remaining_text": remaining_text,
        "levels": levels,
        "grid": grid,
        "legend": legend,
        "countdowns": countdowns,
    }
    content.update({k: v for k, v in fallback.items() if k not in content})
    return content
