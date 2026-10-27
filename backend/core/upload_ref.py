"""上传引用（upload reference）的统一解析。

上传接口返回的是**规范相对路径** /api/uploads/<upload_id>，它与访问它的主机名、
站点 basePath（如 /ink）都无关。下面三种写法指向同一份本地文件：

    /api/uploads/<id>                       规范相对路径（当前契约）
    /ink/api/uploads/<id>                   带站点 basePath 的路径
    http://127.0.0.1:18834/api/uploads/<id>  旧版绝对 URL（历史数据）

后端不再猜测对外 URL：它不知道 nginx 的 basePath，猜出来的地址在浏览器侧
必然不可用。引用只用于定位本地文件，显示用的 URL 由前端用 apiUrl() 自行拼。
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

_UPLOAD_REF_RE = re.compile(r"(?:^|/)api/uploads/([^/?#]+)$")


def upload_ref_id(ref: str) -> str | None:
    """从上传引用里取出 upload id；不是上传引用时返回 None。

    这里不校验 id 是否为合法 uuid：调用方需要区分
    「这是一条上传引用，但文件已不存在」和「这根本不是上传引用」。
    """
    if not ref or not isinstance(ref, str):
        return None
    try:
        parsed = urlparse(ref)
    except ValueError:
        return None
    path = parsed.path or ""
    match = _UPLOAD_REF_RE.search(path)
    if not match:
        return None
    return match.group(1).strip() or None


def is_upload_ref(ref: str) -> bool:
    """是否是上传引用（不关心文件是否还存在）。"""
    return upload_ref_id(ref) is not None
