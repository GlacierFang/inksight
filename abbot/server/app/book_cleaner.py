"""
05 §2.2 `app/book_cleaner.py`：**零 AI 调用**的纯本地电子书清洗。

`.epub` / `.pdf` / `.docx` / `.txt` → 标准 UTF-8 `.txt` + `.toc`。

铁律（05 §4.4 精神 + 07 契约）：
* **不调用任何 AI / 大模型** —— 清洗是纯本地确定性处理；
* 输出必须 **UTF-8**（设备端固件不做 GBK 转码、不做重型解析）；
* `.toc` 与 `.txt` 的字节偏移必须严格对齐（设备端按偏移定位章节）。

设备端最终只做「流式分页 + 边界对齐」，因此本模块的职责是把书洗成
「纯文本 + 章节偏移表」这一种形态。
"""

import json
import re
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SUPPORTED_SUFFIXES = (".epub", ".pdf", ".docx", ".txt", ".md")

# 常见中文章节标题：第一章 / 第1节 / 一、 / 1.1 / Chapter N
_CHAPTER_PATTERNS = (
    re.compile(r"^第[0-9一二三四五六七八九十百]+[章讲]\s*[^\n]{0,60}$"),
    re.compile(r"^第[0-9一二三四五六七八九十百]+[节课]\s*[^\n]{0,60}$"),
    re.compile(r"^[一二三四五六七八九十百]+[、.．]\s*[^\n]{0,60}$"),
    re.compile(r"^\d+(\.\d+)*[、.．]?\s*\S[^\n]{0,60}$"),
    re.compile(r"^(Chapter|CHAPTER)\s+[0-9IVXLC]+[^\n]{0,60}$"),
)


class BookCleanError(RuntimeError):
    """清洗失败。`reason` 用于给用户看的温和提示。"""


def _is_chapter_title(line: str) -> bool:
    line = line.strip()
    if not line or len(line) > 64:
        return False
    return any(p.match(line) for p in _CHAPTER_PATTERNS)


def _split_sections(text: str) -> List[Tuple[str, int]]:
    """
    把正文切成 `(标题, 该标题所在字符偏移)` 序列。

    偏移是**字符偏移**，落盘时统一转成字节偏移（UTF-8 中文 3 字节）。
    """
    sections: List[Tuple[str, int]] = []
    pos = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if _is_chapter_title(stripped):
            sections.append((stripped, pos))
        pos += len(line)
    if not sections:
        sections.append(("正文", 0))
    return sections


def _build_toc(text: str) -> List[Dict[str, Any]]:
    """章节表：每项含标题与**字节偏移**（设备端按字节偏移定位）。"""
    toc: List[Dict[str, Any]] = []
    for title, char_offset in _split_sections(text):
        byte_offset = len(text[:char_offset].encode("utf-8"))
        toc.append({"title": title, "offset": byte_offset})
    return toc


