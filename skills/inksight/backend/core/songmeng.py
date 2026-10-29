"""大宋梦影 provider：每日一则宋代市井梦景，四色卡通版画上屏。

产出：
- 场景 / 题记 / 画面：由 LLM 当日现编（梦影即虚构，不必对得上史实）。
- 图：MiniMax image-01 按屏上四色盘（白/黑/金黄/朱红）画成卡通版画，
  下载后裁白边 + 保线下采样，按显示尺寸 1:1 存本地。

为什么走卡通：
四色屏只有 4 个物理色，任何写实中间调都会被 Atkinson 抖成椒盐噪点
（实测丹青谱那张图 17% 像素是绿青蓝相，冷色相在四色里没有归属）。
卡通 = 大色块 + 粗墨线，天然少中间调，正是这套屏的舒适区。
躺平巴拉已用同一套管线通过肉眼验收，本模式逐字沿用其路径。

与躺平巴拉一致的硬约束：
- 不做预四色化，颜色交给渲染器的 Atkinson 抖动（输出精确四色）。
- 用 `prepare_direct_art` 的**默认参数**（min-pool 保细墨线），不改共享层。
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

logger = logging.getLogger(__name__)

_BACKEND_DIR = Path(__file__).resolve().parent.parent
STATE_DIR = _BACKEND_DIR / "runtime_cache" / "songmeng"
STATE_FILE = STATE_DIR / "state.json"
LOCK_FILE = STATE_DIR / "generate.lock"
ART_DIR = _BACKEND_DIR / "static" / "songmeng"

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
ART_DISPLAY = (400, 234)

LOCK_STALE_SEC = 900
RECENT_KEEP = 7

DREAM_KEYS = ("场景", "题记", "画面")

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
    "画面只用四种颜色：纯白纸面底、墨黑 #000000 勾线与暗部、金黄 #E8B000 填色、朱红 #C80000 点缀。"
    "同一色系内可以有深浅变化：填色允许深浅不一的层次（深金黄到淡金黄），"
    "暗部允许更深的金黄与浓朱红，用明暗表现体积与柔和光影；"
    "但不要出现第五种色相（不要蓝、绿、紫、青），不要写实材质纹理，"
    "四色套色版画质感，线条清晰，不要任何文字、印章、标签、水印、边框。"
)

# 卡通造型要求。这是本模式与躺平巴拉的关键差别 —— 主体每天不同，
# 必须把造型语言写死，否则哪天画回写实就踩回中间调的坑。
CARTOON_SPEC = (
    "造型卡通：圆润饱满的 Q 版造型，简笔概括，大色块填充；"
    "墨线轮廓粗而清晰、连续不断，转折圆滑；"
    "不要写实光影，不要材质纹理，不要渐变。"
)

# 第一次失败后重试用的更狠版本：强调墨线清晰、主体完整，同时保留色阶。
PALETTE_SPEC_RETRY = (
    "只用四种颜色，同一色系内保留深浅层次（深金黄到淡金黄、朱红有浓淡），用明暗表现体积；"
    "墨黑 #000000 勾线要粗而清晰、连续不断，主体轮廓完整；"
    "纯白纸面底、墨黑 #000000、金黄 #E8B000、朱红 #C80000，别用第五种色相。"
    "造型卡通：圆润 Q 版、大色块填充，不要写实材质纹理与渐变。"
    "不要任何文字、印章、标签、水印、边框。"
)

DREAM_PROMPT = """你在替大宋的市井编一则梦。

每天一则梦景：宋代市井里的一天 —— 斗茶、蹴鞠、货郎、乞巧、夜市、说书、灯会、踏青……
梦影本来就是梦，不必对得上史实，可以热闹，可以夸张，可以有一点荒诞的快活。

最近几天写过的（不要重复场景）：
{recent}

请输出，严格按下面格式，每行一条，不要任何多余的话，全文不超过 120 字：
场景：<梦里的场景，2-6 个字，如「夜市」「斗茶」「灯市」「货郎担」>
题记：<一句梦话，8-14 字，有画面、有一点暖意，像梦呓，不要格言、不要问句>
画面：<给画师的中文提示词，30-60 字，写清场景、视角、主要人物与动作、光线与梦里那点甜>

