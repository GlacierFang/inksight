from __future__ import annotations

import asyncio
import copy
import hashlib
import io
import json
import logging
import os
import random
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from PIL import Image, ImageDraw, ImageFont
from core.patterns.utils import load_font

try:  # pragma: no cover - exercised implicitly at import time
    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded
    from slowapi.util import get_remote_address
except ImportError:  # pragma: no cover
    class _DummyLimiter:
        def __init__(self, *args, **kwargs):
            pass

        def limit(self, *args, **kwargs):
            def decorator(func):
                return func

            return decorator

    def get_remote_address(request: Request) -> str:
        client = getattr(request, "client", None)
        return getattr(client, "host", "unknown") if client else "unknown"

    class RateLimitExceeded(Exception):
        """Fallback rate limit exception (never actually raised without slowapi)."""

    async def _rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
        return JSONResponse(
            status_code=429,
            content={
                "error": "rate_limit_unavailable",
                "message": "Rate limiting is not enabled on this server.",
            },
        )

    Limiter = _DummyLimiter  # type: ignore

from core.auth import (
    optional_user,
    require_device_token,
    validate_mac_param,
)
from core.cache import content_cache
from core.config import (
    DEFAULT_CITY,
    DEFAULT_IMAGE_MODEL,
    DEFAULT_IMAGE_PROVIDER,
    DEFAULT_LLM_MODEL,
    DEFAULT_LLM_PROVIDER,
    DEFAULT_MODES,
    DEFAULT_REFRESH_INTERVAL,
)
from core.config_store import (
    get_active_config,
    get_cycle_index,
    get_device_membership,
    get_device_state,
    get_user_role,
    init_db,
    set_cycle_index,
    update_device_state,
)
from core.context import calc_battery_pct, extract_location_settings, get_date_context, get_weather
from core.pipeline import generate_and_render, get_effective_mode_config
from core.renderer import image_to_bmp_bytes
from core.stats_store import (
    get_latest_battery_voltage,
    init_stats_db,
    log_heartbeat,
    log_render,
    save_render_content,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

DISCOVERY_WINDOW_MINUTES = 15
ONLINE_WINDOW_MINUTES = 15

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


@asynccontextmanager
async def lifespan(app):
    await init_db()
    await init_stats_db()
    from core.cache import init_cache_db
    from core.db import close_all
    from core.static_store import init_static_tables, migrate_device_state_columns

    await init_cache_db()
    await init_static_tables()
    await migrate_device_state_columns()

    yield

    await close_all()


def _rate_limit_key(request: Request) -> str:
    mac = request.query_params.get("mac")
    if mac:
        return f"mac:{mac}"
    return get_remote_address(request)


class _NoopLimiter:
    def __init__(self, *args, **kwargs):
        pass

    def limit(self, *args, **kwargs):
        def decorator(func):
            return func

        return decorator


try:
    limiter = Limiter(key_func=_rate_limit_key, storage_uri="memory://")
except Exception as exc:  # pragma: no cover - depends on optional runtime dependency
    logger.warning("Rate limiter disabled due to init error: %s", exc)
    limiter = _NoopLimiter()


async def inksight_error_handler(request: Request, exc):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": type(exc).__name__, "message": exc.message},
    )


def build_claim_url(request: Request, token: str) -> str:
    override = os.environ.get("INKSIGHT_WEB_BASE_URL", "").rstrip("/")
    if override:
        return f"{override}/claim?token={token}"
    host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or request.url.netloc
        or ""
    ).strip()
    if "inksight.site" not in host.lower():
        return ""
    scheme = (request.headers.get("x-forwarded-proto") or request.url.scheme or "https").strip()
    return f"{scheme}://{host}/claim?token={token}"


async def resolve_user_id(request: Request, ink_session: Optional[str]) -> Optional[int]:
    return await optional_user(request, ink_session)


