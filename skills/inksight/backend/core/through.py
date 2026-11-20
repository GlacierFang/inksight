"""透屏 provider：一块透明的屏，往里看是电路板。

2026-11-02 冰河大人定（与「窗外」成对）：
- 窗外是透出去看风景，透屏是透进去看电路板 —— 同一块玻璃的两个方向。
- 视角：正交俯视 —— 视线垂直于板面，整块板平摊摊开，没有透视收缩（2026-11-02 冰河大人定：
  透过相框看背面的板，视线必然垂直，看到的该是正投影平面图，不是斜视）。
- 屏必须在场：画面边缘要看出是从屏背面往里看（屏框 / 玻璃反光）。
- 电路板上有什么完全交给 AI：不预设元件种类、不预设构图。
- 丝印：板子上印着的字（如 R47 10kΩ），和景象同一次 LLM 调用带出来，
  不是系统读出来的数。页脚显示它 —— 它是那个元件的名字，不是系统数据。
- 不额外调看图模型复核 —— 那是白烧 token。
- 四色：交给渲染器的 Atkinson 抖动归色，不做预四色化（同窗外）。
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
ART_DIR = _BACKEND_DIR / "static" / "through"
STATE_DIR = _BACKEND_DIR / "runtime_cache" / "through"
STATE_FILE = STATE_DIR / "state.json"

MINIMAX_BASE_URL = "https://api.minimaxi.com/v1"
MINIMAX_IMAGE_API_URL = f"{MINIMAX_BASE_URL}/images/generations"
IMAGE_MODEL = "MiniMax-H3-Image"
IMAGE_ASPECT = "3:2"

# 屏上正文区尺寸（400x300 去掉页脚 30px）
ART_DISPLAY = (400, 270)

LLM_MODELS = ("MiniMax-M3.1-Flash", "MiniMax-M3")

# ── 四色盘（与 config.py 的 EINK_4COLOR_PALETTE 一致）────────────────────
PALETTE_SPEC = (
    "画面只用四种颜色：纯白纸面底、墨黑 #000000 勾线与暗部、金黄 #E8B000 填色、朱红 #C80000 点缀。"
    "同一色系内可以有深浅变化（深金黄到淡金黄、朱红有浓淡），用明暗表现体积与柔和光影；"
    "但不要出现第五种色相（不要蓝、绿、紫、青），不要写实材质纹理，"
    "四色套色版画质感，线条清晰，不要任何文字、印章、标签、水印。"
)

# ── 透屏：从屏背面往里看电路板 ─────────────────────────────────────────
# 靠提示词保证「是透屏」，不做看图复核。
THROUGH_SPEC = (
    "画面是从电子墨水屏的背面往里看：这块屏是透明的，你透过它看见底下的电路板。"
    "画面边缘要能看出这是屏 —— 屏幕边框正对着你，是一圈大致平行于画面边缘的矩形框，"
    "还能看到一点玻璃的反光。"
    "视线垂直于板面，是正交的俯视平面图：整块电路板平摊摊开在画面里，"
    "走线、焊点、元件都摊开看得全，像从正上方平拍下来的一张电路板照片。"
    "这是正投影，不要透视收缩：边与边互相平行，没有近大远小，没有消失点，没有斜着看的角度。"
    "电路板占画面主体。"
)
THROUGH_SPEC_RETRY = (
    " 画面必须是从上往下垂直俯视整块电路板的正投影（平摊摊开，边与边平行，"
    "没有透视收缩、没有消失点、不要斜视），并且是透过屏幕正对着往里看，屏框一定要出现在画面里。"
)

# ── 电路板景象 + 丝印（LLM 一次调用出两样）─────────────────────────────
SCENE_PROMPT = (
    "用两行回答，不要解释，不要写别的。\n"
    "第一行：一句话（不超过40字）写出你垂直俯视、透过屏幕往里看，底下的电路板是什么样子"
    "（只写看到的物体，不要写颜色，不要写板上的字）。\n"
    "第二行：一行丝印（不超过10字），就是第一行里那个元件上印着的字样，"
    "比如 R47 10kΩ、C12 50V、U3 MCU。\n"
    "电路板上有什么完全由你决定。"
)


# ── 小工具 ────────────────────────────────────────────────────────────

def _strip_label(line: str) -> str:
    """去掉「第一行：」「丝印：」这种前缀。"""
    for sep in ("：", ":"):
        head, _, tail = line.partition(sep)
        if tail.strip() and len(head.strip()) <= 8:
            return tail.strip()
    return line


def _parse_two(raw: str) -> tuple[str, str]:
    """LLM 输出两行 -> (景象, 丝印)。"""
    lines: list[str] = []
    for line in (raw or "").splitlines():
        line = line.strip().strip("「」『』\"'")
        if line:
            lines.append(_strip_label(line))
    scene = lines[0][:60] if lines else ""
    silkscreen = lines[1][:24] if len(lines) > 1 else ""
    return scene, silkscreen


def _art_name() -> str:
    """这一屏透屏的文件名。每次渲染都是新的一张，不复用旧图。"""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    salt = hashlib.md5(f"{stamp}{os.getpid()}".encode("utf-8")).hexdigest()[:4]
    return f"through_{stamp}_{salt}.png"


# ── LLM：写电路板 + 丝印 ───────────────────────────────────────────────

async def _llm_scene(**kwargs) -> tuple[str, str]:
    """让 LLM 写一句电路板景象和一个丝印。

    城市名、天气这些真实信息一律不进 prompt：透屏里没有可读的系统数据，
    板子上有什么完全由模型决定。
    """
    from .content import _call_llm

    provider = str(kwargs.get("llm_provider") or "").strip() or "minimax"
    model = str(kwargs.get("llm_model") or "").strip()
    models = (model,) if model else LLM_MODELS
    prompt = SCENE_PROMPT

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
                    "[Through] LLM 写电路板失败(%s 第 %s 次): %s", model_name, attempt + 1, exc
                )
                raw = ""
            if raw and raw.strip():
                break
        scene, silkscreen = _parse_two(raw)
        if scene:
            return scene, silkscreen
    return "", ""


# ── 出图 ──────────────────────────────────────────────────────────────

def _image_prompt(scene: str, retry: bool = False) -> str:
    """丝印不进生图提示词 —— 让模型画字会糊成一团，版画里也不该有字。"""
    parts = [scene.strip()]
    parts.append(THROUGH_SPEC_RETRY if retry else THROUGH_SPEC)
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
            "area": "1K",
            "n": 1,
            "response_format": "url",
            # 新端点没有 prompt_optimizer/aigc_watermark；disable_prompt_enhanced 反向映射旧开关
            "disable_prompt_enhanced": True,
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
        # 新端点成功时响应里没有 base_resp 字段：只有存在且非 0 才是失败
        if base_resp.get("status_code") not in (0, None):
            reasons.append(f"生图失败: {base_resp.get('status_msg')}")
            break
        # 新端点 data 是数组：业务失败是 HTTP 200 + item.error，图片在 item.url
        items = data.get("data") or []
        if not isinstance(items, list):
            items = []
        urls = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("error"):
                reasons.append("生图失败: " + str(item["error"].get("message") or item["error"].get("code")))
                break
            if item.get("url"):
                urls.append(item["url"])
        if not urls:
            if not reasons:
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


# ── 当日记录（画廊 / 回顾用）──────────────────────────────────────────

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
        logger.warning("[Through] 写状态失败: %s", exc)


def _record_day(day: str, record: dict) -> None:
    state = _read_state()
    days = state.get("days")
    if not isinstance(days, dict):
        days = {}
    days[day] = record
    _write_state({"days": days})


# ── 内容装配 ──────────────────────────────────────────────────────────

async def build_through_content(fallback: dict | None = None, **kwargs) -> dict:
    content = dict(fallback or {})

    dest = ART_DIR / _art_name()
    scene, silkscreen = await _llm_scene(**kwargs)
    if not scene:
        logger.warning("[Through] 没拿到电路板景象")
        content["has_image"] = "no"
        return content
    ok, reason = await _generate_art(dest, scene, **kwargs)
    if not ok:
        logger.warning("[Through] 出图失败: %s", reason)
        content["has_image"] = "no"
        return content

    content["art_url"] = f"/static/through/{dest.name}"
    content["has_image"] = "yes"
    content["silkscreen"] = silkscreen
    content["scene"] = scene

    day = ""
    date_ctx = kwargs.get("date_ctx") or {}
    try:
        day = f"{int(date_ctx['year']):04d}-{int(date_ctx['month']):02d}-{int(date_ctx['day']):02d}"
    except (KeyError, TypeError, ValueError):
        day = time.strftime("%Y-%m-%d")
    _record_day(day, {
        "date": day,
        "silkscreen": silkscreen,
        "scene": scene,
        "art_url": content["art_url"],
        "has_image": "yes",
    })
    return content
