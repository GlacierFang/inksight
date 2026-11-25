"""
天气链路服务端模块。

职责：
1. 后台定时抓取天气 → 原子写 `storage/cache/weather.json`
   结构：{"weather": {"temp": ..., "text": ...}, "city": "...", "updated_at": <unix>, "ts": <unix>}
   —— `ts` 与 `updated_at` 是同一个 unix 秒，兼容 `main.py::_weather_snapshot()` 既有读法。
2. 城市配置的读写：权威源 `config.yaml` 的 `weather` 段 + `storage/settings.json`（设备上报覆盖）。

铁律：
- 抓取失败**不抛错**：保留旧缓存、跳过本轮、下一轮重试；主服务不受影响。
- 写盘走 `app.storage.atomic_write_json`（.tmp → flush → fsync → os.replace）。
- 错误文案不出现 AES/HTTP/signature/Token/Python/Traceback 等技术术语。
"""

from __future__ import annotations

import asyncio
import os
import ssl
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import httpx

from app.config import settings
from app.storage import atomic_write_json, get_storage, read_json

DEFAULT_CITY = "Beijing"
DEFAULT_TTL = 7200          # 2 小时，与 bootstrap 侧的缓存 TTL 对齐
POLL_INTERVAL = 30          # 后台轮询检查间隔（秒）——只做检查，不阻塞
FETCH_TIMEOUT = 12.0
ALLOWED_SOURCES = ("open-meteo", "wttr")

# 天气现象（WMO 代码）→ 中文短语。取自 Open-Meteo 的 weather_code。
WMO_TEXT: Dict[int, str] = {
    0: "晴", 1: "晴间多云", 2: "多云", 3: "阴",
    45: "雾", 48: "雾凇",
    51: "小雨", 53: "毛毛雨", 55: "细雨",
    56: "冻雨", 57: "冻雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
    80: "阵雨", 81: "强阵雨", 82: "暴雨",
    85: "阵雪", 86: "强阵雪",
    95: "雷阵雨", 96: "雷阵雨伴冰雹", 99: "雷阵雨伴冰雹",
}

# wttr.in 返回英文描述 → 中文短语（覆盖常见情形，未命中则原样透出英文）
EN_TEXT: Dict[str, str] = {
    "sunny": "晴", "clear": "晴", "partly cloudy": "晴间多云",
    "cloudy": "多云", "overcast": "阴", "mist": "雾", "fog": "雾",
    "light rain": "小雨", "light rain shower": "阵雨",
    "moderate rain": "中雨", "moderate rain at times": "中雨",
    "heavy rain": "大雨", "heavy rain at times": "大雨",
    "patchy rain possible": "有零星降雨", "patchy rain nearby": "有零星降雨",
    "light snow": "小雪", "moderate snow": "中雪", "heavy snow": "大雪",
    "patchy snow possible": "有零星降雪", "light sleet": "小雨夹雪",
    "thundery outbreaks possible": "可能有雷阵雨",
    "thunderstorm": "雷阵雨", "blizzard": "暴雪", "haze": "霾",
}


# ================================================================ 配置读取
def weather_config() -> Dict[str, Any]:
    """`config.yaml` 的 `weather` 段（热配置，缺字段走默认值）。"""
    try:
        raw = settings.weather or {}
    except Exception:
        raw = {}
    return raw if isinstance(raw, dict) else {}


def config_city() -> str:
    city = str(weather_config().get("city") or "").strip()
    return city or DEFAULT_CITY


def config_ttl() -> int:
    try:
        ttl = int(weather_config().get("ttl_seconds") or DEFAULT_TTL)
    except (TypeError, ValueError):
        ttl = DEFAULT_TTL
    return ttl if ttl > 60 else DEFAULT_TTL


def config_sources() -> list:
    """抓取源开关（白名单内才启用）。"""
    raw = weather_config().get("sources")
    if raw is None:
        return list(ALLOWED_SOURCES)
    if isinstance(raw, str):
        raw = [raw]
    picked = [str(s).strip().lower() for s in raw if str(s).strip()]
    picked = [s for s in picked if s in ALLOWED_SOURCES]
    return picked or list(ALLOWED_SOURCES)


