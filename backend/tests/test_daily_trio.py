"""「日课三则」合并模式（CUSTOM_DAILY_TRIO）测试。

把毒舌（ROAST）/ 猜谜（RIDDLE）/ 微故事（STORY）三个模式合并进一屏，覆盖：
  - 模式 JSON：源文件/部署副本合法且一致，catalog 已注册，缓存策略与三个原模式一致；
  - 一次模型调用出三块（合并的核心收益：省两次调用）；
  - 字段隔离：roast / riddle_* / story_* 三组前缀互不覆盖；
  - 版式：三个来源的字段都真的画出来，三种尺寸下不越过页脚线。
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

MODE_SOURCE = BACKEND_DIR.parent / "modes-ours" / "daily_trio.json"
MODE_DEPLOY = BACKEND_DIR / "core" / "modes" / "builtin" / "custom_daily_trio.json"

ROAST_FIELDS = {"roast"}
RIDDLE_FIELDS = {"riddle_category", "riddle_question", "riddle_hint", "riddle_answer"}
STORY_FIELDS = {"story_title", "story_opening", "story_twist", "story_ending", "story_genre"}
ALL_FIELDS = ROAST_FIELDS | RIDDLE_FIELDS | STORY_FIELDS


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
        assert definition["mode_id"] == "CUSTOM_DAILY_TRIO"
        assert definition["content"]["type"] == "llm_json"
        # 三个原模式都是可缓存的（每天一句/一题/一篇），合并后保持一致
        assert definition["cacheable"] is True


def test_mode_source_and_deploy_match():
    assert MODE_SOURCE.read_text(encoding="utf-8") == MODE_DEPLOY.read_text(encoding="utf-8")


def test_registered_in_catalog():
    from core.mode_catalog import BUILTIN_CATALOG

    ids = {item.mode_id for item in BUILTIN_CATALOG}
    assert "CUSTOM_DAILY_TRIO" in ids


def test_original_modes_untouched():
    """合并只新增，不动原来的毒舌/猜谜/微故事。"""
    builtin = BACKEND_DIR / "core/modes/builtin"
    roast = json.loads((builtin / "roast.json").read_text(encoding="utf-8"))
    riddle = json.loads((builtin / "riddle.json").read_text(encoding="utf-8"))
    story = json.loads((builtin / "story.json").read_text(encoding="utf-8"))
    assert roast["mode_id"] == "ROAST" and roast["content"]["type"] == "llm"
    assert riddle["mode_id"] == "RIDDLE" and riddle["content"]["type"] == "llm_json"
    assert story["mode_id"] == "STORY" and story["content"]["type"] == "llm_json"


def test_output_schema_covers_every_field():
    schema = _mode()["content"]["output_schema"]
    assert set(schema) == ALL_FIELDS
    for field, spec in schema.items():
        assert spec.get("default"), f"{field} 缺少默认值（模型漏字段时要能兜底）"


def test_fallback_covers_every_field():
    fallback = _mode()["content"]["fallback"]
    assert set(fallback) == ALL_FIELDS
    for field, value in fallback.items():
        assert isinstance(value, str) and value.strip(), f"{field} 兜底为空"


def test_prompt_asks_for_all_three_parts():
    prompt = _mode()["content"]["prompt_template"]
    for field in ALL_FIELDS:
        assert field in prompt, f"提示词没提到 {field}"
    assert "{context}" in prompt


# ── 一次调用出三块 ──────────────────────────────────────────
@pytest.mark.asyncio
async def test_one_model_call_produces_all_three(monkeypatch):
    """合并的核心收益：三个模式合计三次调用 → 一次。"""
    import core.json_content as jc

    llm_json = json.dumps({
        "roast": "早高峰的电梯，比你的耐心先到顶。",
        "riddle_category": "脑筋急转弯",
        "riddle_question": "什么东西越洗越脏？",
        "riddle_hint": "想想你在洗什么",
        "riddle_answer": "水",
        "story_title": "夜班",
        "story_opening": "便利店的夜班店员从不看手机。",
        "story_twist": "直到有一天她发现，监控里只有她一个人在动。",
        "story_ending": "她终于敢把手机拿出来了。",
        "story_genre": "悬疑",
    }, ensure_ascii=False)

    calls: list[str] = []

    async def fake_call_llm(*args, **kwargs):
        calls.append(args[3] if len(args) > 3 else "")
        return llm_json

    monkeypatch.setattr(jc, "_call_llm", fake_call_llm)

    content = await jc.generate_json_mode_content(
        _mode(),
        date_ctx={"year": 2026, "month": 10, "month_cn": "十月", "day": 28,
                  "weekday_cn": "周三", "day_of_year": 301, "days_in_year": 365},
        date_str="2026-10-28",
    )

    assert len(calls) == 1, f"只允许一次模型调用，实得 {len(calls)}"
    assert set(content) >= ALL_FIELDS
    # 三组前缀互不覆盖
    assert content["roast"] == "早高峰的电梯，比你的耐心先到顶。"
    assert content["riddle_question"] == "什么东西越洗越脏？"
    assert content["story_ending"] == "她终于敢把手机拿出来了。"


@pytest.mark.asyncio
async def test_partial_llm_output_uses_schema_defaults(monkeypatch):
    """模型只回了一部分 → 缺的字段用 output_schema 的默认值，不空屏。"""
    import core.json_content as jc

    async def fake_call_llm(*args, **kwargs):
        return json.dumps({"roast": "只回了一句毒舌。"}, ensure_ascii=False)

    monkeypatch.setattr(jc, "_call_llm", fake_call_llm)

    content = await jc.generate_json_mode_content(_mode(), date_str="2026-10-28")
    assert content["roast"] == "只回了一句毒舌。"
    assert content["riddle_answer"] == "合肥"
    assert content["story_title"] == "末班车"


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
    """三块内容都要真的画在屏幕上，一个都不能丢。"""
    mode = _mode()
    referenced: set[str] = set()
    _referenced_fields(mode["layout"], referenced)
    for override in mode.get("layout_overrides", {}).values():
        _referenced_fields(override, referenced)
    missing = ALL_FIELDS - referenced
    assert not missing, f"版式没有引用这些字段：{sorted(missing)}"


def test_layout_templates_only_use_known_fields():
    mode = _mode()
    referenced: set[str] = set()
    _referenced_fields(mode["layout"], referenced)
    for override in mode.get("layout_overrides", {}).values():
        _referenced_fields(override, referenced)
    unknown = referenced - ALL_FIELDS - {"context", "_value", "_item"}
    assert not unknown, f"模板引用了不存在的字段：{sorted(unknown)}"


def test_layout_is_three_stacked_blocks():
    """版式骨架：竖排三块——毒舌、谜语、微故事，各带一条短分隔线。"""
    body = _mode()["layout"]["body"]
    assert body["type"] == "column"
    separators = [c for c in body.get("children", []) if c.get("type") == "separator"]
    assert len(separators) == 2, "三块之间应有两条分隔线"
    blocks = [c for c in body.get("children", []) if c.get("type") != "separator"]
    assert len(blocks) == 3, "正文应是三块"
    # 第一块是毒舌，第二块是谜语，第三块是故事
    first_fields: set[str] = set()
    _referenced_fields(blocks[0], first_fields)
    assert "roast" in first_fields
    second_fields: set[str] = set()
    _referenced_fields(blocks[1], second_fields)
    assert {"riddle_question", "riddle_hint", "riddle_answer"} <= second_fields
    third_fields: set[str] = set()
    _referenced_fields(blocks[2], third_fields)
    assert {"story_title", "story_opening", "story_twist", "story_ending"} <= third_fields


@pytest.mark.parametrize("size", [(400, 300), (296, 128), (648, 480)])
def test_layout_fits_above_footer(size):
    """真实内容渲染后不得越过页脚线（否则会被页脚压住）。"""
    from core import json_renderer as jr

    content = {
        "roast": "早高峰的电梯，比你的耐心先到顶。",
        "riddle_category": "脑筋急转弯",
        "riddle_question": "什么东西越洗越脏？",
        "riddle_hint": "想想你在洗什么",
        "riddle_answer": "水",
        "story_title": "夜班",
        "story_opening": "便利店的夜班店员从不看手机，货架上的关东煮灯一直亮着。",
        "story_twist": "直到有一天她发现，监控里只有她一个人在动。",
        "story_ending": "她终于敢把手机拿出来了。",
        "story_genre": "悬疑",
    }

    mode = _mode()
    w, h = size
    layout = jr._merge_layout_dict(dict(mode["layout"]), mode["layout_overrides"].get(f"{w}x{h}", {}))
    layout = jr.expand_layout_presets(layout)

    status_bar_bottom = int(h * 0.11) + 2 if h <= 128 else int(h * 0.12)
    footer_height = int(layout.get("footer", {}).get("height", 30))
    if h <= 128:
        footer_height = max(footer_height, 24)
    footer_top = h - footer_height
    if h <= 128:
        footer_top += 3

    theme = dict(layout.get("component_theme", {}))
    scale = jr._component_tree_scale(
        jr.RenderContext(draw=None, img=None, content=content, screen_w=w, screen_h=h, y=status_bar_bottom),
        theme,
    )
    root = jr._build_component_node(layout["body"], content)
    jr._measure_component_node(root, w, theme, scale)

    available = footer_top - status_bar_bottom
    assert root.measured_height <= available, (
        f"{w}x{h} 内容超高：{root.measured_height} > {available}"
    )
