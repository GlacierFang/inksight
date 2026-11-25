#!/usr/bin/env bash
# abbot OTA 一键发版（固件产物 → 校验 → 发布 → 自检）
#
#   scripts/ota_release.sh <版本号> "<changelog 文本>"
#
# 例：
#   scripts/ota_release.sh 1.0.1 "这一版补齐了……"
#
# 前提：固件源码里的 NET_FW_VERSION 已经抬到同一版本号，且那一轮 CI 已绿、
#       `fw-bin` 分支已经带上那一轮的产物。本脚本**不抬号、不编译**。
#
# 做的事（每步都失败即停）：
#   1. 从 `fw-bin` 分支取产物（本机默认 SSH key 拉不动，必须带 GIT_SSH_COMMAND）；
#   2. 三个 bin 自己算 sha256，与分支里的 manifest.txt **逐一比对**；
#   3. 用 publish_ota.py 发布到 storage/ota/（版本号不带 v 前缀）；
#   4. 核对产物里的固件版本串 == 清单版本（否则设备永远看不到这次发布）；
#   5. 跑 ota_selfcheck.py：新版本号 → 无更新；旧版本号 → 有更新 + 下载三处 sha256 一致。
set -euo pipefail

VERSION="${1:-}"
CHANGELOG="${2:-}"
if [ -z "$VERSION" ] || [ -z "$CHANGELOG" ]; then
    echo "用法: $0 <版本号> \"<changelog>\"" >&2
    exit 2
fi
case "$VERSION" in
    v*|V*)
        echo "版本号不能带 v 前缀（设备自报的是裸版本串，带 v 会永远收不到更新）" >&2
        exit 2
        ;;
esac

SERVER_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO="${ABBOT_FW_REPO:-/tmp/abbot-firmware}"
WORK="${ABBOT_FW_WORK:-/tmp/abbot-ota-release}"
export GIT_SSH_COMMAND="${GIT_SSH_COMMAND:-ssh -i $HOME/.ssh/id_ed25519 -o IdentitiesOnly=yes -o BatchMode=yes}"

step() { echo; echo "=== $* ==="; }

step "1/4 取 $REPO 的 fw-bin 产物"
cd "$REPO"
git fetch origin '+refs/heads/fw-bin:refs/remotes/origin/fw-bin'
FW_COMMIT="$(git rev-parse --short origin/fw-bin)"
echo "fw-bin 指向 $FW_COMMIT"
mkdir -p "$WORK"
for f in bootloader.bin firmware.bin partitions.bin manifest.txt; do
    git show "origin/fw-bin:$f" > "$WORK/$f"
done

step "2/4 三个 bin 的 sha256 与 manifest.txt 比对"
cd "$WORK"
EXPECTED_SHA="$(awk '/^### sha256/{flag=1;next} flag && NF {print $1"  "$2}' manifest.txt)"
ACTUAL_SHA="$(sha256sum bootloader.bin firmware.bin partitions.bin)"
if [ "$EXPECTED_SHA" != "$ACTUAL_SHA" ]; then
    echo "产物与 manifest 不一致，拒绝发布：" >&2
    diff <(echo "$EXPECTED_SHA") <(echo "$ACTUAL_SHA") >&2 || true
    exit 1
fi
echo "$ACTUAL_SHA"
echo "一致 ✅"

step "3/5 发布到 storage/ota/（版本 $VERSION）"
cd "$SERVER_ROOT"
PY="$SERVER_ROOT/../.venv/bin/python"
[ -x "$PY" ] || PY=python3
"$PY" scripts/publish_ota.py \
    --bin "$WORK/firmware.bin" \
    --version "$VERSION" \
    --changelog "$CHANGELOG"

step "4/5 产物里的固件版本串必须等于清单版本"
# 设备上报的是它自己固件里的 NET_FW_VERSION；如果产物里写的是别的版本号，
# 设备查更新时永远看不到这次发布（最坑的一种失败）。
if grep -aqF "$VERSION" "$WORK/firmware.bin"; then
    echo "产物里能搜到版本串 $VERSION ✅"
else
    echo "产物里搜不到版本串 $VERSION —— 固件的 NET_FW_VERSION 与清单版本对不上，拒绝发布！" >&2
    exit 1
fi

step "5/5 自检"
# 设备已是新版本 → 应当「无更新」，也不该再给 bin_url（否则白下一份旧包）
"$PY" scripts/ota_selfcheck.py --current-ver "$VERSION" --expect-no-update
echo
echo "--- 用旧版本号再查一次：应当有更新，且下载三处 sha256 一致 ---"
"$PY" scripts/ota_selfcheck.py --current-ver "0.9.0"

echo
echo "发布完成：$VERSION（产物来自 fw-bin $FW_COMMIT）"
