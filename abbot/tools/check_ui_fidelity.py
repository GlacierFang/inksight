#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""abbot 模拟屏保真度门禁（机检脚本）。

比较两侧：
  固件侧  abbot/firmware/                —— 真机 UI 真相源
  模拟侧  abbot/server/web/index.html     —— 模拟后台「真机画面」Tab 的 canvas 复现

检查项：
  1. 页面集合    ui_router.h 的 PAGE_* 枚举   vs  index.html 的 PAGES.PAGE_*
  2. 设置页条目  固件设置页条目(含城市项)     vs  index.html PAGES.PAGE_SETTINGS 的 label
  3. 候选城市表  ui_weather.c 的 kCities[]    vs  index.html 的城市列表(中英文)
  4. 固件版本    NET_FW_VERSION              vs  index.html 上报的 fw_version

退出码：0 = 四项全过；1 = 有差异（含解析失败）。只用 python3 标准库。

用法：
  python3 abbot/tools/check_ui_fidelity.py
  python3 abbot/tools/check_ui_fidelity.py --firmware-root <dir> --simulator <index.html>
"""

from __future__ import annotations

import argparse
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_FIRMWARE_ROOT = os.path.normpath(os.path.join(_HERE, "..", "firmware"))
_DEFAULT_SIMULATOR = os.path.normpath(
    os.path.join(_HERE, "..", "server", "web", "index.html")
)


class ParseError(Exception):
    """源文件结构变了 —— 无法解析该项。门禁必须 FAIL，不允许静默跳过。"""


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------


def read_text(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError as exc:
        raise ParseError("无法读取文件 %s: %s" % (path, exc))


def strip_c_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def unique(seq):
    seen = set()
    out = []
    for item in seq:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def brace_block(text, brace_idx):
    """从 text[brace_idx] 处的 '{' 开始做花括号配对，返回块体（含首尾花括号）。"""
    if brace_idx >= len(text) or text[brace_idx] != "{":
        raise ParseError("期待 '{'，实际是 %r" % text[brace_idx:brace_idx + 24])
    depth = 0
    i = brace_idx
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in "\"'":
            quote = ch
            i += 1
            while i < n and text[i] != quote:
                i += 2 if text[i] == "\\" else 1
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[brace_idx:i + 1]
        i += 1
    raise ParseError("花括号未配对（块被截断）")


def js_block_after(text, anchor):
    """取 text 中 anchor 之后第一个 '{' 起头的配对块。"""
    idx = text.find("{", anchor)
    if idx < 0:
        raise ParseError("在 %r 之后找不到 '{'" % anchor)
    return brace_block(text, idx)


def cjk(s):
    return re.match(r"^[一-鿿]{2,6}$", s) is not None


# 城市页里常见的界面文案，避免被误当成城市名报成漂移
_CITY_STOPWORDS = set([
    "未设置", "城市", "所在城市", "选择城市", "选择所在城市", "确定", "取消", "返回",
    "提示", "已选择", "请选择", "当前城市", "设置",
])


def normalize_item(s):
    """去掉条目文案里的游标标记与首尾空白：'> 无线网络' → '无线网络'。"""
    return re.sub(r"^[>\s]+", "", s.strip()).strip()


def latin(s):
    return re.match(r"^[A-Za-z][A-Za-z .'\-]{1,30}$", s) is not None


# --------------------------------------------------------------------------
# JS 结构解析辅助（模拟屏侧）
# --------------------------------------------------------------------------


def array_literals(text):
    """单遍扫描，返回所有数组字面量的 (start, end) 区间（内层数组也包含）。"""
    out = []
    stack = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in "\"'`":
            q = ch
            i += 1
            while i < n:
                c2 = text[i]
                i += 2 if c2 == "\\" else 1
                if c2 == q:
                    break
            continue
        if ch == "[":
            stack.append(i)
        elif ch == "]" and stack:
            out.append((stack.pop(), i + 1))
        i += 1
    return out


def blank_js_noise(text):
    """把 JS 注释与正则字面量替换成等长空格，保持偏移不变。

    必须先做这一步：注释里的一个英文撇号就会让「字符串状态」错位，
    后续所有方括号配对全乱，数组边界会被解析成一整个大窗口。
    """
    out = list(text)
    i = 0
    n = len(text)
    prev = ""
    while i < n:
        ch = text[i]
        if ch in "\"'`":
            q = ch
            i += 1
            while i < n:
                c2 = text[i]
                if c2 == "\\":
                    i += 2
                    continue
                i += 1
                if c2 == q:
                    break
            prev = q
            continue
        if ch == "/" and i + 1 < n:
            nxt = text[i + 1]
            if nxt == "/":
                while i < n and text[i] != "\n":
                    out[i] = " "
                    i += 1
                continue
            if nxt == "*":
                while i < n and not (text[i] == "*" and i + 1 < n and text[i + 1] == "/"):
                    if text[i] != "\n":
                        out[i] = " "
                    i += 1
                if i < n:
                    out[i] = " "
                    out[i + 1] = " "
                    i += 2
                continue
            if prev in "(,=:[!&|?{};+*%~^" or prev == "":
                j = i + 1
                in_class = False
                while j < n:
                    c2 = text[j]
                    if c2 == "\\":
                        j += 2
                        continue
                    if c2 == "[":
                        in_class = True
                    elif c2 == "]":
                        in_class = False
                    elif c2 == "/" and not in_class:
                        break
                    elif c2 == "\n":
                        break
                    j += 1
                if j < n and text[j] == "/":
                    for k in range(i, min(j + 1, n)):
                        out[k] = " "
                    i = j + 1
                    while i < n and text[i].isalpha():
                        out[i] = " "
                        i += 1
                    prev = "/"
                    continue
        if not ch.isspace():
            prev = ch
        i += 1
    return "".join(out)


def split_top_level(text):
    """按顶层逗号切分数组元素。"""
    parts = []
    cur = []
    depth = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in "\"'`":
            q = ch
            cur.append(ch)
            i += 1
            while i < n:
                c2 = text[i]
                cur.append(c2)
                if c2 == "\\" and i + 1 < n:
                    cur.append(text[i + 1])
                    i += 2
                    continue
                i += 1
                if c2 == q:
                    break
            continue
        if ch in "[{(":
            depth += 1
            cur.append(ch)
        elif ch in "]})":
            depth -= 1
            cur.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    if "".join(cur).strip():
        parts.append("".join(cur))
    return parts


_CITY_ZH_KEYS = ("zh", "cn", "name", "label", "city")
_CITY_EN_KEYS = ("en", "enName", "en_name", "english", "wire")


def _kv(body, keys):
    """取 {key:'值'} 里的值。用反向引用配对引号：'Xi'an' 这种内含另一种引号的值不能被截断。"""
    for k in keys:
        m = re.search(
            r"(?<![A-Za-z0-9_$])['\"]?%s['\"]?\s*:\s*(['\"])(.*?)\1"
            % re.escape(k),
            body,
            re.S,
        )
        if m:
            return m.group(2).strip()
    return None


