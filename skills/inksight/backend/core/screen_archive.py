"""整屏存档：设备真实下发的整屏图落盘。

只做一件事：把设备**实际收到**的那张整屏 PNG 存下来，供回顾页展示。
- 存的是设备实际尺寸（不是 400x300 假定值）；
- 存的是最终整屏（含状态栏、页脚），不是生成素材图；
- 同一模式同一天多次下发 → 覆盖为最后一次实际下发图。

历史日（设备没下发过的那天）的整屏图走 core/history_render.py：
同一套渲染管线，只是把日期换成指定那天，出完落盘到同一个位置。
"""

from __future__ import annotations

import logging
from pathlib import Path

from .history_render import screen_path, state_phase

logger = logging.getLogger(__name__)

_BACKEND_DIR = Path(__file__).resolve().parent.parent
STATE_DIR = _BACKEND_DIR / "runtime_cache"


def screens_dir(state_name: str) -> Path:
    return STATE_DIR / state_name / "screens"


def _screen_path(state_name: str, day: str, phase: str = "") -> Path:
    """存档路径。用本模块的 STATE_DIR —— 不被 history_render 的常量绑死。"""
    return screen_path(state_name, day, phase, base_dir=STATE_DIR)


def save_screen(state_name: str, day: str, image, phase: str = "") -> Path | None:
    """把设备真实拿到的整屏图落盘。同一天同一时段多次下发 → 覆盖。

    image 是 PIL Image（设备实际尺寸）。

    phase 留空时自己去状态里读当天当前时段（小景一天四张，按时段分开存）。
    调用方只管「这张图属于哪天」，时段由状态说了算 —— 设备下发、缓存预热、
    定时任务三条路因此落到同一个文件上。
    """
    if not state_name or not day:
        return None
    if not phase:
        phase = state_phase(state_name, day, base_dir=STATE_DIR)
    try:
        path = _screen_path(state_name, day, phase)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        image.save(tmp, "PNG")
        tmp.replace(path)
        logger.info("[Archive] 存档 %s %s (%sx%s)", state_name, day, *image.size)
        return path
    except (OSError, ValueError) as exc:
        logger.warning("[Archive] 存档失败 %s %s: %s", state_name, day, exc)
        return None


def read_screen(state_name: str, day: str, phase: str = "") -> bytes | None:
    """读存档 PNG 字节；没有存档返回 None。"""
    if not phase:
        phase = state_phase(state_name, day, base_dir=STATE_DIR)
    try:
        path = _screen_path(state_name, day, phase)
        if path.is_file():
            return path.read_bytes()
    except OSError as exc:
        logger.warning("[Archive] 读存档失败 %s %s: %s", state_name, day, exc)
    return None
