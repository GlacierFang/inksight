#!/usr/bin/env python3
"""按当前真实时段生成/更新「小景」（systemd user timer 调用）。

一天里跨了时段（晨→昼→暮→夜）就换一张新图；
同一个时段内重复触发不会重复生图、不会重复扣费。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


async def main() -> int:
    from core.xiaojing import tick

    record = await tick()
    print(
        f"[xiaojing] {record.get('date')} | 时段={record.get('phase')} | "
        f"图={record.get('has_image')} | {record.get('note')}"
    )
    return 0 if record.get("has_image") == "yes" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