def parse_city_entry(elem):
    """从数组元素解析 (中文名, 英文地名)；不是城市条目返回 None。

    支持两种写法：
      {zh:'北京', en:'Beijing'}
      ['北京', 'Beijing']
    """
    e = elem.strip()
    if not e:
        return None
    if e.startswith("{") and e.endswith("}"):
        body = e[1:-1]
        zh = _kv(body, _CITY_ZH_KEYS)
        en = _kv(body, _CITY_EN_KEYS)
        if zh and en:
            return zh, en
        return None
    strs = [m.group(2) for m in re.finditer(r"(['\"])(.*?)\1", e, re.S)]
    if len(strs) >= 2:
        return strs[0].strip(), strs[1].strip()
    return None


# --------------------------------------------------------------------------
# 1. 页面集合
# --------------------------------------------------------------------------


def firmware_pages(firmware_root):
    """include/ui_router.h 里所有 typedef enum 块中的 PAGE_* 成员（排除 *_COUNT）。"""
    path = os.path.join(firmware_root, "include", "ui_router.h")
    text = strip_c_comments(read_text(path))
    blocks = list(re.finditer(r"typedef\s+enum[^{;]*\{", text))
    if not blocks:
        raise ParseError("在 %s 里找不到任何 typedef enum 块" % path)
    names = []
    for m in blocks:
        body = brace_block(text, text.find("{", m.start()))
        for line in body.splitlines():
            line = line.strip()
            if not line.startswith("PAGE_"):
                continue
            nm = re.match(r"(PAGE_[A-Z0-9_]+)", line)
            if not nm:
                continue
            nm = nm.group(1)
            if nm.endswith("_COUNT"):
                continue
            names.append(nm)
    if not names:
        raise ParseError("在 %s 的枚举块里没解析出任何 PAGE_* 成员" % path)
    return unique(names)


