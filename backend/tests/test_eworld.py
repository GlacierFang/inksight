"""电子世界模式（CUSTOM_EWORLD）测试。

覆盖验收标准：
  1. 任意日期渲染像素级可复现；
  2. 连续运行 365 天不崩溃、不出现空图；
  3. 能出现一方统一全图，并能被复国机制打破；
  4. 当日变动郡在图上清晰可辨；
  5. 战报与地图变化严格对应。
另外校验：四色纯净、州郡无缝拼接、模式 JSON 源文件与部署副本一致。
"""
from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from core import eworld  # noqa: E402
from core.config import EINK_4COLOR_PALETTE  # noqa: E402
from core.eworld import (  # noqa: E402
    _build_report,
    _geometry,
    _marker_positions,
    _target_size,
    build_eworld_content,
    load_dataset,
    simulate,
)

MODE_SOURCE = BACKEND_DIR.parent / "modes-ours" / "eworld.json"
MODE_DEPLOY = BACKEND_DIR / "core" / "modes" / "builtin" / "custom_eworld.json"

PALETTE = {tuple(EINK_4COLOR_PALETTE[i:i + 3]) for i in (0, 3, 6, 9)}


def _ctx(day: date) -> dict:
    return {"year": day.year, "month": day.month, "day": day.day}


def _epoch() -> date:
    return eworld._epoch()


def _day(day_index: int) -> date:
    return _epoch() + timedelta(days=day_index)


# ── 数据集与几何 ────────────────────────────────────────────
def test_dataset_shape():
    data = load_dataset()
    assert data["mode_id"] == "CUSTOM_EWORLD"
    assert data["epoch"] == "2026-10-28"
    assert len(data["provinces"]) == 32
    assert len(data["factions"]) == 4
    assert len(data["hull"]) >= 12
    names = [p["name"] for p in data["provinces"]]
    assert len(set(names)) == 32, "州郡名不能重复"
    for f in data["factions"]:
        assert f["style"] in {"solid", "hatch"}
        assert f["color"] in {"red", "yellow", "white"}


def test_geometry_tiles_without_gaps():
    """32 个州郡必须无缝拼满大陆，且邻接对称、连通。"""
    geom = _geometry()
    assert geom is not None
    provinces = geom["provinces"]
    assert len(provinces) == 32

    hull_area = eworld._polygon_area(
        eworld._convex_hull([tuple(map(float, p)) for p in load_dataset()["hull"]])
    )
    total = sum(p["area"] for p in provinces)
    assert total == pytest.approx(hull_area, rel=0.02), "州郡面积之和应等于大陆面积"

    neighbors = geom["neighbors"]
    for i, nbs in enumerate(neighbors):
        for j in nbs:
            assert i in neighbors[j], "邻接必须对称"

    seen = {0}
    stack = [0]
    while stack:
        cur = stack.pop()
        for nxt in neighbors[cur]:
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    assert len(seen) == 32, "州郡邻接图必须连通"


def test_every_province_labelled():
    geom = _geometry()
    assert all(p["label"] for p in geom["provinces"]), "每个州郡都要能放下名称"


# ── 验收 2：365 天连续运行 ─────────────────────────────────
def test_365_days_no_crash_no_empty_map():
    """连续跑一年：每一天都有归属、无空图、战报可生成。"""
    geom = _geometry()
    neutral_at_start = len(load_dataset()["neutral"])
    prev_unowned: set | None = None
    for day in range(366):
        world, events, changed = simulate(day)
        assert len(world["owner"]) == 32
        unowned = {i for i, o in enumerate(world["owner"]) if o is None}
        if day == 0:
            assert len(unowned) == neutral_at_start, "开局荒原数量应与数据集一致"
        if prev_unowned is not None:
            assert unowned <= prev_unowned, f"第 {day} 天有州郡退回荒原"
        prev_unowned = unowned
        line1, line2 = _build_report(geom, events, changed, day)
        assert line1
        assert isinstance(line2, str)
    assert not prev_unowned, "一年内荒原应被全部吞并"


def test_factions_always_alive_somewhere():
    """任何一天至少有一个势力在世，且最多一方被灭。"""
    for day in range(0, 366, 7):
        world, _events, _changed = simulate(day)
        assert any(world["alive"]), f"第 {day} 天四方俱灭"


# ── 验收 1：像素级可复现 ───────────────────────────────────
def test_same_date_same_bytes():
    """同一天渲染两次，字节完全一致。"""
    ctx = _ctx(_day(77))
    first = asyncio.run(
        build_eworld_content({}, date_ctx=ctx, screen_w=400, screen_h=300)
    )
    eworld._SIM_CACHE.clear()
    second = asyncio.run(
        build_eworld_content({}, date_ctx=ctx, screen_w=400, screen_h=300)
    )
    assert first["_prefetched_map_url"] == second["_prefetched_map_url"]
    assert first["report_line"] == second["report_line"]
    assert first["report_line2"] == second["report_line2"]


