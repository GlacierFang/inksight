"""回顾（画廊）：账号作用域。

为什么不再是设备作用域
----------------------
回顾要的数据本来就按「模式 + 日期」存，两处都不含 MAC：
- 条目：runtime_cache/<state>/state.json
- 整屏图：runtime_cache/<state>/screens/<day>.png

旧路由 /device/{mac}/gallery 只是给同一份数据套了个设备外壳 ——
换一台设备请求，拿到的字节完全一样，MAC 没做任何隔离工作。
代价是：设备没绑定、没上线、屏幕坏着的时候，整条回顾链路连入口都没有。

所以回顾是**账号**的，不是设备的：登录即可看。
旧路由保留（兼容旧链接），内部走同一套 core/gallery.py，不产生第二份数据。

鉴权
----
需要登录（require_user）：接受 Authorization: Bearer 或 ink_session Cookie。
后者是为了 <img> 标签 —— img 带不了 Authorization 头，只能靠 Cookie。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from core.auth import require_user
from core.gallery import entries as gallery_entries
from core.gallery import modes as gallery_modes
from core.gallery import render_wide as gallery_render_wide
from core.gallery import screen_png as gallery_screen_png

logger = logging.getLogger(__name__)

router = APIRouter(tags=["gallery"])


def _form_of(mode_id: str) -> Optional[str]:
    """这个模式的形态（grid / text / wide）；不在收录范围返回 None。"""
    for item in gallery_modes():
        if item["mode_id"] == mode_id:
            return str(item.get("form") or "")
    return None


@router.get("/gallery")
async def gallery(
    mode: Optional[str] = Query(default=None),
    day: Optional[str] = Query(default=None),
    user_id: int = Depends(require_user),
):
    """回顾。

    不带 mode → 收录哪些模式（前端据此排 tab）；
    带 mode   → 该模式的往期条目（wide 形态按 day 现算那一张）。
    """
    if not mode:
        return {"modes": gallery_modes()}

    mode_id = mode.upper()
    form = _form_of(mode_id)
    if not form:
        raise HTTPException(status_code=404, detail="unknown gallery mode")

    if form == "wide":
        target = day or datetime.now().strftime("%Y-%m-%d")
        return {
            "mode": mode_id,
            "form": form,
            "entry": await gallery_render_wide(mode_id, target),
        }

    return {
        "mode": mode_id,
        "form": form,
        "entries": gallery_entries(mode_id),
    }


@router.get("/gallery/image")
async def gallery_image(
    mode: str = Query(...),
    day: str = Query(...),
    user_id: int = Depends(require_user),
):
    """某一天的整屏 PNG（回顾页主图）。

    回顾页要的是**整屏图**（状态栏、页脚、字段都在里面），不是素材图。
    """
    data = gallery_screen_png(mode.upper(), day)
    if not data:
        raise HTTPException(status_code=404, detail="no screen for that day")
    # 不让浏览器缓存：布局改过、整屏图重出之后，URL 没变，
    # 缓存住旧图的话回顾页上看到的还是老样子。
    return Response(
        content=data,
        media_type="image/png",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )
