#!/usr/bin/env python3
"""
生成参观记录的本地 HTML 备份
与飞书文档同步生成，双重保险
格式与飞书文档保持一致
"""

import json
from pathlib import Path
from datetime import datetime

DOCS_DIR = Path("/home/open/.openclaw/workspace/memory/visit-docs")
PHOTOS_BASE = Path("/home/open/.openclaw/media/photos")


def generate_html_doc(tag, photos_data, background_info, feishu_doc_url=None, known_tags=None):
    """
    生成 HTML 备份文档（飞书文档风格）
    
    Args:
        tag: 标签（如"永昌陵"）
        photos_data: 照片信息列表 [{id, path, rel_path, desc, source, date}]
        background_info: 背景资料HTML
        feishu_doc_url: 飞书文档URL（可选）
        known_tags: 已知标签库（可选）
    """
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    
    # 生成安全的文件名
    safe_tag = tag.replace('/', '_').replace('\\', '_').replace(' ', '_')
    html_path = DOCS_DIR / f"{safe_tag}.html"
    
    # 整理照片数据
    valid_photos = [p for p in photos_data if p.get('rel_path')]
    
    # 照片时间范围
    dates = [p.get('date', '') for p in valid_photos if p.get('date')]
    date_range = ""
    if dates:
        dates.sort()
        if dates[0] == dates[-1]:
            date_range = dates[0]
        else:
            date_range = f"{dates[0]} ~ {dates[-1]}"
    
    # 准备照片 HTML（网格画廊风格）
    photos_html = ""
    if valid_photos:
        photos_html = """
        <div class="photo-gallery">
        """
        for photo in valid_photos:
            rel_path = photo.get('rel_path', '')
            desc = photo.get('desc', '') or '照片'
            source = photo.get('source', '未知')
            date = photo.get('date', '')
            
            # 缩略图 URL
            thumb_url = f"/visit/photo/{rel_path}"
            full_url = f"/visit/photo/{rel_path}"
            
            photos_html += f"""
            <div class="photo-card">
                <div class="photo-img" onclick="showFull('{full_url}')">
                    <img src="{thumb_url}" alt="{desc}" loading="lazy">
                </div>
                <div class="photo-caption">
                    <div class="photo-desc">{desc}</div>
                    <div class="photo-meta">{source} · {date}</div>
                </div>
            </div>
            """
        photos_html += "</div>"
    else:
        photos_html = "<div class='empty'>暂无照片记录</div>"
    
    # 生成时间
    gen_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # 参观时间（最早~最晚）
    visit_time_str = date_range if date_range else "未知"
    
    # 标签列表 HTML
    tag_html = f'<span class="tag">{tag}</span>'
    
    # 相关知识点
    related_tags_html = ""
    if known_tags and len(known_tags) > 1:
        related_tags_html = '<div class="related-tags"><span class="related-label">相关标签：</span>'
        for rt in known_tags[:8]:
            if rt != tag:
                related_tags_html += f'<span class="tag">{rt}</span>'
        related_tags_html += '</div>'
    
    # 飞书链接
    if feishu_doc_url:
        feishu_link = f'<a href="{feishu_doc_url}" target="_blank" class="feishu-btn">🔗 飞书原文档</a>'
    else:
        feishu_link = ""
    
    # HTML 模板（飞书文档风格 - 白色简洁）
    html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{tag} 参观记录</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            background: #ffffff;
            color: #1f2329;
            font-family: "PingFang SC", "Microsoft YaHei", "Helvetica Neue", sans-serif;
            font-size: 15px;
            line-height: 1.7;
            padding: 40px;
        }}
        .container {{ max-width: 900px; margin: 0 auto; }}
        
        /* 标题区 */
        .doc-header {{ margin-bottom: 32px; border-bottom: 1px solid #e5e7eb; padding-bottom: 20px; }}
        .doc-title {{ font-size: 28px; font-weight: 600; color: #1f2329; margin-bottom: 12px; }}
        .doc-meta {{ font-size: 13px; color: #8f959e; }}
        
        /* 段落标题 */
        .section-title {{ 
            font-size: 17px; 
            font-weight: 600; 
            color: #1f2329; 
            margin: 28px 0 14px 0;
            padding-left: 12px;
            border-left: 4px solid #07c160;
        }}
        
        /* 信息区 */
        .info-table {{ background: #f7f8fa; border-radius: 8px; padding: 16px 20px; margin-bottom: 8px; }}
        .info-row {{ display: flex; gap: 32px; flex-wrap: wrap; }}
        .info-item {{ display: flex; align-items: baseline; gap: 8px; }}
        .info-label {{ font-size: 13px; color: #8f959e; min-width: 60px; }}
        .info-value {{ font-size: 14px; color: #1f2329; }}
        
        /* 标签 */
        .tag {{ 
            display: inline-block;
            background: #e8f5e9; 
            color: #07c160; 
            padding: 2px 10px; 
            border-radius: 4px; 
            font-size: 13px;
            margin-right: 6px;
        }}
        
        /* 相关知识点 */
        .related-tags {{
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            align-items: center;
            margin-top: 8px;
        }}
        .related-label {{
            font-size: 13px;
            color: #8f959e;
        }}
        
        /* 照片画廊 */
        .photo-gallery {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
            gap: 16px;
            margin-top: 12px;
        }}
        .photo-card {{ 
            background: #fff;
            border: 1px solid #e5e7eb;
            border-radius: 8px;
            overflow: hidden;
            transition: box-shadow 0.2s;
        }}
        .photo-card:hover {{ box-shadow: 0 4px 12px rgba(0,0,0,0.1); }}
        .photo-img {{ 
            width: 100%; 
            height: 160px; 
            overflow: hidden; 
            cursor: pointer;
            background: #f7f8fa;
        }}
        .photo-img img {{ 
            width: 100%; 
            height: 100%; 
            object-fit: cover;
            transition: transform 0.3s;
        }}
        .photo-img:hover img {{ transform: scale(1.05); }}
        .photo-caption {{ padding: 10px 12px; }}
        .photo-desc {{ font-size: 13px; color: #1f2329; margin-bottom: 4px; line-height: 1.4; }}
        .photo-meta {{ font-size: 12px; color: #8f959e; }}
        
        /* 背景资料 */
        .background-text {{ color: #1f2329; line-height: 1.9; }}
        .background-text p {{ margin-bottom: 14px; }}
        .background-text strong {{ color: #07c160; }}
        
        /* 链接区 */
        .links-section {{ margin-top: 28px; display: flex; gap: 12px; flex-wrap: wrap; }}
        .feishu-btn {{
            display: inline-flex; align-items: center; gap: 6px;
            background: #07c160; color: #fff;
            padding: 8px 16px; border-radius: 6px; text-decoration: none;
            font-size: 14px;
        }}
        .feishu-btn:hover {{ background: #06a555; }}
        .local-btn {{
            display: inline-flex; align-items: center; gap: 6px;
            background: #f7f8fa; color: #1f2329; border: 1px solid #e5e7eb;
            padding: 8px 16px; border-radius: 6px; text-decoration: none;
            font-size: 14px;
        }}
        .local-btn:hover {{ background: #f0f1f3; }}
        
        /* 页脚 */
        .gen-time {{ text-align: center; color: #bfc3c9; font-size: 12px; margin-top: 40px; padding-top: 20px; border-top: 1px solid #f1f2f5; }}
        
        /* 空状态 */
        .empty {{ text-align: center; color: #8f959e; padding: 40px; background: #f7f8fa; border-radius: 8px; }}
        
        /* 返回链接 */
        .back-link {{ display: inline-flex; align-items: center; gap: 6px; color: #07c160; text-decoration: none; font-size: 14px; margin-bottom: 24px; }}
        .back-link:hover {{ text-decoration: underline; }}
        
        /* 图片放大弹窗 */
        .modal {{ display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.85); z-index: 1000; justify-content: center; align-items: center; }}
        .modal.active {{ display: flex; }}
        .modal img {{ max-width: 90%; max-height: 90%; object-fit: contain; border-radius: 4px; }}
        .modal-close {{ position: absolute; top: 20px; right: 20px; color: #fff; font-size: 32px; cursor: pointer; width: 40px; height: 40px; display: flex; align-items: center; justify-content: center; background: rgba(255,255,255,0.1); border-radius: 50%; }}
        .modal-close:hover {{ background: rgba(255,255,255,0.2); }}
        
        /* 响应式 */
        @media (max-width: 600px) {{
            body {{ padding: 20px; }}
            .doc-title {{ font-size: 22px; }}
            .photo-gallery {{ grid-template-columns: repeat(2, 1fr); gap: 10px; }}
            .photo-img {{ height: 120px; }}
            .info-row {{ flex-direction: column; gap: 10px; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <a href="/visit/" class="back-link">← 返回本地备份</a>
        
        <!-- 标题 -->
        <div class="doc-header">
            <h1 class="doc-title">{tag} 参观记录</h1>
            <div class="doc-meta">本地备份 · {gen_time}</div>
        </div>
        
        <!-- 基本信息 -->
        <div class="section-title">📋 基本信息</div>
        <div class="info-table">
            <div class="info-row">
                <div class="info-item">
                    <span class="info-label">标签</span>
                    <span class="info-value">{tag_html}</span>
                </div>
                <div class="info-item">
                    <span class="info-label">照片数</span>
                    <span class="info-value">{len(valid_photos)} 张</span>
                </div>
                <div class="info-item">
                    <span class="info-label">参观时间</span>
                    <span class="info-value">{visit_time_str}</span>
                </div>
            </div>
        </div>
        
        <!-- 照片记录 -->
        <div class="section-title">📷 照片记录</div>
        {photos_html}
        
        <!-- 背景资料 -->
        <div class="section-title">📖 背景资料</div>
        <div class="background-text">
            {background_info}
        </div>
        
        <!-- 相关知识点 -->
        {related_tags_html}
        
        <!-- 相关链接 -->
        <div class="links-section">
            <a href="/visit/" class="local-btn">📄 本地备份列表</a>
            {feishu_link}
        </div>
        
        <div class="gen-time">
            本地备份于 {gen_time} 生成
        </div>
    </div>
    
    <!-- 图片放大弹窗 -->
    <div class="modal" id="imgModal" onclick="closeModal()">
        <span class="modal-close">&times;</span>
        <img id="modalImg" src="">
    </div>
    
    <script>
        function showFull(src) {{
            document.getElementById('modalImg').src = src;
            document.getElementById('imgModal').classList.add('active');
        }}
        function closeModal() {{
            document.getElementById('imgModal').classList.remove('active');
        }}
        document.addEventListener('keydown', function(e) {{
            if (e.key === 'Escape') closeModal();
        }});
    </script>
</body>
</html>"""
    
    # 写入文件
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    return str(html_path)


if __name__ == "__main__":
    # 测试
    result = generate_html_doc(
        tag="永昌陵",
        photos_data=[
            {
                'id': 'test.jpg',
                'path': '/home/open/.openclaw/media/photos/2026-03-29/test.jpg',
                'rel_path': '2026-03-29/test.jpg',
                'desc': '永昌陵石刻艺术',
                'source': '飞书',
                'date': '2026-03-29'
            }
        ],
        background_info="<p>永昌陵是宋太祖赵匡胤的陵墓...</p>",
        feishu_doc_url="https://feishu.cn/docx/test"
    )
    print(f"HTML 文档已生成: {result}")