要求：
- 场景要是宋代中国的市井日常，不要现代都市、科幻、恐怖、战争；
- 题记只写梦，不写道理；
- 画面里要能看出人在做什么；写清主要人物与周围的东西；
- 不要出现任何现代物品（汽车、手机、屏幕、玻璃幕墙、路灯），不要出现文字或招牌。
"""

FALLBACK_DREAM = {
    "场景": "夜市",
    "题记": "灯下有人把一盏盏灯笼点亮",
    "画面": "宋代夜市，灯笼连成一条暖黄的街，两三个圆头圆脑的小人在摊前张望",
}


def _image_prompt(scene: str, retry: bool = False) -> str:
    scene = (scene or "").strip()
    parts = []
    if scene:
        parts.append(scene)
    parts.append(CARTOON_SPEC)
    parts.append(PALETTE_SPEC_RETRY if retry else PALETTE_SPEC)
    return "。".join(parts)


# --------------------------------------------------------------------------
# 状态与梦景目录
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
        logger.warning("[Songmeng] 写状态失败", exc_info=True)


def _roll_state(state: dict, date_key: str) -> dict:
    """跨日轮换：昨天的挪到 prev（仅供失败兜底），今天清空。历史不留。"""
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
# 梦景（LLM）
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
    """向 LLM 要今天的梦景。空输出/解析失败就换模型。"""
    from .content import _call_llm

    provider = str(kwargs.get("llm_provider") or "").strip() or "minimax"
    model = str(kwargs.get("llm_model") or "").strip()
    models = (model,) if model else LLM_MODELS
    recent_txt = "、".join(
        f"{r.get('scene', '')}「{r.get('colophon', '')}」" for r in recent if r.get("scene")
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
                    "[Songmeng] LLM 生成失败(%s 第 %s 次): %s", model_name, attempt + 1, exc
                )
                raw = ""
            if raw and raw.strip():
                break
            logger.warning("[Songmeng] LLM 输出为空: %s 第 %s 次", model_name, attempt + 1)

        dream = _parse_dream(raw)
        if dream.get("场景") and dream.get("题记"):
            dream.setdefault("画面", "")
            return dream
        logger.warning("[Songmeng] 梦景解析失败(%s): %r", model_name, (raw or "")[:200])

    return {}


# --------------------------------------------------------------------------
# 生图 → 备成上屏尺寸
# --------------------------------------------------------------------------


def prepare_art_file(src: Path, dest: Path) -> tuple[bool, str]:
    """把生图原图备成可直接上屏的图：裁白边（撑成目标比例）→ 保线下采样。

    不做四色归色：颜色交给渲染器的 Atkinson 抖动（标准路径，输出精确四色）。
    这里只保证下采样不把墨线轮廓平均掉 —— 卡通的粗墨线断了最难看。

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
        # 默认参数 = min-pool 保细墨线。躺平巴拉同一路径已通过肉眼验收。
        art_img = eink_snap.prepare_direct_art(src_img, dw, dh)
    except Exception as exc:
        return False, f"上屏准备失败: {exc}"

    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        art_img.save(dest, "PNG")
    except OSError as exc:
        return False, f"写文件失败: {exc}"

    return True, f"{dw}x{dh}"


async def _generate_image(scene: str, dest: Path, **kwargs) -> tuple[bool, str]:
    """调 MiniMax 生图 → 下载 → 备成上屏尺寸。真出错时换提示词再试一次。"""
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
    if not rel.startswith("songmeng/"):
        return False
    path = ART_DIR / rel[len("songmeng/"):]
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
                dream = {
                    "场景": prev.get("scene", ""),
                    "题记": prev.get("colophon", ""),
                    "画面": "",
                }
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
                art_url = f"/static/songmeng/{art_name}"
                has_image = "yes"
                note = reason
            else:
                note = f"无图：{reason}"
                logger.warning("[Songmeng] %s 没出图: %s", date_key, reason)

        card = {
            "date": date_key,
            "scene": str(dream.get("场景") or ""),
            "colophon": str(dream.get("题记") or ""),
            "art_url": art_url,
            "has_image": has_image,
            "note": note,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        state["today"] = card
        state["date"] = date_key
        recent.append({"date": date_key, "scene": card["scene"], "colophon": card["colophon"]})
        state["recent"] = recent[-RECENT_KEEP:]
        _write_state(state)
        _cleanup_art(date_key)
        return card
    finally:
        _lock_release()


async def generate_today_card(force: bool = False, **kwargs) -> dict | None:
    """生成（或取回）今天的画心。每天只生成一次。"""
    date_key = datetime.now().strftime("%Y-%m-%d")
    for _ in range(2):
        card = await _generate_once(date_key, force, **kwargs)
        if card is not None:
            return card
    return None


async def build_songmeng_content(fallback: dict | None = None, **kwargs) -> dict:
    date_key = datetime.now().strftime("%Y-%m-%d")
    state = _read_state()
    _roll_state(state, date_key)
    today = state.get("today")
    card = today if isinstance(today, dict) and today.get("date") == date_key else None
    if card is None:
        card = await generate_today_card(**kwargs)

    card = card or {}
    scene = str(card.get("scene") or "")
    colophon = str(card.get("colophon") or "")
    has_image = str(card.get("has_image") or "no")
    content = {
        "scene": scene,
        "colophon": colophon,
        "credit": f"{scene} · {colophon}" if (has_image == "yes" and scene and colophon) else "",
        "art_url": str(card.get("art_url") or ""),
        "has_image": has_image,
    }
    if isinstance(fallback, dict):
        for key, value in fallback.items():
            if not content.get(key):
                content[key] = value
    return content
