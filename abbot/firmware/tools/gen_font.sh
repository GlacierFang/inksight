#!/usr/bin/env bash
# 重新生成 LVGL 中文裁剪字库。
#
#   tools/gen_font.sh
#
# 依赖: fonttools (pip) + node 的 lv_font_conv (npm) + 系统里的 Noto Sans CJK。
# 生成物: src/ui/fonts/ui_font_cn_16.c  (提交进仓, CI 只编译不生成)
#
# 字符集: tools/font_charset.txt —— 界面用到的全部中文 + ASCII。
#         改了界面文案就要把新字加进这个文件, 否则屏幕上显示成空白。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/src/ui/fonts/ui_font_cn_16.c"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

SRC_TTF="${SRC_TTF:-/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc}"
SIZE="${SIZE:-16}"
BPP="${BPP:-4}"

echo "[gen_font] source: $SRC_TTF  size=$SIZE bpp=$BPP"

python3 "$ROOT/tools/subset_font.py" "$SRC_TTF" "$TMP/subset.ttf" "$ROOT/tools/font_charset.txt"

if [ -n "${LV_FONT_CONV:-}" ] && [ -x "${LV_FONT_CONV}" ]; then
    # 已经装好了就不必再走 npm（本机 npm 直连才能成，装一次就够）
    CONV="${LV_FONT_CONV}"
elif [ -x /tmp/lvtest/node_modules/.bin/lv_font_conv ]; then
    CONV=/tmp/lvtest/node_modules/.bin/lv_font_conv
else
    # npm 走直连: 本机出口代理会把 registry 挡成 403
    (cd "$TMP" && env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
        -u all_proxy -u ALL_PROXY npm install --silent lv_font_conv@1.5.3)
    CONV="$TMP/node_modules/.bin/lv_font_conv"
fi

echo "[gen_font] lv_font_conv: $CONV"

# --symbols 需要的是**字符本身**, 不是码位。
# 注意: 不能用 `fold -w1` 拆字符 —— 它按**字节**切, 会把 UTF-8 中文切碎,
# lv_font_conv 收到乱码后会把中文全部丢掉 (实测: 只剩 95 个 ASCII 字形)。
SYMBOLS=$(python3 - "$ROOT/tools/font_charset.txt" <<'PY'
import sys
chars = set()
for line in open(sys.argv[1], encoding="utf-8"):
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    chars.update(line)
chars.update(chr(c) for c in range(0x20, 0x7F))
sys.stdout.write("".join(sorted(chars)))
PY
)

(cd "$TMP" && "$CONV" \
    --font "$TMP/subset.ttf" \
    --size "$SIZE" --bpp "$BPP" --format lvgl --lv-include lvgl.h \
    --no-compress --no-kerning --lv-font-name ui_font_cn_16 \
    --symbols "$SYMBOLS" \
    -o "$OUT")

echo "[gen_font] wrote $OUT ($(wc -c < "$OUT") bytes)"
