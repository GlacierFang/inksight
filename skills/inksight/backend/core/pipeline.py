"""
统一的内容生成 + 渲染管道
支持内置 Python 模式和 JSON 定义模式的统一分发
"""
from __future__ import annotations

import logging
from PIL import Image

from datetime import datetime

from .config import (
    SCREEN_WIDTH,
    SCREEN_HEIGHT,
    DEFAULT_LLM_PROVIDER,
    DEFAULT_LLM_MODEL,
    DEFAULT_IMAGE_PROVIDER,
    DEFAULT_IMAGE_MODEL,
    DEFAULT_LANGUAGE,
    DEFAULT_CONTENT_TONE,
)

WEEKDAY_EN_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MONTH_EN_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _format_date_str(date_ctx: dict, language: str) -> str:
    if language == "en":
        now = datetime.now()
        weekday = date_ctx.get("weekday", now.weekday())
        day = date_ctx.get("day", now.day)
        month = now.month
        return f"{MONTH_EN_SHORT[month - 1]} {day} {WEEKDAY_EN_SHORT[weekday]}"
    return date_ctx["date_str"]

logger = logging.getLogger(__name__)


def get_effective_mode_config(cfg: dict | None, persona: str) -> dict:
    base = dict(cfg or {})
    mode_overrides = base.get("mode_overrides", {})
    if not isinstance(mode_overrides, dict):
        return base
    override = mode_overrides.get((persona or "").upper(), {})
    if not isinstance(override, dict):
        return base
    for key in (
        "city",
        "latitude",
        "longitude",
        "timezone",
        "admin1",
        "country",
        "llm_provider",
        "llm_model",
    ):
        value = override.get(key)
        if isinstance(value, str) and value.strip():
            base[key] = value.strip()
        elif isinstance(value, (int, float)) and key in {"latitude", "longitude"}:
            base[key] = value
    reserved = {
        "city",
        "latitude",
        "longitude",
        "timezone",
        "admin1",
        "country",
        "llm_provider",
        "llm_model",
        "llmProvider",
        "llmModel",
    }
    explicit_mode_settings = override.get("mode_settings")
    mode_settings = dict(explicit_mode_settings) if isinstance(explicit_mode_settings, dict) else {}
    for k, v in override.items():
        if k in reserved or k == "mode_settings":
            continue
        mode_settings[k] = v
    if mode_settings:
        base["mode_settings"] = mode_settings
    return base


async def generate_and_render(
    persona: str,
    config: dict | None,
    date_ctx: dict,
    weather: dict,
    battery_pct: float,
    screen_w: int = SCREEN_WIDTH,
    screen_h: int = SCREEN_HEIGHT,
    mac: str = "",
    owner_account: str = "",
    colors: int = 2,
    archive_screen: bool = False,
    use_daily_cache: bool = True,
) -> tuple[Image.Image, dict | None]:
    """Generate content for a persona and render to an e-ink image.

    Dispatches to either a builtin Python mode or a JSON-defined mode
    via the mode registry.

    每日内容缓存（use_daily_cache=True，默认）
    ------------------------------------------
    一个（模式 + 参数指纹 + 日期 + 画幅）只生成一次，所有设备共享。
    命中就完全不调模型。参数指纹只含城市/坐标/时区/语言；时段在生成时
    取一次并冻结进内容，不进指纹 —— 否则早晚各成一份，「一天一张」就破了。
    保留 7 天：当天没有就下发最近一次成功的，屏上永远有东西。

    archive_screen=True 时，把刚渲染出来的整屏图存档到画廊位置
    （runtime_cache/<state>/screens/<日期>.png）。这是「生成即存」的唯一落点：
    走这条路的每一条出图路径（设备下发、缓存批量预热）都会存到同一个地方。
    预览不传这个开关 —— 预览图不该当成设备真实拿到的画面。

    Returns:
        Tuple of (rendered image, content dict).
        命中每日缓存时 content 为 None —— 那张图是之前生成的，内容不在本次手里。
    """
    # ── 订阅闸门 ──────────────────────────────────────────────
    # 未勾选的模式不许调模型，也不许下发缓存里的旧图。
    from .subscription import ensure_allowed

    await ensure_allowed(persona, source="generate_and_render")

    if not use_daily_cache:
        return await _generate_and_render_uncached(
            persona, config, date_ctx, weather, battery_pct,
            screen_w=screen_w, screen_h=screen_h, mac=mac,
            owner_account=owner_account, colors=colors,
            archive_screen=archive_screen,
        )

    from .daily_cache import get_or_generate, param_fingerprint

    cfg = get_effective_mode_config(config, persona)
    fingerprint = param_fingerprint(cfg, persona)
    day = _date_key(date_ctx)

    async def _generate():
        return await _generate_and_render_uncached(
            persona, config, date_ctx, weather, battery_pct,
            screen_w=screen_w, screen_h=screen_h, mac=mac,
            owner_account=owner_account, colors=colors,
            archive_screen=archive_screen,
        )

    img, content, fresh = await get_or_generate(
        persona, fingerprint, day, screen_w, screen_h, _generate
    )
    if img is None:
        raise RuntimeError(f"每日内容不可用: {persona}")
    if archive_screen and not fresh:
        # 命中缓存时也落一次画廊（新鲜生成的那次已经落过了）
        _archive_screen(persona, img, date_ctx)
    # 命中缓存时 content 是 None —— 那张图是之前生成的，内容不在本次手里。
    return img, content


