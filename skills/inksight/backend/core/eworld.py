"""电子世界（CUSTOM_EWORLD）provider：四方电子生命在一张固定大陆上逐日攻伐。

版面（400x300 单屏）：
  - 顶栏：日期、四色图例（势力名号 + 盘数）；
  - 中部：32 个不规则州郡组成的大陆，州界纯黑 1px，荒原为黑白网点；
  - 底部：与当日地图变化严格对应的模板战报（纯本地词库拼装）。

四色只用现有黑白红黄：州界与文字纯黑，四方分别纯红 / 纯黄 / 纯白 / 黑白斜纹。

确定性：epoch = 模式上线日，逐日 hash(epoch, 第 N 天) 推进，不新增持久化状态表。
关机后重开可直接补算当天状态，同一天对所有设备结果一致。
战报不调用 LLM、不联网。
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
from datetime import date, datetime
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw

from .config import EINK_4COLOR_PALETTE
from .patterns.utils import load_font

logger = logging.getLogger(__name__)

_DATA_PATH = Path(__file__).resolve().parent / "data" / "eworld.json"

# ── 调色板（与墨水屏四色完全一致，量化时零误差）──────────────
BLACK = tuple(EINK_4COLOR_PALETTE[0:3])
WHITE = tuple(EINK_4COLOR_PALETTE[3:6])
YELLOW = tuple(EINK_4COLOR_PALETTE[6:9])
RED = tuple(EINK_4COLOR_PALETTE[9:12])
_COLOR_MAP = {"black": BLACK, "white": WHITE, "yellow": YELLOW, "red": RED}

DESIGN_W = 400
DESIGN_H = 300

# 模式 JSON 里 image 块的逻辑尺寸 → 屏幕尺寸覆盖表。
# provider 必须按同一张表算像素尺寸，否则渲染器会二次缩放、四色被抖动打散。
LOGICAL_BLOCKS: dict[str, tuple[int, int]] = {
    "400x300": (400, 300),
    "648x480": (400, 296),
    "296x128": (321, 139),
}

_NEUTRAL_STYLE = "dots"

# ── 战斗参数（决定世界演化节奏）──────────────────────────────
# 势力战力 = 基础 + 士气 + 扩张惩罚。扩张惩罚让大帝国打不动、小势力敢下手，
# 疆界因此持续挪动；统一全图需要一方把其它三方全部磨垮，仍可能发生。
_STR_BASE = 0.55        # 基础战力
_STR_MORALE = 0.004     # 士气系数（士气 60 → +0.24）
_OVEREXT = 0.012        # 超出舒适规模的每州郡惩罚
_OVEREXT_PIVOT = 18     # 舒适规模（州郡数）
_ATK_EDGE = 0.10        # 进攻方先手优势
_HOME_BONUS = 0.18      # 龙兴之地加成（故土难拔）
_NOISE = 0.22           # 战局随机波动
_MORALE_WIN = 6         # 胜方士气
_MORALE_LOSE = 8        # 败方士气
_SECOND_PUSH = 1.10     # 乘胜再取一郡的所需优势


# ── 数据集 ───────────────────────────────────────────────────
@lru_cache(maxsize=1)
def load_dataset() -> dict:
    """读取数据集；缺失或损坏时返回空字典（provider 回落到 fallback）。"""
    try:
        data = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning("[EWorld] 数据集缺失：%s", _DATA_PATH)
        return {}
    except (OSError, ValueError) as exc:
        logger.warning("[EWorld] 数据集读取失败：%s", exc)
        return {}
    if not isinstance(data, dict):
        return {}
    return data


# ── 几何：手写坐标 → 不规则州郡（Voronoi 切分，边界严格贴合）────
def _convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Andrew monotone chain；返回逆时针凸包。"""
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[tuple[float, float]] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _line_intersect(p, q, a, c):
    """线段 pq 与直线 a·x = c 的交点。"""
    fp = a[0] * p[0] + a[1] * p[1] - c
    fq = a[0] * q[0] + a[1] * q[1] - c
    denom = fp - fq
    if abs(denom) < 1e-12:
        return p
    t = fp / denom
    return (p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1]))


