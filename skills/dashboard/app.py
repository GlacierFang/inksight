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
API_LOG_JSONL = Path("/home/open/.openclaw/workspace/memory/minimax-api-log.jsonl")
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

def load_api_calls():
    """读取 API 调用记录：旧 JSON + 新 JSONL 合并"""
    calls = []
    if API_LOG_FILE.exists():
        try:
            with open(API_LOG_FILE, encoding="utf-8") as f:
                calls.extend(json.load(f).get("calls", []))
        except Exception:
            pass
    if API_LOG_JSONL.exists():
        try:
            with open(API_LOG_JSONL, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        calls.append(json.loads(line))
                    except Exception:
                        continue
        except Exception:
            pass
    return calls


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

def _get_minimax_api_key():
    """从环境变量或 skills/gallery/ai_tagger.env 加载 MiniMax API Key（参考 nature-local/app.py:19-26）"""
    key = os.environ.get("MINIMAX_API_KEY") or os.environ.get("MINIMAX_CN_API_KEY") or ""
    if not key:
        env_file = Path(__file__).parent.parent / "gallery" / "ai_tagger.env"
        if env_file.exists():
            try:
                for line in env_file.read_text().splitlines():
                    line = line.strip()
                    if line.startswith("MINIMAX_API_KEY"):
                        key = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break
            except Exception:
                pass
    return key

# ============================================================
# 路由
# ============================================================

@app.route("/")
def index():
    return render_template("dashboard.html")

# ============================================================
# 生日网站 /happy/
# ============================================================
import os
from flask import send_from_directory, make_response

HAPPY_STATIC = SKILLS_DIR / "dashboard" / "static" / "happy"
HAPPY_TEMPLATES = SKILLS_DIR / "dashboard" / "templates" / "happy"

@app.route("/happy/")
def happy_index():
    return render_template("happy/index.html")

@app.route("/happy/game")
def happy_game():
    return render_template("happy/game.html")

@app.route("/happy/pic")
def happy_pic():
    return send_from_directory(HAPPY_STATIC, "gift.png", mimetype="image/png")

@app.route("/happy/gift")
def happy_gift():
    return send_from_directory(HAPPY_STATIC, "gift.png", mimetype="image/png")

# ============================================================
# API: 系统消耗统计
# ============================================================

@app.route("/api/stats")
def api_stats():
    """系统消耗统计（日/周/月）"""
    period = request.args.get("period", "day")  # day / week / month
    
    all_calls = load_api_calls()
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
        c for c in all_calls
        if datetime.fromisoformat(c["timestamp"]).replace(tzinfo=TZ) >= start
    ]
    
    # 按来源分组
    by_source = defaultdict(int)
    total = len(calls)
    for c in calls:
        by_source[c.get("source", "unknown")] += 1
    
    # 当前窗口统计
    window_key = get_window_key()
    window_calls = [c for c in all_calls if c.get("window") == window_key]
    
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
    all_calls = load_api_calls()
    now = datetime.now(TZ)
    
    # 最近30天
    days = defaultdict(lambda: defaultdict(int))
    for c in all_calls:
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
    """记忆状态 - 分项目文档和每日记录两个区块（加固：始终返回有效 JSON）"""
    try:
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
                except Exception:
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
                except Exception:
                    pass

        # 分别按更新时间倒序
        daily_files.sort(key=lambda x: x["updated"], reverse=True)
        project_files.sort(key=lambda x: x["updated"], reverse=True)

        # OpenClaw 设定文件（写死白名单：workspace 根目录的核心配置 md）
        OPENCLAW_FILES = [
            "SOUL.md", "USER.md", "AGENTS.md", "IDENTITY.md",
            "MEMORY.md", "TOOLS.md", "HEARTBEAT.md", "README.md",
        ]
        settings_openclaw = []
        for fname in OPENCLAW_FILES:
            f = WORKSPACE_DIR / fname
            if not f.exists():
                continue
            try:
                mt = f.stat().st_mtime
                dt = datetime.fromtimestamp(mt, tz=TZ)
                settings_openclaw.append({
                    "name": fname,
                    "path": fname,
                    "updated": dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "days_ago": (datetime.now(TZ) - dt).days,
                })
            except Exception:
                pass

        return jsonify({
            "daily": daily_files,
            "projects": project_files,
            "settings_openclaw": settings_openclaw,
        })
    except Exception as e:
        # 任何异常都返回有效 JSON，避免前端 JSON.parse 挂掉
        return jsonify({"daily": [], "projects": [], "error": str(e)}), 200


