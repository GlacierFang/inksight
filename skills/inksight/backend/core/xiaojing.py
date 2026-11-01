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
MINIMAX_IMAGE_API_URL = f"{MINIMAX_BASE_URL}/image_generation"
IMAGE_MODEL = "image-01"
IMAGE_ASPECT = "3:2"

# 画心在屏上的显示尺寸，必须与模式 JSON 里 image 块的 width/height 一致。
# 400×300 基准屏：状态栏 36px + 图 264px + 无页脚 = 300px，正好占满。
ART_DISPLAY = (400, 264)

LOCK_STALE_SEC = 900

PHASES = ("dawn", "day", "dusk", "night")
PHASE_LABEL = {"dawn": "晨", "day": "昼", "dusk": "暮", "night": "夜"}

# ── 时段光线（程序定，不由模型定）──────────────────────────────────────────
_PHASE_PROMPT = {
    "dawn": (
        "early morning, the first pale light of sunrise, the sun low and just clearing the horizon, "
        "soft golden light lying on the land, low mist, everything quiet and fresh"
    ),
    "day": (
        "high midday, bright overhead sunlight, crisp clear light, sharp short shadows, "
        "a vivid clear day, the sun high in the sky"
    ),
    "dusk": (
        "the last light of sunset, the sun very low and touching the horizon, "
        "long stretched shadows, a burning orange-red sky, the end of the day"
    ),
    "night": (
        "the middle of the night, deep darkness, NO sunset, NO sunrise, no orange glow on the horizon, "
        "a moon high in the sky, stars overhead, the land lit only by cold moonlight, quiet and still"
    ),
}

# ── 风格规则（固定的部分）────────────────────────────────────────────────
_STYLE_PROMPT = (
    "cel-shaded flat-colour cartoon illustration in pop-art comic style, "
    "bold clean black outlines, flat colour blocks, no gradients, "
    "ben-day halftone dots, radiating sunburst rays, vintage screen-print texture, "
    "a restricted palette of only four inks: solid black, paper white, warm yellow and vermilion red, "
    "a single small solitary figure, tiny in the frame, dwarfed by the surrounding world, "
    "wide landscape, atmospheric depth, strong readable silhouette, "
    "absolutely no text, no letters, no numbers, no logo, no watermark, no speech bubble, no caption, no border"
)

_RETRY_PROMPT = (
    " Keep the composition wide and the figure very small. The picture must contain no lettering of any kind."
)

# ── 每天换一个「场景线索」：只给名词，不给构图 ───────────────────────────
_SCENE_HINTS = (
    "a wooden footbridge",
    "a stand of pine trees",
    "a winding river",
    "a field of tall grass",
    "a row of stone steps",
    "a cliff above the sea",
    "a misty lake shore",
    "a snowfield",
    "a hillside orchard",
    "a lonely stone lantern",
    "a wide wheat field",
    "a mountain shrine",
    "a sandbar at low water",
    "a bamboo grove",
    "a terraced hillside",
    "a ruined watchtower",
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
    else:  # night
        # 中低调几乎全灭（只剩墨黑），高光（月亮）留白 —— 夜里只该看得见月亮。
        def _night(v: float) -> float:
            if v <= 190:
                return v * 0.18
            return 34 + (v - 190) * (221.0 / 65.0)

        r = r.point(_curve(_night))
        g = g.point(_curve(lambda v: _night(v) * 0.96))
        b = b.point(_curve(_night))
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

    prompt = (
        f"{_PHASE_PROMPT[phase]}. {_STYLE_PROMPT}. "
        f"Today's scene: {hint} — you decide the rest of the picture."
    )
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    reasons: list[str] = []

    for text in (prompt, prompt + _RETRY_PROMPT):
        payload = {
            "model": IMAGE_MODEL,
            "prompt": text,
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
    **kwargs,
) -> dict:
    """出（或复用）今天这张小景。跨时段就换一张。"""
    state = _read_state()
    days = state.get("days")
    if not isinstance(days, dict):
        days = {}
    today = days.get(date_key)
    if not isinstance(today, dict):
        today = {}

    art_name = _art_name(date_key, phase)
    art_path = ART_DIR / art_name

    if not force and today.get("phase") == phase and art_path.exists():
        return {
            "date": date_key,
            "phase": phase,
            "art_url": f"/static/xiaojing/{art_name}",
            "has_image": "yes",
            "note": "复用",
        }

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

    today = {
        "date": date_key,
        "phase": phase,
        "phase_label": PHASE_LABEL.get(phase, ""),
        "art_url": art_url,
        "has_image": "yes" if art_url else "no",
        "note": reason,
    }
    days[date_key] = today
    state["days"] = days
    _write_state(state)

    return today


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

    state = _read_state()
    days = state.get("days")
    today = days.get(date_key) if isinstance(days, dict) else None

    # 历史日：只读已有记录。绝不补生成 —— 那天没生成过就是没生成过。
    if not isinstance(today, dict) or not today.get("art_url"):
        if date_key != datetime.now().strftime("%Y-%m-%d"):
            return _history_content(today or {}, fallback)

    phase, now_local = await current_phase(config)
    record = await generate_scene(phase, date_key, **kwargs)

    content = {
        "art_url": str(record.get("art_url") or ""),
        "has_image": str(record.get("has_image") or "no"),
        "phase": PHASE_LABEL.get(phase, ""),
        "phase_label": PHASE_LABEL.get(phase, ""),
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
