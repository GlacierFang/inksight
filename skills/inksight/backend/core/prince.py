"""小王子动态角色模式 provider（中文）。

按此刻的时间、天气、节日/节气挑一张角色图，再让 LLM 写一句和当下情境
对得上的气泡文案。

约定（刻意保持简单）：
- 不记长期状态：没有养成、没有亲密度、没有连续情绪状态机。
- 唯一的"历史"用途是别把上一句又说一遍——用现有 content_history 里的
  上一条渲染记录，不另开账本、不另加表。
- 角色图放在 runtime_characters/<账号桶>/ 下，账号桶沿用 owner_account
  （U<user_id>），跟相册一个口径。
- 只有中文一套文案，不做双语。
"""
from __future__ import annotations

import difflib
import hashlib
import logging
import re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_BACKEND_DIR = Path(__file__).resolve().parent.parent
CHAR_DIR = _BACKEND_DIR / "runtime_characters"

MODE_ID = "CUSTOM_PRINCE"

# 情绪 → 角色图文件名的候选（小写、去扩展名后比较）
_EMOTION_ALIASES: dict[str, tuple[str, ...]] = {
    "happy": ("happy", "joy", "smile", "cheerful", "开心", "高兴"),
    "excited": ("excited", "exc", "starry", "兴奋"),
    "calm": ("calm", "quiet", "peaceful", "平静"),
    "curious": ("curious", "wonder", "puzzled", "好奇"),
    "sleepy": ("sleepy", "sleep", "tired", "drowsy", "困"),
    "grumpy": ("grumpy", "angry", "mad", "cross", "脾气"),
    "sad": ("sad", "sorrow", "lonely", "难过", "伤心"),
}

EMOTION_CN = {
    "happy": "开心",
    "excited": "兴奋",
    "calm": "平静",
    "curious": "好奇",
    "sleepy": "困倦",
    "grumpy": "闹脾气",
    "sad": "有点难过",
}

# 恶劣天气：冻雨 / 暴雨 / 大雪 / 雷暴
_SEVERE_WEATHER = {56, 57, 65, 67, 75, 77, 82, 95, 96, 99}
# 下雨下雪：会让小王子低落
_RAINY_WEATHER = {51, 53, 55, 61, 63, 71, 73, 80, 81, 85, 86}
# 晴 / 少云 / 多云
_CLEAR_WEATHER = {0, 1, 2}

_IMG_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".bmp")


# ── 情绪选择 ──────────────────────────────────────────────────


def pick_emotion(
    *,
    hour: int,
    weather_code: int = -1,
    festival: str = "",
    solar_term: str = "",
    days_until_holiday: int = 0,
) -> str:
    """按当下情境挑情绪。规则按优先级从高到低，命中即止。"""
    # 1. 特别的日子 → 兴奋
    if festival or solar_term or 0 < int(days_until_holiday or 0) <= 1:
        return "excited"
    # 2. 恶劣天气 → 闹脾气
    if weather_code in _SEVERE_WEATHER:
        return "grumpy"
    # 3. 雨雪天 → 难过
    if weather_code in _RAINY_WEATHER:
        return "sad"
    # 4. 深夜 / 凌晨 / 午后 → 困倦
    if hour >= 23 or hour < 5 or 13 <= hour < 15:
        return "sleepy"
    # 5. 下午等人 → 好奇
    if 15 <= hour < 17:
        return "curious"
    # 6. 傍晚 / 一天快结束 / 晴天 → 开心
    if 17 <= hour < 22 or weather_code in _CLEAR_WEATHER:
        return "happy"
    # 7. 白天常态 → 平静
    return "calm"


# ── 角色图 ────────────────────────────────────────────────────


def _account_dir(account: str) -> Path:
    return CHAR_DIR / account


def list_character_files(account: str) -> list[Path]:
    """账号下所有可用的角色图（按文件名排序，保证确定性）。"""
    d = _account_dir(account)
    if not d.is_dir():
        return []
    return sorted(
        p for p in d.iterdir()
        if p.is_file() and p.suffix.lower() in _IMG_SUFFIXES
    )


def find_character_file(account: str, emotion: str) -> Path | None:
    """按情绪找图：先认标准文件名，认不出就退回目录里第一张。"""
    files = list_character_files(account)
    if not files:
        return None
    by_stem = {p.stem.lower(): p for p in files}
    for alias in _EMOTION_ALIASES.get(emotion, (emotion,)):
        hit = by_stem.get(alias.lower())
        if hit is not None:
            return hit
    return files[0]


# ── 文案 ──────────────────────────────────────────────────────

