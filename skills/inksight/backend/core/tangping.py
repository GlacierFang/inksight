"""躺平巴拉梦境卡 provider：每天一张「躺平巴拉在做梦」的手绘插画卡（四色屏）。

产出三块：
- 地点 + 短句 + 画面提示词：LLM 当日现写（自由发挥，不走种子池，程序校验不撞近期）。
- 图：MiniMax H3 生图 → 后端备成显示尺寸的真彩图 → 按显示尺寸 1:1 存本地。

原则：
- 图是锦上添花，文字保底。生图真出错 → 退成纯文字卡，当天不空。
- LLM 整个失败 → 用保底文案（轮换池），照样出当天的图；不复用昨天的图
  （复用会让昨天的地点、句子、图原样再上一次屏，2026-11-10 就是这样重复了 11-09）。
- 题材不限：古今中外、现代都市都梦（2026-11-14 冰河大人定）。
- 画风：吉卜力风格（2026-11-17 12:08 冰河大人定，见 STYLE）；prompt 不写四色要求。
- **不设质量门槛**（2026-10-26 冰河大人定）：生成了就上屏，只认真出错。
- 存的是真彩图，不做预四色化：颜色归类交给渲染器的 Atkinson 抖动，输出精确四色（2026-10-28 起与天工阁统一）。
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

from . import eink_snap

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
MINIMAX_IMAGE_API_URL = f"{MINIMAX_BASE_URL}/images/generations"
IMAGE_MODEL = "MiniMax-H3-Image"
IMAGE_ASPECT = "16:9"

# 卡片在屏上的显示尺寸，必须与模式 JSON 里 image 块的 width/height 一致（400×300 基准）。
# 一致才能 1:1 落屏：渲染器不再二次缩放，细节不会被平均掉。
CARD_DISPLAY = (400, 230)

# 屏上四色名，与 core/config.py 的 EINK_4COLOR_PALETTE 顺序一致。
PAL_NAMES = ("黑", "白", "金黄", "朱红")

LOCK_STALE_SEC = 900
# 防重复窗口：喂给 prompt 的往期条数（往期记录本身全留，画廊要回看）。
RECENT_KEEP = 14

DREAM_KEYS = ("地点", "短句", "画面")

# 展签用的 LLM：按可靠性排序。第一个空输出/解析失败就换下一个。
LLM_MODELS = ("MiniMax-M3.1-Flash", "MiniMax-M3")

# 生图重试：429/5xx 是限流不是真出错，退避后重试同一个提示词。
# 不重试就直接交无图卡 —— 11-16、11-17 连着两天「生图 HTTP 429」，画面连丢两天。
IMAGE_RETRIES = 3
RETRY_BACKOFF = 20
RETRY_STATUS = (429, 500, 502, 503, 504)

# 画风：2026-11-17 12:08 冰河大人定（对比出图后拍板）。
#   prompt 里不再写四色要求 —— 实测「吉卜力风格 + 画面细腻」不写四色的那张，
#   色彩通透、暗部不闷、水豚毛色自然；写死四色反而把模型的手脚绑住，
#   出来的图平、闷，主体跟背景糊在一起。
#   屏上仍是四色：渲染器的 Atkinson 抖动负责归色，与 prompt 无关。
#   不写「不要文字」：从来没这条要求（以前跟着四色句一起带的）。
STYLE = "吉卜力风格。画面细腻"
# 重试版只多一句：主体别缩在边角（第一次真失败才用）。
# 不能再喊「勾线要粗而清晰」—— 粗描边正是画面粗糙的来源。
STYLE_RETRY = "吉卜力风格。画面细腻。画幅要满，不要缩在边角"

# 2026-11-17 12:08 冰河大人定：主体只留身份，姿势与神态交给当天的画面。
#   原来写死「摊着身子趴着打盹」= 把松弛锁成一种睡姿，模型天天画同一个团子；
#   「短短的耳朵、圆圆的鼻头、眼睛眯着」= 器官清单，会把模型逼成描边插画。
#   09:30 那条「去毛发质感」原本靠删掉「棕色/毛茸茸」实现，现在主体句不带
#   任何色相与毛发词，也不再另加禁令 —— 不加东西。
SUBJECT = "一只圆滚滚的水豚"

# 梦话：地点 + 短句 + 画面提示词。
# 「画面」这一项要留着 —— 写足画面，模型才画得出有景深的画面；
# 只给「地点 + 短句」两个短语会画成图标（11-15 的「云朵被窝」就成了一张卡通贴纸）。
# 写法对齐丹青谱的「图」字段：只写画什么、什么气氛、周围有什么细节，
# 不写成排版指令（不规定左右、不指定留白、不指定哪里填金黄）。
# 题材仍然放开（2026-11-14 冰河大人定）：现代的、旧的、野的都行。
DREAM_PROMPT = """你在替一只叫「躺平巴拉」的水豚写它今晚的梦。
这只水豚圆滚滚、爱躺平、怕麻烦，但心里柔软。它今天做了一个梦。

