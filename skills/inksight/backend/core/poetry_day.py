"""「诗笺日课」合并模式（CUSTOM_POETRY_DAY）的内容 provider。

一屏四块：当日主诗（本地诗库） → 宋代掌故（手写考据池） → 个人到家/在外事实
（本地蓝牙落盘） → 双城天气。

主诗不过模型（2026-11-10 冰河大人定的）：诗名与作者是强绑定的事实，模型会把
「山行」配成「李白」（实际是杜牧）。本地诗库的作者本来就是对的，主诗没理由
再问一遍模型。

私人事实三态，全部读本地落盘，不编造：
  1. home —— 今天已回家：到家时刻、在外时长、对应 HOME 诗签、次日双城天气
  2. away —— 今天已出门且尚未回家：出门时刻、截至当前在外时长、对应 AWAY 诗签、出门时双城天气
  3. none —— 今天没有蓝牙事件，或事件配不上诗签：改用本地诗库按季节 + 使用频次
     衰减取诗，不调用 LLM

双城天气有两个来源：
  - 用户在 InkSight 里自选了双城（mode_settings.poetry_home / poetry_away）→ 用自选城市
    自己抓天气（Open-Meteo / 和风兜底），抓不到就退回蓝牙事件里的旧天气文本
  - 没自选 → 沿用蓝牙事件里 bt-presence 抓的天气文本（郑州 / 淄博）

字段前缀隔离：poem_*（主诗）/ lore_*（掌故）/ fact_*（私人事实），
composite 的 result.update() 不会互相覆盖。
"""

from __future__ import annotations

import importlib.util
import logging
import random
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .bt_presence import (
    DEFAULT_BT_DIR,
    MATCH_TOLERANCE_SEC,
    _bt_dir,
    _format_duration,
    _format_weather,
    _load_json,
)

logger = logging.getLogger(__name__)

CORE_DIR = Path(__file__).resolve().parent
# 宋代掌故池的唯一真相源。
# 2026-11-17 宋人日课（独立模式）下线，池子从它的模式定义文件搬到这里 ——
# 诗笺日课仍要用这份池子，不该依赖一个已经下线的模式文件。
SONG_LORE_POOL = CORE_DIR / "data" / "song_lore_pool.json"

_POETRY_LIB: Any = None

# 自选双城在模式覆盖里的存放位置（mode_settings 下的两个槽位）
MODE_ID = "CUSTOM_POETRY_DAY"
POETRY_HOME_KEY = "poetry_home"
POETRY_AWAY_KEY = "poetry_away"
POETRY_CITY_KEYS = (POETRY_HOME_KEY, POETRY_AWAY_KEY)
# 天气缓存 30 分钟，避免每次渲染都打两次天气接口
WEATHER_TTL = 1800


# ── 本地诗库 ────────────────────────────────────────────────
def _poetry_lib_path() -> Path:
    candidate = _bt_dir() / "poetry_lib.py"
    if candidate.exists():
        return candidate
    return DEFAULT_BT_DIR / "poetry_lib.py"


