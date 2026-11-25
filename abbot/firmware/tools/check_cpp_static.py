#!/usr/bin/env python3
"""C/C++ 静态自检：两类「本机就能查出来、但 CI 一轮只暴露一个」的错。

固件编译只发生在 GitHub Actions，而 Actions 一轮只暴露**一个**编译错误，
反复试错既慢又费额度。这两类错都能在本机查出来：

1. **static 函数先用后声明**（C++ 要求声明在使用之前）
   例：前向声明写在文件第 243 行，使用点在第 113 行 → `was not declared in this scope`。

2. **designated initializer 乱序**
   GCC 在 C++ 模式下对 designated initializer 只支持「平凡」形式，设计器必须**升序**。
   例：枚举里 `PAGE_CITY=12` 排在 `PAGE_POMODORO=11` 之后，数组里却把 `[PAGE_CITY]`
   写在 `[PAGE_POMODORO]` 前面 → `sorry, unimplemented: non-trivial designated
   initializers not supported`。

用法（在 firmware/ 目录下）：
    python3 tools/check_cpp_static.py
退出码非 0 表示有问题。
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCAN = [
    "src/ui/ui_pages.cpp",
    "src/ui/ui_router.cpp",
    "src/ui/ui_ota_page.cpp",
    "src/ui/ui_display_port.cpp",
    "src/ui/ui_error.cpp",
    "src/net/net_api.cpp",
    "src/net/ota_client_esp.cpp",
    "src/net/net_dispatch.c",
    "src/net/net_req.c",
    "src/net/ota_client.c",
    "src/net/outbox.c",
    "src/net/wifi_model.c",
    "src/net/cards.c",
    "src/net/net_client.c",
    "src/ui/ui_data.c",
    "src/ui/ui_weather.c",
    "src/ui/ui_book_guard.c",
    "src/ui/keypad_editor.c",
]

DEF_RE = re.compile(r"^static\s+[\w\*\s]+?\b(\w+)\s*\([^;]*?\)\s*;?\s*$", re.M)
CALL_RE = re.compile(r"\b(\w+)\s*\(")
ENUM_VAL = {}


def collect_enums(text: str) -> None:
    """收集所有枚举常量的数值。"""
    for m in re.finditer(r"typedef enum\s*\{(.*?)\}\s*\w+;", text, re.S):
        val = 0
        for part in m.group(1).split(","):
            part = part.split("/*")[0].strip()
            if not part:
                continue
            if "=" in part:
                name, v = [x.strip() for x in part.split("=", 1)]
                try:
                    val = int(v, 0)
                except ValueError:
                    val += 1
                    continue
            else:
                name = part
            name = name.split("/*")[0].strip()
            if re.fullmatch(r"[A-Za-z_]\w*", name):
                ENUM_VAL.setdefault(name, val)
                val += 1


def check_fwd_decl(text: str, rel: str, problems: list) -> None:
    """static 函数是否在被调用之前就有声明。"""
    lines = text.splitlines()
    defs, decls = {}, {}
    for i, line in enumerate(lines, 1):
        m = DEF_RE.match(line)
        if not m:
            continue
        name = m.group(1)
        if name in ("if", "for", "while", "switch", "return", "sizeof"):
            continue
        j = i
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j < len(lines) and lines[j].strip().startswith("{"):
            defs.setdefault(name, i)
        elif line.rstrip().endswith(";"):
            decls.setdefault(name, i)

    for name, def_line in defs.items():
        earliest = decls.get(name)
        for i, line in enumerate(lines, 1):
            if i >= def_line:
                break
            stripped = line.strip()
            if stripped.startswith(("//", "*", "/*")):
                continue
            for m in CALL_RE.finditer(line):
                if m.group(1) != name:
                    continue
                if i == def_line:
                    continue
                prefix = line[: m.start()].strip()
                if prefix.endswith(("static", "inline")):
                    continue
                if earliest is None or i < earliest:
                    problems.append(
                        f"{rel}:{i} 调用 {name}()，但定义在第 {def_line} 行"
                        + (f"（已有前向声明在第 {earliest} 行，但声明在使用之后）" if earliest else "（**完全没有前向声明**）")
                    )
                break


def check_designated_order(text: str, rel: str, problems: list) -> None:
    """designated initializer 数组的设计器是否升序。"""
    for m in re.finditer(r"static const [^;]*?(\w+)\[\]\s*=\s*\{(.*?)\n\};", text, re.S):
        name, body = m.group(1), m.group(2)
        keys = []
        for dm in re.finditer(r"\[([^\]]+)\]", body):
            expr = dm.group(1).strip()
            if "-" in expr:
                a, b = [x.strip() for x in expr.split("-", 1)]
                if a in ENUM_VAL and b in ENUM_VAL:
                    expr = str(ENUM_VAL[a] - ENUM_VAL[b])
                else:
                    continue
            elif expr in ENUM_VAL:
                expr = str(ENUM_VAL[expr])
            else:
                continue
            try:
                keys.append(int(expr))
            except ValueError:
                continue
        if len(keys) < 2:
            continue
        if not all(keys[i] < keys[i + 1] for i in range(len(keys) - 1)):
            problems.append(f"{rel}::{name} designated initializer 乱序 → {keys}（GCC C++ 模式要求升序）")


def main() -> int:
    problems = []
    files = [ROOT / r for r in SCAN if (ROOT / r).exists()]
    for p in files:
        collect_enums(p.read_text(encoding="utf-8", errors="replace"))
    for p in files:
        text = p.read_text(encoding="utf-8", errors="replace")
        check_fwd_decl(text, str(p.relative_to(ROOT)), problems)
        check_designated_order(text, str(p.relative_to(ROOT)), problems)

    if problems:
        print("=== C/C++ 静态自检：发现问题 ===")
        for x in problems:
            print(f"  ❌ {x}")
        return 1
    print(f"=== C/C++ 静态自检：{len(files)} 个源文件全部通过 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
