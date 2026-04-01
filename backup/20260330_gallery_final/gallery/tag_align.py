#!/usr/bin/env python3
"""
语义标签对齐脚本
新照片打标签后，与标准标签库进行语义对齐
"""

import os
import json
from pathlib import Path
from datetime import datetime

CANONICAL_TAGS_FILE = Path("/home/open/.openclaw/media/canonical_tags.json")
PHOTOS_DIR = Path("/home/open/.openclaw/media/photos")

def load_canonical_tags():
    """加载标准标签库"""
    if CANONICAL_TAGS_FILE.exists():
        with open(CANONICAL_TAGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"tags": {}}

def save_canonical_tags(data):
    """保存标准标签库"""
    data["last_updated"] = datetime.now().strftime("%Y-%m-%d")
    with open(CANONICAL_TAGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def find_canonical_match(tag, canonical_data):
    """查找标签的标准匹配"""
    tags = canonical_data.get("tags", {})

    # 精确匹配标签名
    if tag in tags:
        return tag

    # 精确匹配别名
    for canonical, info in tags.items():
        if tag in info.get("aliases", []):
            return canonical

    return None

def align_tags(tags, canonical_data):
    """
    对齐一组标签
    返回: (对齐后的标签列表, 新发现的标签列表)
    """
    aligned = []
    new_tags = []
    tags_info = canonical_data.get("tags", {})

    for tag in tags:
        canonical = find_canonical_match(tag, canonical_data)
        if canonical:
            if canonical not in aligned:
                aligned.append(canonical)
        else:
            # 新标签，记录下来
            if tag not in aligned:
                aligned.append(tag)
                # 检查是否是已有标签的别名
                is_new = True
                for existing_tag, info in tags_info.items():
                    if tag in info.get("aliases", []):
                        is_new = False
                        break
                if is_new and tag not in new_tags:
                    new_tags.append(tag)

    return aligned, new_tags

def add_new_tag_to_library(tag, canonical_data):
    """将新标签添加到标准库"""
    tags = canonical_data.get("tags", {})
    if tag not in tags:
        tags[tag] = {
            "aliases": [],
            "category": "待分类",
            "added_at": datetime.now().isoformat()
        }
        print(f"  + 新增标准标签: {tag}")
    return canonical_data

def process_photo_meta(meta_path, canonical_data):
    """处理单张照片的 meta 文件"""
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    except Exception as e:
        return None, None, [], False

    if "ai_tags" not in meta or not meta["ai_tags"]:
        return None, None, [], False

    original_tags = meta["ai_tags"]
    aligned_tags, new_tags = align_tags(original_tags, canonical_data)

    # 检查是否有变化
    if set(original_tags) == set(aligned_tags):
        return original_tags, aligned_tags, [], False

    # 更新 meta
    meta["ai_tags"] = aligned_tags
    meta["ai_aligned_at"] = datetime.now().isoformat()

    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    return original_tags, aligned_tags, new_tags, True

def process_all():
    """处理所有照片"""
    canonical_data = load_canonical_tags()
    stats = {
        "total": 0,
        "aligned": 0,
        "new_tags_found": [],
        "errors": 0
    }

    for date_dir in PHOTOS_DIR.iterdir():
        if not date_dir.is_dir():
            continue
        for meta_file in date_dir.glob("*.meta"):
            stats["total"] += 1
            original_tags, aligned_tags, new_tags, changed = process_photo_meta(meta_file, canonical_data)

            if changed:
                stats["aligned"] += 1
                stats["new_tags_found"].extend(new_tags)
                print(f"对齐: {meta_file.name}")
                print(f"  旧: {original_tags}")
                print(f"  新: {aligned_tags}")

                # 将新标签加入标准库
                for tag in new_tags:
                    canonical_data = add_new_tag_to_library(tag, canonical_data)

    # 去重新标签
    stats["new_tags_found"] = list(set(stats["new_tags_found"]))

    return stats, canonical_data

def main():
    print("=" * 50)
    print("语义标签对齐")
    print("=" * 50)

    print("\n处理中...")
    stats, canonical_data = process_all()

    # 保存更新后的标准库
    if stats["new_tags_found"]:
        save_canonical_tags(canonical_data)
        print(f"\n标准标签库已更新，新增: {stats['new_tags_found']}")

    print("\n" + "=" * 50)
    print(f"完成! 总计: {stats['total']}, 对齐: {stats['aligned']}, 新标签: {len(stats['new_tags_found'])}")

if __name__ == "__main__":
    main()
