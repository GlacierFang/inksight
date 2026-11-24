"""
05 §2.2 `app/router_ai.py`：三级意图分流、并发锁、配额仲裁与防依赖教学 Prompt。

三级分流（05 §4.2 第 3 条）：
* **第一级**：本地规则直出（音量/亮度/课表/天气），**零 LLM 调用**；
* **第二级**：轻量纯文本模型（概念问答、单词、卡片追问）；
* **第三级**：多模态视觉模型（拍题导学，05 §4.3 四条铁律 System Prompt）。

配额仲裁（05 §4.2，**服务端账本是唯一真相源**）：
* `effective_remaining = daily_quota - used_count`，**不看设备上报值**；
* 设备上报的 `device_remaining_quota` 仅记入账本 `device_reported_remaining`
  供对账，偏差超 1 次记告警，**不改变判定**；
* 同题追问沿用同一 `solve_session_id` → **不重复扣费**；
* 仅在模型**首次成功**返回后扣费（`code == 0` 且 `status != "NEED_DRAFT"`）。

学段派生（04 §3.1 确定性算法，端云必须一致）。
"""

import asyncio
import json
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

import httpx

from app.config import settings
from app.speech_engine import get_speech_engine

CST = timezone(timedelta(hours=8))

# 05 §3.5 业务状态码
CODE_OK = 0
CODE_SIGN = 4001
CODE_ASR = 4003
CODE_PROFILE = 4004
CODE_LOCK = 4290
CODE_QUOTA = 4291
CODE_UPSTREAM = 5001

VALID_EDU_STAGES = ("PRIMARY", "JUNIOR", "SENIOR", "ADULT")


class RouterError(RuntimeError):
    """
    路由层错误。`code` 为 05 §3.5 业务状态码。

    `user_msg` 是**给用户看的温和文案**（可选）—— 同一个业务码可能对应多种原因
    （如 4004 既可能是学段没设置，也可能是没拍到题），文案必须与真实原因相符，
    否则会把用户引向错误的操作。
    """

    def __init__(self, message: str, code: int = CODE_UPSTREAM, user_msg: str = ""):
        super().__init__(message)
        self.code = code
        self.user_msg = user_msg


# ------------------------------------------------------------------ 学段派生
def derive_grade_anchor(edu_stage: str, enrollment_year: int, now: Optional[datetime] = None) -> str:
    """
    确定性派生学段标签（04 §3.1 端云双方严格一致）。

    `academic_year = cur_year if (cur_month >= 9) else (cur_year - 1)`
    `term = "上" if (cur_month >= 9 or cur_month == 1 or (cur_month == 2 and cur_day <= 15)) else "下"`
    """
    if edu_stage == "ADULT":
        return "通用进修"
    now = now or datetime.now(CST)
    cur_year, cur_month, cur_day = now.year, now.month, now.day
    academic_year = cur_year if cur_month >= 9 else cur_year - 1
    grade_num = academic_year - enrollment_year + 1
    term = "上" if (cur_month >= 9 or cur_month == 1 or (cur_month == 2 and cur_day <= 15)) else "下"
    max_grade = 6 if edu_stage == "PRIMARY" else (3 if edu_stage in ("JUNIOR", "SENIOR") else 0)
    if grade_num < 1 or grade_num > max_grade:
        return "通用进修"
    prefix = "小学" if edu_stage == "PRIMARY" else ("初中" if edu_stage == "JUNIOR" else "高中")
    return f"{prefix}{grade_num}年级{term}"


def validate_profile(edu_stage: str, enrollment_year: int) -> None:
    """画像字段缺失/越界 → `4004`（05 §5.1）。"""
    if edu_stage not in VALID_EDU_STAGES:
        raise RouterError(
            "学段设置不正确", CODE_PROFILE, "还没设置好学段哦，先去设置里选一下"
        )
    if not isinstance(enrollment_year, int) or not (2000 <= enrollment_year <= 2100):
        raise RouterError(
            "入学年份设置不正确", CODE_PROFILE, "还没设置好学段哦，先去设置里选一下"
        )


