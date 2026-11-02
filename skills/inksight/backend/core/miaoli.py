"""狸行记 provider（CUSTOM_MIAOLI）：喵狸的日漫一日。

极简（2026-11-02 冰河大人定的方向）：
- 一句话：给一段 prompt，出一张日漫图。
- 一次 LLM 写出今天画什么（标题 + 画面 + 它的签名），一次生图出一张图。
- 没有骰子、没有状态轴、没有收集册 —— 那些是草地铺道蚁的东西，不是猫的。

屏上结构照抄「透屏」：整版图占满，屏上一句话都没有。
- 画面描述只进生图 prompt，另存进回顾页字段（回头翻能读）。
- 签名进页脚。
- 提示词只给风格要求（2026-11-02 冰河大人定的）：
  「你只需要给风格要求不就好了，折腾这么多有的没的意义在哪里？」
  「你限制的越多，出图的效果越差！」
  所以提示词 = LLM 写的画面 + 四色盘 + 一句风格。
  不写猫的造型，不写角色锚点，不写动态表达 —— 那些交给模型。
- 四色盘写死：屏是四色（core/config.py: EINK_4COLOR_PALETTE =
  黑/白/金黄 #E8B000/朱红 #C80000），把色值告诉模型，别让它自己发挥。
  这是屏幕的物理事实，不是创作限制。

一格单幅（2026-11-02 冰河大人定的）：
- 一格。之前的左右双格效果太差 —— 一次生图只能出一张图，两格塞进同一张里
  每格太小，猫画不清，动作也断。宁可一格画大、画透。
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path

from . import eink_snap

logger = logging.getLogger(__name__)

_BACKEND_DIR = Path(__file__).resolve().parent.parent
STATE_DIR = _BACKEND_DIR / "runtime_cache" / "miaoli"
STATE_FILE = STATE_DIR / "state.json"
LOCK_FILE = STATE_DIR / "generate.lock"
ART_DIR = _BACKEND_DIR / "static" / "miaoli"

try:  # 定时任务独立跑时也要能读到 MINIMAX_API_KEY
    from dotenv import load_dotenv as _load_dotenv

    _load_dotenv(_BACKEND_DIR / ".env")
except Exception:  # pragma: no cover - dotenv 缺失不影响主流程
    pass

MINIMAX_BASE_URL = "https://api.minimaxi.com/v1"
MINIMAX_IMAGE_API_URL = MINIMAX_BASE_URL + "/image_generation"
IMAGE_MODEL = "image-01"
IMAGE_ASPECT = "3:2"

LLM_MODELS = ("MiniMax-M3.1-Flash", "MiniMax-M2.7")

LOCK_STALE_SEC = 900

# 屏上正文区尺寸（400x300 去掉页脚 30px）—— 与透屏一致。
ART_DISPLAY = (400, 270)

CAT_NAME = "喵狸"

# ── 风格与主体：固定在前面 ────────────────────────────────────────────
# 冰河大人 2026-11-02 定的结构：
#   「前面是风格要求：日漫风格，狸花猫，后面是 llm 生成的动作。」
# 风格和主体先读，模型才知道按什么画；动作在后。
STYLE_SPEC = "日漫风格，狸花猫"

# ── 四色盘 ──────────────────────────────────────────────────────────────
# 屏是四色：黑 / 白 / 金黄 #E8B000 / 朱红 #C80000（core/config.py 的 EINK_4COLOR_PALETTE）。
# 这是屏的物理事实，不是创作限制 —— 写成最短的一句。
PALETTE_SPEC = (
    "画面只用四种颜色：纯白纸面底、墨黑 #000000 勾线、金黄 #E8B000 填色、朱红 #C80000 点缀。"
    "不要任何文字、印章、水印。"
)

# 重试只在原提示词后面加一句 —— 照小景的做法，不另写一份平行约束。
RETRY_PROMPT = " 画面里不要有任何文字。"

# ── 提示词 ────────────────────────────────────────────────────────────

DAY_PROMPT = """你在替一只住在院子里的狸花猫「喵狸」写今天的记录。

请写今天的记录，严格按下面格式，每行一条，不要多余的话：
标题：<2-6 字>
画面：<不超过 30 字，一个动作瞬间，看得出它在动>
签名：<不超过 12 字，一句话，是它自己刻在石头上的>