@app.route("/api/memory/<path:filename>")
def api_memory_file(filename):
    """读取指定记忆文件的纯文本内容"""
    import urllib.parse
    filename = urllib.parse.unquote(filename)  # 解码 %2F → /
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

def _du_size(path):
    """递归计算目录总大小（字节），跳过无权限/不存在的文件"""
    total = 0
    try:
        for entry in path.rglob('*'):
            if entry.is_file():
                try:
                    total += entry.stat().st_size
                except (OSError, PermissionError):
                    pass
    except (OSError, PermissionError):
        pass
    return total


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
            # minimax API 直接给 remaining_percent，不再用 total-used 算（total_count 一直是 0）
            interval_remain_pct = m.get("current_interval_remaining_percent", 0)
            weekly_remain_pct = m.get("current_weekly_remaining_percent", 0)
            interval_pct = round(100 - interval_remain_pct, 1)
            weekly_pct = round(100 - weekly_remain_pct, 1)
            interval_total = m.get("current_interval_total_count", 0)
            interval_used = m.get("current_interval_usage_count", 0)
            weekly_total = m.get("current_weekly_total_count", 0)
            weekly_used = m.get("current_weekly_usage_count", 0)
            interval_end_ms = m.get("end_time", 0)
            weekly_end_ms = m.get("weekly_end_time", 0)
            interval_status_code = m.get("current_interval_status", 0)
            weekly_status_code = m.get("current_weekly_status", 0)

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
                    weekly_reset_in = f"{days}天{total_seconds % 86400 // 3600}小时后"

            # 状态判断（基于 consumed pct）
            if interval_pct >= 90:
                interval_status = "critical"
            elif interval_pct >= 70:
                interval_status = "warn"
            else:
                interval_status = "ok"

            # 名称美化
            name_map = {
                "general": "通用模型",
                "video": "视频模型",
                "speech-hd": "语音 HD",
                "MiniMax-Hailuo-2.3-Fast-6s-768p": "视频 Fast",
                "MiniMax-Hailuo-2.3-6s-768p": "视频标准",
                "music-2.5": "音乐",
                "image-01": "图片",
                "MiniMax-H3-Image": "图片",
            }
            display_name = name_map.get(model_name, model_name)

            models.append({
                "name": display_name,
                "model_key": model_name,
                "interval_total": interval_total,
                "interval_used": interval_used,
                "interval_remain": interval_remain,
                "interval_remain_pct": interval_remain_pct,
                "interval_pct": interval_pct,
                "interval_status": interval_status,
                "interval_reset_in": interval_reset_in,
                "weekly_total": weekly_total,
                "weekly_used": weekly_used,
                "weekly_remain": weekly_remain,
                "weekly_remain_pct": weekly_remain_pct,
                "weekly_pct": weekly_pct,
                "weekly_reset_in": weekly_reset_in,
            })

        # 提取 general 模型单独返回（兼容老 JS 代码的 dTp.m3 fallback）
        m3_info = next((m for m in models if m.get("model_key") == "general"), None)

        return jsonify({
            "connected": True,
            "models": models,
            "m3": m3_info,
            "fetched_at": now.isoformat(),
        })
    except Exception as e:
        return jsonify({"connected": False, "error": str(e), "models": []}), 200


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


@app.route("/api/system")
def api_system():
    """系统状态检查"""
    items = []
    
    # 1. Token 窗口
    try:
        all_calls = load_api_calls()
        now = datetime.now(TZ)
        window_key = get_window_key()
        window_calls = [c for c in all_calls if c.get("window") == window_key]
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
    "MiniMax-M3": 204800,
    "MiniMax-M3.1-Flash": 204800,
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


@app.route("/dashboard/api/projects")
def api_projects_dashboard():
    """快速入口列表（兼容前端 /dashboard/api/projects 路径）"""
    return api_projects()

