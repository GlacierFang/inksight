"""海错图 provider：每日一则幻想海洋生物图鉴，四色卡通版画上屏。

产出：
- 图鉴条目：名 / 异名 / 尺寸 / 产地 / 特征 / 题识，由 LLM 当日现编（生灵皆虚构）。
- 图：MiniMax image-01 按屏上四色盘（白/黑/金黄/朱红）画成卡通版画，
  下载后裁白边 + 保线下采样，按显示尺寸 1:1 存本地。

水怎么画（2026-10-29 冰河大人定的路子）：
**留白画水**。屏上没有蓝/青，真蓝在四色里没有归属，会被 Atkinson 抖成脏噪点
（实测丹青谱那张图 17% 像素是绿青蓝相）。所以水面不填色 ——
背景保持纯白纸面，只用稀疏墨线勾波纹与涟漪；白即是水。
主体用墨黑粗线勾轮廓，金黄填身体，朱红点睛（眼睛、鳍尖、触手梢、贝壳内壁）。
白 + 黑为主本身就是大色块，几乎不碰中间调那个坑。

与躺平巴拉 / 大宋梦影一致的硬约束：
- 不做预四色化，颜色交给渲染器的 Atkinson 抖动（输出精确四色）。
- 用 prepare_direct_art 的**默认参数**（min-pool 保细墨线），不改共享层。
- 一天只生成一次，并发时等结果不重复扣费。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import eink_snap
from .patterns.utils import load_font

logger = logging.getLogger(__name__)

_BACKEND_DIR = Path(__file__).resolve().parent.parent
STATE_DIR = _BACKEND_DIR / "runtime_cache" / "haicuotu"
STATE_FILE = STATE_DIR / "state.json"
LOCK_FILE = STATE_DIR / "generate.lock"
ART_DIR = _BACKEND_DIR / "static" / "haicuotu"

try:  # 定时任务独立跑时也要能读到 MINIMAX_API_KEY
    from dotenv import load_dotenv as _load_dotenv

    _load_dotenv(_BACKEND_DIR / ".env")
except Exception:  # pragma: no cover - dotenv 缺失不影响主流程
    pass

MINIMAX_BASE_URL = "https://api.minimaxi.com/v1"
MINIMAX_IMAGE_API_URL = f"{MINIMAX_BASE_URL}/image_generation"
IMAGE_MODEL = "image-01"
IMAGE_ASPECT = "16:9"

# 画心在屏上的显示尺寸，必须与模式 JSON 里 image 块的 width/height 一致。
# 一致才能 1:1 落屏：渲染器不再二次缩放，细节不会被平均掉。
#
# 2026-10-29：名称改成烙在图心上缘的题签里（见 _stamp_name），
# 版面与图心尺寸保持第一版不变 —— 图仍占满整个正文区（y=36..270）。
ART_DISPLAY = (400, 234)

# 题签带：生物名称烙在图心**上缘**的留白里，不占版面正文区。
#
# 为什么烙图里而不是用渲染器画：渲染器的中文小字走 9pt 位图字体（PCF），
# 笔画只有 1px，在四色屏上糊成一团（实测副题「云鲛婆」笔画散开）。
# 烙进图里可以自己用 TrueType 画黑字，字号更大、笔画实。
#
# 题签带高度 38px，画心相应压到 196px；两者相加仍是 234，
# 所以模式 JSON 里 image 块的 height 不用改。
LABEL_BAND_H = 38
ART_PAINT_H = 234 - LABEL_BAND_H  # 196

LOCK_STALE_SEC = 900
RECENT_KEEP = 7

ENTRY_KEYS = ("名", "异名", "尺寸", "产地", "特征", "题识", "画")

# 展签用的 LLM：按可靠性排序。第一个空输出/解析失败就换下一个。
LLM_MODELS = ("MiniMax-M3.1-Flash", "MiniMax-M2.7")

# --------------------------------------------------------------------------
# 提示词
# --------------------------------------------------------------------------

# 四色盘说明：直接告诉生图模型要什么颜色，别让它自己发挥。
#
# 允许「同一色系内的深浅变化」：屏上只有四个物理色，没有灰；
# 层次来自同一色相的深浅（深金黄→金黄→淡金黄），不是第五种颜色。
# 渲染器的 Atkinson 抖动把中间调变成网点，于是体积、柔和光影都有。
PALETTE_SPEC = (
    "画面只用四种颜色：纯白纸面底、墨黑 #000000 勾线与暗部、金黄 #E8B000 填色、朱红 #C80000 点睛。"
    "同一色系内可以有深浅变化：填色允许深浅不一的层次（深金黄到淡金黄），"
    "暗部允许更深的金黄与浓朱红，用明暗表现体积与柔和光影；"
    "但不要出现第五种色相（不要蓝、绿、紫、青），不要写实材质纹理，"
    "四色套色版画质感，线条清晰，不要任何文字、印章、标签、水印、边框。"
)

# 卡通造型要求。主体每天不同，必须把造型语言写死，
# 否则哪天画回写实就踩回中间调的坑。
CARTOON_SPEC = (
    "造型卡通：圆润饱满的 Q 版造型，简笔概括，大色块填充；"
    "墨线轮廓粗而清晰、连续不断，转折圆滑；"
    "不要写实光影，不要材质纹理，不要渐变。"
)

# 水怎么画 —— 本模式的关键。
#
# 屏上没有蓝/青，真蓝在四色里没有归属（丹青谱那张图 17% 像素是绿青蓝相），
# 会被 Atkinson 抖成脏噪点。所以**水面不填色**：白纸面即是水，
# 只用稀疏墨线勾波纹与涟漪。主体是大色块，几乎不碰中间调。
WATER_SPEC = (
    "水用留白表示：背景保持纯白纸面，不要给水填色、不要画大块深色水域；"
    "只用稀疏的墨色细波纹与涟漪勾出水面，疏朗留白；"
    "生灵主体用墨黑粗线勾轮廓，金黄填身体主体，朱红点睛（眼睛、鳍尖、触手梢、贝壳内壁）。"
)

# 第一次失败后重试用的更狠版本：强调墨线清晰、主体完整，同时保留色阶。
PALETTE_SPEC_RETRY = (
    "只用四种颜色，同一色系内保留深浅层次（深金黄到淡金黄、朱红有浓淡），用明暗表现体积；"
    "墨黑 #000000 勾线要粗而清晰、连续不断，生灵主体完整；"
    "纯白纸面底、墨黑 #000000、金黄 #E8B000、朱红 #C80000，别用第五种色相。"
    "水仍然用留白表示：纯白背景 + 稀疏墨色波纹，不要给水填色。"
    "造型卡通：圆润 Q 版、大色块填充，不要写实材质纹理与渐变。"
    "不要任何文字、印章、标签、水印、边框。"
)

BIOL_PROMPT = """你在经营一部幻想海洋生物图鉴《海错图》。书中所载皆为从未存在过的海中生灵，**每一则都是虚构的**。
今天要造第 {index} 则。