async def _generate_and_render_uncached(
    persona: str,
    config: dict | None,
    date_ctx: dict,
    weather: dict,
    battery_pct: float,
    screen_w: int = SCREEN_WIDTH,
    screen_h: int = SCREEN_HEIGHT,
    mac: str = "",
    owner_account: str = "",
    colors: int = 2,
    archive_screen: bool = False,
) -> tuple[Image.Image, dict | None]:
    """真正出图：内容生成 + 渲染。

    每日内容缓存由调用方（generate_and_render）负责 —— 这里不写缓存，
    免得「带临时覆盖的预览」把脏内容写进当天的缓存。
    """
    time_str = date_ctx.get("time_str", "")
    weather_str = weather["weather_str"]
    weather_code = weather.get("weather_code", -1)
    cfg = get_effective_mode_config(config, persona)

    content = await _generate_content_for_persona(
        persona,
        cfg,
        date_ctx,
        weather_str,
        mac=mac,
        owner_account=owner_account,
        screen_w=screen_w,
        screen_h=screen_h,
        weather_code=weather_code,
        colors=colors,
    )

    eff_cfg = get_effective_mode_config(config, persona)
    _eff_lang = eff_cfg.get("mode_language", "") or DEFAULT_LANGUAGE
    date_str = _format_date_str(date_ctx, _eff_lang)

    img = await _render_for_persona(
        persona,
        content,
        date_str=date_str,
        weather_str=weather_str,
        battery_pct=battery_pct,
        weather_code=weather_code,
        time_str=time_str,
        date_ctx=date_ctx,
        screen_w=screen_w,
        screen_h=screen_h,
        mac=mac or "",
        colors=colors,
        language=_eff_lang,
    )
    if archive_screen:
        _archive_screen(persona, img, date_ctx)
    return img, content


async def generate_content_only(
    persona: str,
    config: dict | None,
    date_ctx: dict,
    weather: dict,
    *,
    mac: str = "",
    owner_account: str = "",
    screen_w: int = SCREEN_WIDTH,
    screen_h: int = SCREEN_HEIGHT,
    use_daily_cache: bool = True,
) -> dict:
    """只生成文字内容（不出图）。

    按天固定：当天同一份（模式 + 参数指纹）只生成一次，之后直接读缓存。
    手机 App 的「今日内容」和小组件都走这里 —— 用户开 App 不该每次都烧模型。

    闸门在 _generate_content_for_persona 里，未勾选的模式照样被拒。
    """
    # ── 订阅闸门 ──────────────────────────────────────────────
    # 未勾选的模式不许调模型。手机 App、小组件、预览全走这一个判断，
    # 不留后门 —— 这是「未勾选不调用 LLM」的唯一落点。
    from .subscription import ensure_allowed

    await ensure_allowed(persona, source="generate_content_only")

    cfg = get_effective_mode_config(config, persona)

    if not use_daily_cache:
        return await _generate_content_for_persona(
            persona,
            cfg,
            date_ctx,
            weather.get("weather_str", ""),
            mac=mac,
            owner_account=owner_account,
            screen_w=screen_w,
            screen_h=screen_h,
            weather_code=weather.get("weather_code", -1),
        )

    from .daily_cache import get_text_or_generate, param_fingerprint

    fingerprint = param_fingerprint(cfg, persona)
    day = _date_key(date_ctx)

    async def _generate():
        return await _generate_content_for_persona(
            persona,
            cfg,
            date_ctx,
            weather.get("weather_str", ""),
            mac=mac,
            owner_account=owner_account,
            screen_w=screen_w,
            screen_h=screen_h,
            weather_code=weather.get("weather_code", -1),
        )

    content, _fresh = await get_text_or_generate(persona, fingerprint, day, _generate)
    if content is None:
        raise RuntimeError(f"文字内容不可用: {persona}")
    return content