def _load_poetry_lib() -> Any:
    """按文件路径载入 bt-presence 的 poetry_lib（非包，动态加载）。"""
    global _POETRY_LIB
    if _POETRY_LIB is not None:
        return _POETRY_LIB
    path = _poetry_lib_path()
    try:
        spec = importlib.util.spec_from_file_location("bt_poetry_lib", path)
        if spec is None or spec.loader is None:
            raise ImportError(f"无法加载 {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except (OSError, ImportError, AttributeError, ValueError) as exc:
        logger.warning("poetry_day: 载入 poetry_lib 失败: %s", exc)
        return None
    _POETRY_LIB = module
    return module


def _poem_segments(poem: Any, max_clauses: int = 4) -> list[str]:
    """把一首诗切成屏上放得下的几行。

    按逗号断句（山行 → 远上寒山石径斜 / 白云生处有人家 …）。
    断出来超过 max_clauses 句的诗返回空列表 —— 词曲一句太长，硬塞进窄栏会溢出，
    宁可换一首放得下的，也不截断一首诗冒充全诗。
    """
    if not isinstance(poem, str):
        return []
    segments = [s.strip() for s in re.split(r"[，。]", poem) if s.strip()]
    if len(segments) > max_clauses:
        return []
    return segments


def _local_poem(
    month: int | None = None,
    exclude_titles: set[str] | None = None,
    max_clauses: int | None = None,
) -> dict | None:
    """本地诗库取诗：按季节 + 使用频次指数衰减，不经过 LLM。

    exclude_titles：主诗要避开事实块已经用过的那首 —— 一屏不出现两首一样的诗。
    max_clauses：主诗那一块只放得下四行，诗句数超了的不要。
    诗库本来就按权重随机给，多抽几次避开即可，不在诗库之外另造一套挑诗逻辑。
    """
    lib = _load_poetry_lib()
    if lib is None:
        return None
    exclude = {t for t in (exclude_titles or set()) if t}
    last: dict | None = None
    for _ in range(12):
        try:
            candidate = lib.random_poem(month)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            logger.warning("poetry_day: random_poem 失败: %s", exc)
            return None
        if not isinstance(candidate, dict):
            return None
        last = candidate
        if str(candidate.get("title") or "").strip() in exclude:
            continue
        if max_clauses is not None and not _poem_segments(candidate.get("poem"), max_clauses):
            continue
        break
    if last is None:
        return None
    try:
        lib.update_poem_stats(last.get("title", ""), last.get("author", ""))
    except (OSError, ValueError, TypeError):
        logger.warning("poetry_day: 更新诗词统计失败", exc_info=True)
    return last


def build_poetry_day_poem(
    fallback: dict | None = None,
    *,
    exclude_titles: set[str] | None = None,
) -> dict:
    """当日主诗：只从本地诗库取，不问模型。

    诗名与作者是强绑定的事实 —— 模型会把「山行」配成「李白」（实际是杜牧）。
    本地诗库的作者本来就是对的，主诗没有理由再过一遍模型。
    """
    poem = _local_poem(datetime.now().month, exclude_titles, max_clauses=4)
    if poem is None:
        return dict(fallback or {})
    title = str(poem.get("title") or "").strip()
    author = str(poem.get("author") or "").strip()
    lines = _poem_segments(poem.get("poem"), 4)
    if not title or not lines:
        return dict(fallback or {})
    return _merge(fallback, {"poem_title": title, "poem_author": author, "poem_lines": lines})


# ── 蓝牙事件 ────────────────────────────────────────────────
def _today_events(now: datetime | None = None) -> list[tuple[datetime, dict]]:
    """今天的所有蓝牙事件，按时间正序。"""
    data = _load_json(_bt_dir() / "presence_log.json")
    if not isinstance(data, dict):
        return []
    today = (now or datetime.now()).date()
    events: list[tuple[datetime, dict]] = []
    for item in data.get("log", []):
        if not isinstance(item, dict):
            continue
        ts = item.get("ts")
        if not isinstance(ts, (int, float)):
            continue
        try:
            moment = datetime.fromtimestamp(float(ts))
        except (OSError, OverflowError, ValueError):
            continue
        if moment.date() != today:
            continue
        events.append((moment, item))
    events.sort(key=lambda pair: pair[0])
    return events


def _load_signs() -> list[dict]:
    data = _load_json(_bt_dir() / "poetry_signs.json")
    if not isinstance(data, list):
        return []
    return [s for s in data if isinstance(s, dict)]


def _match_sign(event: dict, signs: list[dict], kind: str) -> dict | None:
    """给一次真实事件找对应的诗签（kind: HOME / AWAY）。"""
    event_ts = event.get("ts")
    if not isinstance(event_ts, (int, float)):
        return None
    best: dict | None = None
    best_delta: float | None = None
    for sign in signs:
        if str(sign.get("event", "")).upper() != kind:
            continue
        sign_ts = sign.get("ts")
        if not isinstance(sign_ts, (int, float)):
            continue
        delta = abs(float(sign_ts) - float(event_ts))
        if delta > MATCH_TOLERANCE_SEC:
            continue
        if best_delta is None or delta < best_delta:
            best, best_delta = sign, delta
    return best


def _merge(fallback: dict | None, content: dict) -> dict:
    """只把有值的字段写进结果，缺失的留给兜底。"""
    if not fallback:
        return content
    merged = dict(fallback)
    merged.update({k: v for k, v in content.items() if v not in ("", [], None)})
    return merged


# ── 私人事实 ────────────────────────────────────────────────
def build_poetry_day_facts(fallback: dict | None = None) -> dict:
    """构造当天的私人事实（到家/出门/无记录三态）。"""
    empty: dict[str, Any] = {
        "fact_state": "none",
        "fact_meta": "",
        "fact_duration": "",
        "fact_time": "",
        "fact_poem_title": "",
        "fact_poem_author": "",
        "fact_lines": [],
        "fact_poem_source": "",
        "fact_weather": "",
    }
    now = datetime.now()
    events = _today_events(now)
    signs = _load_signs()

    state = "none"
    meta = f"{now.strftime('%m-%d')} 无记录"
    duration_text = ""
    time_text = ""
    weather_text = ""
    sign: dict | None = None
    title = ""
    author = ""
    lines: list[str] = []
    source = ""

    if events:
        moment, event = events[-1]  # 今天最后一次事件
        kind = str(event.get("event", "")).lower()
        if kind == "home":
            state = "home"
            sign = _match_sign(event, signs, "HOME")
            duration = _format_duration(event.get("absent_minutes"))
            meta = f"{moment.strftime('%m-%d')} 到家"
            duration_text = f"在外 {duration}" if duration else ""
            time_text = moment.strftime("%H:%M")
            weather = _format_weather(event.get("weather_home"))
            weather_text = f"明日 {weather}" if weather else ""
        elif kind == "away":
            state = "away"
            sign = _match_sign(event, signs, "AWAY")
            minutes = int((now - moment).total_seconds() // 60)
            duration = _format_duration(minutes)
            meta = f"{moment.strftime('%m-%d')} 出门"
            duration_text = f"在外 {duration}" if duration else ""
            time_text = moment.strftime("%H:%M")
            weather = _format_weather(event.get("weather_away"))
            weather_text = f"出门时 {weather}" if weather else ""

    if sign is not None:
        title = str(sign.get("title") or "").strip()
        author = str(sign.get("author") or "").strip()
        lines = _poem_segments(sign.get("poem"), 4)
        source = "诗签" if lines else ""

    if not lines:
        # 配不上签（或者今天根本没有事件）：本地诗库取诗，不调用 LLM
        poem = _local_poem(now.month, max_clauses=4)
        if poem is not None:
            title = str(poem.get("title") or "").strip()
            author = str(poem.get("author") or "").strip()
            lines = _poem_segments(poem.get("poem"), 4)
            source = "本地诗笺" if lines else ""

    content: dict[str, Any] = {
        "fact_state": state,
        "fact_meta": meta,
        "fact_duration": duration_text,
        "fact_time": time_text,
        "fact_poem_title": f"《{title}》" if title else "",
        "fact_poem_author": f"〔{author}〕" if author else "",
        "fact_lines": lines,
        "fact_poem_source": source,
        "fact_weather": weather_text,
    }
    return _merge(fallback, content)


# ── 自选双城 ────────────────────────────────────────────────
def _mode_settings(config: Any) -> dict[str, Any]:
    """取有效配置里的 mode_settings（mode_overrides 里的原始覆盖也兜底查一遍）。"""
    cfg = config if isinstance(config, dict) else {}
    settings = cfg.get("mode_settings")
    settings = dict(settings) if isinstance(settings, dict) else {}
    overrides = cfg.get("mode_overrides")
    if isinstance(overrides, dict):
        override = overrides.get(MODE_ID)
        if isinstance(override, dict):
            extra = override.get("mode_settings")
            if isinstance(extra, dict):
                merged = dict(extra)
                merged.update(settings)
                settings = merged
            for key in POETRY_CITY_KEYS:
                if key not in settings and isinstance(override.get(key), dict):
                    settings[key] = override[key]
    return settings


def _configured_cities(config: Any) -> dict[str, dict]:
    """用户自选的两座城市，只保留带城市名的槽位。"""
    settings = _mode_settings(config)
    cities: dict[str, dict] = {}
    for key in POETRY_CITY_KEYS:
        place = settings.get(key)
        if not isinstance(place, dict):
            continue
        city = str(place.get("city") or "").strip()
        if city:
            cities[key] = place
    return cities


def _to_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


async def _fetch_city_weather(place: dict) -> dict | None:
    """抓一座城市的当前天气，抓不到返回 None（调用方退回旧天气文本）。"""
    from .context import _cache_get, _cache_set, get_weather

    city = str(place.get("city") or "").strip()
    if not city:
        return None
    lat = _to_float(place.get("latitude"))
    lon = _to_float(place.get("longitude"))
    cache_key = f"poetry_city_weather:{city}:{lat}:{lon}"
    cached = _cache_get(cache_key, WEATHER_TTL)
    if isinstance(cached, dict):
        return cached
    try:
        data = await get_weather(lat=lat, lon=lon, city=city)
    except Exception as exc:  # 网络/解析异常都不该拖垮整屏
        logger.warning("poetry_day: 取 %s 天气失败: %s", city, exc)
        return None
    if str(data.get("weather_str") or "").strip() == "--°C":
        return None
    temp = data.get("temp")
    if not isinstance(temp, (int, float)):
        return None
    result = {"city": city, "temp": round(float(temp))}
    _cache_set(cache_key, result)
    return result


def _format_city_weather(items: list[dict]) -> str:
    """['郑州', 14] -> '郑州 14° / 淄博 12°'（与蓝牙事件里的旧格式一致）。"""
    parts: list[str] = []
    for item in items:
        city = str(item.get("city") or "").strip()
        temp = item.get("temp")
        if not city or not isinstance(temp, (int, float)):
            continue
        parts.append(f"{city} {round(float(temp))}°")
    return " / ".join(parts)


async def build_poetry_day_content(fallback: dict | None = None, **kwargs) -> dict:
    """诗笺日课的事实 + 自选双城天气。

    自选了双城就用自选城市抓天气（抓不到退回蓝牙事件里的旧文本）；
    没自选就完全沿用蓝牙事件里的天气文本。

    当日主诗也从本地诗库出，避开事实块已经用过的那首。
    """
    facts = build_poetry_day_facts(fallback)

    exclude: set[str] = set()
    fact_title = str(facts.get("fact_poem_title") or "").strip()
    if fact_title.startswith("《") and fact_title.endswith("》"):
        exclude.add(fact_title[1:-1])
    poem = build_poetry_day_poem(fallback, exclude_titles=exclude)
    facts.update(
        {k: v for k, v in poem.items() if k.startswith("poem_") and v not in ("", [], None)}
    )

    cities = _configured_cities(kwargs.get("config"))
    if not cities:
        return facts
    fetched: list[dict] = []
    for key in POETRY_CITY_KEYS:
        place = cities.get(key)
        if not place:
            continue
        weather = await _fetch_city_weather(place)
        if weather:
            fetched.append(weather)
    text = _format_city_weather(fetched)
    if not text:
        return facts
    state = facts.get("fact_state")
    prefix = "明日 " if state == "home" else ("出门时 " if state == "away" else "")
    facts["fact_weather"] = f"{prefix}{text}"
    return facts


# ── 宋代掌故 ────────────────────────────────────────────────
def build_song_lore(fallback: dict | None = None) -> dict:
    """从宋代掌故池里取一条。"""
    empty: dict[str, Any] = {
        "lore_title": "",
        "lore_era": "",
        "lore_lines": [],
        "lore_note": "",
    }
    data = _load_json(SONG_LORE_POOL)
    pool: list[dict] = []
    if isinstance(data, dict):
        raw = data.get("pool")
        if isinstance(raw, list):
            pool = [e for e in raw if isinstance(e, dict)]
    if not pool:
        logger.info("poetry_day: 宋人日课考据池为空")
        return _merge(fallback, empty)

    entry = random.choice(pool)
    result: dict[str, Any] = {
        "lore_title": str(entry.get("title") or "").strip(),
        "lore_era": str(entry.get("era") or "").strip(),
        "lore_lines": [str(x).strip() for x in (entry.get("lines") or []) if str(x).strip()],
        "lore_note": str(entry.get("note") or "").strip(),
    }
    return _merge(fallback, result)