已经造过的，不要重样（尤其不要同一种生灵或同一种特征）：
{recent}

请输出，严格按下面格式，每行一条，不要任何多余的话，全文不超过 160 字：
名：<4-6 字古雅名，如「墨尾鲛姥」「赤须海蛇」>
异名：<2-4 字别名>
尺寸：<古制尺寸的说法，如「长三尺」「大如舟」>
产地：<虚构海域名，4-6 字，如「沉渊湾」「赤鳍屿外洋」>
特征：<一句话说清它长什么样，12-20 字>
题识：<图旁一行短评，10-16 字，像古人题识的口吻，不要现代词>
画：<给画师的中文提示词，30-60 字，写清主体姿态、水面怎么表示、水上光、哪部分填金黄哪部分用朱红>

要求：
1. 海水是留白，不用颜色画 —— 画提示词里也要写清「水只留白，用稀疏墨线勾波纹」；
2. 生灵主体完整清晰、圆润可爱，不要写实恐怖、不要血腥、不要残肢；
3. 题识与特征只描述「图里画的那个东西」，不写道理、不要现代词（如工厂、实验室、拍摄）；
4. 每行都要短，全文不超过 140 字。"""

FALLBACK_ENTRY = {
    "名": "墨尾鲛姥",
    "异名": "鲛婆",
    "尺寸": "长三尺",
    "产地": "沉渊湾",
    "特征": "圆头无须，尾如墨条，跃起时拖一串水珠",
    "题识": "潮退始见，捕之得盐",
    "画": "一条圆润的鱼形海兽侧身跃出水面，尾部墨黑粗线勾勒并填深墨色，"
    "身体填金黄，眼睛点朱红；背景纯白留白，只有稀疏墨色波纹与涟漪表示水面",
}


def _image_prompt(desc: str, retry: bool = False) -> str:
    desc = (desc or "").strip()
    parts = []
    if desc:
        parts.append(desc)
    parts.append(CARTOON_SPEC)
    parts.append(WATER_SPEC)
    parts.append(PALETTE_SPEC_RETRY if retry else PALETTE_SPEC)
    return "。".join(parts)


# --------------------------------------------------------------------------
# 状态与图鉴目录
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
        logger.warning("[Haicuotu] 写状态失败", exc_info=True)


def _catalog(state: dict) -> list:
    items = state.get("catalog")
    return items if isinstance(items, list) else []


# --------------------------------------------------------------------------
# 并发锁：一天只生成一次，不重复扣费
# --------------------------------------------------------------------------


def _lock_acquire() -> bool:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    for _ in range(2):
        try:
            fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            try:
                age = time.time() - LOCK_FILE.stat().st_mtime
            except OSError:
                age = 0.0
            if age < LOCK_STALE_SEC:
                return False
            try:
                LOCK_FILE.unlink()
            except OSError:
                return False
            continue
        except OSError:
            return False
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(f"{os.getpid()} {time.time():.0f}")
        except OSError:
            pass
        return True
    return False


def _lock_release() -> None:
    try:
        LOCK_FILE.unlink()
    except OSError:
        pass


# --------------------------------------------------------------------------
# 图鉴条目（LLM）
# --------------------------------------------------------------------------


def _parse_entry(raw: str) -> dict:
    """解析 LLM 输出的图鉴条目，取到字段为止。"""
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


async def _llm_entry(index: int, recent: list, **kwargs) -> dict:
    """向 LLM 要今天的图鉴条目。

    不用 token 上限去卡输出长度，而是在提示词里把每个字段的字数写死，
    让它自己收尾；max_tokens 只保证推理有地方烧，不至于把正文挤没。
    """
    recent_txt = "、".join(
        f"{r.get('name', '')}({r.get('origin', '')})" for r in recent if r.get("name")
    ) or "（暂无）"
    prompt = BIOL_PROMPT.format(index=index, recent=recent_txt)

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
                    "[Haicuotu] LLM 生成条目失败(%s 第 %s 次): %s",
                    model_name, attempt + 1, exc,
                )
                raw = ""
            if raw and raw.strip():
                break
            logger.warning("[Haicuotu] LLM 输出为空: %s 第 %s 次", model_name, attempt + 1)

        entry = _parse_entry(raw)
        if entry.get("名"):
            for key in ("异名", "尺寸", "产地", "特征", "题识"):
                entry.setdefault(key, FALLBACK_ENTRY[key])
            entry.setdefault("画", FALLBACK_ENTRY["画"])
            return entry
        logger.warning("[Haicuotu] 条目解析失败(%s): %r", model_name, (raw or "")[:200])

    return dict(FALLBACK_ENTRY)


# --------------------------------------------------------------------------
# 生图 → 上屏准备
# --------------------------------------------------------------------------


def _stamp_name(art, name: str, index_label: str = ""):
    """在图心上缘烙一条题签：左边生物名，右边「第 N 则」。

    题签是纯白底 + 实黑字 + 一条细分隔线，烙完再交给渲染器做四色归色。
    纯黑在四色下是 0 杂点（实测），所以名字不会散。

    返回新图：题签带 + 原画心（总高仍是 ART_DISPLAY 的高）。
    """
    from PIL import Image, ImageDraw

    if not name:
        return art
    w, h = art.size
    band_h = LABEL_BAND_H
    canvas = Image.new("RGB", (w, band_h + h), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)

    name_font = load_font("noto_serif_bold", 20, force_truetype=True)
    idx_font = load_font("noto_serif_bold", 11, force_truetype=True)

    pad = 14
    bbox = draw.textbbox((0, 0), name, font=name_font)
    nh = bbox[3] - bbox[1]
    draw.text((pad, (band_h - nh) // 2 - bbox[1]), name, fill=(0, 0, 0), font=name_font)

    if index_label:
        ib = draw.textbbox((0, 0), index_label, font=idx_font)
        draw.text(
            (w - pad - (ib[2] - ib[0]), (band_h - (ib[3] - ib[1])) // 2 - ib[1]),
            index_label,
            fill=(0, 0, 0),
            font=idx_font,
        )

    draw.line([(0, band_h - 1), (w, band_h - 1)], fill=(0, 0, 0), width=1)
    canvas.paste(art, (0, band_h))
    return canvas


def prepare_art_file(src: Path, dest: Path, name: str = "", index_label: str = "") -> tuple[bool, str]:
    """把生图原图备成可直接上屏的图：裁白边（撑成目标比例）→ 保线下采样 → 烙题签。

    不做四色归色：颜色交给渲染器的 Atkinson 抖动（标准路径，输出精确四色）。
    这里只保证下采样不把墨线轮廓平均掉。

    name 非空时在图心上缘烙一条题签带（名称 + 第 N 则），
    画心相应压到 ART_PAINT_H，题签 + 画心仍是 ART_DISPLAY 的尺寸。

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
    paint_h = ART_PAINT_H if name else dh
    try:
        # 默认参数 = min-pool 保细墨线。躺平巴拉/大宋梦影同一路径已通过肉眼验收。
        art_img = eink_snap.prepare_direct_art(src_img, dw, paint_h)
    except Exception as exc:
        return False, f"上屏准备失败: {exc}"

    if name:
        try:
            art_img = _stamp_name(art_img, name, index_label)
        except Exception as exc:
            return False, f"题签失败: {exc}"

    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        art_img.save(dest, "PNG")
    except OSError as exc:
        return False, f"写文件失败: {exc}"

    return True, f"{dw}x{dh}"