def config_coords() -> Tuple[Optional[float], Optional[float]]:
    """可选经纬度；给了就用，不再做地名解析。"""
    lat = weather_config().get("latitude")
    lon = weather_config().get("longitude")
    try:
        lat = float(lat) if lat not in (None, "") else None
    except (TypeError, ValueError):
        lat = None
    try:
        lon = float(lon) if lon not in (None, "") else None
    except (TypeError, ValueError):
        lon = None
    if lat is None or lon is None:
        return None, None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None, None
    return lat, lon


# ================================================================ 城市设置
def read_settings() -> Dict[str, Any]:
    data = read_json(get_storage().settings_file, None)
    return data if isinstance(data, dict) else {}


def write_settings(data: Dict[str, Any]) -> bool:
    return atomic_write_json(get_storage().settings_file, data)


def set_city(city: str, device_id: str = "") -> bool:
    """设备上报城市 → 写 `storage/settings.json`（权威源，不碰 .env / 不碰 config.yaml）。"""
    city = (city or "").strip()
    if not city:
        return False
    data = read_settings()
    data["city"] = city
    data["updated_at"] = int(time.time())
    devices = data.get("device_cities")
    if not isinstance(devices, dict):
        devices = {}
    if device_id:
        devices[device_id] = city
    data["device_cities"] = devices
    return write_settings(data)


def resolve_city(device_id: Optional[str] = None) -> str:
    """城市解析优先级：设备上报 > 最近一次上报 > config.yaml > 默认值。"""
    data = read_settings()
    devices = data.get("device_cities")
    if device_id and isinstance(devices, dict):
        own = str(devices.get(device_id) or "").strip()
        if own:
            return own
    shared = str(data.get("city") or "").strip()
    if shared:
        return shared
    return config_city()


# ================================================================ 缓存读写
def read_cache() -> Optional[Dict[str, Any]]:
    data = read_json(get_storage().weather_file, None)
    return data if isinstance(data, dict) else None


def write_cache(weather: Dict[str, Any], city: str) -> bool:
    """原子写缓存：结构与 `_weather_snapshot()` 既有读法兼容。"""
    now = int(time.time())
    payload = {
        "weather": {"temp": weather.get("temp"), "text": weather.get("text")},
        "city": city,
        "updated_at": now,
        "ts": now,          # 兼容既有读法（同一 unix 秒）
    }
    path = get_storage().weather_file
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return atomic_write_json(path, payload)


def read_weather_snapshot(device_id: Optional[str] = None) -> Dict[str, Any]:
    """
    给 bootstrap 用的天气快照。缓存缺失 / 损坏 / 城市对不上 → 返回空对象。
    绝不抛错、绝不阻塞。
    """
    try:
        data = read_cache()
        if not data:
            return {}
        cached = data.get("weather")
        if not isinstance(cached, dict) or not cached:
            return {}
        cached_city = str(data.get("city") or "").strip()
        if cached_city:
            want = resolve_city(device_id)
            if want and cached_city.lower() != want.lower():
                # 缓存里是别的城市的天气，别拿它糊弄设备
                return {}
        return {"temp": cached.get("temp"), "text": cached.get("text")}
    except Exception as exc:
        print(f"[weather] 读取天气缓存失败：{exc}")
        return {}


# ================================================================ 网络抓取
def _proxy_ssl_context() -> Optional[ssl.SSLContext]:
    """出口代理的信任包（SSL_CERT_FILE）；没有就返回 None。"""
    bundle = os.environ.get("SSL_CERT_FILE", "")
    if not bundle:
        return None
    path = Path(bundle)
    if not path.is_file():
        return None
    try:
        return ssl.create_default_context(cafile=str(path))
    except Exception:
        return None


async def _get_json(url: str, params: Optional[Dict[str, Any]] = None) -> Optional[Any]:
    """
    取 JSON。两条网络路径依次尝试：
      1. 走出口代理（本机出网默认路径）
      2. 直连（代理挡域名时兜底）
    都不通返回 None —— 不抛错，不影响主服务。
    """
    attempts = []
    ctx = _proxy_ssl_context()
    if ctx is not None:
        attempts.append({"trust_env": True, "verify": ctx})
    attempts.append({"trust_env": False, "verify": True})

    for opt in attempts:
        try:
            async with httpx.AsyncClient(
                timeout=FETCH_TIMEOUT,
                follow_redirects=True,
                trust_env=opt["trust_env"],
                verify=opt["verify"],
            ) as client:
                resp = await client.get(url, params=params)
                if resp.status_code >= 400:
                    continue
                return resp.json()
        except Exception:
            continue
    return None


