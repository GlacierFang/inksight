#!/usr/bin/env python3
"""
OpenClaw Media Gallery - SQLite 数据库版
"""

import os
import json
import mimetypes
import requests
from datetime import datetime
from pathlib import Path
from flask import Flask, render_template, jsonify, request, send_from_directory, abort, redirect

app = Flask(__name__)

sys_path_inserted = False
def ensure_db_import():
    global sys_path_inserted
    if not sys_path_inserted:
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        sys_path_inserted = True

MEDIA_BASE = Path("/home/open/.openclaw/media")
PHOTOS_DIR = MEDIA_BASE / "photos"
VIDEOS_DIR = MEDIA_BASE / "videos"
INBOUND_DIR = MEDIA_BASE / "inbound"
THUMBNAILS_DIR = MEDIA_BASE / ".thumbnails"
AI_WORKS_DIR = MEDIA_BASE / "ai_works"

PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv"}
DEFAULT_PAGE_SIZE = 30

# ============== 数据库导入 ==============

def db():
    ensure_db_import()
    from db import get_db as _get_db
    return _get_db()

# ============== 辅助函数 ==============

def get_file_meta(fpath):
    """读取 .meta 文件（保留，用于照片详情等需要完整信息的场景）"""
    meta_path = Path(str(fpath) + ".meta")
    if meta_path.exists():
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return {}

def get_photo_tags_from_db(photo_id):
    """从数据库获取照片标签"""
    ensure_db_import()
    from db import get_photo_tags
    return get_photo_tags(photo_id)

def scan_media_files(base_dir, media_type, tag_filter=None):
    """扫描媒体文件，返回文件信息列表（标签从数据库读取）"""
    ensure_db_import()
    from db import scan_photos_with_tags, upsert_photo

    files = []
    if not base_dir.exists():
        return files

    for date_dir in sorted(base_dir.iterdir(), reverse=True):
        if not date_dir.is_dir() or not date_dir.match("????-??-??"):
            continue
        for f in sorted(date_dir.iterdir()):
            if f.suffix.lower() not in (PHOTO_EXTS if media_type == "photo" else VIDEO_EXTS):
                continue

            # 确保数据库里有这条记录
            upsert_photo(
                photo_id=f.name,
                filepath=str(f),
                date=date_dir.name,
                timestamp=int(f.stat().st_mtime * 1000),
                source="未知",
                media_type=media_type
            )

            files.append({
                "name": f.name,
                "path": f,
                "date": date_dir.name,
                "timestamp": int(f.stat().st_mtime * 1000),
                "tags": [],  # 标签单独查
                "type": media_type,
            })
    return files

def find_media_by_name(filename):
    """根据文件名查找媒体信息"""
    ensure_db_import()
    from db import upsert_photo

    for base_dir, media_type in [(PHOTOS_DIR, "photo"), (VIDEOS_DIR, "video")]:
        if not base_dir.exists():
            continue
        for date_dir in base_dir.iterdir():
            if not date_dir.is_dir():
                continue
            fpath = date_dir / filename
            if fpath.exists():
                # 确保入库
                upsert_photo(
                    photo_id=filename,
                    filepath=str(fpath),
                    date=date_dir.name,
                    timestamp=int(fpath.stat().st_mtime * 1000),
                    source="未知",
                    media_type=media_type
                )
                tags = get_photo_tags_from_db(filename)
                return {
                    "name": filename,
                    "path": fpath,
                    "date": date_dir.name,
                    "timestamp": int(fpath.stat().st_mtime * 1000),
                    "tags": tags,
                    "type": media_type,
                }
    return None

def get_adjacent_photos(filename, current_type):
    """获取当前照片的前一张和下一张"""
    all_files = scan_media_files(PHOTOS_DIR if current_type == "photo" else VIDEOS_DIR, current_type)
    positions = {f["name"]: i for i, f in enumerate(all_files)}
    idx = positions.get(filename, -1)
    prev_file = all_files[idx - 1] if idx > 0 else None
    next_file = all_files[idx + 1] if idx < len(all_files) - 1 else None
    return prev_file, next_file

# ============== 页面路由 ==============

@app.route("/gallery")
@app.route("/gallery/")
def gallery_index():
    return render_template("gallery.html")

@app.route("/gallery/photo/<path:filename>")
def photo_detail(filename):
    media = find_media_by_name(filename)
    if not media:
        abort(404)
    prev_file, next_file = get_adjacent_photos(filename, media["type"])
    return render_template("photo.html", photo=media, prev=prev_file, next=next_file)