async def _generate_art(
    desc: str,
    dest: Path,
    *,
    name: str = "",
    index_label: str = "",
    **kwargs,
) -> tuple[bool, str]:
    """调 MiniMax 生图 → 下载 → 备成上屏尺寸（带题签）。真出错时换提示词再试一次。"""
    import httpx

    key = (kwargs.get("image_api_key") or "").strip() or os.getenv("MINIMAX_API_KEY", "").strip()
    if not key or key.startswith("sk-your-"):
        return False, "没有可用的生图凭证"

    if not desc:
        desc = FALLBACK_ENTRY["画"]

    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    reasons: list[str] = []
    for prompt in (_image_prompt(desc, False), _image_prompt(desc, True)):
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

        ok, reason = prepare_art_file(tmp, dest, name=name, index_label=index_label)
        try:
            tmp.unlink()
        except OSError:
            pass
        if ok:
            return True, reason
        reasons.append(reason)

    return False, "；".join(reasons) or "生图未成功"


def _cleanup_art(date_key: str, keep_days: int = 7) -> None:
    """不清理了：往期图鉴永久保留（2026-10-29 起）。

    画廊要回看每天那一只。删掉的图找不回来 —— 同一只重画也不是同一只。
    """
    return


def _local_art_exists(url: str) -> bool:
    if not url.startswith("/static/"):
        return False
    rel = url[len("/static/"):]
    if not rel.startswith("haicuotu/"):
        return False
    path = ART_DIR / rel[len("haicuotu/"):]
    try:
        return path.is_file()
    except OSError:
        return False