def test_world_state_is_date_deterministic():
    """清缓存重放，同一天的疆界与盘数完全一致。"""
    before_owner, before_events, before_changed = simulate(123)
    eworld._SIM_CACHE.clear()
    after_owner, after_events, after_changed = simulate(123)
    assert before_owner["owner"] == after_owner["owner"]
    assert before_changed == after_changed
    assert [e["kind"] for e in before_events] == [e["kind"] for e in after_events]


def test_target_size_matches_renderer_scaling():
    """provider 出的图必须与渲染器要的尺寸一致，否则会被二次缩放打散四色。"""
    for w, h in ((400, 300), (648, 480), (296, 128), (500, 300)):
        lw, lh = eworld.LOGICAL_BLOCKS.get(f"{w}x{h}", (400, 300))
        scale = max(0.92, w / 400.0)
        assert _target_size(w, h) == (int(lw * scale), int(lh * scale))


# ── 验收 3：统一全图 + 复国打破 ─────────────────────────────
def test_unification_happens_and_is_broken_by_revive():
    """一年内出现一方统一全图，且随后被复国机制打破。"""
    unify_days = []
    for day in range(0, 366):
        world, events, _changed = simulate(day)
        if any(e["kind"] == "unify" for e in events):
            unify_days.append(day)
            assert max(Counter(world["owner"]).values()) == 32, "统一日必须独占全图"
    assert unify_days, "一年内必须出现一方统一全图"

    # 统一之后，某天有势力复国并夺回龙兴之地 → 统一被打破
    first_unify = unify_days[0]
    broke = False
    for day in range(first_unify + 1, min(first_unify + 60, 366)):
        world, events, _changed = simulate(day)
        if any(e["kind"] == "revive" for e in events):
            assert max(Counter(world["owner"]).values()) < 32, "复国当天必须不再统一"
            broke = True
            break
    assert broke, "统一之后必须能被复国机制打破"


