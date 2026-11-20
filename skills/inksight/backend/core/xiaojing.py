"""小景 provider（CUSTOM_XIAOJING）：每天画一小片世界，跟着此刻的光线换色。

设计（2026-11-01 冰河大人定的方向）
--------------------------------------------------------
- 场景、角色、地形、构图全部交给 AI 每次重新发明 —— 每天都不重样。
- 固定的只有「风格规则」：赛璐璐平涂 + 波普网点/放射线 + 四色墨水 + 人在场景里很小。
  形象和位置都不写死：提示词里只给一个名词线索（今天路过的是「一座木桥」），
  画师自己决定这是什么、放在哪、谁在里面。
- 时刻由程序定，不由模型定：程序按真实太阳位置算出「晨/昼/暮/夜」，
  把该时段的光线写进提示词，再在本地按该时段的配方调一次色。
  模型对「夜」的理解会飘（爱画成落日），本地调色这一层就是兜底。
- 跨时段即换图：state 里记了当天那张图的时段，跨了时段就重新出一张。
  屏上的光一天走完一轮：晨、昼、暮、夜。

只做「调色」，不做四色归色 —— 归色是渲染器的 Atkinson 抖动干的活。
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_BACKEND_DIR = Path(__file__).resolve().parent.parent
STATE_DIR = _BACKEND_DIR / "runtime_cache" / "xiaojing"
STATE_FILE = STATE_DIR / "state.json"
LOCK_FILE = STATE_DIR / "generate.lock"
ART_DIR = _BACKEND_DIR / "static" / "xiaojing"

try:  # 定时任务独立跑时也要能读到 MINIMAX_API_KEY
    from dotenv import load_dotenv as _load_dotenv

    _load_dotenv(_BACKEND_DIR / ".env")
except Exception:  # pragma: no cover - dotenv 缺失不影响主流程
    pass

MINIMAX_BASE_URL = "https://api.minimaxi.com/v1"
MINIMAX_IMAGE_API_URL = f"{MINIMAX_BASE_URL}/images/generations"
IMAGE_MODEL = "MiniMax-H3-Image"
IMAGE_ASPECT = "3:2"

# 画心在屏上的显示尺寸，必须与模式 JSON 里 image 块的 width/height 一致。
# 400×300 基准屏：状态栏 36px + 图 264px + 无页脚 = 300px，正好占满。
ART_DISPLAY = (400, 264)

LOCK_STALE_SEC = 900

PHASES = ("dawn", "day", "dusk", "night")
PHASE_LABEL = {"dawn": "晨", "day": "昼", "dusk": "暮", "night": "夜"}

# ── 时段（程序定，不由模型定）────────────────────────────────────────────
# 只做正面描述，不写「不要日落」这类否定清单。
# 否定约束越多模型越僵，还容易把不该出现的元素招来 ——
# 第一版画出一轮血红落日，就是一堆 NO sun / NO sunset 反招的。
_PHASE_PROMPT = {
    "dawn": "清晨，第一缕日光刚落在山脊上",
    "day": "正午，太阳高悬，光线明亮",
    "dusk": "黄昏，夕阳贴着地平线，天边烧成橘红",
    "night": "夜，天空是一张空白的纸，一轮白月亮挂在天上，星星是细密的墨点，地面和树是浓墨的剪影",
}

# ── 风格段（固定，刻意写短）────────────────────────────────────────────
# 对齐 little-prince 的「水彩，平涂，吉卜力风格」：画风一句话就够。
# 写长了模型会僵住，出图变成照着条文填空 —— 原来的 400 词约束块就是这么废的。
_STYLE_PROMPT = "赛璐璐平涂，波普网点，丝网印刷质感，人物很小，四周是更大的世界"

_RETRY_PROMPT = " 画面里不要有任何文字。"

# ── 四色盘段（只管四色怎么用，构图交给模型）────────────────────────────
# 对齐 danqing 的 PALETTE_SPEC 写法。
_PALETTE_SPEC = (
    "画面只用四种颜色：纯白纸面底、墨黑 #000000 勾线、金黄 #E8B000 填色、朱红 #C80000 点缀。"
    "不要出现第五种色相，不要写实材质纹理，不要照片感。"
    "构图、疏密、留白由你按场景自己定，主体要在画面里站得住，不要缩在边角。"
    "不要任何文字、印章、题签、标签、水印、边框。"
)

# ── 波普背景（按时段）──────────────────────────────────────────────────
# 放射光线属于「有太阳」的时段；夜里换成满天的星点。
# 这里也只做正面描述 —— 夜的背景句里根本没提太阳，就不会有太阳。
_PHASE_BACKGROUND = {
    "dawn": "背后一片放射状的光线铺满天空",
    "day": "背后一片放射状的光线铺满天空",
    "dusk": "背后一片放射状的光线铺满天空",
    "night": "天空大片留白，只撒着细密的星点",
}

# ── 时段对四色配重的影响 ───────────────────────────────────────────────
# 写进提示词，而不是靠后期调色去救 —— 调色救不回一片黑。
_PHASE_INK_WEIGHT = {
    "dawn": "金黄占主调，朱红点缀天空与草木。",
    "day": "金黄与朱红都要亮起来，白色留白要充分。",
    "dusk": "朱红占主调，金黄过渡，天空要有层次。",
    "night": "天空保持纸白，星星用极细的墨点表示；月亮是一个留白的白圆盘，外围一圈细墨边；地面、树和人物是浓墨剪影；金黄只用在一两处点缀",
}

# ── 每天换一个「场景线索」：只给名词，不给构图 ───────────────────────────
_SCENE_HINTS = (
    "一座木桥",
    "一片松林",
    "一条蜿蜒的河",
    "一片高高的草地",
    "一排石阶",
    "海上的悬崖",
    "起雾的湖岸",
    "一片雪原",
    "山坡上的果园",
    "一座孤零零的石灯笼",
    "一片开阔的麦田",
    "山间的小祠",
    "退潮后的沙洲",
    "一片竹林",
    "层层叠叠的梯田",
    "一座废弃的瞭望塔",
)


# --------------------------------------------------------------------------
# 状态与锁
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
        logger.warning("[Xiaojing] 写状态失败", exc_info=True)


def _write_phase_record(state: dict, date_key: str, phase: str, record: dict) -> None:
    """记下某天某时段的那张图。

    一天四张（晨/昼/暮/夜）各记各的 —— 顶层 days[日期] 只存当前时段，
    回顾要一天四张，全靠这里分开记。
    """
    phases = state.get("phases")
    if not isinstance(phases, dict):
        phases = {}
    day_phases = phases.get(date_key)
    if not isinstance(day_phases, dict):
        day_phases = {}
    day_phases[phase] = record
    phases[date_key] = day_phases
    state["phases"] = phases


def reconstruct_phases(state: dict) -> dict:
    """状态里没有 phases 时，从磁盘上真实存在的图反推出来。

    早于 phases 记录的存档只有 days（一天一条），但素材图本来就是按时段分开的
    （scene_<日期>_<时段>.png）—— 图在磁盘上，这条记录就补得回来，不算编造。
    """
    phases = state.get("phases")
    if not isinstance(phases, dict):
        phases = {}
    days = state.get("days")
    if not isinstance(days, dict):
        return phases
    for day in sorted(days):
        day_phases = phases.get(day)
        if not isinstance(day_phases, dict):
            day_phases = {}
        for phase in PHASES:
            if phase in day_phases:
                continue
            art_name = _art_name(day, phase)
            if not (ART_DIR / art_name).is_file():
                continue
            day_phases[phase] = {
                "date": day,
                "phase": phase,
                "phase_label": PHASE_LABEL.get(phase, ""),
                "art_url": f"/static/xiaojing/{art_name}",
                "has_image": "yes",
                "note": "按时段重建",
            }
        if day_phases:
            phases[day] = day_phases
    state["phases"] = phases
    return phases


def _lock_acquire() -> bool:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            age = time_now() - LOCK_FILE.stat().st_mtime
            if age > LOCK_STALE_SEC:
                LOCK_FILE.unlink(missing_ok=True)
                fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            else:
                return False
        except OSError:
            return False
    except OSError:
        return False
    try:
        os.write(fd, str(os.getpid()).encode())
    finally:
        os.close(fd)
    return True


def _lock_release() -> None:
    try:
        LOCK_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def time_now() -> float:
    import time

    return time.time()


# --------------------------------------------------------------------------
# 时刻：程序算
# --------------------------------------------------------------------------


def _local_now(config: dict | None) -> datetime:
    tz_name = str((config or {}).get("timezone") or "Asia/Shanghai")
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(tz_name)
    except Exception:
        tz = timezone(timedelta(hours=8))
    return datetime.now(tz)


def _naive(dt: datetime) -> datetime:
    return dt.replace(tzinfo=None)


def _phase_from_clock(now_local: datetime) -> str:
    """没有日出日落数据时的粗分档。"""
    h = now_local.hour + now_local.minute / 60.0
    if 5.0 <= h < 8.0:
        return "dawn"
    if 8.0 <= h < 16.0:
        return "day"
    if 16.0 <= h < 19.0:
        return "dusk"
    return "night"


def _phase_from_sun(now_local: datetime, sunrise: datetime, sunset: datetime) -> str:
    """按真实日出日落分档。

    - 日出前 40 分钟前 → 夜
    - 日出后 100 分钟内 → 晨
    - 日落前 100 分钟前 → 昼
    - 日落后 60 分钟内 → 暮
    - 之后 → 夜
    """
    now = _naive(now_local)
    sr = _naive(sunrise)
    ss = _naive(sunset)
    if now < sr - timedelta(minutes=40):
        return "night"
    if now < sr + timedelta(minutes=100):
        return "dawn"
    if now < ss - timedelta(minutes=100):
        return "day"
    if now < ss + timedelta(minutes=60):
        return "dusk"
    return "night"


def _parse_sun_time(value) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


async def _sun_times(config: dict | None, now_local: datetime) -> tuple[datetime | None, datetime | None]:
    """今天的日出日落（本地时间）。取不到就返回 None，走粗分档。"""
    try:
        from .sun_moon import _fetch_daily, _resolve_location

        location = _resolve_location(config if isinstance(config, dict) else {})
        data = await _fetch_daily(location)
        rows = data.get("rows") if isinstance(data, dict) else None
        if not isinstance(rows, list) or not rows:
            return None, None
        today = now_local.strftime("%Y-%m-%d")
        row = next((r for r in rows if isinstance(r, dict) and str(r.get("date")) == today), None)
        if row is None:
            row = rows[-1] if isinstance(rows[-1], dict) else None
        if not row:
            return None, None
        return _parse_sun_time(row.get("sunrise")), _parse_sun_time(row.get("sunset"))
    except Exception as exc:
        logger.info("[Xiaojing] 取日出日落失败，走粗分档: %s", exc)
        return None, None


async def current_phase(config: dict | None) -> tuple[str, datetime]:
    """当前时段与本地当前时间。"""
    now_local = _local_now(config)
    sunrise, sunset = await _sun_times(config, now_local)
    if sunrise and sunset and sunset > sunrise:
        phase = _phase_from_sun(now_local, sunrise, sunset)
    else:
        phase = _phase_from_clock(now_local)
    return phase, now_local


def _scene_hint(date_key: str) -> str:
    try:
        idx = date.fromisoformat(date_key).timetuple().tm_yday
    except (TypeError, ValueError):
        idx = abs(hash(date_key)) % 365
    return _SCENE_HINTS[idx % len(_SCENE_HINTS)]


# --------------------------------------------------------------------------
# 调色：按时段决定四色各自占多少比重
# --------------------------------------------------------------------------


def _curve(fn) -> list:
    return [max(0, min(255, int(fn(i)))) for i in range(256)]


def grade_for_phase(img, phase: str):
    """按时段给整张图调一次色。

    只决定四色的比重，不做四色归色（归色是渲染器的 Atkinson 抖动干的活）。
    夜里把中低调压到接近黑，只留月光这类高光；暮时压蓝保红，让朱红当主角。
    """
    from PIL import Image

    img = img.convert("RGB")
    r, g, b = img.split()
    if phase == "dawn":
        r = r.point(_curve(lambda v: v * 1.04 + 10))
        g = g.point(_curve(lambda v: v * 1.06 + 18))
        b = b.point(_curve(lambda v: v * 0.66))
    elif phase == "day":
        r = r.point(_curve(lambda v: v * 1.10 + 8))
        g = g.point(_curve(lambda v: v * 1.10 + 8))
        b = b.point(_curve(lambda v: v * 0.48))
    elif phase == "dusk":
        r = r.point(_curve(lambda v: v * 1.16 + 20))
        g = g.point(_curve(lambda v: v * 0.82 + 8))
        b = b.point(_curve(lambda v: v * 0.30))
    elif phase == "night":
        # 夜只做很轻的一层：稍冷、稍压红。
        #
        # 画面本身该由出图负责（四色盘段里已写明「墨色只作线条与剪影，
        # 不要大面积涂满」），调色不背这个锅 ——
        # 之前三轮调色都在救一片黑，救不回来，白费。
        r = r.point(_curve(lambda v: min(255.0, v * 0.99 + 4)))
        g = g.point(_curve(lambda v: min(255.0, v * 0.99 + 4)))
        b = b.point(_curve(lambda v: min(255.0, v * 1.01 + 8)))
        return Image.merge("RGB", (r, g, b))
    return Image.merge("RGB", (r, g, b))


def prepare_art_file(src: Path, dest: Path, phase: str) -> tuple[bool, str]:
    """生图原图 → 上屏尺寸（裁白边、保线下采样）→ 按时段调色 → 落盘。"""
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
        from . import eink_snap

        art_img = eink_snap.prepare_direct_art(src_img, dw, dh, downsample="box", ink_gamma=1.25)
    except Exception as exc:
        return False, f"上屏准备失败: {exc}"

    try:
        art_img = grade_for_phase(art_img, phase)
    except Exception as exc:
        logger.warning("[Xiaojing] 调色失败，用原图: %s", exc)

    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        art_img.save(dest, "PNG")
    except OSError as exc:
        return False, f"写文件失败: {exc}"

    return True, f"{dw}x{dh} {phase}"


# --------------------------------------------------------------------------
# 生图
# --------------------------------------------------------------------------


async def _generate_art(phase: str, dest: Path, hint: str, **kwargs) -> tuple[bool, str]:
    """调 MiniMax 生图 → 下载 → 备成上屏尺寸。"""
    import httpx

    key = (kwargs.get("image_api_key") or "").strip() or os.getenv("MINIMAX_API_KEY", "").strip()
    if not key or key.startswith("sk-your-"):
        return False, "没有可用的生图凭证"

    # 四段结构，对齐 little-prince：时段 + 背景 + 场景 + 四色盘 + 风格。
    prompt = (
        f"{_PHASE_PROMPT[phase]}，{_PHASE_BACKGROUND[phase]}。"
        f"今天的场景是{hint}，画面其余部分由你自己决定。"
        f"{_PALETTE_SPEC} {_PHASE_INK_WEIGHT[phase]} {_STYLE_PROMPT}。"
    )
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    reasons: list[str] = []

    for text in (prompt, prompt + _RETRY_PROMPT):
        payload = {
            "model": IMAGE_MODEL,
            "prompt": text,
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

        ok, reason = prepare_art_file(tmp, dest, phase)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        if ok:
            return True, reason
        reasons.append(reason)

    return False, "；".join(reasons) or "生图未成功"


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------


def _art_name(date_key: str, phase: str) -> str:
    return f"scene_{date_key.replace('-', '')}_{phase}.png"


def _latest_existing_art() -> str:
    """出图失败时的兜底：沿用最近一张还留着的小景，别让屏空着。"""
    try:
        files = sorted(ART_DIR.glob("scene_*.png"))
    except OSError:
        return ""
    if not files:
        return ""
    return f"/static/xiaojing/{files[-1].name}"


async def generate_scene(
    phase: str,
    date_key: str,
    *,
    force: bool = False,
    update_current: bool = True,
    **kwargs,
) -> dict:
    """出（或复用）某天某时段这张小景。跨时段就换一张。

    一天四张（晨/昼/暮/夜）各记各的：顶层记当前时段，phases 记全部时段 ——
    回顾要一天四张，不能互相覆盖。
    """
    state = _read_state()
    reconstruct_phases(state)
    days = state.get("days")
    if not isinstance(days, dict):
        days = {}
    today = days.get(date_key)
    if not isinstance(today, dict):
        today = {}

    art_name = _art_name(date_key, phase)
    art_path = ART_DIR / art_name

    if not force and today.get("phase") == phase and art_path.exists():
        record = {
            "date": date_key,
            "phase": phase,
            "phase_label": PHASE_LABEL.get(phase, ""),
            "art_url": f"/static/xiaojing/{art_name}",
            "has_image": "yes",
            "note": "复用",
        }
        state["days"] = days
        _write_phase_record(state, date_key, phase, record)
        _write_state(state)
        return record

    if not _lock_acquire():
        # 别的进程正在出图：有旧图就用旧的，没有就退成无图。
        return {
            "date": date_key,
            "phase": phase,
            "art_url": _latest_existing_art(),
            "has_image": "yes" if _latest_existing_art() else "no",
            "note": "出图中",
        }

    try:
        hint = _scene_hint(date_key)
        if art_path.exists():
            ok, reason = True, "复用已有图"
        else:
            ok, reason = await _generate_art(phase, art_path, hint, **kwargs)
        art_url = f"/static/xiaojing/{art_name}" if ok else ""
        if not ok:
            logger.warning("[Xiaojing] %s %s 没出图: %s", date_key, phase, reason)
            fallback_url = _latest_existing_art()
            if fallback_url:
                return {
                    "date": date_key,
                    "phase": phase,
                    "art_url": fallback_url,
                    "has_image": "yes",
                    "note": f"沿用旧图（{reason}）",
                }
    finally:
        _lock_release()

    record = {
        "date": date_key,
        "phase": phase,
        "phase_label": PHASE_LABEL.get(phase, ""),
        "art_url": art_url,
        "has_image": "yes" if art_url else "no",
        "note": reason,
    }
    # 顶层只跟今天 —— 历史日回填不能改掉当天正在用的那条。
    # update_current=False 时连今天也不动：回填补的是「那天各时段那几张」，
    # 不是「设备此刻该显示哪张」。
    if update_current and date_key == datetime.now().strftime("%Y-%m-%d"):
        days[date_key] = record
    else:
        days.setdefault(date_key, {"date": date_key})
    state["days"] = days
    _write_phase_record(state, date_key, phase, record)
    _write_state(state)

    return record


def _history_content(record: dict, fallback: dict | None) -> dict:
    """历史日：只读记到过的那张，不补生成。"""
    from .history_day import MISSING

    art_url = str(record.get("art_url") or "")
    content = {
        "art_url": art_url,
        "has_image": "yes" if art_url else "no",
        "phase": str(record.get("phase_label") or MISSING),
        "phase_label": str(record.get("phase_label") or MISSING),
    }
    if isinstance(fallback, dict):
        for key, value in fallback.items():
            if not content.get(key):
                content[key] = value
    return content


async def build_xiaojing_content(fallback: dict | None = None, **kwargs) -> dict:
    from .history_day import resolve_day

    date_key = resolve_day(kwargs)
    config = kwargs.get("config")
    config = config if isinstance(config, dict) else {}

    # 指定时段（历史回填用）：渲染那天的某一个时段，而不是当前时段。
    date_ctx = kwargs.get("date_ctx") or {}
    phase_override = str(date_ctx.get("phase_override") or "")
    if phase_override not in PHASES:
        phase_override = ""

    state = _read_state()
    days = state.get("days")
    today = days.get(date_key) if isinstance(days, dict) else None

    # 历史日：只读已有记录。绝不补生成 —— 那天没生成过就是没生成过。
    # 指定了时段是例外：回填要补的正是那一天各时段那几张。
    if not isinstance(today, dict) or not today.get("art_url"):
        if date_key != datetime.now().strftime("%Y-%m-%d") and not phase_override:
            return _history_content(today or {}, fallback)

    if phase_override:
        # 指定时段：只补那一张，不改「设备此刻该显示哪张」。
        phase = phase_override
        real_phase, _now = await current_phase(config)
        update_current = (phase == real_phase)
    else:
        phase, now_local = await current_phase(config)
        update_current = True
    record = await generate_scene(phase, date_key, update_current=update_current, **kwargs)

    content = {
        "art_url": str(record.get("art_url") or ""),
        "has_image": str(record.get("has_image") or "no"),
        "phase": PHASE_LABEL.get(phase, ""),
        "phase_label": PHASE_LABEL.get(phase, ""),
        # 英文时段 key：存档层靠它把同一时段的那张图归到一处。
        "phase_key": phase,
    }
    if isinstance(fallback, dict):
        for key, value in fallback.items():
            if not content.get(key):
                content[key] = value
    return content


async def tick(config: dict | None = None, **kwargs) -> dict:
    """定时任务入口：算出当前时段，需要就换一张新图。"""
    config = config if isinstance(config, dict) else {}
    phase, now_local = await current_phase(config)
    date_key = now_local.strftime("%Y-%m-%d")
    record = await generate_scene(phase, date_key, **kwargs)
    return record