def _decode_text(data: bytes) -> str:
    """按 UTF-8 → GB18030 → UTF-16 顺序尝试解码（GB18030 是 GBK 超集）。"""
    for enc in ("utf-8", "gb18030", "utf-16"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    # 兜底：忽略无法解码的字节，绝不让整本书失败
    return data.decode("utf-8", errors="ignore")


def _clean_txt(data: bytes) -> str:
    text = _decode_text(data)
    # 统一换行，去掉行尾空白与连续空行（设备端分页更稳）
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _clean_epub(data: bytes) -> str:
    """
    EPUB 清洗：读取 `container.xml` 定位 OPF，取 spine 顺序的 XHTML 文档，
    去掉标签与脚本样式，保留段落换行。**不解压炸弹** —— 限制单文件大小。
    """
    import io

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise BookCleanError(f"文件不是有效的 EPUB：{exc}") from exc

    names = set(zf.namelist())
    opf_path = ""
    if "META-INF/container.xml" in names:
        try:
            container = zf.read("META-INF/container.xml").decode("utf-8", "ignore")
            m = re.search(r"full-path\s*=\s*[\"']([^\"']+)[\"']", container)
            if m:
                opf_path = m.group(1)
        except (KeyError, OSError):
            opf_path = ""
    if not opf_path:
        candidates = [n for n in names if n.endswith(".opf")]
        if not candidates:
            raise BookCleanError("EPUB 缺少 OPF 目录文件")
        opf_path = candidates[0]

    try:
        opf = zf.read(opf_path).decode("utf-8", "ignore")
    except KeyError as exc:
        raise BookCleanError(f"EPUB 缺少 OPF：{opf_path}") from exc

    base = opf_path.rsplit("/", 1)[0] if "/" in opf_path else ""
    manifest = dict(
        re.findall(r"<item\b[^>]*\bid=[\"']([^\"']+)[\"'][^>]*\bhref=[\"']([^\"']+)[\"']", opf)
    )
    order = re.findall(r"<itemref\b[^>]*\bidref=[\"']([^\"']+)[\"']", opf)

    chapters: List[str] = []
    for item_id in order:
        href = manifest.get(item_id)
        if not href:
            continue
        path = f"{base}/{href}" if base else href
        if path not in names:
            continue
        try:
            raw = zf.read(path)
        except (KeyError, OSError):
            continue
        if len(raw) > 8 * 1024 * 1024:
            continue
        chapters.append(_html_to_text(raw.decode("utf-8", "ignore")))

    if not chapters:
        # 兜底：把所有 xhtml/html 都取一遍
        for name in sorted(names):
            if name.endswith((".xhtml", ".html", ".htm")):
                try:
                    chapters.append(_html_to_text(zf.read(name).decode("utf-8", "ignore")))
                except (KeyError, OSError):
                    continue
    return "\n\n".join(c for c in chapters if c.strip())


def _html_to_text(html: str) -> str:
    """极简 HTML → 纯文本（去脚本样式、块级标签转换行、实体解码）。"""
    import html as html_mod

    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", html)
    html = re.sub(r"(?is)<br\s*/?>", "\n", html)
    html = re.sub(r"(?is)</(p|div|h[1-6]|li|tr|section)>", "\n", html)
    html = re.sub(r"(?s)<[^>]+>", "", html)
    text = html_mod.unescape(html)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _clean_pdf(data: bytes) -> str:
    """PDF 清洗（`pypdf`）。零 AI 调用；无文字层的扫描件会得到空文本。"""
    import io

    try:
        from pypdf import PdfReader
    except ImportError:
        raise BookCleanError("缺少 pypdf 依赖，无法处理 PDF") from None
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:
        raise BookCleanError(f"PDF 解析失败：{exc}") from exc
    pages: List[str] = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    return "\n\n".join(p.strip() for p in pages if p.strip())


def _clean_docx(data: bytes) -> str:
    """DOCX 清洗（`python-docx`）。"""
    import io

    try:
        import docx
    except ImportError:
        raise BookCleanError("缺少 python-docx 依赖，无法处理 DOCX") from None
    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:
        raise BookCleanError(f"DOCX 解析失败：{exc}") from exc
    blocks: List[str] = []
    for para in document.paragraphs:
        text = para.text.strip()
        if text:
            blocks.append(text)
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                blocks.append(" | ".join(cells))
    return "\n".join(blocks)


def clean_content(data: bytes, suffix: str) -> str:
    """按扩展名清洗，返回 UTF-8 纯文本（已归一化换行）。"""
    suffix = suffix.lower()
    if suffix == ".epub":
        return _clean_epub(data)
    if suffix == ".pdf":
        return _clean_pdf(data)
    if suffix == ".docx":
        return _clean_docx(data)
    if suffix in (".txt", ".md"):
        return _clean_txt(data)
    raise BookCleanError(f"暂不支持的格式：{suffix}")


def clean_book(
    src_path: Path,
    out_dir: Path,
    book_id: Optional[str] = None,
    title: Optional[str] = None,
) -> Dict[str, Any]:
    """
    清洗一本书，落盘 `.txt` + `.toc` 到 `out_dir`。

    返回 `{"book_id", "title", "txt_bytes", "toc_bytes", "chars", "sections"}`。
    """
    src_path = Path(src_path)
    if not src_path.exists():
        raise BookCleanError(f"源文件不存在：{src_path}")
    suffix = src_path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise BookCleanError(f"暂不支持的格式：{suffix}")

    raw = src_path.read_bytes()
    text = clean_content(raw, suffix)
    if not text.strip():
        # 扫描版 PDF 无文字层 —— 零 AI 调用时无法还原，如实报出
        raise BookCleanError(
            "这份文件没有可提取的文字（可能是扫描图片版），需要另行识别后才能入库"
        )

    book_id = book_id or src_path.stem
    title = title or src_path.stem
    toc = _build_toc(text)

    txt_bytes = text.encode("utf-8")
    toc_bytes = (
        json.dumps(
            {"schema_version": 1, "book_id": book_id, "title": title, "sections": toc},
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        + b"\n"
    )

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{book_id}.txt").write_bytes(txt_bytes)
    (out_dir / f"{book_id}.toc").write_bytes(toc_bytes)

    return {
        "book_id": book_id,
        "title": title,
        "txt_bytes": len(txt_bytes),
        "toc_bytes": len(toc_bytes),
        "chars": len(text),
        "sections": len(toc),
    }
