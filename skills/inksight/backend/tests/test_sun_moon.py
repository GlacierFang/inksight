"""「日昼与月相」模式（CUSTOM_SUN_MOON）测试。

覆盖：
  - 模式 JSON：源文件/部署副本合法且一致，声明一个城市槽位，页脚城市跟着地点走；
  - 地点解析：没有模式覆盖时跟随设备全局位置；有覆盖时用覆盖的地点，
    城市名和坐标必须来自同一处（不出现「城市换了、坐标没换」）；
  - provider：日出日落、昼长、昼夜判定、LLM 文案都跟着所选城市走；
  - 缓存：同一天不同城市各自分桶，互不污染。
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

MODE_SOURCE = BACKEND_DIR.parent / "modes-ours" / "sun_moon.json"
MODE_DEPLOY = BACKEND_DIR / "core" / "modes" / "builtin" / "custom_sun_moon.json"

DEVICE = {  # 设备全局位置（设备当前所在）
    "city": "郑州",
    "latitude": 34.75,
    "longitude": 113.65,
    "timezone": "Asia/Shanghai",
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
        assert definition["mode_id"] == "CUSTOM_SUN_MOON"
        assert definition["content"]["type"] == "computed"
        assert definition["content"]["provider"] == "sun_moon"


def test_mode_source_and_deploy_match():
    assert MODE_SOURCE.read_text(encoding="utf-8") == MODE_DEPLOY.read_text(encoding="utf-8")


def test_registered_in_catalog():
    from core.mode_catalog import BUILTIN_CATALOG

    ids = {item.mode_id for item in BUILTIN_CATALOG}
    assert "CUSTOM_SUN_MOON" in ids


def test_settings_schema_declares_one_location():
    """自选城市：模式自己声明一个地点槽位，前端据此给城市选择器。"""
    schema = _mode().get("settings_schema") or []
    locations = [item for item in schema if item.get("type") == "location"]
    assert len(locations) == 1, "日昼与月相自选一座城市"
    assert locations[0]["key"] == "city"
    assert locations[0].get("label")


def test_schema_file_allows_location_type():
    """模式 schema 规范里要认 location 类型（诗笺日课/日课早报已经在用）。"""
    schema_path = BACKEND_DIR / "core" / "modes" / "schema" / "mode_schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    item_schema = schema["properties"]["settings_schema"]["items"]
    assert "location" in item_schema["properties"]["type"]["enum"]


def test_no_hardcoded_city_in_mode():
    """模式里不能再写死城市：描述、兜底、页脚都要跟着地点走。"""
    mode = _mode()
    assert "郑州" not in mode["description"]
    assert mode["content"]["fallback"]["city"] == ""
    assert mode["layout"]["footer"]["attribution_template"] == "{city} {date_cn}"


# ── 地点解析 ────────────────────────────────────────────────
def test_follows_device_location_without_override():
    import core.sun_moon as sm

    resolved = sm._resolve_location({**DEVICE, "mode_overrides": {}})
    assert resolved["city"] == "郑州"
    assert resolved["latitude"] == 34.75
    assert resolved["longitude"] == 113.65
    assert resolved["timezone"] == "Asia/Shanghai"


def test_override_city_wins():
    import core.sun_moon as sm

    cfg = {
        **DEVICE,
        "mode_overrides": {
            "CUSTOM_SUN_MOON": {
                "city": "上海",
                "latitude": 31.23,
                "longitude": 121.47,
                "timezone": "Asia/Shanghai",
            }
        },
    }
    resolved = sm._resolve_location(cfg)
    assert resolved["city"] == "上海"
    assert resolved["latitude"] == 31.23
    assert resolved["longitude"] == 121.47


def test_override_city_without_coords_does_not_mix_with_device():
    """只给了城市名时，坐标要跟着城市走，不能沿用设备原来的坐标。"""
    import core.sun_moon as sm

    cfg = {
        **DEVICE,
        "mode_overrides": {"CUSTOM_SUN_MOON": {"city": "杭州"}},
    }
    resolved = sm._resolve_location(cfg)
    assert resolved["city"] == "杭州"
    assert resolved["latitude"] == pytest.approx(30.27, abs=0.05)
    assert resolved["longitude"] == pytest.approx(120.15, abs=0.05)


def test_city_name_wins_when_coords_disagree():
    """预览只改了城市名、坐标还是旧的：这时以城市名为准。"""
    import core.sun_moon as sm

    resolved = sm._resolve_location({**DEVICE, "city": "杭州"})
    assert resolved["city"] == "杭州"
    assert resolved["latitude"] == pytest.approx(30.27, abs=0.05)


def test_other_mode_override_is_ignored():
    """别的模式的覆盖不影响本模式。"""
    import core.sun_moon as sm

    cfg = {
        **DEVICE,
        "mode_overrides": {"WEATHER": {"city": "上海", "latitude": 31.23, "longitude": 121.47}},
    }
    resolved = sm._resolve_location(cfg)
    assert resolved["city"] == "郑州"
    assert resolved["latitude"] == 34.75


def test_effective_config_promotes_mode_override():
    """模式覆盖里的地点要进到有效配置顶层（provider 拿得到）。"""
    from core.pipeline import get_effective_mode_config

    cfg = {
        **DEVICE,
        "mode_overrides": {
            "CUSTOM_SUN_MOON": {
                "city": "上海",
                "latitude": 31.23,
                "longitude": 121.47,
                "timezone": "Asia/Shanghai",
            }
        },
    }
    effective = get_effective_mode_config(cfg, "CUSTOM_SUN_MOON")
    assert effective["city"] == "上海"
    assert effective["latitude"] == 31.23
    assert effective["longitude"] == 121.47
    # 覆盖本身也要留着，provider 按地点整体解析
    assert effective["mode_overrides"]["CUSTOM_SUN_MOON"]["city"] == "上海"


# ── provider ────────────────────────────────────────────────
def _rows(offset_seconds: int, *, daylight_window: str = "day") -> dict:
    """造一份 Open-Meteo 形状的返回，日期按该地点的当地时间算。"""
    now_utc = datetime.now(timezone.utc)
    local = (now_utc + timedelta(seconds=offset_seconds)).replace(tzinfo=None)
    date_key = local.strftime("%Y-%m-%d")
    yesterday = (local - timedelta(days=1)).strftime("%Y-%m-%d")
    if daylight_window == "day":
        sunrise = f"{date_key}T00:00"
        sunset = f"{date_key}T23:59"
    else:
        start = (local.hour + 2) % 24
        sunrise = f"{date_key}T{start:02d}:00"
        end = f"{date_key}T23:59" if start >= 23 else f"{date_key}T{start + 1:02d}:00"
        sunset = end
    return {
        "rows": [
            {"date": yesterday, "sunrise": f"{yesterday}T06:00", "sunset": f"{yesterday}T18:00", "daylight": 40000},
            {"date": date_key, "sunrise": sunrise, "sunset": sunset, "daylight": 41400},
        ],
        "timezone": "Asia/Shanghai",
        "utc_offset_seconds": offset_seconds,
    }


@pytest.fixture()
def sandbox(monkeypatch, tmp_path):
    """缓存和月相图都写到临时目录，不碰运行时数据。"""
    import core.sun_moon as sm

    monkeypatch.setattr(sm, "CACHE_FILE", tmp_path / "sun_moon.json")
    monkeypatch.setattr(sm, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(sm, "MOON_DIR", tmp_path / "moon")
    return tmp_path


def _install_fakes(monkeypatch, by_city: dict, seen: list, prompts: list):
    import core.content as content
    import core.sun_moon as sm

    async def fake_fetch(location):
        seen.append(dict(location))
        return by_city[location["city"]]

    async def fake_llm(provider, model, prompt, **kwargs):
        prompts.append(prompt)
        for city in by_city:
            if f"城市：{city}" in prompt:
                return f"{city}的风轻轻吹过"
        return "风轻轻吹过"

    monkeypatch.setattr(sm, "_fetch_daily", fake_fetch)
    monkeypatch.setattr(content, "_call_llm", fake_llm)


@pytest.mark.asyncio
async def test_provider_uses_device_location(monkeypatch, sandbox):
    """没有模式覆盖 → 跟着设备全局位置。"""
    import core.sun_moon as sm

    seen: list = []
    prompts: list = []
    _install_fakes(monkeypatch, {"郑州": _rows(28800)}, seen, prompts)

    content = await sm.build_sun_moon_content(config={**DEVICE, "mode_overrides": {}})

    assert content["city"] == "郑州"
    assert seen[0]["latitude"] == 34.75
    assert seen[0]["longitude"] == 113.65
    assert content["sunrise"] == "00:00"
    assert content["sunset"] == "23:59"
    assert content["daylight"] == "11小时30分"
    assert content["period"] == "day"
    assert "城市：郑州" in prompts[0]


@pytest.mark.asyncio
async def test_provider_uses_selected_city(monkeypatch, sandbox):
    """自选了城市 → 用自选城市的日出日落。"""
    import core.sun_moon as sm

    seen: list = []
    prompts: list = []
    _install_fakes(
        monkeypatch,
        {"郑州": _rows(28800), "上海": _rows(28800)},
        seen,
        prompts,
    )

    cfg = {
        **DEVICE,
        "mode_overrides": {
            "CUSTOM_SUN_MOON": {
                "city": "上海",
                "latitude": 31.23,
                "longitude": 121.47,
                "timezone": "Asia/Shanghai",
            }
        },
    }
    content = await sm.build_sun_moon_content(config=cfg)

    assert content["city"] == "上海"
    assert seen[0]["latitude"] == 31.23
    assert seen[0]["longitude"] == 121.47
    assert "城市：上海" in prompts[0]


@pytest.mark.asyncio
async def test_period_follows_location_local_time(monkeypatch, sandbox):
    """昼夜判定用地点当地时间：同一个瞬间，远西城市已经是夜里。"""
    import core.sun_moon as sm

    seen: list = []
    prompts: list = []
    _install_fakes(
        monkeypatch,
        {"上海": _rows(28800), "远西": _rows(-43200, daylight_window="night")},
        seen,
        prompts,
    )

    day_cfg = {
        **DEVICE,
        "mode_overrides": {
            "CUSTOM_SUN_MOON": {"city": "上海", "latitude": 31.23, "longitude": 121.47}
        },
    }
    night_cfg = {
        **DEVICE,
        "mode_overrides": {
            "CUSTOM_SUN_MOON": {"city": "远西", "latitude": -16.9, "longitude": -177.4}
        },
    }

    day = await sm.build_sun_moon_content(config=day_cfg)
    night = await sm.build_sun_moon_content(config=night_cfg)

    assert day["period"] == "day"
    assert night["period"] == "night"


@pytest.mark.asyncio
async def test_cache_isolated_per_city(monkeypatch, sandbox):
    """同一天不同城市：每日数据和 LLM 文案各自分桶，互不污染。"""
    import core.sun_moon as sm

    seen: list = []
    prompts: list = []
    _install_fakes(
        monkeypatch,
        {"郑州": _rows(28800), "上海": _rows(28800)},
        seen,
        prompts,
    )

    cfg_zhengzhou = {**DEVICE, "mode_overrides": {}}
    cfg_shanghai = {
        **DEVICE,
        "mode_overrides": {
            "CUSTOM_SUN_MOON": {"city": "上海", "latitude": 31.23, "longitude": 121.47}
        },
    }

    zhengzhou = await sm.build_sun_moon_content(config=cfg_zhengzhou)
    shanghai = await sm.build_sun_moon_content(config=cfg_shanghai)

    assert zhengzhou["line"] != shanghai["line"]

    cache = json.loads((sandbox / "sun_moon.json").read_text(encoding="utf-8"))
    buckets = cache["locations"]
    assert "郑州|34.750|113.650" in buckets
    assert "上海|31.230|121.470" in buckets

    zhengzhou_llm = buckets["郑州|34.750|113.650"]["llm"]
    shanghai_llm = buckets["上海|31.230|121.470"]["llm"]
    assert set(zhengzhou_llm) == set(shanghai_llm), "同一天同昼夜，键应一致"
    assert zhengzhou_llm != shanghai_llm, "不同城市的文案不能共用"


@pytest.mark.asyncio
async def test_llm_cache_hit_skips_second_call(monkeypatch, sandbox):
    """同一城市同一天再取一次，不重复调模型。"""
    import core.sun_moon as sm

    seen: list = []
    prompts: list = []
    _install_fakes(monkeypatch, {"郑州": _rows(28800)}, seen, prompts)

    cfg = {**DEVICE, "mode_overrides": {}}
    first = await sm.build_sun_moon_content(config=cfg)
    calls_after_first = len(prompts)
    second = await sm.build_sun_moon_content(config=cfg)

    assert first["line"] == second["line"]
    assert len(prompts) == calls_after_first, "命中缓存后不该再调模型"


def test_legacy_cache_migrates_to_location_bucket(monkeypatch, tmp_path):
    """旧缓存（写死郑州）自动搬到郑州的桶里，不丢已生成的文案。"""
    import core.sun_moon as sm

    cache_file = tmp_path / "sun_moon.json"
    cache_file.write_text(
        json.dumps(
            {
                "daily": {"rows": [{"date": "2026-10-29", "sunrise": "2026-10-29T06:42"}]},
                "daily_ts": 1,
                "llm": {"2026-10-29_day": "旧文案"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(sm, "CACHE_FILE", cache_file)

    cache = sm._read_cache()
    bucket = cache["locations"]["郑州|34.750|113.650"]
    assert bucket["llm"]["2026-10-29_day"] == "旧文案"
    assert bucket["daily"]["rows"][0]["date"] == "2026-10-29"


@pytest.mark.asyncio
async def test_fetch_failure_still_renders(monkeypatch, sandbox):
    """抓不到日出日落也不能空屏：数值留占位，LLM 照常出一句话。"""
    import core.content as content_module
    import core.sun_moon as sm

    async def boom(location):
        raise RuntimeError("network down")

    async def fake_llm(provider, model, prompt, **kwargs):
        return "天光正好"

    monkeypatch.setattr(sm, "_fetch_daily", boom)
    monkeypatch.setattr(content_module, "_call_llm", fake_llm)

    content = await sm.build_sun_moon_content(config={**DEVICE, "mode_overrides": {}})
    assert content["sunrise"] == "--:--"
    assert content["sunset"] == "--:--"
    assert content["line"]


# ── 四色版 ────────────────────────────────────────────────────
def test_moon_png_is_flat_four_color():
    """月相图只用屏上四色原色平涂：受光金黄、背影墨黑、外圈留白。"""
    from PIL import Image
    import core.sun_moon as sm

    path = Path(__file__).parent / "_tmp_moon_v4.png"
    try:
        assert sm.render_moon_png(0.32, path)
        img = Image.open(path).convert("RGB")
        colors = {c for _, c in img.getcolors(999999)}
    finally:
        path.unlink(missing_ok=True)

    assert colors <= {sm.EINK_WHITE_RGB, sm.EINK_BLACK_RGB, sm.EINK_GOLD_RGB}, colors
    assert sm.EINK_GOLD_RGB in colors, "受光面必须是金黄"
    assert sm.EINK_BLACK_RGB in colors, "背影面必须是墨黑"
    assert sm.EINK_RED_RGB not in colors, "月相图不占用朱红"


def test_day_arc_png_is_gold_band_with_black_arc():
    """日轨图：金黄白昼带 + 墨黑弧线 + 墨黑当前点。"""
    from datetime import datetime
    from PIL import Image
    import core.sun_moon as sm

    path = Path(__file__).parent / "_tmp_arc.png"
    try:
        ok = sm.render_day_arc_png(
            datetime(2026, 11, 3, 6, 35), datetime(2026, 11, 3, 17, 45),
            datetime(2026, 11, 3, 12, 0), path,
        )
        assert ok
        img = Image.open(path).convert("RGB")
        colors = {c for _, c in img.getcolors(999999)}
    finally:
        path.unlink(missing_ok=True)

    assert sm.EINK_GOLD_RGB in colors, "白昼带必须是金黄"
    assert sm.EINK_BLACK_RGB in colors, "弧线和当前点必须是墨黑"
    assert sm.EINK_RED_RGB not in colors, "日轨不占用朱红"


def test_day_arc_height_tracks_daylight_length():
    """白昼越长弧越高：弧顶像素的 y 值更小。"""
    from datetime import datetime
    from PIL import Image
    import core.sun_moon as sm

    def arc_top(sunrise_h, sunset_h):
        path = Path(__file__).parent / "_tmp_arc2.png"
        try:
            sm.render_day_arc_png(
                datetime(2026, 11, 3, sunrise_h, 0), datetime(2026, 11, 3, sunset_h, 0),
                datetime(2026, 11, 3, 12, 0), path,
            )
            img = Image.open(path).convert("RGB")
            w, h = img.size
            px = img.load()
            for y in range(h):
                for x in range(w):
                    if px[x, y] == sm.EINK_BLACK_RGB:
                        return y
        finally:
            path.unlink(missing_ok=True)
        return h

    short = arc_top(9, 15)   # 白昼 6 小时
    long = arc_top(4, 20)    # 白昼 16 小时
    assert long < short, f"白昼长的弧应更高：long={long} short={short}"


def test_mode_definition_uses_colors():
    """模式定义：日出金黄、日落朱红、月相名金黄。"""
    mode = _mode()
    cond = mode["layout"]["body"][0]
    day = cond["conditions"][0]["children"]
    night = cond["conditions"][1]["children"]
    assert day[0]["left"][0]["color"] == "yellow"
    assert day[0]["right"][0]["color"] == "red"
    assert any(b.get("field") == "day_arc" for b in day), "白天侧要有日轨图"
    moon_title = next(b for b in night if b.get("type") == "text" and b.get("template") == "{moon_name}")
    assert moon_title["color"] == "yellow"


def test_day_arc_only_in_day_period(monkeypatch, tmp_path):
    """日轨只白天生成：夜间不画弧。"""
    import core.sun_moon as sm

    monkeypatch.setattr(sm, "ARC_DIR", tmp_path / "arc")
    monkeypatch.setattr(sm, "MOON_DIR", tmp_path / "moon")

    from datetime import datetime as _dt, timezone as _tz
    async def fake_fetch(loc):
        return {
            "rows": [{
                "date": _dt.now(_tz.utc).strftime("%Y-%m-%d"),
                "sunrise": "2026-11-03T06:35", "sunset": "2026-11-03T17:45", "daylight": 40200,
            }],
            "timezone": "Asia/Shanghai", "utc_offset_seconds": 28800,
        }

    async def fake_llm(*a, **k):
        return "天光正好"

    monkeypatch.setattr(sm, "_fetch_daily", fake_fetch)
    monkeypatch.setattr(sm, "_llm_line", fake_llm)

    cfg = {**DEVICE, "mode_overrides": {}}
    content = asyncio.run(sm.build_sun_moon_content(config=cfg))
    assert content["period"] in ("day", "night")
    if content["period"] == "day":
        assert content["day_arc"].startswith("/static/arc/")
    else:
        assert content["day_arc"] == ""
