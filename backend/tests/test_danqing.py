"""「丹青谱」模式（CUSTOM_DANQING）测试。

覆盖：
  - 模式 JSON：源文件/部署副本合法且一致，catalog 已注册；
  - provider 接线：computed 通道能派发到 danqing；
  - 题签解析：LLM 输出按「字段：值」逐行取，缺字段用兜底；
  - 每日推进：同一天只造一次，跨天接着编号，题材轮转不重样；
  - 私印：印文画成朱文方印，印文变了就换新印；
  - 版式：题签字段都真的画出来。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

MODE_SOURCE = BACKEND_DIR.parent / "modes-ours" / "danqing.json"
MODE_DEPLOY = BACKEND_DIR / "core" / "modes" / "builtin" / "custom_danqing.json"

CONTENT_FIELDS = {
    "index_label", "title", "artist", "dynasty", "genre",
    "colophon", "seal_url", "art_url", "has_image",
}


def _mode() -> dict:
    return json.loads(MODE_DEPLOY.read_text(encoding="utf-8"))


# ── 模式文件 ────────────────────────────────────────────────
def test_mode_json_validates():
    from core.mode_registry import _validate_mode_def_with_error

    for path in (MODE_SOURCE, MODE_DEPLOY):
        assert path.exists(), f"缺少模式文件：{path}"
        definition = json.loads(path.read_text(encoding="utf-8"))
        ok, err = _validate_mode_def_with_error(definition)
        assert ok, f"{path}: {err}"
        assert definition["mode_id"] == "CUSTOM_DANQING"
        assert definition["content"]["type"] == "computed"
        assert definition["content"]["provider"] == "danqing"


def test_mode_source_and_deploy_match():
    assert MODE_SOURCE.read_text(encoding="utf-8") == MODE_DEPLOY.read_text(encoding="utf-8")


def test_registered_in_catalog():
    from core.mode_catalog import BUILTIN_CATALOG

    item = next(i for i in BUILTIN_CATALOG if i.mode_id == "CUSTOM_DANQING")
    assert item.category == "custom"
    assert item.zh.name == "丹青谱"


def test_registered_in_mode_registry():
    from core.mode_registry import get_registry

    assert "CUSTOM_DANQING" in get_registry().get_supported_ids()


def test_fallback_covers_every_field():
    fallback = _mode()["content"]["fallback"]
    assert set(fallback) == CONTENT_FIELDS
    # seal_url / art_url 是运行期才有的 URL（私印与画心），兜底为空是对的
    runtime_urls = {"seal_url", "art_url"}
    for field, value in fallback.items():
        if field in runtime_urls:
            assert value == "", f"{field} 兜底应当为空"
            continue
        assert isinstance(value, str) and value.strip(), f"{field} 兜底为空"


def test_provider_is_wired():
    """computed 通道必须能派发到 danqing provider。"""
    import inspect

    import core.json_content as jc

    source = inspect.getsource(jc._generate_computed_content)
    assert 'provider == "danqing"' in source
    assert "build_danqing_content" in source


# ── 题签解析 ────────────────────────────────────────────────
def test_parse_entry_reads_labelled_lines():
    from core.danqing import _parse_entry

    raw = (
        "画名：秋林曳杖图\n"
        "作者：江村陆氏，号拙庵\n"
        "朝代：南宋\n"
        "题材：山水\n"
        "题跋：霜落空山有客归。\n"
        "印文：拙庵居士\n"
        "图：一幅宋代山水册页\n"
        "多余的一句话\n"
    )
    entry = _parse_entry(raw)
    assert entry["画名"] == "秋林曳杖图"
    assert entry["印文"] == "拙庵居士"
    assert "多余的一句话" not in entry


def test_parse_entry_ignores_malformed():
    from core.danqing import _parse_entry

    assert _parse_entry("") == {}
    assert _parse_entry("没有冒号的行\n") == {}


def test_entry_keys_cover_prompt_fields():
    from core.danqing import ENTRY_KEYS, FALLBACK_ENTRY

    for key in ("画名", "作者", "朝代", "题材", "题跋", "印文", "图"):
        assert key in ENTRY_KEYS
        assert FALLBACK_ENTRY.get(key), f"{key} 缺兜底"


def test_prompt_forbids_real_painters():
    """作者与画名必须虚构，不能撞真实宋代画家。"""
    from core.danqing import ENTRY_PROMPT

    assert "虚构" in ENTRY_PROMPT
    assert "不得用真实宋代画家的名" in ENTRY_PROMPT


# ── 私印 ────────────────────────────────────────────────────
def test_seal_is_generated_and_reused(tmp_path, monkeypatch):
    from core import danqing

    monkeypatch.setattr(danqing, "ART_DIR", tmp_path)
    url = danqing._seal_path("拙庵居士")
    assert url.startswith("/static/danqing/")
    assert (tmp_path / url.rsplit("/", 1)[-1]).exists()
    # 同一印文复用同一文件
    assert danqing._seal_path("拙庵居士") == url
    # 不同印文换新印
    assert danqing._seal_path("某某私印") != url


def test_seal_without_text_returns_empty(tmp_path, monkeypatch):
    from core import danqing

    monkeypatch.setattr(danqing, "ART_DIR", tmp_path)
    assert danqing._seal_path("") == ""
    assert danqing._seal_path("   ") == ""


# ── 每日推进 ────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_build_content_uses_llm_entry_and_fills_fields(tmp_path, monkeypatch):
    from core import danqing

    monkeypatch.setattr(danqing, "STATE_DIR", tmp_path)
    monkeypatch.setattr(danqing, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(danqing, "ART_DIR", tmp_path)

    async def fake_generate_art(entry, dest, **kwargs):
        return False, "测试不生图"

    monkeypatch.setattr(danqing, "_generate_art", fake_generate_art)

    raw = (
        "画名：寒林归鸦图\n"
        "作者：钱塘方氏，号静庵\n"
        "朝代：北宋\n"
        "题材：花鸟\n"
        "题跋：寒鸦数点下平芜。\n"
        "印文：静庵之印\n"
        "图：一幅宋代花鸟册页\n"
    )

    async def fake_llm_entry(index, recent, genre, **kwargs):
        return danqing._parse_entry(raw)

    monkeypatch.setattr(danqing, "_llm_entry", fake_llm_entry)

    content = await danqing.build_danqing_content(fallback=_mode()["content"]["fallback"])

    assert content["title"] == "寒林归鸦图"
    assert content["artist"] == "钱塘方氏，号静庵"
    assert content["dynasty"] == "北宋"
    assert content["genre"] == "花鸟"
    assert content["colophon"] == "寒鸦数点下平芜。"
    assert content["index_label"].startswith("第 ")
    assert content["has_image"] == "no"
    assert content["art_url"] == ""
    assert content["seal_url"].startswith("/static/danqing/")


@pytest.mark.asyncio
async def test_same_day_reuses_stored_entry(tmp_path, monkeypatch):
    """同一天再取一次：不重新调用模型，直接用存好的那份。"""
    from core import danqing

    monkeypatch.setattr(danqing, "STATE_DIR", tmp_path)
    monkeypatch.setattr(danqing, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(danqing, "ART_DIR", tmp_path)

    calls: list[int] = []

    async def fake_generate_art(entry, dest, **kwargs):
        return False, "测试不生图"

    monkeypatch.setattr(danqing, "_generate_art", fake_generate_art)

    async def fake_llm_entry(index, recent, genre, **kwargs):
        calls.append(index)
        return dict(danqing.FALLBACK_ENTRY)

    monkeypatch.setattr(danqing, "_llm_entry", fake_llm_entry)

    first = await danqing.build_danqing_content()
    second = await danqing.build_danqing_content()

    assert len(calls) == 1, f"同一天不该重复调用模型，实得 {len(calls)} 次"
    assert first["title"] == second["title"]
    assert first["index_label"] == second["index_label"]


@pytest.mark.asyncio
async def test_genres_rotate_without_repeat(tmp_path, monkeypatch):
    """题材按编号轮转，一轮之内不重样。"""
    from core import danqing

    monkeypatch.setattr(danqing, "STATE_DIR", tmp_path)
    monkeypatch.setattr(danqing, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(danqing, "ART_DIR", tmp_path)

    async def fake_generate_art(entry, dest, **kwargs):
        return False, "测试不生图"

    monkeypatch.setattr(danqing, "_generate_art", fake_generate_art)

    seen: list[str] = []

    async def fake_llm_entry(index, recent, genre, **kwargs):
        seen.append(genre)
        entry = dict(danqing.FALLBACK_ENTRY)
        entry["题材"] = genre
        return entry

    monkeypatch.setattr(danqing, "_llm_entry", fake_llm_entry)

    for _ in range(len(danqing.GENRES)):
        await danqing.build_danqing_content()
        # 每天一个条目：手动把今天挪走，模拟跨天
        state = danqing._read_state()
        days = state.get("days", {})
        for key in list(days):
            days.pop(key)
            state["days"] = days
            danqing._write_state(state)

    assert seen == list(danqing.GENRES)
    assert len(set(seen)) == len(seen)


# ── 版式 ────────────────────────────────────────────────────
def _referenced_fields(node, out: set[str]) -> set[str]:
    if isinstance(node, dict):
        for key in ("field", "code_field", "suffix_field", "max_field", "text_field"):
            if isinstance(node.get(key), str):
                out.add(node[key])
        for key in ("template", "attribution_template"):
            template = node.get(key)
            if isinstance(template, str):
                out.update(re.findall(r"\{(\w+)\}", template))
        for value in node.values():
            _referenced_fields(value, out)
    elif isinstance(node, list):
        for value in node:
            _referenced_fields(value, out)
    return out


def test_layout_shows_every_field():
    mode = _mode()
    referenced: set[str] = set()
    _referenced_fields(mode["layout"], referenced)
    for override in mode.get("layout_overrides", {}).values():
        _referenced_fields(override, referenced)
    missing = CONTENT_FIELDS - referenced
    assert not missing, f"版式没有引用这些字段：{sorted(missing)}"


def test_layout_templates_only_use_known_fields():
    mode = _mode()
    referenced: set[str] = set()
    _referenced_fields(mode["layout"], referenced)
    for override in mode.get("layout_overrides", {}).values():
        _referenced_fields(override, referenced)
    unknown = referenced - CONTENT_FIELDS
    assert not unknown, f"模板引用了不存在的字段：{sorted(unknown)}"


def test_layout_has_image_and_no_image_branches():
    """有图时左图右签，没图时纯题签居中 —— 生图失败不能空屏。"""
    body = _mode()["layout"]["body"]
    assert body[0]["type"] == "conditional"
    assert body[0]["field"] == "has_image"
    values = {c["value"] for c in body[0]["conditions"]}
    assert values == {"yes", "no"}
    for cond in body[0]["conditions"]:
        assert cond["children"], f"{cond['value']} 分支是空的"


def test_has_small_and_large_overrides():
    overrides = _mode()["layout_overrides"]
    assert "296x128" in overrides
    assert "648x480" in overrides


def test_image_block_size_matches_provider_display():
    """版式里的图尺寸必须与 provider 的 ART_DISPLAY 一致，否则会二次缩放丢墨线。"""
    from core.danqing import ART_DISPLAY

    mode = _mode()
    sizes: set[tuple[int, int]] = set()

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "image" and node.get("field") == "art_url":
                sizes.add((int(node["width"]), int(node["height"])))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(mode["layout"])
    assert ART_DISPLAY in sizes, f"版式没有用 ART_DISPLAY {ART_DISPLAY}，实得 {sizes}"


# ── 版式比例：图为主 ────────────────────────────────────────
def _art_blocks(node, out: list) -> list:
    if isinstance(node, dict):
        if node.get("type") == "image" and node.get("field") == "art_url":
            out.append(node)
        for value in node.values():
            _art_blocks(value, out)
    elif isinstance(node, list):
        for value in node:
            _art_blocks(value, out)
    return out


def _text_blocks(node, out: list) -> list:
    if isinstance(node, dict):
        if node.get("type") == "text":
            out.append(node)
        for value in node.values():
            _text_blocks(value, out)
    elif isinstance(node, list):
        for value in node:
            _text_blocks(value, out)
    return out


def test_art_is_a_wide_banner():
    """画心是横幅（宽远大于高），且三屏版式都保持横幅比例。"""
    mode = _mode()
    for label, layout in [
        ("主版式", mode["layout"]),
        *[(k, v) for k, v in mode.get("layout_overrides", {}).items()],
    ]:
        for block in _art_blocks(layout, []):
            w = int(block["width"])
            h = int(block["height"])
            assert w > h * 1.6, f"{label} 画心不是横幅：{w}x{h}"
            assert w / h < 3.0, f"{label} 画心比例太扁：{w}x{h}"


def test_art_fills_most_of_the_width():
    """画心必须铺满大部分屏宽 —— 冰河大人的要求：图占主导。

    渲染器把 image 的 width/height 乘以 ctx.scale（296→0.92，648→1.62），
    而 x 是绝对像素，所以这里按同样规则算出屏上实际占位再断言。
    """
    mode = _mode()
    for label, screen_w, screen_h, layout in [
        ("主版式", 400, 300, mode["layout"]),
        ("296x128", 296, 128, mode["layout_overrides"]["296x128"]),
        ("648x480", 648, 480, mode["layout_overrides"]["648x480"]),
    ]:
        scale = max(0.92, screen_w / 400.0)
        blocks = _art_blocks(layout, [])
        assert blocks, f"{label} 没有画心"
        for block in blocks:
            w = int(block["width"]) * scale
            x = int(block.get("x", 0))
            ratio = (w) / screen_w
            assert ratio >= 0.55, f"{label} 画心只占屏宽 {ratio:.0%}，太小"
            assert x + w <= screen_w + 1, f"{label} 画心超出屏宽：{x}+{w:.0f} > {screen_w}"


def test_caption_text_is_capped():
    """题签必须压到极少文字：每个分支最多 4 个文本块。"""
    mode = _mode()

    def branches(layout):
        body = layout.get("body", [])
        if body and body[0].get("type") == "conditional":
            return [(f"{c['value']}分支", c["children"]) for c in body[0]["conditions"]]
        return [("版式", body)]

    for label, layout in [
        ("主版式", mode["layout"]),
        *[(k, v) for k, v in mode.get("layout_overrides", {}).items()],
    ]:
        for branch_label, children in branches(layout):
            texts = _text_blocks(children, [])
            assert len(texts) <= 4, f"{label} {branch_label} 文本块过多：{len(texts)}"
            for block in texts:
                assert int(block.get("max_lines", 3)) <= 3, f"{label} {branch_label} 允许行数过多"


def test_main_layout_caption_is_two_lines():
    """主版式（有图分支）只有画名 + 一行落款，其余全部省掉。"""
    body = _mode()["layout"]["body"]
    yes = next(c for c in body[0]["conditions"] if c["value"] == "yes")
    children = yes["children"]
    assert children[0]["type"] == "image"
    assert children[1]["type"] == "two_column"
    texts = _text_blocks(children[1], [])
    assert len(texts) == 2, f"主版式题签行数不对：{len(texts)}"
    # 款识已从屏上移除
    assert "mark" not in {t.get("field") for t in texts}
    assert all("{mark}" not in (t.get("template") or "") for t in texts)


def test_no_image_branch_still_shows_text():
    """生图失败时退回纯题签版式，且不能空屏。"""
    body = _mode()["layout"]["body"]
    no = next(c for c in body[0]["conditions"] if c["value"] == "no")
    texts = _text_blocks(no, [])
    assert len(texts) >= 3, "无图分支文字太少"
    referenced = set()
    _referenced_fields(no, referenced)
    assert {"title", "colophon"} <= referenced


# -- 色相硬映射（丹青谱专用）------------------------------------


def _rgb_to_hsv_pixel(rgb):
    """把 RGB 元组转成 HSV 像素，供 _snap_hue_to_ink 的输入构造。"""
    from PIL import Image

    img = Image.new('RGB', (1, 1), rgb)
    h, s, v = img.convert('HSV').split()
    return h.getpixel((0, 0)), s.getpixel((0, 0)), v.getpixel((0, 0))


def _map_pixels(pixels):
    from PIL import Image

    from core.danqing import _snap_hue_to_ink

    img = Image.new('RGB', (len(pixels), 1))
    img.putdata(list(pixels))
    out = _snap_hue_to_ink(img)
    return list(out.getdata())


def test_hue_map_maps_cold_hues_to_black():
    """绿/青/蓝/紫必须归墨黑 —— 四色屏没有绿，这部分只能变墨。"""
    from core.danqing import _SNAP_BLACK

    pixels = [(0, 120, 60), (20, 90, 140), (30, 60, 160), (120, 40, 130)]
    out = _map_pixels(pixels)
    assert all(p == _SNAP_BLACK for p in out), out


def test_hue_map_keeps_red_and_gold():
    """纯红相保朱红，橙金相归金黄。"""
    from core.danqing import _SNAP_RED, _SNAP_YELLOW

    out = _map_pixels([(220, 30, 30), (232, 176, 0)])
    assert out[0] == _SNAP_RED, out
    assert out[1] == _SNAP_YELLOW, out


def test_hue_map_paper_to_white():
    """低饱和高亮 = 纸面，归纯白。"""
    from core.danqing import _SNAP_WHITE

    out = _map_pixels([(250, 248, 244), (255, 255, 255)])
    assert all(p == _SNAP_WHITE for p in out), out


def test_hue_map_output_is_exactly_four_colors():
    """映射结果必须严格落在屏上四色内。"""
    from core.danqing import _SNAP_BLACK, _SNAP_RED, _SNAP_WHITE, _SNAP_YELLOW

    pixels = [
        (250, 248, 244), (0, 120, 60), (20, 90, 140), (220, 30, 30),
        (232, 176, 0), (128, 128, 128), (30, 60, 160),
    ]
    out = _map_pixels(pixels)
    allowed = {_SNAP_BLACK, _SNAP_WHITE, _SNAP_YELLOW, _SNAP_RED}
    assert set(out) <= allowed, set(out) - allowed


def test_hue_map_lives_only_in_danqing():
    """色相映射绝不能进 eink_snap 共享层 —— 躺平巴拉已冻结，共享层改了会动它。"""
    import inspect

    from core import eink_snap

    assert '_snap_hue_to_ink' not in inspect.getsource(eink_snap)


def test_danqing_prompt_forbids_green():
    """丹青谱专用四色盘必须禁绿蓝青紫 —— 模型默认会画花青石绿。"""
    from core.danqing import DANQING_PALETTE_SPEC

    assert '绿色' in DANQING_PALETTE_SPEC
    assert '青色' in DANQING_PALETTE_SPEC


def test_danqing_does_not_share_tiangong_palette():
    """丹青谱不能复用天工阁的 PALETTE_SPEC（那份允许同色系深浅变化）。"""
    from core import danqing
    from core.tiangong import PALETTE_SPEC

    assert danqing.DANQING_PALETTE_SPEC != PALETTE_SPEC
    assert 'PALETTE_SPEC' not in danqing.__dict__