async def require_membership_access(
    request: Request,
    mac: str,
    ink_session: Optional[str],
    *,
    owner_only: bool = False,
) -> dict:
    from core.i18n import detect_lang_from_request, msg

    lang = detect_lang_from_request(request)
    mac = validate_mac_param(mac, lang)
    user_id = await resolve_user_id(request, ink_session)
    if user_id is None:
        logger.warning("[AUTH] require_membership_access: user_id is None for mac=%s", mac)
        raise HTTPException(status_code=401, detail=msg("auth.login_required", lang))
    membership = await get_device_membership(mac, user_id)
    if not membership:
        logger.warning("[AUTH] require_membership_access: no membership for mac=%s user_id=%s", mac, user_id)
        raise HTTPException(status_code=403, detail=msg("auth.no_device_access", lang))
    if owner_only and membership.get("role") != "owner":
        raise HTTPException(status_code=403, detail=msg("auth.owner_only", lang))
    return membership


async def ensure_web_or_device_access(
    request: Request,
    mac: str,
    x_device_token: Optional[str],
    ink_session: Optional[str],
    *,
    owner_only: bool = False,
    allow_device_token: bool = True,
) -> dict:
    mac = validate_mac_param(mac)
    logger.info("[AUTH] ensure_web_or_device_access: mac=%s, has_device_token=%s, has_ink_session=%s",
                mac, bool(x_device_token), bool(ink_session))
    if allow_device_token and x_device_token:
        await require_device_token(mac, x_device_token)
        return {"mode": "device", "role": "device"}
    membership = await require_membership_access(request, mac, ink_session, owner_only=owner_only)
    return {"mode": "user", **membership}


_preview_push_queue: dict[str, dict] = {}
_preview_push_queue_lock = asyncio.Lock()

# 2026-11-17：系统默认能力下线，智能时段改用保留的自研模式。
_SMART_TIME_SLOTS = [
    (6, 9, ["CUSTOM_DAYOVERVIEW", "CUSTOM_ANT"]),
    (9, 12, ["CUSTOM_DAILY_TRIO", "CUSTOM_TANGPING"]),
    (12, 14, ["CUSTOM_SUN_MOON", "CUSTOM_PI_STATUS"]),
    (14, 18, ["CUSTOM_PRINCE", "CUSTOM_DAILY_TRIO"]),
    (18, 21, ["CUSTOM_HAICUOTU", "CUSTOM_TANGPING"]),
    (21, 24, ["CUSTOM_SUN_MOON", "CUSTOM_POETRY_DAY"]),
    (0, 6, ["CUSTOM_SUN_MOON", "CUSTOM_ANT"]),
]


async def choose_persona_from_config(config: dict, peek_next: bool = False) -> str:
    modes = config.get("modes", DEFAULT_MODES) or DEFAULT_MODES
    strategy = config.get("refresh_strategy", "random")

    if strategy == "cycle":
        mac = config.get("mac", "default")
        idx = await get_cycle_index(mac)
        persona = modes[idx % len(modes)]
        if not peek_next:
            await set_cycle_index(mac, idx + 1)
        return persona

    if strategy == "time_slot":
        hour = datetime.now().hour
        rules = config.get("time_slot_rules", [])
        for rule in rules:
            start_h = rule.get("startHour", 0)
            end_h = rule.get("endHour", 24)
            rule_modes = rule.get("modes", [])
            if start_h <= hour < end_h and rule_modes:
                available = [mode for mode in rule_modes if mode in modes]
                if available:
                    return random.choice(available)
        return random.choice(modes)

    if strategy == "smart":
        hour = datetime.now().hour
        for start_h, end_h, candidates in _SMART_TIME_SLOTS:
            if start_h <= hour < end_h:
                available = [mode for mode in candidates if mode in modes]
                if available:
                    return random.choice(available)
        return random.choice(modes)

    return random.choice(modes)


async def advance_to_next_mode(mac: Optional[str], config: dict) -> str:
    modes = config.get("modes", DEFAULT_MODES)
    if not modes:
        return "CUSTOM_DAYOVERVIEW"

    state = await get_device_state(mac) if mac else None
    current = state.get("last_persona", "") if state else ""
    idx = (modes.index(current) + 1) % len(modes) if current in modes else 0
    persona = modes[idx]
    if mac:
        await set_cycle_index(mac, idx + 1)
    return persona


async def consume_pending_mode(mac: str) -> Optional[str]:
    try:
        state = await get_device_state(mac)
        if state and state.get("pending_mode"):
            mode = state["pending_mode"]
            await update_device_state(mac, pending_mode="")
            return mode
    except (OSError, ValueError, TypeError):
        logger.warning("[PENDING_MODE] Failed to consume for %s", mac, exc_info=True)
    return None


