#!/usr/bin/env python3
"""
个人工作台 - Flask 服务
端口：18794
"""

from flask import Flask, render_template, jsonify, request
from pathlib import Path
import json
import sqlite3
from datetime import datetime, timezone, timedelta
from collections import defaultdict

app = Flask(__name__)

# 路径配置
SKILLS_DIR = Path("/home/open/.openclaw/workspace/skills")
MEMORY_DIR = Path("/home/open/.openclaw/workspace/memory")
WORKSPACE_DIR = Path("/home/open/.openclaw/workspace")
GALLERY_DB = Path("/home/open/.openclaw/media/gallery.db")
API_LOG_FILE = Path("/home/open/.openclaw/workspace/memory/minimax-api-log.json")
PUBLISH_LOG = Path("/home/open/.openclaw/workspace/skills/dashboard/data/publish-log.json")
TZ = timezone(timedelta(hours=8))

# ============================================================
# 工具函数
# ============================================================

def get_window_key():
    """获取当前5小时窗口标识"""
    now = datetime.now(TZ)
    window_start = (now.hour // 5) * 5
    window_end = window_start + 5
    return f"{window_start:02d}-{window_end:02d}"

def get_week_key():
    """获取本周起始周一"""
    now = datetime.now(TZ)
    monday = now - timedelta(days=now.weekday())
    return monday.strftime("%Y-%m-%d")

def load_json(path, default=None):
    if path.exists():
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return default if default is not None else {}

def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ============================================================
# 路由
# ============================================================

@app.route("/")
def index():
    return render_template("dashboard.html")

# ============================================================
# API: 系统消耗统计
# ============================================================

@app.route("/api/stats")
def api_stats():
    """系统消耗统计（日/周/月）"""
    period = request.args.get("period", "day")  # day / week / month
    
    log = load_json(API_LOG_FILE, {"calls": []})
    now = datetime.now(TZ)
    
    # 计算时间范围
    if period == "day":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        monday = now - timedelta(days=now.weekday())
        start = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    else:  # month
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    
    # 筛选时间范围内的调用
    calls = [
        c for c in log.get("calls", [])
        if datetime.fromisoformat(c["timestamp"]).replace(tzinfo=TZ) >= start
    ]
    
    # 按来源分组
    by_source = defaultdict(int)
    total = len(calls)
    for c in calls:
        by_source[c.get("source", "unknown")] += 1
    
    # 当前窗口统计
    window_key = get_window_key()
    window_calls = [c for c in log.get("calls", []) if c.get("window") == window_key]
    
    # 各来源在当前窗口的数量
    window_by_source = defaultdict(int)
    for c in window_calls:
        window_by_source[c.get("source", "unknown")] += 1
    
    return jsonify({
        "period": period,
        "total_calls": total,
        "by_source": dict(by_source),
        "window_key": window_key,
        "window_calls": len(window_calls),
        "window_limit": 600,
        "window_by_source": dict(window_by_source),
        "range": {
            "start": start.strftime("%Y-%m-%d %H:%M"),
            "end": now.strftime("%Y-%m-%d %H:%M"),
        }
    })

@app.route("/api/stats-daily")
def api_stats_daily():
    """每日消耗趋势（最近30天）"""
    log = load_json(API_LOG_FILE, {"calls": []})
    now = datetime.now(TZ)
    
    # 最近30天
    days = defaultdict(lambda: defaultdict(int))
    for c in log.get("calls", []):
        ts = datetime.fromisoformat(c["timestamp"]).replace(tzinfo=TZ)
        days[ts.strftime("%Y-%m-%d")][c.get("source", "unknown")] += 1
    
    # 返回最近30天
    result = []
    for i in range(29, -1, -1):
        d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        by_source = dict(days[d])
        result.append({
            "date": d,
            "total": sum(by_source.values()),
            "by_source": by_source
        })
    
    return jsonify({"daily": result})

# ============================================================
# API: 自媒体发布
# ============================================================

@app.route("/api/publish")
def api_publish():
    """发布日志查询"""
    period = request.args.get("period", "month")  # day / week / month
    
    data = load_json(PUBLISH_LOG, {"records": []})
    records = data.get("records", [])
    now = datetime.now(TZ)
    
    if period == "day":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        monday = now - timedelta(days=now.weekday())
        start = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    
    # 筛选
    filtered = [
        r for r in records
        if datetime.fromisoformat(r["timestamp"]).replace(tzinfo=TZ) >= start
    ]
    
    # 按账号分组统计
    by_account = defaultdict(lambda: {"total": 0, "success": 0, "missed": 0})
    for r in filtered:
        acc = r.get("account", "unknown")
        by_account[acc]["total"] += 1
        status = r.get("status", "pending")
        if status == "success":
            by_account[acc]["success"] += 1
        elif status == "missed":
            by_account[acc]["missed"] += 1
    
    # 按时发布率
    total = len(filtered)
    success = sum(1 for r in filtered if r.get("status") == "success")
    on_time_rate = round(success / total * 100, 1) if total > 0 else 0
    
    return jsonify({
        "period": period,
        "total": total,
        "success": success,
        "on_time_rate": on_time_rate,
        "by_account": dict(by_account),
        "records": sorted(filtered, key=lambda x: x["timestamp"], reverse=True)[:50],
    })

@app.route("/api/publish/calendar")
def api_publish_calendar():
    """发布日历数据（本月）"""
    now = datetime.now(TZ)
    year = int(request.args.get("year", now.year))
    month = int(request.args.get("month", now.month))
    
    data = load_json(PUBLISH_LOG, {"records": []})
    
    # 定义发布节奏
    accounts = [
        {"name": "AI视频内容创作", "interval": 3, "last": "2026-03-31"},
        {"name": "AI发展个人思考", "interval": 4, "last": "2026-03-28"},
        {"name": "手工制作视频", "interval": 2, "last": "2026-03-31"},
    ]
    
    # 计算本月应该发布的日期
    import calendar
    _, days_in_month = calendar.monthrange(year, month)
    
    planned = []
    for acc in accounts:
        last = datetime.strptime(acc["last"], "%Y-%m-%d").replace(tzinfo=TZ)
        if last.year == year and last.month == month:
            last_day = last.day
        else:
            # 找本月第一个应发布日期
            days_since = (datetime(year, month, 1, tzinfo=TZ) - last).days
            days_until = (acc["interval"] - days_since % acc["interval"]) % acc["interval"]
            last_day = 1 + days_until if days_since > 0 else last.day
        
        # 生成本月所有应发日期（限制20次防止无限循环）
        current = datetime(year, month, last_day, tzinfo=TZ) if last_day <= days_in_month else None
        iteration = 0
        while current and current.year == year and current.month == month and iteration < 20:
            try:
                planned.append({
                    "date": current.strftime("%Y-%m-%d"),
                    "day": current.day,
                    "account": acc["name"],
                    "interval": acc["interval"],
                })
                current += timedelta(days=acc["interval"])
            except (ValueError, OverflowError):
                break
            iteration += 1
    
    # 合并同一天多个账号
    by_day = defaultdict(list)
    for p in planned:
        by_day[p["day"]].append(p)
    
    # 查找已发布记录
    published = {}
    for r in data.get("records", []):
        ts = datetime.fromisoformat(r["timestamp"]).replace(tzinfo=TZ)
        if ts.year == year and ts.month == month:
            key = (ts.day, r.get("account", ""))
            published[key] = r
    
    # 构建日历数据
    calendar_data = []
    for day in range(1, days_in_month + 1):
        day_tasks = by_day.get(day, [])
        for task in day_tasks:
            key = (day, task["account"])
            record = published.get(key, {})
            calendar_data.append({
                "day": day,
                "account": task["account"],
                "interval": task["interval"],
                "status": record.get("status", "pending"),
                "timestamp": record.get("timestamp"),
                "note": record.get("note", ""),
            })
    
    return jsonify({
        "year": year,
        "month": month,
        "days_in_month": days_in_month,
        "tasks": calendar_data,
    })

@app.route("/api/publish/log", methods=["POST"])
def api_publish_log():
    """记录发布结果（由cron任务调用）"""
    req = request.get_json()
    account = req.get("account", "unknown")
    status = req.get("status", "success")  # success / missed / pending
    note = req.get("note", "")
    
    data = load_json(PUBLISH_LOG, {"records": []})
    data["records"].append({
        "timestamp": datetime.now(TZ).isoformat(),
        "account": account,
        "status": status,
        "note": note,
    })
    
    # 只保留最近180天
    cutoff = datetime.now(TZ) - timedelta(days=180)
    data["records"] = [
        r for r in data["records"]
        if datetime.fromisoformat(r["timestamp"]).replace(tzinfo=TZ) > cutoff
    ]
    
    save_json(PUBLISH_LOG, data)
    return jsonify({"success": True, "total": len(data["records"])})

# ============================================================
# API: 深入分析进度
# ============================================================

@app.route("/api/analysis")
def api_analysis():
    """深入分析进度"""
    index_dir = WORKSPACE_DIR / "library" / "index"
    memory_dir = WORKSPACE_DIR / "memory"
    
    books = []
    if index_dir.exists():
        for kf in sorted(index_dir.glob("*_knowledge.json")):
            try:
                doc = json.load(open(kf, encoding='utf-8'))
                book_name = doc.get("book_name", kf.stem.replace("_knowledge", ""))
                chunks = doc.get("chunks", [])
                total_chunks = len(chunks)
                
                # 估算进度（已完成chunk数/预估总数）
                # 粗略用已有chunk数作为进度
                completed = len([c for c in chunks if c.get("summary")])
                
                books.append({
                    "name": book_name,
                    "total_chunks": total_chunks,
                    "completed_chunks": completed,
                    "progress": round(completed / total_chunks * 100, 1) if total_chunks > 0 else 0,
                    "file": kf.name,
                })
            except:
                pass
    
    # 按进度排序
    books.sort(key=lambda x: x["progress"], reverse=True)
    
    # 统计今日新增
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    today_added = 0
    
    return jsonify({
        "books": books,
        "total_books": len(books),
        "today_added": today_added,
    })

# ============================================================
# API: 记忆状态
# ============================================================

@app.route("/api/memory")
def api_memory():
    """记忆状态"""
    memory_file = WORKSPACE_DIR / "MEMORY.md"
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    
    # MEMORY.md 最后修改时间
    memory_stat = {"exists": memory_file.exists()}
    if memory_file.exists():
        mtime = datetime.fromtimestamp(memory_file.stat().st_mtime, tz=TZ)
        memory_stat["last_modified"] = mtime.strftime("%Y-%m-%d %H:%M")
        memory_stat["last_modified_days"] = (datetime.now(TZ) - mtime).days
    
    # 待整理的日志（3天前的）
    memory_dir = WORKSPACE_DIR / "memory"
    memory_files = sorted(memory_dir.glob("????-??-??.md"), reverse=True)
    pending = []
    for mf in memory_files[:10]:
        mtime = datetime.fromtimestamp(mf.stat().st_mtime, tz=TZ)
        days_ago = (datetime.now(TZ) - mtime).days
        if days_ago >= 3:
            pending.append({
                "name": mf.name,
                "days_ago": days_ago,
            })
    
    return jsonify({
        "memory": memory_stat,
        "pending_logs": pending[:5],
    })

# ============================================================
# API: 今日活动
# ============================================================

@app.route("/api/activity")
def api_activity():
    """今日活动时间线"""
    log = load_json(API_LOG_FILE, {"calls": []})
    now = datetime.now(TZ)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    
    # 今日API调用
    today_calls = [
        c for c in log.get("calls", [])
        if datetime.fromisoformat(c["timestamp"]).replace(tzinfo=TZ) >= today_start
    ]
    
    # 按小时聚合
    by_hour = defaultdict(int)
    for c in today_calls:
        hour = datetime.fromisoformat(c["timestamp"]).replace(tzinfo=TZ).hour
        by_hour[f"{hour:02d}:00"] += 1
    
    # 构建时间线
    timeline = []
    for h in range(0, 24):
        key = f"{h:02d}:00"
        count = by_hour.get(key, 0)
        if count > 0:
            timeline.append({
                "hour": key,
                "count": count,
                "label": f"{key} - {count}次调用",
            })
    
    return jsonify({
        "date": today_start.strftime("%Y-%m-%d"),
        "total_calls": len(today_calls),
        "timeline": timeline,
    })

# ============================================================
# API: 项目入口
# ============================================================

# ============================================================
# API: 系统状态
# ============================================================

@app.route("/api/system")
def api_system():
    """系统状态检查"""
    items = []
    
    # 1. Token 窗口
    try:
        log = load_json(API_LOG_FILE, {"calls": []})
        now = datetime.now(TZ)
        window_key = get_window_key()
        window_calls = [c for c in log.get("calls", []) if c.get("window") == window_key]
        remaining = 600 - len(window_calls)
        if remaining > 300:
            status, message = "ok", str(remaining) + " 条剩余"
        elif remaining > 100:
            status, message = "warn", str(remaining) + " 条剩余"
        else:
            status, message = "error", str(remaining) + " 条紧张"
        items.append({"name": "Token 窗口", "status": status, "message": message})
    except Exception as e:
        items.append({"name": "Token 窗口", "status": "error", "message": "检查失败"})

    # 2. Gallery 连接
    try:
        if GALLERY_DB.exists():
            conn = sqlite3.connect(str(GALLERY_DB))
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM photos")
            count = cur.fetchone()[0]
            conn.close()
            items.append({"name": "相册", "status": "ok", "message": f"{count} 张照片"})
        else:
            items.append({"name": "相册", "status": "error", "message": "数据库不存在"})
    except Exception as e:
        items.append({"name": "相册", "status": "warn", "message": "连接失败"})

    # 3. 电子书库
    try:
        index_dir = WORKSPACE_DIR / "library" / "index"
        if index_dir.exists():
            files = list(index_dir.glob("*_knowledge.json"))
            items.append({"name": "电子书库", "status": "ok", "message": f"{len(files)} 本已索引"})
        else:
            items.append({"name": "电子书库", "status": "warn", "message": "索引目录不存在"})
    except Exception as e:
        items.append({"name": "电子书库", "status": "warn", "message": "检查失败"})

    # 4. Cron 服务
    try:
        import subprocess
        r = subprocess.run(["systemctl", "is-active", "cron"], capture_output=True, text=True)
        if r.returncode == 0:
            items.append({"name": "Cron 服务", "status": "ok", "message": "运行中"})
        else:
            items.append({"name": "Cron 服务", "status": "error", "message": "未运行"})
    except Exception as e:
        items.append({"name": "Cron 服务", "status": "warn", "message": "未知"})

    # 5. 发布日志
    try:
        data = load_json(PUBLISH_LOG, {"records": []})
        recent = [r for r in data.get("records", [])[-10:] if r.get("status") == "success"]
        items.append({"name": "发布日志", "status": "ok", "message": f"最近 {len(recent)} 条成功"})
    except Exception as e:
        items.append({"name": "发布日志", "status": "warn", "message": "检查失败"})

    return jsonify({"items": items})


@app.route("/api/sessions")
def api_sessions():
    """活跃会话列表（从 OpenClaw 获取）"""
    try:
        import urllib.request
        req = urllib.request.Request(
            "http://localhost:18789/api/sessions/list",
            headers={"Content-Type": "application/json"},
            method="GET"
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
            all_sessions = data.get("sessions", [])

        now_ts = datetime.now(TZ).timestamp()
        SESSION_WINDOW = 300  # 5分钟内活跃

        sessions = []
        for s in all_sessions:
            # 只看 main / agent / subagent 类型
            kind = s.get("kind", "")
            if kind not in ("main", "agent", "subagent"):
                continue

            last_ts = s.get("lastMessageAt", 0)
            last_active = ""
            if last_ts:
                try:
                    last_dt = datetime.fromtimestamp(last_ts, tz=TZ)
                    last_active = last_dt.strftime("%H:%M")
                except:
                    pass

            # 判断状态
            if kind in ("agent", "subagent"):
                status = "running"
            elif last_ts and (now_ts - last_ts) < SESSION_WINDOW:
                status = "active"
            else:
                status = "idle"

            sessions.append({
                "id": s.get("id", ""),
                "session_key": s.get("id", ""),
                "label": s.get("label", s.get("id", "")),
                "kind": kind,
                "status": status,
                "last_active": last_active,
                "channel": s.get("channel", ""),
            })

        return jsonify({"sessions": sessions})
    except Exception as e:
        return jsonify({"sessions": [], "error": str(e)})


@app.route("/api/gallery-count")
def api_gallery_count():
    """相册照片数量"""
    try:
        if GALLERY_DB.exists():
            conn = sqlite3.connect(str(GALLERY_DB))
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM photos")
            count = cur.fetchone()[0]
            conn.close()
            return jsonify({"count": count})
    except Exception as e:
        return jsonify({"count": 0, "error": str(e)})


# ============================================================
# API: 项目入口
# ============================================================

@app.route("/api/projects")
def api_projects():
    """项目入口列表"""
    projects = [
        {"id": "gallery", "name": "相册", "icon": "📷", "url": "/gallery/"},
        {"id": "library", "name": "电子书库", "icon": "📚", "url": "/library/"},
        {"id": "visit", "name": "个人知识管理", "icon": "🧠", "url": "/visit/"},
        {"id": "knowledge_explore", "name": "知识探索", "icon": "🔍", "url": "/library/hybrid"},
        {"id": "knowledge_network", "name": "知识网络", "icon": "🕸️", "url": "/library/network"},
        {"id": "dashboard", "name": "个人工作台", "icon": "📊", "url": "/dashboard/"},
    ]
    return jsonify({"projects": projects})

# ============================================================
# 启动
# ============================================================

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 18794))
    app.run(host="0.0.0.0", port=port, debug=False)
