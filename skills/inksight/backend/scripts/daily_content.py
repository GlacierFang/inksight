#!/usr/bin/env python3
"""每日内容生成 + 落帧（systemd user timer 调用）。

为什么要有这个脚本
------------------
回顾（画廊）要的两个东西：
1. 模式自己的当天内容（各模式的 state.json）
2. 当天的整屏图（runtime_cache/<state>/screens/<YYYY-MM-DD>.png）

原来这两样都只在**设备真的来拉图时**才产生（api/routes/render.py 里的
save_screen）。设备睡着、屏幕坏着、没绑定 —— 回顾页就是空的。
scripts/history_backfill.py 能补，但它是手动跑一次就结束。

现在改成：服务器每天定点把这两样都做掉。设备退化成纯显示器 ——
它什么时候醒、醒不醒，内容都在。

用哪台设备的配置
----------------
内容要天气、要城市。这些来自设备配置（configs 表）。脚本取**当前生效
的那份配置**当模板（城市/坐标/语言），但全程不传 MAC：
- 传了 MAC 就等于又绑回设备（设备删了内容也就停了）；
- 不传 MAC，内容就是「这台桌伴的」内容 —— 位置是真的，但载体是账号。

一个模式失败不影响其它模式（各自 try/except），失败原因写日志。
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

def gallery_daily_modes() -> list[str]:
    """画廊收录的、需要每天出内容+落帧的模式。

    从 core/gallery.py 派生，不在这里另抄一份名单 ——
    那是唯一事实来源。wide 形态（电子世界）按日期现算，不落盘，排除掉。
    """
    from core.gallery import modes as gallery_modes

    # wide（电子世界）按日期现算、不落盘；daily=False 的模式已下线，
    # 只保留往期存档，不再每天出图（省一次文字+生图调用）。
    return [
        item["mode_id"]
        for item in gallery_modes()
        if item.get("form") != "wide" and item.get("daily", True)
    ]

# 设备画幅（4 寸墨水屏）。存档只收这个尺寸：预览页可以随便改尺寸，
# 改过的图不是设备画面。
SCREEN_W = 400
SCREEN_H = 300
COLORS = 4


async def _load_template_config(mac: str) -> dict:
    """取这台设备的生效配置当模板（城市/坐标/语言）。

    只借位置信息，不借 MAC —— 详见模块 docstring。
    """
    from core.config_store import get_active_config

    if not mac:
        return {}

    try:
        config = await get_active_config(mac)
    except Exception as exc:
        print(f"[daily] 取 {mac} 的配置失败，用默认配置: {exc}")
        return {}

    if not isinstance(config, dict):
        return {}

    # 位置 + 语言 + 每个模式的覆盖设置（mode_overrides 里可能有城市覆盖）。
    keep = (
        "city", "latitude", "longitude", "timezone",
        "language", "mode_language", "content_tone", "character_tones",
        "mode_overrides",
    )
    template = {key: config[key] for key in keep if config.get(key) not in (None, "")}

    # 凭证由服务端托管：不给 llm/image provider，让 pipeline 走默认的 MiniMax。
    for key in ("llm_provider", "llm_model", "image_provider", "image_model",
                "user_api_key", "user_image_api_key", "llm_base_url"):
        template.pop(key, None)

    return template


async def _targets() -> list[tuple[str, str, dict]]:
    """要生成的（模式, mac, 该设备配置模板）清单。

    遍历**每台在用设备**的勾选集合 —— 两台设备城市不同，内容就该不同。
    同城市的两台设备由每日缓存去重（键含参数指纹），不会重复调模型。

    未勾选的不调模型 —— 这是硬要求，不是优化。
    """
    from core.subscription import active_device_configs

    devices = await active_device_configs()
    if not devices:
        print("[daily] 没有在用设备，跳过", flush=True)
        return []

    gallery_modes = set(gallery_daily_modes())
    targets: list[tuple[str, str, dict]] = []
    seen: set[tuple[str, str]] = set()

    for dev in devices:
        mac = dev["mac"]
        cfg = await _load_template_config(mac)
        if not cfg:
            continue
        for mode_id in dev["modes"]:
            if mode_id not in gallery_modes:
                continue
            key = (mac, mode_id)
            if key in seen:
                continue
            seen.add(key)
            targets.append((mode_id, mac, cfg))

    if not targets:
        print("[daily] 没有需要生成的内容（都未勾选或都已下线）", flush=True)
    return targets


async def _generate_one(
    mode_id: str,
    config: dict,
    date_ctx: dict,
    weather: dict,
    mac: str = "",
) -> tuple[bool, str]:
    """给一个模式生成当天内容 + 落盘当天整屏图。

    走 core/pipeline.generate_and_render —— 和设备下发是同一套出图管线，
    但：
    - 不传 MAC（内容不绑设备）；
    - archive_screen=True（把整屏图存到画廊位置）。

    不用 api.shared.build_image：它在 mac=None 时拿不到设备配置，
    天气会退回默认城市（杭州）而不是这台桌伴所在的城市。

    历史日守卫：provider 对「不是今天」的日期只读往期记录，不调模型。
    这里传的就是今天，所以会正常生成当天内容。
    """
    from core.pipeline import generate_and_render

    from core.subscription import allow_modes

    with allow_modes([mode_id]):
        img, content = await generate_and_render(
            mode_id,
            config,
            date_ctx,
            weather,
            100,                     # 电量：只影响状态栏显示
            screen_w=SCREEN_W,
            screen_h=SCREEN_H,
            mac=mac,                 # 归属设备（内容本身不绑设备）
            colors=COLORS,
            archive_screen=True,     # 落盘到 runtime_cache/<state>/screens/<今天>.png
        )

    if img is None:
        return False, "出图返回空"

    note = "已生成"
    if isinstance(content, dict):
        if content.get("_is_fallback") or content.get("_used_fallback"):
            note += "（内容走了兜底）"
        if content.get("_llm_ok") is False:
            note += "（模型调用失败）"
    return True, note


async def main() -> int:
    from core.context import extract_location_settings, get_date_context, get_weather

    day = datetime.now().strftime("%Y-%m-%d")

    # 日期上下文：和设备下发时取的是同一套，保证画面一致。
    date_ctx = await get_date_context()

    targets = await _targets()
    print(f"[daily] {day} | {len(targets)} 个（设备 x 模式）待生成", flush=True)

    ok_count = 0
    for mode_id, mac, cfg in targets:
        from core.context import extract_location_settings

        try:
            location = extract_location_settings(cfg)
            dev_weather = await get_weather(**location)
            ok, note = await _generate_one(mode_id, cfg, date_ctx, dev_weather, mac)
        except Exception as exc:
            print(f"[daily] {mode_id} ({mac}) 失败: {exc}", flush=True)
            continue
        if ok:
            ok_count += 1
            print(f"[daily] {mode_id} ({mac}) ok ({note})", flush=True)
        else:
            print(f"[daily] {mode_id} ({mac}) 未生成: {note}", flush=True)

    print(f"[daily] 完成 {ok_count}/{len(targets)}", flush=True)

    # 清掉 7 天前的每日内容（保留期就是 7 天）
    try:
        from core.daily_cache import cleanup

        deleted = await cleanup()
        if deleted:
            print(f"[daily] 清理过期内容 {deleted} 条", flush=True)
    except Exception as exc:
        print(f"[daily] 清理失败（不影响本次生成）: {exc}", flush=True)

    return 0 if ok_count == len(targets) else 1


if __name__ == "__main__":
    async def _run() -> int:
        try:
            return await main()
        finally:
            # 必须显式关数据库连接：aiosqlite 的工作线程不是 daemon，
            # 不关的话进程会在所有活干完之后卡住不退（systemd 会判定超时）。
            try:
                from core.db import close_all

                await close_all()
            except Exception as exc:
                print(f"[daily] 关连接失败: {exc}", flush=True)

    raise SystemExit(asyncio.run(_run()))