def _clip_halfplane(poly, a, c):
    """保留满足 a·p <= c 的部分（Sutherland-Hodgman）。"""
    if not poly:
        return []
    out: list[tuple[float, float]] = []
    n = len(poly)
    for i in range(n):
        p = poly[i]
        q = poly[(i + 1) % n]
        fp = a[0] * p[0] + a[1] * p[1] - c
        fq = a[0] * q[0] + a[1] * q[1] - c
        if fp <= 1e-9:
            out.append(p)
            if fq > 1e-9:
                out.append(_line_intersect(p, q, a, c))
        elif fq <= 1e-9:
            out.append(_line_intersect(p, q, a, c))
    return out


def _polygon_area(poly) -> float:
    if len(poly) < 3:
        return 0.0
    total = 0.0
    for i in range(len(poly)):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % len(poly)]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def _polygon_centroid(poly) -> tuple[float, float]:
    if not poly:
        return (0.0, 0.0)
    total = 0.0
    cx = cy = 0.0
    for i in range(len(poly)):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % len(poly)]
        cross = x1 * y2 - x2 * y1
        total += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if abs(total) < 1e-9:
        return (sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly))
    total *= 0.5
    return (cx / (6 * total), cy / (6 * total))


def _vertical_span(poly, x: float) -> tuple[float, float]:
    """竖直线 x 与多边形的上下交点（凸多边形，保证有序）。"""
    ys: list[float] = []
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (x1 <= x <= x2) or (x2 <= x <= x1):
            if abs(x2 - x1) < 1e-9:
                ys.append(y1)
                continue
            t = (x - x1) / (x2 - x1)
            ys.append(y1 + t * (y2 - y1))
    if not ys:
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        return (min(ys), max(ys))
    return (min(ys), max(ys))


_GEOM_CACHE: dict | None = None


def _geometry() -> dict | None:
    """计算州郡几何：多边形、邻接、质心、标签锚点。全进程只算一次。"""
    global _GEOM_CACHE
    if _GEOM_CACHE is not None:
        return _GEOM_CACHE

    data = load_dataset()
    hull_pts = [(float(x), float(y)) for x, y in data.get("hull", [])]
    provinces = data.get("provinces", [])
    if len(hull_pts) < 3 or not provinces:
        return None

    hull = _convex_hull(hull_pts)
    seeds = [(float(p["seed"][0]), float(p["seed"][1])) for p in provinces]

    cells: list[list[tuple[float, float]]] = []
    for i, si in enumerate(seeds):
        poly = list(hull)
        for j, sj in enumerate(seeds):
            if i == j:
                continue
            a = (sj[0] - si[0], sj[1] - si[1])
            c = (sj[0] ** 2 + sj[1] ** 2 - si[0] ** 2 - si[1] ** 2) / 2.0
            poly = _clip_halfplane(poly, a, c)
            if not poly:
                break
        cells.append(poly)

    # 邻接：两个州郡的多边形共边（同一条 Voronoi 中垂线上的边）
    neighbors: list[list[int]] = [set() for _ in seeds]  # type: ignore[assignment]
    for i, poly in enumerate(cells):
        for k in range(len(poly)):
            p = poly[k]
            q = poly[(k + 1) % len(poly)]
            for j, sj in enumerate(seeds):
                if i == j:
                    continue
                a = (sj[0] - seeds[i][0], sj[1] - seeds[i][1])
                c = (sj[0] ** 2 + sj[1] ** 2 - seeds[i][0] ** 2 - seeds[i][1] ** 2) / 2.0
                if (
                    abs(a[0] * p[0] + a[1] * p[1] - c) < 1e-6
                    and abs(a[0] * q[0] + a[1] * q[1] - c) < 1e-6
                ):
                    neighbors[i].add(j)
                    break

    out_provinces = []
    for i, cell in enumerate(cells):
        area = _polygon_area(cell)
        centroid = _polygon_centroid(cell)
        y_top, y_bottom = _vertical_span(cell, centroid[0])
        xs = [p[0] for p in cell] or [0.0]
        out_provinces.append(
            {
                "name": str(provinces[i].get("name") or f"州{i}"),
                "seed": seeds[i],
                "poly": cell,
                "area": area,
                "centroid": centroid,
                "label": (y_bottom - y_top) >= 17 and (max(xs) - min(xs)) >= 26,
                "top_vertex": min(cell, key=lambda p: p[1]) if cell else (0.0, 0.0),
            }
        )

    geom = {
        "provinces": out_provinces,
        "neighbors": [sorted(n) for n in neighbors],
        "factions": data.get("factions", []),
        "styles": [str(f.get("style") or "solid") for f in data.get("factions", [])],
        "colors": [str(f.get("color") or "white") for f in data.get("factions", [])],
        "seeds": [list(f.get("seed") or [0, 0]) for f in data.get("factions", [])],
        "neutral_names": set(data.get("neutral", [])),
    }
    _GEOM_CACHE = geom
    return geom


