"""日昼与月相 provider（城市可配）。

分工：
- 日出 / 日落 / 白昼时长：取自 Open-Meteo 的 daily 接口（真实数据，精度到分钟）。
- 月相：本地天文算法计算，用 PIL 画成适合墨水屏的相位图（本地静态资源）。
- 一句话内容：LLM 生成，按「城市 + 日期 + 昼夜」缓存，一天最多调两次。

城市：优先取模式专属覆盖 mode_overrides[CUSTOM_SUN_MOON] 里的地点；
没有覆盖时跟随设备全局位置。地点作为一个整体解析，避免出现
「城市是上海、坐标还是郑州」的串味。

原则：数值永远不由 LLM 产出，LLM 只负责那句话。LLM 失败时退回写死的备选句，
不生成假数据。
"""

from __future__ import annotations

from .config import EINK_4COLOR_PALETTE

# 屏上四色原色（顺序同 config.EINK_4COLOR_PALETTE：黑/白/金黄/朱红）
_PAL = [tuple(EINK_4COLOR_PALETTE[i:i + 3]) for i in range(0, 12, 3)]
EINK_BLACK_RGB, EINK_WHITE_RGB, EINK_GOLD_RGB, EINK_RED_RGB = _PAL

import hashlib
import json
import logging
import math
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

MODE_ID = "CUSTOM_SUN_MOON"
DEFAULT_TZ_NAME = "Asia/Shanghai"
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
ARC_DIR = _BACKEND_DIR / "static" / "arc"

WEATHER_TTL = 1800  # Open-Meteo 结果缓存 30 分钟
# 月相图画布边长:必须等于模式 JSON 里 image 块的 width/height(104),
# 这样渲染器 fit=contain 时是 1:1 缩放(PIL resize 同尺寸直接 copy,不做插值),
# 边缘没有灰过渡,4 色量化时不会被抖成黄/红杂色。
MOON_RENDER_SIZE = 104
# 月相图美术版本号：改动画法就加 1，旧文件自动失效重画
MOON_ART_VERSION = "v4"
DAY_ARC_VERSION = "v1"
ARC_RENDER_WIDTH = 320
ARC_RENDER_HEIGHT = 74

FALLBACK_LINES = {
    "day": "光一天短一点，省着点用",
    "night": "月色正好，风也轻",
}

_DIGIT_RE = re.compile(r"[0-9０-９]")
_QUOTE_CHARS = "“”\"'「」『』《》 \t"


def _tz(tz_name: str = DEFAULT_TZ_NAME):
    """返回指定时区；系统缺 tzdata 时退回固定 +08:00。"""
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(tz_name or DEFAULT_TZ_NAME)
    except Exception:  # pragma: no cover - 取决于宿主 tzdata
        return timezone(timedelta(hours=8))


# --------------------------------------------------------------------------
# 地点解析
# --------------------------------------------------------------------------


def _clean_str(value) -> str:
    return str(value).strip() if isinstance(value, str) else ""


def _coord(value, limit: float) -> float | None:
    """把任意输入折成合法坐标；不合法返回 None。"""
    if isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num != num or abs(num) > limit:  # NaN / 越界
        return None
    return num


def _city_coords(city: str) -> tuple[float, float] | None:
    """按城市名查内置坐标表；查不到返回 None（不猜）。"""
    if not city:
        return None
    try:
        from .config import CITY_COORDINATES
        from .context import _normalize_place_name

        coords = CITY_COORDINATES.get(city)
        if coords:
            return float(coords[0]), float(coords[1])
        normalized = _normalize_place_name(city)
        for name, value in CITY_COORDINATES.items():
            if _normalize_place_name(name) == normalized:
                return float(value[0]), float(value[1])
    except Exception:  # pragma: no cover - 坐标表缺失时按无坐标处理
        logger.warning("[SunMoon] 城市坐标表不可用: %s", city, exc_info=True)
    return None


