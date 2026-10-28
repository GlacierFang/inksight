"""丹青谱 provider：每日一幅不存在的宋代绘画，附虚构题签与四色画心。

产出：
- 题签：画名 / 作者 / 朝代 / 题材 / 题跋 / 印文，由 LLM 当日现写。
- 图：MiniMax image-01 按屏上四色盘（白/黑/金黄/朱红）画出横幅式画心，
  下载后裁白边 + 保线下采样，按显示尺寸 1:1 存本地。
- 印：作者私印，用 LLM 给的四字印文本地画一枚朱文方印。

原则（与天工阁一致）：
- 图是锦上添花，题签才是每天稳定出货的东西；生图真出错时出纯题签版式。
- 数值与图都不由题签编造——题签只描述「图里画的那个东西」。
- 不做预四色化：图直接交给渲染器的 Atkinson 抖动。
- 作者、画名必须是虚构的：不得使用真实宋代画家的名字，不得使用真实画作名。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

from . import eink_snap
from .tiangong import (
    IMAGE_MODEL,
    LLM_MODELS,
)

logger = logging.getLogger(__name__)

_BACKEND_DIR = Path(__file__).resolve().parent.parent
STATE_DIR = _BACKEND_DIR / "runtime_cache" / "danqing"
STATE_FILE = STATE_DIR / "state.json"
ART_DIR = _BACKEND_DIR / "static" / "danqing"

MINIMAX_BASE_URL = "https://api.minimaxi.com/v1"
MINIMAX_IMAGE_API_URL = f"{MINIMAX_BASE_URL}/image_generation"

# 横幅式画心（宋人手卷/横幅一路，横向铺满屏面）。三条屏（400/296/648）都用同一条
# 2.23:1 的比例，画心在每一块屏上都能满宽、不裁切、不留白边。
# 生图与显示尺寸同比例，不裁不压 —— 1152:512 ≈ 392:176。
IMAGE_WIDTH = 1152
IMAGE_HEIGHT = 512

# 画心在屏上的显示尺寸，必须与模式 JSON 里 image 块的 width/height 一致。
ART_DISPLAY = (392, 176)

GENRES = (
    "山水", "花鸟", "人物", "楼阁",
    "墨竹", "蔬果", "荷石", "渔隐",
)

ENTRY_KEYS = ("画名", "作者", "朝代", "题材", "题跋", "印文", "图")
# -- 色相硬映射参数（丹青谱专用）--------------------------

# 目标色（与屏上四色完全一致，量化零误差）
_SNAP_BLACK = (0, 0, 0)
_SNAP_WHITE = (255, 255, 255)
_SNAP_YELLOW = (232, 176, 0)
_SNAP_RED = (200, 0, 0)

# 纸面判据：饱和度低于此值且明度高于此值 -> 纯白
_PAPER_SNAP_S = 91
_PAPER_SNAP_V = 200

# 色相区间（度）：纯红相保朱红，橙金相归金黄
_RED_HUE_MAX = 14.0
_GOLD_HUE_MAX = 48.0

# 暖色相的最低饱和度（低于此值视为灰墨，直接归黑）
_WARM_MIN_S = 60

# 橙金相暗部阈值：明度低于此值归墨黑（远山留皴法，不留死平涂）
_GOLD_MIN_V = 110


# 丹青谱专用四色盘说明（不复用天工阁的 PALETTE_SPEC）。
#
# 为什么单独一份：天工阁画的是器物，可以用「同一色系内深浅变化」做体积；
# 丹青谱画的是宋代山水，模型默认自带花青石绿（实测该图 16.6% 像素是绿/青/蓝相，
# 躺平巴拉只有 2.9%）。屏上四色没有绿，那部分会被 Atkinson 抖成黑黄椒盐噪点。
# 宋代山水本来就以水墨为主，用纯墨写树石反而是「墨分五色」的正统画法。
DANQING_PALETTE_SPEC = (
    "画面只用四种颜色：纯白纸面底、墨黑 #000000 勾线与皴染、金黄 #E8B000 填色、朱红 #C80000 点缀。\n"
    "树石山体一律用纯墨 #000000 皴染，绝不要任何绿色、青色、蓝色、紫色 —— "
    "宋代山水以水墨为主，花青石绿一概不用；\n"
    "远山、天空、屋宇可用金黄 #E8B000 平涂，同一色系内可分深浅（深金黄到淡金黄），点睛处用朱红 #C80000；\n"
    "勾线要清晰锐利，整幅画缩到 392 像素宽时每条线依然看得清；"
    "不要灰线、不要淡墨线、不要铅笔淡描、不要阴影和明暗排线；\n"
    "不要写实材质纹理，宋代图录版画质感，线条清晰，不要任何文字、印章、标签、水印、边框。"
)

# 第一次：按展签指定的构图画 + 丹青谱专属四色盘。
DANQING_IMAGE_PROMPT_TMPL = "{desc}。{palette}"
# 生图失败后的重试版本（仅用于真出错时再试一次）：比共享版更强调「纯墨写树石」。
DANQING_IMAGE_PROMPT_RETRY = (
    "{desc}。墨线要粗而清晰，山体轮廓完整，树木用纯墨皴染绝不要绿色，朱红部分面积加大一些。\n"
    "画面只用四种颜色：纯白纸面底、墨黑 #000000 勾线与皴染、金黄 #E8B000 填色、朱红 #C80000 点缀。\n"
    "同一色系内允许深浅变化（深金黄到淡金黄），用明暗表现体积；\n"
    "不要出现绿色、青色、蓝色、紫色，不要写实材质纹理，不要任何文字、印章、水印、边框。"
)

ENTRY_PROMPT = """你在经营一座虚构的书画馆「丹青谱」。阁中所藏皆为宋代绘画，**但每一幅都不曾存在过**。
今天要造第 {index} 号藏品。

