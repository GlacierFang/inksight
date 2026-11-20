"""「诗笺日课」合并模式（CUSTOM_POETRY_DAY）测试。

把「每日古诗词 + 宋人日课 + 回家签」三个模式合并进一屏，覆盖：
  - 模式 JSON：源文件/部署副本合法且一致，cacheable=False，主诗不过模型；
  - 字段隔离：poem_* / lore_* / fact_* 三组前缀互不覆盖；
  - 私人事实三态：已到家 / 已出门未回 / 无记录，配不上诗签时回本地诗库且不调 LLM；
  - 合成结果：三个来源的字段一个不落；
  - 版式：每个字段都真的画出来，三种尺寸下不越过页脚线。
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

MODE_SOURCE = BACKEND_DIR.parent / "modes-ours" / "poetry_day.json"
MODE_DEPLOY = BACKEND_DIR / "core" / "modes" / "builtin" / "custom_poetry_day.json"

FACT_FIELDS = {
    "fact_state", "fact_meta", "fact_duration", "fact_time", "fact_poem_title",
    "fact_poem_author", "fact_lines", "fact_poem_source", "fact_weather",
}
LORE_FIELDS = {"lore_title", "lore_era", "lore_lines", "lore_note"}
POEM_FIELDS = {"poem_title", "poem_author", "poem_lines"}
ALL_FIELDS = FACT_FIELDS | LORE_FIELDS | POEM_FIELDS
# 上屏字段：fact_state 只是状态机内部值，lore_note 是掌故的第四行注脚，
# 两者都不进版式（400×300 一屏放四块，没有它们的余量）。
SCREEN_FIELDS = ALL_FIELDS - {"fact_state", "lore_note"}


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
        assert definition["mode_id"] == "CUSTOM_POETRY_DAY"
        assert definition["content"]["type"] == "composite"
        # 当天事实与天气每次生成都不同，不缓存
        assert definition["cacheable"] is False


def test_mode_source_and_deploy_match():
    assert MODE_SOURCE.read_text(encoding="utf-8") == MODE_DEPLOY.read_text(encoding="utf-8")


def test_registered_in_catalog():
    from core.mode_catalog import BUILTIN_CATALOG

    ids = {item.mode_id for item in BUILTIN_CATALOG}
    assert "CUSTOM_POETRY_DAY" in ids


def test_no_llm_step():
    """主诗不再过模型 —— 诗名与作者是强绑定的事实，模型会把「山行」配成「李白」。"""
    steps = _mode()["content"]["steps"]
    llm_steps = [s for s in steps if s.get("type") in ("llm", "llm_json")]
    assert not llm_steps, "主诗不应再走模型"
    # 两步都是本地 computed，不经模型
    providers = [s.get("provider") for s in steps if s.get("type") == "computed"]
    assert providers == ["poetry_day", "song_lore"]


def test_other_modes_untouched():
    """原来三个模式不能因为合并而多出模型调用。"""
    poetry = json.loads((BACKEND_DIR / "core/modes/builtin/poetry.json").read_text(encoding="utf-8"))
    song_lore = json.loads((BACKEND_DIR / "core/modes/builtin/custom_song_lore.json").read_text(encoding="utf-8"))
    home_sign = json.loads((BACKEND_DIR / "core/modes/builtin/custom_home_sign.json").read_text(encoding="utf-8"))
    assert poetry["content"]["type"] == "llm_json"
    assert song_lore["content"]["type"] == "static"
    assert home_sign["content"]["type"] == "computed"
    assert home_sign["content"]["provider"] == "home_sign"


def test_composite_fallback_covers_every_field():
    fallback = _mode()["content"]["fallback"]
    assert set(fallback) == ALL_FIELDS


def test_every_step_has_its_own_fallback():
    for step in _mode()["content"]["steps"]:
        assert step.get("fallback"), f"{step.get('provider') or step.get('type')} 缺少兜底"


def test_settings_schema_declares_two_locations():
    """双城自选：模式自己声明两个地点槽位，前端据此给两个选择器。"""
    schema = _mode().get("settings_schema") or []
    locations = [item for item in schema if item.get("type") == "location"]
    assert len(locations) == 2, "诗笺日课要能自选两座城市"
    assert {item["key"] for item in locations} == {"poetry_home", "poetry_away"}
    for item in locations:
        assert item.get("label"), "地点槽位要有标签"


# ── 私人事实三态 ────────────────────────────────────────────
class _FakeLib:
    """假诗库：避免测试真的去动 poetry_stats.json。

    依次给两首 —— 够验证「主诗避开事实块已经用过的那首」。
    """

    _POEMS = [
        {"title": "秋词", "author": "唐·刘禹锡", "poem": "自古逢秋悲寂寥，我言秋日胜春朝。"},
        {"title": "山居秋暝", "author": "唐·王维", "poem": "空山新雨后，天气晚来秋。"},
    ]
    _idx = 0

    @staticmethod
    def random_poem(month=None):
        poem = _FakeLib._POEMS[_FakeLib._idx % len(_FakeLib._POEMS)]
        _FakeLib._idx += 1
        return dict(poem)

    @staticmethod
    def update_poem_stats(title, author):
        return True


def _write_bt_dir(tmp_path: Path, events: list[dict], signs: list[dict]) -> Path:
    bt_dir = tmp_path / "bt"
    bt_dir.mkdir(parents=True, exist_ok=True)
    (bt_dir / "presence_log.json").write_text(
        json.dumps({"log": events}, ensure_ascii=False), encoding="utf-8"
    )
    (bt_dir / "poetry_signs.json").write_text(
        json.dumps(signs, ensure_ascii=False), encoding="utf-8"
    )
    return bt_dir


def _event(event: str, offset_minutes: float = 0.0, **extra) -> dict:
    ts = time.time() - offset_minutes * 60
    moment = datetime.fromtimestamp(ts)
    data = {"ts": ts, "time": moment.strftime("%H:%M"), "event": event}
    data.update(extra)
    return data


def _sign(event: str, offset_minutes: float = 0.0, poem: str = "残云收夏暑，新雨带秋岚。") -> dict:
    return {
        "ts": time.time() - offset_minutes * 60,
        "date": datetime.now().strftime("%Y-%m-%d"),
        "time": datetime.fromtimestamp(time.time() - offset_minutes * 60).strftime("%H:%M"),
        "event": event,
        "poem": poem,
        "title": "六月三十日水亭送华阴王少府还县",
        "author": "岑参",
    }


@pytest.fixture()
def fake_poetry_lib(monkeypatch):
    import core.poetry_day as pd

    _FakeLib._idx = 0
    monkeypatch.setattr(pd, "_load_poetry_lib", lambda: _FakeLib)


def test_home_state(monkeypatch, tmp_path, fake_poetry_lib):
    """当天已回家：到家时刻、在外时长、HOME 诗签、次日天气。"""
    import core.poetry_day as pd

    bt_dir = _write_bt_dir(
        tmp_path,
        [_event("home", 30, absent_minutes=749, weather_home="郑州: ☁️  +18°C / 淄博: ☀️  +8°C")],
        [_sign("HOME", 30)],
    )
    monkeypatch.setenv("INKSIGHT_BT_PRESENCE_DIR", str(bt_dir))

    facts = pd.build_poetry_day_facts()
    assert facts["fact_state"] == "home"
    assert facts["fact_meta"] == datetime.now().strftime("%m-%d") + " 到家"
    assert facts["fact_duration"].startswith("在外")
    assert facts["fact_time"]
    assert facts["fact_poem_title"] == "《六月三十日水亭送华阴王少府还县》"
    assert facts["fact_poem_author"] == "〔岑参〕"
    assert facts["fact_lines"] == ["残云收夏暑", "新雨带秋岚"]
    assert facts["fact_poem_source"] == "诗签"
    assert facts["fact_weather"].startswith("明日")
    assert "郑州 18°" in facts["fact_weather"]


def test_away_state(monkeypatch, tmp_path, fake_poetry_lib):
    """当天已出门尚未回家：出门时刻、至今在外时长、AWAY 诗签、出门时天气。"""
    import core.poetry_day as pd

    bt_dir = _write_bt_dir(
        tmp_path,
        [_event("away", 120, weather_away="郑州: ✨  +14°C / 淄博: ☀️  +4°C")],
        [_sign("AWAY", 120)],
    )
    monkeypatch.setenv("INKSIGHT_BT_PRESENCE_DIR", str(bt_dir))

    facts = pd.build_poetry_day_facts()
    assert facts["fact_state"] == "away"
    assert facts["fact_meta"].endswith("出门")
    assert facts["fact_duration"].startswith("在外")
    assert facts["fact_poem_source"] == "诗签"
    assert facts["fact_weather"].startswith("出门时")
    assert "郑州 14°" in facts["fact_weather"]


def test_only_today_events_count(monkeypatch, tmp_path, fake_poetry_lib):
    """昨天的到家事件不能算今天的。"""
    import core.poetry_day as pd

    yesterday = datetime.now() - timedelta(days=1)
    old_event = {
        "ts": yesterday.timestamp(),
        "time": yesterday.strftime("%H:%M"),
        "event": "home",
        "absent_minutes": 600,
        "weather_home": "郑州: ☁️  +18°C / 淄博: ☀️  +8°C",
    }
    bt_dir = _write_bt_dir(tmp_path, [old_event], [])
    monkeypatch.setenv("INKSIGHT_BT_PRESENCE_DIR", str(bt_dir))

    facts = pd.build_poetry_day_facts()
    assert facts["fact_state"] == "none"
    assert facts["fact_weather"] == ""
    # 配不上签时回本地诗库，不编造
    assert facts["fact_poem_source"] == "本地诗笺"
    assert facts["fact_lines"] == ["自古逢秋悲寂寥", "我言秋日胜春朝"]


def test_unmatched_sign_falls_back_to_local_poem(monkeypatch, tmp_path, fake_poetry_lib):
    """有到家事件但配不上诗签：回本地诗库，不编造诗签。"""
    import core.poetry_day as pd

    bt_dir = _write_bt_dir(
        tmp_path,
        [_event("home", 30, absent_minutes=120, weather_home="郑州: ☁️  +18°C / 淄博: ☀️  +8°C")],
        [],  # 没有诗签
    )
    monkeypatch.setenv("INKSIGHT_BT_PRESENCE_DIR", str(bt_dir))

    facts = pd.build_poetry_day_facts()
    assert facts["fact_state"] == "home"
    assert facts["fact_poem_source"] == "本地诗笺"
    assert facts["fact_poem_title"] == "《秋词》"


def test_song_lore_reads_shared_pool(monkeypatch):
    """掌故来自宋人日课同一份考据池，字段前缀独立。"""
    import core.poetry_day as pd

    lore = pd.build_song_lore()
    assert lore["lore_title"]
    assert lore["lore_era"]
    assert lore["lore_lines"]
    assert set(lore) == LORE_FIELDS


# ── 自选双城 ────────────────────────────────────────────────
def _city_config(home: str | None = None, away: str | None = None) -> dict:
    settings: dict[str, dict] = {}
    if home:
        settings["poetry_home"] = {"city": home, "latitude": 34.75, "longitude": 113.62}
    if away:
        settings["poetry_away"] = {"city": away, "latitude": 36.81, "longitude": 118.05}
    return {"mode_settings": settings}


def test_configured_cities_read_from_mode_settings():
    import core.poetry_day as pd

    cities = pd._configured_cities(_city_config("郑州", "淄博"))
    assert set(cities) == {"poetry_home", "poetry_away"}
    assert cities["poetry_home"]["city"] == "郑州"
    assert cities["poetry_away"]["city"] == "淄博"
    # 没配城市 → 空
    assert pd._configured_cities({}) == {}
    assert pd._configured_cities({"mode_settings": {"poetry_home": {"city": ""}}}) == {}


def test_effective_config_keeps_both_cities():
    """模式覆盖里的两个槽位要一起进有效配置，不能互相覆盖。"""
    from core.pipeline import get_effective_mode_config

    cfg = {
        "mode_overrides": {
            "CUSTOM_POETRY_DAY": {
                "mode_settings": {
                    "poetry_home": {"city": "郑州", "latitude": 34.75},
                    "poetry_away": {"city": "淄博", "latitude": 36.81},
                }
            }
        }
    }
    effective = get_effective_mode_config(cfg, "CUSTOM_POETRY_DAY")
    settings = effective["mode_settings"]
    assert settings["poetry_home"]["city"] == "郑州"
    assert settings["poetry_away"]["city"] == "淄博"


@pytest.mark.asyncio
async def test_custom_cities_replace_legacy_weather(monkeypatch, tmp_path, fake_poetry_lib):
    """自选了双城 → 用自选城市抓天气，不再用蓝牙事件里的郑州/淄博。"""
    import core.poetry_day as pd

    bt_dir = _write_bt_dir(
        tmp_path,
        [_event("home", 30, absent_minutes=749, weather_home="郑州: ☁️  +18°C / 淄博: ☀️  +8°C")],
        [_sign("HOME", 30)],
    )
    monkeypatch.setenv("INKSIGHT_BT_PRESENCE_DIR", str(bt_dir))

    async def fake_fetch(place):
        temps = {"杭州": 21, "南京": 9}
        return {"city": place["city"], "temp": temps[place["city"]]}

    monkeypatch.setattr(pd, "_fetch_city_weather", fake_fetch)

    facts = await pd.build_poetry_day_content(config=_city_config("杭州", "南京"))
    assert facts["fact_state"] == "home"
    assert facts["fact_weather"] == "明日 杭州 21° / 南京 9°"


@pytest.mark.asyncio
async def test_without_custom_cities_keeps_legacy_weather(monkeypatch, tmp_path, fake_poetry_lib):
    """没自选双城 → 完全沿用蓝牙事件里的旧天气文本。"""
    import core.poetry_day as pd

    bt_dir = _write_bt_dir(
        tmp_path,
        [_event("away", 120, weather_away="郑州: ✨  +14°C / 淄博: ☀️  +4°C")],
        [_sign("AWAY", 120)],
    )
    monkeypatch.setenv("INKSIGHT_BT_PRESENCE_DIR", str(bt_dir))

    async def fake_fetch(place):  # 不该被调用
        raise AssertionError("没自选城市时不应抓天气")

    monkeypatch.setattr(pd, "_fetch_city_weather", fake_fetch)

    facts = await pd.build_poetry_day_content(config={})
    assert facts["fact_weather"].startswith("出门时")
    assert "郑州 14°" in facts["fact_weather"]


@pytest.mark.asyncio
async def test_weather_failure_falls_back_to_legacy_text(monkeypatch, tmp_path, fake_poetry_lib):
    """自选城市抓不到天气 → 退回蓝牙事件里的旧文本，不空屏。"""
    import core.poetry_day as pd

    bt_dir = _write_bt_dir(
        tmp_path,
        [_event("away", 120, weather_away="郑州: ✨  +14°C / 淄博: ☀️  +4°C")],
        [_sign("AWAY", 120)],
    )
    monkeypatch.setenv("INKSIGHT_BT_PRESENCE_DIR", str(bt_dir))

    async def fake_fetch(place):
        return None

    monkeypatch.setattr(pd, "_fetch_city_weather", fake_fetch)

    facts = await pd.build_poetry_day_content(config=_city_config("杭州", "南京"))
    assert facts["fact_weather"].startswith("出门时")
    assert "郑州 14°" in facts["fact_weather"]


@pytest.mark.asyncio
async def test_custom_cities_show_without_bluetooth_event(monkeypatch, tmp_path, fake_poetry_lib):
    """今天没有蓝牙事件时，自选的双城天气照样上屏。"""
    import core.poetry_day as pd

    bt_dir = _write_bt_dir(tmp_path, [], [])
    monkeypatch.setenv("INKSIGHT_BT_PRESENCE_DIR", str(bt_dir))

    async def fake_fetch(place):
        return {"city": place["city"], "temp": 12}

    monkeypatch.setattr(pd, "_fetch_city_weather", fake_fetch)

    facts = await pd.build_poetry_day_content(config=_city_config("杭州", "南京"))
    assert facts["fact_state"] == "none"
    assert facts["fact_weather"] == "杭州 12° / 南京 12°"


@pytest.mark.asyncio
async def test_fetch_city_weather_uses_inksight_weather_stack(monkeypatch):
    """抓天气走 InkSight 自己的 Open-Meteo / 和风那套，不另外造轮子。"""
    import core.context as ctx
    import core.poetry_day as pd

    seen: dict[str, object] = {}

    async def fake_get_weather(lat=None, lon=None, city=None):
        seen.update({"lat": lat, "lon": lon, "city": city})
        return {"temp": 7.4, "weather_code": 3, "weather_str": "7°C"}

    monkeypatch.setattr(ctx, "get_weather", fake_get_weather)
    ctx._context_cache.pop("poetry_city_weather:杭州:34.75:113.62", None)

    weather = await pd._fetch_city_weather({"city": "杭州", "latitude": 34.75, "longitude": 113.62})
    assert weather == {"city": "杭州", "temp": 7}
    assert seen["city"] == "杭州"
    assert seen["lat"] == 34.75


# ── 合成 ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_composite_merges_three_sources(monkeypatch, fake_poetry_lib):
    """三个来源的字段一个不落，且互不覆盖；全程不调模型。"""
    import core.json_content as jc

    async def fail_call_llm(*args, **kwargs):
        raise AssertionError("诗笺日课不应再调模型")

    monkeypatch.setattr(jc, "_call_llm", fail_call_llm)

    content = await jc.generate_json_mode_content(
        _mode(),
        date_ctx={"year": 2026, "month": 10, "month_cn": "十月", "day": 28,
                  "weekday_cn": "周三", "day_of_year": 301, "days_in_year": 365},
        date_str="2026-10-28",
    )

    # 三个来源的字段都在
    assert content["poem_title"]
    assert content["lore_title"]
    assert content["fact_state"] in ("home", "away", "none")
    # 主诗与事实块那首不是同一首
    assert content["poem_title"] != str(content["fact_poem_title"]).strip("《》")
    # 前缀隔离：掌故/事实没有把主诗字段覆盖掉
    assert content["poem_title"] != content["lore_title"]
    assert set(content) >= ALL_FIELDS


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
    """合并的核心标准是「不丢内容」——每个字段都要真的画在屏幕上。"""
    mode = _mode()
    referenced: set[str] = set()
    _referenced_fields(mode["layout"], referenced)
    for override in mode.get("layout_overrides", {}).values():
        _referenced_fields(override, referenced)

    missing = SCREEN_FIELDS - referenced
    assert not missing, f"版式没有引用这些字段：{sorted(missing)}"


def test_layout_templates_only_use_known_fields():
    mode = _mode()
    referenced: set[str] = set()
    _referenced_fields(mode["layout"], referenced)
    for override in mode.get("layout_overrides", {}).values():
        _referenced_fields(override, referenced)
    unknown = referenced - ALL_FIELDS - {"context", "_value", "_item"}
    assert not unknown, f"模板引用了不存在的字段：{sorted(unknown)}"


def test_layout_is_two_columns():
    """版式骨架：左右两列——左列个人事实，右列主诗+掌故。"""
    body = _mode()["layout"]["body"]
    rows = [c for c in body.get("children", []) if c.get("type") == "row"]
    assert rows, "正文顶层应有左右分栏的 row"
    columns = rows[0]["children"]
    assert len(columns) == 2, "应是两列"
    left, right = columns
    # 左列：个人事实
    left_fields = set()
    _referenced_fields(left, left_fields)
    assert {"fact_time", "fact_duration", "fact_weather", "fact_poem_title"} <= left_fields
    # 右列：当日主诗 + 宋代掌故
    right_fields = set()
    _referenced_fields(right, right_fields)
    assert {"poem_title", "poem_lines", "lore_title", "lore_lines"} <= right_fields


def test_read_column_uses_one_font_size():
    """右列（主诗 + 掌故）字号统一，只用粗细分层。"""
    body = _mode()["layout"]["body"]
    row = [c for c in body.get("children", []) if c.get("type") == "row"][0]
    right = row["children"][1]
    sizes: set[int] = set()

    def collect(node):
        if isinstance(node, dict):
            if node.get("type") == "text" and node.get("font_size"):
                sizes.add(node["font_size"])
            for v in node.values():
                collect(v)
        elif isinstance(node, list):
            for v in node:
                collect(v)

    collect(right)
    assert len(sizes) == 1, f"右列字号应统一，实得 {sorted(sizes)}"


@pytest.mark.parametrize("size", [(400, 300), (296, 128), (648, 480)])
@pytest.mark.parametrize("variant", ["facts", "no_facts"])
def test_layout_fits_above_footer(size, variant):
    """真实内容渲染后不得越过页脚线（否则会被页脚压住）。"""
    from core import json_renderer as jr

    content = {
        "poem_title": "山居秋暝", "poem_author": "唐·王维",
        "poem_lines": ["空山新雨后", "天气晚来秋", "明月松间照", "清泉石上流"],
        "lore_title": "永昌陵", "lore_era": "北宋·太平兴国三年",
        "lore_lines": ["宋太祖赵匡胤的陵墓", "位于河南巩义", "陵前石刻仪仗尚存"],
        "fact_state": "away", "fact_meta": "10-28 出门", "fact_duration": "在外 9小时48分",
        "fact_time": "06:56",
        "fact_poem_title": "《残云收夏暑》", "fact_poem_author": "〔岑参〕",
        "fact_lines": ["残云收夏暑，新雨带秋岚。", "第二行也要放得下"],
        "fact_poem_source": "诗签", "fact_weather": "出门时 郑州 14° / 淄博 4°",
    }
    if variant == "no_facts":
        content.update({
            "fact_state": "none", "fact_meta": "10-28 无记录", "fact_duration": "", "fact_time": "",
            "fact_poem_source": "本地诗笺", "fact_weather": "",
        })

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
