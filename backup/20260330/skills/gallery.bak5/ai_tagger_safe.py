#!/usr/bin/env python3
"""
AI 标签生成器 - 安全独立版
与 OpenClaw 完全隔离，通过 API 调用 MiniMax 视觉模型
"""

import os
import sys
import json
import base64
import time
import signal
import requests
from pathlib import Path
from datetime import datetime
from PIL import Image

# ============== 安全配置 ==============
MAX_IMAGE_SIZE = 800  # 最大图片尺寸（超过则压缩）
MAX_TOKENS = 2000  # AI 回复长度限制（图片推理thinking较长，需要更多token）
API_TIMEOUT = 60  # API 调用超时（秒）
PROCESS_TIMEOUT = 180  # 整个进程超时（秒）
REQUEST_DELAY = 1  # 请求间隔（秒）
MAX_RETRIES = 1  # 最大重试次数

# ============== 配置 ==============
PHOTOS_DIR = Path("/home/open/.openclaw/media/photos")
QUEUE_FILE = Path("/home/open/.openclaw/media/.ai_tag_queue.json")
STATE_FILE = Path("/home/open/.openclaw/media/.ai_tag_state.json")

# MiniMax API（通过环境变量配置）
API_KEY = os.environ.get("MINIMAX_API_KEY", "")
API_BASE = os.environ.get("MINIMAX_API_BASE", "https://api.minimaxi.com/anthropic/v1/messages")


def log(msg):
    """带时间戳的日志"""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def load_queue():
    """加载待处理队列"""
    if QUEUE_FILE.exists():
        try:
            with open(QUEUE_FILE, "r") as f:
                return json.load(f)
        except:
            pass
    return []


def save_queue(queue):
    """保存队列"""
    with open(QUEUE_FILE, "w") as f:
        json.dump(queue, f)


def load_state():
    """加载处理状态"""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except:
            pass
    return {"last_processed": None, "processed_count": 0, "error_count": 0}


def save_state(state):
    """保存处理状态"""
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def compress_image(image_path: str) -> str:
    """压缩图片，超过 MAX_IMAGE_SIZE 限制"""
    try:
        img = Image.open(image_path)
        if max(img.size) <= MAX_IMAGE_SIZE:
            return image_path

        img.thumbnail((MAX_IMAGE_SIZE, MAX_IMAGE_SIZE), Image.Resampling.LANCZOS)
        temp_path = image_path + ".compressed.jpg"
        img.save(temp_path, "JPEG", quality=85, optimize=True)
        return temp_path
    except Exception as e:
        log(f"压缩图片失败: {e}")
        return image_path


def encode_image(image_path: str) -> str:
    """图片转 base64"""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def call_ai_vision(image_path: str) -> dict:
    """调用 MiniMax 视觉模型，返回标签和描述"""
    if not API_KEY:
        raise ValueError("需要设置 MINIMAX_API_KEY 环境变量")

    # 压缩图片
    processed_path = compress_image(image_path)
    base64_image = encode_image(processed_path)
    log(f"[DEBUG] image sent: b64_len={len(base64_image)}")

    # 删除临时压缩文件
    if processed_path != image_path and os.path.exists(processed_path):
        try:
            os.remove(processed_path)
        except:
            pass

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": "MiniMax-M2.7",
        "max_tokens": MAX_TOKENS,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image", "data": base64_image},
                    {"type": "text", "text": """分析这张图片，用 JSON 格式返回：
{"tags": ["标签1", "标签2", "标签3"], "description": "简短描述，不超过30字"}
标签从以下选择或添加：人像,风景,建筑,美食,植物,动物,儿童,樱花,校园,室内,室外,夜景,宠物,旅行,家庭,截图,文档,活动
只返回 JSON。"""}
                ]
            }
        ]
    }

    response = requests.post(
        API_BASE,
        headers=headers,
        json=payload,
        timeout=API_TIMEOUT
    )

    if response.status_code != 200:
        raise Exception(f"API 错误: {response.status_code}")

    result = response.json()
    # MiniMax API 格式：content 是 list，包含 thinking 和 text 两种 block
    raw = result.get("content", "")
    log(f"[DEBUG] raw type={type(raw).__name__}, len={len(raw) if raw else 0}, repr={repr(raw)[:200]}")
    text_parts = []
    if isinstance(raw, list):
        for block in raw:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
                elif block.get("type") == "thinking":
                    pass  # skip thinking
    extracted = "".join(text_parts)

    # 解析 JSON（可能是嵌套的 {"text": "..."} 格式）
    extracted = extracted.strip()
    if not extracted:
        return {"tags": [], "description": "空响应"}
    try:
        # 尝试直接解析 JSON
        data = json.loads(extracted)
    except json.JSONDecodeError:
        # 可能是嵌套格式 {"text": "..."}
        try:
            wrapper = json.loads(extracted)
            if isinstance(wrapper, dict) and "text" in wrapper:
                data = json.loads(wrapper["text"])
            else:
                data = {"tags": [], "description": extracted[:50]}
        except json.JSONDecodeError:
            # 不是 JSON，返回原始文本描述
            return {"tags": [], "description": extracted[:50]}
    return {
        "tags": data.get("tags", []),
        "description": data.get("description", "")
    }
