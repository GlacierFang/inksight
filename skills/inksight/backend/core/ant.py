"""草地铺道蚁 provider：一只住在院子里的蚂蚁，每天早上掷骰子过日子。

产出四块：
- 自我认知（第一天定，永久固定）：代号、住所、签名、语气、三条怪癖。
- 今天的骰子：日期哈希做种子 → 方向骰（1-6 种活儿）+ 幅度骰（1-6 投入与详略）
  + 10% 反常（今天偏不跟信息素走，改干别的）。
- 纪事：LLM 以蚂蚁的第一人称写标题 + 3-4 句纪事 + 一句心声。
- 状态：体力 / 饱满 / 熟路 / 同伴 四个数值跨天累积，今天的活儿会改变它们。

原则：
- 骰子只决定方向和幅度，不决定内容。内容由 LLM 自由发挥——骰子是骨架，LLM 才是主角。
- 它不能像人说话。出现人类抽象词就重写一次，仍不合格就退回手写兜底短句。
- 一天只生成一次（文件锁 + 当天状态），设备刷多少次看到的都是同一张。
- 不生图：纯文字，一天一次 LLM 调用，几乎不花钱，也不费墨。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import re
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_BACKEND_DIR = Path(__file__).resolve().parent.parent
STATE_DIR = _BACKEND_DIR / "runtime_cache" / "ant"
STATE_FILE = STATE_DIR / "state.json"
LOCK_FILE = STATE_DIR / "generate.lock"

try:  # 定时任务独立跑时也要能读到凭证
    from dotenv import load_dotenv as _load_dotenv

    _load_dotenv(_BACKEND_DIR / ".env")
except Exception:  # pragma: no cover
    pass

SPECIES_NAME = "草地铺道蚁"
SPECIES_LATIN = "Tetramorium caespitum"

# 物种锚点：给 LLM 的事实底座，别让它自由发挥生物设定。
SPECIES_FACTS = (
    "它是铺道蚁属的模式种，中国分布最广的蚂蚁，在河南一带的城乡蚁类调查里是绝对优势种。"
    "属名 Tetramorium 意为「四脊」（腹柄节两节各有一脊），种名 caespitum 拉丁语本意是「草丛、丛生的」。"
    "中文名「铺道蚁」、英文 pavement ant / tramp ant，三个名字都指向人行道上那条砖缝——它活在人类的路上，"
    "靠人类贸易搭便车扩散。工蚁体长 2.6-3.9 毫米，体色黄褐至黑褐，头胸腹柄黄褐、腹部深褐近黑。"
    "它偏好开阔干燥的扰动地面：人行道、台阶下、花坛边、建筑地基缝、腐木。"
    "群体多蚁后、巢可分散、靠分巢扩张；用信息素径迹招募同伴；食性杂——蜜露、种子、尸体昆虫、人类的甜食。"
    "它进屋但不侵略，是与人共存的蚁。"
)

# 六种活儿：方向骰的六个面。hint 是给 LLM 的具体画面，不是它必须照抄的措辞。
ACTIONS = (
    {
        "key": "carry",
        "name": "搬运",
        "hint": "拖回一粒米、一块饼干渣、一根线头",
        "effect": {"energy": -10, "full": 9, "familiar": 1, "mates": 0},
        "trip": 0.8,
    },
    {
        "key": "explore",
        "name": "探路",
        "hint": "沿着砖缝往远处走，摸到一块新的边界",
        "effect": {"energy": -8, "full": -2, "familiar": 5, "mates": 0},
        "trip": 3.0,
    },
    {
        "key": "graze",
        "name": "放牧",
        "hint": "找到蚜虫，抹上信息素，召同伴来",
        "effect": {"energy": -6, "full": 7, "familiar": 2, "mates": 2},
        "trip": 1.6,
    },
    {
        "key": "move",
        "name": "搬家",
        "hint": "举家挪到新址，衔着卵和粮食搬家",
        "effect": {"energy": -14, "full": -4, "familiar": 4, "mates": -2},
        "trip": 5.0,
    },
    {
        "key": "sugar",
        "name": "蹭食",
        "hint": "人类洒落的糖块、甜食残渣",
        "effect": {"energy": -4, "full": 12, "familiar": 1, "mates": 0},
        "trip": 1.2,
    },
    {
        "key": "stasis",
        "name": "蛰伏",
        "hint": "触角一动不动，装死，或者只是歇着",
        "effect": {"energy": 12, "full": -1, "familiar": 0, "mates": 0},
        "trip": 0.1,
    },
)

# 漂移检测：某句里出现了别的动作的标志性词，就说明 LLM 写到别的事上去了。
# 分两级：
# - 强标志：只有该动作为主题时才会出现的词（搬回、举家、蚜虫…），命中 = 跑题。
# - 弱标志：只是该动作的常见对象（面包屑、糖块…），蚂蚁顺路捎带很正常，不算跑题。
ACTION_DRIFT_STRONG = {
    "carry": ("搬回", "搬起", "搬走", "扛", "拖回", "一趟一趟搬", "往巢里搬"),
    "explore": ("探路", "新岔道", "摸到边界", "陌生的路", "走到尽头", "探到"),
    "graze": ("蚜虫", "放牧", "抹上信息素", "召集同伴", "守着蜜露"),
    "move": ("举家", "搬家", "挪窝", "整窝", "搬进新巢", "迁到"),
    "sugar": ("甜食", "甜味", "舔了一口", "蹭到糖", "糖块"),
    "stasis": ("装死", "一动不动", "静止不动", "缩起触角", "歇着不动"),
}

# 弱标志仅记录，不触发重写。
ACTION_DRIFT_WEAK = {
    "carry": ("米粒", "面包屑", "饼干渣", "线头", "碎屑"),
    "explore": ("岔道", "边界", "陌生", "尽头", "新路", "远处"),
    "graze": ("蜜露", "舔", "汁"),
    "move": ("新巢", "迁"),
    "sugar": ("糖", "甜"),
    "stasis": ("贴住", "静止", "歇"),
}

# 状态轴
STATE_KEYS = ("energy", "full", "familiar", "mates")
STATE_INIT = {"energy": 60, "full": 55, "familiar": 20, "mates": 5}
STATE_LABEL = {
    "energy": "体力",
    "full": "饱满",
    "familiar": "熟路",
    "mates": "同伴",
}

# 蚂蚁不该有的词：出现就是「像人说话」，要重写。
FORBIDDEN = (
    "心情", "决定", "计划", "想法", "觉得", "思考", "梦想", "目标", "意义",
    "孤独", "焦虑", "开心", "难过", "害怕", "朋友", "家人", "工作", "生活",
    "理想", "自由", "追求", "童年", "回忆", "意识", "明白",
)

LLM_MODELS = ("MiniMax-M3.1-Flash", "MiniMax-M3")

LOCK_STALE_SEC = 900
RECENT_KEEP = 3

IDENTITY_KEYS = ("代号", "住所", "签名", "语气", "怪癖")
DAY_KEYS = ("标题", "一", "二", "三", "四", "心")

DEFAULT_IDENTITY = {
    "name": "细腰",
    "home": "台阶下第三道砖缝",
    "signature": "第三块砖缝里的",
    "tone": "短句，实录，不抒情",
    "quirks": ["总沿同一条缝走", "对甜味没有抵抗力", "把鞋底当地震"],
}

# LLM 彻底失灵时的手写兜底：按日期轮换，保证当天不空。
FALLBACK_CARDS = (
    {
        "title": "第七趟",
        "lines": ("米粒卡在缝里，它换了个角度。", "回来的时候天光暗了一点。"),
        "voice": "缝还是那条缝。",
    },
    {
        "title": "空地",
        "lines": ("走过一段没有气味的路。", "尽头是新铺的砖。"),
        "voice": "没有旧气味的地方，算新地方。",
    },
    {
        "title": "一只",
        "lines": ("蚜虫挪了半寸，它跟着挪。", "同伴来得比往常快。"),
        "voice": "气味浓了，来的就多。",
    },
    {
        "title": "不动",
        "lines": ("它把触角收起来，贴住砖面。", "影子从脚边挪到了背上。"),
        "voice": "不动也是一种走法。",
    },
    {
        "title": "甜",
        "lines": ("台阶上有一小块糖化开的痕。", "它绕了两圈才下手。"),
        "voice": "甜的先闻三遍。",
    },
)

# 每种动作各备两条手写兜底：模型挂掉时，内容至少跟今天的活儿对得上。
FALLBACK_BY_ACTION = {
    "carry": (
        {"title": "一粒米", "lines": ("米粒卡在砖缝边，我用颚把它撬出来。", "推了两步，换个角度再推。"),
         "voice": "沉的都要搬。"},
        {"title": "半块渣", "lines": ("饼干渣太碎，我先拢成一小堆。", "再一趟就够一趟的。"),
         "voice": "一趟一趟的。"},
    ),
    "explore": (
        {"title": "新缝", "lines": ("砖缝往前还有一段，没有气味。", "走到头是一块刚铺的砖。"),
         "voice": "没走过的地方都亮。"},
        {"title": "边缘", "lines": ("过了花坛沿，地面忽然矮下去一截。", "我在矮的那条线上停了一会儿。"),
         "voice": "世界的边是这样断的。"},
    ),
    "graze": (
        {"title": "蚜虫", "lines": ("蚜虫挪了半寸，我跟着挪。", "抹上一点气味，同伴一会儿就来。"),
         "voice": "它慢，我更慢。"},
        {"title": "汁", "lines": ("一滴蜜露挂在叶尖，我用触角刮下来。", "刮完把叶尖擦干净。"),
         "voice": "汁是它的，蜜是多的。"},
    ),
    "move": (
        {"title": "挪窝", "lines": ("举家往东挪了三步，卵用颚衔着。", "旧窝留了些气味，够认路。"),
         "voice": "走就是家。"},
        {"title": "开口", "lines": ("新址的砖缝比旧的宽一点。", "我把碎土往里推，推了一整晚。"),
         "voice": "土是软的就能住。"},
    ),
    "sugar": (
        {"title": "甜", "lines": ("台阶上有一小块糖化开的痕。", "闻了三遍才下手。"),
         "voice": "甜的先闻三遍。"},
        {"title": "杯底", "lines": ("一只空杯底还剩一圈甜水。", "我把杯沿的渣刮下来。"),
         "voice": "人喝剩的最甜。"},
    ),
    "stasis": (
        {"title": "不动", "lines": ("我把触角收起来，贴住砖面。", "影子从脚边挪到了背上。"),
         "voice": "不动也是一种走法。"},
        {"title": "装死", "lines": ("一只人的鞋停在半步外。", "我把腿收拢，呼吸放到最慢。"),
         "voice": "活着，但不要被看见。"},
    ),
}

IDENTITY_PROMPT = """给你一只蚂蚁定一个身份。它是真实的物种，身份由它自己认领。