# --------------------------------------------------------------------------
# 生成
# --------------------------------------------------------------------------


async def _wait_for_card(date_key: str, timeout: float = 180.0) -> dict | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        await asyncio.sleep(3)
        state = _read_state()
        days = state.get("days")
        today = days.get(date_key) if isinstance(days, dict) else None
        if isinstance(today, dict) and today.get("date") == date_key:
            return today
        if not LOCK_FILE.exists():
            break
    return None


async def _generate_once(date_key: str, force: bool, **kwargs) -> dict | None:
    if not _lock_acquire():
        # 别人正在生成：等它的结果，不重复扣费
        return await _wait_for_card(date_key)

    try:
        state = _read_state()
        days = state.get("days")
        if not isinstance(days, dict):
            days = {}
        today = days.get(date_key)
        if isinstance(today, dict) and today.get("date") == date_key and not force:
            return today

        catalog = _catalog(state)
        index = len(catalog) + 1
        recent = catalog[-RECENT_KEEP:]

        entry = await _llm_entry(index, recent, **kwargs)

        art_name = f"beast_{date_key.replace('-', '')}_{index:03d}.png"
        art_path = ART_DIR / art_name
        art_url = ""
        if art_path.exists():
            ok, reason = True, "复用已有图"
        else:
            ok, reason = await _generate_art(
                str(entry.get("画") or ""),
                art_path,
                name=str(entry.get("名") or ""),
                index_label=f"第 {index} 则",
                **kwargs,
            )
        if ok:
            art_url = f"/static/haicuotu/{art_name}"
            _cleanup_art(date_key)
        else:
            logger.warning("[Haicuotu] 第 %s 则没出图: %s", index, reason)

        today = {
            "index": index,
            "date": date_key,
            "名": entry.get("名", ""),
            "异名": entry.get("异名", ""),
            "尺寸": entry.get("尺寸", ""),
            "产地": entry.get("产地", ""),
            "特征": entry.get("特征", ""),
            "题识": entry.get("题识", ""),
            "画": entry.get("画", ""),
            "art_url": art_url,
            "art_note": reason,
        }
        days[date_key] = today
        catalog.append(
            {
                "index": index,
                "date": date_key,
                "name": today["名"],
                "origin": today["产地"],
            }
        )
        state["catalog"] = catalog
        state["days"] = days
        _write_state(state)
        return today
    finally:
        _lock_release()


