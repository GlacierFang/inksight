"""天工阁 provider：每日一件不存在的宋代器物，附虚构展签与四色图录插图。

产出三块：
- 展签：器名 / 器形 / 用途 / 纹样 / 款识 / 来历，由 LLM 当日现写。
- 图：MiniMax image-01 按四色盘出图，下载后在模式侧 snap 成精确调色板值，
  按显示尺寸 1:1 存本地。
- 藏品目录：本地记账，用来防重复（每天的器物门类轮转 + 最近 7 件不重样）。

原则：
- 图是锦上添花，文字才是每天稳定出货的东西。生图真出错时，出纯展签版式，
  不影响当天更新。
- 数值与图都不由展签文字编造——展签只描述「图里画的那个东西」。
- 颜色不是要躲的负担，是这块屏的资源：展签直接指定哪部分填金黄、哪部分用朱红，
  生图按四色盘画，模式侧只做 snap，不做「剥颜色词」这种自废武功的事。
- snap 出来的 PNG 每个像素都是精确调色板值，渲染器的 Atkinson 抖动误差为 0，
  等于无损直通（与藏印、月相图同一招）。
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import eink_snap

logger = logging.getLogger(__name__)

_BACKEND_DIR = Path(__file__).resolve().parent.parent
STATE_DIR = _BACKEND_DIR / "runtime_cache" / "tiangong"
STATE_FILE = STATE_DIR / "state.json"
ART_DIR = _BACKEND_DIR / "static" / "tiangong"

MINIMAX_BASE_URL = "https://api.minimaxi.com/v1"
MINIMAX_IMAGE_API_URL = f"{MINIMAX_BASE_URL}/image_generation"
IMAGE_MODEL = "image-01"
IMAGE_ASPECT = "2:3"

# 器物图在屏上的显示尺寸，必须与模式 JSON 里 image 块的 width/height 一致。
# 这样渲染器 fit=contain 时是 1:1，PIL 对同尺寸 resize 直接 copy 不做插值，
# 精确调色板值可以原样送到 Atkinson 抖动（误差为 0，无黄/红杂色）。
ART_DISPLAY = (160, 200)

# 屏上四色名，与 core/config.py 的 EINK_4COLOR_PALETTE 顺序一致。
PAL_NAMES = ("黑", "白", "金黄", "朱红")

# 补墨目标（不是质量门槛）：墨线太少在墨水屏上会看不见，故加粗到该比例。
BLACK_MIN = 0.010

CATEGORIES = (
    "茶器", "酒器", "香事", "文房", "灯具", "服饰",
    "食器", "供器", "琴器", "梳具",
)

ENTRY_KEYS = ("名称", "器形", "用途", "纹样", "款识", "来历", "图")

# 展签用的 LLM：按可靠性排序。第一个空输出/解析失败就换下一个。
# MiniMax-M3 把思考内联在 content 里且吃满 token 预算，不适合当默认。
LLM_MODELS = ("MiniMax-M3.1-Flash", "MiniMax-M2.7")

# 四色盘说明：直接告诉生图模型要什么颜色，别让它自己发挥。
#
# 关键：允许「同一色系内的深浅变化」。屏上只有四个物理色，没有灰；
# 层次不是靠第五种颜色，而是靠同一色相的深浅（深金黄→金黄→淡金黄）。
# 后端 snap 会把区内明暗映射到相邻色阶上，于是色块有体积，不再是死平涂。
PALETTE_SPEC = (
    "画面只用四种颜色：纯白纸面底、墨黑 #000000 勾线、金黄 #E8B000 填色、朱红 #C80000 点缀。"
    "同一色系内可以有深浅变化：填充的金黄可以带深浅不一的层次（深金黄到淡金黄），"
    "朱红点缀也可以有深浅变化；用明暗表现体积与转折。"
    "但不要出现第五种色相（不要蓝、绿、紫、青），不要写实材质纹理，"
    "宋代图录版画质感，线条清晰，不要任何文字、印章、标签、水印、边框。"
)

# 第一次：按展签指定的配色画。
IMAGE_PROMPT_TMPL = "{desc}。{palette}"
# 第一次失败后的重试版本（仅用于真出错时再试一次）：更强调墨线与朱红点缀。
IMAGE_PROMPT_RETRY = (
    "{desc}。墨线要粗而清晰，器形轮廓完整，朱红部分面积加大一些。"
    "画面只用四种颜色：纯白纸面底、墨黑 #000000 勾线、金黄 #E8B000 填色、朱红 #C80000 点缀。"
    "同一色系内允许深浅变化（深金黄到淡金黄、朱红有浓淡），用明暗表现体积；"
    "不要出现第五种色相，不要写实材质纹理，不要任何文字、印章、水印、边框。"
)

ENTRY_PROMPT = """你在经营一座虚构的博物馆「天工阁」。阁中所藏皆为宋代器物，**但每一件都不曾存在过**。
今天要造第 {index} 号藏品。

