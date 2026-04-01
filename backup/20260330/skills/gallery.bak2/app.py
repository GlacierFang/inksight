#!/usr/bin/env python3
"""
OpenClaw Media Gallery - Multi-page version
"""

import os
import json
import mimetypes
from datetime import datetime
from pathlib import Path
from flask import Flask, render_template, jsonify, request, send_from_directory, abort, redirect

app = Flask(__name__)

MEDIA_BASE = Path("/home/open/.openclaw/media")
PHOTOS_DIR = MEDIA_BASE / "photos"
VIDEOS_DIR = MEDIA_BASE / "videos"
INBOUND_DIR = MEDIA_BASE / "inbound"
THUMBNAILS_DIR = MEDIA_BASE / ".thumbnails"
CANONICAL_TAGS_FILE = MEDIA_BASE / "canonical_tags.json"

PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv"}
DEFAULT_PAGE_SIZE = 30

# ============== 辅助函数 ==============

def load_canonical_tags():
    if CANONICAL_TAGS_FILE.exists():
        with open(CANONICAL_TAGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"tags": {}}

def save_canonical_tags(data):
    with open(CANONICAL_TAGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_file_meta(fpath):
    meta_path = Path(str(fpath) + ".meta")
    if meta_path.exists():
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return {}

def scan_media_files(base_dir, media_type, tag_filter=None):
    """扫描媒体文件，返回文件信息列表"""
    files = []
    if not base_dir.exists():
        return files

    for date_dir in sorted(base_dir.iterdir(), reverse=True):
        if not date_dir.is_dir() or not date_dir.match("????-??-??"):
            continue
        for f in sorted(date_dir.iterdir()):
            if f.suffix.lower() not in (PHOTO_EXTS if media_type == "photo" else VIDEO_EXTS):
                continue

            meta = get_file_meta(f)
            tags = meta.get("tags") or meta.get("ai_tags") or []
            if tag_filter and tag_filter not in tags:
                continue

            # 获取时间戳
            timestamp = meta.get("timestamp") or int(f.stat().st_mtime * 1000)

            files.append({
                "name": f.name,
                "path": f,
                "date": date_dir.name,
                "timestamp": timestamp,
                "tags": tags,
                "type": media_type,
            })
    return files

def find_media_by_name(filename):
    """根据文件名查找媒体信息"""
    for base_dir, media_type in [(PHOTOS_DIR, "photo"), (VIDEOS_DIR, "video")]:
        if not base_dir.exists():
            continue
        for date_dir in base_dir.iterdir():
            if not date_dir.is_dir():
                continue
            fpath = date_dir / filename
            if fpath.exists():
                meta = get_file_meta(fpath)
                timestamp = meta.get("timestamp") or int(fpath.stat().st_mtime * 1000)
                return {
                    "name": filename,
                    "path": fpath,
                    "date": date_dir.name,
                    "timestamp": timestamp,
                    "tags": meta.get("tags") or meta.get("ai_tags") or [],
                    "type": media_type,
                }
    return None

def get_adjacent_photos(filename, current_type):
    """获取当前照片的前一张和下一张"""
    base_dir = PHOTOS_DIR if current_type == "photo" else VIDEOS_DIR
    all_files = scan_media_files(base_dir, current_type)
    positions = {f["name"]: i for i, f in enumerate(all_files)}
    idx = positions.get(filename, -1)
    prev_file = all_files[idx - 1] if idx > 0 else None
    next_file = all_files[idx + 1] if idx < len(all_files) - 1 else None
    return prev_file, next_file

# ============== 页面路由 ==============

@app.route("/gallery")
@app.route("/gallery/")
def gallery_index():
    """相册列表页"""
    return render_template("gallery.html")

@app.route("/gallery/photo/<path:filename>")
def photo_detail(filename):
    """单张照片详情页"""
    media = find_media_by_name(filename)
    if not media:
        abort(404)
    prev_file, next_file = get_adjacent_photos(filename, media["type"])
    return render_template("photo.html", photo=media, prev=prev_file, next=next_file)

@app.route("/gallery/video/<path:filename>")
def video_page(filename):
    """视频页面（暂用详情页模板）"""
    media = find_media_by_name(filename)
    if not media:
        abort(404)
    prev_file, next_file = get_adjacent_photos(filename, media["type"])
    return render_template("photo.html", photo=media, prev=prev_file, next=next_file)

# ============== API: 媒体列表（分页）==============

@app.route("/gallery/api/media")
def api_media():
    page = max(1, request.args.get("page", 1, type=int))
    page_size = min(100, max(10, request.args.get("limit", DEFAULT_PAGE_SIZE, type=int)))
    date_filter = request.args.get("date")
    tag_filter = request.args.get("tag")
    media_type = request.args.get("type", "all")

    photos = scan_media_files(PHOTOS_DIR, "photo", tag_filter) if media_type in ["all", "photo"] else []
    videos = scan_media_files(VIDEOS_DIR, "video", tag_filter) if media_type in ["all", "video"] else []
    all_media = photos + videos
    all_media.sort(key=lambda x: x.get("timestamp") or 0, reverse=True)

    # 日期筛选
    if date_filter:
        all_media = [m for m in all_media if m["date"] == date_filter]

    total = len(all_media)
    total_pages = (total + page_size - 1) // page_size
    start = (page - 1) * page_size
    end = start + page_size
    page_items = all_media[start:end]

    # 生成缩略图URL
    result = []
    for m in page_items:
        date_str = m["date"]
        name = m["name"]
        stem = Path(name).stem
        thumb_path = THUMBNAILS_DIR / date_str / f"{stem}.jpg"
        thumb_url = f"/gallery/thumb/{m['type']}/{date_str}/{stem}.jpg" if thumb_path.exists() else None
        result.append({
            "id": name,
            "name": name,
            "date": date_str,
            "timestamp": m["timestamp"],
            "tags": m["tags"],
            "type": m["type"],
            "thumb": thumb_url,
        })

    return jsonify({
        "media": result,
        "total": total,
        "page": page,
        "pageSize": page_size,
        "totalPages": total_pages,
        "hasNext": page < total_pages,
        "hasPrev": page > 1,
    })

# ============== API: 筛选器数据 ==============

@app.route("/gallery/api/dates")
def api_dates():
    dates = set()
    for base_dir in [PHOTOS_DIR, VIDEOS_DIR]:
        if not base_dir.exists():
            continue
        for date_dir in base_dir.iterdir():
            if date_dir.is_dir() and date_dir.match("????-??-??"):
                dates.add(date_dir.name)
    return jsonify({"dates": sorted(dates, reverse=True)})

@app.route("/gallery/api/stats")
def api_stats():
    """获取存储统计"""
    import shutil as sh
    try:
        total, used, free = 0, 0, 0
        for path in ["/home", "/"]:
            try:
                s = sh.disk_usage(path)
                total, used, free = s.total, s.used, s.free
                break
            except: pass

        def size_of(p):
            try: return sum(f.stat().st_size for f in Path(p).rglob('*') if f.is_file())
            except: return 0

        photos_gb = round(size_of(str(PHOTOS_DIR)) / (1024**3), 2)
        videos_gb = round(size_of(str(VIDEOS_DIR)) / (1024**3), 2)
        inbound_gb = round(size_of(str(INBOUND_DIR)) / (1024**3), 2)
        media_gb = round((photos_gb + videos_gb + inbound_gb), 2)
        library_gb = round(size_of("/home/open/.openclaw/workspace/library/books") / (1024**3), 2)

        return jsonify({
            "total_gb": round(total / (1024**3), 1),
            "used_gb": round(used / (1024**3), 1),
            "free_gb": round(free / (1024**3), 1),
            "percent": round(used / total * 100, 1),
            "photos_gb": photos_gb,
            "videos_gb": videos_gb,
            "inbound_gb": inbound_gb,
            "media_gb": media_gb,
            "library_gb": library_gb,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/gallery/api/tags")
def api_tags():
    tags = set()
    for base_dir in [PHOTOS_DIR, VIDEOS_DIR]:
        if not base_dir.exists():
            continue
        for date_dir in base_dir.iterdir():
            if not date_dir.is_dir():
                continue
            for f in date_dir.iterdir():
                if f.suffix.lower() not in (PHOTO_EXTS | VIDEO_EXTS):
                    continue
                meta = get_file_meta(f)
                tags.update(meta.get("tags") or meta.get("ai_tags") or [])
    return jsonify({"tags": sorted(tags)})

# ============== API: 照片详情 ==============

@app.route("/gallery/api/photo/<path:filename>")
def api_photo(filename):
    media = find_media_by_name(filename)
    if not media:
        abort(404)
    prev_file, next_file = get_adjacent_photos(filename, media["type"])

    date_str = media["date"]
    stem = Path(filename).stem
    thumb_url = f"/gallery/thumb/{media['type']}/{date_str}/{stem}.jpg"

    return jsonify({
        "name": filename,
        "date": date_str,
        "timestamp": media["timestamp"],
        "tags": media["tags"],
        "type": media["type"],
        "thumb": thumb_url,
        "path": f"/gallery/media/{media['type']}/{date_str}/{filename}",
        "prev": prev_file["name"] if prev_file else None,
        "next": next_file["name"] if next_file else None,
    })

# ============== API: 删除 ==============

@app.route("/gallery/api/delete/<path:filename>", methods=["DELETE"])
def api_delete(filename):
    for base_dir in [PHOTOS_DIR, VIDEOS_DIR]:
        if not base_dir.exists():
            continue
        for date_dir in base_dir.iterdir():
            if not date_dir.is_dir():
                continue
            fpath = date_dir / filename
            if fpath.exists():
                fpath.unlink()
                # 删除缩略图
                thumb_path = THUMBNAILS_DIR / date_dir.name / f"{Path(filename).stem}.jpg"
                if thumb_path.exists():
                    thumb_path.unlink()
                # 删除 meta 文件
                meta_path = Path(str(fpath) + ".meta")
                if meta_path.exists():
                    meta_path.unlink()
                return jsonify({"success": True})
    return jsonify({"error": "文件不存在"}), 404

# ============== API: 上传 ==============

@app.route("/gallery/upload", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return jsonify({"error": "No file"}), 400
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    filename = file.filename
    ext = Path(filename).suffix.lower()
    if ext not in (PHOTO_EXTS | VIDEO_EXTS):
        return jsonify({"error": "不支持的文件类型"}), 400

    INBOUND_DIR.mkdir(parents=True, exist_ok=True)
    dest = INBOUND_DIR / filename

    # 避免覆盖
    if dest.exists():
        import uuid
        stem = Path(filename).stem
        dest = INBOUND_DIR / f"{stem}_{uuid.uuid4().hex[:8]}{ext}"

    file.save(str(dest))

    # 记录基础元信息（无来源标签）
    meta = {"timestamp": int(datetime.now().timestamp() * 1000)}
    with open(str(dest) + ".meta", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)

    return jsonify({"success": True, "filename": dest.name})

# ============== API: 照片标签 ==============

@app.route("/gallery/api/photo-tags/<path:filename>", methods=["GET"])
def api_get_photo_tags(filename):
    media = find_media_by_name(filename)
    if not media:
        abort(404)
    return jsonify({"tags": media["tags"]})

@app.route("/gallery/api/photo-tags/<path:filename>", methods=["PUT"])
def api_update_photo_tags(filename):
    media = find_media_by_name(filename)
    if not media:
        abort(404)
    req = request.get_json()
    new_tags = req.get("tags", [])

    # 读取现有 meta
    meta_path = Path(str(media["path"]) + ".meta")
    meta = {}
    if meta_path.exists():
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

    meta["tags"] = new_tags
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    return jsonify({"success": True, "tags": new_tags})

# ============== API: 规范标签（标签管理）==============

@app.route("/gallery/api/canonical-tags")
def api_canonical_tags():
    data = load_canonical_tags()
    tags = []
    for name, info in data.get("tags", {}).items():
        tags.append({"name": name, **info})
    return jsonify({"tags": tags})

@app.route("/gallery/api/canonical-tags", methods=["POST"])
def api_add_canonical_tag():
    req = request.get_json()
    name = req.get("name", "").strip()
    if not name:
        return jsonify({"error": "标签名不能为空"}), 400
    data = load_canonical_tags()
    if name in data["tags"]:
        return jsonify({"error": "标签已存在"}), 400
    data["tags"][name] = {"aliases": req.get("aliases", []), "category": req.get("category", "")}
    save_canonical_tags(data)
    return jsonify({"success": True})

@app.route("/gallery/api/canonical-tags/<tag_name>", methods=["PUT"])
def api_update_canonical_tag(tag_name):
    req = request.get_json()
    new_name = req.get("name", "").strip()
    data = load_canonical_tags()
    if tag_name not in data["tags"]:
        return jsonify({"error": "标签不存在"}), 404
    info = data["tags"].pop(tag_name)
    if new_name and new_name != tag_name:
        if new_name in data["tags"]:
            return jsonify({"error": "标签名已存在"}), 400
        info["name"] = new_name
    info["aliases"] = req.get("aliases", info.get("aliases", []))
    info["category"] = req.get("category", info.get("category", ""))
    data["tags"][new_name if new_name else tag_name] = info
    save_canonical_tags(data)
    return jsonify({"success": True})

@app.route("/gallery/api/canonical-tags/<tag_name>", methods=["DELETE"])
def api_delete_canonical_tag(tag_name):
    data = load_canonical_tags()
    if tag_name not in data["tags"]:
        return jsonify({"error": "标签不存在"}), 404
    del data["tags"][tag_name]
    save_canonical_tags(data)
    return jsonify({"success": True})

# ============== 静态文件服务 ==============

@app.route("/gallery/media/<media_type>/<path:filename>")
def media_file(media_type, filename):
    if media_type == "photo":
        return send_from_directory(PHOTOS_DIR, filename)
    elif media_type == "video":
        return send_from_directory(VIDEOS_DIR, filename)
    abort(404)

@app.route("/gallery/thumb/<media_type>/<path:filename>")
def thumb_file(media_type, filename):
    return send_from_directory(THUMBNAILS_DIR, filename)

# ============== 静态文件（CSS/JS）==============

@app.route("/gallery/static/<path:filename>")
def static_files(filename):
    from flask import send_from_directory
    return send_from_directory("/home/open/.openclaw/workspace/skills/gallery/static", filename)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=18790, debug=False)
