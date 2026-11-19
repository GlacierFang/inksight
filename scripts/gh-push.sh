#!/usr/bin/env bash
# GitHub 推送封装
#
# 本机事实：github.com 的 HTTPS git 端点被出口代理挡死（502 / 挂住不返回），
# 只有 SSH(TCP 22) 通。默认密钥 ~/.ssh/id_ed25519 对部分仓库无权限，
# eink-mini 用的是专用 deploy key ~/.ssh/id_ed25519_eink_mini（可写）。
#
# 用法：
#   scripts/gh-push.sh <repo-dir> [branch]
#
# 需要覆盖式推送时设 FORCE=1（重写历史用）。
#
# 行为：按 remote URL 选密钥，推送失败时自动换另一把重试一次。
set -euo pipefail

REPO_DIR="${1:?用法: gh-push.sh <repo-dir> [branch]}"
BRANCH="${2:-main}"
KEY_A="$HOME/.ssh/id_ed25519"
KEY_B="$HOME/.ssh/id_ed25519_eink_mini"

cd "$REPO_DIR"
REMOTE_URL="$(git remote get-url origin 2>/dev/null || echo "")"

# 按 remote 判断先用哪把钥匙：eink-mini 用专用 deploy key，其余用默认 key
if printf '%s' "$REMOTE_URL" | grep -q "eink-mini"; then
  KEYS=("$KEY_B" "$KEY_A")
else
  KEYS=("$KEY_A" "$KEY_B")
fi

for KEY in "${KEYS[@]}"; do
  [ -f "$KEY" ] || continue
  export GIT_SSH_COMMAND="ssh -i $KEY -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=10"
  PUSH_ARGS=(push origin "HEAD:refs/heads/$BRANCH")
  if [ "${FORCE:-0}" = "1" ]; then
    PUSH_ARGS=(--force-with-lease origin "HEAD:refs/heads/$BRANCH")
    echo ">>> 强制推送（FORCE=1）"
  fi
  echo ">>> 推送 $REPO_DIR ($BRANCH)，密钥 $(basename "$KEY")"
  if git "${PUSH_ARGS[@]}"; then
    echo ">>> 推送成功"
    exit 0
  fi
  echo ">>> 该密钥推送失败，换下一把"
done

echo "!!! 两把密钥都没推上去，检查 deploy key 权限" >&2
exit 1
