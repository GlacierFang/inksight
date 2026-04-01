#!/usr/bin/env python3
"""
个人工作台 - Flask 服务
端口：18794
"""

from flask import Flask, render_template, jsonify, request
from pathlib import Path
import json
import re
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
DASHBOARD_DB = Path("/home/open/.openclaw/workspace/skills/dashboard/data/metrics.db")
TZ = timezone(timedelta(hours=8))

# SQLite schema（参考 oa-cli schema.py）
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS cron_daily (
    date        TEXT NOT NULL,
    job_id      TEXT NOT NULL,
    job_name    TEXT NOT NULL DEFAULT '',
    total       INTEGER NOT NULL DEFAULT 0,
    success     INTEGER NOT NULL DEFAULT 0,
    failed      INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT DEFAULT (datetime('now')),
    UNIQUE(date, job_id)
);
CREATE TABLE IF NOT EXISTS agent_daily (
    date            TEXT NOT NULL,
    agent_id        TEXT NOT NULL,
    agent_name      TEXT NOT NULL DEFAULT '',
    session_count   INTEGER NOT NULL DEFAULT 0,
    last_active_at  TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(date, agent_id)
);
CREATE TABLE IF NOT EXISTS usage_daily (
    date            TEXT NOT NULL,
    source_type     TEXT NOT NULL DEFAULT '',
    source_label    TEXT NOT NULL DEFAULT '',
    model           TEXT NOT NULL DEFAULT '',
    input_tokens    INTEGER NOT NULL DEFAULT 0,
    output_tokens   INTEGER NOT NULL DEFAULT 0,
    total_tokens    INTEGER NOT NULL DEFAULT 0,
    cost            REAL NOT NULL DEFAULT 0,
    requests        INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(date, source_type, source_label, model)
);
CREATE INDEX IF NOT EXISTS idx_cron_date ON cron_daily(date);
CREATE INDEX IF NOT EXISTS idx_agent_date ON agent_daily(date);
CREATE INDEX IF NOT EXISTS idx_usage_date ON usage_daily(date);
"""

def _ensure_db():
    """确保 SQLite 数据库和表存在"""
    DASHBOARD_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DASHBOARD_DB))
    conn.executescript(SCHEMA_SQL)
    conn.close()

_ensure_db()  # 启动时初始化 metrics 数据库

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
        "window_limit": None,  # 已废弃，请使用 /api/token-plan 获取真实配额
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
    """记录发布结果，同时同步到 social-media-schedule.json"""
    req = request.get_json()
    account = req.get("account", "unknown")
    status = req.get("status", "success")  # success / missed / pending
    note = req.get("note", "")
    today_str = datetime.now(TZ).strftime("%Y-%m-%d")
    
    # 1. 写入 publish-log.json
    data = load_json(PUBLISH_LOG, {"records": []})
    data["records"].append({
        "timestamp": datetime.now(TZ).isoformat(),
        "account": account,
        "status": status,
        "note": note,
    })
    cutoff = datetime.now(TZ) - timedelta(days=180)
    data["records"] = [
        r for r in data["records"]
        if datetime.fromisoformat(r["timestamp"]).replace(tzinfo=TZ) > cutoff
    ]
    save_json(PUBLISH_LOG, data)
    
    # 2. 同步到 social-media-schedule.json（标记该账号今日已更新）
    schedule_file = WORKSPACE_DIR / "memory" / "social-media-schedule.json"
    if schedule_file.exists():
        try:
            schedule = json.load(open(schedule_file, 'r', encoding='utf-8'))
            updated = False
            for acc in schedule.get("accounts", []):
                if acc.get("name") == account:
                    acc["lastUpdate"] = today_str
                    # 根据 intervalDays 计算下次更新时间
                    interval = acc.get("intervalDays", 2)
                    next_dt = datetime.now(TZ) + timedelta(days=interval)
                    acc["nextUpdate"] = next_dt.strftime("%Y-%m-%d")
                    updated = True
            if updated:
                schedule["updatedAt"] = today_str
                with open(schedule_file, 'w', encoding='utf-8') as f:
                    json.dump(schedule, f, ensure_ascii=False, indent=2)
        except Exception as e:
            pass
    
    return jsonify({"success": True, "total": len(data["records"])})

# ============================================================
# API: 深入分析进度
# ============================================================

@app.route("/api/analysis")
def api_analysis():
    """深入分析进度 - 只显示当前正在分析的任务"""
    chunk_progress_dir = WORKSPACE_DIR / "memory" / "library" / "chunk-progress"
    chunk_queue_file = WORKSPACE_DIR / "memory" / "library" / "chunk-queue.json"
    index_dir = WORKSPACE_DIR / "library" / "index"

    books = []

    # 1. 从 chunk-progress 读取正在分析的书
    if chunk_progress_dir.exists():
        for pf in chunk_progress_dir.glob("*_progress.json"):
            try:
                with open(pf, 'r', encoding='utf-8') as f:
                    pdata = json.load(f)
                status = pdata.get("status", "")
                if status != "processing":
                    continue  # 只显示 processing 中的

                total = pdata.get("total_chunks", 0)
                completed = pdata.get("completed_chunks", 0)
                books.append({
                    "name": pdata.get("doc_name", pf.stem.replace("_progress", "")),
                    "doc_id": pdata.get("doc_id", ""),
                    "total_chunks": total,
                    "completed_chunks": completed,
                    "progress": round(completed / total * 100, 1) if total > 0 else 0,
                    "last_updated": pdata.get("last_updated", ""),
                    "source": "progress",
                })
            except:
                pass

    # 2. 从 chunk-queue 读取队列中等待处理的书（还未开始）
    if chunk_queue_file.exists():
        try:
            with open(chunk_queue_file, 'r', encoding='utf-8') as f:
                queue = json.load(f)
            for item in queue:
                if item.get("status") == "queued":
                    doc_id = item.get("doc_id", "")
                    # 从 index 目录找书名
                    book_name = doc_id
                    index_file = index_dir / f"{doc_id}_knowledge.json"
                    if index_file.exists():
                        try:
                            with open(index_file, 'r', encoding='utf-8') as f2:
                                doc = json.load(f2)
                            book_name = doc.get("book_name", book_name)
                        except:
                            pass
                    books.append({
                        "name": book_name,
                        "doc_id": doc_id,
                        "total_chunks": item.get("total_chunks", 0),
                        "completed_chunks": 0,
                        "progress": 0,
                        "last_updated": item.get("added_at", ""),
                        "source": "queued",
                    })
        except:
            pass

    return jsonify({
        "books": books,
        "total_books": len(books),
    })

# ============================================================
# API: 记忆状态
# ============================================================

@app.route("/api/memory")
def api_memory():
    """记忆状态 - 分项目文档和每日记录两个区块"""
    daily_files = []
    project_files = []

    # memory/*.md（每日记录，如 2026-04-01.md）
    memory_base = WORKSPACE_DIR / "memory"
    if memory_base.exists():
        for f in sorted(memory_base.glob("????-??-??.md"), reverse=True):
            try:
                mt = f.stat().st_mtime
                dt = datetime.fromtimestamp(mt, tz=TZ)
                daily_files.append({
                    "name": f.name,
                    "path": str(f.relative_to(WORKSPACE_DIR)),
                    "updated": dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "days_ago": (datetime.now(TZ) - dt).days,
                })
            except:
                pass

    # memory/projects/*.md（项目文档）
    projects_dir = WORKSPACE_DIR / "memory" / "projects"
    if projects_dir.exists():
        for f in sorted(projects_dir.glob("*.md")):
            try:
                mt = f.stat().st_mtime
                dt = datetime.fromtimestamp(mt, tz=TZ)
                project_files.append({
                    "name": f.name,
                    "path": str(f.relative_to(WORKSPACE_DIR)),
                    "updated": dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "days_ago": (datetime.now(TZ) - dt).days,
                })
            except:
                pass

    # 分别按更新时间倒序
    daily_files.sort(key=lambda x: x["updated"], reverse=True)
    project_files.sort(key=lambda x: x["updated"], reverse=True)

    return jsonify({"daily": daily_files, "projects": project_files})


@app.route("/api/memory/<path:filename>")
def api_memory_file(filename):
    """读取指定记忆文件的纯文本内容"""
    safe_path = (WORKSPACE_DIR / filename).resolve()
    if not str(safe_path).startswith(str(WORKSPACE_DIR.resolve())):
        return "Invalid path", 400
    if not safe_path.exists() or safe_path.suffix != ".md":
        return "Not found", 404
    try:
        with open(safe_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return content
    except Exception as e:
        return str(e), 500

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


@app.route("/api/connector-status")
def api_connector_status():
    """
    Connector Status — 检查各数据源连接状态
    参考 openclaw-control-center usage-cost.ts 的 Connector Status 模块
    返回每个数据源的 connected / partial / not_connected 状态 + hint
    """
    try:
        OPENCLAW_HOME = Path.home() / ".openclaw"
        now = datetime.now(TZ)
        connectors = []

        # 1. Cron Jobs（检查 jobs.json 是否存在且有 jobs）
        try:
            jobs_file = OPENCLAW_HOME / "cron" / "jobs.json"
            runs_dir = OPENCLAW_HOME / "cron" / "runs"
            if jobs_file.exists():
                with open(jobs_file, 'r', encoding='utf-8') as f:
                    jobs_data = json.load(f)
                job_list = jobs_data.get("jobs", [])
                if len(job_list) > 0:
                    connectors.append({
                        "id": "cron",
                        "name": "Cron 任务",
                        "icon": "⏰",
                        "status": "connected",
                        "message": f"{len(job_list)} 个任务已配置",
                        "hint": "最近一次执行记录来自 runs/*.jsonl",
                    })
                else:
                    connectors.append({
                        "id": "cron",
                        "name": "Cron 任务",
                        "icon": "⏰",
                        "status": "partial",
                        "message": "jobs.json 为空",
                        "hint": "检查 ~/.openclaw/cron/jobs.json",
                    })
            else:
                connectors.append({
                    "id": "cron",
                    "name": "Cron 任务",
                    "icon": "⏰",
                    "status": "not_connected",
                    "message": "jobs.json 不存在",
                    "hint": "检查 ~/.openclaw/cron/jobs.json 是否存在",
                })
        except Exception as e:
            connectors.append({
                "id": "cron", "name": "Cron 任务", "icon": "⏰",
                "status": "not_connected", "message": f"读取失败: {e}",
                "hint": "检查 ~/.openclaw/cron/jobs.json 权限",
            })

        # 2. Sessions（检查 agents/*/sessions/sessions.json）
        try:
            agents_dir = OPENCLAW_HOME / "agents"
            agent_count = 0
            session_count = 0
            if agents_dir.exists():
                for agent_dir in agents_dir.iterdir():
                    if not agent_dir.is_dir():
                        continue
                    sessions_json = agent_dir / "sessions" / "sessions.json"
                    if sessions_json.exists():
                        agent_count += 1
                        try:
                            with open(sessions_json, 'r', encoding='utf-8') as f:
                                sd = json.load(f)
                            session_count += len(sd)
                        except:
                            pass
            if agent_count > 0:
                connectors.append({
                    "id": "sessions",
                    "name": "会话",
                    "icon": "💬",
                    "status": "connected",
                    "message": f"{agent_count} 个 Agent，{session_count} 个会话",
                    "hint": "连接正常",
                })
            else:
                connectors.append({
                    "id": "sessions",
                    "name": "会话",
                    "icon": "💬",
                    "status": "not_connected",
                    "message": "未发现任何 Agent 会话",
                    "hint": "检查 ~/.openclaw/agents/ 目录",
                })
        except Exception as e:
            connectors.append({
                "id": "sessions", "name": "会话", "icon": "💬",
                "status": "not_connected", "message": f"读取失败: {e}",
                "hint": "检查 ~/.openclaw/agents/ 权限",
            })

        # 3. API 用量日志（检查 minimax-api-log.json）
        try:
            api_log = WORKSPACE_DIR / "memory" / "minimax-api-log.json"
            if api_log.exists():
                with open(api_log, 'r', encoding='utf-8') as f:
                    log_data = json.load(f)
                call_count = len(log_data.get("calls", []))
                if call_count > 0:
                    # 检查最后一条记录时间
                    last_call = log_data.get("calls", [])[-1] if log_data.get("calls") else None
                    last_time = ""
                    if last_call:
                        try:
                            last_dt = datetime.fromisoformat(last_call.get("timestamp", "").replace("Z", "+00:00")).replace(tzinfo=TZ)
                            delta = now - last_dt
                            if delta.total_seconds() < 3600:
                                last_time = f"，最近 {int(delta.total_seconds()/60)} 分钟前"
                            elif delta.total_seconds() < 86400:
                                last_time = f"，最近 {int(delta.total_seconds()/3600)} 小时前"
                        except:
                            pass
                    connectors.append({
                        "id": "api_log",
                        "name": "API 用量日志",
                        "icon": "📊",
                        "status": "connected",
                        "message": f"{call_count} 条记录{last_time}",
                        "hint": "日志来源：minimax-api-log.json",
                    })
                else:
                    connectors.append({
                        "id": "api_log",
                        "name": "API 用量日志",
                        "icon": "📊",
                        "status": "partial",
                        "message": "文件存在但无调用记录",
                        "hint": "检查 API Logger 是否正常写入",
                    })
            else:
                connectors.append({
                    "id": "api_log",
                    "name": "API 用量日志",
                    "icon": "📊",
                    "status": "not_connected",
                    "message": "minimax-api-log.json 不存在",
                    "hint": "仅在调用 chunk_generation/explore 时写入，暂无调用记录",
                })
        except Exception as e:
            connectors.append({
                "id": "api_log", "name": "API 用量日志", "icon": "📊",
                "status": "not_connected", "message": f"读取失败: {e}",
                "hint": "检查 memory/minimax-api-log.json 权限",
            })

        # 4. Subscription（检查多个可能路径）
        sub_paths = [
            OPENCLAW_HOME / "subscription.json",
            OPENCLAW_HOME / "billing" / "subscription.json",
            OPENCLAW_HOME / "usage" / "subscription.json",
        ]
        sub_found = None
        for p in sub_paths:
            if p.exists():
                try:
                    with open(p, 'r', encoding='utf-8') as f:
                        sub_data = json.load(f)
                    if sub_data:
                        sub_found = p
                        break
                except:
                    pass
        if sub_found:
            connectors.append({
                "id": "subscription",
                "name": "订阅额度",
                "icon": "💳",
                "status": "connected",
                "message": f"已连接（来源：~{sub_found.relative_to(Path.home())}）",
                "hint": "数据来源：" + str(sub_found),
            })
        else:
            pass

        # 5. 趋势历史（检查 SQLite metrics.db）
        try:
            db = DASHBOARD_DB
            if db.exists():
                conn = sqlite3.connect(str(db))
                cur = conn.cursor()
                # 检查各表是否有数据
                tables = ["cron_daily", "agent_daily", "usage_daily"]
                table_stats = {}
                for t in tables:
                    try:
                        cur.execute(f"SELECT COUNT(*) FROM {t}")
                        cnt = cur.fetchone()[0]
                        table_stats[t] = cnt
                    except:
                        table_stats[t] = 0
                conn.close()
                total_records = sum(table_stats.values())
                if total_records > 0:
                    connectors.append({
                        "id": "history",
                        "name": "历史趋势",
                        "icon": "📈",
                        "status": "connected",
                        "message": f"已积累 {total_records} 条历史记录",
                        "hint": f"Cron={table_stats.get('cron_daily',0)} / Agent={table_stats.get('agent_daily',0)} / Usage={table_stats.get('usage_daily',0)}",
                    })
                else:
                    connectors.append({
                        "id": "history",
                        "name": "历史趋势",
                        "icon": "📈",
                        "status": "partial",
                        "message": "数据库存在但暂无历史数据",
                        "hint": "等待 cron 或 session 数据写入",
                    })
            else:
                connectors.append({
                    "id": "history",
                    "name": "历史趋势",
                    "icon": "📈",
                    "status": "not_connected",
                    "message": "metrics.db 不存在",
                    "hint": "Dashboard 首次启动后自动创建",
                })
        except Exception as e:
            connectors.append({
                "id": "history", "name": "历史趋势", "icon": "📈",
                "status": "partial", "message": f"检查失败: {e}",
                "hint": "检查 skills/dashboard/data/metrics.db",
            })

        return jsonify({"connectors": connectors})
    except Exception as e:
        return jsonify({"connectors": [], "error": str(e)}), 500



# ============================================================
# API: Burn Rate 预算预测
# ============================================================

# ============================================================
# API: MiniMax Token Plan 配额（真实数据）
# ============================================================

def _get_minimax_api_key():
    """从 auth-profiles.json 读取 MiniMax API Key"""
    try:
        auth_file = Path.home() / ".openclaw" / "agents" / "main" / "agent" / "auth-profiles.json"
        if not auth_file.exists():
            return None
        with open(auth_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        profiles = data.get("profiles", {})
        for profile in profiles.values():
            if profile.get("provider") == "minimax-portal":
                access = profile.get("access", "")
                if access.startswith("sk-"):
                    return access
    except:
        pass
    return None


# ============================================================
# API: Healthz 系统健康状态（参考 openclaw-control-center healthz.ts）
# ============================================================

def _get_openclaw_version():
    """获取 OpenClaw 版本"""
    try:
        import subprocess
        result = subprocess.run(['openclaw', '--version'], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            return result.stdout.strip()
    except:
        pass
    return "unknown"

def _check_gateway_health():
    """检查 Gateway 连接状态"""
    try:
        import urllib.request
        req = urllib.request.Request(
            "http://localhost:18789/healthz",
            method="GET"
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
            return {"connected": True, "data": data}
    except Exception as e:
        return {"connected": False, "error": str(e)}

def _check_snapshot_freshness():
    """
    检查数据快照新鲜度
    ok: < 12 * polling_interval
    warn: < 48 * polling_interval
    stale: >= 48 * polling_interval
    polling_interval 约 30秒
    """
    POLLING_INTERVAL_MS = 30_000  # 30秒
    OK_THRESHOLD_MS = POLLING_INTERVAL_MS * 12   # 6分钟
    WARN_THRESHOLD_MS = POLLING_INTERVAL_MS * 48  # 24分钟

    # 尝试从 gateway runtime 文件读取最新采集时间
    snapshot_time = None
    try:
        runtime_file = Path.home() / ".openclaw" / "runtime" / "snapshot.json"
        if runtime_file.exists():
            with open(runtime_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            snapshot_time = data.get("generatedAt") or data.get("timestamp")
    except:
        pass

    # 如果没有 snapshot 文件，检查 metrics.db 最后写入时间
    if not snapshot_time:
        try:
            db = DASHBOARD_DB
            if db.exists():
                conn = sqlite3.connect(str(db))
                cur = conn.cursor()
                cur.execute("SELECT MAX(created_at) FROM cron_daily")
                row = cur.fetchone()
                if row and row[0]:
                    snapshot_time = row[0]
                conn.close()
        except:
            pass

    # 计算 age
    if snapshot_time:
        try:
            if isinstance(snapshot_time, str):
                dt = datetime.fromisoformat(snapshot_time.replace("Z", "+00:00")).replace(tzinfo=TZ)
            else:
                dt = datetime.fromtimestamp(snapshot_time / 1000.0, tz=TZ) if snapshot_time > 1e12 else datetime.fromtimestamp(snapshot_time, tz=TZ)
            age_ms = (datetime.now(TZ) - dt).total_seconds() * 1000
            if age_ms > WARN_THRESHOLD_MS:
                status = "stale"
            elif age_ms > OK_THRESHOLD_MS:
                status = "warn"
            else:
                status = "ok"
            return {
                "status": status,
                "generated_at": snapshot_time,
                "age_seconds": round(age_ms / 1000, 1),
                "ok_threshold_seconds": OK_THRESHOLD_MS / 1000,
                "warn_threshold_seconds": WARN_THRESHOLD_MS / 1000,
            }
        except:
            pass

    return {
        "status": "unknown",
        "generated_at": None,
        "age_seconds": None,
        "ok_threshold_seconds": OK_THRESHOLD_MS / 1000,
        "warn_threshold_seconds": WARN_THRESHOLD_MS / 1000,
    }




# ============================================================
# API: 磁盘空间细分
# ============================================================

def _du_size(path: Path) -> int:
    """计算目录或文件大小（字节）"""
    try:
        if path.is_file():
            return path.stat().st_size
        total = 0
        for dirpath, _, filenames in os.walk(path):
            for f in filenames:
                try:
                    total += os.path.getsize(os.path.join(dirpath, f))
                except:
                    pass
        return total
    except:
        return 0

@app.route("/api/disk-space")
def api_disk_space():
    """
    磁盘空间细分
    - 总览（系统级）
    - Gallery（photos + inbound）
    - Library（ebooks）
    - Dashboard 数据
    """
    try:
        now = datetime.now(TZ)

        # 系统总览
        import shutil
        total, used, free = shutil.disk_usage("/")

        # 各模块路径
        media_path = Path.home() / ".openclaw" / "media"
        gallery_dir = Path("/home/open/.openclaw/workspace/skills/gallery")
        library_dir = Path("/home/open/.openclaw/workspace/library")
        dashboard_dir = Path("/home/open/.openclaw/workspace/skills/dashboard/data")
        workspace_dir = Path("/home/open/.openclaw/workspace")
        inbound_dir = gallery_dir / "data" / "inbound"

        # Gallery: photos + inbound
        gallery_photos = _du_size(media_path) if media_path.exists() else 0
        gallery_inbound = _du_size(inbound_dir) if inbound_dir.exists() else 0
        gallery_total = gallery_photos + gallery_inbound

        # Library
        library_size = _du_size(library_dir)

        # Dashboard
        dashboard_size = _du_size(dashboard_dir)

        # Workspace 总计
        workspace_size = _du_size(workspace_dir)

        # 计算各模块占比
        used_by_workspace = workspace_size
        other_used = used - used_by_workspace

        def fmt(bytes_val):
            if bytes_val >= 1024**3:
                return f"{bytes_val/1024**3:.1f} GB"
            elif bytes_val >= 1024**2:
                return f"{bytes_val/1024**2:.1f} MB"
            elif bytes_val >= 1024:
                return f"{bytes_val/1024:.1f} KB"
            return f"{bytes_val} B"

        return jsonify({
            "generated_at": now.isoformat(),
            "system": {
                "total_gb": round(total / 1024**3, 1),
                "used_gb": round(used / 1024**3, 1),
                "free_gb": round(free / 1024**3, 1),
                "used_pct": round(used / total * 100, 1),
            },
            "modules": {
                "gallery": {
                    "label": "Gallery 相册",
                    "photos_bytes": gallery_photos,
                    "photos_fmt": fmt(gallery_photos),
                    "inbound_bytes": gallery_inbound,
                    "inbound_fmt": fmt(gallery_inbound),
                    "total_bytes": gallery_total,
                    "total_fmt": fmt(gallery_total),
                },
                "library": {
                    "label": "Library 电子书库",
                    "bytes": library_size,
                    "fmt": fmt(library_size),
                },
                "dashboard": {
                    "label": "Dashboard 监控",
                    "bytes": dashboard_size,
                    "fmt": fmt(dashboard_size),
                },
            },
            "workspace_gb": round(workspace_size / 1024**3, 2),
            "other_gb": round(max(other_used, 0) / 1024**3, 2),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500



@app.route("/api/healthz")
def api_healthz():
    """
    Healthz 系统健康状态
    参考 openclaw-control-center healthz.ts
    - build info（版本/节点/只读模式）
    - snapshot freshness（数据新鲜度）
    - gateway connectivity（网关连接）
    """
    try:
        now = datetime.now(TZ)

        # Build info
        version = _get_openclaw_version()

        # Gateway health
        gw = _check_gateway_health()

        # Snapshot freshness
        snapshot = _check_snapshot_freshness()

        # 整体状态
        statuses = [snapshot.get("status", "unknown"), "ok" if gw.get("connected") else "error"]
        if "stale" in statuses or "error" in statuses:
            overall = "error"
        elif "warn" in statuses or "unknown" in statuses:
            overall = "warn"
        else:
            overall = "ok"

        # Gateway 延迟
        gw_latency = None
        if gw.get("connected") and gw.get("data"):
            gw_latency = gw["data"].get("latency_ms")

        # 各服务状态
        services = []

        # Gateway
        if gw.get("connected"):
            services.append({"name": "Gateway (18789)", "status": "ok", "message": "正常响应"})
        else:
            services.append({"name": "Gateway (18789)", "status": "error", "message": gw.get("error", "无法连接")})

        # Cron jobs
        try:
            jobs_file = Path.home() / ".openclaw" / "cron" / "jobs.json"
            if jobs_file.exists():
                with open(jobs_file, 'r') as f:
                    jobs = json.load(f).get("jobs", [])
                enabled = sum(1 for j in jobs if j.get("enabled", True))
                services.append({"name": "Cron 调度器", "status": "ok", "message": f"{enabled} 个任务运行中"})
            else:
                services.append({"name": "Cron 调度器", "status": "warn", "message": "jobs.json 不存在"})
        except Exception as e:
            services.append({"name": "Cron 调度器", "status": "error", "message": str(e)})

        # Dashboard itself
        services.append({"name": "Dashboard (18794)", "status": "ok", "message": "本服务运行中"})

        # Gallery
        try:
            import urllib.request
            req = urllib.request.Request("http://localhost:18790/gallery/api/ping", method="GET")
            with urllib.request.urlopen(req, timeout=3) as r:
                services.append({"name": "Gallery (18790)", "status": "ok", "message": "正常"})
        except:
            services.append({"name": "Gallery (18790)", "status": "warn", "message": "无法连接"})

        # Library
        try:
            import urllib.request
            req = urllib.request.Request("http://localhost:18792/library/api/stats", method="GET")
            with urllib.request.urlopen(req, timeout=3) as r:
                services.append({"name": "Library (18792)", "status": "ok", "message": "正常"})
        except:
            services.append({"name": "Library (18792)", "status": "warn", "message": "无法连接"})

        return jsonify({
            "generated_at": now.isoformat(),
            "status": overall,
            "version": version,
            "snapshot": snapshot,
            "gateway": {
                "connected": gw.get("connected", False),
                "latency_ms": gw_latency,
            },
            "services": services,
        })
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500





@app.route("/api/token-plan")
def api_token_plan():
    """
    读取 MiniMax Token Plan 真实配额数据
    数据来源：https://www.minimaxi.com/v1/api/openplatform/coding_plan/remains
    """
    try:
        api_key = _get_minimax_api_key()
        if not api_key:
            return jsonify({"connected": False, "error": "未找到 API Key", "models": []}), 200

        import urllib.request
        req = urllib.request.Request(
            "https://www.minimaxi.com/v1/api/openplatform/coding_plan/remains",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            method="GET"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())

        base_resp = data.get("base_resp", {})
        if base_resp.get("status_code") != 0:
            return jsonify({"connected": False, "error": base_resp.get("status_msg", "未知错误"), "models": []}), 200

        TZ_LOCAL = timezone(timedelta(hours=8))
        now = datetime.now(TZ_LOCAL)

        models = []
        for m in (data.get("model_remains") or []):
            model_name = m.get("model_name", "")
            interval_total = m.get("current_interval_total_count", 0)
            interval_used = m.get("current_interval_usage_count", 0)
            weekly_total = m.get("current_weekly_total_count", 0)
            weekly_used = m.get("current_weekly_usage_count", 0)
            interval_end_ms = m.get("end_time", 0)
            weekly_end_ms = m.get("weekly_end_time", 0)

            interval_remain = max(interval_total - interval_used, 0)
            weekly_remain = max(weekly_total - weekly_used, 0)

            # 5小时窗口剩余时间
            interval_reset_in = ""
            if interval_end_ms > 0:
                reset_dt = datetime.fromtimestamp(interval_end_ms / 1000, tz=TZ_LOCAL)
                delta = reset_dt - now
                total_seconds = int(delta.total_seconds())
                if total_seconds > 0:
                    if total_seconds < 3600:
                        interval_reset_in = f"{total_seconds // 60}分钟后"
                    elif total_seconds < 86400:
                        interval_reset_in = f"{total_seconds // 3600}小时{total_seconds % 3600 // 60}分钟后"
                    else:
                        interval_reset_in = f"{total_seconds // 86400}天{total_seconds % 86400 // 3600}小时后"

            # 本周重置时间
            weekly_reset_in = ""
            if weekly_end_ms > 0:
                reset_dt = datetime.fromtimestamp(weekly_end_ms / 1000, tz=TZ_LOCAL)
                delta = reset_dt - now
                total_seconds = int(delta.total_seconds())
                if total_seconds > 0:
                    days = total_seconds // 86400
                    interval_reset_in = f"{days}天{total_seconds % 86400 // 3600}小时后"

            # 状态判断
            if interval_total > 0:
                interval_pct = interval_used / interval_total * 100
                if interval_pct >= 90:
                    interval_status = "critical"
                elif interval_pct >= 70:
                    interval_status = "warn"
                else:
                    interval_status = "ok"
            else:
                interval_status = "ok"

            # 名称美化
            name_map = {
                "MiniMax-M*": "MiniMax-M2.7",
                "speech-hd": "语音 HD",
                "MiniMax-Hailuo-2.3-Fast-6s-768p": "视频 Fast",
                "MiniMax-Hailuo-2.3-6s-768p": "视频标准",
                "music-2.5": "音乐",
                "image-01": "图片",
            }
            display_name = name_map.get(model_name, model_name)

            models.append({
                "name": display_name,
                "model_key": model_name,
                "interval_total": interval_total,
                "interval_used": interval_used,
                "interval_remain": interval_remain,
                "interval_pct": round(interval_used / interval_total * 100, 1) if interval_total > 0 else 0,
                "interval_status": interval_status,
                "interval_reset_in": interval_reset_in,
                "weekly_total": weekly_total,
                "weekly_used": weekly_used,
                "weekly_remain": weekly_remain,
                "weekly_pct": round(weekly_used / weekly_total * 100, 1) if weekly_total > 0 else 0,
                "weekly_reset_in": weekly_reset_in,
            })

        # 提取 M2.7 单独返回
        m2_info = next((m for m in models if "MiniMax-M" in (m.get("model_key") or "")), None)

        return jsonify({
            "connected": True,
            "models": models,
            "m2": m2_info,
            "fetched_at": now.isoformat(),
        })
    except Exception as e:
        return jsonify({"connected": False, "error": str(e), "models": []}), 200





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

    # 1. Gallery 连接
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


@app.route("/api/cron-runs")
def api_cron_runs():
    """当日定时任务推送情况"""
    try:
        jobs_file = Path("/home/open/.openclaw/cron/jobs.json")
        runs_dir = Path("/home/open/.openclaw/cron/runs")
        now = datetime.now(TZ)
        now_ms = int(now.timestamp() * 1000)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_start_ms = int(today_start.timestamp() * 1000)

        if not jobs_file.exists():
            return jsonify({"tasks": [], "error": None})

        with open(jobs_file, 'r', encoding='utf-8') as f:
            cron_data = json.load(f)

        tasks = []
        for job in cron_data.get("jobs", []):
            job_id = job.get("id", "")
            delivery = job.get("delivery", {})
            channel = delivery.get("channel", "unknown")  # feishu / weixin / none
            # 跳过没有交付渠道的后台任务
            if channel in ("none", "unknown") or not delivery.get("to"):
                continue

            # 取该 job 的最新一条执行记录（JSONL 最后一行）
            run_file = runs_dir / f"{job_id}.jsonl"
            last_run = None
            if run_file.exists():
                try:
                    with open(run_file, 'r', encoding='utf-8') as f:
                        lines = f.readlines()
                    if lines:
                        last_run = json.loads(lines[-1].strip())
                except:
                    pass

            # 判断是否是当日
            is_today = False
            if last_run:
                run_ms = last_run.get("runAtMs", 0)
                if run_ms >= today_start_ms:
                    is_today = True

            status = "未执行"
            status_type = "idle"
            if last_run:
                if last_run.get("status") == "error":
                    status = "失败"
                    status_type = "error"
                elif last_run.get("delivered"):
                    status = "成功"
                    status_type = "ok"
                elif last_run.get("deliveryStatus") == "not-delivered":
                    status = "已执行"
                    status_type = "warn"

            # 渠道标签
            channel_icon = "📱" if channel == "weixin" else "✈️"

            tasks.append({
                "name": job.get("name", "未知任务"),
                "schedule": job.get("schedule", {}).get("expr", ""),
                "channel": channel,
                "channel_icon": channel_icon,
                "is_today": is_today,
                "status": status,
                "status_type": status_type,
                "last_run_time": last_run.get("runAtMs", 0) if last_run else 0,
                "last_summary": (last_run.get("summary", "") or "")[:60] if last_run else "",
            })

        return jsonify({"tasks": tasks})
    except Exception as e:
        return jsonify({"tasks": [], "error": str(e)})


@app.route("/api/heartbeat-status")
def api_heartbeat_status():
    """Heartbeat 状态"""
    try:
        log_file = WORKSPACE_DIR / "memory" / "heartbeat-log.json"
        today_str = datetime.now(TZ).strftime("%Y-%m-%d")

        heartbeats = []
        if log_file.exists():
            try:
                with open(log_file, 'r', encoding='utf-8') as f:
                    logs = json.load(f)
                # 只取今天的记录
                for entry in logs:
                    t = entry.get("time", "")
                    if t.startswith(today_str):
                        heartbeats.append(t.split(" ")[1] if " " in t else t)
            except:
                pass

        return jsonify({
            "heartbeats": heartbeats[-20:],  # 最新20条
            "count": len(heartbeats),
        })
    except Exception as e:
        return jsonify({"heartbeats": [], "count": 0, "error": str(e)}), 500


@app.route("/api/current-status")
def api_current_status():
    """当前状态指标"""
    try:
        sessions_file = Path("/home/open/.openclaw/agents/main/sessions/sessions.json")
        cron_jobs_file = Path("/home/open/.openclaw/cron/jobs.json")

        window_key = get_window_key()
        now = datetime.now(TZ)
        window_start_hour = (now.hour // 5) * 5
        window_start = now.replace(hour=window_start_hour, minute=0, second=0, microsecond=0)
        window_start_ts = window_start.timestamp()
        now_ts = now.timestamp()
        SESSION_ACTIVE_WINDOW = 300  # 5分钟内活跃

        # 读取 cron 任务配置数
        cron_job_count = 0
        if cron_jobs_file.exists():
            try:
                with open(cron_jobs_file, 'r', encoding='utf-8') as f:
                    cron_data = json.load(f)
                cron_job_count = len(cron_data.get("jobs", []))
            except:
                pass

        # 从 sessions.json 统计
        total_sessions = 0
        active_in_window = 0       # 本时段内有更新的 session 数（含 cron）
        active_dialogs = 0         # 本时段内有更新的非 cron session 数
        window_model_calls = 0
        window_total_tokens = 0

        if sessions_file.exists():
            try:
                with open(sessions_file, 'r', encoding='utf-8') as f:
                    sessions_data = json.load(f)
                total_sessions = len(sessions_data)

                for s in sessions_data.values():
                    updated_at = s.get("updatedAt", 0)
                    updated_ts = updated_at / 1000.0 if updated_at > 1e12 else updated_at

                    # 本时段内有更新的 session
                    if updated_ts >= window_start_ts:
                        window_model_calls += 1
                        active_in_window += 1

                        label = s.get("label", "")
                        if not label.startswith("Cron:"):
                            active_dialogs += 1

                        total_tokens = s.get("totalTokens", 0)
                        if total_tokens and total_tokens > 0:
                            window_total_tokens += total_tokens

            except Exception as e:
                pass

        # 剩余配额改由 /api/token-plan 提供，此处仅统计当前会话
        return jsonify({
            "window_key": window_key,
            "used": window_model_calls,
            "remaining": None,  # 请使用 /api/token-plan 获取真实 Token Plan 配额
            "limit": None,
            "total_sessions": total_sessions,
            "active_in_window": active_in_window,
            "active_dialogs": active_dialogs,
            "cron_job_count": cron_job_count,
            "window_total_tokens": window_total_tokens,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/sessions")
def api_sessions():
    """活跃会话列表（从 sessions.json 读取）"""
    try:
        sessions_file = Path("/home/open/.openclaw/agents/main/sessions/sessions.json")
        now_ts = datetime.now(TZ).timestamp()
        SESSION_WINDOW = 300  # 5分钟内活跃

        sessions = []
        if sessions_file.exists():
            with open(sessions_file, 'r', encoding='utf-8') as f:
                sessions_data = json.load(f)

            for key, s in sessions_data.items():
                # key 格式: agent:{type}:{subtype}，从 key 解析 kind
                parts = key.split(":")
                if len(parts) >= 2:
                    kind = parts[1]  # main / agent
                else:
                    kind = s.get("kind", "unknown")

                if kind not in ("main", "agent"):
                    continue

                # 子类型：cron / subagent
                if len(parts) >= 3 and parts[2] == "cron":
                    sub_kind = "cron"
                elif len(parts) >= 3 and parts[2] == "subagent":
                    sub_kind = "subagent"
                else:
                    sub_kind = kind

                updated_at = s.get("updatedAt", 0)
                updated_ts = updated_at / 1000.0 if updated_at > 1e12 else updated_at
                last_active = ""
                if updated_ts:
                    try:
                        last_dt = datetime.fromtimestamp(updated_ts, tz=TZ)
                        last_active = last_dt.strftime("%H:%M")
                    except:
                        pass

                # 判断状态：subagent / cron → running；main 且 5 分钟内 → active；否则 idle
                if sub_kind in ("subagent", "cron"):
                    status = "running"
                elif updated_ts and (now_ts - updated_ts) < SESSION_WINDOW:
                    status = "active"
                else:
                    status = "idle"

                sessions.append({
                    "id": s.get("sessionId", key),
                    "session_key": key,
                    "label": s.get("label", key),
                    "kind": sub_kind,
                    "status": status,
                    "last_active": last_active,
                    "channel": s.get("origin", {}).get("provider", ""),
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
# 通用辅助函数（参考源码）
# ============================================================

MODEL_CONTEXT_WINDOWS = {
    "MiniMax-M2.7": 204800,
    "MiniMax-M2": 102400,
    "default": 128000,
}

def _get_context_window(model):
    if not model:
        return MODEL_CONTEXT_WINDOWS["default"]
    return MODEL_CONTEXT_WINDOWS.get(model.strip(), MODEL_CONTEXT_WINDOWS["default"])

def _resolve_context_threshold(usage_pct):
    if usage_pct is None:
        return ("not_connected", "数据不可用")
    if usage_pct >= 90:
        return ("critical", "快满了要烧钱")
    if usage_pct >= 70:
        return ("warn", "使用中")
    return ("ok", "正常")

def _resolve_cron_health(next_run_ms, enabled, now_ms):
    if not enabled:
        return ("disabled", "已禁用", None)
    if not next_run_ms or next_run_ms <= 0:
        return ("unknown", "未知", None)
    lag_ms = now_ms - next_run_ms
    due_in_seconds = round(lag_ms / 1000)
    if lag_ms <= 0:
        return ("scheduled", "待执行", due_in_seconds)
    elif lag_ms <= 5 * 60 * 1000:
        return ("due", "即将执行", due_in_seconds)
    elif lag_ms <= 30 * 60 * 1000:
        return ("late", "延迟中", due_in_seconds)
    else:
        return ("late", "严重延迟", due_in_seconds)

# ============================================================
# API: Session Context Pressure
# ============================================================

@app.route("/api/session-context")
def api_session_context():
    try:
        OPENCLAW_HOME = Path.home() / ".openclaw"
        agents_dir = OPENCLAW_HOME / "agents"
        now = datetime.now(TZ)
        context_windows = []

        if agents_dir.exists():
            for agent_dir in agents_dir.iterdir():
                if not agent_dir.is_dir():
                    continue
                sessions_json = agent_dir / "sessions" / "sessions.json"
                if not sessions_json.exists():
                    continue
                try:
                    with open(sessions_json, 'r', encoding='utf-8') as f:
                        sessions_data = json.load(f)
                except:
                    continue

                for session_key, sinfo in sessions_data.items():
                    context_limit = _get_context_window(sinfo.get("model", ""))
                    total_tokens = sinfo.get("totalTokens", 0) or 0
                    usage_pct = None
                    if context_limit > 0:
                        usage_pct = round(total_tokens / context_limit * 100, 1)
                    threshold_state, threshold_label = _resolve_context_threshold(usage_pct)

                    updated_at = sinfo.get("updatedAt", 0)
                    last_active_str = ""
                    if updated_at:
                        try:
                            last_dt = datetime.fromtimestamp(updated_at / 1000.0, tz=TZ)
                            delta = now - last_dt
                            seconds = delta.total_seconds()
                            if seconds < 60:
                                last_active_str = "刚刚"
                            elif seconds < 3600:
                                last_active_str = f"{int(seconds/60)}分钟前"
                            elif seconds < 86400:
                                last_active_str = f"{int(seconds/3600)}小时前"
                            else:
                                last_active_str = f"{int(seconds/86400)}天前"
                        except:
                            pass

                    # 解析 session 类型（参考 usage-cost.ts classifySessionType）
                    key = session_key or ""
                    if ":cron:" in key:
                        sess_type = "cron"
                    elif ":feishu:" in key or ":lark:" in key:
                        sess_type = "feishu"
                    elif ":wechat:" in key or ":wx:" in key:
                        sess_type = "wechat"
                    elif ":discord:" in key:
                        sess_type = "discord"
                    elif ":telegram:" in key or ":tg:" in key:
                        sess_type = "telegram"
                    else:
                        sess_type = "main"

                    context_windows.append({
                        "session_key": session_key,
                        "agent_id": agent_dir.name,
                        "model": sinfo.get("model", "unknown") or "unknown",
                        "context_limit": context_limit,
                        "total_tokens": total_tokens,
                        "usage_pct": usage_pct,
                        "threshold_state": threshold_state,
                        "threshold_label": threshold_label,
                        "last_active_str": last_active_str,
                        "updated_at": datetime.fromtimestamp(updated_at / 1000.0, tz=TZ).isoformat() if updated_at else None,
                        "session_type": sess_type,
                    })

        context_windows.sort(key=lambda x: x.get("usage_pct") if x.get("usage_pct") is not None else -1, reverse=True)

        state_counts = {"critical": 0, "warn": 0, "ok": 0, "not_connected": 0}
        for cw in context_windows:
            st = cw["threshold_state"]
            if st in state_counts:
                state_counts[st] += 1

        # 按类型聚合
        type_counts = {}
        for cw in context_windows:
            t = cw.get("session_type", "main")
            if t not in type_counts:
                type_counts[t] = {"total": 0, "critical": 0, "warn": 0, "ok": 0}
            type_counts[t]["total"] += 1
            st = cw.get("threshold_state", "unknown")
            if st in type_counts[t]:
                type_counts[t][st] += 1

        return jsonify({
            "sessions": context_windows,
            "total": len(context_windows),
            "state_counts": state_counts,
            "type_counts": type_counts,
        })
    except Exception as e:
        return jsonify({"sessions": [], "total": 0, "state_counts": {}, "error": str(e)}), 500

# ============================================================
# API: Cron 可靠性（参考 oa-cli 思路 + openclaw-control-center cron-overview.ts）
# ============================================================

@app.route("/api/cron-reliability")
def api_cron_reliability():
    """
    读取 ~/.openclaw/cron/runs/*.jsonl，计算当日 Cron 任务成功率
    参考 oa-cli scanner.py + openclaw-control-center cron-overview.ts
    """
    try:
        OPENCLAW_HOME = Path.home() / ".openclaw"
        runs_dir = OPENCLAW_HOME / "cron" / "runs"
        jobs_file = OPENCLAW_HOME / "cron" / "jobs.json"

        now = datetime.now(TZ)
        now_ms = int(now.timestamp() * 1000)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_start_ms = int(today_start.timestamp() * 1000)

        # 1. 读取 job 定义（含 enabled / nextRunAtMs）
        job_defs = {}
        if jobs_file.exists():
            try:
                with open(jobs_file, 'r', encoding='utf-8') as f:
                    jobs_data = json.load(f)
                for job in jobs_data.get("jobs", []):
                    jid = job.get("id", "")
                    state = job.get("state", {})
                    job_defs[jid] = {
                        "name": job.get("name", jid),
                        "enabled": job.get("enabled", True),
                        "next_run_ms": state.get("nextRunAtMs") or 0,
                        "schedule_expr": job.get("schedule", {}).get("expr", ""),
                    }
            except:
                pass

        # 2. 扫描 jsonl 文件
        total_slots = 0
        success_count = 0
        failed_count = 0
        missed_count = 0
        job_stats = {}  # job_id -> {success, failed, missed, total, name}

        if runs_dir.exists():
            for run_file in runs_dir.glob("*.jsonl"):
                job_id = run_file.stem
                job_name = job_defs.get(job_id, {}).get("name", job_id)

                try:
                    with open(run_file, 'r', encoding='utf-8') as f:
                        lines = f.readlines()
                except:
                    continue

                # 只统计当日的 runs
                day_success = 0
                day_failed = 0
                day_total = 0

                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        run = json.loads(line)
                    except:
                        continue

                    run_ms = run.get("runAtMs", 0)
                    if run_ms < today_start_ms:
                        break  # 更早的记录不需要了（文件按时间排序）
                    if run_ms == 0:
                        continue

                    day_total += 1
                    total_slots += 1

                    status = run.get("status", "")
                    delivered = run.get("delivered", False)
                    delivery_status = run.get("deliveryStatus", "")

                    # 参考 openclaw-control-center: delivered 是主要指标
                    # status=error 明确是失败；delivered=true 明确成功
                    # status=ok 但 delivered=false 说明任务执行了但投递失败（部分成功）
                    if status == "error":
                        day_failed += 1
                        failed_count += 1
                    elif delivered:
                        day_success += 1
                        success_count += 1
                    else:
                        # delivered=false 且 status!=error: 任务执行了但未投递通知（按成功算，避免误报）
                        day_success += 1
                        success_count += 1

                if day_total > 0:
                    job_stats[job_id] = {
                        "name": job_name,
                        "success": day_success,
                        "failed": day_failed,
                        "total": day_total,
                    }

        # 3. 汇总
        total = total_slots
        success = success_count
        failed = failed_count
        rate = round(success / total * 100, 1) if total > 0 else 0

        # 4. 健康状态
        if total == 0:
            health = "none"
            health_label = "无执行记录"
        elif rate >= 90:
            health = "ok"
            health_label = "正常运行"
        elif rate >= 70:
            health = "warn"
            health_label = "部分失败"
        else:
            health = "error"
            health_label = "大量失败"

        # 写入 SQLite（每日汇总，按 job 聚合）
        if total > 0:
            try:
                conn = sqlite3.connect(str(DASHBOARD_DB))
                today_str = today_start.strftime("%Y-%m-%d")
                for job_id, jdata in job_stats.items():
                    conn.execute("""
                        INSERT INTO cron_daily (date, job_id, job_name, total, success, failed)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(date, job_id) DO UPDATE SET
                            total=excluded.total, success=excluded.success, failed=excluded.failed
                    """, (today_str, job_id, jdata["name"], jdata["total"], jdata["success"], jdata["failed"]))
                conn.commit()
                conn.close()
            except Exception as write_err:
                pass  # 静默失败，不影响主流程

        # 3. 为所有 job 添加健康状态
        all_jobs = []
        seen_job_ids = set()

        for jid, jdata in job_stats.items():
            seen_job_ids.add(jid)
            jdef = job_defs.get(jid, {})
            health, label, due_in = _resolve_cron_health(
                jdef.get("next_run_ms"), jdef.get("enabled", True), now_ms
            )
            next_run_ms = jdef.get("next_run_ms", 0)
            all_jobs.append({
                "job_id": jid,
                "name": jdata["name"],
                "schedule": jdef.get("schedule_expr", ""),
                "success": jdata["success"],
                "failed": jdata["failed"],
                "total": jdata["total"],
                "enabled": jdef.get("enabled", True),
                "next_run_at": datetime.fromtimestamp(next_run_ms / 1000, tz=TZ).isoformat() if next_run_ms and next_run_ms > 0 else None,
                "due_in_seconds": due_in,
                "health": health,
                "health_label": label,
            })

        for jid, jdef in job_defs.items():
            if jid in seen_job_ids:
                continue
            seen_job_ids.add(jid)
            health, label, due_in = _resolve_cron_health(
                jdef.get("next_run_ms"), jdef.get("enabled", True), now_ms
            )
            next_run_ms = jdef.get("next_run_ms", 0)
            all_jobs.append({
                "job_id": jid,
                "name": jdef["name"],
                "schedule": jdef.get("schedule_expr", ""),
                "success": 0, "failed": 0, "total": 0,
                "enabled": jdef.get("enabled", True),
                "next_run_at": datetime.fromtimestamp(next_run_ms / 1000, tz=TZ).isoformat() if next_run_ms and next_run_ms > 0 else None,
                "due_in_seconds": due_in,
                "health": health,
                "health_label": label,
            })

        HEALTH_RANK = {"late": 0, "due": 1, "scheduled": 2, "disabled": 3, "unknown": 4}
        all_jobs.sort(key=lambda x: (HEALTH_RANK.get(x["health"], 5), x.get("due_in_seconds") or 0))

        total = total_slots
        success = success_count
        failed = failed_count
        rate = round(success / total * 100, 1) if total > 0 else 0

        late_count = sum(1 for j in all_jobs if j["health"] == "late")
        due_count = sum(1 for j in all_jobs if j["health"] == "due")
        enabled_count = sum(1 for j in all_jobs if j["enabled"])

        if total == 0 and enabled_count > 0:
            if any(j["health"] in ("late", "due") for j in all_jobs):
                health = "warn"; health_label = "等待下次执行"
            else:
                health = "ok"; health_label = "等待下次执行"
        elif late_count > 0:
            health = "error"; health_label = f"{late_count}个任务延迟"
        elif due_count > 0:
            health = "warn"; health_label = f"{due_count}个任务即将执行"
        elif rate >= 90:
            health = "ok"; health_label = "正常运行"
        elif rate >= 70:
            health = "warn"; health_label = "部分失败"
        else:
            health = "error"; health_label = "大量失败"

        if total > 0:
            try:
                conn = sqlite3.connect(str(DASHBOARD_DB))
                today_str = today_start.strftime("%Y-%m-%d")
                for jdata in all_jobs:
                    if jdata["total"] > 0:
                        conn.execute("""
                            INSERT INTO cron_daily (date, job_id, job_name, total, success, failed)
                            VALUES (?, ?, ?, ?, ?, ?)
                            ON CONFLICT(date, job_id) DO UPDATE SET
                                total=excluded.total, success=excluded.success, failed=excluded.failed
                        """, (today_str, jdata["job_id"], jdata["name"], jdata["total"], jdata["success"], jdata["failed"]))
                conn.commit(); conn.close()
            except Exception: pass

        return jsonify({
            "date": today_start.strftime("%Y-%m-%d"),
            "total_slots": total,
            "success": success,
            "failed": failed,
            "rate": rate,
            "health": health,
            "health_label": health_label,
            "jobs": all_jobs,
        })
    except Exception as e:
        return jsonify({"error": str(e), "total_slots": 0, "success": 0, "failed": 0, "rate": 0, "health": "error", "jobs": []}), 500


# ============================================================
# API: Cron 历史趋势（读 SQLite）
# ============================================================

@app.route("/api/cron-history")
def api_cron_history():
    """
    读取 SQLite cron_daily 表，返回近 N 天趋势
    """
    days = int(request.args.get("days", 7))
    days = max(1, min(days, 90))  # 限制 1-90 天

    try:
        conn = sqlite3.connect(str(DASHBOARD_DB))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        today = datetime.now(TZ).strftime("%Y-%m-%d")
        cur.execute("""
            SELECT date, job_id, job_name, total, success, failed
            FROM cron_daily
            WHERE date <= ?
            ORDER BY date DESC
        """, (today,))
        rows = cur.fetchall()
        conn.close()

        # 按日期聚合
        by_date = defaultdict(lambda: {"total": 0, "success": 0, "failed": 0})
        by_job = defaultdict(lambda: defaultdict(lambda: {"total": 0, "success": 0, "failed": 0}))

        for row in rows:
            d = row["date"]
            by_date[d]["total"] += row["total"]
            by_date[d]["success"] += row["success"]
            by_date[d]["failed"] += row["failed"]
            by_job[row["job_id"]][d]["total"] += row["total"]
            by_job[row["job_id"]]["name"] = row["job_name"]
            by_job[row["job_id"]][d]["success"] += row["success"]
            by_job[row["job_id"]][d]["failed"] += row["failed"]

        # 生成日期序列
        date_series = []
        for i in range(days - 1, -1, -1):
            d = (datetime.now(TZ) - timedelta(days=i)).strftime("%Y-%m-%d")
            rate = 0.0
            if by_date[d]["total"] > 0:
                rate = round(by_date[d]["success"] / by_date[d]["total"] * 100, 1)
            date_series.append({
                "date": d,
                "total": by_date[d]["total"],
                "success": by_date[d]["success"],
                "failed": by_date[d]["failed"],
                "rate": rate,
            })

        # Top job 趋势（取最多执行的 5 个 job）
        top_jobs = sorted(by_job.items(), key=lambda x: sum(v.get("total", 0) for v in x[1].values() if isinstance(v, dict)), reverse=True)[:5]

        return jsonify({
            "days": days,
            "by_date": date_series,
            "top_jobs": [
                {
                    "job_id": job_id,
                    "name": data.get("name", job_id),
                    "dates": {d: v for d, v in data.items() if d != "name" and isinstance(v, dict)}
                }
                for job_id, data in top_jobs
            ],
        })
    except Exception as e:
        return jsonify({"error": str(e), "days": days, "by_date": [], "top_jobs": []}), 500


# ============================================================
# API: Agent 活跃度（参考 oa-cli 思路）
# ============================================================

@app.route("/api/agent-activity")
def api_agent_activity():
    """
    读取 ~/.openclaw/sessions/ 目录，按 agent 聚合最后活跃时间
    参考 oa-cli OpenClawScanner._scan_agents() 和 openclaw-control-center agent-roster.ts
    """
    try:
        OPENCLAW_HOME = Path.home() / ".openclaw"
        agents_dir = OPENCLAW_HOME / "agents"

        now = datetime.now(TZ)
        agents_map = {}  # agent_id -> {name, last_active, session_count}

        # 1. 从 agents/*/sessions/sessions.json 扫描（主数据源）
        if agents_dir.exists():
            for agent_dir in agents_dir.iterdir():
                if not agent_dir.is_dir():
                    continue
                agent_id = agent_dir.name
                sessions_json = agent_dir / "sessions" / "sessions.json"
                if not sessions_json.exists():
                    continue
                try:
                    with open(sessions_json, 'r', encoding='utf-8') as f:
                        sessions_data = json.load(f)
                except:
                    continue

                for session_key, session_info in sessions_data.items():
                    # 更新最后活跃时间
                    updated_at = session_info.get("updatedAt", 0)
                    if updated_at:
                        mtime = datetime.fromtimestamp(updated_at / 1000.0, tz=TZ)
                        mtime_iso = mtime.isoformat()
                        if agent_id not in agents_map:
                            agents_map[agent_id] = {
                                "id": agent_id,
                                "name": agent_id.upper(),
                                "last_active": mtime_iso,
                                "last_active_ago": _ago(mtime, now),
                                "session_count": 0,
                            }
                        else:
                            existing = agents_map[agent_id]
                            if mtime_iso > (existing["last_active"] or "1970-01-01"):
                                existing["last_active"] = mtime_iso
                                existing["last_active_ago"] = _ago(mtime, now)
                        agents_map[agent_id]["session_count"] += 1


        # 2. 从 agents 目录补充（确保所有 agent 都被发现）
        if agents_dir.exists():
            for ad in agents_dir.iterdir():
                if ad.is_dir():
                    agent_id = ad.name
                    if agent_id not in agents_map:
                        agents_map[agent_id] = {
                            "id": agent_id,
                            "name": agent_id.upper(),
                            "last_active": None,
                            "last_active_ago": "从未活跃",
                            "session_count": 0,
                        }

        # 3. 排序：最近活跃的排前面
        agents = list(agents_map.values())
        agents.sort(key=lambda x: x["last_active"] or "1970-01-01", reverse=True)

        # 4. 健康状态判断
        for agent in agents:
            ago = agent["last_active_ago"]
            if ago == "从未活跃":
                agent["health"] = "idle"
            elif "分钟" in ago or "刚刚" in ago:
                agent["health"] = "active"
            elif "小时前" in ago:
                hours = int(re.search(r'(\d+)', ago).group(1)) if re.search(r'(\d+)', ago) else 0
                agent["health"] = "active" if hours < 2 else "idle"
            else:
                agent["health"] = "idle"

        # 写入 SQLite（每日快照）
        try:
            conn = sqlite3.connect(str(DASHBOARD_DB))
            today_str = datetime.now(TZ).strftime("%Y-%m-%d")
            for agent in agents:
                conn.execute("""
                    INSERT INTO agent_daily (date, agent_id, agent_name, session_count, last_active_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(date, agent_id) DO UPDATE SET
                        session_count=excluded.session_count, last_active_at=excluded.last_active_at
                """, (today_str, agent["id"], agent["name"], agent["session_count"], agent["last_active"]))
            conn.commit()
            conn.close()
        except Exception as write_err:
            pass  # 静默失败，不影响主流程

        return jsonify({
            "agents": agents,
            "total": len(agents),
            "active_count": sum(1 for a in agents if a["health"] == "active"),
        })
    except Exception as e:
        return jsonify({"error": str(e), "agents": [], "total": 0, "active_count": 0}), 500