# ------------------------------------------------------------------ 教学 Prompt
def build_teaching_prompt(grade_anchor: str, user_voice_text: str, hint_level: int) -> str:
    """05 §4.3 防依赖教学脚手架 System Prompt（四条铁律，**逐字对齐契约**）。"""
    return f"""你是一位克制、启发式的私人学习导师。当前学生处于【{grade_anchor}】阶段，严格把控知识点边界，绝不使用超纲定理。
你必须严格遵守以下四条教学铁律：
1. 【草稿痕迹初筛】：首先观察图片中题目旁是否有学生手写的演算、辅助线或圈画痕迹。如果卷面完全空白，且学生未提供有效语音思路（"{user_voice_text}"为空），你必须拒绝直接讲解，仅用一句话温和引导其先写出第一步想法或画出已知条件，输出 status="NEED_DRAFT"。
2. 【阶梯拆解（当前层级：{hint_level}/2）】：
   - 层级 0：仅点破题目考察的核心概念与第一步切入点，严禁给出公式代入和最终答案。
   - 层级 1：给出关键的中间转化步骤或辅助线思路，留出核心计算步骤让学生自己完成。
   - 层级 2：核对学生思路并给出完整清晰的逻辑梳理与结论。
3. 【留白验算】：在层级 0 和层级 1 的回复结尾，必须抛出一个具体的微问题让孩子口头回答（例如："根据这个条件，你能算出 ∠A 是多少度吗？"）。
4. 【费曼反问】：当判定学生已经做对或进入层级 2 完结时，必须在结尾附带 1 句简短的"费曼反问"（例如："如果把题干里的'平行'改成'相交'，结论还成立吗？"），验证其是否真正理解。
输出必须为严格 JSON：{{"status": "NEED_DRAFT|HINTING|SOLVED", "speech_text": "用于语音播报的口语化短句(<=80字)", "display_markdown": "用于屏幕分步阅读的结构化解析", "feynman_question": "可选反问句"}}"""


# ------------------------------------------------------------------ 上游调用
def _api_key(provider: str) -> str:
    import os

    for name in (f"{provider.upper()}_API_KEY", "MINIMAX_API_KEY"):
        val = os.environ.get(name, "")
        if val:
            return val
    return ""


def _check_base_resp(data: Dict[str, Any]) -> None:
    """
    上游返回 HTTP 200 不代表成功 —— 必须解析 body 的 `base_resp.status_code`。
    `2069` = 模型已下架（05 §6.3 明确警告）。
    """
    base = data.get("base_resp") or {}
    code = base.get("status_code")
    if code not in (None, 0):
        raise RouterError(
            f"上游模型不可用（status_code={code}）：{base.get('status_msg', '')}", CODE_UPSTREAM
        )