async def _generate_content_for_persona(
    persona: str,
    cfg: dict,
    date_ctx: dict,
    weather_str: str,
    mac: str = "",
    owner_account: str = "",
    screen_w: int = SCREEN_WIDTH,
    screen_h: int = SCREEN_HEIGHT,
    weather_code: int = -1,
    colors: int = 2,
) -> dict:
    """Dispatch content generation to the appropriate handler.

    内部实现，不做订阅检查 —— 闸门在外层入口
    （generate_and_render / generate_content_only）统一把关。
    """
    from .mode_registry import ContentContext, get_registry

    registry = get_registry()

    effective_language = cfg.get("mode_language", "") or DEFAULT_LANGUAGE
    date_str = _format_date_str(date_ctx, effective_language)

    # ── 静态模式拦截层 ──────────────────────────────────────
    # 对于 3 个静态模式（POETRY/RIDDLE/THISDAY），
    # 从本地 SQLite 知识库查询，绕过 LLM 调用，实现零 API 消耗。
    # 当静态库为空时返回带 _static_fallback 标记的内容，
    # 此时继续走原有 LLM 路径作为降级策略。
    if registry.is_json_mode(persona):
        from .static_content import generate_static_content, should_use_static_fallback
        static_content = await generate_static_content(
            persona, mac, date_ctx=date_ctx
        )
        if not should_use_static_fallback(static_content):
            # 静态库命中：直接返回
            static_content["_from_static_db"] = True
            static_content["_static_served"] = True
            logger.info("[Pipeline] Static DB hit for mode=%s mac=%s", persona, mac)
            return static_content
        # 静态库未命中，降级走原有 LLM 路径

    # 服务端统一托管凭证：不再接受用户级 API Key（个人端 BYOK 已下线），
    # api_key 传 None，由 content 层回退到服务端环境变量中的平台 Key。
    device_api_key: str | None = None
    device_image_api_key: str | None = None

    ctx = ContentContext(
        config=cfg,
        date_ctx=date_ctx,
        weather_str=weather_str,
        date_str=date_str,
        festival=date_ctx.get("festival", ""),
        daily_word=date_ctx.get("daily_word", ""),
        upcoming_holiday=date_ctx.get("upcoming_holiday", ""),
        days_until_holiday=date_ctx.get("days_until_holiday", 0),
        character_tones=cfg.get("character_tones", []),
        language=effective_language,
        content_tone=cfg.get("content_tone", DEFAULT_CONTENT_TONE),
        llm_provider=cfg.get("llm_provider", DEFAULT_LLM_PROVIDER),
        llm_model=cfg.get("llm_model", DEFAULT_LLM_MODEL),
        api_key=device_api_key,
        image_api_key=device_image_api_key,
        llm_base_url=cfg.get("llm_base_url"),
    )

    # JSON-defined mode
    if registry.is_json_mode(persona):
        from .json_content import generate_json_mode_content
        jm = registry.get_json_mode(persona, mac, language=effective_language)
        if not jm:
            # Try to load from database if mode not in registry
            # This can happen for user-specific custom modes
            if mac:
                from .config_store import get_device_owner, get_custom_mode as get_user_custom_mode_from_db
                owner = await get_device_owner(mac)
                if owner:
                    user_id = owner.get("user_id")
                    if user_id:
                        mode_data = await get_user_custom_mode_from_db(user_id, persona, mac)
                        if mode_data:
                            mode_mac = mode_data.get("mac")
                            registry.load_custom_mode_from_dict(persona, mode_data["definition"], source="custom", mac=mode_mac)
                            jm = registry.get_json_mode(persona, mac, language=effective_language)
        if not jm:
            raise ValueError(f"JSON mode {persona} not found in registry")
        return await generate_json_mode_content(
            jm.definition,
            config=cfg,
            date_ctx=date_ctx,
            date_str=date_str,
            weather_str=weather_str,
            festival=date_ctx.get("festival", ""),
            daily_word=date_ctx.get("daily_word", ""),
            upcoming_holiday=date_ctx.get("upcoming_holiday", ""),
            days_until_holiday=date_ctx.get("days_until_holiday", 0),
            character_tones=cfg.get("character_tones", []),
            language=effective_language,
            content_tone=cfg.get("content_tone", DEFAULT_CONTENT_TONE),
            llm_provider=cfg.get("llm_provider", DEFAULT_LLM_PROVIDER),
            llm_model=cfg.get("llm_model", DEFAULT_LLM_MODEL),
            llm_base_url=cfg.get("llm_base_url"),
            image_provider=cfg.get("image_provider", DEFAULT_IMAGE_PROVIDER),
            image_model=cfg.get("image_model", DEFAULT_IMAGE_MODEL),
            mac=mac,
            owner_account=owner_account,
            screen_w=screen_w,
            screen_h=screen_h,
            weather_code=weather_code,
            api_key=device_api_key,
            image_api_key=device_image_api_key,
            colors=colors,
        )

    # Builtin Python mode - use specialized content functions
    bm = registry.get_builtin(persona)
    if bm:
        return await bm.content_fn(ctx)

    raise ValueError(f"Unknown persona: {persona}")