它是{SPECIES_NAME}（{LATIN}）。{FACTS}

请输出，严格按下面格式，每行一条，不要多余的话：
代号：<2-4 个汉字，像蚁语，不要人名，不要数字>
住所：<不超过 10 字，具体到一处：哪块砖、哪级台阶、哪棵树根>
签名：<不超过 12 字，一句话，像它自己刻在石头上的>
语气：<不超过 20 字，描述它说话的方式>
怪癖：<不超过 12 字>
怪癖：<不超过 12 字>
怪癖：<不超过 12 字>

要求：
- 代号要是蚂蚁会有的那种称呼，不要"小明""阿福"这类人名。
- 三条怪癖要具体到行为，不要抽象性格。
- 不要解释，不要展开。"""

DAY_PROMPT = """你是一只{SPECIES_NAME}，代号{name}，住在{home}。

{IDENTITY_BLOCK}

今天你掷了骰子：
- 本能的方向：{action_name}（{action_hint}）
- 投入程度：{magnitude}/6（1 是碰一下就走，6 是沉进去）
- 反常：{anomaly}

你现在的身体状况：
- 体力 {energy}/100
- 饱满 {full}/100
- 对这片地方的熟悉 {familiar}/100
- 巢里同伴 {mates} 只

最近三天你做过的事：
{recent}