@app.route("/gallery/video/<path:filename>")
def video_page(filename):
    media = find_media_by_name(filename)
    if not media:
        abort(404)
    prev_file, next_file = get_adjacent_photos(filename, media["type"])
    return render_template("photo.html", photo=media, prev=prev_file, next=next_file)

# ============== API: 媒体列表（分页）==============

@app.route("/gallery/api/media")
def api_media():
    ensure_db_import()
    from db import scan_photos_with_tags, upsert_photo

    page = max(1, request.args.get("page", 1, type=int))
    page_size = min(100, max(10, request.args.get("limit", DEFAULT_PAGE_SIZE, type=int)))
    date_filter = request.args.get("date")
    tag_filter = request.args.get("tag")
    media_type = request.args.get("type", "all")

    # 从数据库获取（标签在DB里）
    photos = scan_photos_with_tags(tag_filter) if media_type in ["all", "photo"] else []
    videos = [] if media_type in ["all", "photo"] else scan_photos_with_tags(tag_filter) if media_type == "video" else []

    # media_type = "video" 时只取 videos
    if media_type == "video":
        photos = []

    all_media = []
    for photo_row, tags in photos + videos:
        all_media.append({
            "id": photo_row["id"],
            "name": photo_row["id"],
            "date": photo_row["date"],
            "timestamp": photo_row["timestamp"],
            "tags": tags,
            "type": photo_row["media_type"],
            "thumb": None,  # 缩略图后面补
        })

    # 日期筛选
    if date_filter:
        all_media = [m for m in all_media if m["date"] == date_filter]

    all_media.sort(key=lambda x: x.get("timestamp") or 0, reverse=True)

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
        m["thumb"] = thumb_url
        result.append(m)

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
    ensure_db_import()
    from db import get_all_tags
    return jsonify({"tags": get_all_tags()})

# ============== API: 照片详情 ==============

@app.route("/gallery/api/photo/<path:filename>")
def api_photo(filename):
    ensure_db_import()
    from db import upsert_photo

    media = find_media_by_name(filename)
    if not media:
        abort(404)

    # 确保入库
    upsert_photo(
        photo_id=filename,
        filepath=str(media["path"]),
        date=media["date"],
        timestamp=media["timestamp"],
        source="未知",
        media_type=media["type"]
    )

    prev_file, next_file = get_adjacent_photos(filename, media["type"])
    date_str = media["date"]
    stem = Path(filename).stem
    thumb_url = f"/gallery/thumb/{media['type']}/{date_str}/{stem}.jpg"

    return jsonify({
        "name": filename,
        "date": date_str,
        "timestamp": media["timestamp"],
        "tags": get_photo_tags_from_db(filename),
        "type": media["type"],
        "thumb": thumb_url,
        "path": f"/gallery/media/{media['type']}/{date_str}/{filename}",
        "prev": prev_file["name"] if prev_file else None,
        "next": next_file["name"] if next_file else None,
    })

# ============== API: 删除 ==============

@app.route("/gallery/api/delete/<path:filename>", methods=["DELETE"])
def api_delete(filename):
    ensure_db_import()
    from db import get_db

    for base_dir in [PHOTOS_DIR, VIDEOS_DIR]:
        if not base_dir.exists():
            continue
        for date_dir in base_dir.iterdir():
            if not date_dir.is_dir():
                continue
            fpath = date_dir / filename
            if fpath.exists():
                fpath.unlink()
                thumb_path = THUMBNAILS_DIR / date_dir.name / f"{Path(filename).stem}.jpg"
                if thumb_path.exists():
                    thumb_path.unlink()
                meta_path = Path(str(fpath) + ".meta")
                if meta_path.exists():
                    meta_path.unlink()
                # 从数据库删除
                with get_db() as conn:
                    c = conn.cursor()
                    c.execute("DELETE FROM photo_tags WHERE photo_id = ?", (filename,))
                    c.execute("DELETE FROM photos WHERE id = ?", (filename,))
                return jsonify({"success": True})
    return jsonify({"error": "文件不存在"}), 404

# ============== API: 上传 ==============