async def _render_for_persona(
    persona: str,
    content: dict,
    *,
    date_str: str,
    weather_str: str,
    battery_pct: float,
    weather_code: int = -1,
    time_str: str = "",
    date_ctx: dict | None = None,
    screen_w: int = SCREEN_WIDTH,
    screen_h: int = SCREEN_HEIGHT,
    mac: str = "",
    colors: int = 2,
    language: str = "zh",
) -> Image.Image:
    """Dispatch rendering to the appropriate handler."""
    from .mode_registry import get_registry
    from .renderer import render_mode
    from .json_renderer import render_json_mode

    registry = get_registry()

    # JSON-defined mode
    if registry.is_json_mode(persona):
        jm = registry.get_json_mode(persona, mac or None, language=language)
        if persona.upper() == "WEATHER":
            weather_str_for_bar = ""
            weather_code_for_bar = -1
        else:
            weather_str_for_bar = weather_str
            weather_code_for_bar = weather_code

        return render_json_mode(
            jm.definition, content,
            date_str=date_str, weather_str=weather_str_for_bar, battery_pct=battery_pct,
            weather_code=weather_code_for_bar, time_str=time_str,
            screen_w=screen_w, screen_h=screen_h, colors=colors,
            language=language,
        )

    return render_mode(
        persona, content,
        date_str=date_str, weather_str=weather_str, battery_pct=battery_pct,
        weather_code=weather_code, time_str=time_str, date_ctx=date_ctx,
        screen_w=screen_w, screen_h=screen_h,
    )


def _archive_screen(persona: str, img, date_ctx: dict) -> None:
    """把刚渲染出来的整屏图存到画廊位置（画廊主图用）。

    规则：
    - 只存画廊收录的那几个模式（其余模式存了也没人看，白占磁盘）；
    - 只存当天 —— 往期由回填脚本自己存，预览不污染往期存档；
    - 只存设备画幅（400x300）—— 预览页可以随便改尺寸，改过的图不是设备画面；
    - 存档失败不影响出图（这是锦上添花，不是主流程）。
    """
    try:
        from .gallery import state_name as gallery_state_name
        from .history_day import today_str
        from .history_render import DEVICE_HEIGHT, DEVICE_WIDTH
        from .screen_archive import save_screen

        state_key = gallery_state_name(persona)
        if not state_key:
            return
        day = _target_day(date_ctx)
        if day != today_str():
            return
        if img.size != (DEVICE_WIDTH, DEVICE_HEIGHT):
            return
        save_screen(state_key, day, img)
    except Exception as exc:  # pragma: no cover - 存档不该打断出图
        logger.warning("[Pipeline] 整屏存档失败 %s: %s", persona, exc)


def _date_key(date_ctx: dict | None) -> str:
    """date_ctx → YYYY-MM-DD。取不到就退回今天。"""
    ctx = date_ctx or {}
    y, m, d = ctx.get("year"), ctx.get("month"), ctx.get("day")
    if isinstance(y, int) and isinstance(m, int) and isinstance(d, int):
        return f"{y:04d}-{m:02d}-{d:02d}"
    from .daily_cache import today_str

    return today_str()


def _target_day(date_ctx: dict | None) -> str:
    """这张图属于哪一天（YYYY-MM-DD）。取不到就用今天。"""
    ctx = date_ctx or {}
    try:
        return f"{int(ctx['year']):04d}-{int(ctx['month']):02d}-{int(ctx['day']):02d}"
    except (KeyError, TypeError, ValueError):
        return datetime.now().strftime("%Y-%m-%d")
