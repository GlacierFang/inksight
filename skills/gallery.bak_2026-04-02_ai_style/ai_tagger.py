#!/usr/bin/env python3
"""
AI 标签生成器
调用 MiniMax 大模型分析图片内容，生成标签和描述
"""

import os
import sys
import json
import base64
import requests
from pathlib import Path
from PIL import Image
from datetime import datetime

# MiniMax API 配置（从环境变量或配置文件读取）
MINIMAX_API_KEY = os.environ.get("MINIMAX_API_KEY", "")
MINIMAX_API_BASE = "https://api.minimaxi.com/anthropic/v1/messages"

# 标签库 - 用于从 AI 回复中提取标签
VALID_TAGS = [
    "人像", "风景", "建筑", "美食", "植物", "动物", "宠物",
    "樱花", "校园", "儿童", "室内", "室外", "夜景", "自拍",
    "截图", "文档", "产品", "活动", "旅行", "家庭", "朋友"
]

def encode_image_to_base64(image_path: str) -> str:
    """将图片转为 base64 编码"""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")

def call_minimax_vision(image_path: str, api_key: str) -> dict:
    """
    调用 MiniMax 视觉模型分析图片
    返回: {"tags": [...], "description": "..."}
    """
    if not api_key:
        raise ValueError("需要 MiniMax API Key")

    # 压缩图片（如果太大）
    img = Image.open(image_path)
    max_size = 1024
    if max(img.size) > max_size:
        img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
        # 临时保存压缩后的图片
        temp_path = image_path + ".temp.jpg"
        img.save(temp_path, "JPEG", quality=85)
        image_path = temp_path

    base64_image = encode_image_to_base64(image_path)

    # 删除临时文件
    if image_path.endswith(".temp.jpg"):
        os.remove(image_path)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": "MiniMax-M2.7",
        "max_tokens": 300,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "data": base64_image
                    },
                    {
                        "type": "text",
                        "text": """分析这张图片，用 JSON 格式返回结果：
{
  "tags": ["标签1", "标签2", ...],  // 3-8个标签，从以下词库选择或添加有意义的标签：人像,风景,建筑,美食,植物,动物,宠物,樱花,校园,儿童,室内,室外,夜景,自拍,截图,文档,产品,活动,旅行,家庭,朋友
  "description": "一句话描述图片内容，不超过50字"
}

只返回 JSON，不要其他内容。"""
                    }
                ]
            }
        ]
    }

    response = requests.post(
        MINIMAX_API_BASE,
        headers=headers,
        json=payload,
        timeout=30
    )

    if response.status_code != 200:
        raise Exception(f"API 调用失败: {response.status_code} - {response.text}")

    result = response.json()
    content = result.get("choices", [{}])[0].get("message", {}).get("content", "")

    # 解析 JSON 响应
    try:
        # 尝试提取 JSON
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]

        data = json.loads(content.strip())
        return {
            "tags": data.get("tags", []),
            "description": data.get("description", "")
        }
    except json.JSONDecodeError:
        # 如果解析失败，返回原始内容
        return {
            "tags": [],
            "description": content[:100]
        }

def tag_single_image(image_path: str, meta_path: str = None, api_key: str = None) -> dict:
    """
    为单张图片生成 AI 标签

    Args:
        image_path: 图片路径
        meta_path: .meta 文件路径（可选）
        api_key: MiniMax API Key

    Returns:
        标签结果 dict
    """
    if not api_key:
        api_key = MINIMAX_API_KEY

    print(f"分析图片: {image_path}")

    try:
        result = call_minimax_vision(image_path, api_key)
        print(f"  标签: {result['tags']}")
        print(f"  描述: {result['description']}")

        # 更新 meta 文件
        if meta_path and os.path.exists(meta_path):
            with open(meta_path, "r") as f:
                meta = json.load(f)
        else:
            meta = {}

        meta["ai_tags"] = result["tags"]
        meta["ai_desc"] = result["description"]
        meta["ai_tagged_at"] = datetime.now().isoformat()

        if meta_path:
            with open(meta_path, "w") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
            print(f"  已更新: {meta_path}")

        return result

    except Exception as e:
        print(f"  错误: {e}")
        return {"tags": [], "description": "", "error": str(e)}

def tag_directory(dir_path: str, api_key: str = None, limit: int = None):
    """
    批量处理目录下所有未标签的图片

    Args:
        dir_path: 目录路径
        api_key: API Key
        limit: 处理数量限制
    """
    if not api_key:
        api_key = MINIMAX_API_KEY

    photo_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    tagged_count = 0
    skipped_count = 0

    for root, dirs, files in os.walk(dir_path):
        if limit and tagged_count >= limit:
            break

        for filename in files:
            if limit and tagged_count >= limit:
                break

            filepath = Path(root) / filename
            if filepath.suffix.lower() not in photo_exts:
                continue

            meta_path = filepath.with_suffix(".meta")

            # 检查是否已有 AI 标签
            if meta_path.exists():
                try:
                    with open(meta_path, "r") as f:
                        meta = json.load(f)
                    if "ai_tags" in meta and meta["ai_tags"]:
                        skipped_count += 1
                        continue
                except:
                    pass

            # 处理这张图片
            tag_single_image(str(filepath), str(meta_path), api_key)
            tagged_count += 1

            # 避免请求过快
            import time
            time.sleep(1)

    print(f"\n完成! 新标签: {tagged_count}, 跳过(已有): {skipped_count}")

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="AI 图片标签生成器")
    parser.add_argument("path", help="图片路径或目录路径")
    parser.add_argument("--meta", help=".meta 文件路径", default=None)
    parser.add_argument("--api-key", help="MiniMax API Key", default=None)
    parser.add_argument("--batch", help="批量处理目录下所有图片", action="store_true")
    parser.add_argument("--limit", type=int, help="批量处理数量限制", default=None)

    args = parser.parse_args()

    api_key = args.api_key or MINIMAX_API_KEY

    if not api_key:
        print("错误: 需要设置 MINIMAX_API_KEY 环境变量或 --api-key 参数")
        print("\n申请地址: https://platform.minimaxi.com/")
        sys.exit(1)

    if args.batch:
        tag_directory(args.path, api_key, args.limit)
    else:
        tag_single_image(args.path, args.meta, api_key)
