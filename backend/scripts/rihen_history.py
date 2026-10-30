#!/usr/bin/env python3
"""读回日痕的全部记录——屏幕只留最近几行，旧的字都在这里。

用法：
    python3 scripts/rihen_history.py            # 当前册 + 最近 30 天
    python3 scripts/rihen_history.py --all      # 全部记录（含已归档的册）
    python3 scripts/rihen_history.py --volume 2 # 只看某一册
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def main() -> int:
    ap = argparse.ArgumentParser(description="日痕历史")
    ap.add_argument("--all", action="store_true", help="连同已归档的册一起打印")
    ap.add_argument("--volume", type=int, default=0, help="只看某一册")
    args = ap.parse_args()

    from core.rihen import read_history

    history = read_history()
    days = history["days"]

    if args.volume:
        days = [d for d in days if int(d.get("volume") or 0) == args.volume]

    print(f"当前{history['volume_label']}｜共 {len(history['days'])} 天")
    print("-" * 40)
    for day in days:
        print(f"{day.get('date')}  {day.get('text')}")

    if args.all:
        for arch in history["archived"]:
            print("-" * 40)
            print(f"已归档 {arch.get('volume')} 册  {arch.get('from')} → {arch.get('to')}")
            for day in arch.get("days") or []:
                print(f"{day.get('date')}  {day.get('text')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