async def resolve_mode(
    mac: Optional[str],
    config: Optional[dict],
    persona_override: Optional[str],
    *,
    force_next: bool = False,
) -> str:
    from core.mode_registry import get_registry

    registry = get_registry()
    # Always pass mac to is_supported to ensure device isolation
    if mac and not persona_override:
        pending = await consume_pending_mode(mac)
        if pending and registry.is_supported(pending.upper(), mac):
            return pending.upper()

    if persona_override and registry.is_supported(persona_override.upper(), mac):
        return persona_override.upper()

    if config:
        if force_next:
            return await advance_to_next_mode(mac, config)
        return await choose_persona_from_config(config)

    return random.choice(
        ["CUSTOM_DAYOVERVIEW", "CUSTOM_DAILY_TRIO", "CUSTOM_POETRY_DAY", "CUSTOM_SUN_MOON"]
    )


async def build_image(
    v: float,
    mac: Optional[str],
    persona_override: Optional[str] = None,
    *,
    screen_w: int,
    screen_h: int,
    force_next: bool = False,
    skip_cache: bool = False,
    preview_city_override: Optional[str] = None,
    preview_mode_override: Optional[dict] = None,
    preview_memo_text: Optional[str] = None,
    preview_memo_settings: Optional[dict] = None,
    preview_ui_language: Optional[str] = None,
    current_user_id: Optional[int] = None,
    intent_only: bool = False,
    colors: int = 2,
    owner_account: str = "",
    day_override: Optional[str] = None,
    archive_screen: bool = False,
    phase_override: Optional[str] = None,
):
    from core.mode_registry import get_registry

    battery_pct = calc_battery_pct(v)
    config = await get_active_config(mac) if mac else None
    persona = await resolve_mode(mac, config, persona_override, force_next=force_next)

    # 设备自己勾选的模式 = 它自己的订阅。设备来取图，说明它现在是在用设备。
    # 预览（无设备）不传任何模式 —— 预览照样按全局订阅集合判，不豁免。
    device_modes: set[str] = set()
    if mac and isinstance(config, dict):
        _raw_modes = config.get("modes") or []
        if isinstance(_raw_modes, str):
            _raw_modes = [x for x in _raw_modes.split(",")]
        device_modes = {str(x).upper().strip() for x in _raw_modes if str(x).strip()}

    owner_user_id: Optional[int] = None
    if mac:
        from core.config_store import get_device_owner

        owner = await get_device_owner(mac)
        if owner:
            try:
                owner_user_id = int(owner.get("user_id"))
            except (TypeError, ValueError):
                owner_user_id = None

    # 相册按账号存：设备渲染时用这台设备 owner 的账号相册
    # （和无设备体验页是同一份，设备之间也不各存一份）
    if not owner_account and owner_user_id:
        from core.account import account_bucket
        owner_account = account_bucket(owner_user_id)

    registry = get_registry()
    
    # Load device owner's custom modes if needed (for device rendering)
    # Only load modes for the specific device to avoid loading modes from other devices
    if mac and not registry.is_supported(persona, mac):
        if owner_user_id:
            await registry.load_user_custom_modes(owner_user_id, mac)
            logger.debug(f"[BUILD_IMAGE] Loaded custom modes for device owner {owner_user_id} (device {mac})")
        # Fallback: configured persona may reference a deleted/missing custom mode.
        if not registry.is_supported(persona, mac):
            cfg_modes = config.get("modes", []) if isinstance(config, dict) else []
            fallback_persona = None
            if isinstance(cfg_modes, list):
                for m in cfg_modes:
                    cand = str(m).upper().strip()
                    if registry.is_supported(cand, mac):
                        fallback_persona = cand
                        break
            persona = fallback_persona or "CUSTOM_DAYOVERVIEW"
            logger.warning(
                "[BUILD_IMAGE] Unsupported persona for %s, fallback to %s",
                mac,
                persona,
            )
    
    # For Web preview without mac: load all custom modes for the current user
    # Note: This may cause conflicts if user has same mode_id on different devices
    # but we unregister before loading, so the last loaded will take precedence
    if not mac and current_user_id is not None and not registry.is_supported(persona):
        await registry.load_user_custom_modes(current_user_id, None)
        logger.debug(f"[BUILD_IMAGE] Loaded all custom modes for user {current_user_id} (Web preview without device)")
    
    mode_info = registry.get_mode_info(persona)
    is_mode_cacheable = bool(mode_info.cacheable) if mode_info else True

    # ── 服务端内置 AI 配置（用户零配置）────────────────────────────
    # 文字：MiniMax M3；图像：MiniMax H3。凭证统一由后端 .env 提供，
    # 不再接受用户级 BYOK 覆盖（个人端模型配置能力已下线）。
    selected_config_user_id: Optional[int] = None
    config = dict(config or {})
    config["llm_provider"] = DEFAULT_LLM_PROVIDER
    config["llm_model"] = DEFAULT_LLM_MODEL
    config["image_provider"] = DEFAULT_IMAGE_PROVIDER
    config["image_model"] = DEFAULT_IMAGE_MODEL
    config.pop("user_api_key", None)
    config.pop("user_image_api_key", None)
    config.pop("llm_base_url", None)
    config.pop("llm_access_mode", None)

    # 是否为需要 LLM 的 JSON 模式
    # 需要检查顶层 content 类型，以及 composite 模式中的 steps
    # 涉及 LLM 调用的类型：
    # - llm: 直接调用 LLM
    # - llm_json: 调用 LLM 并解析 JSON
    # - image_gen: 调用 LLM 生成标题（在 generate_artwall_content 中）
    # - composite: 递归检查 steps 中是否包含上述类型
    llm_required = False
    json_mode = registry.get_json_mode(persona, mac)
    if json_mode and isinstance(json_mode.definition, dict):
        content_def = json_mode.definition.get("content", {}) or {}
        ctype = content_def.get("type")
        logger.debug(
            "[LLM MODE] Checking mode %s: ctype=%s, json_mode exists=%s",
            persona,
            ctype,
            json_mode is not None,
        )
        if ctype in ("llm", "llm_json", "image_gen"):
            llm_required = True
            logger.debug("[LLM MODE] Mode %s requires LLM (direct type: %s)", persona, ctype)
        elif ctype == "composite":
            # 递归检查 composite 模式中的 steps 是否包含需要 LLM 的类型
            steps = content_def.get("steps", [])
            logger.debug("[LLM MODE] Mode %s is composite, checking %d steps", persona, len(steps) if isinstance(steps, list) else 0)
            if isinstance(steps, list):
                for step in steps:
                    if isinstance(step, dict):
                        step_type = step.get("type")
                        logger.debug("[LLM MODE] Checking step type: %s", step_type)
                        if step_type in ("llm", "llm_json", "image_gen"):
                            llm_required = True
                            logger.debug("[LLM MODE] Mode %s requires LLM (composite step: %s)", persona, step_type)
                            break
                        # 如果 step 本身也是 composite，需要递归检查（虽然当前没有这种嵌套，但为了完整性）
                        elif step_type == "composite":
                            nested_steps = step.get("steps", [])
                            if isinstance(nested_steps, list):
                                for nested_step in nested_steps:
                                    if isinstance(nested_step, dict):
                                        nested_type = nested_step.get("type")
                                        if nested_type in ("llm", "llm_json", "image_gen"):
                                            llm_required = True
                                            logger.debug("[LLM MODE] Mode %s requires LLM (nested composite step: %s)", persona, nested_type)
                                            break
                                if llm_required:
                                    break
    if not llm_required:
        logger.debug("[LLM MODE] Mode %s does NOT require LLM", persona)

    if preview_city_override or preview_mode_override or preview_memo_text or preview_memo_settings:
        config = copy.deepcopy(config or {})
        mode_overrides = dict(config.get("mode_overrides") or {})
        current_mode_override = dict(mode_overrides.get(persona) or {})
        if preview_city_override:
            config["city"] = preview_city_override
            current_mode_override["city"] = preview_city_override
        if isinstance(preview_mode_override, dict) and preview_mode_override:
            current_mode_override.update(preview_mode_override)
        mode_overrides[persona] = current_mode_override
        config["mode_overrides"] = mode_overrides
        config["modeOverrides"] = mode_overrides
        if persona == "MEMO":
            memo_text = current_mode_override.get("memo_text")
            if isinstance(memo_text, str) and memo_text.strip():
                config["memo_text"] = memo_text.strip()
                config["memoText"] = memo_text.strip()
            if isinstance(preview_memo_text, str) and preview_memo_text.strip():
                memo_clean = preview_memo_text.strip()
                current_mode_override["memo_text"] = memo_clean
                mode_overrides[persona] = current_mode_override
                config["mode_overrides"] = mode_overrides
                config["modeOverrides"] = mode_overrides
                config["memo_text"] = memo_clean
                config["memoText"] = memo_clean
            if isinstance(preview_memo_settings, dict) and preview_memo_settings:
                ms = dict(current_mode_override.get("mode_settings") or {})
                ms.update(preview_memo_settings)
                current_mode_override["mode_settings"] = ms
                mode_overrides[persona] = current_mode_override
                config["mode_overrides"] = mode_overrides
                config["modeOverrides"] = mode_overrides

    # 无设备预览（/preview 等）：按页面站点语言（中/英）决定 mode_language。
    # 设备配置页预览（有 mac）：沿用 configs 中保存的 mode_language，不被 ui_language 覆盖。
    if not mac and preview_ui_language in ("zh", "en"):
        config = copy.deepcopy(config or {})
        config["mode_language"] = preview_ui_language
        config["modeLanguage"] = preview_ui_language

    cache_hit = False
    img = None

    # 每日内容缓存只在「没有临时覆盖」时用：预览时用户改了版式/参数，
    # 那份内容跟设备看到的那张不是一回事，用缓存会给出错的结果。
    # 每日内容缓存只给设备路径用：
    # - 预览不入缓存（用户要的是「现在看看」，不是复用设备那份）
    # - 有临时覆盖（改了城市/版式/便签）不入缓存，那不是设备看到的那张
    # 但闸门对预览同样生效 —— 预览不豁免。
    use_daily_cache = bool(mac) and not (
        day_override
        or preview_mode_override
        or preview_memo_text
        or preview_memo_settings
        or skip_cache
    )

    # ── Preview Cache: Check cache for preview requests (no mac required) ──
    mode_override_hash = None
    if preview_mode_override:
        # Generate a hash of the mode override config for cache key
        mode_override_hash = hashlib.md5(
            json.dumps(preview_mode_override, sort_keys=True).encode()
        ).hexdigest()[:12]

    # 历史日不走预览缓存：缓存键里没有日期，拿到的可能是别的日期的图
    if day_override:
        skip_cache = True

    if not mac and is_mode_cacheable and not skip_cache:
        # This is a preview request without device mac - use preview cache
        preview_cache_key = content_cache._get_preview_cache_key(persona, screen_w, screen_h, preview_city_override, mode_override_hash, preview_ui_language)
        cached_img = await content_cache.get_preview(
            persona,
            screen_w,
            screen_h,
            city_override=preview_city_override,
            mode_override_hash=mode_override_hash,
            ui_language=preview_ui_language,
        )
        if cached_img:
            cache_hit = True
            img = cached_img

    # 设备路径：每日内容缓存
    #
    # 注意这里**不**受 is_mode_cacheable 限制。cacheable 这个旧开关是给
    # 「按 mac 批量预热」用的（哪些模式值得后台预生成一份），而每日内容
    # 缓存取代了它 —— 命中就直接下发，不发任何请求。
    # 继续用旧开关卡着，就等于 16 个模式里只有 1 个能享受日更缓存。
    if mac and config and not skip_cache:
        # 一个（模式 + 参数指纹 + 日期 + 画幅）一天一份，所有设备共享。
        # 先看今天有没有；没有再生成。这取代了原来「按 mac 各存一份 +
        # TTL 到点重生成」的做法 —— 后者会让两台屏各调一次模型。
        if use_daily_cache:
            from core.daily_cache import get_today, param_fingerprint, today_str
            from core.subscription import ModeNotSubscribed, allow_modes, ensure_allowed

            # 读缓存也要过闸门：未勾选的模式连旧图都不给下发。
            # 设备自己的勾选放行（它就是在用设备），其余按全局订阅集合判。
            try:
                with allow_modes(device_modes):
                    await ensure_allowed(persona, source="daily-cache-read")
            except ModeNotSubscribed as exc:
                from fastapi import HTTPException

                logger.warning("[BUILD_IMAGE] %s 未订阅，拒绝下发缓存内容", exc.mode_id)
                raise HTTPException(
                    status_code=403,
                    detail=f"模式 {exc.mode_id} 未被订阅，已拒绝下发内容",
                ) from exc

            _fp = param_fingerprint(get_effective_mode_config(config, persona), persona)
            _daily_img = await get_today(persona, _fp, today_str(), screen_w, screen_h)
            if _daily_img is not None:
                cache_hit = True
                img = _daily_img
                # 同步进按 mac 的旧缓存（读路径已不用它，留着兼容推送预览）
                await content_cache.set(mac, persona, img, screen_w, screen_h)

        # 注意：这里**不再**触发按 mac 的后台批量重生成。
        # 那个逻辑会在缓存到期时把这台设备的全部模式重新生成一遍 ——
        # 两台屏就调两次模型，正是要消除的浪费。
        # 现在没命中就只生成当前这一个模式，写进每日内容缓存，别的屏共享。

        if not cache_hit:
            logger.info("[CACHE MISS] %s:%s - Generating content", mac, persona)
    elif not mac:
        # Preview request without device mac
        if skip_cache:
            logger.info("[PREVIEW] Skip cache for %s:%s", mac, persona)
        # img is already set from preview cache above, don't overwrite
    else:
        if skip_cache:
            logger.info("[PREVIEW] Skip cache for %s:%s", mac, persona)
        img = None

    content_data = None
    content_fallback = False

    if intent_only:
        return img, persona, cache_hit, content_fallback, False, llm_required

    if not cache_hit:
        effective_cfg = get_effective_mode_config(config, persona)
        if day_override:
            # 历史日：日期取指定那天，天气取不到就留空（不编）
            from core.history_day import date_ctx_for

            date_ctx = date_ctx_for(day_override)
            if phase_override:
                # 小景等多时段模式：渲染那天的某一个时段，而不是当前时段
                date_ctx["phase_override"] = phase_override
            weather = {"temp": 0, "weather_code": -1, "weather_str": ""}
        else:
            location_args = extract_location_settings(effective_cfg, fallback_city=DEFAULT_CITY)
            date_ctx, weather = await asyncio.gather(
                get_date_context(),
                get_weather(**location_args),
            )
        from core.subscription import ModeNotSubscribed, allow_modes

        try:
            with allow_modes(device_modes):
                img, content_data = await generate_and_render(
                    persona,
                    config,
                    date_ctx,
                    weather,
                    battery_pct,
                    screen_w=screen_w,
                    screen_h=screen_h,
                    mac=mac or "",
                    owner_account=owner_account,
                    colors=colors,
                    archive_screen=archive_screen,
                    use_daily_cache=use_daily_cache,
                )
        except ModeNotSubscribed as exc:
            from fastapi import HTTPException

            logger.warning("[BUILD_IMAGE] %s 未订阅，拒绝调用模型", exc.mode_id)
            raise HTTPException(
                status_code=403,
                detail=f"模式 {exc.mode_id} 未被订阅，已拒绝调用模型",
            ) from exc
        if isinstance(content_data, dict):
            logger.debug(
                "[BUILD_IMAGE] content_data keys: %s, _is_fallback=%s, _used_fallback=%s, _llm_ok=%s",
                list(content_data.keys()),
                content_data.get("_is_fallback"),
                content_data.get("_used_fallback"),
                content_data.get("_llm_ok"),
            )
            if content_data.get("_is_fallback") is True:
                content_fallback = True
            elif content_data.get("_used_fallback") is True:
                content_fallback = True
            else:
                jm = get_registry().get_json_mode(persona, mac)
                if jm and jm.definition.get("content", {}).get("type") == "image_gen":
                    content_fallback = not bool(content_data.get("image_url"))

        if mac and config:
            # 同步进按 mac 的旧缓存。读路径已经不用它了，留着兼容推送预览。
            await content_cache.set(mac, persona, img, screen_w, screen_h)

        # ── Preview Cache: Save generated image for preview requests ──
        if not mac and is_mode_cacheable and not skip_cache and img is not None:
            await content_cache.set_preview(
                persona,
                img,
                screen_w,
                screen_h,
                city_override=preview_city_override,
                mode_override_hash=mode_override_hash,
                ui_language=preview_ui_language,
            )

    if mac:
        await update_device_state(
            mac,
            last_persona=persona,
            last_refresh_at=datetime.now().isoformat(),
        )

    if mac and content_data:
        try:
            await save_render_content(mac, persona, content_data)
        except (OSError, ValueError, TypeError):
            logger.warning("[CONTENT] Failed to save content for %s:%s", mac, persona, exc_info=True)

    # 检查 API key 是否无效
    api_key_invalid = False
    if isinstance(content_data, dict) and content_data.get("_api_key_invalid") is True:
        api_key_invalid = True
        logger.warning(
            "[API_KEY] User provided API key is invalid or expired for mac=%s, mode=%s",
            mac,
            persona,
        )
        # 对于设备端，返回提示图片；对于 Web 预览，返回 api_key_invalid 标志让接口处理
        if mac:
            img = _render_api_key_invalid_image(screen_w, screen_h)
            await update_device_state(
                mac,
                last_persona=persona,
                last_refresh_at=datetime.now().isoformat(),
            )

    return img, persona, cache_hit, content_fallback, api_key_invalid, llm_required


