#!/usr/bin/env python3
"""
批量归并标签脚本
根据规则归并所有照片的 AI 标签
"""

import os
import json
from pathlib import Path
from datetime import datetime

PHOTOS_DIR = Path("/home/open/.openclaw/media/photos")

# 归并规则
MERGE_RULES = [
    {
        "target": "中国古建筑",
        "merge_from": ["中国传统建筑", "中国建筑", "中式建筑"]
    },
    {
        "target": "石刻",
        "merge_from": ["石碑", "石雕"]
    },
    {
        "target": "历史古迹",
        "merge_from": ["历史遗迹"]
    },
    {
        "target": "石像生",
        "merge_from": ["石象"]
    },
    {
        "target": "石窟寺",
        "merge_from": ["翠屏石窟", "巩义石窟寺"]
    },
]

def merge_tags(tags):
    """根据规则归并标签"""
    merged = []
    to_remove = set()
    
    for tag in tags:
        replaced = False
        for rule in MERGE_RULES:
            if tag in rule["merge_from"]:
                # 查找是否已有目标标签
                if rule["target"] not in tags and rule["target"] not in merged:
                    merged.append(rule["target"])
                to_remove.add(tag)
                replaced = True
                break
        if not replaced and tag not in to_remove:
            merged.append(tag)
    
    return merged

def process_all():
    """处理所有照片的 meta 文件"""
    stats = {
        "total": 0,
        "merged": 0,
        "errors": 0
    }
    
    for date_dir in PHOTOS_DIR.iterdir():
        if not date_dir.is_dir():
            continue
        for meta_file in date_dir.glob("*.meta"):
            stats["total"] += 1
            try:
                meta = json.loads(meta_file.read_text())
                if "ai_tags" not in meta:
                    continue
                
                original_tags = meta["ai_tags"]
                new_tags = merge_tags(original_tags)
                
                # 检查是否有变化
                if set(original_tags) != set(new_tags):
                    meta["ai_tags"] = new_tags
                    meta["ai_merged_at"] = datetime.now().isoformat()
                    meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
                    stats["merged"] += 1
                    print(f"归并: {meta_file.name}")
                    print(f"  原: {original_tags}")
                    print(f"  新: {new_tags}")
                    
            except Exception as e:
                stats["errors"] += 1
                print(f"错误: {meta_file.name} - {e}")
    
    return stats

def main():
    print("=" * 50)
    print("AI 标签批量归并")
    print("=" * 50)
    print(f"\n归并规则：")
    for rule in MERGE_RULES:
        print(f"  {rule['target']} <- {rule['merge_from']}")
    print()
    
    print("开始处理...")
    stats = process_all()
    
    print()
    print("=" * 50)
    print(f"完成! 总计: {stats['total']}, 归并: {stats['merged']}, 错误: {stats['errors']}")
    
    # 保存归并日志
    log_file = Path("/home/open/.openclaw/media/.tag_merge_log.json")
    with open(log_file, "w") as f:
        json.dump({
            "at": datetime.now().isoformat(),
            "stats": stats,
            "rules": MERGE_RULES
        }, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