@app.route("/gallery/upload", methods=["POST"])
def upload_file():
    ensure_db_import()
    from db import upsert_photo

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

    if dest.exists():
        import uuid
        stem = Path(filename).stem
        dest = INBOUND_DIR / f"{stem}_{uuid.uuid4().hex[:8]}{ext}"

    file.save(str(dest))

    # 写入 .meta 文件
    meta = {"timestamp": int(datetime.now().timestamp() * 1000)}
    with open(str(dest) + ".meta", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)

    # 写入数据库
    media_type = "photo" if ext in PHOTO_EXTS else "video"
    upsert_photo(
        photo_id=dest.name,
        filepath=str(dest),
        date=datetime.now().strftime("%Y-%m-%d"),
        timestamp=int(datetime.now().timestamp() * 1000),
        source="网页上传",
        media_type=media_type
    )

    return jsonify({"success": True, "filename": dest.name})

# ============== API: 照片标签（数据库版）==============

@app.route("/gallery/api/photo-tags/<path:filename>", methods=["GET"])
def api_get_photo_tags(filename):
    ensure_db_import()
    if not find_media_by_name(filename):
        abort(404)
    return jsonify({"tags": get_photo_tags_from_db(filename)})

@app.route("/gallery/api/photo-tags/<path:filename>", methods=["PUT"])
def api_update_photo_tags(filename):
    ensure_db_import()
    from db import set_photo_tags

    media = find_media_by_name(filename)
    if not media:
        abort(404)
    req = request.get_json()
    new_tags = req.get("tags", [])
    set_photo_tags(filename, new_tags, source='user')
    return jsonify({"success": True, "tags": new_tags})

@app.route("/gallery/api/photo-tags/<path:filename>", methods=["DELETE"])
def api_delete_photo_tag(filename):
    ensure_db_import()
    from db import remove_tag_from_photo

    media = find_media_by_name(filename)
    if not media:
        abort(404)
    tag_name = request.args.get("tag") or (request.get_json() or {}).get("tag")
    if not tag_name:
        return jsonify({"error": "缺少 tag 参数"}), 400
    remove_tag_from_photo(filename, tag_name)
    return jsonify({"success": True})

# ============== API: 标准标签（数据库版）==============

@app.route("/gallery/api/canonical-tags")
def api_canonical_tags():
    ensure_db_import()
    from db import get_all_tags_with_aliases
    return jsonify({"tags": get_all_tags_with_aliases()})

@app.route("/gallery/api/canonical-tags", methods=["POST"])
def api_add_canonical_tag():
    ensure_db_import()
    from db import ensure_tag
    req = request.get_json()
    name = req.get("name", "").strip()
    if not name:
        return jsonify({"error": "标签名不能为空"}), 400
    aliases = req.get("aliases", [])
    from db import set_tag_aliases
    ensure_tag(name)
    if aliases:
        set_tag_aliases(name, aliases)
    return jsonify({"success": True})

@app.route("/gallery/api/canonical-tags/<tag_name>", methods=["PUT"])
def api_update_canonical_tag(tag_name):
    ensure_db_import()
    from db import ensure_tag, set_tag_aliases
    req = request.get_json()
    new_name = req.get("name", "").strip()
    ensure_tag(tag_name)
    ensure_tag(new_name if new_name else tag_name)
    aliases = req.get("aliases", [])
    set_tag_aliases(new_name if new_name else tag_name, aliases)
    return jsonify({"success": True})

@app.route("/gallery/api/canonical-tags/<tag_name>", methods=["DELETE"])
def api_delete_canonical_tag(tag_name):
    ensure_db_import()
    from db import get_db
    with get_db() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM tag_aliases WHERE tag_id IN (SELECT id FROM tags WHERE name = ?)", (tag_name,))
        c.execute("DELETE FROM tags WHERE name = ?", (tag_name,))
    return jsonify({"success": True})

# ============== AI 风格生成 ==============

@app.route("/gallery/api/ai-styles")
def api_ai_styles():
    """支持的 AI 风格列表"""
    styles = [
        {"id": "song_gongben", "name": "宋代工笔画", "prompt": "Song Dynasty Gongbi painting style, meticulous brushwork, classical Chinese court painting, elegant and refined"},
        {"id": "ink_wash", "name": "水墨画", "prompt": "Traditional Chinese ink wash painting, xieyi style, poetic and serene, minimalist strokes"},
        {"id": "ai_restore", "name": "AI复原图", "prompt": "Historical restoration of ancient Chinese architecture/artifacts, archaeological reconstruction, realistic and detailed"},
        {"id": "green_mountain", "name": "青绿山水", "prompt": "Chinese landscape painting with azurite and malachite pigments, guqin and clouds atmosphere, classical Chinese blue-green landscape"},
        {"id": "dunhuang", "name": "敦煌壁画", "prompt": "Dunhuang cave fresco, Buddhist art, mineral pigments, ancient silk road aesthetic"},
        {"id": "gufeng_figure", "name": "古风人物", "prompt": "Ancient Chinese beauty in traditional Hanfu, classic Chinese painting style, elegant and dignified"},
        {"id": "ukiyo_e", "name": "浮世绘", "prompt": "Japanese ukiyo-e woodblock print style, flat colors, elegant composition, traditional Japanese aesthetic"},
        {"id": "cyberpunk", "name": "赛博朋克", "prompt": "Cyberpunk style, neon lights, futuristic Chinese cityscape, glowing neon signs, dark atmosphere with vibrant colors"},
        {"id": "impressionist", "name": "印象派", "prompt": "Impressionist oil painting style, soft light and color, loose brushwork, Monet or Renoir aesthetic"},
        {"id": "oil_painting", "name": "油画质感", "prompt": "Classical Western oil painting, rich textures and layers, realistic chiaroscuro, Renaissance masterwork"},
    ]
    return jsonify({"styles": styles})

