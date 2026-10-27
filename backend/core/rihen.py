"""日痕 provider：一天一行，屏幕是一册慢慢写满的日志。

每天 05:00 由 systemd timer 生成一条不超过 30 字的短句，写进 runtime_cache/rihen/state.json。
设备每次刷新都是整图推送，服务端按屏幕尺寸取最近 N 行重新合成到画布上——
旧字不丢，只是滚出屏幕；全部记录永久留在服务端，可用 scripts/rihen_history.py 读回。

一册 = VOLUME_DAYS 天（默认 30 天，一月一册）。册满即归档（archived），册号 +1。
册将满（剩 6 天以内）时，页面末尾落一方朱红「第 N 册」印。

原则：
- 一天只生成一次（文件锁 + 按日期幂等），设备刷多少次看到的都是同一行。
- 模型失灵不抛错，退到手写兜底短句，当天不空。
- 不生图：纯文字，一天一次 LLM 调用，几乎不花钱，也不费墨。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_BACKEND_DIR = Path(__file__).resolve().parent.parent
STATE_DIR = _BACKEND_DIR / "runtime_cache" / "rihen"
STATE_FILE = STATE_DIR / "state.json"
LOCK_FILE = STATE_DIR / "generate.lock"

try:  # 定时任务独立跑时也要能读到凭证
    from dotenv import load_dotenv as _load_dotenv

    _load_dotenv(_BACKEND_DIR / ".env")
except Exception:  # pragma: no cover
    pass


# ── 常量 ──────────────────────────────────────────────────────────

VOLUME_DAYS = 30          # 一册多少天
SEAL_THRESHOLD = 6        # 册内剩余不超过这么多天时，页面落「第 N 册」印
# 一行最多多少字。bitmap 字体引擎会把 9-12px 的请求统一落到 12px 位图，
# 12px × 28 字 = 336px，加上行首日期和朱红点刚好放得进 400px 屏；
# 30 字（360px）就挤不下了，所以取 28。
MAX_CHARS = 28
LOCK_STALE_SEC = 900
RECENT_KEEP = 7           # 生成时给模型看最近几天（防重复）
LLM_MODELS = ("MiniMax-M3.1-Flash", "MiniMax-M2.7")

# 屏幕版式档位：必须与 modes-ours/rihen.json 的 layout / layout_overrides 对齐。
# 键是精确的屏幕尺寸（与 layout_overrides 的 key 一致），其余尺寸走 default。
PROFILES = {
    "default": {
        "text_font_size": 12,
        "date_font_size": 8,
        "dot_font_size": 8,
        "line_height": 16,
        "row_gap": 7,
        "column_gap": 6,
        "padding_x": 10,
        "padding_y": 4,
        "seal_font_size": 12,
        "seal_line_height": 16,
        "show_date": True,
    },
    "296x128": {
        "text_font_size": 9,
        "date_font_size": 9,
        "dot_font_size": 9,
        "line_height": 12,
        "row_gap": 3,
        "column_gap": 3,
        "padding_x": 4,
        "padding_y": 2,
        "seal_font_size": 9,
        "seal_line_height": 12,
        "show_date": False,
    },
    "648x480": {
        "text_font_size": 14,
        "date_font_size": 10,
        "dot_font_size": 10,
        "line_height": 20,
        "row_gap": 7,
        "column_gap": 6,
        "padding_x": 16,
        "padding_y": 6,
        "seal_font_size": 14,
        "seal_line_height": 20,
        "show_date": True,
    },
}


# ── 手写兜底：模型挂掉时，当天仍有一行字 ─────────────────────────

FALLBACK_LINES = (
    "今天把该做的事做完了，剩下的是别人的时间。",
    "雨下了一整天，屋里很静。",
    "早起的路上遇见一只猫，它没躲。",
    "把书桌清了一遍，桌面空出来。",
    "煮了一壶茶，第二泡最好喝。",
    "楼下的桂花开了，香气压过车声。",
    "修好了一个拖了很久的小毛病。",
    "今天什么也没赶，睡到自然醒。",
    "给家里打了个电话，聊了十几分钟。",
    "写了几行字，删掉一半，留下一半。",
    "路过河边，风把水面吹出皱。",
    "把旧照片翻了一遍，停在某一张上。",
    "今天走了一万步，脚底有点热。",
    "整理了书架，扔掉三本不再读的书。",
    "天阴了一整天，像要落雨又没落。",
    "把冬天的被子抱出来晒了晒。",
    "同事讲了个笑话，我没听懂但跟着笑了。",
    "晚上煮面，多放了一勺醋。",
    "把三年的旧照片洗了出来。",
    "今天开了三个会，只记住一件事。",
    "窗台的多肉冒了新芽，很小的一点。",
    "去菜市场买了把青菜，比昨天便宜。",
    "看完了一部老电影，片尾曲还在耳朵里。",
    "睡得很沉，梦里好像回了趟老家。",
    "今天风大，围巾一直往上飘。",
    "把手头的一件事做完了，很轻。",
    "在旧书里夹了一张纸条，字已经淡了。",
    "收拾出一箱旧衣服，叠得整整齐齐。",
    "今天是平平常常的一天，没有坏事。",
    "换了新床单，躺下就不想起。",
)


PROMPT = """你在替一个人写「日痕」——他每天在屏幕上留一行字，像往石头上刻一道痕。