async def _geocode(city: str) -> Optional[Tuple[float, float]]:
    """城市名 → 经纬度（Open-Meteo 地理编码）。失败返回 None。"""
    data = await _get_json(
        "https://geocoding-api.open-meteo.com/v1/search",
        {"name": city, "count": 1, "language": "zh", "format": "json"},
    )
    if not isinstance(data, dict):
        return None
    results = data.get("results")
    if not isinstance(results, list) or not results:
        return None
    try:
        return float(results[0]["latitude"]), float(results[0]["longitude"])
    except (KeyError, TypeError, ValueError):
        return None


async def _fetch_open_meteo(city: str) -> Optional[Dict[str, Any]]:
    lat, lon = config_coords()
    if lat is None or lon is None:
        coords = await _geocode(city)
        if coords is None:
            return None
        lat, lon = coords
    data = await _get_json(
        "https://api.open-meteo.com/v1/forecast",
        {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,weather_code",
            "timezone": "auto",
        },
    )
    if not isinstance(data, dict):
        return None
    current = data.get("current")
    if not isinstance(current, dict):
        return None
    try:
        temp = round(float(current["temperature_2m"]))
    except (KeyError, TypeError, ValueError):
        return None
    try:
        code = int(current.get("weather_code"))
    except (TypeError, ValueError):
        code = -1
    return {"temp": temp, "text": WMO_TEXT.get(code, "未知天气")}


async def _fetch_wttr(city: str) -> Optional[Dict[str, Any]]:
    quoted = urllib.parse.quote(city.strip())
    if not quoted:
        return None
    data = await _get_json(f"https://wttr.in/{quoted}", {"format": "j1"})
    if not isinstance(data, dict):
        return None
    current = data.get("current_condition")
    if not isinstance(current, list) or not current:
        return None
    item = current[0]
    try:
        temp = round(float(item["temp_C"]))
    except (KeyError, TypeError, ValueError):
        return None
    text = ""
    desc = item.get("weatherDesc")
    if isinstance(desc, list) and desc:
        raw = str((desc[0] or {}).get("value") or "").strip()
        text = EN_TEXT.get(raw.lower(), raw)
    return {"temp": temp, "text": text or "未知天气"}


async def fetch_weather(city: str) -> Optional[Dict[str, Any]]:
    """按配置里的源开关依次尝试；全失败返回 None。"""
    city = (city or "").strip()
    if not city:
        return None
    for source in config_sources():
        try:
            if source == "open-meteo":
                got = await _fetch_open_meteo(city)
            elif source == "wttr":
                got = await _fetch_wttr(city)
            else:
                got = None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[weather] 抓取失败（{source}）：{exc}")
            got = None
        if got and got.get("text"):
            return got
    return None


# ================================================================ 后台循环
def cache_is_fresh(city: str) -> bool:
    """缓存是否新鲜（城市一致 + 未过 TTL）。"""
    data = read_cache()
    if not data:
        return False
    cached_city = str(data.get("city") or "").strip()
    if cached_city and city and cached_city.lower() != city.lower():
        return False
    ts = int(data.get("ts") or data.get("updated_at") or 0)
    if ts <= 0:
        return False
    return (time.time() - ts) <= config_ttl()


async def weather_loop() -> None:
    """
    后台定时抓取循环。用 `asyncio.sleep`，不阻塞事件循环。
    每轮：城市变了或缓存过期 → 抓一次 → 原子写盘；失败保留旧缓存，下一轮再试。
    """
    print("[weather] 后台天气任务启动")
    while True:
        try:
            city = resolve_city()
            if not cache_is_fresh(city):
                got = await fetch_weather(city)
                if got:
                    if write_cache(got, city):
                        print(f"[weather] 已更新天气：{city} {got.get('text')} {got.get('temp')}°C")
                else:
                    print(f"[weather] 暂时拿不到天气，稍后再试（城市：{city}）")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[weather] 天气任务异常：{exc}")
        await asyncio.sleep(POLL_INTERVAL)


def start_background_task(app) -> "asyncio.Task":
    """在 FastAPI lifespan 里挂起后台天气任务，返回 task（供取消）。"""
    task = asyncio.create_task(weather_loop())
    app.state.weather_task = task
    return task


async def stop_background_task(app) -> None:
    task = getattr(app.state, "weather_task", None)
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:
        pass
    app.state.weather_task = None
