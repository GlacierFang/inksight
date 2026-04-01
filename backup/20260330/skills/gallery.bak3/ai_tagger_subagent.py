#!/usr/bin/env python3
"""
AI 标签生成器 - 通过 OpenClaw subagent 调用
安全隔离版本，不直接调 API
"""

import os
import sys
import json
import time
import subprocess
from pathlib import Path
from datetime import datetime

# ============== 配置 ==============
PHOTOS_DIR = Path("/home/open/.openclaw/media/photos")
STATE_FILE = Path("/home/open/.openclaw/media/.ai_tag_state.json")
QUEUE_FILE = Path("/home/open/.openclaw/media/.ai_tag_queue.json")

# OpenClaw 配置
OPENCLAW_GATEWAY = os.environ.get("OPENCLAW_GATEWAY", "http://localhost:18789")
OPENCLAW_TOKEN = os.environ.get("OPENCLAW_GATEWAY_TOKEN", "0cb7910eb4082efeab6613e3b55b7ab0d53d10387f59ed93")


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def load_state():
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except:
            pass
    return {"processed_count": 0, "error_count": 0, "last_processed": None}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def scan_unprocessed():
    """扫描未处理的图片"""
    photo_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    unprocessed = []

    if not PHOTOS_DIR.exists():
        return unprocessed

    for date_dir in sorted(PHOTOS_DIR.iterdir(), reverse=True):
        if not date_dir.is_dir():
            continue
        for f in sorted(date_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if f.suffix.lower() not in photo_exts:
                continue
            meta_path = f.with_suffix(".meta")
            # 检查是否需要处理
            needs_processing = True
            if meta_path.exists():
                try:
                    with open(meta_path) as mf:
                        meta = json.load(mf)
                    if meta.get("ai_tags") and meta.get("ai_desc"):
                        needs_processing = False
                except:
                    pass
            if needs_processing:
                unprocessed.append(str(f))

    return unprocessed


def invoke_openclaw_tool(tool_name, args):
    """通过 gateway API 调用 OpenClaw 工具"""
    import requests

    url = f"{OPENCLAW_GATEWAY}/tools/invoke"
    payload = {
        "tool": tool_name,
        "action": "json",
        "args": args
    }

    try:
        resp = requests.post(url, json=payload, headers={
            "Authorization": f"Bearer {OPENCLAW_TOKEN}",
            "Content-Type": "application/json"
        }, timeout=60)

        if resp.status_code == 200:
            return resp.json()
        else:
            log(f"工具调用失败: {resp.status_code} - {resp.text}")
            return None
    except Exception as e:
        log(f"请求失败: {e}")
        return None


def call_image_tool(image_path):
    """调用 OpenClaw 的 image 工具分析图片"""
    # image 工具接受 URL 或本地路径
    # 我们传本地路径，OpenClaw 会自动处理
    result = invoke_openclaw_tool("image", {
        "prompt": "分析这张图片，返回JSON格式的标签和描述：{\"tags\": [\"标签1\", \"标签2\"], \"description\": \"简短描述\"}。只返回JSON。",
        "image": str(image_path)
    })
    return result


def write_tags(image_path, tags, description):
    """写入标签到 .meta 文件"""
    meta_path = Path(image_path).with_suffix(".meta")

    meta = {}
    if meta_path.exists():
        try:
            with open(meta_path) as f:
                meta = json.load(f)
        except:
            pass

    meta["ai_tags"] = tags
    meta["ai_desc"] = description
    meta["ai_tagged_at"] = datetime.now().isoformat()

    with open(meta_path, "w") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def process_single(image_path):
    """处理单张图片"""
    log(f"分析中: {Path(image_path).name}")

    result = call_image_tool(image_path)

    if result and result.get("ok"):
        # 解析结果
        content = result.get("result", {}).get("content", "")
        # 尝试从 content 中提取标签
        try:
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            data = json.loads(content.strip())

            tags = data.get("tags", [])
            description = data.get("description", "")

            write_tags(image_path, tags, description)
            log(f"完成: {Path(image_path).name} -> {tags}")
            return True
        except json.JSONDecodeError:
            log(f"解析失败: {content[:100]}")
            return False
    else:
        log(f"调用失败: {result}")
        return False


def main():
    log("AI 标签生成器启动 (subagent 模式)")

    # 扫描待处理图片
    unprocessed = scan_unprocessed()
    log(f"发现 {len(unprocessed)} 张待处理图片")

    if not unprocessed:
        log("没有待处理图片")
        return

    # 限制每次处理数量
    max_per_run = int(os.environ.get("AI_TAGGER_BATCH_SIZE", "3"))
    to_process = unprocessed[:max_per_run]
    log(f"本次处理 {len(to_process)} 张")

    state = load_state()

    for image_path in to_process:
        success = process_single(image_path)
        if success:
            state["processed_count"] += 1
        else:
            state["error_count"] += 1

        state["last_processed"] = image_path
        save_state(state)

        # 处理间隔
        time.sleep(3)

    log(f"完成! 成功: {state['processed_count']}, 失败: {state['error_count']}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("用户中断")
    except Exception as e:
        log(f"错误: {e}")
        sys.exit(1)