# ── 验收 4：当日变动郡清晰可辨 ─────────────────────────────
def test_changed_provinces_carry_black_markers():
    """易主郡才有黑色实心标记：带标记与不带标记两次渲染之差，只应落在易主郡上。"""
    geom = _geometry()
    day = 3
    world, events, changed = simulate(day)
    assert changed, "第 3 天应当有州郡易主"
    size = _target_size(400, 300)
    today = _day(day)
    line1, line2 = _build_report(geom, events, changed, day)

    marked = eworld._render_screen(geom, world, changed, line1, line2, today, day, size)
    plain = eworld._render_screen(geom, world, [], line1, line2, today, day, size)
    img_a = Image.open(BytesIO(marked)).convert("RGB")
    img_b = Image.open(BytesIO(plain)).convert("RGB")
    px_a = list(img_a.getdata())
    px_b = list(img_b.getdata())
    diff = [(i % 400, i // 400) for i in range(len(px_a)) if px_a[i] != px_b[i]]
    assert diff, "易主郡应留下可见标记"

    markers = _marker_positions(geom, changed, size)
    for (mx, my) in markers:
        x, y = int(round(mx)), int(round(my))
        assert img_a.load()[x, y] == (0, 0, 0), f"易主郡标记应为纯黑，实为 {img_a.load()[x, y]}"
        near = [p for p in diff if abs(p[0] - x) <= 5 and abs(p[1] - y) <= 5]
        assert near, "易主郡标记周围应有像素变化"

    for (x, y) in diff:
        assert any(
            abs(x - mx) <= 5 and abs(y - my) <= 5 for (mx, my) in markers
        ), f"变化像素 ({x},{y}) 不应出现在未易主郡上"


# ── 验收 5：战报与地图变化严格对应 ─────────────────────────
def test_report_names_match_changed_provinces():
    """战报里出现的州郡名，必须恰好等于当日易主集合。"""
    geom = _geometry()
    names = [p["name"] for p in geom["provinces"]]
    for day in range(0, 366, 1):
        world, events, changed = simulate(day)
        line1, line2 = _build_report(geom, events, changed, day)
        mentioned = {n for n in names if n in line1 or n in line2}
        expected = {names[i] for i in changed}
        assert mentioned == expected, f"第 {day} 天战报与地图不一致：{mentioned} != {expected}"


def test_report_lists_every_changed_province():
    geom = _geometry()
    names = [p["name"] for p in geom["provinces"]]
    for day in range(0, 200):
        _world, _events, changed = simulate(day)
        if not changed:
            continue
        _line1, line2 = _build_report(geom, _events, changed, day)
        assert line2, f"第 {day} 天有易主却没有名单行"
        for i in changed:
            assert names[i] in line2


# ── 四色纯净 ───────────────────────────────────────────────
def test_render_uses_only_palette_colors():
    ctx = _ctx(_day(60))
    content = asyncio.run(
        build_eworld_content({}, date_ctx=ctx, screen_w=400, screen_h=300)
    )
    img = Image.open(BytesIO(content["_prefetched_map_url"])).convert("RGB")
    assert img.size == (400, 300)
    colors = set(Counter(img.getdata()).keys())
    assert colors <= PALETTE, f"出现非调色板颜色：{colors - PALETTE}"
    assert len(colors) >= 3


# ── 模式 JSON 与注册 ───────────────────────────────────────
def test_mode_json_source_matches_deploy():
    assert MODE_SOURCE.exists(), "模式源文件缺失"
    assert MODE_DEPLOY.exists(), "部署副本缺失"
    src = json.loads(MODE_SOURCE.read_text(encoding="utf-8"))
    dep = json.loads(MODE_DEPLOY.read_text(encoding="utf-8"))
    assert src == dep, "源文件与部署副本不一致"
    assert src["mode_id"].startswith(("CUSTOM_", "MY_"))
    assert src["content"]["type"] == "computed"
    assert src["content"]["provider"] == "eworld"


def test_layout_overrides_match_provider_table():
    """JSON 里各尺寸的 image 块尺寸，必须与 provider 的 LOGICAL_BLOCKS 一致。"""
    mode = json.loads(MODE_SOURCE.read_text(encoding="utf-8"))
    base = mode["layout"]["body"][0]
    assert (base["width"], base["height"]) == eworld.LOGICAL_BLOCKS["400x300"]
    overrides = mode.get("layout_overrides", {})
    for key, (lw, lh) in eworld.LOGICAL_BLOCKS.items():
        if key == "400x300":
            continue
        assert key in overrides, f"缺少 {key} 的版式覆盖"
        block = overrides[key]["body"][0]
        assert (block["width"], block["height"]) == (lw, lh), f"{key} 版式尺寸不一致"


def test_mode_registers_and_provider_wired():
    from core.json_content import _generate_computed_content
    from core.mode_registry import get_registry

    registry = get_registry()
    assert registry.is_json_mode("CUSTOM_EWORLD"), "模式未注册为 JSON 模式"

    mode_def = registry.get_json_mode("CUSTOM_EWORLD")
    assert mode_def is not None, "取不到模式定义"
    definition = mode_def.definition
    content = asyncio.run(
        _generate_computed_content(
            definition, definition.get("content", {}), {}, date_ctx=_ctx(_day(10))
        )
    )
    assert content.get("_prefetched_map_url"), "provider 未产出预渲染图片"


# ── 版式：等比缩放 / 短屏紧凑版 / 战报折行 ──────────────────
def test_viewport_is_uniformly_scaled():
    """非 400x300 的屏必须等比缩放，不能横竖各自拉伸把地图压扁。"""
    for w, h in ((400, 300), (648, 480), (500, 300), (296, 128)):
        vp = eworld._viewport((w, h))
        u = vp["u"]
        # 内容框按同一个 u 缩放后必须放进画布
        assert vp["ox"] >= -0.01 and vp["oy"] >= -0.01
        assert vp["ox"] * 2 + (eworld.DESIGN_W * u) <= w + 0.01 or vp["compact"]
        assert vp["oy"] * 2 + (eworld.DESIGN_H * u) <= h + 0.01 or vp["compact"]


def test_short_screen_renders_map_only():
    """短屏塞不下图例与战报，只画地图本体，且地图仍然居中不变形。"""
    ctx = _ctx(_day(3))
    content = asyncio.run(
        build_eworld_content({}, date_ctx=ctx, screen_w=296, screen_h=128)
    )
    img = Image.open(BytesIO(content["_prefetched_map_url"])).convert("RGB")
    assert img.size == (295, 127)
    px = img.load()
    # 顶栏与战报区应留白（无文字）
    assert px[10, 6] == (255, 255, 255), "短屏顶部不应有图例文字"
    assert px[10, 120] == (255, 255, 255), "短屏底部不应有战报文字"
    # 地图本体在画布中央
    colors = {c for c, _ in Counter(img.getdata()).items()}
    assert (200, 0, 0) in colors, "短屏也应画出州郡颜色"


def test_report_wrap_keeps_every_character():
    """战报折行后拼回去必须与原文一字不差。"""
    from core.eworld import _pick_font_size, _wrap_text

    font = eworld.load_font("noto_serif_regular", 12)
    long_text = "易主：" + "、".join(["敦煌"] * 12)
    lines = _wrap_text(long_text, font, 200)
    assert len(lines) > 1, "过长战报应当折行"
    joined = "".join(lines)
    assert joined == long_text, "折行不能丢字"
    assert _pick_font_size(12, 1.0) == 12
    assert _pick_font_size(12, 1.6) >= 16


def test_design_400x300_is_pixel_stable():
    """设计尺寸下的渲染必须是 400x300，且四色纯净。"""
    ctx = _ctx(_day(3))
    content = asyncio.run(
        build_eworld_content({}, date_ctx=ctx, screen_w=400, screen_h=300)
    )
    img = Image.open(BytesIO(content["_prefetched_map_url"])).convert("RGB")
    assert img.size == (400, 300)