_PROMPT_ZH = """你是小王子，住在 B612 星球上。现在你站在一个人的面前，要跟他说一句话。

现在的世界：{date_str} {time_str}，{weather_line}。
{event_line}
你现在的表情和心情是：{emotion}（跟旁边那张图一模一样）。

要求：
- 只写一句话，最多两句，一共 12 到 28 个字
- 像小孩子说话：短句、直白，偶尔来一句只有小孩才想得出的道理
- 可以自言自语，也可以对眼前的人说，两种都行
- 要跟上面的时间、天气、节日对得上，但别把这些信息一字不落地念出来
- 不要引号，不要书名号，不要表情符号，不要标题，不要解释
- 不要照抄任何小说里的原句，要有自己的话

上一次你说的是「{prev}」。这次换一句新的，不要重复它。
只输出这句话本身。"""

_FALLBACK_ZH: dict[str, tuple[str, ...]] = {
    "happy": (
        "今天的云跑得比我还快。",
        "我把影子分了一半给风。",
        "风把树叶吹成了小铃铛。",
        "今天好像什么好事都会发生。",
        "我把星星数了一遍，多了一颗。",
        "你路过的时候，风变甜了。",
    ),
    "excited": (
        "今天是特别的一天，我知道！",
        "我偷偷藏了一个好消息。",
        "我有一整个下午想送给你。",
        "我想把今天装进口袋里带走。",
        "钟声响了三下，我数得清清楚楚。",
    ),
    "calm": (
        "今天没什么事，挺好的。",
        "我坐在云上面看下面。",
        "风不急，我也不急。",
        "安静得能听见麦子的声音。",
        "我把手放在口袋里待着。",
    ),
    "curious": (
        "你今天会带什么回来？",
        "我在等一个人经过。",
        "路那边是什么？我想去看看。",
        "你有没有见过会飞的鱼？",
        "下午了，你还不来吗？",
    ),
    "sleepy": (
        "我的眼皮有点重。",
        "我先眯一小会儿……",
        "星星在催我睡觉了。",
        "说话说到一半就困了。",
        "晚安，我先躺下了。",
    ),
    "grumpy": (
        "这雨下得真不讲道理。",
        "风把我的帽子吹跑了。",
        "天怎么这么吵，真讨厌。",
        "我不想出门了，真的。",
        "雷声太大了，装什么威风。",
    ),
    "sad": (
        "雨一直下，我不太想说话。",
        "今天的天有点灰。",
        "我把想说的话咽回去了。",
        "有点想你了，仅此而已。",
        "雨停了，天还是没亮。",
    ),
}

_LEADING_LABEL = re.compile(
    r"^\s*(小王子|王子)\s*[:：]?\s*"
)
_QUOTE_CHARS = "「」『』\"'“”‘’《》〈〉 \t"


def _clean_text(raw: str) -> str:
    """把 LLM 输出收拾成一句话：去标签、去引号、去换行。"""
    text = str(raw or "").strip()
    if not text:
        return ""
    text = _LEADING_LABEL.sub("", text)
    # 只要第一段（LLM 偶尔会自己加解释）
    for sep in ("\n", "。\n"):
        if sep in text:
            text = text.split(sep)[0].strip()
    text = text.strip().strip(_QUOTE_CHARS).strip()
    # 去掉结尾的说明性尾巴
    text = re.sub(r"\s*[（(【\[].*?[）)】\]]\s*$", "", text).strip()
    return text


def _norm(text: str) -> str:
    """归一化：只留字母数字汉字，用来判重。"""
    return re.sub(r"[\s\W_]+", "", text or "", flags=re.UNICODE).lower()


def _too_similar(a: str, b: str) -> bool:
    """两句是不是"换了个说法又说了一遍"。"""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na == nb or na in nb or nb in na:
        return True
    return difflib.SequenceMatcher(None, na, nb).ratio() >= 0.62


def _pick_fallback(emotion: str, seed: str) -> str:
    pool = _FALLBACK_ZH.get(emotion) or _FALLBACK_ZH["calm"]
    digest = hashlib.md5(f"{seed}:{emotion}".encode("utf-8")).hexdigest()
    return pool[int(digest[:8], 16) % len(pool)]


async def _call_llm_once(prompt: str, temperature: float, **kwargs) -> str:
    from .content import _call_llm

    provider = str(kwargs.get("llm_provider") or "").strip() or "minimax"
    model = str(kwargs.get("llm_model") or "").strip()
    models = (model,) if model else ("MiniMax-M3",)
    raw = ""
    for model_name in models:
        for attempt in range(2):
            try:
                raw = await _call_llm(
                    provider,
                    model_name,
                    prompt,
                    temperature=temperature,
                    max_tokens=800,
                    api_key=kwargs.get("api_key"),
                    base_url=kwargs.get("llm_base_url"),
                )
            except Exception as exc:  # noqa: BLE001 - LLM 失败要兜底
                logger.warning("[Prince] LLM 调用失败(%s 第 %s 次): %s", model_name, attempt + 1, exc)
                raw = ""
            if raw and raw.strip():
                break
        if raw and raw.strip():
            break
    return raw or ""


