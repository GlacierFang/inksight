#!/usr/bin/env python3
"""每天 05:00 写一行日痕（systemd user timer 调用）。

与屏幕刷新路径共用同一把锁和同一份状态：
- 今天的字已经写就直接用，不会重复调用模型。
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
    from core.rihen import generate_today_mark, read_history

    line = await generate_today_mark()
    if not line:
        print("[rihen] 今日写入失败（锁未释放或状态异常）")
        return 1
    history = read_history()
    print(f"[rihen] {history['volume_label']} | {history['volume']} | 累计 {len(history['days'])} 天")
    print(f"      {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
