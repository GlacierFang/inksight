#!/usr/bin/env python3
"""
媒体文件整理脚本
从 inbound/ 移动媒体到 photos/ 或 videos/，按日期归档
meta 文件始终存储为 {filename}.meta（非 with_suffix）
新照片自动加入 AI 标签队列
"""

import os
import json
import shutil
from pathlib import Path
from datetime import datetime
from PIL import Image

INBOUND_DIR = Path("/home/open/.openclaw/media/inbound")
PHOTOS_DIR = Path("/home/open/.openclaw/media/photos")
VIDEOS_DIR = Path("/home/open/.openclaw/media/videos")
THUMBNAILS_DIR = Path("/home/open/.openclaw/media/.thumbnails")
QUEUE_FILE = Path("/home/open/.openclaw/media/.ai_tag_queue.json")

# 支持的图片和视频扩展名
PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv"}

# 缩略图尺寸
THUMB_SIZE = (400, 400)


def meta_path(photo_path: Path) -> Path:
    """返回 meta 文件路径，始终为 photo_path + '.meta'"""
    return Path(str(photo_path) + ".meta")


def get_file_date(filepath: Path) -> str:
    """获取文件日期"""
    dt = datetime.fromtimestamp(filepath.stat().st_mtime)
    return dt.strftime("%Y-%m-%d")


def generate_thumbnail(src_path: Path, thumb_path: Path):
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
        print(f"Thumbnail error: {e}")
        return False


def load_queue():
    """加载队列"""
    if QUEUE_FILE.exists():
        try:
            with open(QUEUE_FILE) as f:
                return json.load(f)
        except:
            pass
    return []


def save_queue(queue):
    """保存队列"""
    with open(QUEUE_FILE, "w") as f:
        json.dump(queue, f, indent=2)


def add_to_queue(photo_path: str):
    """添加照片到 AI 处理队列"""
    queue = load_queue()
    if photo_path not in queue:
        queue.append(photo_path)
        save_queue(queue)
        print(f"Added to AI queue: {Path(photo_path).name}")


def organize_media():
    """整理 inbound/ 中的媒体文件"""
    if not INBOUND_DIR.exists():
        return

    THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)

    for filepath in INBOUND_DIR.iterdir():
        if not filepath.is_file():
            continue

        ext = filepath.suffix.lower()
        # 跳过非媒体文件和孤立的 .meta 文件
        if ext not in (PHOTO_EXTS | VIDEO_EXTS):
            continue

        # 检查是否为待处理文件（有 ai_tags 才移入相册，否则留在 inbound）
        mp = meta_path(filepath)
        if mp.exists():
            try:
                m = json.loads(mp.read_text(encoding="utf-8"))
                if not m.get("ai_tags"):
                    print(f"Skipping (pending AI tags): {filepath.name}")
                    continue
            except Exception:
                pass

        # 确定目标目录
        target_dir = PHOTOS_DIR if ext in PHOTO_EXTS else VIDEOS_DIR

        # 获取日期并创建目录
        date_str = get_file_date(filepath)
        date_dir = target_dir / date_str
        date_dir.mkdir(parents=True, exist_ok=True)

        # 目标路径
        target_path = date_dir / filepath.name

        if not target_path.exists():
            shutil.move(str(filepath), str(target_path))
            print(f"Moved: {filepath.name} -> {date_dir}")

            # 移动 meta 文件
            mp_src = meta_path(filepath)
            mp_dst = meta_path(target_path)
            if mp_src.exists():
                shutil.move(str(mp_src), str(mp_dst))
                print(f"Moved meta: {mp_src.name} -> {mp_dst.name}")

            # 生成缩略图
            if ext in PHOTO_EXTS:
                thumb_dir = THUMBNAILS_DIR / date_str
                thumb_path = thumb_dir / f"{filepath.stem}.jpg"
                if generate_thumbnail(target_path, thumb_path):
                    print(f"Thumbnail: {thumb_path}")
                add_to_queue(str(target_path))

        else:
            # 文件已存在，只移动 meta（但跳过：无 ai_tags 的 source meta 不覆盖已有 meta）
            mp_src = meta_path(filepath)
            mp_dst = meta_path(target_path)
            if mp_src.exists():
                try:
                    src_meta = json.loads(mp_src.read_text(encoding="utf-8"))
                    # 如果 source meta 没有 ai_tags，且 target meta 已存在有 ai_tags，跳过
                    if not src_meta.get("ai_tags") and mp_dst.exists():
                        dst_meta = json.loads(mp_dst.read_text(encoding="utf-8"))
                        if dst_meta.get("ai_tags"):
                            print(f"Skipped meta overwrite (target has ai_tags): {mp_src.name}")
                            continue
                    # 如果 source 有 ai_tags 但 target 没有，才移动
                    if src_meta.get("ai_tags") and not mp_dst.exists():
                        shutil.move(str(mp_src), str(mp_dst))
                        print(f"Moved meta: {mp_src.name}")
                except Exception:
                    pass


if __name__ == "__main__":
    organize_media()
