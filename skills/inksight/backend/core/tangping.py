"""躺平巴拉梦境卡 provider：每天一张「躺平巴拉在做梦」的四色套色版画。

产出三块：
- 地点 + 短句：LLM 当日现写（自由发挥，不走种子池，只防近期重复）。
- 图：MiniMax image-01 出图 → 后端 snap 成精确四色调色板 → 按显示尺寸 1:1 存本地。
- 画面本身不受主题限制：古今中外、哪个时代都能梦，只限四色平涂、无渐变、无图内文字。

原则：
- 图是锦上添花，文字保底。生图真出错 → 退成纯文字卡，当天不空。
- LLM 整个失败 → 复用昨天的图，日期由 InkSight 顶栏体现。
- **不设质量门槛**（2026-10-26 冰河大人定）：生成了就上屏，只认真出错。
- snap 出来的 PNG 每个像素都是精确调色板值，渲染器 Atkinson 抖动误差为 0，等于无损直通。
- 不留历史：状态里只留今天、昨天（仅供失败兜底）和最近 7 条防重复记录。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

_BACKEND_DIR = Path(__file__).resolve().parent.parent
STATE_DIR = _BACKEND_DIR / "runtime_cache" / "tangping"
STATE_FILE = STATE_DIR / "state.json"
LOCK_FILE = STATE_DIR / "generate.lock"
ART_DIR = _BACKEND_DIR / "static" / "tangping"

try:  # 定时任务独立跑时也要能读到 MINIMAX_API_KEY
    from dotenv import load_dotenv as _load_dotenv

    _load_dotenv(_BACKEND_DIR / ".env")
except Exception:  # pragma: no cover - dotenv 缺失不影响主流程
    pass

MINIMAX_BASE_URL = "https://api.minimaxi.com/v1"
MINIMAX_IMAGE_API_URL = f"{MINIMAX_BASE_URL}/image_generation"
IMAGE_MODEL = "image-01"
IMAGE_ASPECT = "16:9"

# 卡片在屏上的显示尺寸，必须与模式 JSON 里 image 块的 width/height 一致（400×300 基准）。
# 同尺寸时 PIL 的 resize 是 copy，精确调色板值可以原样送到 Atkinson 抖动（误差 0）。
CARD_DISPLAY = (400, 234)

# 屏上四色，值必须与 core/config.py 的 EINK_4COLOR_PALETTE 一致。
PALETTE_RGB = (
    (0, 0, 0),        # 0 墨黑
    (255, 255, 255),  # 1 纯白
    (232, 176, 0),    # 2 金黄
    (200, 0, 0),      # 3 朱红
)
PAL_NAMES = ("黑", "白", "金黄", "朱红")
WHITE_INDEX = 1

# 质量闸门（整幅 400×234 计）
BLACK_MIN = 0.02     # 补墨目标：墨线太少在墨水屏上会看不见

LOCK_STALE_SEC = 900
RECENT_KEEP = 7

DREAM_KEYS = ("地点", "短句", "画面")

# 展签用的 LLM：按可靠性排序。第一个空输出/解析失败就换下一个。
LLM_MODELS = ("MiniMax-M3.1-Flash", "MiniMax-M2.7")

# 四色盘说明：直接告诉生图模型要什么颜色，别让它自己发挥。
PALETTE_SPEC = (
    "画面只用四种颜色：纯白纸面底、墨黑 #000000 勾线与暗部、金黄 #E8B000 填色、朱红 #C80000 点缀。"
    "平涂色块，无渐变，无阴影，无高光，无写实光影，无材质纹理，"
    "四色套色版画质感，线条清晰，不要任何文字、印章、标签、水印、边框。"
)
# 第一次失败后重试用的更狠版本：强调整片色块与主体轮廓。
PALETTE_SPEC_RETRY = (
    "只用四种颜色平涂，色块要整片，不要任何渐变、灰阶与写实明暗；"
    "墨黑 #000000 勾线要粗而清晰，主体轮廓完整；"
    "纯白纸面底、墨黑 #000000、金黄 #E8B000、朱红 #C80000，别用第五种颜色。"
    "不要任何文字、印章、标签、水印、边框。"
)

SUBJECT = "画面主体是一只圆滚滚的棕色水豚，摊着身子趴着打盹，神态松弛安详，短耳朵、毛茸茸"

DREAM_PROMPT = """你在替一只叫「躺平巴拉」的水豚写它今晚的梦。
这只水豚圆滚滚、爱躺平、怕麻烦，但心里柔软。它今天做了一个梦，梦里有地方、有光、有一点点甜。