规则：
- 只写一句话，不超过 {max_chars} 个字，写在一行里，不换行。
- 第一人称，写今天真实发生的一件小事或一个感受，具体、能落地。
- 不要写标题，不要写「今日记」「随笔」这类词，不要加引号，不要结尾标点。
- 可以有一个逗号。语气克制、安静，像随手记在纸上的一行字。
- 不要重复最近几天写过的意象。
- 只输出这一句话，不要解释。

最近的痕迹：
{recent}

今天：{today}"""


# ── 状态读写 ──────────────────────────────────────────────────────


def _read_state() -> dict:
    try:
        with STATE_FILE.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(data: dict) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_name(STATE_FILE.name + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        tmp.replace(STATE_FILE)
    except OSError:
        logger.warning("[Rihen] 写状态失败", exc_info=True)


def _days(state: dict) -> list:
    raw = state.get("days")
    if not isinstance(raw, list):
        return []
    return [d for d in raw if isinstance(d, dict) and d.get("date")]


def _find_day(state: dict, date_key: str) -> dict | None:
    for day in _days(state):
        if str(day.get("date")) == date_key:
            return day
    return None


def _archived(state: dict) -> list:
    raw = state.get("archived")
    return [a for a in raw if isinstance(a, dict)] if isinstance(raw, list) else []


def _current_volume(state: dict) -> int:
    try:
        return max(1, int(state.get("volume") or 1))
    except (TypeError, ValueError):
        return 1


def _volume_days(state: dict, volume: int) -> list:
    return [d for d in _days(state) if int(d.get("volume") or 1) == volume]


# ── 并发锁：一天只写一行 ──────────────────────────────────────────


def _lock_acquire() -> bool:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    for _ in range(2):
        try:
            fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            try:
                age = time.time() - LOCK_FILE.stat().st_mtime
            except OSError:
                age = 0.0
            if age < LOCK_STALE_SEC:
                return False
            try:
                LOCK_FILE.unlink()
            except OSError:
                return False
            continue
        except OSError:
            return False
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(f"{os.getpid()} {time.time():.0f}")
        except OSError:
            pass
        return True
    return False


def _lock_release() -> None:
    try:
        LOCK_FILE.unlink()
    except OSError:
        pass


# ── 解析与校验 ────────────────────────────────────────────────────

_STRIP_RE = re.compile(r"^[\s　]+|[\s　]+$")
_QUOTE_RE = re.compile(r"^[\"'“”‘’「」『』《》\[\(（【]+|[\"'“”‘’「」『』《》\[\]）)】]+$")
_CJK_RE = re.compile(r"[一-鿿]")


def _clean_line(raw: str) -> str:
    """把模型输出收拾成一行干净的话。"""
    for line in (raw or "").splitlines():
        line = _STRIP_RE.sub("", line)
        if not line:
            continue
        line = _QUOTE_RE.sub("", line)
        line = line.replace("**", "").replace("##", "")
        line = _STRIP_RE.sub("", line)
        if len(line) < 4:
            continue
        if not _CJK_RE.search(line):
            continue
        return line
    return ""


def _trim_to(line: str, limit: int = MAX_CHARS) -> str:
    """超长时优先在标点处收尾，收不了再硬截。"""
    if len(line) <= limit:
        return line
    window = line[: limit + 1]
    cut = max(window.rfind("，"), window.rfind("、"), window.rfind(","))
    if cut >= limit - 8:
        return window[:cut]
    return window[:limit].rstrip("，,、")


def _fallback_line(date_key: str) -> str:
    seed = hashlib.sha256(f"rihen-fallback|{date_key}".encode("utf-8")).hexdigest()
    return FALLBACK_LINES[int(seed[:8], 16) % len(FALLBACK_LINES)]


# ── LLM ───────────────────────────────────────────────────────────


async def _call(prompt: str, temperature: float, **kwargs) -> str:
    from .content import _call_llm

    provider = str(kwargs.get("llm_provider") or "").strip() or "minimax"
    model = str(kwargs.get("llm_model") or "").strip()
    models = (model,) if model else LLM_MODELS
    raw = ""
    for model_name in models:
        for attempt in range(2):
            try:
                raw = await _call_llm(
                    provider,
                    model_name,
                    prompt,
                    temperature=temperature,
                    max_tokens=12000,
                    api_key=kwargs.get("api_key"),
                    base_url=kwargs.get("llm_base_url"),
                )
            except Exception as exc:
                logger.warning("[Rihen] LLM 调用失败(%s 第 %s 次): %s", model_name, attempt + 1, exc)
                raw = ""
            if raw and raw.strip():
                break
        if raw and raw.strip():
            break
    return raw or ""


async def _llm_line(date_key: str, recent: list, **kwargs) -> str:
    try:
        weekday = "一二三四五六日"[datetime.strptime(date_key, "%Y-%m-%d").weekday()]
    except (TypeError, ValueError):
        weekday = ""
    recent_txt = "\n".join(f"- {r.get('date')} {r.get('text')}" for r in recent) or "（此前没有记录）"
    prompt = PROMPT.format(max_chars=MAX_CHARS, recent=recent_txt, today=f"{date_key} 星期{weekday}")
    raw = await _call(prompt, 0.9, **kwargs)
    line = _clean_line(raw)
    if not line:
        logger.warning("[Rihen] 解析失败: %r", (raw or "")[:120])
        return ""
    if len(line) > MAX_CHARS:
        line = _trim_to(line)
    return line


# ── 生成 ──────────────────────────────────────────────────────────


def _append_day(state: dict, date_key: str, text: str) -> dict:
    """把一天写进状态；册满则归档并开新册。"""
    volume = _current_volume(state)
    current = _volume_days(state, volume)
    if len(current) >= VOLUME_DAYS:
        # 这一册满了：归档，另起一册
        archived = _archived(state)
        archived.append({
            "volume": volume,
            "from": str(current[0].get("date")) if current else "",
            "to": str(current[-1].get("date")) if current else "",
            "days": [{"date": d.get("date"), "text": d.get("text")} for d in current],
        })
        state["archived"] = archived
        state["days"] = [d for d in _days(state) if int(d.get("volume") or 1) != volume]
        volume += 1
        state["volume"] = volume

    days = _days(state)
    days.append({
        "date": date_key,
        "text": text,
        "volume": volume,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    days.sort(key=lambda d: str(d.get("date", "")))
    state["days"] = days
    state["version"] = 1
    return state


async def _generate_once(date_key: str, force: bool, **kwargs) -> str | None:
    if not _lock_acquire():
        # 别的进程正在写：等它写完
        deadline = time.time() + 120
        while time.time() < deadline:
            await asyncio.sleep(2)
            state = _read_state()
            day = _find_day(state, date_key)
            if day:
                return str(day.get("text") or "")
            if not LOCK_FILE.exists():
                break
        return None

    try:
        state = _read_state()
        existing = _find_day(state, date_key)
        if existing and not force:
            return str(existing.get("text") or "")

        recent = [d for d in _days(state) if str(d.get("date")) != date_key][-RECENT_KEEP:]
        line = await _llm_line(date_key, recent, **kwargs)
        if not line:
            line = _fallback_line(date_key)
            logger.info("[Rihen] 用手写兜底: %s", line)

        state = _read_state()
        _append_day(state, date_key, line)
        _write_state(state)
        return line
    finally:
        _lock_release()


async def generate_today_mark(force: bool = False, **kwargs) -> str | None:
    date_key = datetime.now().strftime("%Y-%m-%d")
    for _ in range(2):
        line = await _generate_once(date_key, force, **kwargs)
        if line is not None:
            return line
    return None


# ── 版式计算 ──────────────────────────────────────────────────────


def _profile_for(screen_w: int, screen_h: int) -> dict:
    key = f"{int(screen_w)}x{int(screen_h)}"
    if key in PROFILES:
        return PROFILES[key]
    return PROFILES["default"]


def _geometry(screen_w: int, screen_h: int) -> dict:
    """复刻 json_renderer 的几何，保证算出来的行数不会压到页脚。"""
    w = int(screen_w or 400)
    h = int(screen_h or 300)
    scale = w / 400.0
    if scale < 0.92:
        scale = 0.92
    if w >= 648:
        scale = min(scale, 1.35)
    min_scale = min(scale, h / 300.0)
    if min_scale < 0.65:
        min_scale = 0.65

    if h <= 128:
        status_bottom = int(h * 0.11) + 2
    elif h < 200:
        status_bottom = int(h * 0.10)
    else:
        status_bottom = int(h * 0.12)

    footer_h = int(30 * min_scale)
    if h <= 128:
        footer_h = max(footer_h, 24)
    footer_top = h - footer_h
    if h <= 128:
        footer_top += 3

    return {
        "scale": scale,
        "body_h": max(0, footer_top - status_bottom),
        "status_bottom": status_bottom,
        "footer_top": footer_top,
    }


def _capacity(profile: dict, geo: dict, with_seal: bool) -> int:
    """这一屏能放几行——必须与 json_renderer 的排版算法一致，否则会压到页脚。

    行高 = 每行 line_height，行距 = row_gap；列的上下内边距各算一次；
    落印时额外占一列间距 + 一行印高。
    """
    scale = geo["scale"]
    line_h = max(1, int(profile["line_height"] * scale))
    gap = max(0, int(profile["row_gap"] * scale))
    pad_y = max(0, int(profile["padding_y"] * scale)) * 2
    usable = geo["body_h"] - pad_y - 2  # 留 2px 余量，防几何算差一像素
    if with_seal:
        usable -= max(0, int(profile["column_gap"] * scale))
        usable -= max(1, int(profile["seal_line_height"] * scale))
    if usable <= 0:
        return 1
    rows = (usable + gap) // (line_h + gap)
    return max(1, int(rows))


# ── 册号 ──────────────────────────────────────────────────────────

_CN_DIGITS = "〇一二三四五六七八九"


def _cn_number(num: int) -> str:
    """1-99 的中文数字，够册号用了。"""
    num = int(num)
    if num <= 0:
        return str(num)
    if num < 10:
        return _CN_DIGITS[num]
    if num == 10:
        return "十"
    if num < 20:
        return "十" + _CN_DIGITS[num - 10]
    tens, ones = divmod(num, 10)
    return _CN_DIGITS[tens] + "十" + (_CN_DIGITS[ones] if ones else "")


def _volume_label(volume: int) -> str:
    return f"第{_cn_number(volume)}册"


def _date_label(date_key: str) -> str:
    try:
        parts = str(date_key).split("-")
        if len(parts) >= 3:
            return f"{int(parts[1])}/{int(parts[2])}"
    except (TypeError, ValueError):
        pass
    return str(date_key)


# ── provider 入口 ─────────────────────────────────────────────────


async def build_rihen_content(fallback: dict | None = None, **kwargs) -> dict:
    screen_w = int(kwargs.get("screen_w") or 400)
    screen_h = int(kwargs.get("screen_h") or 300)
    date_key = datetime.now().strftime("%Y-%m-%d")

    try:
        state = _read_state()
        day = _find_day(state, date_key)
        if day is None:
            line = await generate_today_mark(**kwargs)
            state = _read_state()
            day = _find_day(state, date_key)
            text = str((day or {}).get("text") or line or "")
        else:
            text = str(day.get("text") or "")
    except Exception:
        logger.warning("[Rihen] 取当天记录失败，退回兜底", exc_info=True)
        state = _read_state()
        text = _fallback_line(date_key)

    days = _days(state)
    volume = _current_volume(state)
    volume_days = _volume_days(state, volume)
    remaining = max(0, VOLUME_DAYS - len(volume_days))
    near_full = 0 < remaining <= SEAL_THRESHOLD

    profile = _profile_for(screen_w, screen_h)
    geo = _geometry(screen_w, screen_h)

    # 册将满时页面末尾要落一方印，印占一行
    capacity = _capacity(profile, geo, near_full)

    recent = days[-capacity:] if capacity > 0 else []
    rows = [{"d": _date_label(d.get("date")), "t": str(d.get("text") or "")} for d in recent]

    content = {
        "rows": rows,
        "seal": _volume_label(volume) if near_full else "",
        "volume_label": _volume_label(volume),
        "day_count": str(len(days)),
        "has_text": "yes" if text else "no",
        "today": text,
    }
    if isinstance(fallback, dict):
        for key, value in fallback.items():
            if not content.get(key):
                content[key] = value
    return content


def read_history() -> dict:
    """给 history 脚本用：返回当前册、全部记录和已归档的册。"""
    state = _read_state()
    days = _days(state)
    volume = _current_volume(state)
    return {
        "volume": volume,
        "volume_label": _volume_label(volume),
        "days": [{"date": d.get("date"), "text": d.get("text"), "volume": d.get("volume")} for d in days],
        "archived": _archived(state),
    }