def _reconcile_coords(city: str, lat: float | None, lon: float | None) -> tuple[float | None, float | None]:
    """城市名与坐标对不上时，以城市名为准。

    预览时后端会只把全局 city 换成预览城市，坐标还是设备原来的，
    不对齐就会算出「杭州名字 + 郑州日出」。偏差小于 1° 视为同城，
    保留设备自己的精确坐标。
    """
    table = _city_coords(city)
    if not table:
        return lat, lon
    tlat, tlon = table
    if lat is None or lon is None:
        return tlat, tlon
    if abs(tlat - lat) > 1.0 or abs(tlon - lon) > 1.0:
        return tlat, tlon
    return lat, lon


def _resolve_location(config: dict | None) -> dict:
    """解析本次要用的地点：模式专属覆盖优先，否则跟随设备全局位置。

    地点是一个整体：城市名和经纬度必须来自同一处，不能一半来自覆盖、
    一半来自设备全局，否则会出现「城市换了、坐标没换」。
    """
    cfg = config if isinstance(config, dict) else {}
    overrides = cfg.get("mode_overrides")
    if not isinstance(overrides, dict):
        overrides = {}
    override = overrides.get(MODE_ID)
    if not isinstance(override, dict):
        override = {}

    city = _clean_str(override.get("city"))
    lat = _coord(override.get("latitude"), 90.0)
    lon = _coord(override.get("longitude"), 180.0)
    tz_name = _clean_str(override.get("timezone"))

    if not city:
        # 没有模式专属地点 → 跟随设备全局位置
        city = _clean_str(cfg.get("city"))
        lat = lat if lat is not None else _coord(cfg.get("latitude"), 90.0)
        lon = lon if lon is not None else _coord(cfg.get("longitude"), 180.0)
        tz_name = tz_name or _clean_str(cfg.get("timezone"))

    if lat is None or lon is None:
        coords = _city_coords(city)
        if coords:
            lat, lon = coords
    lat, lon = _reconcile_coords(city, lat, lon)

    return {
        "city": city,
        "latitude": lat,
        "longitude": lon,
        "timezone": tz_name,
    }


def _location_key(location: dict) -> str:
    """地点分桶键：城市 + 坐标，缓存按它隔离。"""
    city = _clean_str(location.get("city")) or "?"
    lat = location.get("latitude")
    lon = location.get("longitude")
    lat_s = f"{float(lat):.3f}" if isinstance(lat, (int, float)) else "?"
    lon_s = f"{float(lon):.3f}" if isinstance(lon, (int, float)) else "?"
    return f"{city}|{lat_s}|{lon_s}"


# --------------------------------------------------------------------------
# 缓存
# --------------------------------------------------------------------------


def _read_cache() -> dict:
    """读运行时缓存。旧结构（固定郑州）自动迁移到按地点分桶。"""
    try:
        with CACHE_FILE.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {"locations": {}}
    if not isinstance(data, dict):
        return {"locations": {}}
    if isinstance(data.get("locations"), dict):
        return data

    legacy_bucket: dict = {}
    if isinstance(data.get("daily"), dict):
        legacy_bucket["daily"] = data["daily"]
    if data.get("daily_ts"):
        legacy_bucket["daily_ts"] = data["daily_ts"]
    if isinstance(data.get("llm"), dict):
        legacy_bucket["llm"] = data["llm"]
    if not legacy_bucket:
        return {"locations": {}}
    return {"locations": {"郑州|34.750|113.650": legacy_bucket}}


