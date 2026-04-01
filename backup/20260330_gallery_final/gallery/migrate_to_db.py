#!/usr/bin/env python3
"""
相册标签数据库迁移脚本
从 .meta 文件迁移到 SQLite 数据库
"""

import json
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))
from db import init_db, upsert_photo, set_photo_tags, ensure_tag, set_tag_aliases, add_tag_to_photo

PHOTOS_DIR = Path("/home/open/.openclaw/media/photos")
CANONICAL_TAGS_FILE = Path("/home/open/.openclaw/media/canonical_tags.json")

PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}

def load_canonical_tags():
    """加载标准标签库（兼容旧格式）"""
    if CANONICAL_TAGS_FILE.exists():
        with open(CANONICAL_TAGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"tags": {}}

def migrate_canonical_tags():
    """迁移标准标签库到数据库"""
    print("[1/3] 迁移标准标签库...")
    canonical = load_canonical_tags()
    count = 0
    for name, info in canonical.get("tags", {}).items():
        ensure_tag(name)
        aliases = info.get("aliases", [])
        if aliases:
            set_tag_aliases(name, aliases)
        count += 1
    print(f"  -> 迁入 {count} 个标准标签")

def migrate_photos():
    """迁移所有照片元数据到数据库"""
    print("[2/3] 迁移照片元数据...")
    photo_count = 0
    tag_count = 0

    for date_dir in PHOTOS_DIR.iterdir():
        if not date_dir.is_dir() or not date_dir.match("????-??-??"):
            continue
        for f in date_dir.iterdir():
            if f.suffix.lower() not in PHOTO_EXTS:
                continue

            photo_id = f.name
            meta_path = f.with_suffix(".jpg.meta")
            # 读取 meta（新版路径格式）
            if not meta_path.exists():
                meta_path = Path(str(f) + ".meta")

            meta = {}
            if meta_path.exists():
                try:
                    with open(meta_path, "r", encoding="utf-8") as mf:
                        meta = json.load(mf)
                except Exception as e:
                    print(f"  警告: 读取 meta 失败 {meta_path}: {e}")

            ai_tags = meta.get("ai_tags", [])
            # 如果没有 ai_tags，尝试 tags
            if not ai_tags:
                ai_tags = meta.get("tags", [])

            ai_desc = meta.get("ai_desc", "")
            ai_tagged_at = meta.get("ai_tagged_at", "")

            # 写入数据库
            upsert_photo(
                photo_id=photo_id,
                filepath=str(f),
                date=date_dir.name,
                timestamp=meta.get("timestamp", int(f.stat().st_mtime * 1000)),
                source=meta.get("source", "未知"),
                media_type="photo",
                ai_desc=ai_desc,
                ai_tagged_at=ai_tagged_at
            )

            # 写入标签（语义对齐）
            for tag in ai_tags:
                aligned = align_tag(tag)
                if aligned:
                    add_tag_to_photo(photo_id, aligned, source='ai')
                    tag_count += 1
                else:
                    # 未对齐的标签：作为新标签加入，再关联
                    add_tag_to_photo(photo_id, tag, source='ai')
                    tag_count += 1

            photo_count += 1

    print(f"  -> 迁入 {photo_count} 张照片, {tag_count} 个标签关联")

def migrate_organize_queue():
    """迁移 organize 的待处理队列"""
    print("[3/3] 检查待迁移...")
    queue_file = Path("/home/open/.openclaw/media/.ai_tag_queue.json")
    if queue_file.exists():
        try:
            with open(queue_file) as f:
                queue = json.load(f)
            print(f"  -> 发现 {len(queue)} 个待处理条目（将在后续由 cron job 处理）")
        except:
            pass
    print("迁移完成!")

def align_tag(tag_name):
    """语义对齐：返回标准标签或 None"""
    from db import get_db
    with get_db() as conn:
        c = conn.cursor()
        # 精确匹配
        c.execute("SELECT id, name FROM tags WHERE name = ?", (tag_name,))
        row = c.fetchone()
        if row:
            return row["name"]
        # 别名匹配
        c.execute("""
            SELECT t.name FROM tags t
            JOIN tag_aliases ta ON ta.tag_id = t.id
            WHERE ta.alias = ?
        """, (tag_name,))
        row = c.fetchone()
        if row:
            return row["name"]
        return None  # 未找到

if __name__ == "__main__":
    print("=" * 50)
    print("相册标签数据库迁移")
    print("=" * 50)
    print()
    init_db()
    migrate_canonical_tags()
    migrate_photos()
    migrate_organize_queue()
    print()
    print("=" * 50)
    print("迁移成功！数据库已就绪。")
