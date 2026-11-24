#!/usr/bin/env python3
"""从固件源码里扫出界面用到的字符, 裁剪出 LVGL 中文字库。

为什么需要它: LVGL 内置的 Montserrat 没有汉字, 中文界面必须自带字库;
把整份 Noto Sans CJK 塞进固件会让 Flash 爆掉 —— 全量 CJK 约 20MB+,
裁剪到界面实际用到的几百字后只有几十 KB。

用法:
    python3 tools/subset_font.py <ttf> <out-subset.ttf> <charset.txt>

charset.txt 每行一个字符 (可为空行, 以 # 开头为注释)。
"""
import sys


def read_charset(path):
    chars = set()
    with open(path, encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            chars.update(line)
    return chars


def main():
    if len(sys.argv) != 4:
        print(__doc__)
        return 2
    src_ttf, out_ttf, charset = sys.argv[1], sys.argv[2], sys.argv[3]

    from fontTools import subset
    from fontTools.ttLib import TTCollection, TTFont

    if src_ttf.lower().endswith(".ttc"):
        coll = TTCollection(src_ttf)
        font = None
        for f in coll.fonts:
            name = f["name"].getDebugName(4) or ""
            if "Noto Sans CJK SC" in name:
                font = f
                break
        if font is None:
            font = coll.fonts[0]
            print("fallback face:", font["name"].getDebugName(4))
    else:
        font = TTFont(src_ttf)

    chars = read_charset(charset)
    codes = {ord(c) for c in chars} | set(range(0x20, 0x7F))

    opts = subset.Options()
    opts.layout_features = []
    opts.name_IDs = ["*"]
    opts.notdef_outline = True
    opts.glyph_names = False
    opts.recalc_bounds = True
    opts.recalc_timestamp = False

    sub = subset.Subsetter(options=opts)
    sub.populate(unicodes=codes)
    sub.subset(font)
    font.save(out_ttf)
    print("subset glyphs =", len(font.getGlyphOrder()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