def _ago(dt: datetime, now: datetime) -> str:
    """计算相对时间字符串"""
    delta = now - dt
    seconds = delta.total_seconds()
    if seconds < 60:
        return "刚刚"
    minutes = int(seconds / 60)
    if minutes < 60:
        return f"{minutes}分钟前"
    hours = int(minutes / 60)
    if hours < 24:
        return f"{hours}小时前"
    days = int(hours / 24)
    return f"{days}天前"


# ============================================================
# API: 用量拆分（参考 openclaw-control-center usage-cost.ts）
# ============================================================

def _scan_session_jsonl(sessions_json_path: Path, lookback_days: int):
    """
    扫描一个 sessions.json，解析所有 .jsonl 文件中的 assistant 用量事件
    排除 cron 类型的 session（cron 用量从 cron/runs/*.jsonl 单独获取）
    返回: list of {date, source_type, source_label, model, input, output, total_tokens, cost}
    """
    OPENCLAW_HOME = Path.home() / ".openclaw"
    lookback_ms = (datetime.now(TZ) - timedelta(days=lookback_days)).timestamp() * 1000
    events = []

    try:
        with open(sessions_json_path, 'r', encoding='utf-8') as f:
            sessions_data = json.load(f)
    except:
        return events

    # 建立 sessionId -> sessionKey 的映射，排除 cron session（走 cron/runs/*.jsonl）
    session_key_map = {}  # uuid -> sessionKey
    for sk, sv in sessions_data.items():
        # 跳过 cron session（:cron: 存在于 key 中）
        sk_lower = sk.lower()
        if ":cron:" in sk_lower:
            continue
        sid = sv.get("sessionId", "")
        if sid:
            session_key_map[sid] = sk

    sessions_dir = sessions_json_path.parent

    for uuid, session_key in session_key_map.items():
        jsonl_file = sessions_dir / f"{uuid}.jsonl"
        if not jsonl_file.exists():
            continue

        # 判断 source_type
        sk_lower = session_key.lower()
        if "feishu" in sk_lower:
            source_type = "feishu"
            source_label = "飞书"
        elif "weixin" in sk_lower or "wechat" in sk_lower:
            source_type = "wechat"
            source_label = "微信"
        elif sk_lower == "agent:main:main":
            source_type = "main"
            source_label = "主会话"
        else:
            source_type = "other"
            source_label = session_key

        try:
            mtime = jsonl_file.stat().st_mtime
            if mtime * 1000 < lookback_ms:
                continue  # 文件太旧，跳过
        except:
            pass

        try:
            with open(jsonl_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except:
                        continue

                    if d.get("type") != "message":
                        continue
                    msg = d.get("message", {})
                    if msg.get("role") != "assistant":
                        continue

                    usage = msg.get("usage", {})
                    if not usage:
                        continue

                    ts_raw = d.get("timestamp") or msg.get("timestamp")
                    if not ts_raw:
                        continue

                    try:
                        # timestamp 可能是 ISO 字符串或毫秒数字
                        if isinstance(ts_raw, str):
                            ts_ms = datetime.fromisoformat(ts_raw.replace("Z", "+00:00")).timestamp() * 1000
                        else:
                            ts_ms = float(ts_raw)
                        if ts_ms < lookback_ms:
                            continue
                    except:
                        pass

                    cost_obj = usage.get("cost", {})
                    cost = cost_obj.get("total") if isinstance(cost_obj, dict) else 0
                    if cost is None:
                        cost = 0

                    events.append({
                        "date": datetime.now(TZ).strftime("%Y-%m-%d"),
                        "source_type": source_type,
                        "source_label": source_label,
                        "model": msg.get("model", "unknown") or "unknown",
                        "input": usage.get("input", 0) or 0,
                        "output": usage.get("output", 0) or 0,
                        "total_tokens": usage.get("totalTokens", 0) or usage.get("input", 0) + usage.get("output", 0),
                        "cost": cost,
                    })
        except:
            continue

    return events


def _scan_cron_runs_jsonl(lookback_days: int):
    """
    读取 cron/runs/*.jsonl，提取每个 run 的 usage（jobId / model / provider / tokens / cost）
    cost 从 usage.totalTokens 估算（参考 usage-cost.ts 的推算逻辑）
    """
    OPENCLAW_HOME = Path.home() / ".openclaw"
    runs_dir = OPENCLAW_HOME / "cron" / "runs"
    jobs_file = OPENCLAW_HOME / "cron" / "jobs.json"
    lookback_ms = (datetime.now(TZ) - timedelta(days=lookback_days)).timestamp() * 1000

    # 读 job 名称映射
    job_names = {}
    if jobs_file.exists():
        try:
            with open(jobs_file, 'r', encoding='utf-8') as f:
                for job in json.load(f).get("jobs", []):
                    job_names[job.get("id", "")] = job.get("name", job.get("id", ""))
        except:
            pass

    events = []
    if not runs_dir.exists():
        return events

    for run_file in runs_dir.glob("*.jsonl"):
        job_id = run_file.stem
        job_name = job_names.get(job_id, job_id)

        try:
            with open(run_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        except:
            continue

        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                run = json.loads(line)
            except:
                continue

            run_ms = run.get("runAtMs", 0)
            if run_ms > 0 and run_ms < lookback_ms:
                continue

            usage = run.get("usage", {})
            input_tokens = usage.get("input_tokens", 0) or 0
            output_tokens = usage.get("output_tokens", 0) or 0
            total_tokens = usage.get("total_tokens", 0) or (input_tokens + output_tokens)

            # 估算 cost（参考 openclaw-control-center，用 MMLU pricing 估算）
            # MiniMax M2.7: input ~$0.001/1K tokens, output ~$0.003/1K tokens
            cost = (input_tokens / 1_000_000 * 0.7) + (output_tokens / 1_000_000 * 2.0)

            # 从 sessionKey 解析日期
            session_key = run.get("sessionKey", "")
            run_dt = datetime.fromtimestamp(run_ms / 1000, tz=TZ) if run_ms > 0 else datetime.now(TZ)
            date_str = run_dt.strftime("%Y-%m-%d")

            events.append({
                "date": date_str,
                "source_type": "cron",
                "source_label": job_name,
                "model": run.get("model", "unknown") or "unknown",
                "input": input_tokens,
                "output": output_tokens,
                "total_tokens": total_tokens,
                "cost": round(cost, 6),
            })

    return events


def _write_usage_to_sqlite(events: list):
    """将用量事件聚合写入 SQLite"""
    if not events:
        return
    today_str = datetime.now(TZ).strftime("%Y-%m-%d")
    # 按 (date, source_type, source_label, model) 聚合
    agg = defaultdict(lambda: {"input": 0, "output": 0, "total_tokens": 0, "cost": 0, "requests": 0})
    for e in events:
        key = (e["date"], e["source_type"], e["source_label"], e["model"])
        agg[key]["input"] += e["input"]
        agg[key]["output"] += e["output"]
        agg[key]["total_tokens"] += e["total_tokens"]
        agg[key]["cost"] += e["cost"]
        agg[key]["requests"] += 1

    try:
        conn = sqlite3.connect(str(DASHBOARD_DB))
        for (date, st, sl, model), v in agg.items():
            conn.execute("""
                INSERT INTO usage_daily (date, source_type, source_label, model, input_tokens, output_tokens, total_tokens, cost, requests)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(date, source_type, source_label, model) DO UPDATE SET
                    input_tokens=excluded.input_tokens, output_tokens=excluded.output_tokens,
                    total_tokens=excluded.total_tokens, cost=excluded.cost, requests=excluded.requests
            """, (date, st, sl, model, v["input"], v["output"], v["total_tokens"], round(v["cost"], 6), v["requests"]))
        conn.commit()
        conn.close()
    except Exception:
        pass


@app.route("/api/usage-breakdown")
def api_usage_breakdown():
    """
    扫描 agents/*/sessions/sessions.jsonl，统计用量拆分（byCronJob / byChannel）
    每次调用时写入 SQLite 积累历史，同时返回当日实时拆分
    """
    days = int(request.args.get("days", 7))
    days = max(1, min(days, 30))

    OPENCLAW_HOME = Path.home() / ".openclaw"
    agents_dir = OPENCLAW_HOME / "agents"

    all_events = []

    # 1. 普通 session 用量（agents/*/sessions/*.jsonl）
    for agent_dir in agents_dir.iterdir():
        if not agent_dir.is_dir():
            continue
        sessions_json = agent_dir / "sessions" / "sessions.json"
        if sessions_json.exists():
            all_events.extend(_scan_session_jsonl(sessions_json, days))

    # 2. Cron 任务用量（cron/runs/*.jsonl）
    all_events.extend(_scan_cron_runs_jsonl(days))

    # 写入 SQLite（当日数据）
    today_str = datetime.now(TZ).strftime("%Y-%m-%d")
    today_events = [e for e in all_events if e["date"] == today_str]
    if today_events:
        _write_usage_to_sqlite(today_events)

    # 聚合实时结果
    total_input = sum(e["input"] for e in all_events)
    total_output = sum(e["output"] for e in all_events)
    total_tokens = sum(e["total_tokens"] for e in all_events)
    total_cost = sum(e["cost"] for e in all_events)
    total_requests = len(all_events)

    # by source_type
    by_type = defaultdict(lambda: {"input": 0, "output": 0, "total_tokens": 0, "cost": 0, "requests": 0, "sources": defaultdict(lambda: {"tokens": 0, "cost": 0, "requests": 0})})
    for e in all_events:
        st = e["source_type"]
        by_type[st]["input"] += e["input"]
        by_type[st]["output"] += e["output"]
        by_type[st]["total_tokens"] += e["total_tokens"]
        by_type[st]["cost"] += e["cost"]
        by_type[st]["requests"] += 1
        sl = e["source_label"]
        by_type[st]["sources"][sl]["tokens"] += e["total_tokens"]
        by_type[st]["sources"][sl]["cost"] += e["cost"]
        by_type[st]["sources"][sl]["requests"] += 1

    by_type_result = {}
    for st, v in by_type.items():
        sources_list = [
            {"label": sl, "tokens": sv["tokens"], "cost": round(sv["cost"], 4), "requests": sv["requests"]}
            for sl, sv in v["sources"].items()
        ]
        sources_list.sort(key=lambda x: x["tokens"], reverse=True)
        by_type_result[st] = {
            "input": v["input"],
            "output": v["output"],
            "total_tokens": v["total_tokens"],
            "cost": round(v["cost"], 4),
            "requests": v["requests"],
            "sources": sources_list,
        }

    # 按 token 排序 source_type
    sorted_types = sorted(by_type_result.items(), key=lambda x: x[1]["total_tokens"], reverse=True)

    return jsonify({
        "days": days,
        "total_input": total_input,
        "total_output": total_output,
        "total_tokens": total_tokens,
        "total_cost": round(total_cost, 4),
        "total_requests": total_requests,
        "by_type": [{"type": st, **v} for st, v in sorted_types],
    })


@app.route("/api/usage-history")
def api_usage_history():
    """
    读取 SQLite usage_daily 表，返回近 N 天趋势
    """
    days = int(request.args.get("days", 14))
    days = max(1, min(days, 90))

    try:
        conn = sqlite3.connect(str(DASHBOARD_DB))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        today = datetime.now(TZ).strftime("%Y-%m-%d")
        cur.execute("""
            SELECT date, source_type, source_label, model,
                   input_tokens, output_tokens, total_tokens, cost, requests
            FROM usage_daily
            WHERE date <= ?
            ORDER BY date DESC
        """, (today,))
        rows = cur.fetchall()
        conn.close()

        # 按日期聚合
        by_date = defaultdict(lambda: {"total_tokens": 0, "cost": 0, "requests": 0})
        # 按 source_type 聚合
        by_type = defaultdict(lambda: {"total_tokens": 0, "cost": 0, "requests": 0})

        for row in rows:
            by_date[row["date"]]["total_tokens"] += row["total_tokens"]
            by_date[row["date"]]["cost"] += row["cost"]
            by_date[row["date"]]["requests"] += row["requests"]
            by_type[row["source_type"]]["total_tokens"] += row["total_tokens"]
            by_type[row["source_type"]]["cost"] += row["cost"]
            by_type[row["source_type"]]["requests"] += row["requests"]

        # 日期序列
        date_series = []
        for i in range(days - 1, -1, -1):
            d = (datetime.now(TZ) - timedelta(days=i)).strftime("%Y-%m-%d")
            date_series.append({
                "date": d,
                "tokens": by_date[d]["total_tokens"],
                "cost": round(by_date[d]["cost"], 4),
                "requests": by_date[d]["requests"],
            })

        sorted_types = sorted(by_type.items(), key=lambda x: x[1]["total_tokens"], reverse=True)
        type_breakdown = [
            {"type": st, "tokens": v["total_tokens"], "cost": round(v["cost"], 4), "requests": v["requests"]}
            for st, v in sorted_types
        ]

        return jsonify({
            "days": days,
            "by_date": date_series,
            "by_type": type_breakdown,
        })
    except Exception as e:
        return jsonify({"error": str(e), "days": days, "by_date": [], "by_type": []}), 500


# ============================================================
# API: 项目入口
# ============================================================

@app.route("/api/work-data")
def api_work_data():
    """工作数据：SSD存储 / 照片 / 电子书"""
    try:
        # SSD 存储空间
        import shutil
        total, used, free = 0, 0, 0
        try:
            stats = shutil.disk_usage("/")
            total = stats.total
            used = stats.used
            free = stats.free
        except:
            pass

        # 照片数量
        photo_count = 0
        try:
            conn = sqlite3.connect(str(GALLERY_DB))
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM photos")
            photo_count = cur.fetchone()[0]
            conn.close()
        except:
            pass

        # 电子书数量
        index_dir = WORKSPACE_DIR / "library" / "index"
        indexed_count = 0
        if index_dir.exists():
            indexed_count = len(list(index_dir.glob("*_knowledge.json")))

        book_files = []
        for ext in ("*.txt", "*.pdf", "*.epub"):
            book_files.extend(WORKSPACE_DIR.glob(f"library/**/{ext}"))

        total_books = len(book_files)

        return jsonify({
            "ssd_total_gb": round(total / 1024**3, 1),
            "ssd_used_gb": round(used / 1024**3, 1),
            "ssd_free_gb": round(free / 1024**3, 1),
            "photo_count": photo_count,
            "book_total": total_books,
            "book_indexed": indexed_count,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/projects")
def api_projects():
    """项目入口列表"""
    projects = [
        {"id": "gallery", "name": "相册", "icon": "📷", "url": "/gallery/"},
        {"id": "library", "name": "电子书库", "icon": "📚", "url": "/library/"},
        {"id": "visit", "name": "个人知识管理", "icon": "🧠", "url": "/visit/"},
    ]
    return jsonify({"projects": projects})

# ============================================================
# 启动
# ============================================================

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 18794))
    app.run(host="0.0.0.0", port=port, debug=False)
