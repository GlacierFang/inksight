from __future__ import annotations

import logging
import os
import re
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from api.routes import api_routers, page_routers
from api.shared import (
    RateLimitExceeded,
    _rate_limit_exceeded_handler,
    inksight_error_handler,
    lifespan,
    limiter,
)
from core.errors import InkSightError


def _build_allowed_hosts() -> list[str]:
    hosts = [
        "www.inksight.site",
        "inksight.site",
        "web.inksight.site",
        "localhost",
        "127.0.0.1",
        "::1",
        "test",
        "testserver",
    ]
    seen = set()
    result: list[str] = []
    extra = os.getenv("INKSIGHT_ALLOWED_HOSTS", "")
    for raw in [*hosts, *extra.split(",")]:
        host = raw.strip().lower()
        if not host:
            continue
        if host not in seen:
            seen.add(host)
            result.append(host)
    return result


def _build_cors_settings() -> tuple[list[str], str | None]:
    """Resolve allowed browser Origins for CORS.

    - Default: official web origins + local Next.js + Expo Web on 3000 / 8081.
    - INKSIGHT_CORS_ORIGINS: comma-separated extra origins (e.g. LAN IP for phone / Expo).
    - INKSIGHT_CORS_ALLOW_LAN=1: allow any http(s) Origin on private IPv4 + localhost (dev only).
    """
    defaults = [
        "https://www.inksight.site",
        "https://inksight.site",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8081",
        "http://127.0.0.1:8081",
    ]
    seen = set(defaults)
    origins = list(defaults)
    extra = os.getenv("INKSIGHT_CORS_ORIGINS", "")
    for part in extra.split(","):
        origin = part.strip()
        if origin and origin not in seen:
            seen.add(origin)
            origins.append(origin)

    origin_regex = None
    flag = os.getenv("INKSIGHT_CORS_ALLOW_LAN", "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        # RFC1918 + loopback; any port (Expo / dev servers on arbitrary ports).
        origin_regex = (
            r"^https?://("
            r"localhost|127\.0\.0\.1"
            r"|192\.168\.\d{1,3}\.\d{1,3}"
            r"|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
            r"|172\.(?:1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3}"
            r")(?::\d+)?$"
        )
    return origins, origin_regex


# Host 白名单放行的内网 IPv4 段（RFC1918 + 回环 + 链路本地）。
# 家里用局域网 IP 访问时，DHCP 换了地址也不能失效，所以按段放行，不逐个 IP 登记。
_LAN_HOST_RE = re.compile(
    r"^(?:127\.0\.0\.1|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3}"
    r"|169\.254\.\d{1,3}\.\d{1,3})$"
)


def _parse_host_header(raw: str | None) -> str | None:
    """取出 Host 里的主机名（小写），去掉端口与 IPv6 的方括号。"""
    if not raw:
        return None
    host = raw.strip()
    if host.startswith("["):
        end = host.find("]")
        if end < 0:
            return None
        return host[1:end].lower() or None
    if ":" in host:
        host = host.partition(":")[0]
    return host.lower() or None


class HostAllowMiddleware(BaseHTTPMiddleware):
    """Host 白名单：精确名单 + 可选的内网 IPv4 段。

    Starlette 自带的 TrustedHostMiddleware 只支持精确匹配与 `*.` 后缀，
    写不出「192.168.x.x」这种段 —— DHCP 换了地址就失效。
    这里自己实现一份，语义与 Starlette 对齐：
    不在名单 → 400 Invalid host header；`www.<host>` 命中 → 307 跳到带 www 的形式。
    """

    def __init__(self, app: FastAPI, allowed_hosts: list[str], allow_lan: bool = False):
        super().__init__(app)
        self.allowed_hosts = {h.lower() for h in allowed_hosts}
        self.allow_lan = allow_lan

    async def dispatch(self, request: Request, call_next):
        host = _parse_host_header(request.headers.get("host"))
        if host is None:
            return PlainTextResponse("Invalid host header", status_code=400)
        if host in self.allowed_hosts:
            return await call_next(request)
        if self.allow_lan and _LAN_HOST_RE.match(host):
            return await call_next(request)
        if f"www.{host}" in self.allowed_hosts:
            url = request.url
            netloc = f"www.{host}"
            if url.port:
                netloc = f"{netloc}:{url.port}"
            return RedirectResponse(url=str(url.replace(netloc=netloc)))
        return PlainTextResponse("Invalid host header", status_code=400)


class OriginValidationMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: FastAPI, allow_origins: list[str], allow_origin_regex: str | None = None):
        super().__init__(app)
        self.allow_origins = {origin.lower() for origin in allow_origins}
        self.allow_origin_regex = allow_origin_regex

    async def dispatch(self, request: Request, call_next):
        origin = request.headers.get("origin")
        if not origin:
            return await call_next(request)

        try:
            parsed = urlsplit(origin)
        except ValueError:
            return JSONResponse({"error": "origin_not_allowed"}, status_code=403)

        if not parsed.scheme or not parsed.netloc:
            return JSONResponse({"error": "origin_not_allowed"}, status_code=403)

        normalized = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
        forwarded_host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
        forwarded_proto = request.headers.get("x-forwarded-proto") or request.url.scheme
        same_origin = f"{forwarded_proto.lower()}://{forwarded_host.lower()}" if forwarded_host else ""
        if normalized == same_origin:
            return await call_next(request)

        if normalized in self.allow_origins:
            return await call_next(request)

        if self.allow_origin_regex:
            import re
            if re.match(self.allow_origin_regex, normalized):
                return await call_next(request)

        return JSONResponse({"error": "origin_not_allowed"}, status_code=403)


class _AccessLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        full_path = ""
        if isinstance(record.args, tuple) and len(record.args) >= 3:
            full_path = str(record.args[2] or "")
        if not full_path:
            try:
                full_path = record.getMessage()
            except Exception:
                full_path = ""
        return not (
            full_path.startswith("/api/device/")
            and (full_path.endswith("/state") or "/state?" in full_path)
        )


_uvicorn_access_logger = logging.getLogger("uvicorn.access")
_state_access_filter_present = any(isinstance(f, _AccessLogFilter) for f in _uvicorn_access_logger.filters)
if not _state_access_filter_present:
    _state_access_filter = _AccessLogFilter()
    _uvicorn_access_logger.addFilter(_state_access_filter)
    for _handler in _uvicorn_access_logger.handlers:
        _handler.addFilter(_state_access_filter)


_cors_origins, _cors_origin_regex = _build_cors_settings()
_allowed_hosts = _build_allowed_hosts()
# INKSIGHT_ALLOW_LAN_HOSTS=1：额外放行内网 IPv4 段（家用局域网 IP 访问）。
# 与 INKSIGHT_CORS_ALLOW_LAN 同一个路子，别把某个 DHCP 地址写死。
_allow_lan_hosts = os.getenv("INKSIGHT_ALLOW_LAN_HOSTS", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)

app = FastAPI(title="InkSight API", version="1.1.0", lifespan=lifespan)
app.state.limiter = limiter
app.add_middleware(
    HostAllowMiddleware,
    allowed_hosts=_allowed_hosts,
    allow_lan=_allow_lan_hosts,
)
app.add_middleware(
    OriginValidationMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=_cors_origin_regex,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=_cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_exception_handler(InkSightError, inksight_error_handler)

for router in api_routers:
    app.include_router(router, prefix="/api")
    app.include_router(router, prefix="/api/v1")

for router in page_routers:
    app.include_router(router)