规矩：
- 画面用「它」来写，不要用「我」—— 提示词开头已经有「狸花猫」了，主语不能打架。
- 画面是能画成一张漫画的瞬间：具体的场景，具体的一个动作。
- 画面要有速度：扑、撞、跳、追、钻、翻。
- 像猫一样感知：爪垫、尾巴、鼻子、胡子、风、毛、影子、地上的味道。
- 语气可以痞、可以懒、可以有点仙 —— 流浪猫的那种江湖气。
- 短句，不要抒情成散文。
- 签名是它自己给自己刻的，不是格言。
- 签名里不要出现「喵狸」这个名字（页脚已经写了名字）。
- 不要解释，不要展开。
"""

DEFAULT_SIGNATURE = "此路有我，概不收租。"
STRIP_CHARS = "「」『』" + chr(34) + "'"

# ── 小工具 ────────────────────────────────────────────────────────────


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
        logger.warning("[Miaoli] 写状态失败: %s", exc)


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


def _parse_kv(raw: str, keys: tuple) -> dict:
    out: dict[str, str] = {}
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line or "：" not in line:
            continue
        key, _, value = line.partition("：")
        key = key.strip()
        value = value.strip().strip(STRIP_CHARS)
        if key in keys and value:
            out.setdefault(key, value)
    return out


def _strip_name(signature: str) -> str:
    """签名里不重复猫名 —— 页脚已经是「喵狸·签名」了。

    模型偶尔会写「喵狸来此一爪」，拼起来就成了「喵狸·喵狸来此一爪」。
    """
    text = str(signature or "").strip()
    if text.startswith(CAT_NAME):
        text = text[len(CAT_NAME):].strip("，,。.、：: ")
    return text


def _parse_day(raw: str) -> dict:
    parsed = _parse_kv(raw, ("标题", "画面", "签名"))
    scene = parsed.get("画面", "")
    if not scene:
        return {}
    signature = _strip_name(parsed.get("签名", ""))[:16]
    return {
        "title": parsed.get("标题", "")[:8],
        "scene": scene[:60],
        "signature": signature or DEFAULT_SIGNATURE,
    }


async def _call_llm(prompt: str, temperature: float, **kwargs) -> str:
    from .content import _call_llm as _do_call

    provider = str(kwargs.get("llm_provider") or "").strip() or "minimax"
    model = str(kwargs.get("llm_model") or "").strip()
    models = (model,) if model else LLM_MODELS
    for model_name in models:
        for attempt in range(2):
            try:
                raw = await _do_call(
                    provider,
                    model_name,
                    prompt,
                    temperature=temperature,
                    max_tokens=2000,
                    api_key=kwargs.get("api_key"),
                    base_url=kwargs.get("llm_base_url"),
                )
            except Exception as exc:
                logger.warning("[Miaoli] LLM 失败(%s 第 %s 次): %s", model_name, attempt + 1, exc)
                raw = ""
            if raw and raw.strip():
                return raw
    return ""


async def _llm_day(**kwargs) -> dict:
    raw = await _call_llm(DAY_PROMPT, 0.95, **kwargs)
    card = _parse_day(raw)
    if card:
        return card
    return {
        "title": "今日",
        "scene": "喵狸猛地蹬墙窜上屋脊，胡须被风拉直。",
        "signature": DEFAULT_SIGNATURE,
    }


# ── 出图 ──────────────────────────────────────────────────────────────


def _art_name(date_key: str) -> str:
    return "miaoli_" + date_key.replace("-", "") + ".png"


def _image_prompt(scene: str, retry: bool = False) -> str:
    """生图提示词：风格 + 主体 + LLM 写的动作 + 四色盘。

    冰河大人 2026-11-02 定的结构：
    「前面是风格要求：日漫风格，狸花猫，后面是 llm 生成的动作。」

    只给风格要求。不限定猫的造型，不写角色锚点，不写动态表达 —— 交给模型。
    """
    parts = [
        STYLE_SPEC + "。" + scene.strip() + "。",
        PALETTE_SPEC,
    ]
    prompt = "".join(p for p in parts if p)
    if retry:
        prompt += RETRY_PROMPT
    return prompt


def _prepare_art(src: Path, dest: Path) -> tuple[bool, str]:
    """生图原图 -> 可直接上屏的图（不做四色归色，交给渲染器的 Atkinson 抖动）。"""
    try:
        from PIL import Image
    except Exception:  # pragma: no cover
        return False, "PIL 不可用"
    try:
        img = Image.open(src).convert("RGB")
    except Exception as exc:
        return False, "读图失败: " + str(exc)

    dw, dh = ART_DISPLAY
    try:
        card_img = eink_snap.prepare_direct_art(img, dw, dh)
    except Exception as exc:
        return False, "上屏准备失败: " + str(exc)

    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        card_img.save(dest, "PNG")
    except OSError as exc:
        return False, "写文件失败: " + str(exc)
    return True, str(dw) + "x" + str(dh)


async def _generate_art(dest: Path, scene: str, **kwargs) -> tuple[bool, str]:
    import httpx

    key = (kwargs.get("image_api_key") or "").strip() or os.getenv("MINIMAX_API_KEY", "").strip()
    if not key or key.startswith("sk-your-"):
        return False, "没有可用的生图凭证"

    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
    reasons: list[str] = []
    for retry in (False, True):
        prompt = _image_prompt(scene, retry=retry)
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
            reasons.append("生图请求失败: " + str(exc))
            break
        if resp.status_code != 200:
            reasons.append("生图 HTTP " + str(resp.status_code))
            break
        try:
            data = resp.json()
        except ValueError:
            reasons.append("生图返回不是 JSON")
            break
        base_resp = data.get("base_resp") or {}
        if base_resp.get("status_code") != 0:
            reasons.append("生图失败: " + str(base_resp.get("status_msg")))
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
            reasons.append("下载图失败: " + str(exc))
            break

        ok, reason = _prepare_art(tmp, dest)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        if ok:
            return True, reason
        reasons.append(reason)

    return False, "；".join(reasons) or "生图未成功"


# ── 当日记录 ──────────────────────────────────────────────────────────


def _record_day(state: dict, date_key: str, record: dict) -> None:
    days = state.get("days")
    if not isinstance(days, dict):
        days = {}
    days[date_key] = record
    state["days"] = days


async def generate_today_art(date_key: str, force: bool = False, **kwargs) -> dict | None:
    """写今天的画面 -> 出一张图。返回当日记录。"""
    if not _lock_acquire():
        return None

    try:
        state = _read_state()
        days = state.get("days")
        today = days.get(date_key) if isinstance(days, dict) else None
        if isinstance(today, dict) and today.get("art_url") and not force:
            return today

        card = await _llm_day(**kwargs)

        dest = ART_DIR / _art_name(date_key)
        ok, reason = await _generate_art(dest, card["scene"], **kwargs)
        art_url = ""
        if ok:
            art_url = "/static/miaoli/" + dest.name
        else:
            logger.warning("[Miaoli] %s 没出图: %s", date_key, reason)

        record = {
            "date": date_key,
            "title": card["title"],
            "scene": card["scene"],
            "signature": card["signature"],
            "art_url": art_url,
            "has_image": "yes" if art_url else "no",
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        state["today"] = record
        _record_day(state, date_key, record)
        _write_state(state)
        return record
    finally:
        _lock_release()


def _history_record(state: dict, date_key: str) -> dict:
    """往期记录里那一天那条。没有就是没有 —— 如实标「历史缺失」，不补生成。"""
    from .history_day import MISSING

    days = state.get("days")
    if isinstance(days, dict):
        item = days.get(date_key)
        if isinstance(item, dict):
            return dict(item)
    return {"date": date_key, "title": MISSING, "has_image": "no"}


async def build_miaoli_content(fallback: dict | None = None, **kwargs) -> dict:
    from .history_day import MISSING, resolve_day

    date_key = resolve_day(kwargs)
    state = _read_state()
    days = state.get("days")
    today = days.get(date_key) if isinstance(days, dict) else None
    card = today if isinstance(today, dict) else None

    # 历史日：只读已有记录。绝不补生成 —— 那天没生成过就是没生成过。
    if card is None:
        if date_key != datetime.now().strftime("%Y-%m-%d"):
            card = _history_record(state, date_key)
        else:
            card = await generate_today_art(date_key, **kwargs)
    card = card or {}

    art_url = str(card.get("art_url") or "")
    content = {
        "art_url": art_url,
        "has_image": "yes" if art_url else "no",
        "title": str(card.get("title") or MISSING),
        "scene": str(card.get("scene") or MISSING),
        "signature": str(card.get("signature") or DEFAULT_SIGNATURE),
        "name": CAT_NAME,
    }
    if isinstance(fallback, dict):
        for key, value in fallback.items():
            if not content.get(key):
                content[key] = value
    return content


async def tick(**kwargs) -> dict:
    """定时任务入口：写今天的画面，出今天的图。"""
    date_key = datetime.now().strftime("%Y-%m-%d")
    record = await generate_today_art(date_key, **kwargs)
    if not record:
        return {"date": date_key, "has_image": "no", "note": "锁未释放"}
    return {
        "date": date_key,
        "title": record.get("title", ""),
        "has_image": record.get("has_image", "no"),
        "note": record.get("art_url", ""),
    }


async def regenerate_art(date_key: str, **kwargs) -> tuple[bool, str]:
    """重出某天的图（换 prompt 或改风格后用）。"""
    state = _read_state()
    item = _history_record(state, date_key)
    scene = str(item.get("scene") or "")
    if not scene:
        return False, "那天没有记录"
    ART_DIR.mkdir(parents=True, exist_ok=True)
    dest = ART_DIR / _art_name(date_key)
    try:
        dest.unlink(missing_ok=True)
    except OSError:
        pass
    return await _generate_art(dest, scene, **kwargs)