def simulator_pages(sim_path):
    text = read_text(sim_path)
    names = re.findall(r"\bPAGES\.(PAGE_[A-Z0-9_]+)\s*=", text)
    if not names:
        raise ParseError("在 %s 里没找到任何 PAGES.PAGE_* 页面定义" % sim_path)
    return unique(names)


# --------------------------------------------------------------------------
# 2. 设置页条目
# --------------------------------------------------------------------------

# 设置项语义槽位：允许两侧文案不同（固件「屏幕亮度」/ 模拟屏「屏幕背光亮度」），
# 但条目能力必须一一对应。关键词按顺序匹配，先命中先算。
_SETTING_SLOTS = [
    ("wifi", ("无线网络", "wifi", "wi-fi", "热点")),
    ("volume", ("音量", "播报", "喇叭", "volume")),
    ("backlight", ("亮度", "背光", "backlight", "brightness")),
    ("ota", ("固件升级", "固件版本", "ota", "升级")),
    ("battery", ("电池", "省电", "battery", "power")),
    ("about", ("关于", "about")),
    ("focus", ("专注", "番茄", "focus", "pomodoro")),
    ("city", ("城市", "city")),
    ("pair", ("配对", "绑定", "pair")),
    ("clock", ("校时", "时间", "clock")),
]


def setting_slot(label):
    low = label.lower()
    for slot, keys in _SETTING_SLOTS:
        for k in keys:
            if k.lower() in low:
                return slot
    return "未归类<%s>" % label


def firmware_settings_items(firmware_root):
    """固件设置页条目：ui_build_settings() 里的 items[] 数组 + 单条 list item。"""
    ui_dir = os.path.join(firmware_root, "src", "ui")
    if not os.path.isdir(ui_dir):
        raise ParseError("找不到固件 UI 目录 %s" % ui_dir)
    hits = []
    for fn in sorted(os.listdir(ui_dir)):
        if not fn.endswith((".c", ".cpp")):
            continue
        path = os.path.join(ui_dir, fn)
        text = strip_c_comments(read_text(path))
        for m in re.finditer(r"\bui_build_settings\s*\([^)]*\)\s*\{", text):
            hits.append((path, m.start()))
    if not hits:
        raise ParseError("在固件 src/ui/*.c(pp) 里找不到设置页构造函数 ui_build_settings()")
    path, pos = hits[0]
    text = strip_c_comments(read_text(path))
    body = js_block_after(text, pos)

    labels = []
    m = re.search(r"\bitems\s*\[\s*\]\s*=\s*\{", body)
    if m:
        arr = brace_block(body, body.find("{", m.start()))
        labels += re.findall(r'"((?:[^"\\]|\\.)*)"', arr)
    for lm in re.finditer(r"ui_make_list_item(?:_ex)?\s*\([^,]*,\s*\"((?:[^\"\\]|\\.)*)\"", body):
        labels.append(lm.group(1))
    if not labels:
        raise ParseError("在 %s 的 ui_build_settings() 里没解析出任何设置项" % path)
    return unique([normalize_item(l) for l in labels if l.strip()])


def simulator_settings_items(sim_path):
    """模拟屏 PAGES.PAGE_SETTINGS 的条目 label。"""
    text = blank_js_noise(read_text(sim_path))
    m = re.search(r"\bPAGES\.PAGE_SETTINGS\s*=", text)
    if not m:
        raise ParseError("在 %s 里找不到 PAGES.PAGE_SETTINGS" % sim_path)
    body = js_block_after(text, m.end())
    labels = re.findall(r"\blabel\s*:\s*['\"]([^'\"]+)['\"]", body)
    if not labels:
        labels = []
        for am in re.finditer(r"items\s*=\s*\[([^\]]*)\]", body):
            labels += re.findall(r"['\"]([^'\"]+)['\"]", am.group(1))
    if not labels:
        raise ParseError("在 %s 的 PAGES.PAGE_SETTINGS 里没解析出条目 label" % sim_path)
    return unique([normalize_item(l) for l in labels if l.strip()])


# --------------------------------------------------------------------------
# 3. 候选城市表
# --------------------------------------------------------------------------


