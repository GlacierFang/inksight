#!/usr/bin/env python3
"""
相册数据库层
负责 SQLite 连接、初始化、标签读写
"""

import sqlite3
import json
from pathlib import Path
from datetime import datetime
from contextlib import contextmanager

DB_PATH = Path("/home/open/.openclaw/media/gallery.db")

# ============== 数据库连接 ==============

@contextmanager
def get_db():
    """获取数据库连接的上下文管理器"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

# ============== 初始化 ==============

def init_db():
    """创建数据库表"""
    with get_db() as conn:
        c = conn.cursor()

        c.execute("""
        CREATE TABLE IF NOT EXISTS photos (
            id          TEXT PRIMARY KEY,
            filepath    TEXT UNIQUE NOT NULL,
            date        TEXT NOT NULL,
            timestamp   INTEGER NOT NULL,
            source      TEXT DEFAULT '未知',
            media_type  TEXT NOT NULL,
            ai_desc     TEXT,
            ai_tagged_at TEXT,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS tags (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT UNIQUE NOT NULL,
            category    TEXT DEFAULT '待分类',
            is_system   INTEGER DEFAULT 0,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS photo_tags (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            photo_id    TEXT NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
            tag_id      INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            source      TEXT NOT NULL DEFAULT 'ai',
            created_at  TEXT NOT NULL,
            UNIQUE(photo_id, tag_id)
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS tag_aliases (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            tag_id      INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            alias       TEXT UNIQUE NOT NULL,
            created_at  TEXT NOT NULL
        )
        """)

        # 索引
        c.execute("CREATE INDEX IF NOT EXISTS idx_photo_tags_photo ON photo_tags(photo_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_photo_tags_tag  ON photo_tags(tag_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_photos_date     ON photos(date)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_photos_source   ON photos(source)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_tag_aliases_tag ON tag_aliases(tag_id)")

        print(f"数据库初始化完成: {DB_PATH}")

# ============== 照片操作 ==============

def upsert_photo(photo_id, filepath, date, timestamp, source='未知', media_type='photo',
                 ai_desc=None, ai_tagged_at=None):
    """插入或更新照片记录"""
    now = datetime.now().isoformat()
    with get_db() as conn:
        c = conn.cursor()
        c.execute("""
            INSERT INTO photos (id, filepath, date, timestamp, source, media_type,
                               ai_desc, ai_tagged_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                filepath=excluded.filepath,
                date=excluded.date,
                timestamp=excluded.timestamp,
                source=excluded.source,
                media_type=excluded.media_type,
                ai_desc=excluded.ai_desc,
                ai_tagged_at=excluded.ai_tagged_at,
                updated_at=excluded.updated_at
        """, (photo_id, filepath, date, timestamp, source, media_type,
              ai_desc, ai_tagged_at, now, now))

def get_photo_tags(photo_id):
    """获取照片的所有标签"""
    with get_db() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT t.name FROM tags t
            JOIN photo_tags pt ON pt.tag_id = t.id
            WHERE pt.photo_id = ?
        """, (photo_id,))
        return [row["name"] for row in c.fetchall()]

def get_all_tags():
    """获取所有唯一标签"""
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT name FROM tags ORDER BY name")
        return [row["name"] for row in c.fetchall()]

def scan_photos_with_tags(tag_filter=None):
    """扫描照片，返回 [(photo_row, tags_list)]。无 tag_filter 时返回所有照片。"""
    with get_db() as conn:
        c = conn.cursor()
        if tag_filter:
            c.execute("""
                SELECT DISTINCT p.* FROM photos p
                JOIN photo_tags pt ON pt.photo_id = p.id
                JOIN tags t ON t.id = pt.tag_id
                WHERE t.name = ?
                ORDER BY p.timestamp DESC
            """, (tag_filter,))
        else:
            c.execute("SELECT * FROM photos ORDER BY timestamp DESC")
        photos = c.fetchall()

        result = []
        for p in photos:
            c.execute("""
                SELECT t.name FROM tags t
                JOIN photo_tags pt ON pt.tag_id = t.id
                WHERE pt.photo_id = ?
            """, (p["id"],))
            tags = [row["name"] for row in c.fetchall()]
            result.append((dict(p), tags))
        return result

# ============== 标签操作 ==============

def ensure_tag(name):
    """确保标签存在，返回 tag_id"""
    now = datetime.now().isoformat()
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT id FROM tags WHERE name = ?", (name,))
        row = c.fetchone()
        if row:
            return row["id"]
        c.execute("""
            INSERT INTO tags (name, created_at, updated_at) VALUES (?, ?, ?)
        """, (name, now, now))
        return c.lastrowid

def add_tag_to_photo(photo_id, tag_name, source='ai'):
    """给照片添加标签"""
    tag_id = ensure_tag(tag_name)
    now = datetime.now().isoformat()
    with get_db() as conn:
        c = conn.cursor()
        c.execute("""
            INSERT OR IGNORE INTO photo_tags (photo_id, tag_id, source, created_at)
            VALUES (?, ?, ?, ?)
        """, (photo_id, tag_id, source, now))

def remove_tag_from_photo(photo_id, tag_name):
    """从照片移除标签"""
    with get_db() as conn:
        c = conn.cursor()
        c.execute("""
            DELETE FROM photo_tags
            WHERE photo_id = ? AND tag_id = (
                SELECT id FROM tags WHERE name = ?
            )
        """, (photo_id, tag_name))

def set_photo_tags(photo_id, tag_names, source='ai'):
    """设置照片的全部标签（替换）"""
    with get_db() as conn:
        c = conn.cursor()
        # 删除旧标签
        c.execute("DELETE FROM photo_tags WHERE photo_id = ? AND source = ?", (photo_id, source))
        # 批量添加新标签
        now = datetime.now().isoformat()
        for name in tag_names:
            tag_id = ensure_tag(name)
            c.execute("""
                INSERT OR IGNORE INTO photo_tags (photo_id, tag_id, source, created_at)
                VALUES (?, ?, ?, ?)
            """, (photo_id, tag_id, source, now))

# ============== 标签别名（语义对齐）==============

def set_tag_aliases(tag_name, aliases):
    """设置标签的别名列表"""
    tag_id = ensure_tag(tag_name)
    now = datetime.now().isoformat()
    with get_db() as conn:
        c = conn.cursor()
        # 删除旧别名
        c.execute("DELETE FROM tag_aliases WHERE tag_id = ?", (tag_id,))
        # 批量插入新别名
        for alias in aliases:
            c.execute("""
                INSERT OR IGNORE INTO tag_aliases (tag_id, alias, created_at)
                VALUES (?, ?, ?)
            """, (tag_id, alias, now))

def get_all_tags_with_aliases():
    """获取所有标签及其别名"""
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT id, name FROM tags ORDER BY name")
        tags = []
        for row in c.fetchall():
            tid = row["id"]
            name = row["name"]
            c.execute("SELECT alias FROM tag_aliases WHERE tag_id = ?", (tid,))
            aliases = [r["alias"] for r in c.fetchall()]
            tags.append({"name": name, "aliases": aliases})
        return tags

def align_tag(tag_name):
    """
    语义对齐：给定一个标签名，返回标准标签
    优先：精确匹配 → 别名匹配
    如果都没匹配到，返回原标签（作为新标签加入）
    """
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
    init_db()