已经造过这些，不要重样（尤其不要同门类）：
{recent}

今天指定的门类：{category}

请输出这件器物，严格按下面格式，每行一条，不要任何多余的话，全文不超过 150 字：
名称：<4-6 字古雅名，含器物类别，如「梅瓶式温酒器」>
器形：<一句话 12-18 字，写清器物的形制>
用途：<一句话 10-16 字>
纹样：<一句话 10-16 字>
款识：<一句话 10-16 字>
来历：<一句话 18-26 字，写这件器物如何流传到今天，要有具体年代和地名，但不要出现真实历史人物>
图：<给画师的中文提示词，30-50 字，写清器形轮廓、纹样位置、平视视角，并说明哪部分填金黄、哪部分用朱红>

要求：每个字段都要短，全文不超过 150 字；器物必须是宋代形制；不要用真实存在过的名窑名器（如汝窑、官窑、定窑、哥窑）；来历里不要出现真实历史人物；不要出现现代词（如工厂、实验室、拍卖行）。"""

FALLBACK_ENTRY = {
    "名称": "素面圆盒",
    "器形": "圆口浅腹，盖与身以子母口相合",
    "用途": "盛放香丸或药末",
    "纹样": "素面无纹，仅盖顶一道弦纹",
    "款识": "底刻「天工」二字",
    "来历": "宋理宗淳祐年间作于临安，后随一箱旧物流落北方",
    "图": "一只宋代圆形浅盒，平视，器身填金黄，盖面用朱红",
}

SEAL_TEXT = "天工阁藏"


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
        logger.warning("[Tiangong] 写状态失败", exc_info=True)


def _catalog(state: dict) -> list:
    items = state.get("catalog")
    return items if isinstance(items, list) else []


# --------------------------------------------------------------------------
# 展签（LLM）
# --------------------------------------------------------------------------


def _parse_entry(raw: str) -> dict:
    """解析 LLM 输出的展签，取到字段为止。"""
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


async def _llm_entry(index: int, recent: list, category: str, **kwargs) -> dict:
    """向 LLM 要一件虚构宋代器物。

    不用 token 上限去卡输出长度（那是跟推理模型的脾气较劲），
    而是在提示词里把每个字段的字数写死，让它自己收尾；
    max_tokens 只保证推理有地方烧，不至于把正文挤没。
    """
    recent_txt = "、".join(
        f"{r.get('name', '')}({r.get('category', '')})" for r in recent if r.get("name")
    ) or "（暂无）"
    prompt = ENTRY_PROMPT.format(
        index=index, recent=recent_txt, category=category
    )
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
                    "[Tiangong] LLM 生成展签失败(%s 第 %s 次): %s",
                    model_name, attempt + 1, exc,
                )
                raw = ""
            if raw and raw.strip():
                break
            logger.warning("[Tiangong] LLM 输出为空: %s 第 %s 次", model_name, attempt + 1)

        entry = _parse_entry(raw)
        if entry.get("名称"):
            for key in ("器形", "用途", "纹样", "款识", "来历"):
                entry.setdefault(key, FALLBACK_ENTRY[key])
            entry.setdefault("图", FALLBACK_ENTRY["图"])
            return entry
        logger.warning("[Tiangong] 展签解析失败(%s): %r", model_name, raw[:200])

    return dict(FALLBACK_ENTRY)


# --------------------------------------------------------------------------
# 生图 → 四色 snap
# --------------------------------------------------------------------------


def _crop_content(img):
    """裁掉白边，让器物充满画面。"""
    from PIL import ImageChops

    w, h = img.size
    lum = img.convert("L")
    mask = lum.point(lambda p: 255 if p < 240 else 0)
    # 饱和区域（金黄/朱红）即使亮度高也要算内容
    sat = img.convert("HSV").split()[1].point(lambda p: 255 if p > 60 else 0)
    mask = ImageChops.lighter(mask, sat)
    bbox = mask.getbbox()
    if not bbox:
        return img
    pad = int(max(bbox[2] - bbox[0], bbox[3] - bbox[1]) * 0.03)
    return img.crop((
        max(0, bbox[0] - pad), max(0, bbox[1] - pad),
        min(w, bbox[2] + pad), min(h, bbox[3] + pad),
    ))


def _counts(index_img) -> dict:
    from collections import Counter

    cnt = Counter(index_img.getdata())
    total = index_img.size[0] * index_img.size[1]
    return {PAL_NAMES[i]: cnt.get(i, 0) / total for i in range(4)}


def snap_to_palette(src: Path, dest: Path) -> tuple[bool, str]:
    """把生图 snap 成四色盘：裁白边 → 区域归色（保中间调）→ 补墨。

    只做四色归色（墨线不够就补墨），**不设质量门槛**：生成了就上屏。
    返回 (是否成功, 四色占比说明)。只有真出错（读不到图/写不出文件）才算失败。
    """
    try:
        from PIL import Image
    except Exception:  # pragma: no cover
        return False, "PIL 不可用"

    try:
        src_img = Image.open(src).convert("RGB")
    except Exception as exc:
        return False, f"读图失败: {exc}"

    try:
        cropped = _crop_content(src_img)
    except Exception as exc:
        return False, f"裁白边失败: {exc}"

    dw, dh = ART_DISPLAY
    try:
        index_img = eink_snap.snap_illustration(
            cropped, dw, dh, ink_min=BLACK_MIN
        )
    except Exception as exc:
        return False, f"四色归色失败: {exc}"

    counts = _counts(index_img)

    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        index_img.convert("RGB").save(dest, "PNG")
    except OSError as exc:
        return False, f"写文件失败: {exc}"

    desc = " ".join(f"{k}{v*100:.1f}%" for k, v in counts.items())
    return True, desc


async def _generate_art(entry: dict, dest: Path, **kwargs) -> tuple[bool, str]:
    """调 MiniMax 生图 → 下载 → snap 成四色。真出错时换提示词再试一次。"""
    import httpx

    key = (kwargs.get("image_api_key") or "").strip() or os.getenv("MINIMAX_API_KEY", "").strip()
    if not key or key.startswith("sk-your-"):
        return False, "没有可用的生图凭证"

    desc = str(entry.get("图") or "").strip()
    if not desc:
        desc = "一件宋代器物，平视"

    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    reasons = []
    prompts = (
        IMAGE_PROMPT_TMPL.format(desc=desc, palette=PALETTE_SPEC),
        IMAGE_PROMPT_RETRY.format(desc=desc),
    )
    for prompt in prompts:
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

        ok, reason = snap_to_palette(tmp, dest)
        try:
            tmp.unlink()
        except OSError:
            pass
        if ok:
            return True, reason
        reasons.append(reason)

    return False, "；".join(reasons) or "生图未成功"


# --------------------------------------------------------------------------
# 藏印
# --------------------------------------------------------------------------


def _seal_path() -> str:
    """朱文印：红框红字，4 个字。生成一次就复用。

    像素是精确的 (200,0,0)，与屏上调色板一致，Atkinson 抖动时误差为 0。
    """
    path = ART_DIR / "seal_v1.png"
    if path.exists():
        return "/static/tiangong/seal_v1.png"
    try:
        from PIL import Image, ImageDraw

        from .patterns.utils import load_font

        size = 44
        img = Image.new("RGB", (size, size), (255, 255, 255))
        draw = ImageDraw.Draw(img)
        red = (200, 0, 0)
        draw.rectangle([1, 1, size - 2, size - 2], outline=red, width=2)
        font = load_font("noto_serif_bold", 12)
        chars = list(SEAL_TEXT)
        cx = size / 2
        for i, ch in enumerate(chars):
            row, col = divmod(i, 2)
            x = cx + (-11 if col == 0 else 11)
            y = 13 + row * 16
            draw.text((x, y), ch, fill=red, font=font, anchor="mm")
        ART_DIR.mkdir(parents=True, exist_ok=True)
        img.save(path, "PNG")
    except Exception as exc:
        logger.warning("[Tiangong] 藏印生成失败: %s", exc)
        return ""
    return "/static/tiangong/seal_v1.png"


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------


def _cleanup_art(date_key: str, keep_days: int = 7) -> None:
    """清理过期器物图：保留最近 keep_days 天（含今天），其余删掉。

    天工阁的图比躺平巴拉大得多，不清理会一直堆在 static/tiangong/ 里。
    只按文件名里的日期判断，不碰 seal_v1.png 这类固定素材。
    """
    try:
        today = datetime.strptime(date_key, "%Y-%m-%d")
    except ValueError:
        return
    keep: set[str] = set()
    for offset in range(max(0, int(keep_days))):
        keep.add((today - timedelta(days=offset)).strftime("%Y%m%d"))
    try:
        for path in ART_DIR.glob("obj_*.png"):
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


async def build_tiangong_content(fallback: dict | None = None, **kwargs) -> dict:
    now = datetime.now()
    date_key = now.strftime("%Y-%m-%d")
    state = _read_state()

    days = state.get("days")
    if not isinstance(days, dict):
        days = {}
    today = days.get(date_key)

    if not isinstance(today, dict) or not today.get("名称"):
        catalog = _catalog(state)
        index = len(catalog) + 1
        category = CATEGORIES[(index - 1) % len(CATEGORIES)]
        recent = catalog[-7:]
        entry = await _llm_entry(index, recent, category, **kwargs)

        art_name = f"obj_{date_key.replace('-', '')}_{index:03d}.png"
        art_path = ART_DIR / art_name
        art_url = ""
        if art_path.exists():
            ok, reason = True, "复用已有图"
        else:
            ok, reason = await _generate_art(entry, art_path, **kwargs)
        if ok:
            art_url = f"/static/tiangong/{art_name}"
            _cleanup_art(date_key)
        else:
            logger.warning("[Tiangong] 第 %s 号没出图: %s", index, reason)

        today = {
            "index": index,
            "date": date_key,
            "category": category,
            "名称": entry.get("名称", ""),
            "器形": entry.get("器形", ""),
            "用途": entry.get("用途", ""),
            "纹样": entry.get("纹样", ""),
            "款识": entry.get("款识", ""),
            "来历": entry.get("来历", ""),
            "图": entry.get("图", ""),
            "art_url": art_url,
            "art_note": reason,
        }
        days[date_key] = today
        catalog.append(
            {
                "index": index,
                "date": date_key,
                "name": today["名称"],
                "category": category,
                "keyword": today["纹样"],
            }
        )
        state["catalog"] = catalog
        state["days"] = days
        _write_state(state)

    content = {
        "index_label": f"第 {today.get('index', 1)} 号",
        "name": today.get("名称", ""),
        "shape": today.get("器形", ""),
        "use": today.get("用途", ""),
        "pattern": today.get("纹样", ""),
        "mark": today.get("款识", ""),
        "origin": today.get("来历", ""),
        "art_url": today.get("art_url", ""),
        "has_image": "yes" if today.get("art_url") else "no",
        "seal_url": _seal_path(),
        "collection": "天工阁藏品 · 传世仅此一件",
    }
    if isinstance(fallback, dict):
        for key, value in fallback.items():
            content.setdefault(key, value)
    return content