最近几天写过的梦（不要重复地点，也不要重复句子）：
{recent}
{avoid}

请输出，严格按下面格式，每行一条，不要任何多余的话，全文不超过 160 字：
地点：<梦中地点，2-6 个字>
短句：<一句梦话，8-16 个字>
画面：<给画师的一段中文描述，50-100 字，写足这幅画：画的是什么、什么气氛、周围有哪些东西和细节、水豚在里面是什么样子>

要求：
- 地点可以是古今中外任何地方，也可以是很具体的角落；现代的、旧的、野的都行。
- 短句只写梦，不写道理，不要格言、不要问句。
- 画面只写画什么、什么气氛，不要写成排版指令：不要规定左右各放什么、不要指定留白位置、
  不要指定哪里填金黄哪里用朱红——交给画师自己判断，每天的画才画得不一样。
- 主语和落点跟前几天不一样：换个说法，别再写「把…落在我…」。
"""

# 撞词时补的一句：点名要避开的地点和句子（只在重试时插进 prompt）。
AVOID_HINT = """
刚才那几条已经用过了，换个全新的地点和说法：
{avoid_txt}
"""

# 保底文案池：LLM 整个失败时用。轮换着取，别让保底卡自己天天撞车
# （11-16、11-17 连着两天都是「云上」）。
FALLBACK_DREAMS = (
    {"地点": "云上", "短句": "梦里有条不赶时间的河"},
    {"地点": "旧屋顶", "短句": "风把睡意铺了满满一层"},
    {"地点": "苔石阶", "短句": "水声在石头上睡了一觉"},
    {"地点": "晾衣绳下", "短句": "阳光把布单晒得打盹"},
    {"地点": "末班电车", "短句": "车轮替我数着到站"},
)
FALLBACK_DREAM = dict(FALLBACK_DREAMS[0])


def _pick_fallback(recent: list) -> dict:
    """从保底池里挑一条最近没用过的。"""
    used = {str(r.get("place") or "") for r in recent if isinstance(r, dict)}
    for item in FALLBACK_DREAMS:
        if item["地点"] not in used:
            return dict(item)
    return dict(FALLBACK_DREAMS[0])


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
    """跨日轮换：昨天的卡挪到 prev（只留档，不再用于兜底出图），今天清空。"""
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


def _is_duplicate(dream: dict, recent: list) -> str:
    """梦和最近几条撞了没。撞了返回撞上的字段名（地点/短句），没撞返回空串。"""
    place = str(dream.get("地点") or "")
    line = str(dream.get("短句") or "")
    for item in recent:
        if not isinstance(item, dict):
            continue
        if place and place == str(item.get("place") or ""):
            return "地点"
        if line and line == str(item.get("line") or ""):
            return "短句"
    return ""


async def _llm_dream(recent: list, **kwargs) -> dict:
    """向 LLM 要今晚的梦。空输出/解析失败/和前几天撞词，就换模型重来。"""
    from .content import _call_llm

    provider = str(kwargs.get("llm_provider") or "").strip() or "minimax"
    model = str(kwargs.get("llm_model") or "").strip()
    models = (model,) if model else LLM_MODELS
    recent_txt = "、".join(
        f"{r.get('place', '')}「{r.get('line', '')}」" for r in recent if r.get("place")
    ) or "（暂无）"
    avoid_txt = ""

    for model_name in models:
        for attempt in range(2):
            prompt = DREAM_PROMPT.format(recent=recent_txt, avoid=avoid_txt)
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
                    "[Tangping] LLM 生成失败(%s 第 %s 次): %s", model_name, attempt + 1, exc
                )
                raw = ""
            if raw and raw.strip():
                break
            logger.warning("[Tangping] LLM 输出为空: %s 第 %s 次", model_name, attempt + 1)

        dream = _parse_dream(raw)
        if dream.get("地点") and dream.get("短句"):
            dup = _is_duplicate(dream, recent)
            if not dup:
                return dream
            # 撞词：点名避开，换模型再来一次
            logger.warning(
                "[Tangping] 梦境撞词(%s): %s「%s」", model_name, dup, dream.get(dup)
            )
            avoid_txt = AVOID_HINT.format(
                avoid_txt=f"「{dream.get('地点')}」「{dream.get('短句')}」"
            )
            continue
        logger.warning("[Tangping] 梦境解析失败(%s): %r", model_name, (raw or "")[:200])

    return {}


# --------------------------------------------------------------------------
# 生图 → 备成上屏尺寸
# --------------------------------------------------------------------------


def _prepare_card(src: Path, dest: Path) -> tuple[bool, str]:
    """把生图备成可直接上屏的图：裁白边 → 明度归一 → 纸面置白 → 保线下采样。

    **不做四色归色**：颜色交给渲染器的 Atkinson 抖动（标准路径，输出精确四色）。
    这里只保证下采样不把细节平均掉 —— 那才是原来细节变噪点的真正原因。

    返回 (是否成功, 四色占比说明)。只有真出错（读不到图/写不出文件）才算失败。
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
    try:
        card_img = eink_snap.prepare_direct_art(img, dw, dh)
    except Exception as exc:
        return False, f"上屏准备失败: {exc}"

    # 四色占比：按渲染器真正会用的 Atkinson 抖动脉冲算一遍，
    # 描述屏上实际会看到什么，不是质量门槛。
    counts = {}
    try:
        from collections import Counter

        from . import native_dither

        cnt = Counter(native_dither.atkinson_palette(card_img, 4).getdata())
        total = dw * dh
        counts = {PAL_NAMES[i]: cnt.get(i, 0) / total for i in range(4)}
    except Exception:  # pragma: no cover
        pass

    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        card_img.save(dest, "PNG")
    except OSError as exc:
        return False, f"写文件失败: {exc}"

    desc = " ".join(f"{k}{v * 100:.1f}%" for k, v in counts.items())
    return True, desc


