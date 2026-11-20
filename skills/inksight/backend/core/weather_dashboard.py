"""天气看板 provider：把 Open-Meteo 的实况与逐小时预报画成一屏天气图。

参照一张 800×600 的天气屏做的复刻，按 400×300 缩排：
- 左上：太阳/云图标 + 七段数码大字温度 + 五行实况（天气/体感/湿度/风/降水）
- 右上：三天预报（图标 + 温度区间 + 天气文字，虚线分隔）
- 下半屏：逐小时气温曲线（带数值标注）+ 逐小时降水概率柱

数据一次拉齐：current / hourly / daily 同一个请求取回，落 runtime_cache 缓存
30 分钟，避免设备每次取图都打外网。整屏由 PIL 画好写成一张 PNG，
模式 JSON 只挂一个 image 块 —— 版式全在这份代码里，不拆成 JSON 拼装。
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import math

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

MODE_ID = "CUSTOM_WEATHER"

_BACKEND_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = _BACKEND_DIR / "runtime_cache"
CACHE_FILE = CACHE_DIR / "weather_dashboard.json"
ASSET_DIR = _BACKEND_DIR / "static" / "weather"

CACHE_TTL = 1800  # Open-Meteo 结果缓存 30 分钟
CACHE_VERSION = "v2"  # 预报天数/字段变了要失效旧缓存

PAGE_W = 400
PAGE_H = 300

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

from .config import EINK_4COLOR_PALETTE  # noqa: E402

# 屏上四色原色（顺序同 config.EINK_4COLOR_PALETTE：黑/白/金黄/朱红）
_PAL = [tuple(EINK_4COLOR_PALETTE[i:i + 3]) for i in range(0, 12, 3)]
BLACK, WHITE, GOLD, RED = _PAL

TRUETYPE_DIR = _BACKEND_DIR / "fonts" / "truetype"

WEEKDAY_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

# 七段数码管：每位点亮的段
_SEGMENTS = {
    "0": "abcdef",
    "1": "bc",
    "2": "abdeg",
    "3": "abcdg",
    "4": "bcfg",
    "5": "acdfg",
    "6": "acdefg",
    "7": "abc",
    "8": "abcdefg",
    "9": "abcdfg",
    "-": "g",
}


# ── 小工具 ────────────────────────────────────────────────────────────


def _clean_str(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _wind_dir_cn(deg) -> str:
    try:
        deg = float(deg)
    except (TypeError, ValueError):
        return ""
    names = ("北风", "东北风", "东风", "东南风", "南风", "西南风", "西风", "西北风")
    return names[int((deg % 360) / 45 + 0.5) % 8]


def _wind_level_cn(speed) -> str:
    """风速 km/h → 风力等级。"""
    try:
        s = float(speed)
    except (TypeError, ValueError):
        return ""
    for level, hi in ((1, 5), (2, 11), (3, 19), (4, 28), (5, 38), (6, 49),
                      (7, 61), (8, 74), (9, 88), (10, 102), (11, 117)):
        if s <= hi:
            return f"{level}级"
    return "12级"


def _code_desc(code) -> str:
    mapping = {
        0: "晴", 1: "多云", 2: "多云", 3: "阴", 45: "雾", 48: "雾凇",
        51: "小雨", 53: "中雨", 55: "大雨", 56: "冻雨", 57: "冻雨",
        61: "小雨", 63: "中雨", 65: "大雨", 66: "冻雨", 67: "冻雨",
        71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
        80: "阵雨", 81: "阵雨", 82: "暴雨", 85: "阵雪", 86: "阵雪",
        95: "雷阵雨", 96: "雷阵雨", 99: "雷阵雨",
    }
    try:
        code = int(code)
    except (TypeError, ValueError):
        return "未知"
    return mapping.get(code, "未知")


def _location(cfg) -> dict:
    """取城市与经纬度。模式专属城市优先，其次设备全局位置。"""
    c = cfg if isinstance(cfg, dict) else {}
    overrides = c.get("mode_overrides")
    override = {}
    if isinstance(overrides, dict):
        cand = overrides.get(MODE_ID)
        if isinstance(cand, dict):
            override = cand

    city = _clean_str(override.get("city")) or _clean_str(c.get("city"))
    try:
        lat = float(override.get("latitude")) if override.get("latitude") is not None else None
    except (TypeError, ValueError):
        lat = None
    try:
        lon = float(override.get("longitude")) if override.get("longitude") is not None else None
    except (TypeError, ValueError):
        lon = None
    if lat is None or lon is None:
        from .context import _resolve_city

        f_lat, f_lon = _resolve_city(city)
        lat = lat if lat is not None else f_lat
        lon = lon if lon is not None else f_lon
    return {"city": city, "lat": lat, "lon": lon}


def _loc_key(loc: dict) -> str:
    return f"{loc.get('city') or '?'}|{loc.get('lat')}|{loc.get('lon')}"


def cache_bucket(cfg=None) -> str:
    """缓存分桶：天气图含逐小时预报，按当地钟点分桶，一小时一份。

    用当地时间而不是 UTC —— 日更缓存的日期键也是本地日期，两者要对齐，
    否则跨过本地零点时会出现「日期没变、钟点变了」的错位。
    """
    tz_name = ""
    if isinstance(cfg, dict):
        tz_name = _clean_str(cfg.get("timezone"))
    if tz_name:
        try:
            from zoneinfo import ZoneInfo

            return datetime.now(ZoneInfo(tz_name)).strftime("%H")
        except Exception:
            pass
    return datetime.now().strftime("%H")


# ── 取数 ──────────────────────────────────────────────────────────────


async def _fetch(loc: dict) -> dict:
    """Open-Meteo 一次拉齐实况/逐小时/逐日，带 30 分钟本地缓存。"""
    key = _loc_key(loc) + f"#{CACHE_VERSION}"
    try:
        cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    if not isinstance(cache, dict):
        cache = {}
    entry = cache.get(key)
    if (
        isinstance(entry, dict)
        and entry.get("data")
        and (time.time() - float(entry.get("ts") or 0)) < CACHE_TTL
    ):
        return entry["data"]

    params = {
        "latitude": loc["lat"],
        "longitude": loc["lon"],
        "current": (
            "temperature_2m,apparent_temperature,relative_humidity_2m,"
            "wind_speed_10m,wind_direction_10m,weather_code"
        ),
        "hourly": "temperature_2m,precipitation_probability",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min",
        "timezone": "auto",
        "forecast_days": 4,
    }

    import httpx

    async with httpx.AsyncClient(timeout=12.0) as client:
        resp = await client.get(OPEN_METEO_URL, params=params)
        resp.raise_for_status()
        payload = resp.json()

    cache[key] = {"ts": time.time(), "data": payload}
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_FILE.with_name(CACHE_FILE.name + ".tmp")
        tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        tmp.replace(CACHE_FILE)
    except OSError:
        logger.warning("[Weather] 缓存写入失败", exc_info=True)
    return payload


# ── 绘图工具 ──────────────────────────────────────────────────────────


def _font(name: str, size: int):
    from PIL import ImageFont

    try:
        return ImageFont.truetype(str(TRUETYPE_DIR / name), size)
    except Exception:
        return ImageFont.load_default()


def _text(draw, xy, s: str, font, fill=BLACK, anchor=None):
    """画文字：用 1 位掩膜渲染，笔画硬边、无灰阶。

    墨水屏上文字要实心。直接画 RGB 抗锯齿字会留下中灰边，而四色量化
    会把中灰判给金黄（比黑白混合更近），字口就长出金黄点。
    用「抗锯齿 + 固定阈值」自己砍灰阶也不行 —— 小字号的笔画覆盖率本
    来就低，阈值一刀切下去笔画就断了。

    PIL 的 ImageDraw.Draw(im, "1") 直接出 1 位掩膜：字体 hinting 自己
    把笔画落到像素网格上，笔画完整且没有灰阶，量化时误差为 0。
    """
    img = getattr(draw, "_image", None)
    if img is None or not s:
        draw.text(xy, s, font=font, fill=fill, anchor=anchor)
        return
    mask = Image.new("1", img.size, 0)
    ImageDraw.Draw(mask, "1").text(xy, s, font=font, fill=1, anchor=anchor)
    ink = mask.getbbox()
    if not ink:
        return
    region = mask.crop(ink)
    img.paste(Image.new("RGB", region.size, fill), ink[:2], region)


def _text_w(draw, s: str, font) -> int:
    box = draw.textbbox((0, 0), s, font=font)
    return box[2] - box[0]


def _dotted_vline(draw, x: int, y0: int, y1: int, fill=BLACK, step: int = 4):
    y = y0
    while y < y1:
        draw.line([(x, y), (x, min(y + 2, y1))], fill=fill, width=1)
        y += step


# ── 图标：四色网点 ────────────────────────────────────────
# 参考图（479×321 实拍屏）的太阳是金黄↔朱红网点、云是白底黑点，
# 质感来自「四色抖动」本身。
#
# 做法：不用连续调交给管线抖动 —— 管线是 Atkinson 误差扩散，误差会随机
# 偏色（金黄与朱红之间只按距离选），网点不可控且中灰会被判给金黄。
# 这里自己按 8×8 有序抖动算好网点，直接用四个物理色原色落笔：
# 量化时像素到调色板距离为 0、误差为 0，图案原样保留。
#
# 纯 PIL 实现：服务 venv 里没有 numpy，不能引入。

_BAYER8 = (
    (0, 32, 8, 40, 2, 34, 10, 42),
    (48, 16, 56, 24, 50, 18, 58, 26),
    (12, 44, 4, 36, 14, 46, 6, 38),
    (60, 28, 52, 20, 62, 30, 54, 22),
    (3, 35, 11, 43, 1, 33, 9, 41),
    (51, 19, 59, 27, 49, 17, 57, 25),
    (15, 47, 7, 39, 13, 45, 5, 37),
    (63, 31, 55, 23, 61, 29, 53, 21),
)


def _clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


def _capsule(draw, x0, y0, x1, y1, w, fill):
    """圆头粗线段（PIL 的 line 是平头，这里补两个圆头）。"""
    draw.line([(x0, y0), (x1, y1)], fill=fill, width=w)
    r = w / 2.0
    for px, py in ((x0, y0), (x1, y1)):
        draw.ellipse([px - r, py - r, px + r, py + r], fill=fill)


def _draw_sun(img, cx, cy, r, rays=8, ray_len=0.45):
    """日面：金黄↔朱红网点（中心偏金黄、越靠边越朱红）+ 黑描边；光芒：朱红圆头胶囊。"""
    d = ImageDraw.Draw(img)
    for i in range(rays):
        ang = math.radians(i * 360.0 / rays)
        r0 = r * 1.30
        r1 = r * (1.30 + ray_len)
        _capsule(
            d,
            cx + r0 * math.cos(ang), cy + r0 * math.sin(ang),
            cx + r1 * math.cos(ang), cy + r1 * math.sin(ang),
            max(2, int(r * 0.16)), RED,
        )

    R = int(math.ceil(r))
    pad = 2
    size = 2 * R + 1 + 2 * pad
    ox = int(round(cx)) - R - pad
    oy = int(round(cy)) - R - pad
    edge = 0.985
    ring_w = 1.6 / max(r, 0.001)
    lim = edge + ring_w
    region = Image.new("RGB", (size, size))
    mask = Image.new("L", (size, size))
    px = region.load()
    mp = mask.load()
    for y in range(size):
        row = _BAYER8[y % 8]
        for x in range(size):
            rr = math.hypot(x + ox - cx, y + oy - cy) / max(r, 0.001)
            if rr > lim:
                continue
            mp[x, y] = 255
            if rr > edge:
                px[x, y] = BLACK
                continue
            gold_d = _clamp((0.92 - rr) * 1.12, 0.0, 0.88)
            px[x, y] = RED if gold_d > row[x % 8] / 64.0 else GOLD
    img.paste(region, (ox, oy), mask)


def _draw_cloud(img, cx, cy, w, base=0.10, top=0.50):
    """云：白底黑点网点（上疏下密）+ 黑描边。"""
    x0 = int(math.floor(cx - 0.55 * w))
    y0 = int(math.floor(cy - 0.50 * w))
    x1 = int(math.ceil(cx + 0.55 * w))
    y1 = int(math.ceil(cy + 0.30 * w))
    bw = max(1, x1 - x0)
    bh = max(1, y1 - y0)
    shape = Image.new("L", (bw, bh), 0)
    sd = ImageDraw.Draw(shape)
    for dx, dy, rr in ((-0.28, 0.06, 0.26), (0.06, -0.10, 0.32), (0.30, 0.08, 0.22)):
        sd.ellipse(
            [cx + dx * w - rr * w - x0, cy + dy * w - rr * w - y0,
             cx + dx * w + rr * w - x0, cy + dy * w + rr * w - y0],
            fill=255,
        )
    sd.rectangle(
        [cx - 0.47 * w - x0, cy + 0.02 * w - y0,
         cx + 0.46 * w - x0, cy + 0.26 * w - y0],
        fill=255,
    )
    region = Image.new("RGB", (bw, bh), WHITE)
    px = region.load()
    for y in range(bh):
        row = _BAYER8[y % 8]
        dens = _clamp(base + (top - base) * (y / max(1, bh - 1)), 0.0, 0.62)
        for x in range(bw):
            if dens > row[x % 8] / 64.0:
                px[x, y] = BLACK
    img.paste(region, (x0, y0), shape)
    # 描边：轮廓内缩 1px 求差集
    edge_m = ImageChops.subtract(shape, shape.filter(ImageFilter.MinFilter(3)))
    img.paste(Image.new("RGB", (bw, bh), BLACK), (x0, y0), edge_m)


def _draw_weather_icon(img, code, cx, cy, size):
    """按天气代码画图标：晴=日面，多云=日面+云，阴=云，雨=云+雨丝。"""
    try:
        code = int(code)
    except (TypeError, ValueError):
        code = -1
    if code == 0:
        _draw_sun(img, cx, cy, size * 0.34)
        return
    if code == 1:
        _draw_sun(img, cx - size * 0.12, cy - size * 0.10, size * 0.26, rays=8, ray_len=0.36)
        _draw_cloud(img, cx + size * 0.06, cy + size * 0.12, size * 0.62)
        return
    if code == 2:
        _draw_sun(img, cx - size * 0.18, cy - size * 0.14, size * 0.24, rays=8, ray_len=0.34)
        _draw_cloud(img, cx + size * 0.06, cy + size * 0.10, size * 0.66)
        return
    d = ImageDraw.Draw(img)
    if code in (95, 96, 99):
        _draw_cloud(img, cx, cy - size * 0.06, size * 0.7)
        for dx in (-0.22, 0.0, 0.22):
            _capsule(
                d,
                cx + dx * size, cy + size * 0.24,
                cx + dx * size - size * 0.05, cy + size * 0.42,
                max(1, int(size * 0.06)), RED,
            )
        return
    if 71 <= code <= 77 or code in (85, 86):
        _draw_cloud(img, cx, cy - size * 0.06, size * 0.7)
        for dx in (-0.2, 0.0, 0.2):
            d.ellipse(
                [cx + dx * size - size * 0.04, cy + size * 0.24,
                 cx + dx * size + size * 0.04, cy + size * 0.32],
                fill=BLACK,
            )
        return
    if 51 <= code <= 67 or 80 <= code <= 82:
        _draw_cloud(img, cx, cy - size * 0.08, size * 0.72)
        for dx in (-0.2, 0.02, 0.24):
            _capsule(
                d,
                cx + dx * size, cy + size * 0.22,
                cx + dx * size - size * 0.05, cy + size * 0.40,
                max(1, int(size * 0.055)), RED,
            )
        return
    _draw_cloud(img, cx, cy, size * 0.76)


def _draw_swatch(img, x0, y0, x1, y1):
    """图例色块：金黄→朱红网点渐变（参考图里那个橙红小方块）。"""
    w = max(1, int(x1 - x0))
    h = max(1, int(y1 - y0))
    region = Image.new("RGB", (w, h))
    px = region.load()
    for y in range(h):
        row = _BAYER8[y % 8]
        for x in range(w):
            d = x / max(1, w - 1)
            px[x, y] = RED if d > row[x % 8] / 64.0 else GOLD
    img.paste(region, (int(x0), int(y0)))


def _draw_precip_bar(img, x0, y0, x1, y1, pv):
    """降水概率柱：底色金黄，朱红网点密度随概率升高（= 参考图色阶块）。"""
    w = max(1, int(x1 - x0))
    h = max(1, int(y1 - y0))
    frac = _clamp(float(pv), 0.0, 100.0) / 100.0
    region = Image.new("RGB", (w, h))
    px = region.load()
    for y in range(h):
        row = _BAYER8[y % 8]
        for x in range(w):
            d = (x / max(1, w - 1)) * frac
            px[x, y] = RED if d > row[x % 8] / 64.0 else GOLD
    img.paste(region, (int(x0), int(y0)))


def _draw_seg_digits(draw, text: str, x: int, y: int, digit_w: int, digit_h: int,
                     thickness: int, color=BLACK) -> int:
    """七段数码管画一串数字，返回占用宽度。"""
    t = thickness

    def rect_for(seg: str):
        if seg == "a":
            return (t, 0, digit_w - t, t)
        if seg == "g":
            mid = (digit_h - t) // 2
            return (t, mid, digit_w - t, mid + t)
        if seg == "d":
            return (t, digit_h - t, digit_w - t, digit_h)
        if seg == "f":
            return (0, t // 2, t, (digit_h - t) // 2)
        if seg == "b":
            return (digit_w - t, t // 2, digit_w, (digit_h - t) // 2)
        if seg == "e":
            return (0, digit_h // 2, t, digit_h - t // 2)
        if seg == "c":
            return (digit_w - t, digit_h // 2, digit_w, digit_h - t // 2)
        return None

    cx = x
    for ch in text:
        if ch == " ":
            cx += digit_w // 2
            continue
        if ch == "1":
            # 参考图里的「1」是一根连续的圆头竖条。
            # 走通用 b+c 分支的话两段在中间留 2px 断口，看起来像断开的。
            bw = t + 1
            draw.rounded_rectangle(
                (cx + digit_w - bw, y, cx + digit_w, y + digit_h),
                radius=max(1, bw // 2),
                fill=color,
            )
            cx += digit_w + t
            continue
        lit = _SEGMENTS.get(ch, "")
        for seg in "abcdefg":
            r = rect_for(seg)
            if r is not None and seg in lit:
                draw.rectangle((cx + r[0], y + r[1], cx + r[2], y + r[3]), fill=color)
        cx += digit_w + t
    return cx - x


# ── 整屏绘制 ──────────────────────────────────────────────────────────


def _hour_times(h_times: list[str]) -> list[datetime]:
    out = []
    for t in h_times:
        try:
            out.append(datetime.fromisoformat(str(t)))
        except ValueError:
            out.append(None)
    return out


def _now_index(data: dict, parsed: list[datetime]) -> int:
    """当前小时在 hourly 数组里的下标。"""
    try:
        offset = float(data.get("utc_offset_seconds") or 0)
    except (TypeError, ValueError):
        offset = 0.0
    now_local = datetime.now(timezone.utc) + timedelta(seconds=offset)
    now_local = now_local.replace(tzinfo=None, minute=0, second=0, microsecond=0)
    for i, dt in enumerate(parsed):
        if dt is not None and dt >= now_local:
            return i
    return max(0, len(parsed) - 1)


def _render_page(data: dict, city: str):
    """画整屏天气图，返回 PIL Image。"""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (PAGE_W, PAGE_H), WHITE)
    draw = ImageDraw.Draw(img)

    current = data.get("current") or {}
    hourly = data.get("hourly") or {}
    daily = data.get("daily") or {}

    f_cn = _font("NotoSerifSC-Regular.ttf", 11)
    f_cn_b = _font("NotoSerifSC-Bold.ttf", 12)
    f_num = _font("Inter_24pt-Medium.ttf", 8)
    # 轴标签是中文（「现在」「12时」），Inter 没有中文字形，会回退成方块。
    f_axis = _font("NotoSerifSC-Regular.ttf", 9)
    f_desc = _font("NotoSerifSC-Bold.ttf", 13)
    f_unit = _font("NotoSerifSC-Regular.ttf", 9)

    code = current.get("weather_code")
    temp = current.get("temperature_2m")
    feels = current.get("apparent_temperature")
    humidity = current.get("relative_humidity_2m")

    h_times = list(hourly.get("time") or [])
    h_temp = list(hourly.get("temperature_2m") or [])
    h_prob = list(hourly.get("precipitation_probability") or [])
    parsed = _hour_times(h_times)
    now_i = _now_index(data, parsed)

    # ── 左上：图标 + 七段温度 ─────────────────────────────
    icon_cx, icon_cy = 42.0, 40.0
    if code == 0:
        _draw_sun(img, icon_cx, icon_cy, 24)
    elif code == 1:
        _draw_sun(img, icon_cx - 5, icon_cy - 4, 13, rays=8, ray_len=0.36)
        _draw_cloud(img, icon_cx + 3, icon_cy + 6, 34)
    elif code == 2:
        _draw_sun(img, icon_cx - 7, icon_cy - 6, 13, rays=8, ray_len=0.34)
        _draw_cloud(img, icon_cx + 3, icon_cy + 6, 36)
    else:
        _draw_weather_icon(img, code, icon_cx, icon_cy, 46)

    temp_str = "--"
    try:
        temp_str = f"{int(round(float(temp)))}"
    except (TypeError, ValueError):
        pass
    seg_w, seg_h, seg_t = 24, 36, 4
    digits_x = 94
    used = _draw_seg_digits(draw, temp_str, digits_x, 20, seg_w, seg_h, seg_t, BLACK)
    _text(draw, (digits_x + used + 3, 22), "°C", f_unit, BLACK)

    # ── 实况文字 ───────────────────────────────────────────
    rows = [(_code_desc(code), f_desc)]
    try:
        rows.append((f"体感 {int(round(float(feels)))}°C", f_cn))
    except (TypeError, ValueError):
        pass
    try:
        rows.append((f"湿度 {int(round(float(humidity)))}%", f_cn))
    except (TypeError, ValueError):
        pass
    wtxt = _wind_dir_cn(current.get("wind_direction_10m"))
    wlv = _wind_level_cn(current.get("wind_speed_10m"))
    if wtxt or wlv:
        rows.append((f"{wtxt}{wlv}", f_cn))
    now_prob = 0
    if now_i < len(h_prob):
        try:
            now_prob = int(round(float(h_prob[now_i])))
        except (TypeError, ValueError):
            now_prob = 0
    rows.append((f"降水 {now_prob}%", f_cn))

    ty = 52
    for i, (text, font) in enumerate(rows):
        _text(draw, (84, ty), text, font, BLACK)
        ty += 15 if i == 0 else 11

    # ── 右上：三天预报 ─────────────────────────────────────
    d_times = list(daily.get("time") or [])
    d_codes = list(daily.get("weather_code") or [])
    d_max = list(daily.get("temperature_2m_max") or [])
    d_min = list(daily.get("temperature_2m_min") or [])
    cols = []
    for i in range(len(d_times)):
        try:
            dt = datetime.fromisoformat(str(d_times[i]))
        except ValueError:
            continue
        if i == 0:
            continue  # 今天不进预报行
        if len(cols) >= 3:
            break
        delta = (dt.date() - datetime.now().date()).days
        label = "明天" if delta == 1 else ("后天" if delta == 2 else WEEKDAY_CN[dt.weekday()])
        try:
            c = int(d_codes[i])
        except (TypeError, ValueError):
            c = -1
        try:
            hi = int(round(float(d_max[i])))
            lo = int(round(float(d_min[i])))
        except (TypeError, ValueError):
            hi = lo = None
        cols.append({"label": label, "code": c, "hi": hi, "lo": lo, "desc": _code_desc(c)})

    col_x0, col_w, gap = 250.0, 44.0, 3.0
    for i, col in enumerate(cols):
        cx = col_x0 + i * (col_w + gap)
        ccx = cx + col_w / 2
        _text(draw, (ccx, 5), col["label"], f_cn_b, BLACK, anchor="ma")
        _draw_weather_icon(img, col["code"], ccx, 26, 21)
        if col["hi"] is not None:
            _text(draw, (ccx, 40), f"{col['lo']}~{col['hi']}°C", f_cn, BLACK, anchor="ma")
        _text(draw, (ccx, 56), col["desc"], f_cn, BLACK, anchor="ma")
        if i:
            _dotted_vline(draw, int(cx - gap / 2), 5, 72)

    # ── 分隔线 ─────────────────────────────────────────────
    draw.line([(0, 122), (PAGE_W, 122)], fill=BLACK, width=1)

    # ── 下半屏 ─────────────────────────────────────────────
    chart_l, chart_r = 16.0, 384.0
    n_hours = min(24, max(0, len(h_prob) - now_i))
    col_w2 = (chart_r - chart_l) / 24.0 if n_hours else 0

    # 图例：气温（线+点）与降水概率（色块），靠右
    legend_y = 128
    pl = "降水概率(%)"
    pw = _text_w(draw, pl, f_cn)
    px = chart_r - pw
    _draw_swatch(img, px - 12, legend_y + 1, px - 4, legend_y + 9)
    _text(draw, (px, legend_y), pl, f_cn, BLACK)
    gl = "气温(°C)"
    gw = _text_w(draw, gl, f_cn)
    gx = px - 22 - gw
    draw.line([(gx - 21, legend_y + 5), (gx - 12, legend_y + 5)], fill=BLACK, width=1)
    draw.ellipse([gx - 18, legend_y + 3, gx - 14, legend_y + 7], fill=BLACK)
    _text(draw, (gx, legend_y), gl, f_cn, BLACK)

    # 气温曲线：每三小时一个点，标注数值
    curve_top, curve_bot = 152.0, 208.0
    idxs = [now_i + k * 3 for k in range(9) if now_i + k * 3 < len(h_temp)]
    pts = []
    for idx in idxs:
        try:
            pts.append((idx, float(h_temp[idx])))
        except (TypeError, ValueError):
            continue
    if len(pts) >= 2:
        temps = [p[1] for p in pts]
        tmin, tmax = min(temps), max(temps)
        if tmax - tmin < 1:
            tmax = tmin + 1

        def px_of(idx: int) -> float:
            return chart_l + (idx - now_i + 0.5) * col_w2

        def py_of(tv: float) -> float:
            ratio = (tv - tmin) / (tmax - tmin)
            return curve_bot - ratio * (curve_bot - curve_top)

        coords = [(px_of(i), py_of(t)) for i, t in pts]
        draw.line(coords, fill=BLACK, width=1)
        prev_t = None
        for n, ((i, tv), (cx, cy)) in enumerate(zip(pts, coords)):
            if n == 0:
                # 起点（现在）：空心圈，和后面的实心点区分开
                draw.ellipse([cx - 3, cy - 3, cx + 3, cy + 3], outline=BLACK, width=1)
            else:
                draw.ellipse([cx - 2, cy - 2, cx + 2, cy + 2], fill=BLACK)
            label = f"{int(round(tv))}°"
            above = prev_t is None or tv >= prev_t
            ly = cy - 12 if above else cy + 3
            if ly < 143:
                ly = cy + 3
            _text(draw, (cx, ly), label, f_num, BLACK, anchor="ma")
            prev_t = tv
        # 曲线横轴：现在 + 每三小时的钟点
        axis_y = 212
        _text(draw, (px_of(pts[0][0]), axis_y), "现在", f_axis, BLACK, anchor="ma")
        for i, _t in pts[1:]:
            dt = parsed[i] if i < len(parsed) else None
            if dt is None:
                continue
            _text(draw, (px_of(i), axis_y), dt.strftime("%H时"), f_axis, BLACK, anchor="ma")

    # 逐小时降水概率：金黄柱（从基线往上长）
    base_y = 262.0
    bar_max_h = 16.0
    for k in range(n_hours):
        idx = now_i + k
        try:
            pv = float(h_prob[idx])
        except (TypeError, ValueError):
            continue
        if pv <= 0:
            continue
        cx = chart_l + (k + 0.5) * col_w2
        h = min(bar_max_h, pv / 100.0 * bar_max_h)
        _draw_precip_bar(
            img,
            cx - col_w2 * 0.28, base_y - h, cx + col_w2 * 0.28, base_y, pv,
        )

    # 降水概率数值行
    for k in range(n_hours):
        idx = now_i + k
        try:
            pv = int(round(float(h_prob[idx])))
        except (TypeError, ValueError):
            continue
        cx = chart_l + (k + 0.5) * col_w2
        _text(draw, (cx, 240), str(pv), f_num, BLACK, anchor="ma")

    # 基线 + 小时刻度行
    draw.line([(chart_l, base_y), (chart_r, base_y)], fill=BLACK, width=1)
    for k in range(n_hours):
        cx = chart_l + (k + 0.5) * col_w2
        _text(draw, (cx, 266), str(k), f_num, BLACK, anchor="ma")

    # 左下「现在」与右下日期
    _text(draw, (4, 284), "现在", f_num, BLACK)
    _text(draw, (PAGE_W - 6, 284), datetime.now().strftime("%m-%d"), f_num, BLACK, anchor="ra")

    return img


# ── 对外入口 ──────────────────────────────────────────────────────────


async def build_weather_dashboard_content(fallback=None, **kwargs) -> dict:
    """provider 入口：取数 → 画整屏 → 交给模式 JSON 挂载。"""
    cfg = kwargs.get("config") or {}
    loc = _location(cfg)
    try:
        data = await _fetch(loc)
    except Exception as exc:
        logger.warning("[Weather] Open-Meteo 失败: %s", exc, exc_info=True)
        return dict(fallback or {})

    try:
        img = _render_page(data, loc.get("city") or "")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        payload = buf.getvalue()
    except Exception:
        logger.warning("[Weather] 整屏绘制失败", exc_info=True)
        return dict(fallback or {})

    digest = hashlib.sha1(payload).hexdigest()[:12]
    filename = f"page_{digest}.png"
    try:
        ASSET_DIR.mkdir(parents=True, exist_ok=True)
        (ASSET_DIR / filename).write_bytes(payload)
        _prune_assets(keep=24)
    except OSError:
        logger.warning("[Weather] 天气图写入失败", exc_info=True)
        return dict(fallback or {})

    return {
        "page_url": f"/static/weather/{filename}",
        "has_image": "yes",
        "city": loc.get("city") or "",
    }


def _prune_assets(keep: int = 24) -> None:
    try:
        files = sorted(ASSET_DIR.glob("page_*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return
    for path in files[keep:]:
        try:
            path.unlink()
        except OSError:
            pass
