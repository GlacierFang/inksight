#!/usr/bin/env python3
"""
媒体来源监控服务
监控 inbound/ 目录，新文件出现时从最近会话获取渠道信息并写入 .meta 文件
"""

import os
import sys
import json
import glob
from pathlib import Path
from datetime import datetime
import subprocess
import time

INBOUND_DIR = Path("/home/open/.openclaw/media/inbound")
SESSION_DIR = Path("/home/open/.openclaw/agents/main/sessions")

def get_recent_session_channel():
    """获取最近活跃会话的渠道"""
    sessions_file = SESSION_DIR / "sessions.json"
    if not sessions_file.exists():
        return "未知"
    
    try:
        with open(sessions_file) as f:
            data = json.load(f)
        
        sessions = list(data.values())
        if not sessions:
            return "未知"
        
        # 按 lastChannel 分组，取每个渠道最近活跃的会话
        feishu_sessions = []
        weixin_sessions = []
        other_sessions = []
        
        for s in sessions:
            last_ch = s.get("lastChannel", "")
            updated = s.get("updatedAt", 0)
            if last_ch == "feishu" or s.get("origin", {}).get("provider") == "feishu":
                feishu_sessions.append((updated, s))
            elif "weixin" in str(last_ch).lower() or "wechat" in str(last_ch).lower() or last_ch == "openclaw-weixin" or s.get("origin", {}).get("provider") == "openclaw-weixin":
                weixin_sessions.append((updated, s))
            else:
                other_sessions.append((updated, s))
        
        # 策略：优先用 lastChannel 非空的最近会话
        # 如果当前有活跃的 lastChannel 会话，用它；否则按 updatedAt  fallback
        all_with_channel = feishu_sessions + weixin_sessions
        all_with_channel.sort(key=lambda x: x[0], reverse=True)
        
        if all_with_channel:
            _, session = all_with_channel[0]
            last_ch = session.get("lastChannel", "")
            origin_provider = session.get("origin", {}).get("provider", "")
            
            if last_ch == "feishu" or origin_provider == "feishu":
                return "飞书"
            elif "weixin" in str(last_ch).lower() or "wechat" in str(last_ch).lower() or last_ch == "openclaw-weixin" or origin_provider == "openclaw-weixin":
                return "微信"
        
        # Fallback: 最近的任意会话
        sessions.sort(key=lambda x: x.get("updatedAt", 0), reverse=True)
        session = sessions[0]
        delivery_context = session.get("deliveryContext", {})
        channel = delivery_context.get("channel", "")
        origin = session.get("origin", {})
        provider = origin.get("provider", "")
        
        if channel == "feishu" or provider == "feishu":
            return "飞书"
        elif "weixin" in str(channel).lower() or "wechat" in str(channel).lower() or channel == "openclaw-weixin" or provider == "openclaw-weixin":
            return "微信"
        
        return channel if channel else "未知"
        
    except Exception as e:
        print(f"Error reading sessions: {e}")
        return "未知"

def create_meta_file(filepath: Path, source: str):
    """创建 .meta 文件"""
    meta_path = Path(str(filepath) + ".meta")
    
    # 如果 meta 已存在，跳过
    if meta_path.exists():
        try:
            existing = json.loads(meta_path.read_text())
            if existing.get("source") and existing.get("source") != "未知":
                return False  # 已有有效来源
        except:
            pass
    
    meta = {
        "source": source,
        "filename": filepath.name,
        "timestamp": int(filepath.stat().st_mtime * 1000),
        "detected_at": datetime.now().isoformat()
    }
    
    try:
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
        print(f"Meta: {filepath.name} -> {source}")
        return True
    except Exception as e:
        print(f"Error creating meta for {filepath.name}: {e}")
        return False

def process_existing_files():
    """处理已存在但没有有效 meta 的文件"""
    if not INBOUND_DIR.exists():
        return
    
    source = get_recent_session_channel()
    print(f"Using default source: {source}")
    
    for fpath in INBOUND_DIR.iterdir():
        if fpath.is_file() and not fpath.name.endswith(".meta"):
            ext = fpath.suffix.lower()
            if ext in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv"}:
                create_meta_file(fpath, source)

def watch_inbound():
    """监控 inbound/ 目录"""
    print("Starting media watcher...")
    
    # 先处理已存在的文件
    process_existing_files()
    
    # 使用 inotifywait 监控
    cmd = [
        "inotifywait",
        "-m",           # 持续监控
        "-e", "CREATE", # 只监控创建事件
        "-q",           # 静默输出
        "--format", "%f",
        str(INBOUND_DIR)
    ]
    
    print(f"Watching {INBOUND_DIR} for new media files...")
    
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    
    for line in process.stdout:
        filename = line.decode().strip()
        
        # 忽略 .meta 文件
        if filename.endswith(".meta"):
            continue
        
        # 检查是否是媒体文件
        ext = Path(filename).suffix.lower()
        if ext in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv"}:
            print(f"New file: {filename}")
            
            # 等待文件写完成
            time.sleep(0.5)
            
            # 获取当前活跃会话的渠道
            source = get_recent_session_channel()
            print(f"  -> Source: {source}")
            
            fpath = INBOUND_DIR / filename
            if fpath.exists():
                create_meta_file(fpath, source)

if __name__ == "__main__":
    try:
        watch_inbound()
    except KeyboardInterrupt:
        print("Media watcher stopped.")
        sys.exit(0)
