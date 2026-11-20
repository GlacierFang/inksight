"""按天定位：解析目标日期、构造那一天的 date_ctx、从状态里取那一天的历史记录。

历史留存用：回填时指定某一天，走真实渲染管线出整屏图。

原则：
- 只读历史。改状态、调模型、编内容都不在这一层做。
- 那天没有记录就是没有 —— 返回空字段，由画廊决定不占位。
- 历史缺字段如实标「历史缺失」，不用模型补写。
"""
from __future__ import annotations

import calendar
from datetime import date, datetime

from pathlib import Path

from .config import MONTH_CN, WEEKDAY_CN

_BACKEND_DIR = Path(__file__).resolve().parent.parent

MISSING = "历史缺失"


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def resolve_day(kwargs: dict | None) -> str:
    """从 date_ctx 取目标日期（YYYY-MM-DD）；取不到就用今天。

    渲染管线把目标日期放在 date_ctx 里（year/month/day 是数字），
    各 provider 靠这个函数知道自己要画哪一天。
    """
    ctx = (kwargs or {}).get("date_ctx") or {}
    try:
        target = date(int(ctx["year"]), int(ctx["month"]), int(ctx["day"]))
    except (KeyError, TypeError, ValueError):
        return today_str()
    return target.isoformat()


def is_today(day: str) -> bool:
    return day == today_str()


def date_ctx_for(day: str) -> dict:
    """构造某一天的 date_ctx（渲染管线用的那一套键）。

    天气不放这里 —— 历史日的天气取不到，调用方一律留空。
    """
    target = date.fromisoformat(day)
    weekday = target.weekday()
    return {
        "date_str": f"{target.month}月{target.day}日 {WEEKDAY_CN[weekday]}",
        "time_str": "12:00:00",
        "weekday": weekday,
        "hour": 12,
        "is_weekend": weekday >= 5,
        "year": target.year,
        "month": target.month,
        "day": target.day,
        "month_cn": MONTH_CN[target.month - 1],
        "weekday_cn": WEEKDAY_CN[weekday],
        "day_of_year": target.timetuple().tm_yday,
        "days_in_year": 366 if calendar.isleap(target.year) else 365,
        "festival": "",
        "is_holiday": False,
        "is_workday": True,
        "upcoming_holiday": "",
        "days_until_holiday": 0,
        "holiday_date": "",
        "daily_word": "",
    }


def find_record(state: dict, day: str) -> dict | None:
    """在状态文件里找那一天的历史记录。

    days 是字典（天工阁/丹青谱/海错图）和 recent 是列表（大宋梦影/躺平巴拉/铺道蚁）
    两种形状都认；同一天多条时取最后一条。
    """
    days = state.get("days")
    if isinstance(days, dict):
        record = days.get(day)
        return record if isinstance(record, dict) else None
    for key in ("recent", "days", "history"):
        items = state.get(key)
        if not isinstance(items, list):
            continue
        for item in reversed(items):
            if isinstance(item, dict) and str(item.get("date")) == day:
                return item
    return None


def missing_ok(value) -> str:
    """历史字段：没记到就如实标「历史缺失」，不补、不编。"""
    if isinstance(value, list):
        return "".join(str(v) for v in value if v) or MISSING
    return str(value or "").strip() or MISSING


def art_url_or_pattern(record: dict, day: str, state_name: str, pattern: str) -> str:
    """那天那张素材图的路径。

    旧记录常常没写 art_url（那是后来才加的），按命名规则找回：
    找得到就用，找不到就空着 —— 那天确实没图。
    """
    url = str(record.get("art_url") or "")
    if url:
        return url
    index = record.get("index")
    index = int(index) if isinstance(index, int) and index > 0 else 1
    stem = pattern.format(date=day.replace("-", ""), index=index)
    rel = f"{state_name}/{stem}"
    try:
        path = _BACKEND_DIR / "static" / rel
        if path.is_file():
            return f"/static/{rel}"
    except OSError:
        pass
    return ""
