"""账号桶：U<user_id>。

相册（CUSTOM_ALBUM）已下线，但这个桶还被小王子（CUSTOM_PRINCE）的角色图目录沿用 ——
角色图和相册照片都按「账号」而不是「设备」归档，所以桶的规则留在这里。
"""

from __future__ import annotations

# 账号目录前缀：U<user_id>
ACCOUNT_PREFIX = "U"


def account_bucket(user_id: int) -> str:
    """账号桶名：U<user_id>。"""
    return f"{ACCOUNT_PREFIX}{int(user_id)}"