def firmware_cities(firmware_root):
    """ui_weather.c 的 kCities[] → (中文名列表, 英文上报名列表)。"""
    path = os.path.join(firmware_root, "src", "ui", "ui_weather.c")
    text = strip_c_comments(read_text(path))
    m = re.search(r"\bkCities\s*\[\s*\]\s*=\s*\{", text)
    if not m:
        raise ParseError("在 %s 里找不到候选城市表 kCities[]" % path)
    body = brace_block(text, text.find("{", m.start()))
    pairs = re.findall(r"\{\s*\"([^\"]+)\"\s*,\s*\"([^\"]+)\"\s*\}", body)
    if not pairs:
        raise ParseError("在 %s 的 kCities[] 里没解析出城市条目" % path)
    zh = unique([a for a, _b in pairs])
    en = unique([b for _a, b in pairs])
    return zh, en


def simulator_cities(sim_path):
    """模拟屏候选城市表 → (中文名列表, 上报英文名列表, 备注)。

    只认「明确的候选城市数组」：数组内至少 6 条、且每条都能解析出中文名+英文地名。
    不靠变量名猜测，避免把手势数组之类的无关数组误判成城市表。
    """
    text = blank_js_noise(read_text(sim_path))
    zh_all = []
    en_all = []
    matched_arrays = 0
    for start, end in array_literals(text):
        window = text[start:end]
        if not re.search(r"[一-鿿]", window):
            continue
        elems = [e for e in split_top_level(window[1:-1]) if e.strip()]
        pairs = []
        for e in elems:
            p = parse_city_entry(e)
            if p and cjk(p[0]) and latin(p[1]):
                pairs.append(p)
        if len(pairs) >= 6:
            matched_arrays += 1
            zh_all += [p[0] for p in pairs]
            en_all += [p[1] for p in pairs]
    if matched_arrays == 0:
        raise ParseError(
            "在 %s 里找不到候选城市数组（需要 ≥6 条 {zh,en} 或 ['中文','English'] 条目）"
            % sim_path
        )
    zh = unique([z for z in zh_all if z not in _CITY_STOPWORDS])
    en = unique(en_all)
    return zh, en, ""


# --------------------------------------------------------------------------
# 4. 固件版本
# --------------------------------------------------------------------------


def firmware_version(firmware_root):
    path = os.path.join(firmware_root, "include", "net_api.h")
    text = strip_c_comments(read_text(path))
    m = re.search(r"#define\s+NET_FW_VERSION\s+\"([^\"]+)\"", text)
    if not m:
        raise ParseError("在 %s 里找不到 NET_FW_VERSION" % path)
    return m.group(1).strip().lstrip("vV")


def simulator_version(sim_path):
    """模拟屏上报的固件版本。

    支持两种写法：
      fw_version:'1.0.1'                （直接字面量）
      const FW_VERSION='1.0.1'; fw_version:FW_VERSION   （常量引用）
    返回值已去掉 v 前缀。
    """
    text = read_text(sim_path)
    m = re.search(r"fw_version\s*:\s*['\"]([^'\"]+)['\"]", text)
    if m:
        return m.group(1).strip().lstrip("vV")
    m = re.search(r"fw_version\s*:\s*((?:[A-Za-z_$][\w$]*\.)*[A-Za-z_$][\w$]*)", text)
    if m:
        ident = m.group(1).split(".")[-1]
        cm = re.search(
            r"(?:const|let|var)\s+%s\s*=\s*['\"]([^'\"]+)['\"]" % re.escape(ident), text
        )
        if cm:
            return cm.group(1).strip().lstrip("vV")
        raise ParseError(
            "fw_version 引用了常量 %s，但在文件里找不到它的字面量赋值" % ident
        )
    raise ParseError("在 %s 里找不到上报的 fw_version" % sim_path)


# --------------------------------------------------------------------------
# 报告
# --------------------------------------------------------------------------


def clip(text, limit=108):
    text = text or ""
    return text if len(text) <= limit else text[:limit - 3] + "..."


def diff(a, b):
    sb = set(b)
    sa = set(a)
    return [x for x in a if x not in sb], [x for x in b if x not in sa]


class Report(object):
    def __init__(self):
        self.items = []

    def add(self, title, lines, ok):
        self.items.append((title, lines, ok))

    def print(self):
        bad = 0
        for title, lines, ok in self.items:
            if ok:
                print("[PASS] %s" % title)
            else:
                bad += 1
                print("[FAIL] %s" % title)
                for ln in lines:
                    print("       %s" % clip(ln))
        print("")
        if bad:
            print("FAIL: %d 项检查存在差异（模拟屏与固件 UI 已漂移）" % bad)
        else:
            print("PASS: 模拟屏与固件 UI 一致")
        return bad


