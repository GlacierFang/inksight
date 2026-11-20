#!/usr/bin/env python3
"""每天 06:00 掷骰子，生成草地铺道蚁当天的记录（systemd user timer 调用）。

与屏幕刷新路径共用同一把锁和同一份状态：
- 当天的骰子已经掷过就直接用，不会重复调用模型。
- 模型失灵不抛错，provider 内部会退到手写兜底短句，当天不空。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


async def main() -> int:
    from core.subscription import subscribed_modes

    if "CUSTOM_ANT" not in await subscribed_modes():
        print("[ant] 未被订阅，跳过", flush=True)
        return 0

    from core.ant import generate_today_card

    card = await generate_today_card()
    if not card:
        print("[ant] 今日记录生成失败（锁未释放或状态异常）")
        return 1
    dice = card.get("dice") or {}
    print(
        f"[ant] {card.get('date')} | {card.get('title')} | "
        f"骰子 {dice.get('action_name')}/{dice.get('magnitude')}"
        f"{'（反常）' if dice.get('anomaly') else ''} | "
        f"行程 {card.get('trip_m')} 米"
    )
    for line in card.get("lines") or []:
        print(f"      {line}")
    if card.get("voice"):
        print(f"      「{card.get('voice')}」")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
