#!/usr/bin/env python3
"""为现有文件生成缩略图和元数据"""

import os
import json
from pathlib import Path
from PIL import Image

PHOTOS_DIR = Path("/home/open/.openclaw/media/photos")
THUMBNAILS_DIR = Path("/home/open/.openclaw/media/.thumbnails")
THUMB_SIZE = (400, 400)

def get_source_from_filename(fname):
    """从文件名判断来源"""
    fname_lower = fname.lower()
    if "feishu" in fname_lower or "ou_" in fname_lower:
        return "飞书"
    elif "weixin" in fname_lower or "wechat" in fname_lower or "wx_" in fname_lower:
        return "微信"
    return "未知"

def generate_thumbnail(src_path, thumb_path):
    """生成缩略图"""
    try:
        with Image.open(src_path) as img:
            if img.mode in ('RGBA', 'LA', 'P'):
                img = img.convert('RGB')
            img.thumbnail(THUMB_SIZE, Image.Resampling.LANCZOS)
            thumb_path.parent.mkdir(parents=True, exist_ok=True)
            img.save(thumb_path, "JPEG", quality=85, optimize=True)
            return True
    except Exception as e:
        print(f"Error: {e}")
        return False

for date_dir in PHOTOS_DIR.iterdir():
    if not date_dir.is_dir():
        continue
    date_str = date_dir.name
    
    for f in date_dir.iterdir():
        if f.is_file() and f.suffix.lower() in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}:
            # 生成缩略图
            thumb_dir = THUMBNAILS_DIR / date_str
            thumb_path = thumb_dir / f"{f.stem}.jpg"
            
            if not thumb_path.exists():
                if generate_thumbnail(f, thumb_path):
                    print(f"Thumbnail: {f.name} -> {thumb_path}")
            
            # 添加元数据
            meta_path = f.with_suffix(".meta")
            if not meta_path.exists():
                source = get_source_from_filename(f.name)
                meta_path.write_text(json.dumps({
                    "source": source,
                    "date": date_str,
                    "original_name": f.name
                }, ensure_ascii=False))
                print(f"Meta: {f.name} -> source={source}")

print("Done!")