def main(argv=None):
    ap = argparse.ArgumentParser(description="abbot 模拟屏保真度门禁")
    ap.add_argument("--firmware-root", default=_DEFAULT_FIRMWARE_ROOT,
                    help="固件目录（默认 %(default)s）")
    ap.add_argument("--simulator", default=_DEFAULT_SIMULATOR,
                    help="模拟屏 index.html（默认 %(default)s）")
    args = ap.parse_args(argv)

    print("abbot 模拟屏保真度门禁")
    print("  固件侧: %s" % args.firmware_root)
    print("  模拟侧: %s" % args.simulator)
    print("")

    rep = Report()

    # --- 1. 页面集合 ---
    try:
        fw_pages = firmware_pages(args.firmware_root)
        sim_pages = simulator_pages(args.simulator)
        only_fw, only_sim = diff(fw_pages, sim_pages)
        lines = []
        if only_fw:
            lines.append("固件独有: %s" % ", ".join(only_fw))
        if only_sim:
            lines.append("模拟屏独有: %s" % ", ".join(only_sim))
        rep.add("页面集合", lines, not lines)
    except ParseError as exc:
        rep.add("页面集合", ["无法解析该项: %s" % exc], False)

    # --- 2. 设置页条目 ---
    try:
        fw_items = firmware_settings_items(args.firmware_root)
        sim_items = simulator_settings_items(args.simulator)
        fw_slots = {}
        for x in fw_items:
            fw_slots.setdefault(setting_slot(x), []).append(x)
        sim_slots = {}
        for x in sim_items:
            sim_slots.setdefault(setting_slot(x), []).append(x)
        only_fw = [s for s in fw_slots if s not in sim_slots]
        only_sim = [s for s in sim_slots if s not in fw_slots]
        wording = []
        for s in fw_slots:
            if s in sim_slots and set(fw_slots[s]) != set(sim_slots[s]):
                wording.append("%s[固件 %s / 模拟屏 %s]"
                               % (s, "/".join(fw_slots[s]), "/".join(sim_slots[s])))
        lines = ["条目数: 固件 %d / 模拟屏 %d" % (len(fw_items), len(sim_items))]
        if only_fw:
            lines.append("固件独有能力: %s" % ", ".join(
                "%s(%s)" % (s, "/".join(fw_slots[s])) for s in only_fw))
        if only_sim:
            lines.append("模拟屏独有能力: %s" % ", ".join(
                "%s(%s)" % (s, "/".join(sim_slots[s])) for s in only_sim))
        if wording:
            lines.append("文案不一致: %s" % ", ".join(wording))
        rep.add("设置页条目", lines, not only_fw and not only_sim)
    except ParseError as exc:
        rep.add("设置页条目", ["无法解析该项: %s" % exc], False)

    # --- 3. 候选城市表 ---
    try:
        fw_zh, fw_en = firmware_cities(args.firmware_root)
        sim_zh, sim_en, note = simulator_cities(args.simulator)
        only_fw, only_sim = diff(fw_zh, sim_zh)
        only_fw_en, only_sim_en = diff(fw_en, sim_en)
        lines = []
        if note:
            lines.append(note)
        if only_fw:
            lines.append("固件独有(中文名): %s" % ", ".join(only_fw))
        if only_sim:
            lines.append("模拟屏独有(中文名): %s" % ", ".join(only_sim))
        if only_fw_en:
            lines.append("固件独有(上报英文名): %s" % ", ".join(only_fw_en))
        if only_sim_en:
            lines.append("模拟屏独有(上报英文名): %s" % ", ".join(only_sim_en))
        rep.add("候选城市表", lines, not (only_fw or only_sim or only_fw_en or only_sim_en))
    except ParseError as exc:
        rep.add("候选城市表", ["无法解析该项: %s" % exc], False)

    # --- 4. 固件版本 ---
    try:
        fw_ver = firmware_version(args.firmware_root)
        sim_ver = simulator_version(args.simulator)
        lines = []
        if fw_ver != sim_ver:
            lines.append("固件=%s  模拟屏=%s" % (fw_ver, sim_ver))
        rep.add("固件版本", lines, fw_ver == sim_ver)
    except ParseError as exc:
        rep.add("固件版本", ["无法解析该项: %s" % exc], False)

    bad = rep.print()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