async def log_render_stats(
    mac: str,
    persona: str,
    cache_hit: bool,
    elapsed_ms: int,
    *,
    voltage: float = 3.3,
    rssi: Optional[int] = None,
    status: str = "success",
    is_fallback: bool = False,
):
    try:
        await log_render(mac, persona, cache_hit, elapsed_ms, status, is_fallback=is_fallback)
        await log_heartbeat(mac, voltage, rssi)
    except (OSError, ValueError, TypeError):
        logger.warning("[STATS] Failed to log render stats for %s", mac, exc_info=True)


async def resolve_preview_voltage(v: Optional[float], mac: Optional[str]) -> float:
    if v is not None:
        return v
    if mac:
        latest_voltage = await get_latest_battery_voltage(mac)
        if latest_voltage is not None:
            return latest_voltage
    return 3.3


def _local_hour_for_config(config: Optional[dict], now: Optional[datetime] = None) -> int:
    """按配置里的时区取当前小时。

    设备配置带时区（我们的是 Asia/Shanghai），服务器本身也是东八区，
    但走配置时区更保险 —— 设备换城市时不用改服务器。
    """
    moment = now or datetime.now()
    tz_name = str((config or {}).get("timezone") or "").strip()
    if tz_name:
        try:
            from zoneinfo import ZoneInfo

            return moment.astimezone(ZoneInfo(tz_name)).hour
        except Exception:
            logger.warning("[NIGHT] Invalid timezone %s, falling back to server time", tz_name)
    return moment.hour


