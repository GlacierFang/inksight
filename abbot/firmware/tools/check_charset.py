#!/usr/bin/env python3
"""检查固件源码里**上屏文案**用到的中文字符是否都在 tools/font_charset.txt 里。

只看字符串字面量里的字符 —— 注释里的汉字不会显示到屏幕上，不需要进字库。

用法：python3 tools/check_charset.py
缺字会逐个列出来（带出处文件:行）。
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHARSET = os.path.join(ROOT, "tools", "font_charset.txt")

# 只需要扫 UI 层与提示文案（其它目录是协议/文档，不上屏）
SCAN_DIRS = [os.path.join(ROOT, "src", "ui")]

CJK = re.compile(r"[　-〿㐀-䶿一-鿿＀-￯]")

have = set()
for line in open(CHARSET, encoding="utf-8"):
    line = line.rstrip("\n")
    if not line or line.startswith("#"):
        continue
    have.update(line)


def strip_comments(text):
    """去掉 // 与 /* */ 注释，保留字符串字面量内容。"""
    out = []
    i = 0
    n = len(text)
    in_str = False
    while i < n:
        c = text[i]
        if in_str:
            if c == "\\":
                out.append(text[i:i + 2])
                i += 2
                continue
            if c == '"':
                in_str = False
            out.append(c)
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == "'":
            # 字符字面量：里面可能有 '"'，必须整段跳过
            out.append(c)
            i += 1
            while i < n:
                if text[i] == "\\":
                    out.append(text[i:i + 2])
                    i += 2
                    continue
                out.append(text[i])
                if text[i] == "'":
                    i += 1
                    break
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


missing = {}
scanned = 0
for d in SCAN_DIRS:
    for dirpath, _dirs, files in os.walk(d):
        if os.path.basename(dirpath) == "fonts":
            continue
        for f in files:
            if not f.endswith((".c", ".cpp", ".h")):
                continue
            path = os.path.join(dirpath, f)
            scanned += 1
            raw = open(path, encoding="utf-8").read()
            text = strip_comments(raw)
            # 只看字符串字面量
            for lit in re.findall(r'"((?:[^"\\]|\\.)*)"', text):
                for ch in CJK.findall(lit):
                    if ch not in have:
                        missing.setdefault(ch, []).append(
                            os.path.relpath(path, ROOT))

if not missing:
    print("charset OK: %d 个文件, 上屏文案无缺字" % scanned)
    sys.exit(0)

print("charset 缺字 %d 个:" % len(missing))
for ch in sorted(missing):
    print("  %r  %s" % (ch, ", ".join(sorted(set(missing[ch]))[:3])))
sys.exit(1)