@app.route("/api/projects")
def api_projects():
    """项目入口列表（写死，与 dashboard 快速入口 panel 对齐）"""
    projects = [
        {"id": "library", "name": "电子书库", "icon": "📚", "url": "/library/"},
        {"id": "my-island", "name": "梦见岛", "icon": "🏝", "url": "/my-island-web/"},
        {"id": "stamp", "name": "邮票收藏", "icon": "📮", "url": "/stamp/"},
        {"id": "ink", "name": "墨鱼墨水屏", "icon": "🖥️", "url": "/ink"},
        
        {"id": "diary", "name": "存在感日记", "icon": "📅", "url": "/diary/"},
        
        {"id": "retro", "name": "老游戏厅", "icon": "🕹", "url": "/retro/"},
        {"id": "app-download", "name": "App 下载页", "icon": "📱", "url": "/app/"},
        {"id": "wenhua", "name": "华夏文明五书", "icon": "📖", "url": "/wenhua/"},
        {"id": "hanmo", "name": "翰墨时空谱", "icon": "📜", "url": "/hanmo/"},
        {"id": "huaxia", "name": "华夏文明纪", "icon": "🎮", "url": "/huaxia/"},
        {"id": "shiji", "name": "史迹探寻录", "icon": "📜", "url": "/shiji/"},
        {"id": "guobao", "name": "国宝博物馆", "icon": "🏺", "url": "/guobao/"},
        {"id": "puzzle", "name": "游戏下载页", "icon": "🧩", "url": "/game/"},
        {"id": "bala", "name": "躺平巴拉", "icon": "🐐", "url": "/bala/"},
        {"id": "shanhe-app", "name": "山河图记", "icon": "🗺️", "url": "/shanhe-app/"},
        {"id": "pet", "name": "桌面宠物", "icon": "🐾", "url": "/ai-agent-pet/"},
        {"id": "eink-mini", "name": "墨水屏留言板", "icon": "📟", "url": "/eink-mini/"},

    ]
    return jsonify({"projects": projects})

# ============================================================
# 新增 API：daemon-status / health / presence-log / usage-daily-compare
# ============================================================

import subprocess

def _read_proc_stat_cpu():
    """读取 /proc/stat 计算 CPU 使用率（瞬时采样）"""
    try:
        with open('/proc/stat') as f:
            line = f.readline()
        parts = line.split()
        total = sum(int(x) for x in parts[1:8])
        idle = int(parts[4])
        return total, idle
    except Exception:
        return 0, 0

def _calc_cpu_percent():
    """两次采样计算 CPU 使用率"""
    try:
        t1, i1 = _read_proc_stat_cpu()
        import time
        time.sleep(0.1)
        t2, i2 = _read_proc_stat_cpu()
        total_delta = t2 - t1
        idle_delta = i2 - i1
        if total_delta <= 0:
            return 0
        return round((total_delta - idle_delta) * 100 / total_delta, 1)
    except Exception:
        return 0

def _read_loadavg():
    try:
        with open('/proc/loadavg') as f:
            parts = f.read().split()
        return {'1': parts[0], '5': parts[1], '15': parts[2]}
    except Exception:
        return {}

def _read_mem():
    try:
        with open('/proc/meminfo') as f:
            lines = f.readlines()
        info = {}
        for line in lines:
            if ':' in line:
                k, v = line.split(':', 1)
                info[k.strip()] = v.strip().split()[0]  # kB
        total = int(info.get('MemTotal', 0))
        avail = int(info.get('MemAvailable', 0))
        used = total - avail
        pct = round(used * 100 / total, 1) if total > 0 else 0
        free_mb = round(avail / 1024, 0)
        return {'percent': pct, 'used_mb': round(used / 1024, 0), 'total_mb': round(total / 1024, 0), 'free_mb': free_mb}
    except Exception:
        return {}

def _read_disk():
    try:
        st = subprocess.run(['df', '-PB1', '/'], capture_output=True, text=True, timeout=3)
        lines = st.stdout.strip().split('\n')
        if len(lines) >= 2:
            parts = lines[1].split()
            total = int(parts[1])
            used = int(parts[2])
            free = int(parts[3])
            pct = int(parts[4].rstrip('%'))
            return {'percent': pct, 'used': f'{used // (1024**3)}G', 'free': f'{free // (1024**3)}G', 'total': f'{total // (1024**3)}G'}
    except Exception:
        pass
    return {}

