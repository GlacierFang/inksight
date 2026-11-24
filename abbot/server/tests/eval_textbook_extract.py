#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""中文教材抽取质量评估：pypdf(PDF) vs python-docx(DOCX)。

夹具用 MuPDF 内置 CJK 字体（china-s）生成 —— 编码形态贴近真实中文出版 PDF
（CID 字体 + ToUnicode 映射），不是图片 PDF。
另造一份「扫描件」PDF（纯位图无文字层）验证零文字层场景。
基准是同一份正文的纯 .txt。
"""
import io
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ---------- 基准正文（教材味：章节标题 + 段落 + 公式 + 中英数混排） ----------
CHAPTERS = [
    ("第一章 有理数", [
        "正数与负数：可以用正负号表示具有相反意义的量。",
        "有理数的分类：整数和分数统称有理数。",
        "绝对值是指一个数在数轴上对应点到原点的距离，|-3| = 3。",
    ]),
    ("第二章 整式的加减", [
        "同类项：所含字母相同，并且相同字母的指数也相同的项叫同类项。",
        "合并同类项的法则是把同类项的系数相加，字母和字母的指数不变。",
        "例：3x² - 5x + 2x² = 5x² - 5x，注意系数是 3 + 2 = 5。",
    ]),
    ("第三章 一元一次方程", [
        "只含一个未知数，且未知数的次数都是 1 的整式方程叫做一元一次方程。",
        "解方程的一般步骤：去分母、去括号、移项、合并同类项、系数化为 1。",
        "检验：把方程的解代入原方程，看左右两边是否相等。",
    ]),
]
PURE = []
for title, paras in CHAPTERS:
    PURE.append(title)
    PURE.extend(paras)
PURE_TEXT = "\n".join(PURE) + "\n"


def cjk_ratio(s: str) -> float:
    if not s:
        return 0.0
    return sum(1 for c in s if "\u4e00" <= c <= "\u9fff") / len(s)


def build_text_pdf() -> bytes:
    """带文字层的中文 PDF（CID 字体，接近真实出版 PDF）。"""
    import fitz

    doc = fitz.open()
    for title, paras in CHAPTERS:
        page = doc.new_page(width=595, height=842)  # A4
        y = 70
        page.insert_text((60, y), title, fontname="china-s", fontsize=18)
        y += 34
        for p in paras:
            for line in wrap_cjk(p, 38):
                page.insert_text((60, y), line, fontname="china-s", fontsize=11)
                y += 20
            y += 10
        y += 20
        page.insert_text((60, y), "— 第 %d 页 —" % (doc.page_count), fontname="china-s", fontsize=9)
    out = doc.tobytes()
    doc.close()
    return out


def build_scanned_pdf() -> bytes:
    """扫描件：把文字层栅格化成位图再重新封装 → 无文字层。"""
    import fitz

    src = fitz.open(stream=build_text_pdf(), filetype="pdf")
    dst = fitz.open()
    for i in range(src.page_count):
        pix = src[i].get_pixmap(dpi=120)
        p = dst.new_page(width=pix.width, height=pix.height)
        p.insert_image(p.rect, pixmap=pix)
    out = dst.tobytes()
    src.close()
    dst.close()
    return out


def build_docx() -> bytes:
    import docx

    d = docx.Document()
    for title, paras in CHAPTERS:
        d.add_heading(title, level=1)
        for p in paras:
            d.add_paragraph(p)
    t = d.add_table(rows=2, cols=3)
    t.cell(0, 0).text = "知识点"
    t.cell(0, 1).text = "公式"
    t.cell(0, 2).text = "难度"
    t.cell(1, 0).text = "合并同类项"
    t.cell(1, 1).text = "3x² + 2x² = 5x²"
    t.cell(1, 2).text = "基础"
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def wrap_cjk(text: str, width: int):
    """按字符宽度粗切，保留 ASCII 单词不被劈开。"""
    out, cur, w = [], "", 0
    for ch in text:
        cw = 1 if ord(ch) < 128 else 2
        if w + cw > width * 2 and cur:
            out.append(cur)
            cur, w = "", 0
        cur += ch
        w += cw
    if cur:
        out.append(cur)
    return out or [text]


# ---------- 评分 ----------
def score(name: str, extracted: str, baseline: str = PURE_TEXT):
    ex = extracted or ""
    base_chars = [c for c in baseline if not c.isspace()]
    ex_chars = [c for c in ex if not c.isspace()]
    # 字符级召回：基准里每个非空白字符是否出现在抽取结果中（按序比对）
    i = 0
    hit = 0
    base_seq = "".join(base_chars)
    ex_seq = "".join(ex_chars)
    for ch in base_seq:
        j = ex_seq.find(ch, i)
        if j >= 0:
            hit += 1
            i = j + 1
    recall = hit / len(base_seq) if base_seq else 0.0
    # 噪声：抽取里出现但基准没有的字符占比
    bset = set(base_seq)
    noise = sum(1 for c in ex_seq if c not in bset) / len(ex_seq) if ex_seq else 0.0
    # 中文串里被插入的空格（pypdf 常见副作用）
    sp = sum(1 for k in range(1, len(ex)) if ex[k - 1] == " " and ex[k] == " ")
    cn = cjk_ratio(ex)
    print("  %-22s 长度 %5d | 字符召回 %5.1f%% | 噪声 %4.1f%% | 中文占比 %4.1f%% | 连续空格 %d"
          % (name, len(ex), recall * 100, noise * 100, cn * 100, sp))
    return {"name": name, "len": len(ex), "recall": recall, "noise": noise,
            "cjk": cn, "text": ex}


def toc_check(txt: str, toc: list) -> bool:
    """校验 .toc 的每个字节偏移是否真落在章节标题开头。"""
    raw = txt.encode("utf-8")
    ok = True
    for entry in toc:
        off = entry["offset"]
        snippet = raw[off:off + 40].decode("utf-8", "ignore").split("\n")[0].strip()
        if not snippet.startswith(entry["title"]):
            ok = False
            print("    ✗ 偏移 %d 指向「%s」，期望「%s」" % (off, snippet[:24], entry["title"]))
    return ok


def main() -> int:
    sys.path.insert(0, "/home/open/.openclaw/workspace/abbot/server")
    from app.book_cleaner import clean_content, clean_book

    print("=" * 78)
    print("中文教材抽取质量评估")
    print("=" * 78)
    print("基准正文：%d 字符（中文占比 %.1f%%），%d 章"
          % (len(PURE_TEXT), cjk_ratio(PURE_TEXT) * 100, len(CHAPTERS)))

    pdf_bytes = build_text_pdf()
    scan_bytes = build_scanned_pdf()
    docx_bytes = build_docx()
    print("夹具：带文字层 PDF %d B | 扫描件 PDF %d B | DOCX %d B"
          % (len(pdf_bytes), len(scan_bytes), len(docx_bytes)))

    print("\n--- 1) 原始抽取（不经清洗器）---")
    from pypdf import PdfReader
    import docx as docxmod

    raw_pdf = "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(pdf_bytes)).pages)
    dd = docxmod.Document(io.BytesIO(docx_bytes))
    raw_docx = "\n".join(p.text for p in dd.paragraphs)
    s1 = score("pypdf（带文字层）", raw_pdf)
    s2 = score("python-docx", raw_docx)
    score("扫描件 PDF（pypdf）",
          "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(scan_bytes)).pages))

    print("\n--- 2) 过清洗器（生产路径 book_cleaner.clean_content）---")
    c_pdf = clean_content(pdf_bytes, ".pdf")
    c_docx = clean_content(docx_bytes, ".docx")
    c_scan = clean_content(scan_bytes, ".pdf")
    s3 = score("清洗后 PDF", c_pdf)
    s4 = score("清洗后 DOCX", c_docx)
    score("清洗后 扫描件 PDF", c_scan)
    print("  扫描件清洗结果是否为空文本：%s（长度 %d）" % (not c_scan.strip(), len(c_scan.strip())))
    print("  DOCX 表格内容是否进正文：%s" % ("合并同类项" in c_docx))

    print("\n--- 3) clean_book 全流程 + .toc 校验 ---")
    tmp = "/tmp/_eval_book"
    os.makedirs(tmp, exist_ok=True)
    for suffix, blob in ((".pdf", pdf_bytes), (".docx", docx_bytes), (".pdf", scan_bytes)):
        tag = "扫描件" if blob is scan_bytes else ""
        src = os.path.join(tmp, "教材%s%s" % (tag, suffix))
        with open(src, "wb") as fh:
            fh.write(blob)
        out_dir = os.path.join(tmp, "out_%s%s" % (tag, suffix.replace(".", "_")))
        os.makedirs(out_dir, exist_ok=True)
        try:
            res = clean_book(Path(src), Path(out_dir))
            # TOC 实体在落盘的 .toc 文件里（sections 只是计数）
            toc_raw = json.loads(
                (Path(out_dir) / ("%s.toc" % res["book_id"])).read_text(encoding="utf-8")
            )
            toc = toc_raw["sections"]
            txt = (Path(out_dir) / ("%s.txt" % res["book_id"])).read_text(encoding="utf-8")
            print("  %-12s 输出 %d 字节 .txt（%d 字符），.toc %d 条"
                  % (tag + suffix, res["txt_bytes"], res["chars"], len(toc)))
            print("     .toc 偏移量是否都落在章节标题开头：%s"
                  % ("是" if toc_check(txt, toc) else "否"))
            for e in toc:
                print("       %-14s @ %d" % (e["title"], e["offset"]))
        except Exception as exc:
            print("  %-12s clean_book 抛错：%s: %s"
                  % (tag + suffix, type(exc).__name__, exc))

    print("\n--- 4) 抽取文本样例（各 3 行）---")
    for label, txt in (("pypdf", raw_pdf), ("python-docx", raw_docx), ("清洗后PDF", c_pdf)):
        lines = [l for l in txt.split("\n") if l.strip()]
        print("  [%s]" % label)
        for l in lines[:3]:
            print("     " + l[:60])

    print("\n" + "=" * 78)
    print("结论数据（召回按基准字符顺序比对；噪声=抽取中基准没有的字符占比）")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
