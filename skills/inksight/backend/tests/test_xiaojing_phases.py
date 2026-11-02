"""小景多时段回顾测试。

小景一天出四张（晨/昼/暮/夜），回顾里必须一天四张，不能互相覆盖。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def test_screen_path_separates_phases():
    """多时段模式一天多张：路径按 <日期>_<时段> 分开。"""
    from core.history_render import screen_path

    assert screen_path("xiaojing", "2026-11-02", "dawn").name == "2026-11-02_dawn.png"
    assert screen_path("xiaojing", "2026-11-02", "night").name == "2026-11-02_night.png"
    # 普通模式仍是一天一张
    assert screen_path("window", "2026-11-02").name == "2026-11-02.png"


def test_state_phase_reads_current_phase(tmp_path, monkeypatch):
    """存档自己从状态里读当天时段，调用方只传日期。"""
    from core import history_render

    state_dir = tmp_path / "xiaojing"
    state_dir.mkdir(parents=True)
    (state_dir / "state.json").write_text(
        json.dumps({"days": {"2026-11-02": {"phase": "dusk"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(history_render, "STATE_DIR", tmp_path)

    assert history_render.state_phase("xiaojing", "2026-11-02") == "dusk"
    # 非多时段模式没有时段概念
    assert history_render.state_phase("window", "2026-11-02") == ""


def test_gallery_entries_expand_one_day_into_four(tmp_path, monkeypatch):
    """一天四条：晨/昼/暮/夜 各一条，不互相覆盖。"""
    from core import gallery

    state_dir = tmp_path / "xiaojing"
    screens = state_dir / "screens"
    screens.mkdir(parents=True)
    for phase in ("dawn", "day", "dusk", "night"):
        (screens / f"2026-11-02_{phase}.png").write_bytes(b"x")
    (state_dir / "state.json").write_text(
        json.dumps(
            {
                "days": {"2026-11-02": {"date": "2026-11-02", "phase": "dusk"}},
                "phases": {
                    "2026-11-02": {
                        p: {
                            "date": "2026-11-02",
                            "phase": p,
                            "phase_label": label,
                            "art_url": f"/static/xiaojing/scene_20261102_{p}.png",
                        }
                        for p, label in (("dawn", "晨"), ("day", "昼"), ("dusk", "暮"), ("night", "夜"))
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(gallery, "STATE_DIR", tmp_path)
    monkeypatch.setattr(gallery.history_render, "STATE_DIR", tmp_path)

    entries = gallery.entries("CUSTOM_XIAOJING")
    assert len(entries) == 4
    assert {e["phase"] for e in entries} == {"dawn", "day", "dusk", "night"}
    assert {e["date"] for e in entries} == {"2026-11-02"}
    # 中文时段名是给人看的
    assert {e["title"] for e in entries} == {"晨", "昼", "暮", "夜"}


def test_gallery_single_phase_mode_unchanged(tmp_path, monkeypatch):
    """非多时段模式不受影响：一天一条，路径不带时段。"""
    from core import gallery

    state_dir = tmp_path / "window"
    screens = state_dir / "screens"
    screens.mkdir(parents=True)
    (screens / "2026-11-02.png").write_bytes(b"x")
    (state_dir / "state.json").write_text(
        json.dumps({"days": {"2026-11-02": {"city_name": "郑州", "weather_text": "阴 19°C"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(gallery, "STATE_DIR", tmp_path)
    monkeypatch.setattr(gallery.history_render, "STATE_DIR", tmp_path)

    entries = gallery.entries("CUSTOM_WINDOW")
    assert len(entries) == 1
    assert entries[0]["phase"] == ""
    assert entries[0]["title"] == "郑州"


def test_reconstruct_phases_from_disk(tmp_path, monkeypatch):
    """老存档只有 days：素材图在磁盘上，时段记录补得回来。"""
    from core import xiaojing

    art_dir = tmp_path / "static" / "xiaojing"
    art_dir.mkdir(parents=True)
    for phase in ("dawn", "day"):
        (art_dir / f"scene_20261101_{phase}.png").write_bytes(b"x")

    monkeypatch.setattr(xiaojing, "ART_DIR", art_dir)

    state = {"days": {"2026-11-01": {"date": "2026-11-01", "phase": "day"}}}
    phases = xiaojing.reconstruct_phases(state)
    assert set(phases.get("2026-11-01", {})) == {"dawn", "day"}
    assert phases["2026-11-01"]["dawn"]["phase_label"] == "晨"


@pytest.mark.asyncio
async def test_phase_override_does_not_change_current(monkeypatch, tmp_path):
    """回填指定时段时，不改「设备此刻该显示哪张」。"""
    from core import xiaojing

    monkeypatch.setattr(xiaojing, "ART_DIR", tmp_path)
    monkeypatch.setattr(xiaojing, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(xiaojing, "STATE_FILE", tmp_path / "state" / "state.json")
    monkeypatch.setattr(xiaojing, "LOCK_FILE", tmp_path / "state" / "generate.lock")

    async def _fake_phase(config):
        return "dusk", None

    monkeypatch.setattr(xiaojing, "current_phase", _fake_phase)

    # 预置：今天正在显示暮
    state_file = tmp_path / "state" / "state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        json.dumps(
            {
                "days": {
                    "2026-11-02": {
                        "date": "2026-11-02",
                        "phase": "dusk",
                        "phase_label": "暮",
                        "art_url": "/static/xiaojing/scene_20261102_dusk.png",
                        "has_image": "yes",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    content = await xiaojing.build_xiaojing_content(
        fallback={},
        date_ctx={
            "year": 2026,
            "month": 11,
            "day": 2,
            "phase_override": "dawn",
        },
    )

    assert content["phase_key"] == "dawn"
    # 顶层「当前显示哪张」没被动
    saved = json.loads(state_file.read_text(encoding="utf-8"))
    assert saved["days"]["2026-11-02"]["phase"] == "dusk"
    # 但 dawn 这一时段被记下来了
    assert "dawn" in saved["phases"]["2026-11-02"]
