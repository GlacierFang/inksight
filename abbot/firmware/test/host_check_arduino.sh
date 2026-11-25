#!/usr/bin/env bash
# 主机侧 Arduino 层语法检查 —— 不装 PlatformIO、不做固件编译
#
# 为什么需要它：固件编译只发生在 GitHub Actions（硬线），而 Actions 一轮只暴露**一个**
# 编译错误，反复试错既慢又费额度。本机用 host gcc + stub 头把 Arduino 层（net_api.cpp）
# 的语法/类型/格式串问题一次抓出来 —— 签名字段抄自 arduino-esp32 2.0.17 真实头文件。
#
# 范围：只做 `-fsyntax-only` 的**语法与警告**检查，不链接、不产出固件。
# 真正的固件构建仍以 GitHub Actions 的 PlatformIO 为准。
#
# 用法：bash test/host_check_arduino.sh
set -euo pipefail

cd "$(dirname "$0")/.."

SRC="src/net/net_api.cpp"
OUT=$(mktemp -d)
trap 'rm -rf "$OUT"' EXIT

echo "=== Arduino 层主机侧语法检查（stub 头，非固件编译）==="
g++ -fsyntax-only \
    -Wall -Wextra -Werror \
    -Wformat=2 -Wformat-truncation=2 \
    -I test/host_stubs -I include -I src \
    "$SRC"

echo "OK: $SRC 通过主机侧语法检查"

# UI 数据层是纯 C（不依赖 Arduino.h），一并做主机侧语法检查。
echo "=== UI 数据层主机侧语法检查 ==="
gcc -fsyntax-only \
    -Wall -Wextra -Werror \
    -I test/host_stubs -I include -I src \
    src/ui/ui_data.c

echo "OK: src/ui/ui_data.c 通过主机侧语法检查"

# OTA 设备侧（ota_client_esp.cpp）—— 依赖 Arduino / WiFi / Preferences / ESP-IDF，
# 全部走 host_stubs。stub 头里的签名逐条抄自真实头文件，
# 所以这一步能在本机把「类型不匹配 / 格式串 / 签名写错」一次抓出来。
echo "=== OTA 设备侧主机侧语法检查（stub 头，非固件编译）==="
g++ -fsyntax-only \
    -Wall -Wextra -Werror \
    -Wformat=2 \
    -I test/host_stubs -I include -I src \
    src/net/ota_client_esp.cpp

echo "OK: src/net/ota_client_esp.cpp 通过主机侧语法检查"
