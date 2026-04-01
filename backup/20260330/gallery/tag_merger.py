#!/usr/bin/env python3
"""
AI 标签归并分析脚本
收集所有标签，发送给 MiniMax 分析相似/可归并的标签组
"""

import os
import json
from pathlib import Path
from datetime import datetime

PHOTOS_DIR = Path("/home/open/.openclaw/media/photos")

def collect_all_tags():
    """收集所有唯一标签"""
    all_tags = {}
    
    for date_dir in PHOTOS_DIR.iterdir():
        if not date_dir.is_dir():
            continue
        for meta_file in date_dir.glob("*.meta"):
            try:
                meta = json.loads(meta_file.read_text())
                if "ai_tags" in meta and meta["ai_tags"]:
                    for tag in meta["ai_tags"]:
                        if tag not in all_tags:
                            all_tags[tag] = 0
                        all_tags[tag] += 1
            except:
                pass
    
    return all_tags

def analyze_tags_with_ai(tags_dict):
    """使用 MiniMax 分析标签归并规则"""
    import base64
    import requests
    
    # 准备标签列表
    tags_list = [f"{tag} ({count}次)" for tag, count in sorted(tags_dict.items(), key=lambda x: -x[1])]
    tags_text = "\n".join(tags_list)
    
    prompt = f"""分析以下图片标签，识别语义相同或高度相似的标签组，给出归并规则。

标签列表（标签后数字为使用次数）：
{tags_text}

请按以下格式输出归并建议（JSON数组）：
[
  {{
    "target": "保留的标签",
    "merge_from": ["被归并的标签1", "被归并的标签2"],
    "reason": "归并原因"
  }},
  ...
]

规则：
1. 只归并语义完全相同或极其相似的标签
2. 被归并的标签出现次数较少
3. 保留更准确或更常用的标签作为主标签
4. 如果没有可归并的标签，返回空数组 []

只返回JSON，不要其他内容。"""
    
    API_KEY = os.environ.get("MINIMAX_API_KEY", "")
    if not API_KEY:
        print("需要设置 MINIMAX_API_KEY 环境变量")
        return []
    
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": "MiniMax-M2.7",
        "max_tokens": 2500,
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": prompt}]
            }
        ]
    }
    
    response = requests.post(
        "https://api.minimaxi.com/anthropic/v1/messages",
        headers=headers,
        json=payload,
        timeout=60
    )
    
    if response.status_code != 200:
        print(f"API错误: {response.status_code} - {response.text}")
        return []
    
    result = response.json()
    content_blocks = result.get("choices", [{}])[0].get("message", {}).get("content", [])
    
    # 从 content blocks 中提取 text
    text_content = ""
    for block in content_blocks:
        if block.get("type") == "text":
            text_content = block.get("text", "")
            break
    
    # 解析JSON
    try:
        if "```json" in text_content:
            text_content = text_content.split("```json")[1].split("```")[0]
        elif "```" in text_content:
            text_content = text_content.split("```")[1].split("```")[0]
        return json.loads(text_content.strip())
    except json.JSONDecodeError as e:
        print(f"JSON解析失败: {e}")
        print(f"原始内容: {text_content[:500]}")
        return []

def main():
    print("=" * 50)
    print("AI 标签归并分析工具")
    print("=" * 50)
    
    # 收集标签
    print("\n[1/3] 收集所有标签...")
    tags_dict = collect_all_tags()
    print(f"共发现 {len(tags_dict)} 个不同标签")
    print("\n标签列表：")
    for tag, count in sorted(tags_dict.items(), key=lambda x: -x[1])[:30]:
        print(f"  {tag}: {count}次")
    if len(tags_dict) > 30:
        print(f"  ... 还有 {len(tags_dict) - 30} 个标签")
    
    # 分析归并规则
    print("\n[2/3] 发送至 AI 分析归并规则...")
    merge_rules = analyze_tags_with_ai(tags_dict)
    
    if not merge_rules:
        print("未找到归并规则")
        return
    
    # 输出归并规则
    print("\n[3/3] 归并建议：")
    print("=" * 50)
    for i, rule in enumerate(merge_rules, 1):
        print(f"\n规则 {i}:")
        print(f"  保留: {rule['target']}")
        print(f"  归并: {rule['merge_from']}")
        print(f"  原因: {rule['reason']}")
    
    print("\n" + "=" * 50)
    print(f"共 {len(merge_rules)} 条归并规则")
    
    # 保存规则
    output_file = Path("/home/open/.openclaw/media/.tag_merge_rules.json")
    with open(output_file, "w") as f:
        json.dump(merge_rules, f, ensure_ascii=False, indent=2)
    print(f"\n规则已保存至: {output_file}")

if __name__ == "__main__":
    main()
