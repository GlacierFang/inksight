"""健康检查路由：给运维探活和上游集成测试用。

刻意不挂在任何鉴权后面——探活端点一旦要求登录，监控系统就废了。
也不查数据库：探活要的是「进程还活着」，不是「每张表都健康」。
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    return {"status": "ok"}
