"""「节气倒计时」模式（CUSTOM_SOLAR_TERMS）测试。

覆盖：
  - 模式 JSON：源文件/部署副本合法且一致，注册进目录；
  - provider：当年 24 个节气、两列各 12 个、过去/将来/当天的文案口径；
  - 数据边界：表内年份用表，表外年份（2031 起）用寿星公式推算；
  - 派发：json_content 里有对应 provider 分支；
  - 纯本地：结果确定，不联网、不调 LLM。
"""
from __future__ import annotations

import asyncio
import inspect
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

MODE_SOURCE = BACKEND_DIR.parent / "modes-ours" / "solar_terms.json"
MODE_DEPLOY = BACKEND_DIR / "core" / "modes" / "builtin" / "custom_solar_terms.json"

TERM_NAMES = (
    "小寒", "大寒", "立春", "雨水", "惊蛰", "春分",
    "清明", "谷雨", "立夏", "小满", "芒种", "夏至",
    "小暑", "大暑", "立秋", "处暑", "白露", "秋分",
    "寒露", "霜降", "立冬", "小雪", "大雪", "冬至",
)


def _mode() -> dict:
    return json.loads(MODE_DEPLOY.read_text(encoding="utf-8"))


def _ctx(year: int, month: int, day: int) -> dict:
    return {"year": year, "month": month, "day": day}


def _build(year: int, month: int, day: int, **kwargs) -> dict:
    import core.solar_terms as st

    return asyncio.run(
        st.build_solar_terms_content({}, date_ctx=_ctx(year, month, day), **kwargs)
    )


def _days_map(content: dict) -> dict:
    out: dict = {}
    for row in content["rows"]:
        out[row["l_name"]] = row["l_days"]
        out[row["r_name"]] = row["r_days"]
    return out


# ── 模式文件 ────────────────────────────────────────────────
def test_mode_json_validates():
    from core.mode_registry import _validate_mode_def_with_error

    for path in (MODE_SOURCE, MODE_DEPLOY):
        assert path.exists(), f"缺少模式文件：{path}"
        definition = json.loads(path.read_text(encoding="utf-8"))
        ok, err = _validate_mode_def_with_error(definition)
        assert ok, f"{path}: {err}"
        assert definition["mode_id"] == "CUSTOM_SOLAR_TERMS"
        assert definition["content"]["type"] == "computed"
        assert definition["content"]["provider"] == "solar_terms"


def test_mode_source_and_deploy_match():
    assert MODE_SOURCE.read_text(encoding="utf-8") == MODE_DEPLOY.read_text(encoding="utf-8")


def test_registered_in_catalog():
    from core.mode_catalog import BUILTIN_CATALOG

    ids = {item.mode_id for item in BUILTIN_CATALOG}
    assert "CUSTOM_SOLAR_TERMS" in ids


def test_provider_branch_exists():
    import core.json_content as jc

    assert "solar_terms" in inspect.getsource(jc._generate_computed_content)


# ── provider ────────────────────────────────────────────────
def test_year_has_24_terms():
    import core.solar_terms as st

    terms = st.terms_of_year(2026)
    assert len(terms) == 24
    assert [name for _, name in terms] == list(TERM_NAMES)
    assert terms == sorted(terms), "节气必须按时间升序"


def test_two_columns_twelve_rows():
    """左列第 1-12 个、右列第 13-24 个，两列各自按时间顺序。"""
    content = _build(2026, 11, 2)
    rows = content["rows"]
    assert len(rows) == 12
    assert [r["l_name"] for r in rows] == list(TERM_NAMES[:12])
    assert [r["r_name"] for r in rows] == list(TERM_NAMES[12:])


def test_past_and_future_wording():
    content = _build(2026, 11, 2)
    days = _days_map(content)
    assert days["小寒"] == "已过301天"
    assert days["小暑"] == "已过118天"
    assert days["夏至"] == "已过134天"
    assert days["冬至"] == "还有50天"


def test_today_shows_今日():
    content = _build(2026, 10, 23)  # 霜降当天
    days = _days_map(content)
    assert days["霜降"] == "今日"
    assert content["current_name"] == "霜降"
    assert content["next_name"] == "立冬"
    assert content["next_days"] == 15


def test_current_and_next_header():
    content = _build(2026, 11, 2)
    assert content["title"] == "二十四节气"
    assert content["year"] == "2026"
    assert content["current_name"] == "霜降"
    assert content["current_index"] == "20/24"
    assert content["next_name"] == "立冬"
    assert content["next_days"] == 5


def test_year_end_next_term_is_next_year():
    content = _build(2026, 12, 31)
    assert content["current_name"] == "冬至"
    assert content["next_name"] == "小寒"
    assert content["next_days"] == 5  # 2027-01-05


def test_out_of_table_year_uses_formula():
    """表只到 2030，2031 起走寿星公式。"""
    import core.solar_terms as st

    assert not any(y == 2031 for (y, _m, _d), _n in st.SOLAR_TERMS.items())
    terms = st.terms_of_year(2031)
    assert len(terms) == 24
    assert [name for _, name in terms] == list(TERM_NAMES)


def test_build_for_out_of_table_year():
    content = _build(2031, 6, 1)
    assert content["year"] == "2031"
    assert len(content["rows"]) == 12


def test_terms_strictly_increasing_across_years():
    """任何年份（含闰年、表外年份）节气都严格递增、间隔合理。"""
    import core.solar_terms as st

    for year in (2026, 2028, 2031, 2032):
        terms = st.terms_of_year(year)
        assert len(terms) == 24, year
        for (d0, _), (d1, _) in zip(terms, terms[1:]):
            delta = (d1 - d0).days
            assert 0 < delta <= 20, f"{year}: {d0} → {d1} 间隔 {delta} 天不合理"


def test_english_wording():
    content = _build(2026, 10, 23, language="en")
    assert content["title"] == "Solar Terms"
    days = _days_map(content)
    assert days["霜降"] == "today"
    assert days["冬至"].startswith("in ")
    assert days["小寒"].endswith(" days ago")


def test_deterministic_and_offline():
    """节气是确定数据：不联网、不调 LLM、同输入同输出。"""
    import core.solar_terms as st

    source = inspect.getsource(st)
    assert "_call_llm" not in source
    assert "httpx" not in source and "requests" not in source

    assert _build(2026, 11, 2) == _build(2026, 11, 2)


def test_dispatch_through_json_content():
    from core.json_content import _generate_computed_content

    mode = _mode()
    content = asyncio.run(
        _generate_computed_content(mode, mode["content"], {}, date_ctx=_ctx(2026, 11, 2))
    )
    assert content["current_name"] == "霜降"
    assert content["current_index"] == "20/24"
    assert len(content["rows"]) == 12
