"""丹青谱 provider：每日一幅不存在的宋代绘画，附虚构题签与四色画心。

产出：
- 题签：画名 / 作者 / 朝代 / 题材 / 题跋 / 印文，由 LLM 当日现写。
- 图：MiniMax H3 生图按屏上四色盘（白/黑/金黄/朱红）画出横幅式画心，
  下载后裁白边 + 保线下采样，按显示尺寸 1:1 存本地。
- 印：作者私印，用 LLM 给的四字印文本地画一枚朱文方印。

原则：
- 图是锦上添花，题签才是每天稳定出货的东西；生图真出错时出纯题签版式。
- 数值与图都不由题签编造——题签只描述「图里画的那个东西」。
- 不做预四色化：图直接交给渲染器的 Atkinson 抖动。
- 作者、画名必须是虚构的：不得使用真实宋代画家的名字，不得使用真实画作名。

出图提示词（2026-10-30 冰河大人定：原来限制太死）：
- 丹青谱有自己的一套提示词，不再跟天工阁共用器物那套。
- 题签里的「图」字段只描述画面本身（主体、意境、疏密、笔意、气氛），
  不再规定「左右分置 + 固定留白 + 指定两处填色」——那套写死的排版指令
  让每幅画长得一模一样：主体被挤到边角，中间一片空纸。
- 构图交给画师按题材自己定。
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
MINIMAX_IMAGE_API_URL = f"{MINIMAX_BASE_URL}/images/generations"

# 横幅式画心（宋人手卷/横幅一路，横向铺满屏面）。三条屏（400/296/648）都用同一条
# 2.23:1 的比例，画心在每一块屏上都能满宽、不裁切、不留白边。
# 生图与显示尺寸同比例，不裁不压 —— 1152:512 ≈ 392:176。
IMAGE_WIDTH = 1152
IMAGE_HEIGHT = 512
# 新端点只支持 aspect_ratio（width/height 会失效）
IMAGE_ASPECT = "21:9"

# 画心在屏上的显示尺寸，必须与模式 JSON 里 image 块的 width/height 一致。
ART_DISPLAY = (392, 176)

GENRES = (
    "山水", "花鸟", "人物", "楼阁",
    "墨竹", "蔬果", "荷石", "渔隐",
)

ENTRY_KEYS = ("画名", "作者", "朝代", "题材", "题跋", "印文", "图")

# 丹青谱专用四色盘（不再跟天工阁共用器物那套）。
#
# 与天工阁的差别：
# - 不再写死「缩到 150 像素宽」——画心在屏上是 392 像素宽，那个数字是错的，
#   模型为了满足它会把线条和构图一起简化掉。
# - 不再一刀切禁掉灰阶与明暗：层次本来就该有，只是层次要靠同色系浓淡来做，
#   靠第五种色相来做。墨线保持浓黑（接近纯黑）是为了避免下采样后中灰被抖成黄点。
DANQING_PALETTE_SPEC = (
    "画面只用四种颜色：纯白纸面底、墨黑 #000000 勾线、金黄 #E8B000 填色、朱红 #C80000 点缀。"
    "墨要浓，接近纯黑，可以有枯笔飞白与浓淡干湿的笔意，但主线要黑实，不要淡到发灰；"
    "金黄与朱红各自可以有深浅层次，用明暗表现体积、前后与远近。"
    "不要出现第五种色相（不要蓝、绿、紫、青），不要写实材质纹理，不要照片感。"
    "构图、疏密、留白由你按题材自己定，主体要在画面里站得住，不要缩在边角。"
    "宋代绘画的气韵，线条与墨韵本身要好看，不要任何文字、印章、题签、标签、水印、边框。"
)

# 第一次：按题签描述的画面，画成四色画心。
DANQING_IMAGE_PROMPT = "{desc}。{palette}"
# 真出错时的重试版本：更强调墨线与画幅。
DANQING_IMAGE_PROMPT_RETRY = (
    "{desc}。墨线要粗而清晰，画幅要满幅有力，不要只剩边角零星几笔。"
    "画面只用四种颜色：纯白纸面底、墨黑 #000000 勾线、金黄 #E8B000 填色、朱红 #C80000 点缀；"
    "金黄与朱红可加深浅层次表现体积；不要出现第五种色相，不要写实材质纹理；"
    "不要任何文字、印章、题签、标签、水印、边框。"
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
图：<给画师的中文描述，40-70 字。只写画面本身：画的是什么、什么意境、构图疏密与视觉重心、笔意与气氛。横幅卷轴，宽约为高的两倍。>

要求：
1. 作者、画名、印文必须全部虚构，不得使用真实宋代画家名或真实画作名；
2. 题跋要像古人的口吻，不要现代词（如工厂、实验室、摄影、展览）；
3. 图的描述只说画什么，不要写成排版指令：不要规定左右各放什么、不要指定留白位置、
   不要指定哪里填金黄哪里用朱红——这些交给画师自己判断，每天的画才画得不一样；
4. 每个字段都要短，全文不超过 140 字。"""

