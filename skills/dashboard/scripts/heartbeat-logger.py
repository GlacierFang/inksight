#!/usr/bin/env python3
"""心跳记录脚本 - 由系统 cron 每分钟执行，不消耗 MiniMax 额度"""
import json
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=8))
LOG_FILE = Path("/home/open/.openclaw/workspace/memory/heartbeat-log.json")
MAX_ENTRIES = 200

def main():
    now = datetime.now(TZ)
    entry = {
        "ts": int(now.timestamp() * 1000),
        "time": now.strftime("%Y-%m-%d %H:%M:%S")
    }

    if LOG_FILE.exists():
        try:
            with open(LOG_FILE, 'r', encoding='utf-8') as f:
                logs = json.load(f)
        except:
            logs = []
    else:
        logs = []

    logs.append(entry)
    logs = logs[-MAX_ENTRIES:]

    with open(LOG_FILE, 'w', encoding='utf-8') as f:
        json.dump(logs, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