def _write_cache(data: dict) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_FILE.with_name(CACHE_FILE.name + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        tmp.replace(CACHE_FILE)
    except OSError:
        logger.warning("[SunMoon] 写缓存失败", exc_info=True)


def _bucket(cache: dict, key: str) -> dict:
    """取某个地点的缓存桶，没有就建一个。"""
    locations = cache.get("locations")
    if not isinstance(locations, dict):
        locations = {}
        cache["locations"] = locations
    bucket = locations.get(key)
    if not isinstance(bucket, dict):
        bucket = {}
        locations[key] = bucket
    return bucket


# --------------------------------------------------------------------------
# Open-Meteo：日出 / 日落 / 白昼时长
# --------------------------------------------------------------------------


async def _fetch_daily(location: dict) -> dict:
    """取昨天/今天/明天的日出日落白昼时长（按地点缓存）。

    用 timezone=auto：接口按地点自己的时区给当地日期和当地时刻，
    并回传 utc_offset_seconds，本地昼夜判定直接用它。
    """
    latitude = location.get("latitude")
    longitude = location.get("longitude")
    if latitude is None or longitude is None:
        raise ValueError(f"缺少坐标，无法取日出日落: {location.get('city')}")

    key = _location_key(location)
    cache = _read_cache()
    bucket = _bucket(cache, key)
    cached = bucket.get("daily")
    if (
        isinstance(cached, dict)
        and cached.get("rows")
        and (time.time() - float(bucket.get("daily_ts") or 0)) < WEATHER_TTL
    ):
        return cached

    import httpx

    params = {
        "latitude": latitude,
        "longitude": longitude,
        "daily": "sunrise,sunset,daylight_duration",
        "timezone": "auto",
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
    daily = {
        "rows": rows,
        "timezone": str(payload.get("timezone") or ""),
        "utc_offset_seconds": payload.get("utc_offset_seconds"),
    }
    bucket["daily"] = daily
    bucket["daily_ts"] = time.time()
    _write_cache(cache)
    return daily


def _local_now(now_utc: datetime, offset_seconds, tz_name: str) -> datetime:
    """地点当地时间（naive）。优先用接口回的 utc_offset，退回时区换算。"""
    if offset_seconds is not None:
        try:
            return (now_utc + timedelta(seconds=float(offset_seconds))).replace(tzinfo=None)
        except (TypeError, ValueError):
            pass
    return now_utc.astimezone(_tz(tz_name)).replace(tzinfo=None)


def _parse_naive(ts: str | None) -> datetime | None:
    """解析接口给的当地时刻字符串（接口已按地点当地时区给，这里只做本地时刻）。"""
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(str(ts))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(parsed.tzinfo).replace(tzinfo=None)
    return parsed


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


def render_moon_png(frac: float, path: Path, colors: int = 4) -> bool:
    """画一张四色月相图：受光面填金黄，背影面填墨黑，外圈墨黑勾边。

    几何模型：把月球视作球体，光源方向 s = (sin(2πf), 0, -cos(2πf))，
    表面法向 n 与 s 点积为正即被照亮，投影后就是真实的月相形状。

    只用屏上四色原色平涂，不做渐变：e-ink 四色走 Atkinson 抖动，
    中间色会抖成噪点，平涂才干净。2 色屏把金黄抖成白，形状与现在一致。
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

    img = Image.new("RGB", (size, size), EINK_WHITE_RGB)
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
            # 受光面亮、背影面暗。4 色屏受光面用金黄；
            # 2 色屏金黄会被抖动成噪点，改用白纸面（天文图惯例）
            lit_rgb = EINK_GOLD_RGB if colors >= 3 else EINK_WHITE_RGB
            pixels[x, y] = lit_rgb if lit else EINK_BLACK_RGB

    draw = ImageDraw.Draw(img)
    draw.ellipse(
        [center - radius, center - radius, center + radius, center + radius],
        outline=EINK_BLACK_RGB,
        width=2,
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        img.save(path, "PNG")
    except OSError:
        logger.warning("[SunMoon] 月相图写入失败: %s", path, exc_info=True)
        return False
    return True


def render_day_arc_png(sunrise, sunset, now, path: Path, colors: int = 4) -> bool:
    """画一条日轨弧：左端日出、右端日落，弧高按真实昼长，黑点标当前时刻。

    给「日昼与月相」白天侧用。弧下填金黄表示白昼区间，弧线与基线用墨黑。
    四色屏：金色白昼带 + 墨黑弧；2 色屏：金黄抖成白，只剩墨黑弧，形状照旧。
    """
    try:
        from PIL import Image, ImageDraw
    except Exception:  # pragma: no cover
        logger.warning("[SunMoon] PIL 不可用，跳过日轨图", exc_info=True)
        return False

    w, h = ARC_RENDER_WIDTH, ARC_RENDER_HEIGHT
    base_y = h - 6
    img = Image.new("RGB", (w, h), EINK_WHITE_RGB)
    draw = ImageDraw.Draw(img)

    total = max(1.0, (sunset - sunrise).total_seconds())
    amp = 10.0 + 44.0 * min(1.0, max(0.0, (total / 3600.0) / 24.0))
    margin = 10
    x0, x1 = margin, w - margin

    def point_at(t: float):
        x = x0 + (x1 - x0) * t
        y = base_y - amp * math.sin(math.pi * t)
        return int(round(x)), int(round(y))

    pts = [point_at(i / 120.0) for i in range(121)]
    if colors >= 3:
        band = pts + [(x1, base_y), (x0, base_y)]
        draw.polygon(band, fill=EINK_GOLD_RGB)
    draw.line(pts, fill=EINK_BLACK_RGB, width=2)
    draw.line([(x0, base_y), (x1, base_y)], fill=EINK_BLACK_RGB, width=1)
    for x in (x0, x1):
        draw.line([(x, base_y), (x, base_y - 7)], fill=EINK_BLACK_RGB, width=1)

    t_now = (now - sunrise).total_seconds() / total
    t_now = min(1.0, max(0.0, t_now))
    nx, ny = point_at(t_now)
    draw.ellipse([nx - 4, ny - 4, nx + 4, ny + 4], fill=EINK_BLACK_RGB)

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        img.save(path, "PNG")
    except OSError:
        logger.warning("[SunMoon] 日轨图写入失败: %s", path, exc_info=True)
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


def _moon_image_url(location_key: str, date_key: str, phase: dict, colors: int = 4) -> str:
    """生成（或复用）当天的月相图，返回可直接给 image 块用的本地 URL。

    文件名带地点指纹：不同城市的当地日期可能不同，月相图不能跨城市复用。
    """
    slug = hashlib.md5(location_key.encode("utf-8")).hexdigest()[:8]
    filename = f"moon_{slug}_{date_key.replace('-', '')}_{phase['index']}_c{colors}_{MOON_ART_VERSION}.png"
    path = MOON_DIR / filename
    if not path.exists():
        if not render_moon_png(phase["frac"], path, colors=colors):
            return ""
    _prune_moon_images(filename)
    return f"/static/moon/{filename}"


def _day_arc_url(location_key: str, date_key: str, sunrise, sunset, now, colors: int = 4) -> str:
    """生成当天的日轨弧图。

    同一地点同一天共用一个文件名，每次刷新就地重画：当前时刻点会沿弧移动，
    但文件名不变，磁盘上只留一份（渲染器直接读本地文件，不走 HTTP 缓存）。
    """
    slug = hashlib.md5(location_key.encode("utf-8")).hexdigest()[:8]
    filename = f"arc_{slug}_{date_key.replace('-', '')}_c{colors}_{DAY_ARC_VERSION}.png"
    path = ARC_DIR / filename
    if not render_day_arc_png(sunrise, sunset, now, path, colors=colors):
        return ""
    _prune_arc_images()
    return f"/static/arc/{filename}"


def _prune_arc_images(max_age_days: int = 3) -> None:
    """清掉过期日轨图，避免目录无限增长。"""
    try:
        import time as _time
        cutoff = _time.time() - max_age_days * 86400
        for path in ARC_DIR.glob("arc_*.png"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue
    except Exception:  # pragma: no cover
        logger.warning("[SunMoon] 清理日轨图失败", exc_info=True)




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


async def _llm_line(kind: str, date_key: str, prompt: str, location_key: str, **kwargs) -> str:
    """按「城市 + 日期 + 昼夜」缓存的一句 LLM 文案。失败时退回备选句。"""
    cache = _read_cache()
    bucket = _bucket(cache, location_key)
    llm_cache = bucket.get("llm")
    if not isinstance(llm_cache, dict):
        llm_cache = {}
        bucket["llm"] = llm_cache
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


def _period_from_sun(
    now_local: datetime, sunrise_dt: datetime | None, sunset_dt: datetime | None
) -> str:
    """昼夜判定：有日出日落按真实时刻，没有就按 6-18 点粗判。"""
    if sunrise_dt and sunset_dt:
        return "day" if sunrise_dt <= now_local <= sunset_dt else "night"
    return "day" if 6 <= now_local.hour < 18 else "night"


def cache_period(cfg: dict | None = None) -> str:
    """当前该出白天版还是夜里版 —— 给缓存键分段用。

    只读运行时缓存里的日出日落，不发网络请求。缓存里没有今天的记录时，
    按 6-18 点粗判，和 build_sun_moon_content 的兜底是同一套规则。
    """
    now_utc = datetime.now(timezone.utc)
    location = _resolve_location(cfg if isinstance(cfg, dict) else {})
    cache = _read_cache()
    locations = cache.get("locations") if isinstance(cache, dict) else None
    bucket = locations.get(_location_key(location)) if isinstance(locations, dict) else None
    daily = bucket.get("daily") if isinstance(bucket, dict) else None
    if not isinstance(daily, dict):
        return _period_from_sun(_local_now(now_utc, None, DEFAULT_TZ_NAME), None, None)
    now_local = _local_now(
        now_utc, daily.get("utc_offset_seconds"), daily.get("timezone") or DEFAULT_TZ_NAME
    )
    rows = daily.get("rows")
    today = None
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and row.get("date") == now_local.strftime("%Y-%m-%d"):
                today = row
                break
    if not today:
        return _period_from_sun(now_local, None, None)
    return _period_from_sun(
        now_local, _parse_naive(today.get("sunrise")), _parse_naive(today.get("sunset"))
    )


async def build_sun_moon_content(fallback: dict | None = None, **kwargs) -> dict:
    """生成日昼与月相内容。"""
    config = kwargs.get("config")
    colors = int(kwargs.get("colors", 2) or 2)
    location = _resolve_location(config if isinstance(config, dict) else {})
    location_key = _location_key(location)
    city = location["city"]

    now_utc = datetime.now(timezone.utc)

    try:
        daily = await _fetch_daily(location)
    except Exception as exc:
        logger.warning("[SunMoon] 取日出日落失败(%s): %s", city, exc)
        daily = {"rows": []}

    tz_name = _clean_str(daily.get("timezone")) or _clean_str(location.get("timezone")) or DEFAULT_TZ_NAME
    now_local = _local_now(now_utc, daily.get("utc_offset_seconds"), tz_name)
    date_key = now_local.strftime("%Y-%m-%d")
    week_cn = "一二三四五六日"[now_local.weekday()]
    date_cn = f"{now_local.month}月{now_local.day}日 周{week_cn}"

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

    # 昼夜判定：地点当地时间与当天日出日落比较
    sunrise_dt = _parse_naive(today.get("sunrise") if today else None)
    sunset_dt = _parse_naive(today.get("sunset") if today else None)
    period = _period_from_sun(now_local, sunrise_dt, sunset_dt)

    phase = moon_phase(now_utc)
    illum_pct = int(round(phase["illum"] * 100))
    moon_age = f"{phase['age_days']:.1f}"
    to_full = f"{phase['days_to_full']:.1f}"
    to_new = f"{phase['days_to_new']:.1f}"

    moon_image = _moon_image_url(location_key, date_key, phase, colors=colors)

    day_arc = ""
    if period == "day" and sunrise_dt and sunset_dt:
        day_arc = _day_arc_url(location_key, date_key, sunrise_dt, sunset_dt, now_local, colors=colors)

    info = {
        "city": city,
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
        line = await _llm_line("day", date_key, _day_prompt(info), location_key, **kwargs)
    else:
        line = await _llm_line("night", date_key, _night_prompt(info), location_key, **kwargs)

    content = {
        "period": period,
        "city": city,
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
        "day_arc": day_arc,
        "line": line,
        "source": "Open-Meteo",
    }
    if isinstance(fallback, dict):
        for key, value in fallback.items():
            content.setdefault(key, value)
    return content