# ── 仿真：日期确定性推进 ─────────────────────────────────────
def _epoch() -> date:
    data = load_dataset()
    try:
        return datetime.strptime(str(data.get("epoch")), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return date(2026, 10, 28)


def _day_rng(day_index: int) -> random.Random:
    """hash(epoch, 第 N 天) → 稳定随机源（跨设备、跨进程一致）。"""
    raw = f"{_epoch().isoformat()}|{day_index}".encode("utf-8")
    seed = int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")
    return random.Random(seed)


def _home_provinces(geom: dict) -> list[int]:
    """龙兴之地：每个势力离自己坐标最近的州郡。"""
    homes: list[int] = []
    for fs in geom["seeds"]:
        fx, fy = float(fs[0]), float(fs[1])
        best = -1
        best_d = None
        for i, p in enumerate(geom["provinces"]):
            if p["name"] in geom["neutral_names"]:
                continue
            sx, sy = p["seed"]
            d = (sx - fx) ** 2 + (sy - fy) ** 2
            if best_d is None or d < best_d:
                best, best_d = i, d
        homes.append(best)
    return homes


def _initial_world(geom: dict) -> dict:
    factions = geom["factions"]
    owners: list[int | None] = []
    for p in geom["provinces"]:
        if p["name"] in geom["neutral_names"]:
            owners.append(None)
            continue
        best = 0
        best_d = None
        for f, fs in enumerate(geom["seeds"]):
            fx, fy = float(fs[0]), float(fs[1])
            sx, sy = p["seed"]
            d = (sx - fx) ** 2 + (sy - fy) ** 2
            if best_d is None or d < best_d:
                best, best_d = f, d
        owners.append(best)
    return {
        "owner": owners,
        "alive": [True] * len(factions),
        "morale": [60] * len(factions),
        "revive_in": [0] * len(factions),
        "home": _home_provinces(geom),
    }


def _copy_world(world: dict) -> dict:
    return {
        "owner": list(world["owner"]),
        "alive": list(world["alive"]),
        "morale": list(world["morale"]),
        "revive_in": list(world["revive_in"]),
        "home": list(world["home"]),
    }


def _simulate_day(world: dict, rng: random.Random, geom: dict) -> list[dict]:
    """推进一天，返回当日事件。"""
    owners = world["owner"]
    neighbors = geom["neighbors"]
    events: list[dict] = []

    for f in range(len(world["alive"])):
        if not world["alive"][f]:
            continue
        mine = [i for i, o in enumerate(owners) if o == f]
        if not mine:
            continue
        cands: list[int] = []
        for i in mine:
            for j in neighbors[i]:
                if owners[j] != f and j not in cands:
                    cands.append(j)
        if not cands:
            continue

        def weight(j: int) -> float:
            w = 1.0
            if owners[j] is None:
                w += 1.2  # 荒原最好打
            else:
                d = owners[j]
                count_d = sum(1 for o in owners if o == d)
                w += max(0.0, 9 - count_d) * 0.12  # 弱邻优先
            return w * (0.75 + rng.random() * 0.5)

        target = max(cands, key=weight)
        defender = owners[target]
        atk = (
            _STR_BASE
            + _STR_MORALE * world["morale"][f]
            - _OVEREXT * max(0, len(mine) - _OVEREXT_PIVOT)
            + _ATK_EDGE
            + rng.uniform(-_NOISE, _NOISE)
        )
        if defender is None:
            dfn = _STR_BASE - 0.05 + rng.uniform(-0.10, 0.10)
        else:
            count_d = sum(1 for o in owners if o == defender)
            dfn = (
                _STR_BASE
                + _STR_MORALE * world["morale"][defender]
                - _OVEREXT * max(0, count_d - _OVEREXT_PIVOT)
                + (_HOME_BONUS if target == world["home"][defender] else 0.0)
                + rng.uniform(-_NOISE, _NOISE)
            )
        if atk >= dfn:
            taken = [target]
            if defender is not None and (atk - dfn) > _SECOND_PUSH:
                extra = [j for j in neighbors[target] if owners[j] == defender]
                if extra:
                    taken.append(max(extra, key=lambda _j: rng.random()))
            for t in taken:
                owners[t] = f
            world["morale"][f] = max(10, min(100, world["morale"][f] + _MORALE_WIN))
            if defender is not None:
                world["morale"][defender] = max(5, world["morale"][defender] - _MORALE_LOSE)
            events.append(
                {"kind": "battle", "attacker": f, "defender": defender, "places": taken}
            )
        else:
            world["morale"][f] = max(5, world["morale"][f] - _MORALE_LOSE)
            world["morale"][defender] = max(5, world["morale"][defender] + _MORALE_WIN)
            events.append(
                {"kind": "repel", "attacker": f, "defender": defender, "places": []}
            )

    for f in range(len(world["alive"])):
        if world["alive"][f] and not any(o == f for o in owners):
            world["alive"][f] = False
            world["revive_in"][f] = rng.randint(9, 20)
            world["morale"][f] = 40
            events.append({"kind": "fallen", "attacker": None, "defender": f, "places": []})

    for f in range(len(world["alive"])):
        if world["alive"][f]:
            continue
        world["revive_in"][f] -= 1
        if world["revive_in"][f] <= 0:
            home = world["home"][f]
            owners[home] = f
            world["alive"][f] = True
            world["morale"][f] = 55
            events.append(
                {"kind": "revive", "attacker": f, "defender": None, "places": [home]}
            )

    total = len(owners)
    for f in range(len(world["alive"])):
        if world["alive"][f] and sum(1 for o in owners if o == f) == total:
            events.append({"kind": "unify", "attacker": f, "defender": None, "places": []})
            break
    return events


_SIM_CACHE: dict[int, dict] = {}


def _state_at(day_index: int, geom: dict) -> dict:
    """回放到第 day_index 天结束时的世界状态（带内存缓存，不落盘）。"""
    if day_index < 0:
        return _initial_world(geom)
    if day_index in _SIM_CACHE:
        return _copy_world(_SIM_CACHE[day_index])

    base_day = 0
    base_state = _initial_world(geom)
    for d in sorted(_SIM_CACHE):
        if 0 <= d <= day_index and d > base_day:
            base_day = d
            base_state = _SIM_CACHE[d]

    state = _copy_world(base_state)
    for d in range(base_day + 1, day_index + 1):
        _simulate_day(state, _day_rng(d), geom)
        if len(_SIM_CACHE) > 96:
            for key in sorted(_SIM_CACHE)[:48]:
                _SIM_CACHE.pop(key, None)
        _SIM_CACHE[d] = _copy_world(state)
    return state


def simulate(day_index: int) -> tuple[dict, list[dict], list[int]]:
    """返回（当天结束的世界、当日事件、当日易主州郡下标）。"""
    geom = _geometry()
    if geom is None:
        return ({}, [], [])
    if day_index <= 0:
        world = _initial_world(geom)
        homes = list(world["home"])
        events = [{"kind": "founding", "attacker": None, "defender": None, "places": homes}]
        return world, events, homes

    world = _state_at(day_index - 1, geom)
    before = list(world["owner"])
    events = _simulate_day(world, _day_rng(day_index), geom)
    changed = [i for i, o in enumerate(world["owner"]) if o != before[i]]
    return world, events, changed


# ── 战报：本地模板 + 词库拼装 ────────────────────────────────
def _day_index(today: date) -> int:
    return (today - _epoch()).days


def _build_report(geom: dict, events: list[dict], changed: list[int], day_index: int):
    """生成两行战报：叙事行 + 易主名单行。

    约束：战报里出现的州郡名，必须恰好等于当日地图上被标记的易主郡。
    """
    data = load_dataset()
    templates = data.get("templates", {}) or {}
    labels = data.get("report", {}) or {}
    names = [p["name"] for p in geom["provinces"]]
    beasts = [str(f.get("name") or "") for f in geom["factions"]]
    changed_set = set(changed)

    if day_index <= 0:
        line1 = str(templates.get("founding", ["四方立国。"])[0])
        line2 = str(labels.get("founded_label", "立国：")) + "、".join(
            names[i] for i in changed
        )
        return line1, line2

    rng = _day_rng(day_index)
    clauses: list[str] = []
    for ev in events:
        kind = ev["kind"]
        pool = templates.get(kind) or []
        if not pool:
            continue
        tpl = str(pool[rng.randrange(len(pool))])
        atk = ev.get("attacker")
        dfn = ev.get("defender")
        places = [p for p in ev.get("places", []) if p in changed_set]
        if kind == "battle":
            if not places:
                continue
            if dfn is None:
                pool = templates.get("expand") or []
                if not pool:
                    continue
                tpl = str(pool[rng.randrange(len(pool))])
                clause = tpl.format(a=beasts[atk], p=names[places[0]])
            else:
                clause = tpl.format(a=beasts[atk], d=beasts[dfn], p=names[places[0]])
        elif kind == "repel":
            clause = tpl.format(a=beasts[atk], d=beasts[dfn])
        elif kind == "fallen":
            clause = tpl.format(a=beasts[dfn])
        elif kind == "revive":
            if not places:
                continue
            clause = tpl.format(a=beasts[atk], p=names[places[0]])
        elif kind == "unify":
            clause = tpl.format(a=beasts[atk])
        else:
            continue
        clauses.append(clause)

    if not clauses:
        clauses.append(str(templates.get("calm", ["四方无战。"])[0]))
    line1 = "；".join(clauses[:2])
    line2 = ""
    if changed:
        line2 = str(labels.get("changed_label", "易主：")) + "、".join(
            names[i] for i in changed
        )
    return line1, line2


# ── 渲染 ─────────────────────────────────────────────────────
def _target_size(screen_w: int, screen_h: int) -> tuple[int, int]:
    """与 json_renderer 的缩放规则完全一致，保证图片不发生二次重采样。"""
    key = f"{int(screen_w)}x{int(screen_h)}"
    logical_w, logical_h = LOGICAL_BLOCKS.get(key, (DESIGN_W, DESIGN_H))
    scale = max(0.92, screen_w / 400.0)
    return int(logical_w * scale), int(logical_h * scale)


def _draw_text(canvas: Image.Image, xy, text: str, font, fill=BLACK) -> None:
    """以二值掩膜落字，保证文字只有纯黑/纯白，不产生中间灰。"""
    if not text:
        return
    mask = Image.new("1", canvas.size, 0)
    d = ImageDraw.Draw(mask)
    try:
        d.fontmode = "1"
    except Exception:  # pragma: no cover - 老版本 Pillow 无 fontmode
        pass
    d.text(xy, text, font=font, fill=1)
    canvas.paste(fill, (0, 0), mask)


def _text_plate(
    canvas_size: tuple[int, int],
    xy: tuple[float, float],
    text: str,
    font,
    pad: int = 1,
) -> Image.Image | None:
    """文字白底板掩膜：紧贴字形外扩 pad 像素。

    用途：黑字压红底只有 3.46:1 对比度（AA-large），垫白底后升到 21:1；
    文字区从此只有纯白+纯黑两色，不受底图网点混色干扰。
    返回 None 表示文字为空或渲染失败（调用方跳过铺底）。
    """
    if not text:
        return None
    mask = Image.new("1", canvas_size, 0)
    d = ImageDraw.Draw(mask)
    try:
        d.fontmode = "1"
    except Exception:  # pragma: no cover - 老版本 Pillow 无 fontmode
        pass
    d.text(xy, text, font=font, fill=1)
    bbox = mask.getbbox()
    if bbox is None:
        return None
    plate = Image.new("L", canvas_size, 0)
    ImageDraw.Draw(plate).rectangle(
        [bbox[0] - pad, bbox[1] - pad, bbox[2] + pad - 1, bbox[3] + pad - 1],
        fill=255,
    )
    return plate


def _text_width(text: str, font) -> float:
    try:
        return float(ImageDraw.Draw(Image.new("1", (1, 1))).textlength(text, font=font))
    except Exception:  # pragma: no cover
        return float(len(text) * 12)


def _pattern_layer(size: tuple[int, int], style: str, pitch: float) -> Image.Image:
    """黑白网点 / 斜纹层（白底黑线）。

    两种纹样必须一眼可分：斜纹密而细，网点疏而粗。图例色块只有十几个像素，
    密斜纹与疏网点在小尺寸下才认得出。
    """
    w, h = size
    layer = Image.new("RGB", (w, h), WHITE)
    d = ImageDraw.Draw(layer)
    if style == "hatch":
        step = max(3, int(round(pitch)))
        for offset in range(-h, w + h, step):
            d.line([(offset, 0), (offset + h, h)], fill=BLACK, width=1)
    else:
        step = max(4, int(round(pitch)))
        for y in range(0, h + step, step):
            for x in range(0, w + step, step):
                d.rectangle([x, y, x + 1, y + 1], fill=BLACK)
    return layer


def _pick_font_size(design_size: int, u: float) -> int:
    """位图字体只有 12/13/14/16 几档，优先贴住设计尺寸以保持墨水屏上的清晰度。"""
    target = design_size * u
    for cand in (12, 13, 14, 16):
        if cand >= target:
            return cand
    return max(8, int(round(target)))


def _wrap_text(text: str, font, max_w: float) -> list[str]:
    """按「、」断行，保证拼回去与原文一字不差（战报与地图的对应关系不能丢字）。"""
    if not text:
        return []
    if _text_width(text, font) <= max_w:
        return [text]
    parts = text.split("、")
    lines: list[str] = []
    cur = ""
    for part in parts:
        cand = part if not cur else cur + "、" + part
        if not cur or _text_width(cand, font) <= max_w:
            cur = cand
        else:
            lines.append(cur + "、")
            cur = part
    if cur:
        lines.append(cur)
    return lines


def _viewport(size: tuple[int, int]) -> dict:
    """算出可视区与缩放。

    400x300 是设计稿，u=1 像素级不变；其它尺寸按统一比例缩放并居中，
    不做横竖各自拉伸（那会把地图压扁）。
    短屏（如 296x128）塞不下图例与战报，只画地图本体。
    """
    tw, th = size
    design = load_dataset().get("design", {}) or {}
    header_bottom = float(design.get("header_bottom", 44))
    report_top = float(design.get("report_top", 248))
    compact = th < 200
    if compact:
        vx0, vy0, vx1, vy1 = 0.0, header_bottom - 2.0, DESIGN_W, report_top - 2.0
    else:
        vx0, vy0, vx1, vy1 = 0.0, 0.0, DESIGN_W, DESIGN_H
    vw, vh = vx1 - vx0, vy1 - vy0
    u = min(tw / vw, th / vh)
    return {
        "vx0": vx0,
        "vy0": vy0,
        "u": u,
        "ox": (tw - vw * u) / 2.0,
        "oy": (th - vh * u) / 2.0,
        "compact": compact,
    }


def _marker_positions(geom: dict, changed: list[int], size: tuple[int, int]) -> list[tuple[float, float]]:
    """当日易主州郡的黑色标记位置（钉在州郡顶部内侧，保证落在州内）。"""
    vp = _viewport(size)
    vx0, vy0, u, ox, oy = (
        vp["vx0"], vp["vy0"], vp["u"], vp["ox"], vp["oy"],
    )
    out: list[tuple[float, float]] = []
    for i in changed:
        p = geom["provinces"][i]
        tx, ty = p["top_vertex"]
        cx, cy = p["centroid"]
        vx, vy = tx - cx, ty - cy
        norm = (vx * vx + vy * vy) ** 0.5 or 1.0
        mx = (tx - vx / norm * 7 - vx0) * u + ox
        my = (ty - vy / norm * 7 - vy0) * u + oy
        out.append((mx, my))
    return out


def _render_screen(
    geom: dict,
    world: dict,
    changed: list[int],
    line1: str,
    line2: str,
    today: date,
    day_index: int,
    size: tuple[int, int],
) -> bytes:
    """渲染整屏：顶栏（日期+图例）/ 地图 / 底部战报。"""
    tw, th = size
    design = load_dataset().get("design", {}) or {}
    report_top = float(design.get("report_top", 248))
    line_pitch = float(design.get("report_line_pitch", 14))
    max_report_lines = int(design.get("report_lines", 3))
    dot_pitch = float(design.get("dot_pitch", 7))
    hatch_pitch = float(design.get("hatch_pitch", 4))

    vp = _viewport(size)
    vx0, vy0, u, ox, oy = vp["vx0"], vp["vy0"], vp["u"], vp["ox"], vp["oy"]
    compact = vp["compact"]

    def X(x: float) -> float:
        return (x - vx0) * u + ox

    def Y(y: float) -> float:
        return (y - vy0) * u + oy

    canvas = Image.new("RGB", (tw, th), WHITE)
    draw = ImageDraw.Draw(canvas)

    provinces = geom["provinces"]
    owners = world.get("owner", [])

    # 1) 州郡填充
    for i, p in enumerate(provinces):
        pts = [(X(x), Y(y)) for x, y in p["poly"]]
        if not pts:
            continue
        owner = owners[i] if i < len(owners) else None
        if owner is None:
            style = _NEUTRAL_STYLE
            color = WHITE
        else:
            style = geom["styles"][owner]
            color = _COLOR_MAP.get(geom["colors"][owner], WHITE)
        if style == "solid":
            draw.polygon(pts, fill=color)
        else:
            mask = Image.new("1", (tw, th), 0)
            ImageDraw.Draw(mask).polygon(pts, fill=1)
            pitch = (hatch_pitch if style == "hatch" else dot_pitch) * u
            layer = _pattern_layer((tw, th), style, pitch)
            canvas.paste(layer, (0, 0), mask)

    # 2) 州界（纯黑 1px）。存成掩膜，后续要在文字白底板上重画穿过的界
    border_w = max(1, int(round(u)))
    border_mask = Image.new("L", (tw, th), 0)
    bdraw = ImageDraw.Draw(border_mask)
    for p in provinces:
        pts = [(X(x), Y(y)) for x, y in p["poly"]]
        for k in range(len(pts)):
            bdraw.line([pts[k], pts[(k + 1) % len(pts)]], fill=255, width=border_w)
    canvas.paste(BLACK, (0, 0), border_mask)

    fs_small = _pick_font_size(12, u)
    fs_bold = _pick_font_size(13, u)
    font_name = load_font("noto_serif_regular", fs_small)
    font_bold = load_font("noto_serif_bold", fs_bold)

    # 3) 州郡名（短屏省略：小屏放不下名字，挤在一起反而看不清）
    #    文字垫白底：黑字直接压红底只有 3.46:1，垫白底后 21:1；
    #    且文字区从此只有纯白+纯黑两色，不受任何网点混色干扰。
    label_items: list[tuple[str, tuple[float, float]]] = []
    if not compact:
        for p in provinces:
            if not p.get("label"):
                continue
            cx, cy = X(p["centroid"][0]), Y(p["centroid"][1])
            w = _text_width(p["name"], font_name)
            label_items.append((p["name"], (cx - w / 2, cy - fs_small / 2)))

    plates: list[Image.Image] = []
    for name, xy in label_items:
        plate = _text_plate(canvas.size, xy, name, font_name)
        if plate is not None:
            canvas.paste(WHITE, (0, 0), plate)
            plates.append(plate)
    # 白底板会盖住穿过的州界，把这段界在板上补回来（只重画板内部分）
    if plates:
        union = plates[0]
        for extra in plates[1:]:
            union = ImageChops.lighter(union, extra)
        canvas.paste(BLACK, (0, 0), ImageChops.multiply(border_mask, union))
    # 字最后画：与界重叠时以字为准，保证可读
    for name, xy in label_items:
        _draw_text(canvas, xy, name, font_name)

    # 4) 当日易主标记（黑色实心圆，钉在州郡顶部内侧）
    r = max(2, int(round(4 * u)))
    for mx, my in _marker_positions(geom, changed, (tw, th)):
        draw.ellipse([mx - r, my - r, mx + r, my + r], fill=BLACK)

    if compact:
        buf = BytesIO()
        canvas.save(buf, format="PNG", optimize=True)
        return buf.getvalue()

    # 5) 顶栏：日期 + 图例
    weekday = ["一", "二", "三", "四", "五", "六", "日"][today.weekday()]
    date_text = f"{today.month}月{today.day}日 周{weekday}"
    _draw_text(canvas, (X(8), Y(4)), date_text, font_bold)
    day_text = f"第{day_index + 1}日"
    dw = _text_width(day_text, font_bold)
    _draw_text(canvas, (X(DESIGN_W - 8) - dw, Y(4)), day_text, font_bold)

    counts = [0] * len(geom["factions"])
    for o in owners:
        if o is not None and 0 <= o < len(counts):
            counts[o] += 1
    neutral_count = sum(1 for o in owners if o is None)

    entries: list[tuple[str, str, int, str]] = []
    for f, fac in enumerate(geom["factions"]):
        entries.append(
            (
                str(fac.get("name") or ""),
                geom["styles"][f],
                counts[f],
                str(geom["colors"][f]),
            )
        )
    if neutral_count:
        entries.append(
            (str(load_dataset().get("neutral_name", "荒原")), _NEUTRAL_STYLE, neutral_count, "white")
        )

    x = 8.0
    sw = 12.0
    for name, style, count, color_key in entries:
        sx0, sy0 = X(x), Y(22)
        sw0 = sw * u
        sh0 = sw * u
        if style == "solid":
            draw.rectangle([sx0, sy0, sx0 + sw0, sy0 + sh0], fill=_COLOR_MAP.get(color_key, WHITE))
            draw.rectangle([sx0, sy0, sx0 + sw0, sy0 + sh0], outline=BLACK, width=1)
        else:
            mask = Image.new("1", (tw, th), 0)
            ImageDraw.Draw(mask).rectangle([sx0, sy0, sx0 + sw0, sy0 + sh0], fill=1)
            pitch = (hatch_pitch if style == "hatch" else dot_pitch) * u
            layer = _pattern_layer((tw, th), style, pitch)
            canvas.paste(layer, (0, 0), mask)
            draw.rectangle([sx0, sy0, sx0 + sw0, sy0 + sh0], outline=BLACK, width=1)
        _draw_text(canvas, (X(x + sw + 4), Y(21)), name, font_name)
        cnt = str(count)
        name_w = _text_width(name, font_name) / u
        cnt_w = _text_width(cnt, font_name) / u
        nx = x + sw + 4 + name_w + 3
        _draw_text(canvas, (X(nx), Y(21)), cnt, font_name)
        x = nx + cnt_w + 9

    # 6) 底部分隔线 + 战报（过长自动折行，不丢字）
    draw.line([(X(6), Y(report_top)), (X(DESIGN_W - 6), Y(report_top))], fill=BLACK, width=1)
    margin = 8 * u
    max_w = tw - 2 * margin
    report_lines: list[str] = []
    for text in (line1, line2):
        report_lines.extend(_wrap_text(text, font_name, max_w))
    for idx, text in enumerate(report_lines[:max_report_lines]):
        _draw_text(canvas, (X(8), Y(report_top + 5 + idx * line_pitch)), text, font_name)

    buf = BytesIO()
    canvas.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ── provider 入口 ────────────────────────────────────────────
def _resolve_today(date_ctx: dict | None) -> date:
    ctx = date_ctx or {}
    try:
        year = int(ctx.get("year"))
        month = int(ctx.get("month"))
        day = int(ctx.get("day"))
        return date(year, month, day)
    except (TypeError, ValueError):
        return date.today()


async def build_eworld_content(fallback: dict | None = None, **kwargs) -> dict:
    """电子世界模式内容生成（纯本地仿真）。"""
    content = dict(fallback or {})
    geom = _geometry()
    if geom is None:
        logger.warning("[EWorld] 数据集不可用，回落到 fallback")
        content.setdefault("map_url", "")
        return content

    today = _resolve_today(kwargs.get("date_ctx"))
    day_index = max(0, _day_index(today))
    world, events, changed = simulate(day_index)
    line1, line2 = _build_report(geom, events, changed, day_index)

    screen_w = int(kwargs.get("screen_w") or DESIGN_W)
    screen_h = int(kwargs.get("screen_h") or DESIGN_H)
    size = _target_size(screen_w, screen_h)
    png = _render_screen(geom, world, changed, line1, line2, today, day_index, size)

    names = [p["name"] for p in geom["provinces"]]
    counts = [0] * len(geom["factions"])
    for o in world["owner"]:
        if o is not None and 0 <= o < len(counts):
            counts[o] += 1

    content.update(
        {
            "map_url": "/runtime_eworld/map.png",
            "_prefetched_map_url": png,
            "title": "电子世界",
            "subtitle": f"第{day_index + 1}日",
            "report_line": line1,
            "report_line2": line2,
            "day_index": day_index,
            "day_number": day_index + 1,
            "date_line": today.isoformat(),
            "changed_names": [names[i] for i in changed],
            "faction_counts": {
                str(f.get("name")): counts[i] for i, f in enumerate(geom["factions"])
            },
        }
    )
    return content
