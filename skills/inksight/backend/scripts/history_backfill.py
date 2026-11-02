#!/usr/bin/env python3
"""一次性历史回填：把各模式真实存在过的那几天，走真实渲染管线出整屏图并落盘。

边界（严禁无限扩充）：
- 只处理这 7 个模式；
- 每天的清单来自各模式自己的 state.json —— 那天没有记录就不出图；
- 电子世界按 epoch→今天（今天是上限）；
- 跑完就结束，不追不补不虚构。

用法：
    python3 scripts/history_backfill.py              # 回填全部
    python3 scripts/history_backfill.py --dry-run    # 只看清单
    python3 scripts/history_backfill.py --force      # 已存在的也重出（布局改了就用这个）
    python3 scripts/history_backfill.py CUSTOM_HAICUOTU
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import date, timedelta
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from core import gallery, history_render  # noqa: E402

# 电子世界的起算日：数据集里的 epoch
def _eworld_epoch() -> date:
    try:
        data = json.loads((BACKEND_DIR / "core/data/eworld.json").read_text(encoding="utf-8"))
        return date.fromisoformat(str(data["epoch"]))
    except (OSError, ValueError, KeyError):
        return date(2026, 10, 28)


def _days_for(mode_id: str) -> list[str]:
    """这个模式真实存在过的那几天（已去重、已排序）。"""
    spec = gallery._spec(mode_id)
    if not spec:
        return []
    if spec.get("form") == "wide":
        # 电子世界：从 epoch 演到今天为止
        epoch = _eworld_epoch()
        today = date.today()
        out = []
        cur = epoch
        while cur <= today:
            out.append(cur.isoformat())
            cur += timedelta(days=1)
        return out

    state = gallery._read_state(str(spec.get("state") or ""))
    return [day for day, _item in gallery._rows(state, spec)]


async def main(argv: list[str]) -> int:
    dry_run = "--dry-run" in argv
    # --force：已存在的整屏图也重出。布局/排版改过之后，旧图留在回顾页上
    # 就是错的 —— 页面上看到的应该是当前管线出的图。
    force = "--force" in argv
    modes = [a.upper() for a in argv if not a.startswith("-")]
    if not modes:
        modes = list(gallery._MODES.keys())

    total = 0
    done = 0
    for mode_id in modes:
        spec = gallery._spec(mode_id)
        if not spec:
            print(f"跳过未知模式 {mode_id}")
            continue
        state_name = str(spec.get("state") or "")
        days = _days_for(mode_id)
        total += len(days)
        print(f"\n=== {spec['name']} ({mode_id}) —— {len(days)} 天")
        for day in days:
            target = history_render.screen_path(state_name, day)
            if target.is_file() and not force:
                print(f"  已有 {day}")
                done += 1
                continue
            if dry_run:
                print(f"  待出 {day}")
                continue
            data = await history_render.render_day(mode_id, day)
            if not data:
                print(f"  失败 {day}")
                continue
            history_render.save_png(state_name, day, data)
            done += 1
            print(f"  出图 {day} ({len(data)} bytes)")

    print(f"\n合计 {total} 天，已完成 {done} 天" + ("（dry-run，未出图）" if dry_run else ""))
    return 0


if __name__ == "__main__":
    async def _run() -> int:
        try:
            return await main(sys.argv[1:])
        finally:
            # 必须显式关数据库连接：aiosqlite 的工作线程不是 daemon，
            # 不关的话进程会在活干完之后卡住不退。
            try:
                from core.db import close_all

                await close_all()
            except Exception as exc:
                print(f"[backfill] 关连接失败: {exc}", flush=True)

    raise SystemExit(asyncio.run(_run()))