请写今天的记录，严格按下面格式，每行一条，不要多余的话：
标题：<2-8 字>
{line_spec}
心：<10-18 字>

规矩：
- 第一人称，是那只蚂蚁在写自己，不是写它。
- 今天只有一件事：{action_name}。每一句都要落在这件事上，不许写到别的事上去。
- 语气是{style_hint}。
- 只用蚂蚁的方式计量和感知：触角、气味、信息素、一趟两趟、几粒米、影子挪了多远、地面震了一下。
- 不许出现这些词：{forbidden}
- 不许抒情成散文。短句，像观察记录。
- 纪事要具体：写了什么、搬了什么、走到哪、看见了什么。
- 每一行不超过 20 个字。写不下就少写一句，别把两件事挤在一行。
- 心声只有一句，是它说不出口的那半句。
- 正文里不要写具体数字（行程数字由别的部分显示）。
- 不要提"今天""记录"这类词，直接写。"""


# --------------------------------------------------------------------------
# 状态读写
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
        logger.warning("[Ant] 写状态失败", exc_info=True)


def _roll_state(state: dict, date_key: str) -> dict:
    """跨天时把昨天的结果接到今天的起点。

    state["states"] 始终是「当天开始时的身体状况」，
    state["states_end"] 是当天干完活儿后的结果。
    分开存是为了幂等：当天重跑一次不会把效果叠加两遍。
    """
    if state.get("date") == date_key:
        return state
    end = state.get("states_end")
    if isinstance(end, dict):
        state["states"] = end
    state["today"] = None
    state["date"] = date_key
    return state


def _states(state: dict) -> dict:
    raw = state.get("states")
    out = dict(STATE_INIT)
    if isinstance(raw, dict):
        for key in STATE_KEYS:
            try:
                out[key] = int(raw.get(key, out[key]))
            except (TypeError, ValueError):
                continue
    return out


def _recent(state: dict) -> list:
    items = state.get("recent")
    if not isinstance(items, list):
        return []
    return [r for r in items if isinstance(r, dict)]


# --------------------------------------------------------------------------
# 并发锁：一天只掷一次骰子
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
# 骰子：日期哈希做种子，同一天永远掷出同一个结果
# --------------------------------------------------------------------------


def _dice_for_date(date_key: str) -> dict:
    seed = hashlib.sha256(f"ant-dice|{date_key}".encode("utf-8")).hexdigest()
    rng = random.Random(int(seed[:16], 16))
    direction = rng.randint(1, 6)
    magnitude = rng.randint(1, 6)
    anomaly = rng.random() < 0.10

    action = ACTIONS[(direction - 1) % len(ACTIONS)]
    if anomaly:
        action = ACTIONS[((direction - 1) + 3) % len(ACTIONS)]
    return {
        "direction": direction,
        "magnitude": magnitude,
        "anomaly": anomaly,
        "action": action["key"],
        "action_name": action["name"],
    }


def _apply_effect(states: dict, action_key: str, magnitude: int) -> dict:
    action = next((a for a in ACTIONS if a["key"] == action_key), ACTIONS[0])
    scale = 0.5 + 0.25 * (magnitude - 1)
    out = dict(states)
    for key, delta in action["effect"].items():
        out[key] = int(out.get(key, 0) + delta * scale)
    out["energy"] = max(0, min(100, out["energy"]))
    out["full"] = max(0, min(100, out["full"]))
    out["familiar"] = max(0, min(100, out["familiar"]))
    out["mates"] = max(0, min(40, out["mates"]))
    return out


def _trip_m(action_key: str, magnitude: int, energy: int) -> float:
    action = next((a for a in ACTIONS if a["key"] == action_key), ACTIONS[0])
    meters = action["trip"] * (0.6 + 0.2 * magnitude)
    meters *= 0.5 + 0.5 * (max(0, min(100, energy)) / 100.0)
    return round(meters, 1)


# --------------------------------------------------------------------------
# 解析
# --------------------------------------------------------------------------


def _parse_kv(raw: str, keys: tuple) -> dict:
    out: dict[str, str] = {}
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line or "：" not in line:
            continue
        key, _, value = line.partition("：")
        key = key.strip()
        value = value.strip().strip("「」『』\"'")
        if key in keys and value:
            out.setdefault(key, value)
    return out


def _parse_identity(raw: str) -> dict:
    parsed = _parse_kv(raw, IDENTITY_KEYS)
    quirks = [ln.split("：", 1)[1].strip() for ln in (raw or "").splitlines()
              if ln.strip().startswith("怪癖：") and ln.split("：", 1)[1].strip()]
    name = parsed.get("代号", "")
    if not re.fullmatch(r"[一-鿿]{2,5}", name):
        return {}
    return {
        "name": name,
        "home": parsed.get("住所", DEFAULT_IDENTITY["home"])[:12],
        "signature": parsed.get("签名", DEFAULT_IDENTITY["signature"])[:14],
        "tone": parsed.get("语气", DEFAULT_IDENTITY["tone"])[:30],
        "quirks": (quirks or list(DEFAULT_IDENTITY["quirks"]))[:3],
    }


def _parse_day(raw: str) -> dict:
    parsed = _parse_kv(raw, DAY_KEYS)
    lines = [parsed[key] for key in ("一", "二", "三", "四") if parsed.get(key)]
    if not parsed.get("标题") or len(lines) < 2:
        return {}
    return {
        "title": parsed["标题"][:10],
        "lines": lines[:4],
        "voice": parsed.get("心", "")[:24],
    }


def _hits_forbidden(text: str) -> list:
    return [w for w in FORBIDDEN if w in (text or "")]


def _drift_actions(text: str, today_key: str) -> list:
    """纪事里出现了别的动作的强标志词 = 写到别的事上去了。

    只认强标志：蚂蚁在蹭食路上顺路衔了块面包屑，那是正常的一天，不是跑题。
    """
    out = []
    for key, words in ACTION_DRIFT_STRONG.items():
        if key == today_key:
            continue
        if any(w in (text or "") for w in words):
            out.append(key)
    return out


def _weak_drift(text: str, today_key: str) -> list:
    """弱标志（对象词）出现得太多，也是跑题的信号。

    单个弱标志不算——蚂蚁捎带一块面包屑很正常；
    同一对象的弱词反复出现（两次以上）说明重心偏了。
    """
    out = []
    for key, words in ACTION_DRIFT_WEAK.items():
        if key == today_key:
            continue
        hits = [w for w in words if w in (text or "")]
        if len(hits) >= 2:
            out.append(key)
    return out


# 屏上正文区只有 234px（状态栏 36 → 页脚 270）。15px 汉字一字宽 15px，
# 376px 的行宽只装得下 25 字 —— 一旦折行，下面的心声/体力/行程就被挤出屏幕。
# 所以正文每行硬压到 20 字，超了就在标点处断。
LINE_MAX_CHARS = 20
VOICE_MAX_CHARS = 20


def _clamp_line(text: str, limit: int) -> str:
    """把一行压到 limit 字以内。优先在标点处断；硬切时去掉结尾的悬空标点。"""
    s = " ".join(str(text or "").split())
    if len(s) <= limit:
        return s
    # 在标点处断，但不带走这个标点：末行留个逗号像话没说完。
    for i in range(min(limit, len(s) - 1), max(0, limit - 8), -1):
        if s[i] in "。！？；，、：":
            return s[:i].rstrip("，、；：")
    return s[:limit].rstrip("，、；：")


def _line_spec(magnitude: int) -> str:
    """幅度骰决定这张卡写多满：投入浅就少写几句，投入深才铺开。

    深度靠「写几句」表达，不靠「每句多长」—— 句长一涨就折行，
    折行会把屏下面挤没。
    """
    count = {1: 2, 2: 2, 3: 3, 4: 3, 5: 4, 6: 4}.get(int(magnitude), 3)
    span = {1: "10-14", 2: "12-16", 3: "14-18", 4: "16-18", 5: "16-20", 6: "18-20"}.get(
        int(magnitude), "14-18"
    )
    labels = ("一", "二", "三", "四")
    lines = [f"{labels[i]}：<{span} 字>" for i in range(count)]
    return "\n".join(lines)


def _style_hint(magnitude: int) -> str:
    return {
        1: "碰一下就走，话少，句子短",
        2: "浅尝一口，话不多",
        3: "正常的一天",
        4: "干得挺久，话密一些",
        5: "沉进去了，细节多",
        6: "完全陷进去，几乎忘了时间",
    }.get(int(magnitude), "正常的一天")


# --------------------------------------------------------------------------
# LLM
# --------------------------------------------------------------------------


async def _call(prompt: str, temperature: float, **kwargs) -> str:
    from .content import _call_llm

    provider = str(kwargs.get("llm_provider") or "").strip() or "minimax"
    model = str(kwargs.get("llm_model") or "").strip()
    models = (model,) if model else LLM_MODELS
    raw = ""
    for model_name in models:
        for attempt in range(2):
            try:
                raw = await _call_llm(
                    provider,
                    model_name,
                    prompt,
                    temperature=temperature,
                    max_tokens=12000,
                    api_key=kwargs.get("api_key"),
                    base_url=kwargs.get("llm_base_url"),
                )
            except Exception as exc:
                logger.warning("[Ant] LLM 调用失败(%s 第 %s 次): %s", model_name, attempt + 1, exc)
                raw = ""
            if raw and raw.strip():
                break
        if raw and raw.strip():
            break
    return raw or ""


async def _llm_identity(**kwargs) -> dict:
    prompt = IDENTITY_PROMPT.format(
        SPECIES_NAME=SPECIES_NAME,
        LATIN=SPECIES_LATIN,
        FACTS=SPECIES_FACTS,
    )
    raw = await _call(prompt, 0.9, **kwargs)
    identity = _parse_identity(raw)
    if identity:
        return identity
    logger.warning("[Ant] 身份解析失败: %r", (raw or "")[:200])
    return dict(DEFAULT_IDENTITY)


async def _llm_day(dice: dict, states: dict, recent: list, creature: dict, **kwargs) -> dict:
    identity_block = (
        f"你的语气：{creature.get('tone', '')}。\n"
        f"你的三条怪癖：{'；'.join(creature.get('quirks', []))}。"
    )
    recent_txt = "\n".join(
        f"- {r.get('date')} {r.get('title')}：" + "".join(r.get("lines", []))
        for r in recent
    ) or "（此前没有记录）"
    anomaly_txt = (
        "今天反常——你偏不跟平日那股气味走，改干别的"
        if dice.get("anomaly") else "今天没有反常，按本能来"
    )
    prompt = DAY_PROMPT.format(
        SPECIES_NAME=SPECIES_NAME,
        name=creature.get("name", ""),
        home=creature.get("home", ""),
        IDENTITY_BLOCK=identity_block,
        action_name=dice.get("action_name", ""),
        action_hint=next((a["hint"] for a in ACTIONS if a["key"] == dice.get("action")), ""),
        magnitude=dice.get("magnitude", 3),
        anomaly=anomaly_txt,
        energy=states.get("energy", 0),
        full=states.get("full", 0),
        familiar=states.get("familiar", 0),
        mates=states.get("mates", 0),
        recent=recent_txt,
        forbidden="、".join(FORBIDDEN),
        line_spec=_line_spec(dice.get("magnitude", 3)),
        style_hint=_style_hint(dice.get("magnitude", 3)),
    )

    raw = await _call(prompt, 0.95, **kwargs)
    card = _parse_day(raw)
    hits = _hits_forbidden(raw)
    drift = _drift_actions(raw, dice.get("action", "")) + _weak_drift(raw, dice.get("action", ""))
    if card and not hits:
        return card
    if hits or drift:
        reasons = []
        if hits:
            reasons.append(f"你用了这些词：{'、'.join(hits)}")
        if drift:
            names = "、".join(
                next((a["name"] for a in ACTIONS if a["key"] == k), k) for k in drift
            )
            reasons.append(f"你把事情写到了「{names}」上去了")
        logger.warning("[Ant] %s，重写一次", "；".join(reasons))
        retry_prompt = prompt + (
            "\n\n上一次你写的有问题："
            + "；".join(reasons)
            + f"。重写一遍：今天只干「{dice.get('action_name')}」这一件事，别的动作一个字都不许提。"
        )
        raw = await _call(retry_prompt, 1.0, **kwargs)
        card = _parse_day(raw)
        new_hits = _hits_forbidden(raw)
        new_drift = _drift_actions(raw, dice.get("action", "")) + _weak_drift(raw, dice.get("action", ""))
        if card and not new_hits:
            # 禁词是硬底线：像人说话就整张作废。
            # 漂移只是尾巴上沾了别的动作，不值得扔掉整张卡。
            if new_drift:
                logger.warning("[Ant] 重写后仍有轻微漂移: %s", "、".join(new_drift))
            return card
        if new_hits:
            logger.warning("[Ant] 重写后仍像人说话: %s", "、".join(new_hits))
    if not card:
        logger.warning("[Ant] 纪事解析失败: %r", (raw or "")[:200])
    return {}


# --------------------------------------------------------------------------
# 生成
# --------------------------------------------------------------------------


def _fallback_card(date_key: str, action_key: str = "") -> dict:
    """手写兜底：优先用当天动作对应的短句，保证内容不跑题。"""
    pool = FALLBACK_BY_ACTION.get(action_key)
    if pool:
        seed = hashlib.sha256(f"ant-fallback|{action_key}|{date_key}".encode("utf-8")).hexdigest()
        card = pool[int(seed[:4], 16) % len(pool)]
    else:
        seed = hashlib.sha256(f"ant-fallback|{date_key}".encode("utf-8")).hexdigest()
        card = FALLBACK_CARDS[int(seed[:4], 16) % len(FALLBACK_CARDS)]
    return {
        "title": card["title"],
        "lines": list(card["lines"]),
        "voice": card["voice"],
    }


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
        return await _wait_for_card(date_key)

    try:
        state = _read_state()
        _roll_state(state, date_key)
        today = state.get("today")
        if isinstance(today, dict) and today.get("date") == date_key and not force:
            return today

        creature = state.get("creature")
        if not isinstance(creature, dict) or not creature.get("name"):
            creature = await _llm_identity(**kwargs)
            state["creature"] = creature
            logger.info("[Ant] 它认领了身份: %s（%s）", creature.get("name"), creature.get("home"))

        states = _states(state)
        dice = _dice_for_date(date_key)
        recent = _recent(state)

        # 体力不够的蚂蚁干不动重活：幅度骰压低，但方向不变。
        # 这是跨天的因果——昨天没吃饱，今天就浅尝。
        if states.get("energy", 100) < 25 and dice.get("magnitude", 3) > 2:
            dice = dict(dice, magnitude=2, base_magnitude=dice["magnitude"], tired=True)
        elif states.get("energy", 100) > 85 and dice.get("magnitude", 3) < 5:
            dice = dict(
                dice,
                magnitude=dice["magnitude"] + 1,
                base_magnitude=dice["magnitude"],
                rested=True,
            )
        else:
            dice = dict(dice, base_magnitude=dice["magnitude"])

        card = await _llm_day(dice, states, recent[-RECENT_KEEP:], creature, **kwargs)
        if not card:
            card = _fallback_card(date_key, dice.get("action", ""))

        new_states = _apply_effect(states, dice["action"], dice["magnitude"])
        delta = {k: new_states.get(k, 0) - states.get(k, 0) for k in STATE_KEYS}
        trip = _trip_m(dice["action"], dice["magnitude"], states.get("energy", 50))

        today = {
            "date": date_key,
            "title": card["title"],
            "lines": card["lines"],
            "voice": card.get("voice", ""),
            "dice": dice,
            "states": new_states,
            "state_delta": delta,
            "trip_m": trip,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        state["today"] = today
        state["date"] = date_key
        state["states_end"] = new_states
        recent = [r for r in recent if r.get("date") != date_key]
        # 完整保存当日卡：四行、心声、状态、行程、掷骰动作、反常信息、巢身份。
        # 画廊重建整屏要用这些字段；缺了只能如实标「历史缺失」。
        recent.append({
            "date": date_key,
            "title": card["title"],
            "lines": list(card["lines"]),
            "voice": card.get("voice", ""),
            "dice": dice,
            "states": new_states,
            "state_delta": delta,
            "trip_m": trip,
            "name": creature.get("name", ""),
            "home": creature.get("home", ""),
            "signature": creature.get("signature", ""),
        })
        recent.sort(key=lambda r: str(r.get("date", "")))
        state["recent"] = recent  # 全留：画廊要回看往期（prompt 只取最近 RECENT_KEEP 条）
        _write_state(state)
        return today
    finally:
        _lock_release()


async def generate_today_card(force: bool = False, **kwargs) -> dict | None:
    date_key = datetime.now().strftime("%Y-%m-%d")
    for _ in range(2):
        card = await _generate_once(date_key, force, **kwargs)
        if card is not None:
            return card
    return None



def _history_card(state: dict, date_key: str) -> dict:
    """往期记录里那一天的那张卡；旧记录只存了标题和前两行，其余如实标「历史缺失」。"""
    from .history_day import MISSING

    for item in reversed(state.get("recent") or []):
        if isinstance(item, dict) and str(item.get("date")) == date_key:
            lines = item.get("lines")
            lines = [str(v) for v in lines] if isinstance(lines, list) else []
            # 旧记录只存了前两行，后面的行如实标「历史缺失」
            while len(lines) < 4:
                lines.append(MISSING)
            return {
                "date": date_key,
                "title": str(item.get("title") or MISSING),
                "lines": lines,
                "voice": str(item.get("voice") or MISSING),
                "states": item.get("states") if isinstance(item.get("states"), dict) else {},
                "state_delta": item.get("state_delta") if isinstance(item.get("state_delta"), dict) else {},
                "trip_m": item.get("trip_m"),
                "name": str(item.get("name") or MISSING),
                "home": str(item.get("home") or MISSING),
                "signature": str(item.get("signature") or MISSING),
                "_history": True,
            }
    return {
        "date": date_key,
        "title": MISSING,
        "lines": [MISSING, MISSING, MISSING, MISSING],
        "voice": MISSING,
        "states": {},
        "state_delta": {},
        "trip_m": None,
        "name": MISSING,
        "home": MISSING,
        "signature": MISSING,
        "_history": True,
    }


async def build_ant_content(fallback: dict | None = None, **kwargs) -> dict:
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

    creature = state.get("creature") if isinstance(state.get("creature"), dict) else {}
    creature = creature or DEFAULT_IDENTITY
    states = card.get("states") if isinstance(card.get("states"), dict) else _states(state)
    lines = card.get("lines") or []
    trip = float(card.get("trip_m") or 0.0)

    anomaly = bool((card.get("dice") or {}).get("anomaly"))
    title = card.get("title", "")

    delta = card.get("state_delta") if isinstance(card.get("state_delta"), dict) else {}
    state_parts = []
    for key in STATE_KEYS:
        value = int(states.get(key, 0))
        d = int(delta.get(key, 0))
        mark = "↑" if d > 0 else ("↓" if d < 0 else "·")
        # 状态行只有一行 376px：标签、数值、分隔符都不留空格才装得下。
        state_parts.append(f"{STATE_LABEL[key]}{value}{mark}")

    if card.get("_history"):
        # 历史记录没记到的就是没记到 —— 显示 0 等于编造
        from .history_day import MISSING as _MISSING
        state_line = "·".join(state_parts) if states else _MISSING
        trip_line = (
            f"今日行程 {trip:g} 米 ≈ 你走 {int(round(trip * 100))} 米"
            if card.get("trip_m") is not None else _MISSING
        )
    else:
        state_line = "·".join(state_parts)
        trip_line = f"今日行程 {trip:g} 米 ≈ 你走 {int(round(trip * 100))} 米"
    content = {
        "title": (f"今天反常 · {title}" if anomaly else title),
        "l1": _clamp_line(lines[0], LINE_MAX_CHARS) if len(lines) > 0 else "",
        "l2": _clamp_line(lines[1], LINE_MAX_CHARS) if len(lines) > 1 else "",
        "l3": _clamp_line(lines[2], LINE_MAX_CHARS) if len(lines) > 2 else "",
        "l4": _clamp_line(lines[3], LINE_MAX_CHARS) if len(lines) > 3 else "",
        "voice": _clamp_line(card.get("voice", ""), VOICE_MAX_CHARS),
        "state_line": state_line,
        "trip_line": trip_line,
        "name": creature.get("name", ""),
        "home": creature.get("home", ""),
        "signature": creature.get("signature", ""),
        "has_text": "yes" if card.get("title") else "no",
    }
    if isinstance(fallback, dict):
        for key, value in fallback.items():
            if not content.get(key):
                content[key] = value
    return content