def _image_prompt(scene: str, retry: bool = False) -> str:
    """主体 → 当天的画面 → 画风。

    场景优先用 LLM 写的「画面」；没有就退回地点 + 短句。
    不写四色要求、不写禁文字 —— 屏上四色由渲染器抖动归色。
    """
    scene = (scene or "").strip()
    parts = [SUBJECT]
    if scene:
        parts.append(scene)
    parts.append(STYLE_RETRY if retry else STYLE)
    return "。".join(parts)


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
            "area": "1K",
            "n": 1,
            "response_format": "url",
            # 新端点没有 prompt_optimizer/aigc_watermark；disable_prompt_enhanced 反向映射旧开关
            "disable_prompt_enhanced": False,  # 与小王子一致：开增强
        }
        resp = None
        for attempt in range(IMAGE_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=180.0) as client:
                    resp = await client.post(MINIMAX_IMAGE_API_URL, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                reasons.append(f"生图请求失败: {exc}")
                break
            if resp.status_code == 200:
                break
            if resp.status_code in RETRY_STATUS and attempt < IMAGE_RETRIES - 1:
                # 限流/服务端抖动：退避后重试同一个提示词，别直接交无图卡
                logger.warning(
                    "[Tangping] 生图 HTTP %s，%s 秒后重试",
                    resp.status_code,
                    RETRY_BACKOFF * (attempt + 1),
                )
                await asyncio.sleep(RETRY_BACKOFF * (attempt + 1))
                continue
            reasons.append(f"生图 HTTP {resp.status_code}")
            break
        if resp is None or resp.status_code != 200:
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

        ok, reason = _prepare_card(tmp, dest)
        try:
            tmp.unlink()
        except OSError:
            pass
        if ok:
            return True, reason
        reasons.append(reason)

    return False, "；".join(reasons) or "生图未成功"


# --------------------------------------------------------------------------
# 图文件：往期永久保留，不再按天清理
# --------------------------------------------------------------------------


def _cleanup_art(date_key: str) -> None:
    """不清理了：往期梦境永久保留（2026-10-29 起），理由同天工阁。"""
    return


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

        recent = _recent(state)

        dream = await _llm_dream(recent[-RECENT_KEEP:], **kwargs)

        art_url = ""
        has_image = "no"
        note = ""
        fallback_used = False
        if not dream:
            # LLM 整体失败 → 文字保底，照样按当天的地点生图。
            # 不再复用昨天的图：复用会让昨天的地点、句子、图原样再上一次屏。
            dream = _pick_fallback(recent)
            fallback_used = True

        place = str(dream.get("地点") or "")
        line = str(dream.get("短句") or "")
        # 场景优先用 LLM 写的「画面」；没有就退回地点 + 短句。
        scene = str(dream.get("画面") or "") or "，".join(p for p in (place, line) if p)
        art_name = f"dream_{date_key.replace('-', '')}.png"
        art_path = ART_DIR / art_name
        image_prompt = _image_prompt(scene)
        ok, reason = await _generate_image(scene, art_path, **kwargs)
        if ok:
            art_url = f"/static/tangping/{art_name}"
            has_image = "yes"
            note = f"文字保底；{reason}" if fallback_used else reason
        else:
            note = f"无图：{reason}"
            logger.warning("[Tangping] %s 没出图: %s", date_key, reason)

        card = {
            "date": date_key,
            "place": place,
            "line": line,
            "art_url": art_url,
            "has_image": has_image,
            "note": note,
            # 实际发出去的画面提示词，落盘备查（以后调画风不用再靠猜）
            "image_prompt": image_prompt,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        state["today"] = card
        state["date"] = date_key
        recent = [r for r in recent if r.get("date") != date_key]
        recent.append({
            "date": date_key,
            "place": card["place"],
            "line": card["line"],
            "art_url": art_url,
            "has_image": has_image,
            "image_prompt": image_prompt,
        })
        state["recent"] = recent  # 全留：画廊要回看往期（prompt 只取最近 RECENT_KEEP 条）
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



def _art_url(record: dict, date_key: str) -> str:
    """那天那张素材图：优先用记下的 art_url，旧记录按命名规则找回。"""
    from .history_day import art_url_or_pattern

    return art_url_or_pattern(record, date_key, "tangping", "dream_{date}.png")


def _history_card(state: dict, date_key: str) -> dict:
    """往期记录里那一天的那张卡；没有就标「历史缺失」。"""
    from .history_day import MISSING

    for item in reversed(state.get("recent") or []):
        if isinstance(item, dict) and str(item.get("date")) == date_key:
            art_url = _art_url(item, date_key)
            return {
                "date": date_key,
                "place": str(item.get("place") or MISSING),
                "line": str(item.get("line") or MISSING),
                "art_url": art_url,
                "has_image": "yes" if art_url else "no",
            }
    return {
        "date": date_key,
        "place": MISSING,
        "line": MISSING,
        "art_url": "",
        "has_image": "no",
    }


async def build_tangping_content(fallback: dict | None = None, **kwargs) -> dict:
    from .history_day import resolve_day

    date_key = resolve_day(kwargs)
    state = _read_state()
    today = state.get("today")
    card = today if isinstance(today, dict) and today.get("date") == date_key else None

    # 历史日：从往期记录里找那一天。找不到就如实标「历史缺失」，不补生成。
    if card is None:
        if date_key != datetime.now().strftime("%Y-%m-%d"):
            card = _history_card(state, date_key)
        else:
            _roll_state(state, date_key)
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