def night_refresh_minutes_for_config(
    config: Optional[dict], *, now: Optional[datetime] = None
) -> int:
    """夜间窗口内该用的刷新间隔；不在夜间、或没启用夜间睡眠，返回 0。

    夜间睡眠三件套（都在 configs 表里）：
      night_refresh_interval = 0  -> 没启用，完全不影响现有行为
      night_start_hour / night_end_hour -> 夜间窗口，支持跨午夜（23:00 → 06:00）

    为什么要有这个：墨水屏不是耗电，是耗刷新次数。夜间没人看，
    把间隔拉长，一晚上 18 次刷新降到 1 次，省的是面板寿命。
    """
    if not config:
        return 0
    try:
        interval = int(config.get("night_refresh_interval") or 0)
    except (TypeError, ValueError):
        return 0
    if interval <= 0:
        return 0
    try:
        start_raw = config.get("night_start_hour")
        end_raw = config.get("night_end_hour")
        start = int(0 if start_raw is None else start_raw)
        end = int(6 if end_raw is None else end_raw)
    except (TypeError, ValueError):
        return 0
    start = max(0, min(23, start))
    end = max(0, min(24, end))
    if start == end:
        return 0
    hour = _local_hour_for_config(config, now)
    if start < end:
        in_window = start <= hour < end
    else:
        # 跨午夜，例如 23:00 → 06:00
        in_window = hour >= start or hour < end
    return interval if in_window else 0