已经造过这些，不要重样（尤其不要同题材）：
{recent}

今天指定的题材：{genre}

请输出这幅画，严格按下面格式，每行一条，不要任何多余的话，全文不超过 160 字：
画名：<4-7 字古雅名，含题材，如「秋林曳杖图」>
作者：<虚构画家名 + 号，如「江村陆氏，号拙庵」，必须是虚构人物，不得用真实宋代画家的名>
朝代：<北宋或南宋>
题跋：<画上的一行题跋，12-20 字，像古人题跋的口吻，不要现代词>
印文：<四字印文，如「拙庵居士」>
图：<给画师的中文提示词，30-50 字，必须是横向横幅构图（宽约为高的两倍，宋人手卷布局），写清画面主体与左右布局、留白位置，并说明哪部分填金黄、哪部分用朱红>

要求：
1. 作者、画名、印文必须全部虚构，不得使用真实宋代画家名或真实画作名；
2. 题跋要像古人的口吻，不要现代词（如工厂、实验室、摄影、展览）；
3. 图的提示词要能独立看懂画的是什么，构图必须是横向横幅，并写明哪部分填金黄、哪部分用朱红；
4. 每个字段都要短，全文不超过 140 字。"""

FALLBACK_ENTRY = {
    "画名": "秋林曳杖图",
    "作者": "江村陆氏，号拙庵",
    "朝代": "南宋",
    "题材": "山水",
    "题跋": "霜落空山有客归，路深不辨斜阳微。",
    "印文": "拙庵居士",
    "图": "一幅宋代山水横幅手卷，左右展开的平远构图，右侧层叠淡墨山峦，左侧大片留白与小舟，近处枯树与策杖行人；远山填金黄，人物衣袍用朱红",
}


# --------------------------------------------------------------------------
# 状态与藏品目录
# --------------------------------------------------------------------------


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
        tmp = STATE_FILE.with_name(STATE_FILE.name + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        tmp.replace(STATE_FILE)
    except OSError:
        logger.warning("[Danqing] 写状态失败", exc_info=True)


def _catalog(state: dict) -> list:
    items = state.get("catalog")
    return items if isinstance(items, list) else []


# --------------------------------------------------------------------------
# 题签（LLM）
# --------------------------------------------------------------------------


def _parse_entry(raw: str) -> dict:
    """解析 LLM 输出的题签，取到字段为止。"""
    entry: dict = {}
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line or "：" not in line:
            continue
        key, _, value = line.partition("：")
        key = key.strip()
        value = value.strip().strip("「」『』\"'")
        if key in ENTRY_KEYS and value:
            entry[key] = value
    return entry


async def _llm_entry(index: int, recent: list, genre: str, **kwargs) -> dict:
    """向 LLM 要一幅虚构宋代绘画。

    不用 token 上限去卡输出长度，而是在提示词里把每个字段的字数写死，
    让它自己收尾；max_tokens 只保证推理有地方烧，不至于把正文挤没。
    """
    recent_txt = "、".join(
        f"{r.get('title', '')}({r.get('genre', '')})" for r in recent if r.get("title")
    ) or "（暂无）"
    prompt = ENTRY_PROMPT.format(index=index, recent=recent_txt, genre=genre)

    from .content import _call_llm

    provider = str(kwargs.get("llm_provider") or "").strip() or "minimax"
    model = str(kwargs.get("llm_model") or "").strip()
    models = (model,) if model else LLM_MODELS

    for model_name in models:
        for attempt in range(2):
            raw = ""
            try:
                raw = await _call_llm(
                    provider,
                    model_name,
                    prompt,
                    temperature=0.9,
                    # 推理模型：额度给足，推理有地方烧，正文不会被挤成空。
                    max_tokens=12000,
                    api_key=kwargs.get("api_key"),
                    base_url=kwargs.get("llm_base_url"),
                )
            except Exception as exc:
                logger.warning(
                    "[Danqing] LLM 生成题签失败(%s 第 %s 次): %s",
                    model_name, attempt + 1, exc,
                )
                raw = ""
            if raw and raw.strip():
                break
            logger.warning("[Danqing] LLM 输出为空: %s 第 %s 次", model_name, attempt + 1)

        entry = _parse_entry(raw)
        if entry.get("画名"):
            for key in ("作者", "朝代", "题材", "题跋", "印文"):
                entry.setdefault(key, FALLBACK_ENTRY[key])
            entry.setdefault("图", FALLBACK_ENTRY["图"])
            return entry
        logger.warning("[Danqing] 题签解析失败(%s): %r", model_name, raw[:200])

    return dict(FALLBACK_ENTRY)


# --------------------------------------------------------------------------
# 生图 → 上屏准备
# --------------------------------------------------------------------------


def _snap_hue_to_ink(img):
    """把画心的色相硬映射到屏上四色（丹青谱专用，绝不进 eink_snap 共享层）。

    为什么需要：宋代山水模型默认自带花青石绿 —— 实测该图 16.6% 像素是绿/青/蓝相
    （躺平巴拉只有 2.9%，天工阁 0.3%）。屏上四色没有绿，那部分色相在 Atkinson
    里没有归属，只能被抖成黑黄椒盐噪点：黄色最大连通块 11%、孤立率 61%。
    映射之后同一张图变成黄色最大块 51%、孤立率 43%。

    映射规则：
      纸白（低饱和高亮）-> 纯白 #FFFFFF
      纯红相（<= 14°）   -> 朱红 #C80000（点睛保留）
      橙金相（15°~48°）  -> 金黄 #E8B000，暗部归墨黑（远山留皴法）
      其余色相（绿/青/蓝/紫/灰）-> 墨黑 #000000

    绿树变墨树不是降级：宋代山水本来就以水墨为主，这是「墨分五色」的正统画法。

    必须在 eink_snap.prepare_direct_art 之后调用：先备好尺寸与明度，再归色相。
    顺序反过来（先归色相再备）会因为 _stretch_value 把已归好的色相重新拉伸而劣化
    —— 实测黄色孤立率 52% vs 43%。
    """
    from PIL import Image

    hch, sch, vch = img.convert("HSV").split()
    rgb = img.convert("RGB")
    hp, sp, vp = hch.load(), sch.load(), vch.load()
    rp = rgb.load()

    out = Image.new("RGB", img.size)
    op = out.load()
    width, height = img.size

    for y in range(height):
        for x in range(width):
            r, g, b = rp[x, y]
            sat = sp[x, y]
            val = vp[x, y]
            hue_deg = hp[x, y] * 360.0 / 255.0

            if sat <= _PAPER_SNAP_S and val >= _PAPER_SNAP_V:
                op[x, y] = _SNAP_WHITE
                continue

            is_red = hue_deg <= _RED_HUE_MAX or hue_deg >= 360 - _RED_HUE_MAX
            is_gold = (not is_red) and hue_deg <= _GOLD_HUE_MAX

            if (is_red or is_gold) and sat >= _WARM_MIN_S:
                if is_red:
                    op[x, y] = _SNAP_RED
                else:
                    op[x, y] = _SNAP_YELLOW if val >= _GOLD_MIN_V else _SNAP_BLACK
                continue

            op[x, y] = _SNAP_BLACK

    return out


def prepare_art_file(src: Path, dest: Path) -> tuple[bool, str]:
    """把生图原图备成可直接上屏的图：裁白边（撑成目标比例）→ 保线下采样。

    不做四色归色：颜色交给渲染器的 Atkinson 抖动（标准路径，输出精确四色）。
    这里只保证下采样不把细墨线平均掉。

    返回 (是否成功, 说明)。只有真出错（读不到图/写不出文件）才算失败。
    """
    try:
        from PIL import Image
    except Exception:  # pragma: no cover
        return False, "PIL 不可用"

    try:
        src_img = Image.open(src).convert("RGB")
    except Exception as exc:
        return False, f"读图失败: {exc}"

    dw, dh = ART_DISPLAY
    try:
        art_img = eink_snap.prepare_direct_art(src_img, dw, dh)
        art_img = _snap_hue_to_ink(art_img)
    except Exception as exc:
        return False, f"上屏准备失败: {exc}"

    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        art_img.save(dest, "PNG")
    except OSError as exc:
        return False, f"写文件失败: {exc}"

    return True, f"{dw}x{dh}"


async def _generate_art(entry: dict, dest: Path, **kwargs) -> tuple[bool, str]:
    """调 MiniMax 生图 → 下载 → 备成上屏尺寸。真出错时换提示词再试一次。"""
    import httpx

    key = (kwargs.get("image_api_key") or "").strip() or os.getenv("MINIMAX_API_KEY", "").strip()
    if not key or key.startswith("sk-your-"):
        return False, "没有可用的生图凭证"

    desc = str(entry.get("图") or "").strip()
    if not desc:
        desc = "一幅宋代册页画，浅墨山水，大片留白"

    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    reasons = []
    prompts = (
        DANQING_IMAGE_PROMPT_TMPL.format(desc=desc, palette=DANQING_PALETTE_SPEC),
        DANQING_IMAGE_PROMPT_RETRY.format(desc=desc),
    )
    for prompt in prompts:
        payload = {
            "model": IMAGE_MODEL,
            "prompt": prompt,
            # 不发 aspect_ratio：两者同时给时它优先，width/height 会失效
            "width": IMAGE_WIDTH,
            "height": IMAGE_HEIGHT,
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

        ok, reason = prepare_art_file(tmp, dest)
        try:
            tmp.unlink()
        except OSError:
            pass
        if ok:
            return True, reason
        reasons.append(reason)

    return False, "；".join(reasons) or "生图未成功"


# --------------------------------------------------------------------------
# 私印
# --------------------------------------------------------------------------


def _seal_path(seal_text: str) -> str:
    """朱文方印：按印文画一枚 2x2 的红框红字印，生成一次就复用。

    像素是精确的 (200,0,0)，与屏上调色板一致，Atkinson 抖动时误差为 0。
    """
    chars = [c for c in (seal_text or "") if c.strip()][:4]
    if not chars:
        return ""
    digest = hashlib.md5("".join(chars).encode("utf-8")).hexdigest()[:10]
    path = ART_DIR / f"seal_{digest}.png"
    if path.exists():
        return f"/static/danqing/{path.name}"
    try:
        from PIL import Image, ImageDraw

        from .patterns.utils import load_font

        size = 44
        img = Image.new("RGB", (size, size), (255, 255, 255))
        draw = ImageDraw.Draw(img)
        red = (200, 0, 0)
        draw.rectangle([1, 1, size - 2, size - 2], outline=red, width=2)
        font = load_font("noto_serif_bold", 12)
        if len(chars) <= 2:
            # 一列两字（竖排）
            cx = size // 2
            for i, ch in enumerate(chars):
                y = size // 2 + (i - (len(chars) - 1) / 2) * 16
                draw.text((cx, y), ch, fill=red, font=font, anchor="mm")
        else:
            cx = size // 2
            for i, ch in enumerate(chars):
                row, col = divmod(i, 2)
                x = cx + (-11 if col == 0 else 11)
                y = 13 + row * 16
                draw.text((x, y), ch, fill=red, font=font, anchor="mm")
        ART_DIR.mkdir(parents=True, exist_ok=True)
        img.save(path, "PNG")
    except Exception as exc:
        logger.warning("[Danqing] 私印生成失败: %s", exc)
        return ""
    return f"/static/danqing/{path.name}"


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------


def _cleanup_art(date_key: str, keep_days: int = 7) -> None:
    """清理过期画心：保留最近 keep_days 天（含今天），其余删掉。

    只按文件名里的日期判断，不碰 seal_*.png 这类固定素材。
    """
    try:
        today = datetime.strptime(date_key, "%Y-%m-%d")
    except ValueError:
        return
    keep: set[str] = set()
    for offset in range(max(0, int(keep_days))):
        keep.add((today - timedelta(days=offset)).strftime("%Y%m%d"))
    try:
        for path in ART_DIR.glob("art_*.png"):
            parts = path.stem.split("_")
            if len(parts) < 3:
                continue
            stamp = parts[1]
            if stamp in keep:
                continue
            try:
                path.unlink()
            except OSError:
                continue
    except OSError:
        pass


async def build_danqing_content(fallback: dict | None = None, **kwargs) -> dict:
    now = datetime.now()
    date_key = now.strftime("%Y-%m-%d")
    state = _read_state()

    days = state.get("days")
    if not isinstance(days, dict):
        days = {}
    today = days.get(date_key)

    if not isinstance(today, dict) or not today.get("画名"):
        catalog = _catalog(state)
        index = len(catalog) + 1
        genre = GENRES[(index - 1) % len(GENRES)]
        recent = catalog[-7:]
        entry = await _llm_entry(index, recent, genre, **kwargs)

        art_name = f"art_{date_key.replace('-', '')}_{index:03d}.png"
        art_path = ART_DIR / art_name
        art_url = ""
        if art_path.exists():
            ok, reason = True, "复用已有图"
        else:
            ok, reason = await _generate_art(entry, art_path, **kwargs)
        if ok:
            art_url = f"/static/danqing/{art_name}"
            _cleanup_art(date_key)
        else:
            logger.warning("[Danqing] 第 %s 幅没出图: %s", index, reason)

        today = {
            "index": index,
            "date": date_key,
            "genre": genre,
            "画名": entry.get("画名", ""),
            "作者": entry.get("作者", ""),
            "朝代": entry.get("朝代", ""),
            "题材": entry.get("题材", ""),
            "题跋": entry.get("题跋", ""),
            "印文": entry.get("印文", ""),
            "图": entry.get("图", ""),
            "art_url": art_url,
            "art_note": reason,
        }
        days[date_key] = today
        catalog.append(
            {
                "index": index,
                "date": date_key,
                "title": today["画名"],
                "genre": genre,
                "author": today["作者"],
            }
        )
        state["catalog"] = catalog
        state["days"] = days
        _write_state(state)

    content = {
        "index_label": f"第 {today.get('index', 1)} 幅",
        "title": today.get("画名", ""),
        "artist": today.get("作者", ""),
        "dynasty": today.get("朝代", ""),
        "genre": today.get("题材", ""),
        "colophon": today.get("题跋", ""),
        "seal_url": _seal_path(str(today.get("印文", ""))),
        "art_url": today.get("art_url", ""),
        "has_image": "yes" if today.get("art_url") else "no",
    }
    if isinstance(fallback, dict):
        for key, value in fallback.items():
            content.setdefault(key, value)
    return content