FALLBACK_ENTRY = {
    "画名": "秋林曳杖图",
    "作者": "江村陆氏，号拙庵",
    "朝代": "南宋",
    "题材": "山水",
    "题跋": "霜落空山有客归，路深不辨斜阳微。",
    "印文": "拙庵居士",
    "图": "一幅宋代山水横幅卷轴，平远江景，远山层叠、近处枯树小舟，疏处见天光，意境萧疏秋寂",
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
            # 「题材」不在这个兜底名单里：ENTRY_PROMPT 根本没向 LLM 要这个字段，
            # 兜一次就等于把每一天都写成「山水」。它由调用方按轮转值补。
            for key in ("作者", "朝代", "题跋", "印文"):
                entry.setdefault(key, FALLBACK_ENTRY[key])
            entry.setdefault("图", FALLBACK_ENTRY["图"])
            return entry
        logger.warning("[Danqing] 题签解析失败(%s): %r", model_name, raw[:200])

    return dict(FALLBACK_ENTRY)


# --------------------------------------------------------------------------
# 生图 → 上屏准备
# --------------------------------------------------------------------------


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
        # 2026-10-28 冰河大人选定：box 下采样 + 只压暗墨色（白纸与彩色不动）
        art_img = eink_snap.prepare_direct_art(
            src_img, dw, dh, downsample="box", ink_gamma=1.35
        )
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
        desc = "一幅宋代山水横幅卷轴，远山近树，意境萧疏"

    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    reasons = []
    prompts = (
        DANQING_IMAGE_PROMPT.format(desc=desc, palette=DANQING_PALETTE_SPEC),
        DANQING_IMAGE_PROMPT_RETRY.format(desc=desc),
    )
    for prompt in prompts:
        payload = {
            "model": IMAGE_MODEL,
            "prompt": prompt,
            # 新端点只认 aspect_ratio + area，不接受 width/height
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
    """不清理了：往期画心永久保留（2026-10-29 起）。

    画廊要回看每天那一张。删掉的画找不回来 —— 同一张重画也不是同一张。
    """
    return



def _art_url(record: dict, date_key: str) -> str:
    """那天那张素材图：优先用记下的 art_url，旧记录按命名规则找回。"""
    from .history_day import art_url_or_pattern

    return art_url_or_pattern(record, date_key, "danqing", "art_{date}_{index:03d}.png")


def _history_content(record: dict, fallback: dict | None) -> dict:
    """历史日的整屏内容：只取记到过的字段，缺的如实标「历史缺失」。"""
    from .history_day import MISSING

    content = {
        "index_label": f"第 {record.get('index', 1)} 幅",
        "title": str(record.get("画名") or MISSING),
        "artist": str(record.get("作者") or MISSING),
        "dynasty": str(record.get("朝代") or MISSING),
        "genre": str(record.get("题材") or MISSING),
        "colophon": str(record.get("题跋") or MISSING),
        "seal_url": _seal_path(str(record.get("印文") or "")),
        "art_url": _art_url(record, date_key),
        "has_image": "yes" if _art_url(record, date_key) else "no",
    }
    if isinstance(fallback, dict):
        for key, value in fallback.items():
            if not content.get(key):
                content[key] = value
    return content


async def build_danqing_content(fallback: dict | None = None, **kwargs) -> dict:
    from .history_day import resolve_day

    date_key = resolve_day(kwargs)
    state = _read_state()

    days = state.get("days")
    if not isinstance(days, dict):
        days = {}
    today = days.get(date_key)

    # 历史日：只读已有记录，不补生成。
    if not isinstance(today, dict) or not today.get("画名"):
        if date_key != datetime.now().strftime("%Y-%m-%d"):
            return _history_content(today or {}, fallback)
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
            # 题材：LLM 明确说了就用它，否则用轮转值。
            # （ENTRY_PROMPT 压根没问 LLM 题材，早先取 entry 只会拿到兜底的「山水」，
            #   于是 8 题材轮转全白转，页面上每天都是山水。）
            "题材": str(entry.get("题材") or genre),
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