async def _recent_bubble(mac: str, mode_id: str) -> str:
    """上一句说过什么——只查现成的渲染记录，不另开账本。"""
    if not mac:
        return ""
    try:
        from .stats_store import get_content_history

        rows = await get_content_history(mac, limit=3, mode=mode_id)
    except Exception as exc:  # noqa: BLE001 - 历史查不到不该拖垮渲染
        logger.warning("[Prince] 读取上一句失败: %s", exc)
        return ""
    for row in rows or []:
        content = row.get("content") or {}
        bubble = str(content.get("bubble") or "").strip()
        if bubble:
            return bubble
    return ""


# ── 主入口 ────────────────────────────────────────────────────


def _weather_line(weather_str: str, weather_code: int) -> str:
    from .context import _weather_code_to_desc

    desc = ""
    if weather_code >= 0:
        try:
            desc = _weather_code_to_desc(weather_code)
        except Exception:  # noqa: BLE001
            desc = ""
    temp = str(weather_str or "").strip()
    parts = [p for p in (desc, temp) if p]
    if not parts:
        return "天气怎么样我不知道"
    return "，".join(parts)


def _event_line(festival: str, solar_term: str, days_until_holiday: int) -> str:
    if festival:
        return f"今天是{festival}，是个特别的日子。"
    if solar_term:
        return f"今天是{solar_term}，是个节气。"
    if 0 < int(days_until_holiday or 0) <= 1:
        return "明天就是节假日了。"
    return "普普通通的一天，没什么特别。"


async def build_prince_content(fallback: dict, **kwargs) -> dict:
    """computed provider：一张角色图 + 一句气泡文案。"""
    content = dict(fallback or {})
    date_ctx = kwargs.get("date_ctx") or {}

    now = datetime.now()
    hour = int(date_ctx.get("hour", now.hour) or now.hour)
    year = int(date_ctx.get("year", now.year) or now.year)
    month = int(date_ctx.get("month", now.month) or now.month)
    day = int(date_ctx.get("day", now.day) or now.day)
    date_str = str(date_ctx.get("date_str") or f"{month}月{day}日")
    time_str = str(date_ctx.get("time_str") or now.strftime("%H:%M:%S"))[:5]
    festival = str(date_ctx.get("festival") or "").strip()
    days_until_holiday = int(date_ctx.get("days_until_holiday", 0) or 0)
    raw_weather_code = kwargs.get("weather_code", -1)
    try:
        weather_code = -1 if raw_weather_code is None else int(raw_weather_code)
    except (TypeError, ValueError):
        weather_code = -1
    weather_str = str(kwargs.get("weather_str") or "").strip()

    solar_term = ""
    try:
        from .config import SOLAR_TERMS

        solar_term = str(SOLAR_TERMS.get((year, month, day), "") or "")
    except Exception:  # noqa: BLE001
        solar_term = ""

    emotion = pick_emotion(
        hour=hour,
        weather_code=weather_code,
        festival=festival,
        solar_term=solar_term,
        days_until_holiday=days_until_holiday,
    )

    content["emotion"] = emotion
    content["today_code"] = weather_code

    # ── 角色图 ────────────────────────────────────────────
    account = str(kwargs.get("owner_account") or "").strip().upper()
    if account:
        char_file = find_character_file(account, emotion)
        if char_file is not None:
            try:
                content["_prefetched_image_url"] = char_file.read_bytes()
                content["image_url"] = f"/runtime_characters/{account}/{char_file.name}"
            except OSError as exc:
                logger.warning("[Prince] 读取角色图失败 %s: %s", char_file, exc)
        else:
            logger.info("[Prince] 账号 %s 下没有角色图", account)

    # ── 文案 ──────────────────────────────────────────────
    mode_id = str(kwargs.get("mode_id") or MODE_ID).upper()
    mac = str(kwargs.get("mac") or "").strip().upper()
    prev = await _recent_bubble(mac, mode_id)

    prompt = _PROMPT_ZH.format(
        date_str=date_str,
        time_str=time_str,
        weather_line=_weather_line(weather_str, weather_code),
        event_line=_event_line(festival, solar_term, days_until_holiday),
        emotion=EMOTION_CN.get(emotion, emotion),
        prev=prev or "（还没有）",
    )

    bubble = ""
    raw = await _call_llm_once(prompt, 0.9, **kwargs)
    bubble = _clean_text(raw)
    if bubble and prev and _too_similar(bubble, prev):
        # 撞上上一句：再来一次，明确要求换个说法
        retry_prompt = prompt + "\n\n再换一句：意思也要不一样，不要只是把上一句倒过来说。"
        raw = await _call_llm_once(retry_prompt, 1.0, **kwargs)
        retry_text = _clean_text(raw)
        if retry_text and not _too_similar(retry_text, prev):
            bubble = retry_text
        else:
            bubble = ""
    if not bubble:
        bubble = _pick_fallback(emotion, f"{year}-{month:02d}-{day:02d}:{mac}")

    content["bubble"] = bubble
    return content
