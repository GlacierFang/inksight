"""画廊：把「设备真实下发过 / 历史留存过的那张整屏图」归档起来，供网页回看。

三种形态：
- grid  图文：主图 = 整屏图（状态栏、页脚、字段都在里面）
- text  纯文字：一天一条（草地铺道蚁）
- wide  宽幅整屏：按日期现算（电子世界）

数据源是各模式自己的状态文件（runtime_cache/<mode>/state.json）——
那些文件本来就是各模式的账本，不新建表。

整屏图的来源只有一处：core/history_render.py 走真实渲染管线出的图，
落盘在 runtime_cache/<mode>/screens/<YYYY-MM-DD>.png；
设备当天真实下发的图也存同一个位置（core/screen_archive.py）。
不存在「重建」这种第二套东西 —— 页面上看到的就是同一套管线出的图。
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import date
from pathlib import Path

from . import history_render

logger = logging.getLogger(__name__)

_BACKEND_DIR = Path(__file__).resolve().parent.parent
STATE_DIR = _BACKEND_DIR / "runtime_cache"
STATIC_DIR = _BACKEND_DIR / "static"

# shape=map  状态里是 {日期: 条目} 的字典
# shape=list 状态里是 [{日期: 条目}] 的列表（rows 指明取哪个键）
# form=wide  按日期现算（电子世界）
# state=该模式在 runtime_cache 下的目录名（整屏图放在这个目录下的 screens/）
_MODES: dict[str, dict] = {
    "CUSTOM_HAICUOTU": {
        "name": "海错图", "form": "grid", "state": "haicuotu", "shape": "map",
        "title": "名", "text": "特征", "meta": "异名",
        "fields": [("名", "名"), ("异名", "异名"), ("尺寸", "尺寸"),
                   ("产地", "产地"), ("特征", "特征"), ("题识", "题识")],
    },
    "CUSTOM_DANQING": {
        "name": "丹青谱", "form": "grid", "state": "danqing", "shape": "map",
        "title": "画名", "text": "题跋", "meta": "作者",
        "fields": [("画名", "画名"), ("作者", "作者"), ("朝代", "朝代"),
                   ("题材", "题材"), ("题跋", "题跋"), ("印文", "印文")],
    },
    "CUSTOM_SONGMENG": {
        "name": "大宋梦影", "form": "grid", "state": "songmeng", "shape": "list",
        "rows": "recent", "title": "scene", "text": "colophon", "meta": "",
        "fields": [("scene", "场景"), ("colophon", "题记")],
    },
    "CUSTOM_TANGPING": {
        "name": "躺平巴拉", "form": "grid", "state": "tangping", "shape": "list",
        "rows": "recent", "title": "place", "text": "line", "meta": "",
        "fields": [("place", "地点"), ("line", "呓语")],
    },
    "CUSTOM_TIANGONG": {
        "name": "天工阁", "form": "grid", "state": "tiangong", "shape": "map",
        "title": "名称", "text": "器形", "meta": "款识",
        "fields": [("category", "类别"), ("名称", "名称"), ("器形", "器形"),
                   ("用途", "用途"), ("纹样", "纹样"), ("款识", "款识"),
                   ("来历", "来历")],
    },
    "CUSTOM_ANT": {
        # 铺道蚁是纯文字板，但屏上显示的就是那张整屏图 —— 一样按图展示
        "name": "草地铺道蚁", "form": "grid", "state": "ant", "shape": "list",
        "rows": "recent", "title": "title", "text": "lines", "meta": "",
        "fields": [("title", "标题"), ("lines", "纪事"), ("voice", "心声"),
                   ("state_line", "状态"), ("trip_line", "行程"),
                   ("signature", "签名")],
    },
    "CUSTOM_XIAOJING": {
        "name": "小景", "form": "grid", "state": "xiaojing", "shape": "map",
        "title": "phase_label", "text": "phase", "meta": "",
        "fields": [("phase_label", "时段"), ("phase", "时段")],
    },
    "CUSTOM_EWORLD": {"name": "电子世界", "form": "wide", "state": "eworld"},
    "CUSTOM_WINDOW": {
        "name": "窗外", "form": "grid", "state": "window", "shape": "map",
        "title": "city_name", "text": "weather_text", "meta": "",
        "fields": [("city_name", "城市"), ("weather_text", "天气")],
    },
}


def modes() -> list[dict]:
    """画廊收录的模式清单（前端据此排 tab）。"""
    return [
        {"mode_id": mid, "name": spec["name"], "form": spec["form"]}
        for mid, spec in _MODES.items()
    ]


def _spec(mode_id: str) -> dict | None:
    return _MODES.get((mode_id or "").upper())


def state_name(mode_id: str) -> str:
    """该模式在 runtime_cache 下的目录名。"""
    spec = _spec(mode_id)
    return str(spec.get("state") or "") if spec else ""


def _read_state(name: str) -> dict:
    try:
        with (STATE_DIR / name / "state.json").open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _eworld_epoch() -> date:
    """电子世界的起算日（数据集里的 epoch）。"""
    try:
        data = json.loads(
            (_BACKEND_DIR / "core/data/eworld.json").read_text(encoding="utf-8")
        )
        return date.fromisoformat(str(data["epoch"]))
    except (OSError, ValueError, KeyError, TypeError):
        return date(2026, 10, 28)


def _as_text(value) -> str:
    if isinstance(value, list):
        return "".join(str(v) for v in value if v)
    return str(value or "")


def _rows(state: dict, spec: dict) -> list[tuple[str, dict]]:
    if spec.get("shape") == "map":
        days = state.get("days")
        if not isinstance(days, dict):
            return []
        return [
            (str(key), days[key])
            for key in sorted(days)
            if isinstance(days[key], dict)
        ]
    items = state.get(spec.get("rows") or "days")
    if not isinstance(items, list):
        return []
    # 同一天可能有多条（重渲染会追加），只留最后一条
    latest: dict[str, dict] = {}
    for item in items:
        if isinstance(item, dict) and item.get("date"):
            latest[str(item.get("date"))] = item
    return sorted(latest.items(), key=lambda pair: pair[0])


def _fields(spec: dict, item: dict) -> list[dict]:
    """那一天的全部字段。缺的东西如实标「历史缺失」，不编。"""
    out: list[dict] = []
    for key, label in spec.get("fields") or []:
        raw = item.get(key)
        if isinstance(raw, list):
            value = "\n".join(str(v) for v in raw if v)
        else:
            value = str(raw or "")
        out.append({
            "label": label,
            "value": value,
            "missing": not value,
        })
    return out


def _has_screen(state: str, day: str) -> bool:
    return history_render.screen_path(state, day).is_file()


def entries(mode_id: str) -> list[dict]:
    """某模式的往期条目，按日期升序。

    只收真的留了整屏图的日子 —— 没图的日子不占位。
    """
    spec = _spec(mode_id)
    if not spec or spec.get("form") == "wide":
        return []
    state_name = str(spec.get("state") or "")
    state = _read_state(state_name)
    out: list[dict] = []
    for date_key, item in _rows(state, spec):
        if not _has_screen(state_name, date_key):
            continue
        out.append({
            "date": date_key,
            "title": _as_text(item.get(spec.get("title") or "")),
            "text": _as_text(item.get(spec.get("text") or "")),
            "meta": _as_text(item.get(spec.get("meta") or "")),
            "has_screen": True,
            "fields": _fields(spec, item),
        })
    return out


def screen_png(mode_id: str, day: str) -> bytes | None:
    """某一天的整屏 PNG 字节。"""
    spec = _spec(mode_id)
    if not spec:
        return None
    return history_render.read_png(str(spec.get("state") or ""), day)


def _data_url(png: bytes | None) -> str:
    if not png:
        return ""
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


async def render_wide(mode_id: str, day: str) -> dict | None:
    """wide 形态按日期现算那一张（纯本地计算，不调模型）。

    算完也落盘 —— 和其他模式同一个位置，回顾页下次直接读。
    """
    mode_id = (mode_id or "").upper()
    try:
        year, month, dom = (int(part) for part in str(day).split("-"))
        target = date(year, month, dom)
    except (TypeError, ValueError):
        return None

    # 未来日期不存在：电子世界只演到今天
    if target > date.today():
        return None

    date_ctx = {"year": target.year, "month": target.month, "day": target.day}

    if mode_id == "CUSTOM_EWORLD":
        from .eworld import build_eworld_content

        # 起点之前的世界还没开始，不往前翻
        epoch = _eworld_epoch()
        if target < epoch:
            return None

        content = await build_eworld_content(config={}, date_ctx=date_ctx)
        png = content.get("_prefetched_map_url")
        return {
            "date": target.isoformat(),
            "title": str(content.get("title") or ""),
            "subtitle": str(content.get("subtitle") or ""),
            "text": str(content.get("report_line") or ""),
            "meta": str(content.get("report_line2") or ""),
            "image": _data_url(png),
        }

    return None