def process_image(image_path: str) -> bool:
    """处理单张图片，返回是否成功"""
    meta_path = Path(image_path).with_suffix(".meta")

    # 读取现有 meta
    meta = {}
    if meta_path.exists():
        try:
            with open(meta_path, "r") as f:
                meta = json.load(f)
        except:
            pass

    # 检查是否已有标签
    if meta.get("ai_tags") and meta.get("ai_desc"):
        log(f"已有标签，跳过: {Path(image_path).name}")
        return True

    log(f"分析中: {Path(image_path).name}")

    try:
        result = call_ai_vision(image_path)

        # 更新 meta
        meta["ai_tags"] = result["tags"]
        meta["ai_desc"] = result["description"]
        meta["ai_tagged_at"] = datetime.now().isoformat()

        with open(meta_path, "w") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        log(f"完成: {Path(image_path).name} -> {result['tags']}")
        return True

    except Exception as e:
        log(f"失败: {Path(image_path).name} - {e}")
        return False


def scan_for_unprocessed() -> list:
    """扫描待处理图片"""
    photo_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    unprocessed = []

    if not PHOTOS_DIR.exists():
        return unprocessed

    for date_dir in PHOTOS_DIR.iterdir():
        if not date_dir.is_dir():
            continue
        for f in date_dir.iterdir():
            if f.suffix.lower() not in photo_exts:
                continue
            meta_path = f.with_suffix(".meta")
            # 检查是否需要处理
            needs_processing = True
            if meta_path.exists():
                try:
                    with open(meta_path, "r") as mf:
                        meta = json.load(mf)
                    if meta.get("ai_tags") and meta.get("ai_desc"):
                        needs_processing = False
                except:
                    pass
            if needs_processing:
                unprocessed.append(str(f))

    return unprocessed


def main():
    """主函数"""
    log("AI 标签生成器启动")

    # 检查 API Key
    if not API_KEY:
        log("错误: 需要设置 MINIMAX_API_KEY 环境变量")
        sys.exit(1)

    # 扫描待处理图片
    unprocessed = scan_for_unprocessed()
    log(f"发现 {len(unprocessed)} 张待处理图片")

    if not unprocessed:
        log("没有待处理图片")
        return

    # 限制每次处理的图片数量
    max_per_run = int(os.environ.get("AI_TAGGER_BATCH_SIZE", "5"))
    to_process = unprocessed[:max_per_run]
    log(f"本次处理 {len(to_process)} 张")

    # 加载状态
    state = load_state()

    # 处理每张图片
    for image_path in to_process:
        success = process_image(image_path)
        if success:
            state["processed_count"] += 1
        else:
            state["error_count"] += 1

        state["last_processed"] = image_path
        save_state(state)

        # 请求间隔，避免过快
        time.sleep(REQUEST_DELAY)

    log(f"完成! 成功: {state['processed_count']}, 失败: {state['error_count']}")


if __name__ == "__main__":
    # 设置进程超时
    start_time = time.time()

    def timeout_handler(signum, frame):
        elapsed = time.time() - start_time
        log(f"进程超时 ({elapsed:.0f}秒)，退出")
        sys.exit(0)

    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(PROCESS_TIMEOUT)

    try:
        main()
    except KeyboardInterrupt:
        log("用户中断")
        sys.exit(0)
    except Exception as e:
        log(f"未知错误: {e}")
        sys.exit(1)
