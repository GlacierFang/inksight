"""历史整屏图：走真实渲染管线出那一天的那张图，落盘后供回顾页取用。

这是**唯一**的历史出图路径 —— 和设备当天收到的那张图同一套管线、同一套版式。
不另写重建逻辑，不另起渲染器：预览怎么出，这里就怎么出。

落盘位置：runtime_cache/<state>/screens/<YYYY-MM-DD>.png
- 设备当天真实下发的图，也存同一个位置（见 core/screen_archive.py）。
- 同一个模式同一天重复出图 → 覆盖为最后一次。
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_BACKEND_DIR = Path(__file__).resolve().parent.parent
STATE_DIR = _BACKEND_DIR / "runtime_cache"

# 设备默认实际尺寸（4 寸墨水屏）
DEVICE_WIDTH = 400
DEVICE_HEIGHT = 300
DEVICE_COLORS = 4


def screens_dir(state_name: str) -> Path:
    return STATE_DIR / state_name / "screens"


def screen_path(state_name: str, day: str, phase: str = "", base_dir=None) -> Path:
    """整屏图路径。

    多时段模式（小景）一天四张，用 <日期>_<时段>.png 分开存；
    其余模式仍是一天一张 <日期>.png。

    base_dir 显式指定根目录 —— 调用方（screen_archive）有自己的 STATE_DIR，
    不能被这里的模块级常量绑死。
    """
    root = base_dir if base_dir is not None else STATE_DIR
    name = f"{day}_{phase}.png" if phase else f"{day}.png"
    return root / state_name / "screens" / name


def state_phase(state_name: str, day: str, base_dir=None) -> str:
    """多时段模式当天当前是哪个时段（dawn/day/dusk/night）；其它模式返回空串。

    存档自己判断时段，不让调用方决定 —— 设备下发、缓存预热、定时任务
    三条路都只传「这张图属于哪天」，时段从状态里读，三者因此一致。
    """
    root = base_dir if base_dir is not None else STATE_DIR
    try:
        path = root / state_name / "state.json"
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        days = data.get("days") if isinstance(data, dict) else None
        record = days.get(day) if isinstance(days, dict) else None
        if isinstance(record, dict):
            return str(record.get("phase") or "")
    except (OSError, ValueError):
        pass
    return ""


def save_png(state_name: str, day: str, data: bytes, phase: str = "") -> Path | None:
    """把整屏 PNG 落盘。同一天同一时段重复出图 → 覆盖。"""
    if not state_name or not day:
        return None
    try:
        path = screen_path(state_name, day, phase)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
        logger.info("[History] 落盘 %s %s (%d bytes)", state_name, day, len(data))
        return path
    except OSError as exc:
        logger.warning("[History] 落盘失败 %s %s: %s", state_name, day, exc)
        return None


def read_png(state_name: str, day: str, phase: str = "") -> bytes | None:
    """读已落盘的整屏 PNG；没有返回 None。"""
    try:
        path = screen_path(state_name, day, phase)
        if path.is_file():
            return path.read_bytes()
    except OSError as exc:
        logger.warning("[History] 读盘失败 %s %s: %s", state_name, day, exc)
    return None


async def render_day(mode_id: str, day: str, *, phase: str = "", colors: int = DEVICE_COLORS,
                    screen_w: int = DEVICE_WIDTH, screen_h: int = DEVICE_HEIGHT) -> bytes | None:
    """按真实管线渲染某一天的整屏图，返回 PNG 字节。

    - 走 build_image（和设备下发同一套代码），只是把日期换成指定那天；
    - 历史日的天气留空（那天取不到，不编）；
    - 不调模型补内容：provider 自己决定那天有没有记录。
    """
    from api.shared import build_image

    try:
        img, resolved, _hit, _fb, _invalid, _llm = await build_image(
            3.9,
            None,
            mode_id,
            screen_w=screen_w,
            screen_h=screen_h,
            colors=colors,
            day_override=day,
            skip_cache=True,
            phase_override=phase or None,
        )
    except Exception as exc:
        logger.warning("[History] 渲染 %s %s 失败: %s", mode_id, day, exc)
        return None

    if img is None:
        return None
    if img.size != (screen_w, screen_h):
        from PIL import Image

        img = img.resize((screen_w, screen_h), Image.NEAREST)

    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def render_day_sync(mode_id: str, day: str, **kwargs) -> bytes | None:
    """同步包装（脚本里用）。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(render_day(mode_id, day, **kwargs))
    raise RuntimeError("render_day_sync 不能在事件循环里调用")
