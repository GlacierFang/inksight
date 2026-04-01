#!/usr/bin/env python3
"""
MiniMax API 调用日志记录器
记录每次 API 调用的时间戳，用于 5 小时窗口统计
"""
import json
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta

LOG_FILE = Path("/home/open/.openclaw/workspace/memory/minimax-api-log.json")
TZ = timezone(timedelta(hours=8))  # UTC+8

def get_window():
    """获取当前 5 小时窗口"""
    now = datetime.now(TZ)
    hour = now.hour
    # 窗口: 00-04, 05-09, 10-14, 15-19, 20-23
    window_start = (hour // 5) * 5
    window_end = window_start + 5
    start_time = now.replace(hour=window_start, minute=0, second=0, microsecond=0)
    end_time = start_time + timedelta(hours=5)
    return start_time, end_time

def get_window_key():
    """获取窗口标识 (如 '15-20')"""
    start, end = get_window()
    return f"{start.strftime('%H')}-{end.strftime('%H')}"

def load_log():
    """加载日志"""
    if LOG_FILE.exists():
        try:
            with open(LOG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {"calls": []}

def save_log(log):
    """保存日志"""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, 'w', encoding='utf-8') as f:
        json.dump(log, f, ensure_ascii=False, indent=2)

def log_api_call(source, count=1):
    """记录 API 调用"""
    log = load_log()
    now = datetime.now(TZ)
    window_start, window_end = get_window()
    
    for _ in range(count):
        log["calls"].append({
            "timestamp": now.isoformat(),
            "source": source,
            "window": get_window_key()
        })
    
    # 清理超过 24 小时的记录
    cutoff = now - timedelta(hours=24)
    log["calls"] = [
        c for c in log["calls"]
        if datetime.fromisoformat(c["timestamp"]).replace(tzinfo=TZ) > cutoff
    ]
    
    save_log(log)
    return len(log["calls"])

def get_stats():
    """获取当前窗口的统计"""
    log = load_log()
    now = datetime.now(TZ)
    window_start, window_end = get_window()
    current_window = get_window_key()
    
    # 筛选当前窗口内的调用
    window_calls = [
        c for c in log["calls"]
        if c.get("window") == current_window
        and datetime.fromisoformat(c["timestamp"]).replace(tzinfo=TZ) >= window_start
        and datetime.fromisoformat(c["timestamp"]).replace(tzinfo=TZ) < window_end
    ]
    
    # 按来源分组统计
    stats = {}
    for c in window_calls:
        source = c.get("source", "unknown")
        stats[source] = stats.get(source, 0) + 1
    
    total = len(window_calls)
    
    return {
        "window": current_window,
        "window_start": window_start.strftime("%H:%M"),
        "window_end": window_end.strftime("%H:%M"),
        "total": total,
        "remaining": max(0, 600 - total),
        "by_source": stats,
        "log_count": len(log["calls"])  # 总记录数（24小时内）
    }

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        if sys.argv[1] == "log":
            source = sys.argv[2] if len(sys.argv) > 2 else "test"
            count = int(sys.argv[3]) if len(sys.argv) > 3 else 1
            log_api_call(source, count)
            print(f"Logged: {source} x{count}")
        elif sys.argv[1] == "stats":
            print(json.dumps(get_stats(), indent=2, ensure_ascii=False))
