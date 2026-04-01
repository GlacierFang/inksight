#!/usr/bin/env python3
"""
个人知识管理 - 本地文档查看器
提供参观记录的本地备份，脱离飞书独立查看
"""

from flask import Flask, render_template, send_from_directory, abort, jsonify, request
from pathlib import Path
import json
import os
import shutil

app = Flask(__name__)

# 文档存储目录
DOCS_DIR = Path("/home/open/.openclaw/workspace/memory/visit-docs")
VISIT_PENDING = Path("/home/open/.openclaw/workspace/memory/visit-pending.json")
PHOTOS_BASE = Path("/home/open/.openclaw/media/photos")

@app.route("/visit/")
def index():
    """文档列表页"""
    docs = []
    if DOCS_DIR.exists():
        for f in DOCS_DIR.iterdir():
            if f.suffix == '.html':
                # 从文件名提取标签
                tag = f.stem
                # 读取创建时间
                ctime = f.stat().st_mtime
                docs.append({
                    'tag': tag,
                    'filename': f.name,
                    'ctime': ctime
                })
    
    # 按时间倒序
    docs.sort(key=lambda x: x['ctime'], reverse=True)
    
    return render_template('index.html', docs=docs)

@app.route("/visit/doc/<path:filename>")
def doc(filename):
    """查看文档"""
    doc_path = DOCS_DIR / filename
    if not doc_path.exists():
        abort(404)
    return send_from_directory(DOCS_DIR, filename)

@app.route("/visit/photo/<path:filename>")
def photo(filename):
    """查看照片缩略图"""
    # 尝试多个位置
    for base in [
        PHOTOS_BASE,
        Path("/home/open/.openclaw/media/photos"),
        Path("/home/open/.openclaw/openclaw_safe_storage/.openclaw/media/photos")
    ]:
        photo_path = base / filename
        if photo_path.exists():
            return send_from_directory(base, filename)
    
    # 尝试从 meta 获取
    return abort(404)

@app.route("/visit/api/list")
def api_list():
    """API: 获取文档列表"""
    docs = []
    if DOCS_DIR.exists():
        for f in DOCS_DIR.iterdir():
            if f.suffix == '.html':
                tag = f.stem
                docs.append({
                    'tag': tag,
                    'filename': f.name
                })
    return {'docs': docs}

@app.route("/visit/api/delete/<tag>", methods=["POST"])
def api_delete(tag):
    """
    删除指定标签的本地备份文档
    1. 删除 visit-docs/ 下的 HTML 文件
    2. 从 visit-pending.json 的 generated 列表中移除该标签的记录
    """
    import datetime
    
    html_path = DOCS_DIR / f"{tag}.html"
    deleted_from_disk = False
    deleted_from_pending = False
    
    # 1. 删除 HTML 文件
    if html_path.exists():
        html_path.unlink()
        deleted_from_disk = True
    
    # 2. 从 visit-pending.json 移除 generated 记录
    try:
        with open(VISIT_PENDING, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        original_len = len(data.get('generated', []))
        data['generated'] = [g for g in data.get('generated', []) if g.get('tag') != tag]
        deleted_count = original_len - len(data['generated'])
        
        if deleted_count > 0:
            with open(VISIT_PENDING, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            deleted_from_pending = True
        
        changes = {"disk": deleted_from_disk, "pending": deleted_from_pending, "removed_records": deleted_count}
    except Exception as e:
        changes = {"error": str(e)}
    
    return jsonify({
        "success": True,
        "tag": tag,
        "changes": changes
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=18793, debug=False)