def _read_cpu_temp():
    """读取 CPU 温度（树莓派优先读 thermal_zone0）"""
    try:
        # 树莓派
        with open('/sys/class/thermal/thermal_zone0/temp') as f:
            return round(int(f.read().strip()) / 1000, 1)
    except Exception:
        pass
    try:
        # 通用 thermal_zone
        for z in range(5):
            try:
                with open(f'/sys/class/thermal/thermal_zone{z}/temp') as f:
                    return round(int(f.read().strip()) / 1000, 1)
            except Exception:
                continue
    except Exception:
        pass
    return None

def _read_uptime():
    try:
        with open('/proc/uptime') as f:
            secs = float(f.read().split()[0])
        days = int(secs // 86400)
        hours = int((secs % 86400) // 3600)
        mins = int((secs % 3600) // 60)
        if days > 0:
            return f'{days}天{hours}小时'
        elif hours > 0:
            return f'{hours}小时{mins}分'
        else:
            return f'{mins}分'
    except Exception:
        return '-'


def _get_service_status():
    """从 ports.json 读取服务清单，检查各端口是否存活"""
    registry_path = Path("/home/open/.openclaw/workspace/tools/port-registry/ports.json")
    try:
        with open(registry_path) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

    ports_data = data.get('ports', {})
    try:
        ss_out = subprocess.run(['ss', '-tlnp'], capture_output=True, text=True, timeout=3).stdout
    except Exception:
        ss_out = ''

    result = {}
    for port_str, info in sorted(ports_data.items(), key=lambda x: int(x[0])):
        scope = info.get('scope', 'user')
        if scope not in ('user', 'both'):
            continue
        port = int(port_str)
        alive = f':{port} ' in ss_out or f':{port}\n' in ss_out
        desc = info.get('description', '')
        name = desc.split('（')[0].split('(')[0].strip() or info.get('service', port_str)
        if len(name) > 20:
            name = name[:20] + '…'
        result[name] = {
            'port': port,
            'url': info.get('path'),
            'status': 'up' if alive else 'down',
        }
    return result


@app.route("/api/health")
def api_health():
    """树莓派健康监控 + 服务端口状态（CPU/内存/磁盘/温度 + services）"""
    try:
        cpu = _calc_cpu_percent()
        load = _read_loadavg()
        mem = _read_mem()
        disk = _read_disk()
        temp = _read_cpu_temp()
        uptime = _read_uptime()
        services = _get_service_status()
        return jsonify({
            'cpu_percent': cpu,
            'load': load,
            'mem': mem,
            'disk': disk,
            'cpu_temp': temp,
            'uptime': uptime,
            'services': services,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route("/api/daemon-status")
def api_daemon_status():
    """守护进程状态（动态扫描所有 openclaw-* 服务，按 skill 分组）"""
    try:
        env = os.environ.copy()
        env['XDG_RUNTIME_DIR'] = f'/run/user/{os.getuid()}'
        env['DBUS_SESSION_BUS_ADDRESS'] = f'unix:path=/run/user/{os.getuid()}/bus'

        # 已知守护进程的 (skill, 中文描述) 映射（按 skill 分组）
        DAEMON_DESC = {
            # Gallery 相册
            'gallery-push': ('相册', '相册 AI 识别推送'),
            'gallery-thumbs': ('相册', '相册缩略图生成'),
            'gallery-app': ('时光印象册', '时光印象册 App 后端'),
            # Library 电子书库
            'library': ('电子书库', '电子书库主服务'),
            # Knowledge 知识中心 / 探识
            'knowledge-app': ('探识', '探识 App 后端'),
            'knowledge-briefing': ('知识中心', '每日知识简报推送'),
            # Nature 识物志
            'nature-local': ('识物志', '识物志本地版 API'),
            # Train 驿车志
            'train-app': ('驿车志', '驿车志 App 后端'),
            # Apps 后端（独立 skill）
            'mizong': ('觅踪', '觅踪 App 后端'),
            'qingnang': ('青囊书', '青囊书 App 后端'),
            # Stamp 邮票
            'stamp-collection': ('邮票收藏', '邮票收藏主服务'),
            'stamp-monitor': ('邮票收藏', '邮票价格监控'),
            'stamp-price-updater': ('邮票收藏', '邮票价格更新'),
            'new-stamp-fetcher': ('邮票收藏', '新邮票抓取'),
            # My-island 梦见岛
            'my-island': ('梦见岛', '梦见岛 Flask 后端'),
            'my-island-vite': ('梦见岛', '梦见岛 Vite 前端'),
            'my-island-notice': ('梦见岛', '梦见岛消息推送'),
            # Visit / Video / Retro
            'retro-gaming': ('老游戏厅', '复古模拟器'),
            # Creative 创意工坊
            'creative-app': ('创意工坊', '如梦令 App 后端'),
            # Presence 蓝牙感知 / 存在感
            'bt-presence': ('蓝牙感知', '蓝牙感知守护进程'),
            'presence': ('存在感日记', '存在感日记主页'),
            'presence-daemon': ('存在感日记', '存在感日记守护'),
            'presence-push': ('存在感日记', '存在感日记推送'),
            # AI / 工具
            'deepseek-webui': ('DeepSeek', 'DeepSeek WebUI'),
            'organize': ('队列处理', '队列处理'),
            'ai-tagger': ('AI标注', 'AI 标注定时任务'),
            # 推送 / 提醒
            'poetry-push': ('诗词签', '诗词签飞书推送'),
            'scraps-push': ('灵感碎片', '灵感碎片飞书推送'),
            'reminder-daemon': ('待办提醒', '待办提醒'),
            # Gateway / Dashboard / 发行版
            'gateway': ('Gateway', 'OpenClaw 主网关'),
            'dashboard': ('Dashboard', '个人工作台'),
            
            'app-download': ('App下载', 'App 下载页'),
        }

        # 1. 扫描所有 openclaw-* unit（user + system scope）
        units_with_scope = []
        seen = set()
        for scope_args, scope_name in [(['--user'], 'user'), ([], 'system')]:
            try:
                r = subprocess.run(
                    ['systemctl'] + scope_args + ['list-unit-files', 'openclaw-*.service',
                     '--no-pager', '--no-legend', '--plain', '--state', 'enabled,disabled,static,generated,transient'],
                    capture_output=True, text=True, timeout=5, env=env
                )
                for line in r.stdout.splitlines():
                    parts = line.split()
                    if parts and parts[0] not in seen:
                        seen.add(parts[0])
                        units_with_scope.append((parts[0], scope_name))
            except Exception:
                pass

        daemons = []
        for unit, scope_name in sorted(units_with_scope):
            # 短名
            short = unit.replace('openclaw-', '').replace('.service', '')
            if short not in DAEMON_DESC:
                # 未列入白名单的 daemon（zombie / 不需要展示）直接跳过
                continue
            skill, desc = DAEMON_DESC[short]

            # 2. 探测状态
            scope_args = ['--user'] if scope_name == 'user' else []
            try:
                sr = subprocess.run(
                    ['systemctl'] + scope_args + ['is-active', unit],
                    capture_output=True, text=True, timeout=3, env=env
                )
                state = sr.stdout.strip()
                if state == 'active':
                    status = 'running'
                    status_text = '运行中'
                elif state == 'failed':
                    status = 'failed'
                    status_text = '启动失败'
                else:
                    status = 'stopped'
                    status_text = '已停止'
            except Exception:
                status = 'unknown'
                status_text = '未知'

            # 3. 取最后一条日志
            last_msg = ''
            last_time = ''
            try:
                jr = subprocess.run(
                    ['journalctl'] + scope_args + ['-u', unit, '-n', '1',
                     '--no-pager', '-q', '--since=-1 day', '--output=short'],
                    capture_output=True, text=True, timeout=3, env=env
                )
                line = jr.stdout.strip()
                if line:
                    # 格式: "Aug 12 14:50:00 host unit[pid]: message"
                    parts = line.split(' ', 5)
                    if len(parts) >= 6:
                        last_time = f'{parts[0]} {parts[1]} {parts[2]}'
                        last_msg = parts[5][:120]
                    else:
                        last_msg = line[:120]
            except Exception:
                pass

            daemons.append({
                'name': short,
                'desc': desc,
                'skill': skill,
                'scope': scope_name,
                'unit': unit,
                'status': status,
                'status_text': status_text,
                'last_time': last_time,
                'last_msg': last_msg,
            })

        # 按 skill 分组排序
        daemons.sort(key=lambda d: (d['skill'], d['name']))

        running_cnt = sum(1 for d in daemons if d['status'] == 'running')
        summary = f'运行 {running_cnt} / 共 {len(daemons)} 个守护进程'

        return jsonify({
            'daemons': daemons,
            'summary': summary,
        })
    except Exception as e:
        return jsonify({'daemons': [], 'summary': f'错误: {e}', 'error': str(e)})


@app.route("/api/presence-log")
def api_presence_log():
    """今日蓝牙感知事件（从 bt-presence 持久化日志读）"""
    try:
        presence_log = Path("/home/open/.openclaw/workspace/skills/bt-presence/presence_log.json")
        if not presence_log.exists():
            return jsonify({'ok': True, 'entries': []})
        try:
            data = json.loads(presence_log.read_text())
        except Exception:
            data = {'log': []}
        events = data.get('log', [])
        if not events:
            return jsonify({'ok': True, 'entries': []})

        # 取今日（按 ts unix 时间戳）
        today_start = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        today_events = [e for e in events if e.get('ts', 0) >= today_start]
        # 最新在前
        today_events.sort(key=lambda e: e.get('ts', 0), reverse=True)

        entries = []
        for e in today_events:
            event_type = e.get('event', '').lower()
            if event_type not in ('home', 'away'):
                continue
            entry = {
                'event': event_type,
                'time': e.get('time', ''),
                'timestamp': str(int(e.get('ts', 0))),
            }
            if e.get('absent_minutes') is not None:
                entry['absent_minutes'] = e['absent_minutes']
            entries.append(entry)

        return jsonify({'ok': True, 'entries': entries})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e), 'entries': []})


@app.route("/api/usage-daily-compare")
def api_usage_daily_compare():
    """每日 Token 用量对比（分页 + 趋势）"""
    try:
        days = int(request.args.get('days', 7))
        page = int(request.args.get('page', 1))
        page_size = int(request.args.get('page_size', 10))
        page = max(1, page)
        page_size = max(1, min(50, page_size))

        rows = []
        if DASHBOARD_DB.exists():
            conn = sqlite3.connect(str(DASHBOARD_DB))
            try:
                cur = conn.cursor()
                cur.execute("""
                    SELECT date, COALESCE(SUM(total_tokens), 0) AS total
                    FROM usage_daily
                    WHERE date >= date('now', '-' || ? || ' days', 'localtime')
                    GROUP BY date
                    ORDER BY date DESC
                """, (days,))
                rows = cur.fetchall()
            finally:
                conn.close()

        # 计算趋势（vs 前一天）
        daily = []
        for i, (d, total) in enumerate(rows):
            if i + 1 < len(rows):
                prev_total = rows[i + 1][1]
                if total > prev_total * 1.05:
                    trend = 'up'
                elif total < prev_total * 0.95:
                    trend = 'down'
                else:
                    trend = 'flat'
            else:
                trend = 'flat'
            daily.append({'date': d, 'total': total, 'trend': trend})

        # 分页
        total_records = len(daily)
        total_pages = (total_records + page_size - 1) // page_size
        start = (page - 1) * page_size
        paged = daily[start:start + page_size]
        grand_total = sum(r['total'] for r in daily)

        return jsonify({
            'days': days,
            'grand_total': grand_total,
            'daily': paged,
            'total_pages': total_pages,
            'current_page': page,
            'total_records': total_records,
            'page_size': page_size,
        })
    except Exception as e:
        return jsonify({'error': str(e), 'daily': [], 'total_pages': 1, 'current_page': 1, 'total_records': 0, 'grand_total': 0, 'days': days})


# ============================================================
# 启动
# ============================================================

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 18794))
    app.run(host="0.0.0.0", port=port, debug=False)
