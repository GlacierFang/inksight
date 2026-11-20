"""订阅集合 —— 「哪些模式允许调用模型」的唯一真相源。

背景
----
2026-11-17 之前，所有 LLM / 生图调用只要有代码路径走到就会发请求：
定时器、每日批处理、画廊预热都不看设备到底勾了哪些模式。结果是
设备只勾了 5 个模式，后台照样按全集在生成 —— 大量调用花给没人看的内容。

规则
----
1. 订阅集合 = 所有**在用设备**勾选模式的并集。
2. 在用设备 = 24 小时内有心跳（device_heartbeats）或状态轮询（device_state）
   的设备。离线设备的配置保留（插电回来自动恢复），但不占生成量。
3. 任何内容生成前必须过闸门：模式不在订阅集合里 → 直接拒绝，不发请求。
   手动预览**不豁免**，和后台路径走同一套判断（用户明确要求不留后门）。
4. 闸门拦的是「内容模式」，不是基础设施调用。语音识别、聊天这类
   不属于内容模式，不受此约束。

为什么用标准库 sqlite3 而不是 core/db.py 的 aiosqlite
-------------------------------------------------------
aiosqlite 的连接绑定在创建它的事件循环上。闸门会被设备热路径和单测
同时用到，跨事件循环复用会挂住。这里改成标准库 sqlite3 + 线程池：
读的是本地小文件、结果缓存 60 秒，线程开销可以忽略，跨循环绝对安全。

快照
----
订阅集合缓存 60 秒。设备刚勾选的新模式最多 60 秒后生效 ——
配置变更频率极低，这个延迟可以接受，换来每次生成不必查库。
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

_BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..")
_MAIN_DB_PATH = os.path.abspath(os.path.join(_BACKEND_DIR, "inksight.db"))

# 在用设备窗口：24 小时内有心跳或状态轮询即视为在用
ACTIVE_WINDOW_HOURS = 24

# 订阅集合快照缓存时间（秒）
SNAPSHOT_TTL_SECONDS = 60

# 单次读库超时（秒）。宁可按旧快照判，也不要把设备取图拖死。
DB_TIMEOUT_SECONDS = 3.0


class ModeNotSubscribed(Exception):
    """模式不在订阅集合里 —— 拒绝调用模型。

    API 层应把它翻译成 403 + 一句人话，而不是 500。
    """

    def __init__(self, mode_id: str, source: str = ""):
        self.mode_id = mode_id
        self.source = source
        super().__init__(f"mode {mode_id!r} not subscribed (source={source})")


_snapshot: frozenset[str] | None = None
_snapshot_at: float = 0.0

# 本次请求额外允许的模式。
# 设备自己的勾选就是它自己的订阅 —— 设备来取图，说明它现在是在用设备。
# 用 contextvar 传递，免得把 allow 一路穿到每个生成函数里去。
_allowed_extra: contextvars.ContextVar[frozenset[str]] = contextvars.ContextVar(
    "subscription_allowed_extra", default=frozenset()
)


@contextlib.contextmanager
def allow_modes(modes):
    """本段上下文内，允许这些模式通过闸门。

    用法：设备取图时把它自己勾选的模式放进来。
    注意这不是「预览豁免」—— 预览（无设备）不传任何模式，
    照样按全局订阅集合判。
    """
    token = _allowed_extra.set(frozenset(str(m).upper() for m in (modes or ())))
    try:
        yield
    finally:
        _allowed_extra.reset(token)


def _parse_modes(raw) -> list[str]:
    """configs.modes 有两种存法：JSON 数组字符串，或逗号分隔串。

    两种都要认 —— 历史上两种都写过。
    """
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        items: list = list(raw)
    else:
        s = str(raw).strip()
        if not s:
            return []
        if s.startswith("["):
            try:
                items = json.loads(s)
            except (ValueError, TypeError):
                items = []
        else:
            items = [x for x in s.split(",")]
    out: list[str] = []
    for x in items:
        if isinstance(x, str) and x.strip():
            out.append(x.strip())
    return out


def _query_active_devices() -> list[tuple[str, str]]:
    """返回 [(mac, modes 原文)]，只含 24 小时内活跃的设备。

    在线程池里跑（同步 sqlite3 不能阻塞事件循环）。
    """
    cutoff = (datetime.now() - timedelta(hours=ACTIVE_WINDOW_HOURS)).isoformat()

    def _run() -> list[tuple[str, str]]:
        if not os.path.exists(_MAIN_DB_PATH):
            return []
        conn = sqlite3.connect(_MAIN_DB_PATH, timeout=DB_TIMEOUT_SECONDS)
        try:
            conn.row_factory = sqlite3.Row
            active: set[str] = set()
            for sql in (
                "SELECT DISTINCT mac FROM device_heartbeats WHERE created_at >= ?",
                "SELECT DISTINCT mac FROM device_state WHERE last_state_poll_at >= ?",
            ):
                try:
                    for row in conn.execute(sql, (cutoff,)):
                        if row[0]:
                            active.add(row[0])
                except sqlite3.Error:
                    continue

            out: list[tuple[str, str]] = []
            for mac in sorted(active):
                raw = None
                try:
                    cur = conn.execute(
                        "SELECT modes FROM configs WHERE mac = ? AND is_active = 1",
                        (mac,),
                    )
                    row = cur.fetchone()
                    if row:
                        raw = row[0]
                    else:
                        cur = conn.execute(
                            "SELECT modes FROM configs WHERE mac = ? ORDER BY id DESC LIMIT 1",
                            (mac,),
                        )
                        row = cur.fetchone()
                        if row:
                            raw = row[0]
                except sqlite3.Error:
                    raw = None
                out.append((mac, raw))
            return out
        finally:
            conn.close()

    return asyncio.to_thread(_run)


async def active_device_configs() -> list[dict]:
    """在用设备各自的勾选模式：[{mac, modes}]。

    每台设备的城市/语言可能不同 —— 内容要按各自的参数生成。
    同参数的设备由每日缓存自动去重（键含参数指纹），不会重复调模型。
    """
    rows = await _query_active_devices()
    return [{"mac": mac, "modes": _parse_modes(raw)} for mac, raw in rows]


async def active_macs() -> set[str]:
    """24 小时内有心跳或状态轮询的设备。"""
    rows = await _query_active_devices()
    return {mac for mac, _ in rows if mac}


async def _load_subscribed_modes() -> frozenset[str]:
    modes: set[str] = set()
    for dev in await active_device_configs():
        modes.update(dev["modes"])
    return frozenset(modes)


async def get_snapshot(*, force: bool = False) -> frozenset[str]:
    """当前订阅集合。带 60 秒缓存。

    读库失败时沿用上一份快照；一次都没读到过就返回空集合
    （宁可全部拒绝，也不要在状态不明时放行调用）。
    """
    global _snapshot, _snapshot_at
    now = time.monotonic()
    if not force and _snapshot is not None and (now - _snapshot_at) < SNAPSHOT_TTL_SECONDS:
        return _snapshot
    try:
        _snapshot = await asyncio.wait_for(_load_subscribed_modes(), timeout=DB_TIMEOUT_SECONDS * 2)
        _snapshot_at = now
    except Exception:
        logger.warning("[SUBSCRIPTION] 读取订阅集合失败，沿用上一份快照", exc_info=True)
        if _snapshot is None:
            _snapshot = frozenset()
    return _snapshot


async def ensure_allowed(mode_id: str, *, source: str = "") -> None:
    """闸门。模式不在订阅集合里就抛 ModeNotSubscribed。

    这是「未勾选内容不调用 LLM」的硬保证点：所有内容生成路径
    都要先过这一关，不在集合里就不发请求。
    """
    if not mode_id:
        return
    snapshot = await get_snapshot()
    extra = _allowed_extra.get()
    if mode_id not in snapshot and mode_id not in extra:
        raise ModeNotSubscribed(mode_id, source)


async def subscribed_modes(*, force: bool = False) -> set[str]:
    return set(await get_snapshot(force=force))


def clear_snapshot_cache() -> None:
    """配置变更后调用，让新订阅立刻生效。"""
    global _snapshot, _snapshot_at
    _snapshot = None
    _snapshot_at = 0.0
