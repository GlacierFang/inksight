#!/usr/bin/env python3
"""
个人电子书资料库
- 文档列表页 /library/
- 上传页 /library/upload
- 上传接口 /library/api/upload
- 文档列表接口 /library/api/books
- 文档详情接口 /library/api/book/<id>
- 删除接口 /library/api/book/<id>
"""

import os
import json
import uuid
import subprocess
import requests
from pathlib import Path
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_from_directory, abort

MINIMAX_API_KEY = "sk-cp-lBFxrch74HPvQ3If5W1q6-08P0emI2PWLMbGX9IlAE1cnx5x17CGs4HDOQmVmLJu6lExhMGbkbTMN5Kgi7WxP5qSpNUb50llKiRz6yDa9Tu6d3OK8R6RUK4"
MINIMAX_BASE_URL = "https://api.minimaxi.com/anthropic/v1/messages"
app = Flask(__name__, template_folder='templates', static_folder='static')
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024  # 200MB max

# 目录配置
BASE_DIR = Path("/home/open/.openclaw/workspace/library")
BOOKS_DIR = BASE_DIR / "books"
INDEX_FILE = Path("/home/open/.openclaw/workspace/memory/library-index.json")
THUMBNAILS_DIR = BASE_DIR / "covers"

BOOKS_DIR.mkdir(parents=True, exist_ok=True)
THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)

# 支持的格式
SUPPORTED_FORMATS = {".pdf", ".epub", ".txt", ".md", ".docx"}