class RouterAI:
    """三级意图分流 + 配额仲裁 + Prompt 注入。"""

    def __init__(self, storage=None):
        self._storage = storage
        self._locks: Dict[str, asyncio.Lock] = {}

    def storage(self):
        if self._storage is not None:
            return self._storage
        from app.storage import get_storage

        return get_storage()

    def lock_for(self, device_id: str) -> asyncio.Lock:
        """每设备并发锁（05 §4.2 第 1 条）：同一设备前一个推理未完成前拒绝并发。"""
        lock = self._locks.get(device_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[device_id] = lock
        return lock

    # ------------------------------------------------------------ 配额
    def _today(self) -> str:
        return datetime.now(CST).strftime("%Y-%m-%d")

    def quota_state(self, device_id: str) -> Dict[str, Any]:
        """
        读该设备的服务端配额账本状态。

        **账本真相源 = 服务端 `storage/quota.json`**。设备上报值不参与判定。
        账本损坏/缺失 → `used_count` 归零重算（05 §4.2 降级规则）。
        """
        ledger = self.storage().read_quota() or {}
        entry = ledger.get(device_id) or {}
        if entry.get("date") != self._today():
            # 跨日重置（UTC+8 服务器日期为准）
            entry = {"date": self._today(), "used_count": 0, "charged_sessions": []}
        return entry

    def effective_remaining(self, device_id: str) -> int:
        """`effective_remaining = daily_quota - used_count`（**只认服务端账本**）。"""
        entry = self.quota_state(device_id)
        return int(settings.daily_quota) - int(entry.get("used_count", 0))

    def _save_quota(self, device_id: str, entry: Dict[str, Any]) -> None:
        ledger = self.storage().read_quota() or {}
        ledger[device_id] = entry
        self.storage().write_quota(ledger)

    def record_reported_quota(self, device_id: str, reported: Optional[int]) -> None:
        """
        记录设备上报的剩余配额 —— **仅对账，不影响判定**。

        与服务端计算值偏差超过 1 次即记一条告警日志（供管理台排查）。
        """
        if reported is None:
            return
        try:
            reported = int(reported)
        except (TypeError, ValueError):
            return
        entry = self.quota_state(device_id)
        server_remaining = int(settings.daily_quota) - int(entry.get("used_count", 0))
        entry["device_reported_remaining"] = reported
        if abs(reported - server_remaining) > 1:
            print(
                f"[quota] 对账偏差：设备 {device_id} 上报 {reported}，服务端账本 {server_remaining}"
            )
        self._save_quota(device_id, entry)

    def check_quota(self, device_id: str, solve_session_id: str) -> Tuple[bool, int]:
        """
        配额闸门。返回 `(是否放行, effective_remaining)`。

        * 同题追问（`solve_session_id` 已扣费过）→ **直接放行且不重复扣费**；
        * 否则看服务端账本，`<= 0` → 拒绝（`4291`）。
        """
        entry = self.quota_state(device_id)
        charged = entry.get("charged_sessions") or []
        if solve_session_id in charged:
            return True, int(settings.daily_quota) - int(entry.get("used_count", 0))
        remaining = int(settings.daily_quota) - int(entry.get("used_count", 0))
        return remaining > 0, remaining

    def charge(self, device_id: str, solve_session_id: str) -> None:
        """模型首次成功返回后扣费并原子写回账本。"""
        entry = self.quota_state(device_id)
        charged = list(entry.get("charged_sessions") or [])
        if solve_session_id in charged:
            return
        charged.append(solve_session_id)
        entry["charged_sessions"] = charged
        entry["used_count"] = int(entry.get("used_count", 0)) + 1
        entry["date"] = self._today()
        self._save_quota(device_id, entry)

    # ------------------------------------------------------------ 模型调用
    def _ai_cfg(self) -> Dict[str, Any]:
        return settings.ai

    def _text_call(self, system_prompt: str, user_content: Any) -> Dict[str, Any]:
        """调用轻量纯文本模型（第二级 / 第三级文本兜底）。"""
        cfg = self._ai_cfg()
        provider = cfg.get("provider", "")
        if provider == "mock":
            return self._mock_text(user_content)
        base_url = (cfg.get("base_url") or "").rstrip("/")
        model = cfg.get("text_model", "")
        api_key = _api_key(provider)
        if not base_url or not model or not api_key:
            raise RouterError("模型服务配置不完整或缺少密钥", CODE_UPSTREAM)

        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.6,
        }
        try:
            with httpx.Client(timeout=float(cfg.get("timeout_seconds", 60)), trust_env=False) as client:
                resp = client.post(
                    f"{base_url}/v1/text/chatcompletion_v2",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
        except httpx.TimeoutException:
            raise RouterError("模型响应超时了", CODE_UPSTREAM) from None
        except httpx.HTTPError as exc:
            raise RouterError(f"模型请求失败：{exc}", CODE_UPSTREAM) from exc

        if resp.status_code >= 400:
            raise RouterError(f"模型服务返回错误：{resp.status_code}", CODE_UPSTREAM)
        try:
            data = resp.json()
        except ValueError:
            raise RouterError("模型返回内容无法解析", CODE_UPSTREAM) from None
        _check_base_resp(data)
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise RouterError("模型返回结构异常", CODE_UPSTREAM) from None

    def _vision_call(self, system_prompt: str, image_b64: str, user_voice_text: str) -> str:
        """调用多模态视觉模型（第三级：拍题导学）。"""
        cfg = self._ai_cfg()
        provider = cfg.get("provider", "")
        if provider == "mock":
            return self._mock_vision(system_prompt)
        base_url = (cfg.get("base_url") or "").rstrip("/")
        model = cfg.get("vision_model", "")
        api_key = _api_key(provider)
        if not base_url or not model or not model.startswith("<"):
            raise RouterError("视觉模型尚未配置", CODE_UPSTREAM)
        if not api_key:
            raise RouterError("模型服务缺少密钥", CODE_UPSTREAM)

        content: Any = [
            {"type": "text", "text": user_voice_text or "请分析这道题。"},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
        ]
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
            "temperature": 0.6,
        }
        try:
            with httpx.Client(timeout=float(cfg.get("timeout_seconds", 60)), trust_env=False) as client:
                resp = client.post(
                    f"{base_url}/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
        except httpx.TimeoutException:
            raise RouterError("模型响应超时了", CODE_UPSTREAM) from None
        except httpx.HTTPError as exc:
            raise RouterError(f"模型请求失败：{exc}", CODE_UPSTREAM) from exc

        if resp.status_code >= 400:
            raise RouterError(f"模型服务返回错误：{resp.status_code}", CODE_UPSTREAM)
        try:
            data = resp.json()
        except ValueError:
            raise RouterError("模型返回内容无法解析", CODE_UPSTREAM) from None
        _check_base_resp(data)
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise RouterError("模型返回结构异常", CODE_UPSTREAM) from None

    # ------------------------------------------------------------ mock 上游
    #
    # `config.yaml` 里 `ai.provider: "mock"` 时启用。它**不调任何外部服务**，
    # 按 05 §4.3 的输出契约返回结构合法的固定 JSON，用于无网络/无密钥时的
    # 联调自检（验收清单里的“控制台模拟拍题闭环”）。
    # 生产部署必须把 `ai.provider` 改成真实服务商。

    def _mock_vision(self, system_prompt: str) -> str:
        """返回严格符合 05 §4.3 输出契约的 JSON 字符串。"""
        level = 0
        m = re.search(r"当前层级：(\d)/2", system_prompt)
        if m:
            level = int(m.group(1))
        # 【草稿痕迹初筛】：卷面空白且学生无语音思路 → NEED_DRAFT（不扣配额）
        voice = ""
        vm = re.search(r"（\"([^\"]*)\"为空）", system_prompt)
        if vm:
            voice = vm.group(1)
        status = "SOLVED" if level >= 2 else ("HINTING" if voice or level >= 1 else "NEED_DRAFT")
        payload = {
            "status": status,
            "speech_text": (
                "这道题先想想已知条件，能先说说你打算怎么做吗？"
                if status == "NEED_DRAFT"
                else "我们先看这一步的思路，你自己算算下一步该算什么。"
            ),
            "display_markdown": "### 思路\n1. 读题并圈出已知条件\n2. 想清楚要用哪个关系\n3. 动手算",
            "feynman_question": "如果换个条件，这个结论还成立吗？",
        }
        return json.dumps(payload, ensure_ascii=False)

    def _mock_text(self, user_content: Any) -> str:
        text = str(user_content)
        return f"（mock 上游）收到你的问题：{text[:40]}。这是联调占位回复。"

    # ------------------------------------------------------------ 拍题导学
    def solve(self, device_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        第三级：拍题导学。返回 05 §5 约定的响应字段。

        流程：画像校验 → 学段派生 → 配额闸门 → ASR（若有 PCM）→ 视觉模型 →
        严格 JSON 解析 → TTS → 扣费。
        """
        edu_stage = payload.get("edu_stage", "")
        enrollment_year = payload.get("enrollment_year", 0)
        validate_profile(edu_stage, enrollment_year)

        solve_session_id = payload.get("solve_session_id") or ""
        image_b64 = payload.get("image_b64") or ""
        if not solve_session_id:
            raise RouterError("缺少会话标识", CODE_PROFILE, "这次提问没识别出来，再试一次")
        if not image_b64:
            raise RouterError(
                "没有收到题目照片", CODE_PROFILE, "没拍到题目，再拍一次试试"
            )

        hint_level = int(payload.get("hint_level", 0) or 0)
        hint_level = max(0, min(2, hint_level))

        # 设备上报配额 —— 仅对账
        self.record_reported_quota(device_id, payload.get("device_remaining_quota"))

        allowed, remaining = self.check_quota(device_id, solve_session_id)
        if not allowed:
            raise RouterError("今天的深度导学次数用完了，明天再来吧", CODE_QUOTA)

        grade_anchor = derive_grade_anchor(edu_stage, enrollment_year)
        user_voice_text = payload.get("voice_text") or ""
        if payload.get("pcm_base64"):
            try:
                user_voice_text = get_speech_engine().transcribe(payload["pcm_base64"])
            except Exception as exc:
                # ASR 失败不阻断拍题导学（图片已够用）
                print(f"[solve] ASR 失败，降级为纯图片：{exc}")

        system_prompt = build_teaching_prompt(grade_anchor, user_voice_text, hint_level)
        raw = self._vision_call(system_prompt, image_b64, user_voice_text)
        parsed = self._parse_teach_json(raw)

        status = parsed.get("status", "HINTING")
        speech_text = (parsed.get("speech_text") or "").strip()
        display_markdown = parsed.get("display_markdown") or ""
        feyman = (parsed.get("feynman_question") or "").strip()

        # TTS（静默降级：失败只置 has_audio=false，不报错）
        has_audio = False
        tts_key = None
        if speech_text:
            tts_key, audio = get_speech_engine().synthesize(speech_text)
            has_audio = audio is not None

        # 扣费：仅在首次成功返回且不是 NEED_DRAFT 时扣
        if status != "NEED_DRAFT":
            self.charge(device_id, solve_session_id)

        return {
            "status": status,
            "speech_text": speech_text,
            "display_markdown": display_markdown,
            "feynman_question": feyman,
            "has_audio": has_audio,
            "tts_cache_key": tts_key or "",
            "audio_url": "",
            "effective_remaining_quota": self.effective_remaining(device_id),
        }

    def _parse_teach_json(self, raw: str) -> Dict[str, Any]:
        """解析模型返回的严格 JSON（容忍 ```json 包裹与前后杂句）。"""
        text = (raw or "").strip()
        fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
        if fence:
            text = fence.group(1).strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # 退而求其次：截取最外层 {...}
            start, end = text.find("{"), text.rfind("}")
            if start >= 0 and end > start:
                try:
                    data = json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    raise RouterError("模型返回的不是合法 JSON", CODE_UPSTREAM) from None
            else:
                raise RouterError("模型返回的不是合法 JSON", CODE_UPSTREAM) from None
        if not isinstance(data, dict):
            raise RouterError("模型返回结构异常", CODE_UPSTREAM)
        return data

    # ------------------------------------------------------------ 语音指令
    def voice(self, device_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        语音指令 / 问答 / 卡片追问 —— 三级分流。

        返回 `intent_type` / `action_payload` / `reply_text` / `has_audio` /
        `tts_cache_key` / `audio_url`。
        """
        edu_stage = payload.get("edu_stage", "")
        enrollment_year = payload.get("enrollment_year", 0)
        validate_profile(edu_stage, enrollment_year)

        pcm_b64 = payload.get("pcm_base64") or ""
        if not pcm_b64:
            raise RouterError("没有收到录音", CODE_ASR, "没听清，再说一次好吗")
        text = get_speech_engine().transcribe(pcm_b64)

        # ---- 第一级：本地规则直出（零 LLM 调用）----
        local = self._local_intent(text)
        if local:
            local["has_audio"] = False
            local["tts_cache_key"] = ""
            local["audio_url"] = ""
            return local

        # ---- 第二级：卡片上下文追问 / 自由问答 ----
        context = payload.get("context") or {}
        pack_id = context.get("pack_id") or ""
        card_id = context.get("card_id") or ""
        extra = ""
        if pack_id and card_id:
            extra = self._load_card_context(pack_id, card_id)

        grade_anchor = derive_grade_anchor(edu_stage, enrollment_year)
        system_prompt = (
            f"你是一位克制、启发式的私人学习导师。当前学生处于【{grade_anchor}】阶段。"
            "回答简短口语化，不超过 60 字，不要使用超纲内容。"
        )
        user_text = f"{extra}\n\n学生的问题：{text}" if extra else f"学生的问题：{text}"
        answer = self._text_call(system_prompt, user_text)
        answer = (answer or "").strip()

        tts_key, audio = get_speech_engine().synthesize(answer) if answer else (None, None)
        return {
            "intent_type": "AI_REPLY",
            "action_payload": {"action": "NO_ACTION"},
            "reply_text": answer,
            "has_audio": audio is not None,
            "tts_cache_key": tts_key or "",
            "audio_url": "",
        }

    def _local_intent(self, text: str) -> Optional[Dict[str, Any]]:
        """第一级：本地规则（音量/亮度/课表/天气）。"""
        t = text.strip()

        m = re.search(r"(音量|声音|响)[^\d]{0,4}(大|高|响)一?[点些]", t) or re.search(
            r"(调大|调高|大声)[^一-龥]{0,2}", t
        )
        if m:
            return {
                "intent_type": "LOCAL_ACTION",
                "action_payload": {"action": "SET_VOLUME", "delta": 1, "value": 0},
                "reply_text": "音量调大一点。",
            }
        if re.search(r"(音量|声音|响)[^\d]{0,4}(小|低|轻)", t) or re.search(r"(调小|调低|小声)", t):
            return {
                "intent_type": "LOCAL_ACTION",
                "action_payload": {"action": "SET_VOLUME", "delta": -1, "value": 0},
                "reply_text": "音量调小一点。",
            }
        if re.search(r"(屏幕|亮度)[^\d]{0,4}(亮|大)", t) or re.search(r"(调亮|亮一点)", t):
            return {
                "intent_type": "LOCAL_ACTION",
                "action_payload": {"action": "SET_BRIGHTNESS", "delta": 1, "value": 0},
                "reply_text": "屏幕调亮一点。",
            }
        if re.search(r"(屏幕|亮度)[^\d]{0,4}(暗|小|低)", t) or re.search(r"(调暗|暗一点)", t):
            return {
                "intent_type": "LOCAL_ACTION",
                "action_payload": {"action": "SET_BRIGHTNESS", "delta": -1, "value": 0},
                "reply_text": "屏幕调暗一点。",
            }
        if re.search(r"(课表|课程|今天.*课|明天.*课)", t):
            day = "tomorrow" if "明天" in t else "today"
            return {
                "intent_type": "LOCAL_ACTION",
                "action_payload": {"action": "SHOW_SCHEDULE", "day": day},
                "reply_text": "今天的课表已经显示在屏幕上了。" if day == "today" else "明天的课表已经显示在屏幕上了。",
            }
        if re.search(r"(天气|气温|下雨|温度)", t):
            weather = self._weather_snapshot()
            if weather:
                return {
                    "intent_type": "WEATHER_REPLY",
                    "action_payload": {"action": "NO_ACTION"},
                    "reply_text": f"现在{weather.get('text', '')}，{weather.get('temp', '')}度。",
                }
        return None

    def _weather_snapshot(self) -> Optional[Dict[str, Any]]:
        """读 `storage/cache/weather.json`（过期则异步更新，这里只读缓存）。"""
        from app.storage import read_json

        return read_json(self.storage().weather_file, None)

    def _load_card_context(self, pack_id: str, card_id: str) -> str:
        """从 `storage/packs/<pack_id>/source.json` 检索该卡明文上下文。"""
        from app.storage import read_json

        src = read_json(self.storage().pack_dir(pack_id) / "source.json", None)
        if not src:
            return ""
        for card in src.get("cards", []) or []:
            if card.get("id") == card_id:
                parts = [
                    f"当前卡片题干：{card.get('question', '')}",
                    f"第一层提示：{card.get('hint1', '')}",
                    f"第二层提示：{card.get('hint2', '')}",
                    f"完整解析：{card.get('analysis', '')}",
                ]
                return "\n".join(p for p in parts if p.split("：", 1)[-1])
        return ""


_router: Optional[RouterAI] = None


def get_router() -> RouterAI:
    global _router
    if _router is None:
        _router = RouterAI()
    return _router