def resolve_refresh_minutes_for_device_state(
    config: Optional[dict],
    state: Optional[dict],
    *,
    now: Optional[datetime] = None,
) -> int:
    refresh_minutes_raw = config.get("refresh_interval") if config else DEFAULT_REFRESH_INTERVAL
    try:
        refresh_minutes = int(refresh_minutes_raw)
    except (TypeError, ValueError):
        refresh_minutes = DEFAULT_REFRESH_INTERVAL
    if refresh_minutes <= 0:
        refresh_minutes = DEFAULT_REFRESH_INTERVAL

    expected_refresh_raw = state.get("expected_refresh_min", 0) if state else 0
    try:
        expected_refresh = int(expected_refresh_raw)
    except (TypeError, ValueError):
        expected_refresh = 0
    if expected_refresh > 0:
        refresh_minutes = expected_refresh
    # 夜间睡眠压过一切：设备自己上报的 expected_refresh_min 也得让路，
    # 否则服务器说了不算（设备上报值优先级更高）。
    night_minutes = night_refresh_minutes_for_config(config, now=now)
    if night_minutes > 0:
        refresh_minutes = night_minutes
    return refresh_minutes


def reconnect_threshold_seconds(refresh_minutes: int) -> int:
    base_seconds = max(1, int(refresh_minutes)) * 60
    return max(base_seconds + 30, int(base_seconds * 1.5))