最近几天写过的梦（不要重复地点，也不要重复句子）：
{recent}

请输出，严格按下面格式，每行一条，不要任何多余的话，全文不超过 120 字：
地点：<梦中地点，2-5 个字，如「汴京」「琉璃厂」「云上」「海边旧船」>
短句：<一句梦话，8-14 个字，有画面、有一点暖意，像梦呓，不要格言、不要问句>
画面：<给画师的中文提示词，30-60 字，写清场景、视角、光线与梦里那点甜>

要求：
- 地点可以是古今中外的任何地方，也可以是很具体的角落；不要现代都市、科幻、恐怖、战争。
- 短句只写梦，不写道理。
- 画面里要能看出水豚在打盹；写清它趴着的位置和周围的东西。
- 不要出现任何现代物品（汽车、手机、屏幕、玻璃幕墙、路灯），不要出现文字或招牌。
"""

FALLBACK_DREAM = {
    "地点": "云上",
    "短句": "梦里有条不赶时间的河",
    "画面": "",
}


# --------------------------------------------------------------------------
# 状态
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
        logger.warning("[Tangping] 写状态失败", exc_info=True)


def _roll_state(state: dict, date_key: str) -> dict:
    """跨日轮换：昨天的卡挪到 prev（仅供失败兜底），今天清空。历史不留。"""
    if state.get("date") == date_key:
        return state
    today = state.get("today")
    if isinstance(today, dict) and today.get("date"):
        state["prev"] = today
    state["today"] = None
    state["date"] = date_key
    return state


def _recent(state: dict) -> list:
    items = state.get("recent")
    return [r for r in items if isinstance(r, dict)] if isinstance(items, list) else []


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
# 梦境（LLM）
# --------------------------------------------------------------------------


def _parse_dream(raw: str) -> dict:
    out: dict[str, str] = {}
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line or "：" not in line:
            continue
        key, _, value = line.partition("：")
        key = key.strip()
        value = value.strip().strip("「」『』\"'")
        if key in DREAM_KEYS and value:
            out[key] = value
    return out


async def _llm_dream(recent: list, **kwargs) -> dict:
    """向 LLM 要今晚的梦。空输出/解析失败就换模型。"""
    from .content import _call_llm

    provider = str(kwargs.get("llm_provider") or "").strip() or "minimax"
    model = str(kwargs.get("llm_model") or "").strip()
    models = (model,) if model else LLM_MODELS
    recent_txt = "、".join(
        f"{r.get('place', '')}「{r.get('line', '')}」" for r in recent if r.get("place")
    ) or "（暂无）"
    prompt = DREAM_PROMPT.format(recent=recent_txt)

    for model_name in models:
        for attempt in range(2):
            raw = ""
            try:
                raw = await _call_llm(
                    provider,
                    model_name,
                    prompt,
                    temperature=0.95,
                    max_tokens=12000,
                    api_key=kwargs.get("api_key"),
                    base_url=kwargs.get("llm_base_url"),
                )
            except Exception as exc:
                logger.warning(
                    "[Tangping] LLM 生成失败(%s 第 %s 次): %s", model_name, attempt + 1, exc
                )
                raw = ""
            if raw and raw.strip():
                break
            logger.warning("[Tangping] LLM 输出为空: %s 第 %s 次", model_name, attempt + 1)

        dream = _parse_dream(raw)
        if dream.get("地点") and dream.get("短句"):
            dream.setdefault("画面", "")
            return dream
        logger.warning("[Tangping] 梦境解析失败(%s): %r", model_name, (raw or "")[:200])

    return {}


# --------------------------------------------------------------------------
# 生图 → 四色 snap → 本地质量闸门
# --------------------------------------------------------------------------


def _crop_to_ratio(img, ratio: float):
    from PIL import Image

    w, h = img.size
    if w <= 0 or h <= 0:
        return img
    cur = w / h
    if abs(cur - ratio) < 1e-6:
        return img
    if cur > ratio:
        nw = max(1, int(round(h * ratio)))
        left = (w - nw) // 2
        return img.crop((left, 0, left + nw, h))
    nh = max(1, int(round(w / ratio)))
    top = (h - nh) // 2
    return img.crop((0, top, w, top + nh))


def _boost_ink(index: bytearray, lum: list, dw: int, dh: int, target: float) -> None:
    """墨线太少就补墨：先让已有的墨线加粗，还不够就取最暗的一批像素补上。

    e-ink 上没有墨线的图会很淡。但补出来的墨必须是成片的，否则就是噪点——
    所以按亮度排序取整片最暗的像素，而不是随手点几个点。
    """
    total = dw * dh
    need = int(target * total)
    black = sum(1 for v in index if v == 0)
    if black >= need:
        return

    try:
        from PIL import Image, ImageFilter
    except Exception:  # pragma: no cover
        return

    for _ in range(3):
        if black >= need:
            break
        mask = Image.new("L", (dw, dh), 255)
        mp = mask.load()
        for y in range(dh):
            row = y * dw
            for x in range(dw):
                if index[row + x] == 0:
                    mp[x, y] = 0
        grown = mask.filter(ImageFilter.MaxFilter(3)).point(
            lambda p: 0 if p < 128 else 255, mode="L"
        )
        gp = grown.load()
        for y in range(dh):
            row = y * dw
            for x in range(dw):
                if gp[x, y] == 0:
                    index[row + x] = 0
        black = sum(1 for v in index if v == 0)

    if black < need:
        # 只在白底像素里补，不碰已经定色的金黄与朱红（否则四色被吃成三色）
        cand = []
        for p, value in enumerate(index):
            if value == WHITE_INDEX:
                cand.append((lum[p], p))
        cand.sort()
        for _, p in cand[: need - black]:
            index[p] = 0


def _snap_to_palette(src: Path, dest: Path) -> tuple[bool, str]:
    """把生图 snap 成四色盘。

    只做四色归色（墨线不够就补墨），**不设质量门槛**：生成了就上屏。
    返回 (是否成功, 四色占比说明)。只有真出错（读不到图、写不出文件）才算失败。
    """
    try:
        from PIL import Image
    except Exception:  # pragma: no cover
        return False, "PIL 不可用"

    try:
        img = Image.open(src).convert("RGB")
    except Exception as exc:
        return False, f"读图失败: {exc}"

    dw, dh = CARD_DISPLAY
    img = _crop_to_ratio(img, dw / dh).resize((dw, dh), Image.LANCZOS)

    px = img.load()
    index = bytearray(dw * dh)
    lum: list[int] = [0] * (dw * dh)
    for y in range(dh):
        row = y * dw
        for x in range(dw):
            r, g, b = px[x, y]
            lum[row + x] = (r * 299 + g * 587 + b * 114) // 1000
            best = WHITE_INDEX
            best_d = None
            for i, (pr, pg, pb) in enumerate(PALETTE_RGB):
                d = (r - pr) * (r - pr) + (g - pg) * (g - pg) + (b - pb) * (b - pb)
                if best_d is None or d < best_d:
                    best_d = d
                    best = i
            index[row + x] = best

    _boost_ink(index, lum, dw, dh, BLACK_MIN)

    counts = [0, 0, 0, 0]
    for v in index:
        counts[v] += 1
    total = dw * dh

    try:
        rgb = Image.new("RGB", (dw, dh))
        rp = rgb.load()
        for y in range(dh):
            row = y * dw
            for x in range(dw):
                rp[x, y] = PALETTE_RGB[index[row + x]]
        dest.parent.mkdir(parents=True, exist_ok=True)
        rgb.save(dest, "PNG")
    except OSError as exc:
        return False, f"写文件失败: {exc}"

    desc = " ".join(f"{PAL_NAMES[i]}{counts[i] * 100 / total:.1f}%" for i in range(4))
    return True, desc


def _image_prompt(scene: str, retry: bool = False) -> str:
    scene = (scene or "").strip()
    parts = []
    if scene:
        parts.append(scene)
    parts.append(SUBJECT)
    parts.append(PALETTE_SPEC_RETRY if retry else PALETTE_SPEC)
    return "。".join(parts)


async def _generate_image(scene: str, dest: Path, **kwargs) -> tuple[bool, str]:
    """调 MiniMax 生图 → 下载 → snap 成四色。真出错时换提示词再试一次。"""
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

        ok, reason = _snap_to_palette(tmp, dest)
        try:
            tmp.unlink()
        except OSError:
            pass
        if ok:
            return True, reason
        reasons.append(reason)

    return False, "；".join(reasons) or "生图未成功"


# --------------------------------------------------------------------------
# 图文件清理：只留今天与昨天，不留历史
# --------------------------------------------------------------------------


def _cleanup_art(date_key: str) -> None:
    try:
        today = datetime.strptime(date_key, "%Y-%m-%d")
    except ValueError:
        return
    keep = {
        today.strftime("dream_%Y%m%d.png"),
        (today - timedelta(days=1)).strftime("dream_%Y%m%d.png"),
    }
    try:
        for path in ART_DIR.glob("dream_*.png"):
            if path.name in keep:
                continue
            try:
                path.unlink()
            except OSError:
                continue
    except OSError:
        pass


def _local_art_exists(url: str) -> bool:
    if not url.startswith("/static/"):
        return False
    rel = url[len("/static/"):]
    if not rel.startswith("tangping/"):
        return False
    path = ART_DIR / rel[len("tangping/"):]
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
        today = state.get("today")
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
        _roll_state(state, date_key)
        today = state.get("today")
        if isinstance(today, dict) and today.get("date") == date_key and not force:
            return today

        prev = state.get("prev") if isinstance(state.get("prev"), dict) else {}
        recent = _recent(state)

        dream = await _llm_dream(recent[-RECENT_KEEP:], **kwargs)

        art_url = ""
        has_image = "no"
        note = ""
        if not dream:
            # LLM 整体失败 → 复用昨天的图，日期由顶栏体现
            prev_art = str(prev.get("art_url") or "")
            if prev_art and _local_art_exists(prev_art):
                dream = {"地点": prev.get("place", ""), "短句": prev.get("line", ""), "画面": ""}
                art_url = prev_art
                has_image = "yes"
                note = "复用昨日的图"
            else:
                dream = dict(FALLBACK_DREAM)
                note = "文字保底"
        else:
            art_name = f"dream_{date_key.replace('-', '')}.png"
            art_path = ART_DIR / art_name
            ok, reason = await _generate_image(str(dream.get("画面") or ""), art_path, **kwargs)
            if ok:
                art_url = f"/static/tangping/{art_name}"
                has_image = "yes"
                note = reason
            else:
                note = f"无图：{reason}"
                logger.warning("[Tangping] %s 没出图: %s", date_key, reason)

        card = {
            "date": date_key,
            "place": str(dream.get("地点") or ""),
            "line": str(dream.get("短句") or ""),
            "art_url": art_url,
            "has_image": has_image,
            "note": note,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        state["today"] = card
        state["date"] = date_key
        recent.append({"date": date_key, "place": card["place"], "line": card["line"]})
        state["recent"] = recent[-RECENT_KEEP:]
        _write_state(state)
        _cleanup_art(date_key)
        return card
    finally:
        _lock_release()


async def generate_today_card(force: bool = False, **kwargs) -> dict | None:
    """生成（或取回）今天的卡片。每天只生成一次。"""
    date_key = datetime.now().strftime("%Y-%m-%d")
    for _ in range(2):
        card = await _generate_once(date_key, force, **kwargs)
        if card is not None:
            return card
    return None


async def build_tangping_content(fallback: dict | None = None, **kwargs) -> dict:
    date_key = datetime.now().strftime("%Y-%m-%d")
    state = _read_state()
    _roll_state(state, date_key)
    today = state.get("today")
    card = today if isinstance(today, dict) and today.get("date") == date_key else None
    if card is None:
        card = await generate_today_card(**kwargs)

    card = card or {}
    place = str(card.get("place") or "")
    line = str(card.get("line") or "")
    has_image = str(card.get("has_image") or "no")
    content = {
        "place": place,
        "line": line,
        "credit": f"{place} · {line}" if (has_image == "yes" and place and line) else "",
        "art_url": str(card.get("art_url") or ""),
        "has_image": has_image,
    }
    if isinstance(fallback, dict):
        for key, value in fallback.items():
            if not content.get(key):
                content[key] = value
    return content