def load_index():
    if INDEX_FILE.exists():
        try:
            with open(INDEX_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return {"documents": [], "updated_at": datetime.now().strftime("%Y-%m-%d")}


def save_index(data):
    data["updated_at"] = datetime.now().strftime("%Y-%m-%d")
    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_file_ext_info(filepath):
    """获取文件扩展名信息"""
    ext = filepath.suffix.lower()
    info = {
        "pdf": {"icon": "📄", "color": "#e74c3c"},
        "epub": {"icon": "📖", "color": "#27ae60"},
        "txt": {"icon": "📝", "color": "#3498db"},
        "md": {"icon": "📋", "color": "#9b59b6"},
        "docx": {"icon": "📃", "color": "#2980b9"},
    }
    return info.get(ext[1:], {"icon": "📁", "color": "#7f8c8d"})


def extract_text_preview(filepath, max_chars=500):
    """提取文本文档预览（用于AI分析）"""
    text = _extract_full_text_impl(filepath)
    return text[:max_chars]


def _extract_full_text_impl(filepath):
    """提取完整文本（内部实现）"""
    import zipfile, re
    if isinstance(filepath, str):
        filepath = Path(filepath)
    ext = filepath.suffix.lower()
    try:
        if ext in [".txt", ".md"]:
            for enc in ["utf-8", "gbk", "gb2312", "latin1"]:
                try:
                    with open(filepath, "r", encoding=enc) as f:
                        return f.read()
                except:
                    continue
        elif ext == ".pdf":
            result = subprocess.run(
                ["pdftotext", str(filepath), "-"],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode == 0:
                text = result.stdout
                text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
                return text
        elif ext == ".epub":
            try:
                with zipfile.ZipFile(filepath, "r") as z:
                    text_parts = []
                    for name in z.namelist():
                        if re.match(r'.*/(text|chapter|content)\d*\.x?html?$', name, re.I):
                            try:
                                html = z.read(name).decode("utf-8", errors="ignore")
                                text = re.sub(r'<[^>]+>', '', html)
                                text = re.sub(r'\s+', ' ', text).strip()
                                if text:
                                    text_parts.append(text)
                            except:
                                continue
                    return "\n".join(text_parts)
            except Exception as e:
                print(f"EPUB extract error: {e}")
    except Exception as e:
        print(f"Text extraction error: {e}")
    return ""


def _split_into_chunks(text):
    """将文本拆分为 chunks，优先按章节，否则按固定长度"""
    import re
    # 章节分割：第X章、一、二、三、Chapter X
    chapter_patterns = [
        r'^第[一二三四五六七八九十百千零\d]+[章节篇部卷集]\s*[\u4e00-\u9fa5a-zA-Z0-9]+',
        r'^Chapter\s+\d+',
        r'^\d+\.\s+[\u4e00-\u9fa5a-zA-Z]',
    ]

    # 先尝试章节分割
    lines = text.split('\n')
    chapters = []
    current = []

    for line in lines:
        is_chapter = False
        for pat in chapter_patterns:
            if re.match(pat, line.strip(), re.I):
                is_chapter = True
                break
        if is_chapter and current:
            chapters.append('\n'.join(current))
            current = []
        current.append(line)

    if len(chapters) > 1:
        # 按章节分割，去掉末尾空章节
        chunks = [c.strip() for c in chapters if c.strip() and len(c.strip()) > 100]
        if chunks:
            return chunks

    # 兜底：按 2000 字切块，500 字重叠
    CHUNK_SIZE = 2000
    OVERLAP = 500
    chunks = []
    start = 0
    while start < len(text):
        end = start + CHUNK_SIZE
        chunk = text[start:end]
        # 在句子边界切断（。！？；）
        if end < len(text):
            for sep in '。！？；':
                last_sep = chunk.rfind(sep)
                if last_sep > CHUNK_SIZE - 200:
                    chunk = chunk[:last_sep + 1]
                    end = start + last_sep + 1
                    break
        chunks.append(chunk.strip())
        start = end - OVERLAP
        if start <= 0:
            break

    valid = [c for c in chunks if len(c) > 50]
    if not valid and text.strip():
        # 文本很短但不为空，至少保留一个 chunk
        valid = [text.strip()]
    return valid


# ============== 页面路由 ==============

@app.route("/library/")
def index():
    """资料库首页"""
    return render_template("index.html")


@app.route("/library/upload")
def upload_page():
    """上传页面"""
    return render_template("upload.html")

@app.route("/library/book/<doc_id>")
def book_page(doc_id):
    """书籍详情页"""
    return render_template("book.html")

@app.route("/library/static/<path:filename>")
def serve_static(filename):
    from flask import send_from_directory
    return send_from_directory(app.static_folder, filename)

@app.route("/library/reader/<doc_id>")
def reader_page(doc_id):
    return render_template("reader.html", doc_id=doc_id)

@app.route("/library/network")
def network_page():
    return render_template("network.html")
    """EPUB阅读器"""
    return render_template("reader.html")


# ============== API 路由 ==============

@app.route("/library/api/upload", methods=["POST"])
def api_upload():
    """上传文档"""
    if "file" not in request.files:
        return jsonify({"error": "没有文件"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "文件名为空"}), 400

    filename = file.filename
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_FORMATS:
        return jsonify({"error": f"不支持的格式，支持：{', '.join(SUPPORTED_FORMATS)}"}), 400

    # 生成唯一ID
    doc_id = uuid.uuid4().hex[:8]
    safe_filename = f"{doc_id}_{filename}"
    dest_path = BOOKS_DIR / safe_filename

    file.save(str(dest_path))
    file_size = dest_path.stat().st_size

    # 提取预览
    preview = extract_text_preview(dest_path)

    # 写入索引
    index = load_index()
    doc_entry = {
        "id": doc_id,
        "original_name": filename,
        "stored_name": safe_filename,
        "filepath": str(dest_path),
        "format": ext[1:],
        "size_mb": round(file_size / (1024 * 1024), 2),
        "uploaded_at": datetime.now().isoformat(),
        "tags": [],
        "category": "待分类",
        "summary": "",
        "preview": preview[:200],
        "status": "uploaded",  # uploaded -> classifying -> indexed
        "chunk_status": "pending",  # pending -> processing -> ready
        "knowledge_card": None
    }
    index["documents"].insert(0, doc_entry)
    save_index(index)

    # 触发后台分类
    import threading
    def classify_async(doc_id):
        from pathlib import Path
        import time
        time.sleep(2)  # 等待文件稳定
        try:
            classify_document(doc_id)
        except Exception as e:
            print(f"Classification error for {doc_id}: {e}")

    threading.Thread(target=classify_async, args=(doc_id,), daemon=True).start()

    return jsonify({
        "success": True,
        "document": {
            "id": doc_id,
            "original_name": filename,
            "format": ext[1:],
            "size_mb": doc_entry["size_mb"],
            "status": "uploaded"
        }
    })


@app.route("/library/api/books")
def api_books():
    """获取文档列表"""
    index = load_index()
    category = request.args.get("category")
    search = request.args.get("search", "").strip()

    docs = index.get("documents", [])

    # 过滤
    if category and category != "全部":
        docs = [d for d in docs if d.get("category") == category]
    if search:
        docs = [d for d in docs if search.lower() in d.get("original_name", "").lower()]

    tag_filter = request.args.get("tag")
    if tag_filter:
        docs = [d for d in docs if tag_filter in d.get("tags", [])]

    # 按上传时间倒序
    docs.sort(key=lambda x: x.get("uploaded_at", ""), reverse=True)

    # 返回简化信息
    result = []
    for d in docs:
        ext_info = get_file_ext_info(Path(d["stored_name"]))
        result.append({
            "id": d["id"],
            "original_name": d["original_name"],
            "format": d["format"],
            "format_icon": ext_info["icon"],
            "format_color": ext_info["color"],
            "size_mb": d["size_mb"],
            "uploaded_at": d["uploaded_at"][:10],
            "category": d.get("category", "待分类"),
            "status": d.get("status", "unknown"),
            "chunk_status": d.get("chunk_status", "pending"),
            "chunk_count": d.get("chunk_count", 0),
            "tags": d.get("tags", []),
            "preview": d.get("preview", "")[:100],
        })

    return jsonify({"documents": result, "total": len(result)})


@app.route("/library/api/book/<doc_id>")
def api_book(doc_id):
    """获取单个文档详情"""
    index = load_index()
    for d in index["documents"]:
        if d["id"] == doc_id:
            ext_info = get_file_ext_info(Path(d["stored_name"]))
            return jsonify({
                "id": d["id"],
                "original_name": d["original_name"],
                "format": d["format"],
                "format_icon": ext_info["icon"],
                "format_color": ext_info["color"],
                "size_mb": d["size_mb"],
                "uploaded_at": d["uploaded_at"][:10],
                "category": d.get("category", "待分类"),
                "status": d.get("status", "unknown"),
                "chunk_status": d.get("chunk_status", "pending"),
                "tags": d.get("tags", []),
                "summary": d.get("summary", ""),
                "preview": d.get("preview", ""),
                "knowledge_card": d.get("knowledge_card"),
                "chunk_count": d.get("chunk_count", 0),
            })
    return jsonify({"error": "文档不存在"}), 404


@app.route("/library/api/book/<doc_id>", methods=["DELETE"])
def api_delete(doc_id):
    """删除文档"""
    index = load_index()
    for i, d in enumerate(index["documents"]):
        if d["id"] == doc_id:
            # 删除文件
            filepath = Path(d["filepath"])
            if filepath.exists():
                filepath.unlink()
            # 删除索引
            index["documents"].pop(i)
            save_index(index)
            return jsonify({"success": True})
    return jsonify({"error": "文档不存在"}), 404


@app.route("/library/api/book/<doc_id>/chunk", methods=["POST"])
def api_trigger_chunk(doc_id):
    """触发动态chunk处理"""
    index = load_index()
    for d in index["documents"]:
        if d["id"] == doc_id:
            if d.get("chunk_status") == "processing":
                return jsonify({"error": "正在处理中"}), 400
            d["chunk_status"] = "processing"
            save_index(index)

            import threading
            def chunk_async(doc_id):
                try:
                    process_document_chunks(doc_id)
                except Exception as e:
                    print(f"Chunk error for {doc_id}: {e}")
                    index = load_index()
                    for d in index["documents"]:
                        if d["id"] == doc_id:
                            d["chunk_status"] = "pending"
                            save_index(index)
                            break

            threading.Thread(target=chunk_async, args=(doc_id,), daemon=True).start()
            return jsonify({"success": True, "status": "processing"})
    return jsonify({"error": "文档不存在"}), 404


@app.route("/library/api/stats")
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

        photos_gb = round(size_of("/home/open/.openclaw/media/photos") / (1024**3), 2)
        videos_gb = round(size_of("/home/open/.openclaw/media/videos") / (1024**3), 2)
        inbound_gb = round(size_of("/home/open/.openclaw/media/inbound") / (1024**3), 2)
        library_gb = round(size_of("/home/open/.openclaw/workspace/library/books") / (1024**3), 2)
        media_gb = round((photos_gb + videos_gb + inbound_gb), 2)

        return jsonify({
            "total_gb": round(total / (1024**3), 1),
            "used_gb": round(used / (1024**3), 1),
            "free_gb": round(free / (1024**3), 1),
            "percent": round(used / total * 100, 1),
            "photos_gb": photos_gb,
            "videos_gb": videos_gb,
            "inbound_gb": inbound_gb,
            "library_gb": library_gb,
            "media_gb": media_gb,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/library/api/categories")
def api_categories():
    """获取所有分类"""
    index = load_index()
    categories = set()
    for d in index.get("documents", []):
        cat = d.get("category", "待分类")
        if cat:
            categories.add(cat)
    return jsonify({"categories": sorted(categories)})


@app.route("/library/api/tags")
def api_tags():
    """获取所有标签"""
    index = load_index()
    tags = set()
    for d in index.get("documents", []):
        for tag in d.get("tags", []):
            tags.add(tag)
    return jsonify({"tags": sorted(tags)})


@app.route("/library/api/book/<doc_id>/tags", methods=["PUT"])
def api_update_book_tags(doc_id):
    """更新文档标签"""
    index = load_index()
    req = request.get_json()
    new_tags = req.get("tags", [])

    for d in index["documents"]:
        if d["id"] == doc_id:
            d["tags"] = new_tags
            save_index(index)
            return jsonify({"success": True, "tags": new_tags})

    return jsonify({"error": "文档不存在"}), 404


@app.route("/library/api/book/<doc_id>/classify", methods=["POST"])
def api_classify_book(doc_id):
    """手动触发AI打标签（用于上传时未自动打标签的大文件）"""
    index = load_index()
    for d in index["documents"]:
        if d["id"] == doc_id:
            if d.get("status") == "classified" and d.get("tags"):
                return jsonify({"error": "已经打过标签"}), 400
            d["status"] = "classifying"
            save_index(index)

            import threading
            def classify_async(doc_id):
                try:
                    ai_tag_document(doc_id)
                except Exception as e:
                    print(f"Classify error for {doc_id}: {e}")

            threading.Thread(target=classify_async, args=(doc_id,), daemon=True).start()
            return jsonify({"success": True, "message": "开始AI打标签，请稍候刷新页面"})

    return jsonify({"error": "文档不存在"}), 404


# ============== 文件下载/阅读 ==============

@app.route("/library/file/<doc_id>")
def serve_book(doc_id):
    """提供文档下载/阅读"""
    index = load_index()
    for d in index["documents"]:
        if d["id"] == doc_id:
            filepath = Path(d["filepath"])
            if filepath.exists():
                return send_from_directory(
                    BOOKS_DIR, d["stored_name"],
                    as_attachment=False,
                    mimetype=get_mimetype(d["format"])
                )
    abort(404)


def get_mimetype(fmt):
    types = {
        "pdf": "application/pdf",
        "epub": "application/epub+zip",
        "txt": "text/plain",
        "md": "text/markdown",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    }
    return types.get(fmt, "application/octet-stream")


# ============== 后台处理函数 ==============

# ============== AI 自动打标签 ==============

def ai_tag_document(doc_id):
    """用 MiniMax AI 自动分析文档生成标签和分类"""
    import time
    time.sleep(2)

    index = load_index()
    doc = None
    for d in index["documents"]:
        if d["id"] == doc_id:
            doc = d
            break
    if not doc:
        return

    filepath = Path(doc["filepath"])
    if not filepath.exists():
        return

    # 提取文本内容（最多 2000 字给 AI）
    text = extract_text_preview(filepath, max_chars=2000)
    if not text.strip():
        text = Path(doc["original_name"]).stem  # 用文件名做兜底

    book_name = Path(doc["original_name"]).stem

    prompt = f"""分析以下书籍，生成最合适的标签和分类。

书名：{book_name}
内容预览：
{text[:1500]}

请以JSON格式返回，不要有其他内容：
{{"tags": ["标签1", "标签2", ...], "category": "分类"}}

规则：
- 标签数量 3-6 个，每个标签 2-4 个字
- 分类必须是单个词或短语，如：文学、历史、哲学、科学、技术、艺术、传记、小说、诗词、社会、文化、教育、军事、其他
- 标签要反映书的内容主题和领域

直接返回JSON，不要解释。"""

    try:
        resp = requests.post(
            MINIMAX_BASE_URL,
            headers={
                "Authorization": f"Bearer {MINIMAX_API_KEY}",
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
                "anthropic-dangerous-direct-browser-access": "true",
            },
            json={
                "model": "MiniMax-M2.7",
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=30,
        )
        if resp.status_code == 200:
            result = resp.json()
            content = result.get("content", [])
            text_reply = ""
            if isinstance(content, list):
                for item in content:
                    if item.get("type") == "text":
                        text_reply = item.get("text", "")
                        break
                # 尝试解析 JSON
                import re, json as json_mod
                m = re.search(r'\{.*\}', text_reply, re.DOTALL)
                if m:
                    data = json_mod.loads(m.group())
                    doc["tags"] = data.get("tags", [])
                    doc["category"] = data.get("category", "其他")
                    doc["status"] = "classified"
                    print(f"AI tagging success for {doc_id}: tags={doc['tags']}, category={doc['category']}")
                else:
                    print(f"AI response parse failed for {doc_id}: {text_reply[:100]}")
        else:
            print(f"AI API error for {doc_id}: {resp.status_code} {resp.text[:100]}")
    except Exception as e:
        print(f"AI tagging error for {doc_id}: {e}")

    doc["status"] = "classified"
    save_index(index)


def classify_document(doc_id):
    """上传后触发 AI 自动分类"""
    import threading
    threading.Thread(target=ai_tag_document, args=(doc_id,), daemon=True).start()


def _generate_chunk_card(chunk_text, chunk_index, total_chunks, book_name):
    """为单个 chunk 生成知识卡片（调用 MiniMax AI）"""
    import re, json as json_mod

    prompt = f"""你是一本中文书籍的阅读助手。以下是《{book_name}》第 {chunk_index+1}/{total_chunks} 部分的文本内容：

【文本内容】
{chunk_text[:2500]}

请分析这段内容，生成一份结构化的知识卡片，以JSON格式返回：

{{
  "summary": "这段内容50字以内的摘要",
  "key_concepts": ["核心概念1", "核心概念2", "核心概念3"],
  "people": ["出现的重要人物1", "人物2"],
  "events": ["重要事件1", "事件2"],
  "quotes": ["精彩引文1（如果有）", "引文2"],
  "questions": ["这段内容能回答的一个问题"]
}}

规则：
- summary 最多50字，要精准概括
- key_concepts 提取这段最核心的概念术语，2-4个
- people 只列出有历史/文学意义的重要人物，没有则空列表[]
- events 只列出有重要意义的事件，没有则空列表[]
- quotes 只保留真正值得引用的精彩句子，没有则空列表[]
- questions 是基于这段内容可以提出的一个有意义的问题
- 直接返回JSON，不要解释

直接返回JSON："""

    try:
        resp = requests.post(
            MINIMAX_BASE_URL,
            headers={
                "Authorization": f"Bearer {MINIMAX_API_KEY}",
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
                "anthropic-dangerous-direct-browser-access": "true",
            },
            json={
                "model": "MiniMax-M2.7",
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=60,
        )
        if resp.status_code == 200:
            result = resp.json()
            content = result.get("content", [])
            text_reply = ""
            if isinstance(content, list):
                for item in content:
                    if item.get("type") == "text":
                        text_reply = item.get("text", "")
                        break
            if not text_reply:
                return None
            m = re.search(r'\{.*\}', text_reply, re.DOTALL)
            if m:
                card = json_mod.loads(m.group())
                return {
                    "chunk_index": chunk_index,
                    "text": chunk_text[:500],  # 保留原文前500字作为参考
                    "summary": card.get("summary", ""),
                    "key_concepts": card.get("key_concepts", []),
                    "people": card.get("people", []),
                    "events": card.get("events", []),
                    "quotes": card.get("quotes", []),
                    "questions": card.get("questions", []),
                }
    except Exception as e:
        print(f"Chunk card generation error: {e}")
    return None


def process_document_chunks(doc_id):
    """处理文档 chunk，生成知识卡片"""
    import time
    index = load_index()
    doc = None
    for d in index["documents"]:
        if d["id"] == doc_id:
            doc = d
            break
    if not doc:
        return

    filepath = Path(doc["filepath"])
    if not filepath.exists():
        print(f"File not found for {doc_id}")
        return

    # 提取全文
    full_text = _extract_full_text_impl(filepath)
    if not full_text.strip():
        print(f"Empty text for {doc_id}")
        index = load_index()
        for d in index["documents"]:
            if d["id"] == doc_id:
                d["chunk_status"] = "error"
                save_index(index)
                break
        return

    # 拆分为 chunks
    chunks = _split_into_chunks(full_text)
    if not chunks:
        print(f"No chunks generated for {doc_id}")
        return

    print(f"Processing {doc_id}: {len(chunks)} chunks")

    # 检查是否有未完成的知识文件（中断恢复）
    knowledge_file = BASE_DIR / "index" / f"{doc_id}_knowledge.json"
    knowledge_file.parent.mkdir(parents=True, exist_ok=True)
    existing_cards = []
    processed_indices = set()
    if knowledge_file.exists():
        try:
            existing = json.load(open(knowledge_file, encoding="utf-8"))
            existing_cards = existing.get("chunks", [])
            processed_indices = {c["chunk_index"] for c in existing_cards}
            print(f"Resuming {doc_id}: {len(processed_indices)}/{len(chunks)} already done")
        except:
            existing_cards = []

    # 生成知识卡片（增量：每块保存一次）
    for i, chunk_text in enumerate(chunks):
        if i in processed_indices:
            continue  # 跳过已完成的 chunk
        card = _generate_chunk_card(chunk_text, i, len(chunks), doc["original_name"])
        if card:
            existing_cards.append(card)
        time.sleep(1)  # 避免 API 限速

        # 每块处理完立即保存（防丢失）
        knowledge_data = {
            "doc_id": doc_id,
            "book_name": doc["original_name"],
            "full_text_length": len(full_text),
            "chunks": existing_cards,
            "chunk_count": len(existing_cards),
            "total_chunks": len(chunks),
            "processed_at": datetime.now().isoformat(),
        }
        with open(knowledge_file, "w", encoding="utf-8") as f:
            json.dump(knowledge_data, f, ensure_ascii=False, indent=2)

    # 更新索引
    index = load_index()
    for d in index["documents"]:
        if d["id"] == doc_id:
            d["chunk_status"] = "ready"
            d["status"] = "indexed"
            d["chunk_count"] = len(existing_cards)
            d["knowledge_card"] = str(knowledge_file)
            save_index(index)
            break

    print(f"Document {doc_id} chunk processing complete: {len(existing_cards)}/{len(chunks)} cards")


# ============== 知识问答（第三阶段） ==============

def _build_global_index():
    """构建全局搜索索引（所有已处理书籍的知识卡片）"""
    import json
    index = {}
    idx_dir = BASE_DIR / "index"
    if not idx_dir.exists():
        return index
    for kf in idx_dir.glob("*_knowledge.json"):
        try:
            doc = json.load(open(kf, encoding="utf-8"))
            doc_id = doc["doc_id"]
            index[doc_id] = {
                "book_name": doc["book_name"],
                "chunks": doc.get("chunks", []),
            }
        except:
            pass
    return index


def _search_chunks(query, chunks, top_k=5):
    """简单关键词检索：从 chunks 中找出最相关的 top_k 个"""
    import re
    # 分词：英文/数字按单词切，中文按 2-gram 切
    english_words = re.findall(r'[a-zA-Z0-9]{2,}', query)
    chinese_chars = re.findall(r'[\u4e00-\u9fa5]', query)
    chinese_bigrams = [chinese_chars[i]+chinese_chars[i+1] for i in range(len(chinese_chars)-1)]
    query_terms = set(english_words + chinese_bigrams)

    scored = []
    for chunk in chunks:
        score = 0
        text = (chunk.get("summary", "") + " " + " ".join(chunk.get("key_concepts", [])))
        chunk_text = chunk.get("text", "")

        for term in query_terms:
            # 在摘要/概念中匹配
            score += text.count(term) * 3
            # 在 key_concepts 中匹配权重更高
            for kw in chunk.get("key_concepts", []):
                if term in kw:
                    score += 8
            # 在原文匹配
            score += chunk_text.count(term)

        if score > 0:
            scored.append((score, chunk))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored[:top_k]]


@app.route("/library/api/ask", methods=["POST"])
def api_ask():
    """知识问答：POST {question, doc_id?}"""
    req = request.get_json()
    question = req.get("question", "").strip()
    doc_id = req.get("doc_id")  # 可选：指定某本书

    if not question:
        return jsonify({"error": "问题不能为空"}), 400

    # 加载知识卡片
    global_index = _build_global_index()
    if not global_index:
        return jsonify({"error": "知识库为空，请先处理文档"}), 400

    # 确定搜索范围
    if doc_id and doc_id in global_index:
        search_books = {doc_id: global_index[doc_id]}
    else:
        search_books = global_index

    # 收集所有相关 chunks
    all_results = []
    for bid, bdata in search_books.items():
        relevant = _search_chunks(question, bdata["chunks"], top_k=5)
        for chunk in relevant:
            all_results.append({
                "book_id": bid,
                "book_name": bdata["book_name"],
                "chunk_index": chunk["chunk_index"],
                "summary": chunk.get("summary", ""),
                "key_concepts": chunk.get("key_concepts", []),
                "people": chunk.get("people", []),
                "text": chunk.get("text", "")[:300],
            })

    if not all_results:
        return jsonify({"error": "未找到相关内容，请尝试其他问题"}), 404

    # 构造 AI prompt
    context = "\n\n".join([
        f"【{r['book_name']} 第{r['chunk_index']+1}节】摘要：{r['summary']}；概念：{', '.join(r['key_concepts'])}；原文：{r['text'][:200]}"
        for r in all_results[:5]
    ])

    prompt = f"""你是一个知识问答助手。请根据以下参考资料回答用户的问题。

【参考资料】
{context}

【用户问题】
{question}

回答要求：
- 基于上述参考资料给出准确答案
- 如果资料不足以回答，说明"资料中没有相关信息"
- 适当引用参考内容（可注明来自哪本书）
- 回答简洁有条理

直接开始回答："""

    try:
        resp = requests.post(
            MINIMAX_BASE_URL,
            headers={
                "Authorization": f"Bearer {MINIMAX_API_KEY}",
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
                "anthropic-dangerous-direct-browser-access": "true",
            },
            json={
                "model": "MiniMax-M2.7",
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=60,
        )
        answer = ""
        if resp.status_code == 200:
            result = resp.json()
            content = result.get("content", [])
            if isinstance(content, list):
                for item in content:
                    if item.get("type") == "text":
                        answer = item.get("text", "")
                        break
    except Exception as e:
        return jsonify({"error": f"AI 生成失败: {str(e)}"}), 500

    return jsonify({
        "question": question,
        "answer": answer,
        "sources": [{"book": r["book_name"], "chunk": r["chunk_index"]+1, "concept": r["key_concepts"][:3]} for r in all_results[:5]],
    })


@app.route("/library/api/knowledge/<doc_id>")
def api_knowledge(doc_id):
    """获取某本书的所有知识卡片"""
    import json
    kf = BASE_DIR / "index" / f"{doc_id}_knowledge.json"
    if not kf.exists():
        return jsonify({"error": "文档未处理"}), 404
    with open(kf, encoding="utf-8") as f:
        data = json.load(f)
    return jsonify({"doc_id": doc_id, "book_name": data["book_name"], "chunks": data.get("chunks", [])})


# ============== 知识关联网络（第四阶段） ==============

def _build_entity_index():
    """从所有知识卡片构建实体索引"""
    import json
    all_chunks = []  # (doc_id, book_name, chunk_index, entities)
    idx_dir = BASE_DIR / "index"
    if not idx_dir.exists():
        return []
    for kf in idx_dir.glob("*_knowledge.json"):
        try:
            doc = json.load(open(kf, encoding="utf-8"))
            doc_id = doc["doc_id"]
            book_name = doc["book_name"]
            for chunk in doc.get("chunks", []):
                entities = {
                    "people": [p.strip() for p in chunk.get("people", []) if p.strip()],
                    "events": [e.strip() for e in chunk.get("events", []) if e.strip()],
                    "concepts": [c.strip() for c in chunk.get("key_concepts", []) if c.strip()],
                }
                all_chunks.append({
                    "doc_id": doc_id,
                    "book_name": book_name,
                    "chunk_index": chunk["chunk_index"],
                    "entities": entities,
                })
        except:
            pass
    return all_chunks


@app.route("/library/api/network")
def api_network():
    """
    获取知识关联网络数据
    - nodes: 所有实体节点
    - edges: 实体间的共现关系
    - books: 书籍列表
    """
    all_chunks = _build_entity_index()
    if not all_chunks:
        return jsonify({"nodes": [], "edges": [], "books": []})

    # 收集所有实体节点
    nodes_map = {}  # entity_name -> node_info
    for chunk_data in all_chunks:
        for p in chunk_data["entities"]["people"]:
            if p not in nodes_map:
                nodes_map[p] = {"id": p, "type": "person", "books": set(), "count": 0}
            nodes_map[p]["books"].add(chunk_data["book_name"])
            nodes_map[p]["count"] += 1
        for e in chunk_data["entities"]["events"]:
            if e not in nodes_map:
                nodes_map[e] = {"id": e, "type": "event", "books": set(), "count": 0}
            nodes_map[e]["books"].add(chunk_data["book_name"])
            nodes_map[e]["count"] += 1
        for c in chunk_data["entities"]["concepts"]:
            if c not in nodes_map:
                nodes_map[c] = {"id": c, "type": "concept", "books": set(), "count": 0}
            nodes_map[c]["books"].add(chunk_data["book_name"])
            nodes_map[c]["count"] += 1

    # 构建共现边（同一chunk内共同出现的实体 → 关联）
    edges_map = {}  # (e1, e2) -> {"weight": count, "books": set()}
    for chunk_data in all_chunks:
        all_entities = (
            chunk_data["entities"]["people"]
            + chunk_data["entities"]["events"]
            + chunk_data["entities"]["concepts"]
        )
        # 去重但保持顺序
        seen = []
        for e in all_entities:
            if e not in seen:
                seen.append(e)
        # 所有实体两两配对
        for i in range(len(seen)):
            for j in range(i + 1, len(seen)):
                e1, e2 = sorted([seen[i], seen[j]])
                key = (e1, e2)
                if key not in edges_map:
                    edges_map[key] = {"source": e1, "target": e2, "weight": 0, "books": set()}
                edges_map[key]["weight"] += 1
                edges_map[key]["books"].add(chunk_data["book_name"])

    # 过滤：只保留 weight >= 1 的边（出现过至少1次共现）
    # 节点：只保留在边中出现的节点
    connected_nodes = set()
    for (e1, e2), edata in edges_map.items():
        if edata["weight"] >= 1:
            connected_nodes.add(e1)
            connected_nodes.add(e2)

    nodes = []
    for name, ndata in nodes_map.items():
        if name in connected_nodes:
            nodes.append({
                "id": name,
                "type": ndata["type"],
                "books": list(ndata["books"]),
                "count": ndata["count"],
            })

    edges = [
        {
            "source": e1,
            "target": e2,
            "weight": edata["weight"],
            "books": list(edata["books"]),
        }
        for (e1, e2), edata in edges_map.items()
        if edata["weight"] >= 1
    ]

    books = list(set(d["book_name"] for d in all_chunks))

    return jsonify({
        "nodes": nodes,
        "edges": edges,
        "books": books,
        "total_nodes": len(nodes),
        "total_edges": len(edges),
    })


# ============== 知识卡片导出 ==============

@app.route("/library/api/export/<doc_id>")
def api_export(doc_id):
    """导出知识卡片为 Markdown 格式"""
    import json
    kf = BASE_DIR / "index" / f"{doc_id}_knowledge.json"
    if not kf.exists():
        return jsonify({"error": "文档未处理或不存在"}), 404

    with open(kf, encoding="utf-8") as f:
        data = json.load(f)

    book_name = data.get("book_name", doc_id)
    chunks = data.get("chunks", [])
    total = len(chunks)

    md_lines = [
        f"# {book_name}",
        f"",
        f"> 本笔记由 AI 自动生成，共 {total} 个知识片段",
        f"",
    ]

    chunks_sorted = sorted(chunks, key=lambda x: x.get("chunk_index", 0))

    for c in chunks_sorted:
        idx = c.get("chunk_index", "?")
        summary = c.get("summary", "").strip()
        concepts = c.get("key_concepts", [])
        people = c.get("people", [])
        events = c.get("events", [])
        quotes = c.get("quotes", [])
        questions = c.get("questions", [])
        text = c.get("text", "").strip()[:300]

        md_lines.append(f"## 第 {idx + 1} 节")
        md_lines.append("")
        if summary:
            md_lines.append(f"**摘要：** {summary}")
        if concepts:
            md_lines.append(f"**核心概念：** {' · '.join(concepts)}")
        if people:
            md_lines.append(f"**人物：** {' · '.join(people)}")
        if events:
            md_lines.append(f"**事件：** {' · '.join(events)}")
        if quotes:
            for q in quotes:
                md_lines.append(f"> {q}")
        if questions:
            md_lines.append(f"**思考问题：** {questions[0] if questions else ''}")
        if text:
            md_lines.append(f"**原文：** {text}……")
        md_lines.append("")
        md_lines.append("---")
        md_lines.append("")

    md_content = "\n".join(md_lines)
    filename = f"{book_name}.md"

    from flask import make_response
    from urllib.parse import quote
    safe_filename = quote(filename, safe='')
    resp = make_response(md_content)
    resp.headers["Content-Type"] = "text/markdown; charset=utf-8"
    resp.headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{safe_filename}"
    return resp


@app.route("/library/api/export/<doc_id>/json")
def api_export_json(doc_id):
    """导出知识卡片原始 JSON"""
    import json
    kf = BASE_DIR / "index" / f"{doc_id}_knowledge.json"
    if not kf.exists():
        return jsonify({"error": "文档未处理或不存在"}), 404
    with open(kf, encoding="utf-8") as f:
        data = json.load(f)
    book_name = data.get("book_name", doc_id)
    filename = f"{book_name}_知识卡片.json"
    from flask import make_response
    from urllib.parse import quote
    safe_filename = quote(filename, safe='')
    resp = make_response(json.dumps(data, ensure_ascii=False, indent=2))
    resp.headers["Content-Type"] = "application/json; charset=utf-8"
    resp.headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{safe_filename}"
    return resp


# ============== 启动 ==============

if __name__ == "__main__":
    import sys
    port = 18792
    print(f"Library server starting on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