def normalize_pushed_preview(image_bytes: bytes, *, width: int, height: int) -> bytes:
    with Image.open(io.BytesIO(image_bytes)) as incoming:
        img = incoming.convert("1")
        if img.size != (width, height):
            img = img.resize((width, height), Image.NEAREST)
        return image_to_bmp_bytes(img)


def _render_api_key_invalid_image(screen_w: int, screen_h: int) -> Image.Image:
    """渲染 API key 无效提示图（1-bit），对 ESP32 固件保持兼容。

    始终返回 mode=\"1\" 的黑白图像，调用方按 BMP 返回给设备（HTTP 200）。
    """
    img = Image.new("1", (screen_w, screen_h), 1)  # 1 = 白色背景
    draw = ImageDraw.Draw(img)
    message = "API key 无效或已过期，请检查设备配置"
    try:
        font = load_font("noto_serif_regular", 12)
    except Exception:  # pragma: no cover - 极端环境下回退
        font = None
    try:
        if font:
            bbox = draw.textbbox((0, 0), message, font=font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]
        else:
            text_w = len(message) * 6
            text_h = 10
        x = (screen_w - text_w) // 2
        y = (screen_h - text_h) // 2
        draw.text((x, y), message, fill=0, font=font)  # 0 = 黑色文字
    except Exception:
        logger.warning("[RENDER] Failed to render API key invalid message", exc_info=True)
    return img


