"""每日内容缓存 —— 一个（模式 + 参数指纹 + 日期）只生成一次，全设备共享。

背景
----
墨水屏一天能看几次，但原来设备每次刷新都可能重新调模型生成内容，
缓存键还按 mac 分 —— 每台屏各生成一份同样的内容。

规则
----
1. 缓存键 = 模式 + 参数指纹 + 日期 + 画幅。**不含 mac**：
   同参数的多台设备共享同一份内容。
2. 参数指纹只含真正影响内容的字段：城市/坐标/时区/语言。
   主题语气（content_tone / character_tones）不算 —— 内容风格统一。
3. 时段不进指纹：生成时刻取一次并冻结进内容。否则早晚各成一份，
   「一天一张」就破了。
4. 保留 7 天：当天没有就取最近一次成功的。
5. 同键并发只生成一次（进程内锁）：定时器和设备同时要同一份时，
   只发一次模型请求，另一个等结果。

存储
----
复用 cache.db 的 image_cache 表，键前缀 daily:；
文字内容存 daily_text_cache 表，键前缀 dailytext:。

为什么用标准库 sqlite3 而不是 core/db.py 的 aiosqlite
-------------------------------------------------------
aiosqlite 的连接绑定在创建它的事件循环上。这个模块同时被后端服务
（长驻单循环）、每日批处理脚本（一次性循环）和单元测试（每个用例一个
循环）使用，跨循环复用会死锁。标准库 sqlite3 + 线程池没有这个问题：
本地小文件、结果缓存、线程开销可忽略。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import AsyncIterator, Optional

from PIL import Image

logger = logging.getLogger(__name__)

# 参与参数指纹的字段：这些变了，内容就得是另一份。
FINGERPRINT_FIELDS = (
    "city",
    "latitude",
    "longitude",
    "timezone",
    "language",
    "mode_language",
)

# 缓存保留天数：当天没有就取最近 7 天内成功过的那份
RETENTION_DAYS = 7

# 同键生成锁的表大小上限（模式 × 指纹 × 每天，量很小）
_MAX_LOCKS = 128

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_CACHE_DB_PATH = os.path.join(_BACKEND_DIR, "cache.db")

# 单次读库超时（秒）
DB_TIMEOUT_SECONDS = 3.0

TEXT_PREFIX = "dailytext:"

_locks: dict[str, asyncio.Lock] = {}
_locks_guard = asyncio.Lock()


# ── sqlite 辅助 ──────────────────────────────────────────────────

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_CACHE_DB_PATH, timeout=DB_TIMEOUT_SECONDS)
    return conn


def _ensure_tables() -> None:
    """确保两张表都在。同步调用，只在首次和写操作前跑。"""
    if not os.path.exists(_CACHE_DB_PATH):
        return
    try:
        conn = _connect()
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS image_cache (
                    cache_key TEXT PRIMARY KEY,
                    image_data BLOB NOT NULL,
                    created_at TEXT NOT NULL
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS daily_text_cache (
                    cache_key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )"""
            )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.warning("[DAILY_CACHE] 建表失败: %s", exc)


def _run_sql(statements: list[tuple[str, tuple]]) -> list:
    """在线程池里跑同步 sqlite3 读。

    statements = [(sql, params), ...]，返回每条的结果行列表。
    """
    def _exec() -> list:
        if not os.path.exists(_CACHE_DB_PATH):
            return [[] for _ in statements]
        conn = _connect()
        try:
            out = []
            for sql, params in statements:
                try:
                    out.append(conn.execute(sql, params).fetchall())
                except sqlite3.Error:
                    out.append([])
            return out
        finally:
            conn.close()

    return asyncio.to_thread(_exec)


def _run_write(statements: list[tuple[str, tuple]]) -> int:
    """在线程池里跑写操作。返回受影响行数（取最大的那条）。"""
    def _exec() -> int:
        if not os.path.exists(_CACHE_DB_PATH):
            return 0
        conn = _connect()
        try:
            best = 0
            for sql, params in statements:
                try:
                    cur = conn.execute(sql, params)
                    if cur.rowcount and cur.rowcount > best:
                        best = cur.rowcount
                except sqlite3.Error:
                    continue
            conn.commit()
            return best
        finally:
            conn.close()

    return asyncio.to_thread(_exec)


# ── 键与指纹 ────────────────────────────────────────────────────

def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def param_fingerprint(effective_cfg: dict | None, persona: str = "") -> str:
    """参数指纹。effective_cfg 是该模式的有效配置（含 mode_overrides 合并）。

    只取 FINGERPRINT_FIELDS —— 主题语气不影响「是不是同一份内容」的判定，
    按用户决定统一处理。

    一天之内画面会变的模式（如日昼与月相：白天日出日落 / 夜里月相），
    指纹再加一段时段标记，白天和夜里各存一份缓存。
    """
    cfg = effective_cfg if isinstance(effective_cfg, dict) else {}
    payload = {k: cfg.get(k) for k in FINGERPRINT_FIELDS}
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    fp = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    segment = day_night_segment(persona, cfg)
    return f"{fp}.{segment}" if segment else fp


