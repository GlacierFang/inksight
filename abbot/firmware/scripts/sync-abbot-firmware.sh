#!/bin/bash
# abbot 固件：主副本 → /tmp/abbot-firmware 同步
# 用法：bash scripts/sync-abbot-firmware.sh
set -euo pipefail

SRC=/home/open/.openclaw/workspace/abbot/firmware
DST=/tmp/abbot-firmware

mkdir -p "$DST"
rsync -a --delete \
  --exclude '.git' \
  --exclude 'PLAN-local-caps.md' \
  "$SRC"/ "$DST"/

echo "synced $SRC -> $DST"
cd "$DST"
git status --short