def _render_device_unbound_image(screen_w: int, screen_h: int, pair_code: str) -> Image.Image:
    img = Image.new("1", (screen_w, screen_h), 1)
    draw = ImageDraw.Draw(img)
    title = "欢迎使用 InkSight"
    pair_line = f"配对码：{pair_code}" if pair_code else "正在生成配对码..."
    hint = "请在设备配置页输入配对码完成绑定"
    try:
        title_font = load_font("noto_serif_regular", 20)
        body_font = load_font("noto_serif_regular", 14)
    except Exception:
        title_font = None
        body_font = None
    try:
        if title_font:
            title_bbox = draw.textbbox((0, 0), title, font=title_font)
            title_w = title_bbox[2] - title_bbox[0]
            title_h = title_bbox[3] - title_bbox[1]
        else:
            title_w = len(title) * 10
            title_h = 18
        if body_font:
            pair_bbox = draw.textbbox((0, 0), pair_line, font=body_font)
            hint_bbox = draw.textbbox((0, 0), hint, font=body_font)
            pair_w = pair_bbox[2] - pair_bbox[0]
            pair_h = pair_bbox[3] - pair_bbox[1]
            hint_w = hint_bbox[2] - hint_bbox[0]
            hint_h = hint_bbox[3] - hint_bbox[1]
        else:
            pair_w = len(pair_line) * 7
            pair_h = 12
            hint_w = len(hint) * 7
            hint_h = 12
        spacing = 18
        total_h = title_h + pair_h + hint_h + spacing * 2
        y = max(16, (screen_h - total_h) // 2)
        draw.text((max(0, (screen_w - title_w) // 2), y), title, fill=0, font=title_font)
        y += title_h + spacing
        draw.text((max(0, (screen_w - pair_w) // 2), y), pair_line, fill=0, font=body_font)
        y += pair_h + spacing
        draw.text((max(0, (screen_w - hint_w) // 2), y), hint, fill=0, font=body_font)
    except Exception:
        logger.warning("[RENDER] Failed to render device unbound message", exc_info=True)
    return img