# 一天之内画面会变的模式：缓存键要带时段，否则白天的图会霸占一整天。
# mode_id → (模块名, 分桶函数名)
_SEGMENT_PROVIDERS = {
    "CUSTOM_SUN_MOON": ("sun_moon", "cache_period"),      # 白天日出日落 / 夜里月相
    "CUSTOM_WEATHER": ("weather_dashboard", "cache_bucket"),  # 逐小时预报，每小时一份
}


def day_night_segment(persona: str, cfg: dict | None = None) -> str:
    """日内分段标记：同一天里画面会变的模式，按时段分开缓存。

    未注册的模式返回空串（指纹不变，行为与从前一致）。
    """
    target = _SEGMENT_PROVIDERS.get((persona or "").upper())
    if not target:
        return ""
    module_name, func_name = target
    try:
        import importlib

        module = importlib.import_module(f".{module_name}", package=__package__)
        return str(getattr(module, func_name)(cfg) or "")
    except Exception:
        logger.warning("[DAILY_CACHE] 时段标记计算失败，按不分区处理", exc_info=True)
        return ""


def daily_key(persona: str, fingerprint: str, day: str, screen_w: int, screen_h: int) -> str:
    persona = (persona or "").upper()
    return f"daily:{persona}:{fingerprint}:{day}:{screen_w}x{screen_h}"


def text_key(persona: str, fingerprint: str, day: str) -> str:
    return f"{TEXT_PREFIX}{(persona or '').upper()}:{fingerprint}:{day}"


def _prefix(persona: str, fingerprint: str) -> str:
    return f"daily:{(persona or '').upper()}:{fingerprint}:"


