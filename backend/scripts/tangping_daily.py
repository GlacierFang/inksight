#!/usr/bin/env python3
"""每天 04:00 生成躺平巴拉当天的梦境卡（systemd user timer 调用）。

与屏幕刷新路径共用同一把锁和同一份状态：
- 当天的卡已经存在就直接用，不会重复生图、重复扣费。
- 生图失败不抛错，provider 内部会退成纯文字卡。
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

    if "CUSTOM_TANGPING" not in await subscribed_modes():
        print("[tangping] 未被订阅，跳过", flush=True)
        return 0

    from core.tangping import generate_today_card

    card = await generate_today_card()
    if not card:
        print("[tangping] 今日卡片生成失败（锁未释放或状态异常）")
        return 1
    print(
        f"[tangping] {card.get('date')} | {card.get('place')} | {card.get('line')} | "
        f"图={card.get('has_image')} | {card.get('note')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