async def generate_today_card(force: bool = False, **kwargs) -> dict | None:
    """生成（或取回）今天的图鉴条目。每天只生成一次。"""
    date_key = datetime.now().strftime("%Y-%m-%d")
    for _ in range(2):
        card = await _generate_once(date_key, force, **kwargs)
        if card is not None:
            return card
    return None


async def build_haicuotu_content(fallback: dict | None = None, **kwargs) -> dict:
    date_key = datetime.now().strftime("%Y-%m-%d")
    state = _read_state()
    days = state.get("days")
    today = days.get(date_key) if isinstance(days, dict) else None

    if not isinstance(today, dict) or not today.get("名"):
        today = await generate_today_card(**kwargs)

    today = today or {}
    name = str(today.get("名") or "")
    alias = str(today.get("异名") or "")
    size = str(today.get("尺寸") or "")
    origin = str(today.get("产地") or "")
    feature = str(today.get("特征") or "")
    colophon = str(today.get("题识") or "")
    art_url = str(today.get("art_url") or "")

    content = {
        "index_label": f"第 {today.get('index', 1)} 则",
        "name": name,
        "alias": alias,
        "size": size,
        "origin": origin,
        "feature": feature,
        "colophon": colophon,
        "credit": colophon,
        "art_url": art_url,
        "has_image": "yes" if art_url else "no",
    }
    if isinstance(fallback, dict):
        for key, value in fallback.items():
            if not content.get(key):
                content[key] = value
    return content