@asynccontextmanager
async def generation_lock(key: str) -> AsyncIterator[None]:
    """同键互斥。定时器和设备同时要同一份内容时，只生成一次。"""
    async with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            if len(_locks) >= _MAX_LOCKS:
                for k in [k for k, v in _locks.items() if not v.locked()][: len(_locks) // 2]:
                    _locks.pop(k, None)
            lock = asyncio.Lock()
            _locks[key] = lock
    async with lock:
        yield


async def _load_image(blob: bytes) -> Optional[Image.Image]:
    import io

    try:
        img = Image.open(io.BytesIO(blob))
        img.load()
        return img
    except (OSError, TypeError, ValueError) as exc:
        logger.warning("[DAILY_CACHE] 解码失败: %s", exc)
        return None


# ── 图片内容 ────────────────────────────────────────────────────

async def get_today(
    persona: str, fingerprint: str, day: str, screen_w: int, screen_h: int
) -> Optional[Image.Image]:
    """取当天的内容。没有就是 None。"""
    key = daily_key(persona, fingerprint, day, screen_w, screen_h)
    try:
        rows = await _run_sql(
            [("SELECT image_data FROM image_cache WHERE cache_key = ?", (key,))]
        )
    except Exception as exc:
        logger.warning("[DAILY_CACHE] 读取失败 %s: %s", key, exc)
        return None
    if not rows or not rows[0]:
        return None
    return await _load_image(rows[0][0][0])


async def set_daily(
    persona: str,
    fingerprint: str,
    day: str,
    screen_w: int,
    screen_h: int,
    img: Image.Image,
) -> None:
    """写入当天内容。同键覆盖。"""
    import io

    key = daily_key(persona, fingerprint, day, screen_w, screen_h)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    data = buf.getvalue()
    now = datetime.now().isoformat()
    try:
        await _run_write(
            [(
                """INSERT INTO image_cache (cache_key, image_data, created_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(cache_key) DO UPDATE SET image_data = ?, created_at = ?""",
                (key, data, now, data, now),
            )]
        )
    except Exception as exc:
        logger.warning("[DAILY_CACHE] 写入失败 %s: %s", key, exc)


async def get_recent(
    persona: str,
    fingerprint: str,
    screen_w: int,
    screen_h: int,
    max_age_days: int = RETENTION_DAYS,
) -> Optional[Image.Image]:
    """取最近一次成功的内容（7 天内）。

    用于「当天生成失败」时的兜底 —— 下发上一次成功的那张，
    屏上永远有东西，不用额外写一套降级链。
    """
    cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat()
    pattern = _prefix(persona, fingerprint) + f"%:{screen_w}x{screen_h}"
    try:
        rows = await _run_sql(
            [(
                """SELECT image_data FROM image_cache
                   WHERE cache_key LIKE ? AND created_at >= ?
                   ORDER BY created_at DESC LIMIT 1""",
                (pattern, cutoff),
            )]
        )
    except Exception as exc:
        logger.warning("[DAILY_CACHE] 读取近期内容失败: %s", exc)
        return None
    if not rows or not rows[0]:
        return None
    return await _load_image(rows[0][0][0])


async def get_or_generate(
    persona: str,
    fingerprint: str,
    day: str,
    screen_w: int,
    screen_h: int,
    generate_fn,
) -> tuple[Optional[Image.Image], Optional[dict], bool]:
    """取当天内容，没有就生成。

    generate_fn 必须返回 (图片, 内容) —— 内容是刚生成时的原始 dict，
    缓存命中时拿不到，会返回 None。

    返回 (图片, 内容, 是否新生成)。图片为 None 表示彻底没有内容可用。

    同键互斥：定时器批处理和设备取图同时要同一份时，只发一次模型请求。
    拿到锁之后**再查一次** —— 别人可能刚生成完。

    生成失败时退到 7 天内最近一次成功的：屏上永远有东西，
    不需要另写一套 429 降级链。
    """
    key = daily_key(persona, fingerprint, day, screen_w, screen_h)
    async with generation_lock(key):
        img = await get_today(persona, fingerprint, day, screen_w, screen_h)
        if img is not None:
            return img, None, False

        content = None
        try:
            img, content = await generate_fn()
        except Exception as exc:
            logger.error("[DAILY_CACHE] 生成失败 %s: %s", persona, exc)
            img = None

        if img is not None:
            await set_daily(persona, fingerprint, day, screen_w, screen_h, img)
            return img, content, True

        recent = await get_recent(persona, fingerprint, screen_w, screen_h)
        if recent is not None:
            logger.info("[DAILY_CACHE] %s 当天无内容，下发最近一次成功的", persona)
        return recent, None, True


async def has_today(
    persona: str, fingerprint: str, day: str, screen_w: int, screen_h: int
) -> bool:
    key = daily_key(persona, fingerprint, day, screen_w, screen_h)
    try:
        rows = await _run_sql(
            [("SELECT 1 FROM image_cache WHERE cache_key = ? LIMIT 1", (key,))]
        )
        return bool(rows and rows[0])
    except Exception:
        return False


async def cleanup(max_age_days: int = RETENTION_DAYS) -> int:
    """删掉过期条目。返回删除条数。"""
    cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat()
    try:
        return await _run_write(
            [(
                "DELETE FROM image_cache WHERE cache_key LIKE 'daily:%' AND created_at < ?",
                (cutoff,),
            )]
        )
    except Exception as exc:
        logger.warning("[DAILY_CACHE] 清理失败: %s", exc)
        return 0


# ── 文字内容 ────────────────────────────────────────────────────
# 图片缓存存 PNG，文字内容（dict）存 JSON，同一个（模式 + 指纹 + 日期）键。
# 文字侧按天固定：除非用户临时改了参数，否则当天只生成一次。

async def get_text(persona: str, fingerprint: str, day: str) -> Optional[dict]:
    """取当天的文字内容。没有就是 None。"""
    key = text_key(persona, fingerprint, day)
    try:
        rows = await _run_sql(
            [("SELECT payload FROM daily_text_cache WHERE cache_key = ?", (key,))]
        )
    except Exception as exc:
        logger.warning("[DAILY_TEXT] 读取失败 %s: %s", key, exc)
        return None
    if not rows or not rows[0]:
        return None
    try:
        return json.loads(rows[0][0][0])
    except (ValueError, TypeError) as exc:
        logger.warning("[DAILY_TEXT] 解析失败 %s: %s", key, exc)
        return None


async def set_text(persona: str, fingerprint: str, day: str, content: dict) -> None:
    """写入当天的文字内容。同键覆盖。"""
    key = text_key(persona, fingerprint, day)
    now = datetime.now().isoformat()
    payload = json.dumps(content, ensure_ascii=False, default=str)
    try:
        await _run_write(
            [(
                """INSERT INTO daily_text_cache (cache_key, payload, created_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(cache_key) DO UPDATE SET payload = ?, created_at = ?""",
                (key, payload, now, payload, now),
            )]
        )
    except Exception as exc:
        logger.warning("[DAILY_TEXT] 写入失败 %s: %s", key, exc)


async def get_text_or_generate(
    persona: str,
    fingerprint: str,
    day: str,
    generate_fn,
) -> tuple[Optional[dict], bool]:
    """取当天文字内容，没有就生成。

    同键互斥：多个请求同时要同一份时只调一次模型。
    返回 (内容, 是否新生成)。内容为 None 表示没有可用内容。
    """
    key = text_key(persona, fingerprint, day)
    async with generation_lock(key):
        cached = await get_text(persona, fingerprint, day)
        if cached is not None:
            return cached, False
        try:
            content = await generate_fn()
        except Exception as exc:
            logger.error("[DAILY_TEXT] 生成失败 %s: %s", persona, exc)
            return None, True
        if content is None:
            return None, True
        await set_text(persona, fingerprint, day, content)
        return content, True
