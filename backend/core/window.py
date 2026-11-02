"""窗外 provider：一扇向外的窗，窗外是用户所选城市的实时天气。

2026-11-02 冰河大人定：
- 天气取用户填的城市，每次渲染现取现用（走 cache.py 已有的 Open-Meteo 预取链路）。
- 窗外是什么完全交给 AI：不预设地点、不预设时段、不预设构图。
- 窗必须存在：窗写进生图提示词，出图失败重试时再强调一次。
  不额外调看图模型去复核 —— 那是白烧 token。
- 四色：交给渲染器的 Atkinson 抖动归色，不做预四色化（同天工阁/躺平巴拉）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path

from . import eink_snap

logger = logging.getLogger(__name__)

_BACKEND_DIR = Path(__file__).resolve().parent.parent
ART_DIR = _BACKEND_DIR / "static" / "window"
STATE_DIR = _BACKEND_DIR / "runtime_cache" / "window"
STATE_FILE = STATE_DIR / "state.json"

MINIMAX_BASE_URL = "https://api.minimaxi.com/v1"
MINIMAX_IMAGE_API_URL = f"{MINIMAX_BASE_URL}/image_generation"
IMAGE_MODEL = "image-01"
IMAGE_ASPECT = "3:2"

# 屏上正文区尺寸（400x300 去掉页脚 30px）
ART_DISPLAY = (400, 270)

LLM_MODELS = ("MiniMax-M3.1-Flash", "MiniMax-M2.7")

# ── 四色盘（与 config.py 的 EINK_4COLOR_PALETTE 一致）────────────────────
PALETTE_SPEC = (
    "画面只用四种颜色：纯白纸面底、墨黑 #000000 勾线与暗部、金黄 #E8B000 填色、朱红 #C80000 点缀。"
    "同一色系内可以有深浅变化（深金黄到淡金黄、朱红有浓淡），用明暗表现体积与柔和光影；"
    "但不要出现第五种色相（不要蓝、绿、紫、青），不要写实材质纹理，"
    "四色套色版画质感，线条清晰，不要任何文字、印章、标签、水印。"
)

# ── 窗：整张图就是从屋里向外望的一扇窗 ─────────────────────────────────
# 靠提示词保证「有窗」，不做看图复核。
WINDOW_SPEC = "画面是从屋内向外望的一扇窗，窗框出现在画面边缘，窗外是主要画面。"
WINDOW_SPEC_RETRY = " 画面必须是从屋内透过一扇窗向外看的视角，窗框一定要出现在画面里。"

# ── 窗外景象（LLM 写一句）──────────────────────────────────────────────
SCENE_PROMPT = (
    "现在是{city}的天气：{desc}{temp}。\n"
    "用一句话（不超过40字）写出你从屋里透过一扇窗向外看到的景象。"
    "窗外是什么完全由你决定，只要和上面的天气对得上就行。只写那一句，不要解释。"
)

_FALLBACK_CITY = "杭州"


# ── 小工具 ────────────────────────────────────────────────────────────

def _city_from_config(cfg: dict) -> str:
    """从（生效后的）设备配置里取城市。"""
    try:
        from .context import extract_location_settings

        loc = extract_location_settings(cfg or {})
        city = str(loc.get("city") or "").strip()
        if city:
            return city
    except Exception:  # pragma: no cover
        logger.warning("[Window] 取城市失败", exc_info=True)
    return ""


def _weather_fact(weather_code, weather_str: str, language: str) -> tuple[str, str]:
    """实时天气 -> (天气描述, 温度文字)。"""
    desc = ""
    try:
        if isinstance(weather_code, int) and weather_code >= 0:
            from .context import _weather_code_to_desc

            desc = _weather_code_to_desc(weather_code, language=language)
    except Exception:  # pragma: no cover
        desc = ""
    temp = (weather_str or "").strip()
    return desc, temp


def _clean_scene(raw: str) -> str:
    """从 LLM 输出里取出那一句景象。"""
    for line in (raw or "").splitlines():
        line = line.strip().strip("「」『』\"'")
        if not line:
            continue
        for sep in ("：", ":"):
            head, _, tail = line.partition(sep)
            if tail.strip() and len(head.strip()) <= 6:
                line = tail.strip()
                break
        if line:
            return line[:60]
    return ""


def _art_name(city: str, weather_code) -> str:
    """这一屏窗的文件名。

    每次渲染都是新的一张 —— 窗外是什么交给 AI 随机，不复用旧图。
    文件名带城市哈希 + 天气码 + 时刻，便于回看时认出来源。
    """
    stamp = time.strftime("%Y%m%d-%H%M%S")
    code = str(weather_code) if isinstance(weather_code, int) and weather_code >= 0 else "x"
    city_hash = hashlib.md5((city or "").encode("utf-8")).hexdigest()[:6]
    salt = hashlib.md5(f"{stamp}{os.getpid()}".encode("utf-8")).hexdigest()[:4]
    return f"window_{city_hash}_{code}_{stamp}_{salt}.png"


# ── LLM：写窗外那一句 ─────────────────────────────────────────────────

async def _llm_scene(city: str, desc: str, temp: str, **kwargs) -> str:
    from .content import _call_llm

    provider = str(kwargs.get("llm_provider") or "").strip() or "minimax"
    model = str(kwargs.get("llm_model") or "").strip()
    models = (model,) if model else LLM_MODELS
    prompt = SCENE_PROMPT.format(
        city=city or _FALLBACK_CITY,
        desc=desc or "天气不明",
        temp=(temp + " ") if temp else "",
    )

    for model_name in models:
        for attempt in range(2):
            raw = ""
            try:
                raw = await _call_llm(
                    provider,
                    model_name,
                    prompt,
                    temperature=0.95,
                    max_tokens=2000,
                    api_key=kwargs.get("api_key"),
                    base_url=kwargs.get("llm_base_url"),
                )
            except Exception as exc:
                logger.warning(
                    "[Window] LLM 写窗外景象失败(%s 第 %s 次): %s", model_name, attempt + 1, exc
                )
                raw = ""
            if raw and raw.strip():
                break
        scene = _clean_scene(raw)
        if scene:
            return scene
    return ""


# ── 出图 ──────────────────────────────────────────────────────────────

def _image_prompt(scene: str, retry: bool = False) -> str:
    parts = [scene.strip()]
    parts.append(WINDOW_SPEC_RETRY if retry else WINDOW_SPEC)
    parts.append(PALETTE_SPEC)
    return "。".join(p for p in parts if p)


def _prepare_art(src: Path, dest: Path) -> tuple[bool, str]:
    """生图原图 -> 可直接上屏的图（不做四色归色，交给渲染器的 Atkinson 抖动）。"""
    try:
        from PIL import Image
    except Exception:  # pragma: no cover
        return False, "PIL 不可用"
    try:
        img = Image.open(src).convert("RGB")
    except Exception as exc:
        return False, f"读图失败: {exc}"

    dw, dh = ART_DISPLAY
    try:
        card_img = eink_snap.prepare_direct_art(img, dw, dh)
    except Exception as exc:
        return False, f"上屏准备失败: {exc}"

    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        card_img.save(dest, "PNG")
    except OSError as exc:
        return False, f"写文件失败: {exc}"
    return True, f"{dw}x{dh}"


async def _generate_art(dest: Path, scene: str, **kwargs) -> tuple[bool, str]:
    import httpx

    key = (kwargs.get("image_api_key") or "").strip() or os.getenv("MINIMAX_API_KEY", "").strip()
    if not key or key.startswith("sk-your-"):
        return False, "没有可用的生图凭证"

    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    reasons: list[str] = []
    for prompt in (_image_prompt(scene, False), _image_prompt(scene, True)):
        payload = {
            "model": IMAGE_MODEL,
            "prompt": prompt,
            "aspect_ratio": IMAGE_ASPECT,
            "n": 1,
            "response_format": "url",
            "prompt_optimizer": False,
            "aigc_watermark": False,
        }
        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                resp = await client.post(MINIMAX_IMAGE_API_URL, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            reasons.append(f"生图请求失败: {exc}")
            break
        if resp.status_code != 200:
            reasons.append(f"生图 HTTP {resp.status_code}")
            break
        try:
            data = resp.json()
        except ValueError:
            reasons.append("生图返回不是 JSON")
            break
        base_resp = data.get("base_resp") or {}
        if base_resp.get("status_code") != 0:
            reasons.append(f"生图失败: {base_resp.get('status_msg')}")
            break
        urls = (data.get("data") or {}).get("image_urls") or []
        if not urls:
            reasons.append("生图没给图")
            break

        tmp = ART_DIR / (dest.stem + "_raw.png")
        try:
            async with httpx.AsyncClient(timeout=90.0, follow_redirects=True) as client:
                rimg = await client.get(urls[0])
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(rimg.content)
        except (httpx.HTTPError, OSError) as exc:
            reasons.append(f"下载图失败: {exc}")
            break

        ok, reason = _prepare_art(tmp, dest)
        try:
            tmp.unlink()
        except OSError:
            pass
        if ok:
            return True, reason
        reasons.append(reason)

    return False, "；".join(reasons) or "生图未成功"


# ── 当日记录（画廊 / 回顾用）────────────────────────────────────────

def _read_state() -> dict:
    try:
        with STATE_FILE.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(data: dict) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(STATE_FILE)
    except OSError as exc:
        logger.warning("[Window] 写状态失败: %s", exc)


def _record_day(day: str, record: dict) -> None:
    state = _read_state()
    days = state.get("days")
    if not isinstance(days, dict):
        days = {}
    days[day] = record
    _write_state({"days": days})


# ── 内容装配 ──────────────────────────────────────────────────────────

async def build_window_content(fallback: dict | None = None, **kwargs) -> dict:
    content = dict(fallback or {})
    cfg = kwargs.get("config") or {}
    language = str(kwargs.get("language") or "zh")

    city = _city_from_config(cfg)
    desc, temp = _weather_fact(
        kwargs.get("weather_code", -1), str(kwargs.get("weather_str") or ""), language
    )
    weather_text = " ".join(x for x in (desc, temp) if x).strip()

    content["city_name"] = city
    content["weather_text"] = weather_text or "天气未知"

    dest = ART_DIR / _art_name(city, kwargs.get("weather_code", -1))
    scene = await _llm_scene(city, desc, temp, **kwargs)
    if not scene:
        logger.warning("[Window] 没拿到窗外景象")
        content["has_image"] = "no"
        return content
    ok, reason = await _generate_art(dest, scene, **kwargs)
    if not ok:
        logger.warning("[Window] 出图失败: %s", reason)
        content["has_image"] = "no"
        return content

    content["art_url"] = f"/static/window/{dest.name}"
    content["has_image"] = "yes"

    day = ""
    date_ctx = kwargs.get("date_ctx") or {}
    try:
        day = f"{int(date_ctx['year']):04d}-{int(date_ctx['month']):02d}-{int(date_ctx['day']):02d}"
    except (KeyError, TypeError, ValueError):
        day = time.strftime("%Y-%m-%d")
    _record_day(day, {
        "date": day,
        "city_name": city,
        "weather_text": content["weather_text"],
        "art_url": content["art_url"],
        "has_image": "yes",
    })
    return content
