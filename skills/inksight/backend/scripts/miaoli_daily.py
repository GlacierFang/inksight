#!/usr/bin/env python3
"""写出喵狸今天的画面，出一张日漫图（systemd user timer 调用）。

当天已经出过图就直接用，不会重复调用模型、不会重复扣费。
模型失灵不抛错，provider 内部会退到兜底画面，当天不空。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


async def main() -> int:
    from core.miaoli import tick

    record = await tick()
    print(
        "[miaoli] {date} | {title} | 图={has_image}".format(
            date=record.get("date", ""),
            title=record.get("title", ""),
            has_image=record.get("has_image", "no"),
        )
    )
    return 0 if record.get("has_image") == "yes" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
