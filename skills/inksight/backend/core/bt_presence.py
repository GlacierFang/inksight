"""回家签内容 provider（computed: home_sign）。

数据源（全部本地落盘，不经 LLM）：
  - skills/bt-presence/poetry_signs.json  诗词签元数据（诗库选句 + 生图时的场景描述）
  - skills/bt-presence/presence_log.json  到家/离家事件（含 absent_minutes、weather_home）

规则：
  - 只认 HOME 签，且该签必须能配到 presence_log 里的真实 home 事件
    （两边 ts 都是「事件发生时刻」，容差内即视为同一次到家）。
  - 多个候选时取最近一次（到家时间倒序）。
  - 诗句按中文句号切分，与 poetry_sign.py 画飞书诗签时的分行规则一致。
  - 天气取该次到家事件里的 weather_home（bt_presence.py 抓的是「次日」天气）。
  - 配不到任何真实到家事件时返回空内容，不编造、不兜底。
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# backend/core/bt_presence.py -> backend -> inksight -> skills -> workspace
SKILLS_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_BT_DIR = SKILLS_ROOT / "bt-presence"

# 诗签 ts 与到家事件 ts 的配对容差（秒）。诗签 ts 写的是事件发生时刻，
# 但落盘顺序是「先记日志、后写队列」，留 120s 余量。
MATCH_TOLERANCE_SEC = 120

_TEMP_RE = re.compile(r"([+-]?\d+)\s*°?\s*C")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _bt_dir() -> Path:
    override = os.environ.get("INKSIGHT_BT_PRESENCE_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return DEFAULT_BT_DIR


def _load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        logger.warning("home_sign: 读取 %s 失败: %s", path, exc)
        return None


def _format_duration(minutes: Any) -> str:
    """把 absent_minutes 格式化成「12小时29分」。"""
    try:
        mins = int(minutes)
    except (TypeError, ValueError):
        return ""
    if mins <= 0:
        return ""
    hours, rest = divmod(mins, 60)
    if hours and rest:
        return f"{hours}小时{rest}分"
    if hours:
        return f"{hours}小时"
    return f"{rest}分"


def _format_weather(raw: Any) -> str:
    """'郑州: ☁️  +14°C / 淄博: ☁️  +12°C' -> '郑州 14° / 淄博 12°'"""
    if not isinstance(raw, str) or not raw.strip():
        return ""
    parts: list[str] = []
    for chunk in raw.split("/"):
        chunk = chunk.strip()
        if not chunk:
            continue
        city, _, tail = chunk.partition(":")
        temp_match = _TEMP_RE.search(tail)
        if not temp_match:
            continue
        temp = temp_match.group(1)
        if temp.startswith("+"):
            temp = temp[1:]
        city = city.strip()
        parts.append(f"{city} {temp}°" if city else f"{temp}°")
    return " / ".join(parts)


def _poem_lines(poem: Any) -> list[str]:
    """按中文句号切分诗句，保留句号（与 poetry_sign.py 画诗签一致）。"""
    if not isinstance(poem, str):
        return []
    lines: list[str] = []
    for seg in poem.split("。"):
        seg = seg.strip()
        if seg:
            lines.append(f"{seg}。")
    return lines


def _match_sign(event: dict, signs: list[dict]) -> dict | None:
    """给一次真实到家事件找对应的 HOME 诗签。"""
    event_ts = event.get("ts")
    if not isinstance(event_ts, (int, float)):
        return None
    best: dict | None = None
    best_delta = None
    for sign in signs:
        if str(sign.get("event", "")).upper() != "HOME":
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


def _collect_candidates() -> list[tuple[dict, dict]]:
    """返回 [(到家事件, 对应诗签)]，按到家时间倒序。"""
    bt_dir = _bt_dir()
    log_data = _load_json(bt_dir / "presence_log.json")
    signs_data = _load_json(bt_dir / "poetry_signs.json")
    if not isinstance(log_data, dict) or not isinstance(signs_data, list):
        return []

    events = [e for e in log_data.get("log", []) if isinstance(e, dict) and e.get("event") == "home"]
    signs = [s for s in signs_data if isinstance(s, dict)]

    pairs: list[tuple[dict, dict]] = []
    for event in events:
        sign = _match_sign(event, signs)
        if sign is not None:
            pairs.append((event, sign))
    pairs.sort(key=lambda pair: float(pair[0].get("ts") or 0), reverse=True)
    return pairs


def build_home_sign_content(fallback: dict | None = None) -> dict:
    """构造回家签内容。配不到真实到家事件时返回空内容。"""
    empty: dict[str, Any] = {
        "arrival_meta": "",
        "arrival_time": "",
        "away_text": "",
        "poem_title": "",
        "poem_author": "",
        "lines": [],
        "note": "",
    }

    pairs = _collect_candidates()
    if not pairs:
        logger.info("home_sign: 没有可配对的到家事件与诗签")
        return empty

    event, sign = pairs[0]
    try:
        arrived = datetime.fromtimestamp(float(event.get("ts")))
    except (TypeError, ValueError, OSError):
        return empty

    title = str(sign.get("title") or "").strip()
    author = str(sign.get("author") or "").strip()
    lines = _poem_lines(sign.get("poem"))
    if not title and not lines:
        return empty

    away_text = _format_duration(event.get("absent_minutes"))
    weather = _format_weather(event.get("weather_home"))
    tomorrow = (arrived + timedelta(days=1)).strftime("%m-%d")

    content: dict[str, Any] = {
        "arrival_meta": f"{arrived.strftime('%m-%d')} 到家",
        "arrival_time": arrived.strftime("%H:%M"),
        "away_text": f"在外 {away_text}" if away_text else "",
        "poem_title": f"《{title}》" if title else "",
        "poem_author": f"〔{author}〕" if author else "",
        "lines": lines,
        "note": f"{tomorrow} {weather}" if weather else "",
    }

    if fallback:
        merged = dict(fallback)
        merged.update({k: v for k, v in content.items() if v not in ("", [], None)})
        return merged
    return content
