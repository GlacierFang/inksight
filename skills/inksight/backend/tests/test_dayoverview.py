"""「每日总览」合并模式（CUSTOM_DAYOVERVIEW）测试。

把「每日推荐 + 天气看板 + 农历节气」三个模式合并进一屏，覆盖：
  - 模式 JSON：源文件/部署副本合法且一致，cacheable=False；
  - 内容合成：四个来源（日期 / 本地历法 / 天气 / LLM）齐备，LLM 步骤用
    type="llm" + output_format="json" 以便注入前序步骤的权威字段；
  - 字段完整性：合并后的字段一个不落，且版式真的把它们都画出来；
  - 故障隔离：单个来源失败时其余步骤照常产出，字段由兜底补齐；
  - 版式适配：400×300 / 296×128 / 648×480 三种尺寸下内容不越过页脚线。
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

MODE_SOURCE = BACKEND_DIR.parent / "modes-ours" / "dayoverview.json"
MODE_DEPLOY = BACKEND_DIR / "core" / "modes" / "builtin" / "custom_dayoverview.json"

# 合并模式应当产出的全部字段（日期 / 农历节气 / 天气 / 推荐 四类）
EXPECTED_FIELDS = {
    # daily_meta
    "year", "day", "month_cn", "weekday_cn", "day_of_year", "days_in_year",
    # almanac_local（离线历法）
    "lunar_display", "ganzhi_zodiac", "solar_term", "next_term", "days_to_next_term",
    # weather_forecast
    "city", "today_temp", "today_desc", "today_code", "today_low", "today_high",
    "today_range", "today_humidity", "today_wind_dir", "today_wind_level",
    "sunrise", "sunset", "advice", "forecast",
    # llm
    "quote", "author", "tip",
}

# 版式里以别名形式出现的字段：today_range 本身即 "低°C / 高°C"
ALIAS_FIELDS = {"today_low", "today_high"}

LLM_FIELDS = {"quote", "author", "tip"}

LOCAL_PAYLOAD = {
    "lunar_display": "九月十九",
    "ganzhi_zodiac": "丙午马年",
    "solar_term": "霜降",
    "next_term": "立冬",
    "days_to_next_term": 10,
}

WEATHER_PAYLOAD = {
    "city": "开封",
    "today_temp": 19,
    "today_desc": "晴",
    "today_code": 0,
    "today_low": 11,
    "today_high": 22,
    "today_range": "11°C / 22°C",
    "today_humidity": 62,
    "today_wind_dir": "东南风",
    "today_wind_level": "2级",
    "sunrise": "06:42",
    "sunset": "17:38",
    "advice": "早晚温差大，记得带件外套",
    "forecast": [
        {"day": "明天", "date": "10-29", "temp_range": "10/21°C", "temp_min": 10,
         "temp_max": 21, "desc": "多云", "code": 2},
    ],
}

LLM_JSON = json.dumps({
    "quote": "阻碍行动的障碍，本身就是行动的路。",
    "author": "马可·奥勒留",
    "tip": "秋分后早晚温差大，注意添衣保暖。",
}, ensure_ascii=False)


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
        assert definition["mode_id"] == "CUSTOM_DAYOVERVIEW"
        assert definition["content"]["type"] == "composite"
        # 天气/历法/推荐都按当天生成，不缓存
        assert definition["cacheable"] is False


def test_mode_source_and_deploy_match():
    assert MODE_SOURCE.read_text(encoding="utf-8") == MODE_DEPLOY.read_text(encoding="utf-8")


def test_registered_in_catalog():
    from core.mode_catalog import BUILTIN_CATALOG

    ids = {item.mode_id for item in BUILTIN_CATALOG}
    assert "CUSTOM_DAYOVERVIEW" in ids


def test_no_almanac_api_dependency():
    """本模式已摘除黄历 API 依赖：不再出现 almanac_api / 命理字段。"""
    raw = MODE_DEPLOY.read_text(encoding="utf-8")
    mode = json.loads(raw)
    providers = [s.get("provider") for s in mode["content"]["steps"] if s.get("provider")]
    assert "almanac_api" not in providers
    for banned in ("yi", "ji", "shenwei", "xingsu", "suisha", "chongsha", "health_tip"):
        assert banned not in mode["content"]["fallback"], f"命理字段仍在：{banned}"
        assert f"{{{banned}}}" not in raw, f"版式仍引用命理字段：{banned}"


# ── 内容合成 ────────────────────────────────────────────────
def test_composite_merges_all_four_sources():
    steps = _mode()["content"]["steps"]
    providers = [s.get("provider") for s in steps if s.get("provider")]
    assert providers == ["daily_meta", "almanac_local", "weather_forecast"]
    assert len(steps) == 4

    llm_steps = [s for s in steps if s.get("type") == "llm"]
    assert len(llm_steps) == 1, "推荐语步骤必须用 type=llm（composite 的 {字段} 注入只对它生效）"
    llm_step = llm_steps[0]
    assert llm_step["output_format"] == "json"
    assert set(llm_step["output_fields"]) == LLM_FIELDS
    # 权威字段要能注入提示词
    for injected in ("{solar_term}", "{today_temp}", "{lunar_display}", "{day}"):
        assert injected in llm_step["prompt_template"]


def test_composite_fallback_covers_every_field():
    """顶层兜底必须覆盖全部字段：任一来源失败时字段不空缺。"""
    fallback = _mode()["content"]["fallback"]
    assert set(fallback) == EXPECTED_FIELDS


def test_every_step_has_its_own_fallback():
    for step in _mode()["content"]["steps"]:
        assert step.get("fallback"), f"{step.get('provider') or step.get('type')} 缺少兜底"


def test_local_almanac_computation():
    """离线历法：农历月日、干支生肖、当前节气与下一节气倒数。"""
    from core.json_content import _local_almanac_fields

    fields = _local_almanac_fields({"year": 2026, "month_cn": "十月", "day": 28})
    assert fields["lunar_display"] == "九月十九"
    assert fields["ganzhi_zodiac"] == "丙午马年"
    assert fields["solar_term"] == "霜降"
    assert fields["next_term"] == "立冬"
    assert fields["days_to_next_term"] == 10


# ── 版式 ────────────────────────────────────────────────────
def _referenced_fields(node, out: set[str]) -> set[str]:
    """收集版式里引用到的内容字段（字段名 + {占位符}）。"""
    if isinstance(node, dict):
        for key in ("field", "code_field", "suffix_field", "max_field"):
            if isinstance(node.get(key), str):
                out.add(node[key])
        template = node.get("template")
        if isinstance(template, str):
            out.update(re.findall(r"\{(\w+)\}", template))
        for value in node.values():
            _referenced_fields(value, out)
    elif isinstance(node, list):
        for value in node:
            _referenced_fields(value, out)
    return out


def test_layout_shows_every_field():
    """合并的核心标准是「不丢内容」——每个字段都要真的画在屏幕上。"""
    mode = _mode()
    referenced: set[str] = set()
    _referenced_fields(mode["layout"], referenced)
    _referenced_fields(mode["layout_overrides"].get("296x128", {}), referenced)

    missing = EXPECTED_FIELDS - referenced - ALIAS_FIELDS
    assert not missing, f"版式没有引用这些字段：{sorted(missing)}"


def test_layout_templates_only_use_known_fields():
    """模板占位符必须都存在，避免改字段后静默渲染成空白。"""
    known = EXPECTED_FIELDS | {"context"}
    unknown: set[str] = set()
    for node in ({"layout": _mode()["layout"]}, _mode()["layout_overrides"]):
        _referenced_fields(node, unknown)
    unknown -= known
    assert not unknown, f"模板引用了不存在的字段：{sorted(unknown)}"


# ── 故障隔离 ────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_one_provider_failure_keeps_other_steps(monkeypatch):
    """天气来源失败时，日期/历法/推荐照常产出，天气字段由兜底补齐。"""
    import core.json_content as jc

    orig_computed = jc._generate_computed_content

    async def fake_computed(mode_def, content_cfg, fallback, **kwargs):
        if content_cfg.get("provider") == "almanac_local":
            return dict(LOCAL_PAYLOAD)
        return await orig_computed(mode_def, content_cfg, fallback, **kwargs)

    async def fake_external(mode_def, content_cfg, fallback, **kwargs):
        raise OSError("network down")

    async def fake_call_llm(*args, **kwargs):
        return LLM_JSON

    monkeypatch.setattr(jc, "_generate_computed_content", fake_computed)
    monkeypatch.setattr(jc, "_generate_external_data_content", fake_external)
    monkeypatch.setattr(jc, "_call_llm", fake_call_llm)

    content = await jc.generate_json_mode_content(
        _mode(),
        date_ctx={"year": 2026, "month": 10, "month_cn": "十月", "day": 28,
                  "weekday_cn": "周三", "day_of_year": 301, "days_in_year": 365},
        date_str="2026-10-28",
    )

    # 其余来源照常
    assert content["month_cn"] == "十月"
    assert content["solar_term"] == "霜降"
    assert content["quote"].startswith("阻碍行动")
    # 失败来源的字段由兜底补齐，不空缺
    assert content["city"] == ""
    assert content["today_temp"] == "--"
    assert content["today_desc"] == "暂无数据"


@pytest.mark.asyncio
async def test_llm_failure_is_isolated(monkeypatch):
    """LLM 失败时天气/历法/日期不受影响，推荐语走兜底。"""
    import core.json_content as jc

    orig_computed = jc._generate_computed_content

    async def fake_computed(mode_def, content_cfg, fallback, **kwargs):
        if content_cfg.get("provider") == "almanac_local":
            return dict(LOCAL_PAYLOAD)
        return await orig_computed(mode_def, content_cfg, fallback, **kwargs)

    async def fake_external(mode_def, content_cfg, fallback, **kwargs):
        return dict(WEATHER_PAYLOAD)

    async def fake_call_llm(*args, **kwargs):
        raise OSError("llm down")

    monkeypatch.setattr(jc, "_generate_computed_content", fake_computed)
    monkeypatch.setattr(jc, "_generate_external_data_content", fake_external)
    monkeypatch.setattr(jc, "_call_llm", fake_call_llm)

    content = await jc.generate_json_mode_content(
        _mode(),
        date_ctx={"year": 2026, "month": 10, "month_cn": "十月", "day": 28,
                  "weekday_cn": "周三", "day_of_year": 301, "days_in_year": 365},
        date_str="2026-10-28",
    )

    assert content["city"] == "开封"
    assert content["today_temp"] == 19
    assert content["solar_term"] == "霜降"
    assert content["quote"], "LLM 失败后语录应落到兜底"


# ── 版式适配 ────────────────────────────────────────────────
@pytest.mark.parametrize("size", [(400, 300), (296, 128), (648, 480)])
def test_layout_fits_above_footer(size):
    """真实内容渲染后不得越过页脚线（否则会被页脚压住）。"""
    from PIL import Image, ImageDraw

    from core import json_renderer as jr

    content = {
        "year": 2026, "day": 28, "month_cn": "十月", "weekday_cn": "周三",
        "day_of_year": 301, "days_in_year": 365,
        "lunar_display": "九月十九", "ganzhi_zodiac": "丙午马年",
        "solar_term": "霜降", "next_term": "立冬", "days_to_next_term": 10,
        "city": "石家庄", "today_temp": 19, "today_desc": "多云转阴", "today_code": 2,
        "today_range": "11°C / 22°C", "today_humidity": 62,
        "today_wind_dir": "东南风", "today_wind_level": "2级",
        "sunrise": "06:42", "sunset": "17:38",
        "advice": "早晚温差大，记得带件外套",
        "forecast": [
            {"day": "明天", "date": "10-29", "temp_range": "10/21°C", "temp_min": 10,
             "temp_max": 21, "desc": "多云转阴", "code": 2},
            {"day": "后天", "date": "10-30", "temp_range": "9/20°C", "temp_min": 9,
             "temp_max": 20, "desc": "小雨转阴", "code": 61},
            {"day": "周六", "date": "10-31", "temp_range": "8/19°C", "temp_min": 8,
             "temp_max": 19, "desc": "阴转多云", "code": 3},
            {"day": "周日", "date": "11-01", "temp_range": "9/20°C", "temp_min": 9,
             "temp_max": 20, "desc": "晴间多云", "code": 0},
        ],
        "quote": "阻碍行动的障碍本身就是行动的路",
        "author": "塞涅卡",
        "tip": "秋分后早晚温差大，注意添衣保暖，忌露脚踝",
    }

    mode = _mode()
    w, h = size
    layout = jr._merge_layout_dict(dict(mode["layout"]), mode["layout_overrides"].get(f"{w}x{h}", {}))
    layout = jr.expand_layout_presets(layout)

    img = Image.new("1", (w, h), jr.EINK_BG)
    draw = ImageDraw.Draw(img)
    status_bar_bottom = int(h * 0.11) + 2 if h <= 128 else int(h * 0.12)
    ctx = jr.RenderContext(draw=draw, img=img, content=content, screen_w=w, screen_h=h, y=status_bar_bottom)
    ctx.footer_height = int(layout.get("footer", {}).get("height", 30))

    body = layout.get("body", [])
    for block in (body if isinstance(body, list) else [body]):
        jr._render_block(ctx, block)

    pixels = img.load()
    lowest = -1
    for y in range(h):
        for x in range(w):
            if pixels[x, y] != jr.EINK_BG:
                lowest = y
                break

    assert lowest < ctx.footer_top, (
        f"{w}x{h} 内容压到页脚上了：最底墨迹 y={lowest}，页脚顶 y={ctx.footer_top}"
    )
