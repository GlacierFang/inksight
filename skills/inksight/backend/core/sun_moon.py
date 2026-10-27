"""日昼与月相 provider（郑州）。

分工：
- 日出 / 日落 / 白昼时长：取自 Open-Meteo 的 daily 接口（真实数据，精度到分钟）。
- 月相：本地天文算法计算，用 PIL 画成适合墨水屏的相位图（本地静态资源）。
- 一句话内容：LLM 生成，按「日期 + 昼夜」缓存，一天最多调两次。

原则：数值永远不由 LLM 产出，LLM 只负责那句话。LLM 失败时退回写死的备选句，
不生成假数据。
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

CITY = "郑州"
LAT = 34.75
LON = 113.65
TZ_NAME = "Asia/Shanghai"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# 新月基准时刻（UTC）与朔望月长度（天）
NEW_MOON_REF = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
SYNODIC_DAYS = 29.530588853
HALF_SYNODIC = SYNODIC_DAYS / 2

# 八分相名
PHASE_NAMES = ("新月", "峨眉月", "上弦月", "盈凸月", "满月", "亏凸月", "下弦月", "残月")

_BACKEND_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = _BACKEND_DIR / "runtime_cache"
CACHE_FILE = CACHE_DIR / "sun_moon.json"
MOON_DIR = _BACKEND_DIR / "static" / "moon"

WEATHER_TTL = 1800  # Open-Meteo 结果缓存 30 分钟
# 月相图画布边长:必须等于模式 JSON 里 image 块的 width/height(104),
# 这样渲染器 fit=contain 时是 1:1 缩放(PIL resize 同尺寸直接 copy,不做插值),
# 边缘没有灰过渡,4 色量化时不会被抖成黄/红杂色。
MOON_RENDER_SIZE = 104
# 月相图美术版本号：改动画法就加 1，旧文件自动失效重画
MOON_ART_VERSION = "v3"

FALLBACK_LINES = {
    "day": "光一天短一点，省着点用",
    "night": "月色正好，风也轻",
}

_DIGIT_RE = re.compile(r"[0-9０-９]")
_QUOTE_CHARS = "“”\"'「」『』《》 \t"


def _tz():
    """返回郑州时区；系统缺 tzdata 时退回固定 +08:00。"""
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(TZ_NAME)
    except Exception:  # pragma: no cover - 取决于宿主 tzdata
        return timezone(timedelta(hours=8))


# --------------------------------------------------------------------------
# 缓存
# --------------------------------------------------------------------------


def _read_cache() -> dict:
    try:
        with CACHE_FILE.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_cache(data: dict) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_FILE.with_name(CACHE_FILE.name + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        tmp.replace(CACHE_FILE)
    except OSError:
        logger.warning("[SunMoon] 写缓存失败", exc_info=True)


# --------------------------------------------------------------------------
# Open-Meteo：日出 / 日落 / 白昼时长
# --------------------------------------------------------------------------


async def _fetch_daily() -> dict:
    """取当天与前一天的日出日落白昼时长。返回 {"rows": [...]}。"""
    cache = _read_cache()
    cached = cache.get("daily")
    if (
        isinstance(cached, dict)
        and cached.get("rows")
        and (time.time() - float(cache.get("daily_ts") or 0)) < WEATHER_TTL
    ):
        return cached

    import httpx

    params = {
        "latitude": LAT,
        "longitude": LON,
        "daily": "sunrise,sunset,daylight_duration",
        "timezone": TZ_NAME,
        "past_days": 1,
        "forecast_days": 2,
    }
    async with httpx.AsyncClient(timeout=12.0) as client:
        resp = await client.get(OPEN_METEO_URL, params=params)
        resp.raise_for_status()
        payload = resp.json()

    raw = payload.get("daily") or {}
    times = list(raw.get("time") or [])
    sunrises = list(raw.get("sunrise") or [])
    sunsets = list(raw.get("sunset") or [])
    durations = list(raw.get("daylight_duration") or [])

    def _at(seq, idx):
        return seq[idx] if 0 <= idx < len(seq) else None

    rows = []
    for i, day in enumerate(times):
        rows.append(
            {
                "date": str(day),
                "sunrise": _at(sunrises, i),
                "sunset": _at(sunsets, i),
                "daylight": _at(durations, i),
            }
        )
    daily = {"rows": rows}
    cache["daily"] = daily
    cache["daily_ts"] = time.time()
    _write_cache(cache)
    return daily


def _parse_local(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts)).replace(tzinfo=_tz())
    except ValueError:
        return None


def _hhmm(ts: str | None) -> str:
    if not ts or "T" not in str(ts):
        return "--:--"
    return str(ts).split("T", 1)[1][:5]


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "--"
    total = int(round(float(seconds) / 60.0))
    return f"{total // 60}小时{total % 60:02d}分"


def _delta_text(today_sec: float | None, prev_sec: float | None) -> str:
    if today_sec is None or prev_sec is None:
        return ""
    diff = float(today_sec) - float(prev_sec)
    if abs(diff) < 60:
        return "与昨天几乎持平"
    mins = int(round(abs(diff) / 60.0))
    return f"比昨天{'长' if diff > 0 else '短'} {mins} 分钟"


# --------------------------------------------------------------------------
# 月相计算与绘图
# --------------------------------------------------------------------------


def moon_phase(now_utc: datetime) -> dict:
    """返回月龄、照亮比例、相名等。"""
    age = ((now_utc - NEW_MOON_REF).total_seconds() / 86400.0) % SYNODIC_DAYS
    frac = age / SYNODIC_DAYS
    illum = (1 - math.cos(2 * math.pi * frac)) / 2
    idx = int(frac * 8) % 8
    return {
        "age_days": age,
        "frac": frac,
        "illum": illum,
        "index": idx,
        "name": PHASE_NAMES[idx],
        "waxing": age < HALF_SYNODIC,
        "days_to_full": (HALF_SYNODIC - age) % SYNODIC_DAYS,
        "days_to_new": (SYNODIC_DAYS - age) % SYNODIC_DAYS,
    }


def render_moon_png(frac: float, path: Path) -> bool:
    """画一张 1-bit 月相图：被照亮的部分填黑，未照亮部分留白，外圈加轮廓。

    几何模型：把月球视作球体，光源方向 s = (sin(2πf), 0, -cos(2πf))，
    表面法向 n 与 s 点积为正即被照亮，投影后就是真实的月相形状。
    """
    try:
        from PIL import Image, ImageDraw
    except Exception:  # pragma: no cover
        logger.warning("[SunMoon] PIL 不可用，跳过月相图", exc_info=True)
        return False

    size = MOON_RENDER_SIZE
    radius = size / 2 - 2
    center = size / 2
    angle = 2 * math.pi * frac
    sin_a, cos_a = math.sin(angle), math.cos(angle)

    img = Image.new("L", (size, size), 255)
    pixels = img.load()
    r2 = radius * radius
    for y in range(size):
        dy = y + 0.5 - center
        for x in range(size):
            dx = x + 0.5 - center
            d2 = dx * dx + dy * dy
            if d2 > r2:
                continue
            z = math.sqrt(r2 - d2)
            lit = (dx * sin_a - z * cos_a) > 0
            # 天文图惯例：受光部分留白，阴影部分填黑
            pixels[x, y] = 255 if lit else 0

    draw = ImageDraw.Draw(img)
    draw.ellipse(
        [center - radius, center - radius, center + radius, center + radius],
        outline=0,
        width=2,
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        img.save(path, "PNG")
    except OSError:
        logger.warning("[SunMoon] 月相图写入失败: %s", path, exc_info=True)
        return False
    return True


def _prune_moon_images(keep: str, max_age_days: int = 5) -> None:
    """清掉过期月相图，只留今天这张。"""
    try:
        cutoff = time.time() - max_age_days * 86400
        for path in MOON_DIR.glob("moon_*.png"):
            if path.name == keep:
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue
    except OSError:
        pass


def _moon_image_url(date_key: str, phase: dict) -> str:
    """生成（或复用）当天的月相图，返回可直接给 image 块用的本地 URL。"""
    filename = f"moon_{date_key.replace('-', '')}_{phase['index']}_{MOON_ART_VERSION}.png"
    path = MOON_DIR / filename
    if not path.exists():
        if not render_moon_png(phase["frac"], path):
            return ""
    _prune_moon_images(filename)
    return f"/static/moon/{filename}"


# --------------------------------------------------------------------------
# LLM 一句话
# --------------------------------------------------------------------------


def _clean_line(text: str) -> str:
    """取首行、去引号、去数字，避免 LLM 编造具体数值。"""
    raw = (text or "").strip()
    if not raw:
        return ""
    line = raw.splitlines()[0].strip()
    line = line.strip(_QUOTE_CHARS)
    line = _DIGIT_RE.sub("", line)
    line = re.sub(r"\s{2,}", " ", line).strip()
    line = line.strip("。．.，,、；;：:！!？?~～—-")
    return line.strip()


def _day_prompt(info: dict) -> str:
    return (
        "你在给一块墨水屏写一句话，屏上要显示今天的日出、日落和白昼时长。\n"
        f"城市：{info['city']}\n"
        f"日期：{info['date_cn']}\n"
        f"日出：{info['sunrise']}\n"
        f"日落：{info['sunset']}\n"
        f"白昼时长：{info['daylight']}\n"
        f"白昼变化：{info['delta']}\n\n"
        "请写一句与今天的日出日落、白昼长短有关的中文短句。\n"
        "要求：不超过 20 个字；不要出现任何数字；不要引号；不要解释；只输出这一句。"
    )


def _night_prompt(info: dict) -> str:
    return (
        "你在给一块墨水屏写一句话，屏上要显示今晚的月相。\n"
        f"城市：{info['city']}\n"
        f"日期：{info['date_cn']}\n"
        f"月相：{info['moon_name']}（{'盈' if info['waxing'] else '亏'}，照亮 {info['illum_pct']}%）\n"
        f"月龄：{info['moon_age']} 天\n"
        f"距下次满月：{info['to_full']} 天\n\n"
        "请写一句与今夜月色有关的中文短句。\n"
        "要求：不超过 20 个字；不要出现任何数字；不要引号；不要解释；只输出这一句。"
    )


async def _llm_line(kind: str, date_key: str, prompt: str, **kwargs) -> str:
    """按「日期 + 昼夜」缓存的一句 LLM 文案。失败时退回备选句。"""
    cache = _read_cache()
    llm_cache = cache.get("llm")
    if not isinstance(llm_cache, dict):
        llm_cache = {}
        cache["llm"] = llm_cache
    key = f"{date_key}_{kind}"
    cached = llm_cache.get(key)
    if isinstance(cached, str) and cached.strip():
        return cached

    from .content import _call_llm

    provider = str(kwargs.get("llm_provider") or "").strip() or "minimax"
    model = str(kwargs.get("llm_model") or "").strip() or "MiniMax-M3"
    try:
        raw = await _call_llm(
            provider,
            model,
            prompt,
            temperature=0.9,
            max_tokens=1500,
            api_key=kwargs.get("api_key"),
            base_url=kwargs.get("llm_base_url"),
        )
    except Exception as exc:
        logger.warning("[SunMoon] LLM 生成失败(%s): %s", kind, exc)
        return FALLBACK_LINES.get(kind, "")

    line = _clean_line(raw)
    if len(line) < 4:
        logger.warning("[SunMoon] LLM 文案过短或含数字过多: %r", raw)
        return FALLBACK_LINES.get(kind, "")

    llm_cache[key] = line
    _write_cache(cache)
    return line


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------


async def build_sun_moon_content(fallback: dict | None = None, **kwargs) -> dict:
    """生成日昼与月相内容。"""
    now_local = datetime.now(_tz())
    now_utc = now_local.astimezone(timezone.utc)
    date_key = now_local.strftime("%Y-%m-%d")
    week_cn = "一二三四五六日"[now_local.weekday()]
    date_cn = f"{now_local.month}月{now_local.day}日 周{week_cn}"

    try:
        daily = await _fetch_daily()
    except Exception as exc:
        logger.warning("[SunMoon] 取日出日落失败: %s", exc)
        daily = {"rows": []}

    rows = [r for r in (daily.get("rows") or []) if isinstance(r, dict)]
    today = next((r for r in rows if r.get("date") == date_key), None)
    prev_dates = sorted(r["date"] for r in rows if r.get("date") and r["date"] < date_key)
    yesterday = next((r for r in rows if r.get("date") == prev_dates[-1]), None) if prev_dates else None

    sunrise = _hhmm(today.get("sunrise") if today else None)
    sunset = _hhmm(today.get("sunset") if today else None)
    daylight_sec = today.get("daylight") if today else None
    prev_sec = yesterday.get("daylight") if yesterday else None
    daylight = _format_duration(daylight_sec)
    delta = _delta_text(daylight_sec, prev_sec)

    # 昼夜判定：以郑州本地时间与当天日出日落比较
    sunrise_dt = _parse_local(today.get("sunrise") if today else None)
    sunset_dt = _parse_local(today.get("sunset") if today else None)
    if sunrise_dt and sunset_dt and sunrise_dt <= now_local <= sunset_dt:
        period = "day"
    elif sunrise_dt and sunset_dt:
        period = "night"
    else:
        period = "day" if 6 <= now_local.hour < 18 else "night"

    phase = moon_phase(now_utc)
    illum_pct = int(round(phase["illum"] * 100))
    moon_age = f"{phase['age_days']:.1f}"
    to_full = f"{phase['days_to_full']:.1f}"
    to_new = f"{phase['days_to_new']:.1f}"

    moon_image = _moon_image_url(date_key, phase)

    info = {
        "city": CITY,
        "date_cn": date_cn,
        "sunrise": sunrise,
        "sunset": sunset,
        "daylight": daylight,
        "delta": delta,
        "moon_name": phase["name"],
        "waxing": phase["waxing"],
        "illum_pct": illum_pct,
        "moon_age": moon_age,
        "to_full": to_full,
    }
    if period == "day":
        line = await _llm_line("day", date_key, _day_prompt(info), **kwargs)
    else:
        line = await _llm_line("night", date_key, _night_prompt(info), **kwargs)

    content = {
        "period": period,
        "city": CITY,
        "date_cn": date_cn,
        "sunrise": sunrise,
        "sunset": sunset,
        "daylight": daylight,
        "delta": delta,
        "moon_name": phase["name"],
        "moon_illum": f"{illum_pct}%",
        "moon_age": f"{moon_age} 天",
        "moon_to_full": f"距满月 {to_full} 天",
        "moon_to_new": f"距新月 {to_new} 天",
        "moon_image": moon_image,
        "line": line,
        "source": "Open-Meteo",
    }
    if isinstance(fallback, dict):
        for key, value in fallback.items():
            content.setdefault(key, value)
    return content