@app.route("/gallery/api/ai-generate", methods=["POST"])
def api_ai_generate():
    """调用 MiniMax image-01 图生图，生成 AI 风格图片"""
    import base64
    import urllib.request
    data = request.get_json()
    photo_filename = data.get("photo_filename")   # e.g. "2026-04-01/uuid.jpg"
    style_id = data.get("style_id")
    style_prompt = data.get("style_prompt", "")

    if not photo_filename or not style_id:
        return jsonify({"error": "缺少参数"}), 400

    # 读取照片完整路径
    photo_path = PHOTOS_DIR / photo_filename
    if not photo_path.exists():
        return jsonify({"error": f"照片不存在: {photo_filename}"}), 404

    # 读取 API Key
    api_key = None
    auth_file = Path.home() / ".openclaw" / "agents" / "main" / "agent" / "auth-profiles.json"
    if auth_file.exists():
        with open(auth_file) as f:
            profiles = json.load(f).get("profiles", {})
        for p in profiles.values():
            access = p.get("access", "")
            if access.startswith("sk-"):
                api_key = access
                break
    if not api_key:
        return jsonify({"error": "未找到 MiniMax API Key"}), 500

    # 读取照片为 base64
    with open(photo_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()

    # 调用 MiniMax image-01 图生图
    req_body = {
        "model": "image-01",
        "prompt": style_prompt,
        "image_file": f"data:image/jpeg;base64,{img_b64}",
        "n": 1,
        "aspect_ratio": "1:1",
    }

    try:
        req = urllib.request.Request(
            "https://api.minimaxi.com/v1/image_generation",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            data=json.dumps(req_body).encode(),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read())
    except Exception as e:
        return jsonify({"error": f"MiniMax API 调用失败: {str(e)}"}), 500

    base_resp = result.get("base_resp", {})
    if base_resp.get("status_code") != 0:
        return jsonify({"error": base_resp.get("status_msg", "API错误")}), 500

    image_data = result.get("data", {})
    image_urls = image_data.get("image_urls", [])
    if not image_urls:
        return jsonify({"error": "未返回图片"}), 500

    image_url = image_urls[0]

    # 下载图片并保存到 ai_works/
    today = datetime.now().strftime("%Y-%m-%d")
    work_dir = AI_WORKS_DIR / today
    work_dir.mkdir(parents=True, exist_ok=True)

    import uuid
    output_filename = f"{uuid.uuid4().hex}.jpg"
    output_path = work_dir / output_filename

    try:
        with urllib.request.urlopen(image_url, timeout=60) as resp:
            with open(output_path, "wb") as out:
                out.write(resp.read())
    except Exception as e:
        return jsonify({"error": f"图片下载失败: {str(e)}"}), 500

    # 写入元数据
    meta = {
        "original": str(photo_filename),
        "style_id": style_id,
        "style_name": data.get("style_name", style_id),
        "prompt": style_prompt,
        "generated_at": datetime.now().isoformat(),
        "image_url": image_url,
    }
    meta_path = output_path.with_suffix(".jpg.meta")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    # 生成缩略图
    try:
        from PIL import Image
        thumb_dir = THUMBNAILS_DIR / "ai_work" / today
        thumb_dir.mkdir(parents=True, exist_ok=True)
        thumb_path = thumb_dir / f"{output_filename}.jpg"
        with Image.open(output_path) as img:
            img.thumbnail((400, 400))
            img.convert("RGB").save(thumb_path, "JPEG", quality=85)
    except Exception:
        pass  # 缩略图失败不影响主流程

    return jsonify({
        "success": True,
        "filename": str(output_path.relative_to(MEDIA_BASE)),
        "url": f"/gallery/media/ai_work/{today}/{output_filename}",
        "thumb_url": f"/gallery/thumb/ai_work/{today}/{output_filename}.jpg",
        "style_name": data.get("style_name", style_id),
    })

@app.route("/gallery/api/ai-works")
def api_ai_works():
    """列出所有 AI 作品"""
    if not AI_WORKS_DIR.exists():
        return jsonify({"works": []})

    works = []
    for date_dir in sorted(AI_WORKS_DIR.iterdir(), reverse=True):
        if not date_dir.is_dir():
            continue
        for f in sorted(date_dir.iterdir(), reverse=True):
            if f.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
                continue
            meta = {}
            meta_path = f.with_suffix(".jpg.meta")
            if meta_path.exists():
                with open(meta_path, encoding="utf-8") as mf:
                    meta = json.load(mf)
            thumb_path = THUMBNAILS_DIR / "ai_work" / date_dir.name / f"{f.stem}.jpg"
            works.append({
                "filename": str(f.relative_to(MEDIA_BASE)),
                "url": f"/gallery/media/ai_work/{date_dir.name}/{f.name}",
                "thumb_url": f"/gallery/thumb/ai_work/{date_dir.name}/{f.stem}.jpg" if thumb_path.exists() else None,
                "generated_at": meta.get("generated_at", ""),
                "style_name": meta.get("style_name", ""),
                "original": meta.get("original", ""),
            })
    return jsonify({"works": works})

@app.route("/gallery/ai-works")
def gallery_ai_works():
    """AI 作品集页面"""
    return render_template("ai_works.html")

# ============== 静态文件服务 ==============

@app.route("/gallery/media/<media_type>/<path:filename>")
def media_file(media_type, filename):
    if media_type == "photo":
        return send_from_directory(PHOTOS_DIR, filename)
    elif media_type == "video":
        return send_from_directory(VIDEOS_DIR, filename)
    elif media_type == "ai_work":
        return send_from_directory(AI_WORKS_DIR, filename)
    abort(404)

@app.route("/gallery/thumb/<media_type>/<path:filename>")
def thumb_file(media_type, filename):
    return send_from_directory(THUMBNAILS_DIR, filename)

@app.route("/gallery/static/<path:filename>")
def static_files(filename):
    return send_from_directory("/home/open/.openclaw/workspace/skills/gallery/static", filename)

@app.route("/gallery/api/ai-send-feishu", methods=["POST"])
def api_ai_send_feishu():
    """将 AI 作品发送到飞书"""
    import base64
    data = request.get_json()
    filename = data.get("filename")  # e.g. "ai_works/2026-04-02/xxx.jpg"
    if not filename:
        return jsonify({"error": "缺少 filename"}), 400

    file_path = MEDIA_BASE / filename
    if not file_path.exists():
        return jsonify({"error": "文件不存在"}), 404

    FEISHU_APP_ID = "cli_a9259b2e6738dcd6"
    FEISHU_APP_SECRET = "OuFdqJk0K1XLZAjUESV28bM6cPg0JeTQ"
    FEISHU_USER_OPEN_ID = "ou_d1b4e7815a063128f882df928effe864"

    # 获取飞书 token
    token_resp = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET},
        timeout=10
    )
    token = token_resp.json().get("tenant_access_token", "")
    if not token:
        return jsonify({"error": "获取飞书 token 失败"}), 500

    # 上传图片到飞书（作为 image 消息）
    with open(file_path, "rb") as f:
        img_data = f.read()
    import io
    upload_resp = requests.post(
        "https://open.feishu.cn/open-apis/im/v1/images",
        headers={"Authorization": f"Bearer {token}"},
        data={"image_type": "message"},
        files={"image": (file_path.name, img_data, "image/jpeg")},
        timeout=30,
    )
    upload_json = upload_resp.json()
    image_key = upload_json.get("data", {}).get("image_key", "")
    if not image_key:
        return jsonify({"error": f"图片上传失败: {upload_json}"}), 500

    # 发送图片消息
    msg_resp = requests.post(
        "https://open.feishu.cn/open-apis/im/v1/messages",
        params={"receive_id_type": "open_id"},
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={
            "receive_id": FEISHU_USER_OPEN_ID,
            "msg_type": "image",
            "content": json.dumps({"image_key": image_key}),
        },
        timeout=10,
    )
    msg_json = msg_resp.json()
    return jsonify({"success": True, "code": msg_json.get("code")})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=18790, debug=False)
