"""
05 §2.2 `app/main.py`：FastAPI 入口、全局异常处理、静态页面挂载。

设备端 API（05 §5，唯一口径来源）：
    POST /api/v1/pair              首次 6 位配对码换取密钥（**明文 JSON 请求**）
    GET  /api/v1/ping              标准响应信封自检
    POST /api/v1/sync/bootstrap    心跳同步（天气/课表/学科包/电子书元数据）
    GET  /api/v1/content/download  统一签名资产流式下载
    POST /api/v1/study/solve       拍题导学
    POST /api/v1/study/voice       语音指令 / 问答 / 卡片追问
    GET  /api/v1/ota/check         单通道固件升级检查

Admin API（05 §6.2）：`/admin/api/*`，会话 Cookie + CSRF 头校验。

**屏幕文案铁律**：所有对设备端返回的 `msg` 必须是温和、可操作的中文，
**不得**暴露技术术语（不出现 "signature" / "AES" / "HTTP 403" 这类字样）。
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app import security
from app.book_cleaner import BookCleanError, clean_book
from app.config import settings
from app.ota_manager import OtaError, OtaManager
from app.pack_builder import PackBuildError, build_pack, parse_source_text
from app.router_ai import (
    CODE_ASR,
    CODE_OK,
    CODE_PROFILE,
    CODE_QUOTA,
    CODE_SIGN,
    CODE_UPSTREAM,
    RouterError,
    derive_grade_anchor,
    get_router,
)
from app.speech_engine import SpeechError, get_speech_engine
from app.storage import get_storage

app = FastAPI(title="abbot study gateway", version="1.0.0")

# ---------------------------------------------------------------- 错误码 → 温和中文
# 设备端只显示 msg，**不显示 code**。措辞要像人对学生说话。
ERROR_TEXT = {
    4001: "连接有点不稳，请再试一次",
    4002: "配对码已经过期了，请在电脑上重新生成",
    4003: "没有听清，再说一次好吗",
    4004: "还没设置好学段哦，先去设置里选一下",
    4290: "上一个问题还在想，等一下下",
    4291: "今天的深度导学次数用完啦，明天再来",
    5001: "云端有点忙，稍后再试一次",
}


def err(
    code: int, detail: str = "", secret_hex: str = "", ts: str = "", user_msg: str = ""
) -> JSONResponse:
    """
    统一错误响应。

    `msg` 用 `ERROR_TEXT` 里的温和文案；`detail` 只进服务端日志，**不回给设备端**。

    `user_msg` 是业务层给出的**更准确的温和文案**（可选）—— 同一业务码可能对应
    多种原因（4004 既可能是学段没设置，也可能是没拍到题），文案必须与真实原因
    相符，否则会把用户引向错误的操作。

    **签名**：05 §3.2 要求「网关返回**任何**业务响应时」都附 `X-Resp-Signature` ——
    错误响应同样要签。已知设备密钥时（`secret_hex` + `ts`）一律加签，
    否则设备端无法区分「网关伪造的错误」与「真实错误」。
    """
    text = user_msg or ERROR_TEXT.get(code, "出了点小状况，稍后再试")
    if detail:
        print(f"[api] 错误 {code}: {detail}")
    body = json.dumps(
        {"code": code, "msg": text, "payload": ""}, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    resp = Response(content=body, media_type="application/json")
    if secret_hex and ts:
        resp.headers["X-Resp-Signature"] = security.sign_response(ts, body, secret_hex)
    return resp


def ok_envelope() -> JSONResponse:
    """标准响应信封（无业务数据时 `payload` 为空串）。"""
    return JSONResponse({"code": CODE_OK, "msg": "ok", "payload": ""})


def encrypted_response(
    secret_hex: str, req_timestamp: str, data: Dict[str, Any], code: int = CODE_OK, msg: str = "ok"
) -> Response:
    """
    加密业务响应 + 响应签名。

    响应体 = `{"code":.., "msg":.., "payload":"<Base64(IV||CT)>"}`，
    响应头 `X-Resp-Signature = HMAC(secret, "REQ_TS\\nSHA256(resp_body)")`。
    """
    payload_b64 = security.encrypt_json(data, secret_hex)
    body = json.dumps(
        {"code": code, "msg": msg, "payload": payload_b64}, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    resp = Response(content=body, media_type="application/json")
    resp.headers["X-Resp-Signature"] = security.sign_response(req_timestamp, body, secret_hex)
    return resp


def _device_secret(device_id: str) -> Optional[str]:
    device = get_storage().get_device(device_id)
    if not device:
        return None
    return device.get("device_secret_hex", "")


def _authenticate(req: Request, raw_body: bytes) -> Tuple[str, str, Optional[JSONResponse]]:
    """
    设备端请求鉴权。返回 `(device_id, device_secret_hex, 错误响应)`。

    三项校验：设备存在 → 时间戳窗口 → 签名一致。
    """
    device_id = req.headers.get("X-Device-Id", "")
    timestamp = req.headers.get("X-Timestamp", "")
    signature = req.headers.get("X-Signature", "")
    if not device_id:
        return "", "", err(CODE_SIGN, "缺少设备标识")
    secret = _device_secret(device_id)
    if not secret:
        return "", "", err(CODE_SIGN, f"设备未配对：{device_id}")
    path = req.url.path  # 不含域名的纯路径
    try:
        security.verify_request(req.method, path, timestamp, raw_body, signature, secret)
    except security.SecurityError as exc:
        return "", "", err(CODE_SIGN, str(exc))
    return device_id, secret, None


def _decrypt_body(raw_body: bytes, secret_hex: str) -> Dict[str, Any]:
    """解析并解密请求信封 `{"payload": "<b64>"}`。"""
    try:
        envelope = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise security.SecurityError(f"请求体不是合法 JSON：{exc}", CODE_SIGN)
    payload_b64 = envelope.get("payload", "")
    if not payload_b64:
        raise security.SecurityError("缺少 payload", CODE_SIGN)
    return security.decrypt_json(payload_b64, secret_hex)


# ================================================================ 设备端 API
@app.get("/api/v1/ping")
async def api_ping():
    """标准响应信封自检。"""
    return ok_envelope()


# ---- 配对码（进程内，5 分钟有效） ----
_PAIR_CODES: Dict[str, float] = {}


def _make_pair_code() -> str:
    code = f"{secrets.randbelow(1_000_000):06d}"
    _PAIR_CODES[code] = time.time() + 300
    return code


@app.post("/api/v1/pair")
async def api_pair(req: Request):
    """
    首次 6 位配对码换取密钥。**明文 JSON 请求**（此时设备还没有密钥，无法签名）。

    配对码 5 分钟有效；成功后生成 32 字节 `device_secret`（64 位 Hex）并落盘。
    """
    try:
        data = json.loads((await req.body()).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return err(CODE_SIGN, "配对请求格式错误")

    device_id = data.get("device_id", "")
    pair_code = str(data.get("pair_code", ""))
    if not device_id:
        return err(CODE_SIGN, "缺少设备标识")

    expiry = _PAIR_CODES.get(pair_code)
    if expiry is None or expiry < time.time():
        _PAIR_CODES.pop(pair_code, None)
        return err(4002, "配对码无效或已过期")

    # 一次性使用
    _PAIR_CODES.pop(pair_code, None)

    secret_hex = secrets.token_hex(32)
    get_storage().upsert_device(device_id, secret_hex)
    return JSONResponse(
        {
            "code": CODE_OK,
            "msg": "ok",
            "payload": json.dumps(
                {"device_secret": secret_hex, "server_time": int(time.time())}, ensure_ascii=False
            ),
        }
    )


@app.post("/api/v1/sync/bootstrap")
async def api_bootstrap(req: Request):
    """心跳同步：天气、课表、新学科包与电子书元数据。"""
    raw = await req.body()
    device_id, secret, failure = _authenticate(req, raw)
    if failure:
        return failure
    try:
        payload = _decrypt_body(raw, secret)
    except security.SecurityError as exc:
        return err(CODE_SIGN, str(exc), secret, req.headers.get("X-Timestamp", ""))

    storage = get_storage()
    storage.touch_device(device_id)

    # 课表
    schedule = storage.read_schedule()
    schedule_download = None
    if schedule:
        schedule_download = {
            "rel_path": "schedules/latest.json",
            "updated_at": schedule.get("updated_at"),
            "token": security.issue_token(device_id, "schedules/latest.json", secret),
        }

    # 待更新学科包 / 电子书
    local_packs = set(payload.get("local_packs") or [])
    local_books = set(payload.get("local_books") or [])
    pending_packs = [
        {"pack_id": pid, "rel_path": f"packs/{pid}/meta.json"}
        for pid in storage.list_pack_ids()
        if pid not in local_packs
    ]
    pending_books = [
        {"book_id": bid, "rel_path": f"books/{bid}/{bid}.txt"}
        for bid in storage.list_book_ids()
        if bid not in local_books
    ]
    for item in pending_packs:
        item["token"] = security.issue_token(device_id, item["rel_path"], secret)
    for item in pending_books:
        item["token"] = security.issue_token(device_id, item["rel_path"], secret)

    weather = _weather_snapshot()

    data = {
        "weather": weather,
        "schedule_download": schedule_download,
        "pending_packs": pending_packs,
        "pending_books": pending_books,
        "effective_remaining_quota": get_router().effective_remaining(device_id),
        "server_time": int(time.time()),
    }
    return encrypted_response(secret, req.headers.get("X-Timestamp", ""), data)


@app.get("/api/v1/content/download")
async def api_download(req: Request, token: str = ""):
    """统一签名资产流式下载。响应头含 `Content-Length` 与 `X-File-Sha256`。"""
    device_id = req.headers.get("X-Device-Id", "")
    if not device_id:
        return err(CODE_SIGN, "缺少设备标识")
    secret = _device_secret(device_id)
    if not secret:
        return err(CODE_SIGN, f"设备未配对：{device_id}")
    try:
        rel_path = security.verify_token(token, device_id, secret)
    except security.SecurityError as exc:
        return err(CODE_SIGN, str(exc))

    storage = get_storage()
    target = (storage.root / rel_path).resolve()
    root = storage.root.resolve()
    # 路径白名单防穿越（verify_token 已查一层，这里再查一层实际落盘路径）
    if not str(target).startswith(str(root)) or not target.is_file():
        return err(CODE_SIGN, f"资源不存在或路径非法：{rel_path}")

    sha = hashlib.sha256(target.read_bytes()).hexdigest()
    size = target.stat().st_size

    def stream():
        with open(target, "rb") as f:
            while True:
                chunk = f.read(64 * 1024)
                if not chunk:
                    break
                yield chunk

    return StreamingResponse(
        stream(),
        media_type="application/octet-stream",
        headers={"Content-Length": str(size), "X-File-Sha256": sha},
    )


@app.post("/api/v1/study/solve")
async def api_solve(req: Request):
    """拍题导学（第三级：多模态视觉模型）。"""
    raw = await req.body()
    device_id, secret, failure = _authenticate(req, raw)
    if failure:
        return failure
    try:
        payload = _decrypt_body(raw, secret)
    except security.SecurityError as exc:
        return err(CODE_SIGN, str(exc))

    router = get_router()
    lock = router.lock_for(device_id)
    ts = req.headers.get("X-Timestamp", "")
    if lock.locked():
        return err(4290, "同设备并发请求被拒", secret, ts)
    async with lock:
        try:
            data = router.solve(device_id, payload)
        except RouterError as exc:
            return err(exc.code, str(exc), secret, ts, exc.user_msg)
        except SpeechError as exc:
            return err(exc.code, str(exc), secret, ts)
        except Exception as exc:  # 兜底：不让异常泄到设备端
            return err(CODE_UPSTREAM, f"拍题导学异常：{exc}", secret, ts)
    return encrypted_response(secret, ts, data)


@app.post("/api/v1/study/voice")
async def api_voice(req: Request):
    """语音指令 / 问答 / 卡片追问（三级分流）。"""
    raw = await req.body()
    device_id, secret, failure = _authenticate(req, raw)
    if failure:
        return failure
    try:
        payload = _decrypt_body(raw, secret)
    except security.SecurityError as exc:
        return err(CODE_SIGN, str(exc))

    router = get_router()
    lock = router.lock_for(device_id)
    ts = req.headers.get("X-Timestamp", "")
    if lock.locked():
        return err(4290, "同设备并发请求被拒", secret, ts)
    async with lock:
        try:
            data = router.voice(device_id, payload)
        except RouterError as exc:
            return err(exc.code, str(exc), secret, ts, exc.user_msg)
        except SpeechError as exc:
            return err(exc.code, str(exc), secret, ts)
        except Exception as exc:
            return err(CODE_UPSTREAM, f"语音指令异常：{exc}", secret, ts)
    return encrypted_response(secret, ts, data)


@app.get("/api/v1/ota/check")
async def api_ota_check(req: Request, device_id: str = "", current_ver: str = ""):
    """单通道固件升级检查（走标准设备签名 Header）。"""
    raw = await req.body()
    device_id = req.headers.get("X-Device-Id", "") or device_id
    timestamp = req.headers.get("X-Timestamp", "")
    signature = req.headers.get("X-Signature", "")
    secret = _device_secret(device_id) if device_id else None
    if not secret:
        return err(CODE_SIGN, "设备未配对")
    try:
        security.verify_request(req.method, req.url.path, timestamp, raw, signature, secret)
    except security.SecurityError as exc:
        return err(CODE_SIGN, str(exc))

    ota = _ota_manager()
    data = ota.check_update(device_id, current_ver)
    if data.get("has_update"):
        rel_path = f"ota/{data['latest_ver']}.bin"
        data["bin_url"] = (
            f"/api/v1/content/download?token={security.issue_token(device_id, rel_path, secret)}"
        )
    return encrypted_response(secret, timestamp, data)


# ================================================================ 天气缓存
def _weather_snapshot() -> Dict[str, Any]:
    """读 `storage/cache/weather.json`（TTL 2 小时）。失败返回空对象，不报错。"""
    from app.storage import read_json

    data = read_json(get_storage().weather_file, None)
    if not data:
        return {}
    ts = int(data.get("ts", 0))
    if time.time() - ts > 7200:
        return dict(data.get("weather", {}) or {})
    return dict(data.get("weather", {}) or {})


# ================================================================ Admin 会话
_SESSIONS: Dict[str, Dict[str, float]] = {}
SESSION_COOKIE = "sg_session"
SESSION_IDLE_TTL = 2 * 3600  # 2 小时无操作闲置过期
SESSION_ABS_TTL = 7 * 24 * 3600  # 7 天绝对过期


def _session_ok(req: Request) -> bool:
    token = req.cookies.get(SESSION_COOKIE, "")
    sess = _SESSIONS.get(token)
    if not sess:
        return False
    now = time.time()
    if now - sess["created"] > SESSION_ABS_TTL:
        _SESSIONS.pop(token, None)
        return False
    if now - sess["last"] > SESSION_IDLE_TTL:
        _SESSIONS.pop(token, None)
        return False
    sess["last"] = now
    return True


def _require_session(req: Request) -> Optional[JSONResponse]:
    if not _session_ok(req):
        return JSONResponse({"ok": False, "msg": "请先登录"}, status_code=401)
    return None


def _require_csrf(req: Request) -> Optional[JSONResponse]:
    """POST 状态修改必须带 `X-Requested-With: XMLHttpRequest`（05 §6.1 CSRF 防护）。"""
    if req.headers.get("X-Requested-With") != "XMLHttpRequest":
        return JSONResponse({"ok": False, "msg": "请求方式不对"}, status_code=403)
    return None


@app.post("/admin/api/login")
async def admin_login(req: Request):
    """管理员密码登录（密码来自 `.env` 的 `ADMIN_PASSWORD`）。"""
    try:
        data = json.loads((await req.body()).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JSONResponse({"ok": False, "msg": "请求格式不对"}, status_code=400)
    password = data.get("password", "")
    expected = os.environ.get("ADMIN_PASSWORD", "")
    if not expected:
        return JSONResponse({"ok": False, "msg": "服务还没配置好"}, status_code=500)
    if not hmac.compare_digest(password, expected):
        return JSONResponse({"ok": False, "msg": "密码不对"}, status_code=401)

    token = secrets.token_urlsafe(32)
    now = time.time()
    _SESSIONS[token] = {"created": now, "last": now}
    resp = JSONResponse({"ok": True, "msg": "登录成功"})
    resp.set_cookie(
        SESSION_COOKIE, token, httponly=True, samesite="strict", max_age=SESSION_ABS_TTL, path="/"
    )
    return resp


@app.post("/admin/api/logout")
async def admin_logout(req: Request):
    """退出登录（清除 Session Cookie）。"""
    token = req.cookies.get(SESSION_COOKIE, "")
    _SESSIONS.pop(token, None)
    resp = JSONResponse({"ok": True, "msg": "已退出"})
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


@app.get("/admin/api/status")
async def admin_status(req: Request):
    """网关状态、配额统计与已配对设备列表。"""
    if (fail := _require_session(req)):
        return fail
    storage = get_storage()
    devices = storage.read_devices()
    quota = storage.read_quota() or {}
    router = get_router()

    tts_dir = storage.tts_cache_dir
    tts_bytes = sum(f.stat().st_size for f in tts_dir.glob("*.wav")) if tts_dir.exists() else 0

    device_list = []
    for did, dev in devices.items():
        entry = quota.get(did) or {}
        device_list.append(
            {
                "device_id": did,
                "last_seen": dev.get("last_seen", ""),
                "used_count": entry.get("used_count", 0),
                "remaining": router.effective_remaining(did),
                "device_reported_remaining": entry.get("device_reported_remaining"),
            }
        )
    return JSONResponse(
        {
            "ok": True,
            "msg": "ok",
            "data": {
                "uptime_seconds": int(time.time() - _START_TIME),
                "device_count": len(devices),
                "tts_cache_mb": round(tts_bytes / 1024 / 1024, 2),
                "daily_quota": settings.daily_quota,
                "devices": device_list,
                "packs": storage.list_pack_ids(),
                "books": storage.list_book_ids(),
                "ota_versions": [v.get("version") for v in _ota_manager().list_versions()],
            },
        }
    )


@app.post("/admin/api/pair-code")
async def admin_pair_code(req: Request):
    """生成 6 位一次性配对码（5 分钟有效）。"""
    if (fail := _require_session(req)):
        return fail
    if (fail := _require_csrf(req)):
        return fail
    code = _make_pair_code()
    return JSONResponse({"ok": True, "msg": "ok", "data": {"pair_code": code, "expires_in": 300}})


@app.post("/admin/api/device/unbind")
async def admin_unbind(req: Request):
    """解绑并吊销指定设备密钥。"""
    if (fail := _require_session(req)):
        return fail
    if (fail := _require_csrf(req)):
        return fail
    data = await _admin_json(req)
    device_id = data.get("device_id", "")
    devices = get_storage().read_devices()
    if device_id in devices:
        devices.pop(device_id)
        get_storage().write_devices(devices)
    return JSONResponse({"ok": True, "msg": "已解绑"})


@app.post("/admin/api/device/rotate-secret")
async def admin_rotate_secret(req: Request):
    """轮换指定设备密钥（需生成新配对码重新绑定）。"""
    if (fail := _require_session(req)):
        return fail
    if (fail := _require_csrf(req)):
        return fail
    data = await _admin_json(req)
    device_id = data.get("device_id", "")
    devices = get_storage().read_devices()
    if device_id not in devices:
        return JSONResponse({"ok": False, "msg": "这台设备还没配对"}, status_code=404)
    # 吊销旧密钥 —— 设备需用新配对码重新绑定
    devices.pop(device_id)
    get_storage().write_devices(devices)
    return JSONResponse({"ok": True, "msg": "旧密钥已吊销，请重新配对"})


@app.post("/admin/api/quota/reset")
async def admin_quota_reset(req: Request):
    """手动重置指定设备当日导学配额（原子清零）。"""
    if (fail := _require_session(req)):
        return fail
    if (fail := _require_csrf(req)):
        return fail
    data = await _admin_json(req)
    device_id = data.get("device_id", "")
    storage = get_storage()
    ledger = storage.read_quota() or {}
    entry = ledger.get(device_id) or {}
    entry["date"] = time.strftime("%Y-%m-%d", time.localtime())
    entry["used_count"] = 0
    entry["charged_sessions"] = []
    ledger[device_id] = entry
    storage.write_quota(ledger)
    return JSONResponse({"ok": True, "msg": "配额已重置"})


@app.get("/admin/api/config")
async def admin_config_get(req: Request):
    """读取配置（**脱敏** —— 密钥永不回显完整明文）。"""
    if (fail := _require_session(req)):
        return fail
    return JSONResponse({"ok": True, "msg": "ok", "data": _masked_config()})


@app.post("/admin/api/config")
async def admin_config_post(req: Request):
    """热更新 `config.yaml`（API 地址 / 密钥 / 模型名 / 默认配额）。"""
    if (fail := _require_session(req)):
        return fail
    if (fail := _require_csrf(req)):
        return fail
    data = await _admin_json(req)
    if not data:
        return JSONResponse({"ok": False, "msg": "配置不能为空"}, status_code=400)

    raw = settings.raw
    # 只接受白名单字段，避免前端注入任意配置
    if "api_base_url" in data:
        raw.setdefault("ai", {})["base_url"] = str(data["api_base_url"])
    if "text_model" in data:
        raw.setdefault("ai", {})["text_model"] = str(data["text_model"])
    if "vision_model" in data:
        raw.setdefault("ai", {})["vision_model"] = str(data["vision_model"])
    if "daily_quota" in data:
        try:
            raw.setdefault("quota", {})["daily_limit"] = int(data["daily_quota"])
        except (TypeError, ValueError):
            return JSONResponse({"ok": False, "msg": "配额要填数字"}, status_code=400)
    if not settings.save(raw):
        return JSONResponse({"ok": False, "msg": "保存失败"}, status_code=500)
    return JSONResponse({"ok": True, "msg": "已保存并生效"})


@app.post("/admin/api/cache/tts/clear")
async def admin_clear_tts(req: Request):
    """清空本地 TTS 音频缓存。"""
    if (fail := _require_session(req)):
        return fail
    if (fail := _require_csrf(req)):
        return fail
    removed = 0
    for f in get_storage().tts_cache_dir.glob("*.wav"):
        try:
            f.unlink()
            removed += 1
        except OSError:
            pass
    return JSONResponse({"ok": True, "msg": f"已清理 {removed} 个缓存"})


@app.get("/admin/api/schedule/get")
async def admin_schedule_get(req: Request):
    """读取服务端保存的课表 JSON。"""
    if (fail := _require_session(req)):
        return fail
    return JSONResponse({"ok": True, "msg": "ok", "data": get_storage().read_schedule() or {}})


@app.post("/admin/api/schedule/parse")
async def admin_schedule_parse(req: Request):
    """
    AI 解析原始课表文本 → 结构化 `days` / `period_times`（**预览第一步**，不落盘）。

    视觉/文本模型尚未配置时如实报错，不返回假数据。
    """
    if (fail := _require_session(req)):
        return fail
    if (fail := _require_csrf(req)):
        return fail
    data = await _admin_json(req)
    text = str(data.get("text", "")).strip()
    if not text:
        return JSONResponse({"ok": False, "msg": "没有可解析的内容"}, status_code=400)

    router = get_router()
    system_prompt = (
        "你是课表结构化助手。请把输入的课表文本解析为严格 JSON："
        '{"days": {"周一": [{"period": 1, "subject": "语文", "teacher": ""}]}, '
        '"period_times": [{"period": 1, "start": "08:00", "end": "08:45"}]}。'
        "只输出 JSON，不要解释。"
    )
    try:
        raw = router._text_call(system_prompt, text)
    except RouterError as exc:
        return JSONResponse({"ok": False, "msg": f"解析失败：{exc}"}, status_code=502)
    try:
        parsed = json.loads(raw.strip().strip("`"))
    except json.JSONDecodeError:
        return JSONResponse({"ok": False, "msg": "解析结果不是合法 JSON"}, status_code=502)
    return JSONResponse({"ok": True, "msg": "ok", "data": parsed})


@app.post("/admin/api/schedule/save")
async def admin_schedule_save(req: Request):
    """确认保存正式课表（预览第二步）→ `storage/schedules/latest.json`。"""
    if (fail := _require_session(req)):
        return fail
    if (fail := _require_csrf(req)):
        return fail
    data = await _admin_json(req)
    if not data.get("days"):
        return JSONResponse({"ok": False, "msg": "课表内容不能为空"}, status_code=400)
    schedule = {
        "schema_version": 1,
        "updated_at": int(time.time()),
        "days": data.get("days", {}),
        "period_times": data.get("period_times", []),
        "weekend": data.get("weekend", [6, 7]),
        "holidays": data.get("holidays", []),
    }
    if not get_storage().write_schedule(schedule):
        return JSONResponse({"ok": False, "msg": "保存失败"}, status_code=500)
    return JSONResponse({"ok": True, "msg": "课表已保存，设备下次心跳自动拉取"})


@app.post("/admin/api/packs/preview")
async def admin_packs_preview(req: Request):
    """AI 拆解讲义为阶梯卡片（预览第一步，不打包）。"""
    if (fail := _require_session(req)):
        return fail
    if (fail := _require_csrf(req)):
        return fail
    data = await _admin_json(req)
    text = str(data.get("text", "")).strip()
    if not text:
        return JSONResponse({"ok": False, "msg": "没有可拆解的内容"}, status_code=400)

    router = get_router()
    system_prompt = (
        "你是讲义拆解助手。把输入讲义拆成阶梯教学卡片，输出严格 JSON 数组："
        '[{"id":"c001","q":"题干","h1":"第一层提示","h2":"第二层提示","ans":"答案"}]。'
        "只输出 JSON。"
    )
    try:
        raw = router._text_call(system_prompt, text)
    except RouterError as exc:
        return JSONResponse({"ok": False, "msg": f"拆解失败：{exc}"}, status_code=502)
    try:
        cards = json.loads(raw.strip().strip("`"))
    except json.JSONDecodeError:
        return JSONResponse({"ok": False, "msg": "拆解结果不是合法 JSON"}, status_code=502)
    if not isinstance(cards, list):
        return JSONResponse({"ok": False, "msg": "拆解结果格式不对"}, status_code=502)
    for idx, card in enumerate(cards):
        card.setdefault("id", f"c{idx + 1:03d}")
    return JSONResponse({"ok": True, "msg": "ok", "data": cards})


@app.post("/admin/api/packs/build")
async def admin_packs_build(req: Request):
    """
    加密打包学科包（预览第二步）。

    同时写明文 `source.json`（供语音追问上下文）；
    `device_id` 非空 → 用其 `device_secret[0:16]` 加密、`key_type="device"`；
    否则用 `STUDY_DEMO_KEY_16`、`key_type="demo"`。
    """
    if (fail := _require_session(req)):
        return fail
    if (fail := _require_csrf(req)):
        return fail
    data = await _admin_json(req)
    pack_id = str(data.get("pack_id", "")).strip()
    name = str(data.get("name", "")).strip() or pack_id
    device_id = str(data.get("device_id", "")).strip()
    if not pack_id:
        return JSONResponse({"ok": False, "msg": "学科包编号不能为空"}, status_code=400)

    # 卡片来源：前端微调后的列表，或原始文本
    raw_cards = data.get("cards")
    if isinstance(raw_cards, list) and raw_cards:
        cards = [
            {
                "id": c.get("id", f"c{i:04d}"),
                "question": c.get("q", c.get("question", "")),
                "answer": c.get("ans", c.get("answer", "")),
                "hint1": c.get("h1", c.get("hint1", "")),
                "hint2": c.get("h2", c.get("hint2", "")),
                "analysis": c.get("analysis", ""),
            }
            for i, c in enumerate(raw_cards)
        ]
    else:
        cards = parse_source_text(str(data.get("text", "")))
    if not cards:
        return JSONResponse({"ok": False, "msg": "没有可打包的卡片"}, status_code=400)

    if device_id:
        secret = _device_secret(device_id)
        if not secret:
            return JSONResponse({"ok": False, "msg": "这台设备还没配对"}, status_code=404)
        key_type, device_secret = "device", secret
    else:
        key_type, device_secret = "demo", None

    try:
        built = build_pack(
            pack_id=pack_id,
            name=name,
            cards=cards,
            key_type=key_type,
            device_secret_hex=device_secret,
        )
    except PackBuildError as exc:
        return JSONResponse({"ok": False, "msg": f"打包失败：{exc}"}, status_code=400)

    storage = get_storage()
    storage.write_pack_file(pack_id, "cards.enc", built["cards.enc"])
    storage.write_pack_file(pack_id, "index.idx", built["index.idx"])
    storage.write_pack_file(pack_id, "meta.json", built["meta.json"])
    storage.write_pack_file(pack_id, "source.json", built["source.json"])
    return JSONResponse({"ok": True, "msg": "打包完成", "data": built["meta"]})


@app.post("/admin/api/books/clean")
async def admin_books_clean(req: Request, file: UploadFile = File(...)):
    """纯本地清洗电子书（**零 AI 调用**）→ `storage/books/`。"""
    if (fail := _require_session(req)):
        return fail
    if (fail := _require_csrf(req)):
        return fail
    raw = await file.read()
    if not raw:
        return JSONResponse({"ok": False, "msg": "文件是空的"}, status_code=400)
    suffix = Path(file.filename or "").suffix.lower()
    book_id = Path(file.filename or "book").stem
    try:
        result = clean_book_data(raw, suffix, book_id, book_id)
    except BookCleanError as exc:
        return JSONResponse({"ok": False, "msg": str(exc)}, status_code=400)

    storage = get_storage()
    storage.write_book_file(result["book_id"], f"{result['book_id']}.txt", result["txt"])
    storage.write_book_file(result["book_id"], f"{result['book_id']}.toc", result["toc"])
    return JSONResponse({"ok": True, "msg": "清洗完成", "data": result["info"]})


@app.post("/admin/api/ota/upload")
async def admin_ota_upload(req: Request, file: UploadFile = File(...), version: str = Form("")):
    """上传新固件 `.bin`（校验版本号、大小 <= 3.9MB、首字节 0xE9、计算 SHA-256）。"""
    if (fail := _require_session(req)):
        return fail
    if (fail := _require_csrf(req)):
        return fail
    raw = await file.read()
    if not raw:
        return JSONResponse({"ok": False, "msg": "文件是空的"}, status_code=400)
    if not version:
        version = Path(file.filename or "").stem.replace("fw_", "")
    if not version or not all(c.isdigit() or c == "." for c in version):
        return JSONResponse({"ok": False, "msg": "版本号格式不对"}, status_code=400)

    max_bytes = int(float(settings.ota.get("max_size_mb", 3.9)) * 1024 * 1024)
    if len(raw) > max_bytes:
        return JSONResponse({"ok": False, "msg": f"固件太大（上限 {settings.ota.get('max_size_mb')}MB）"}, status_code=400)
    if raw[0] != 0xE9:
        return JSONResponse({"ok": False, "msg": "这不是有效的固件文件"}, status_code=400)

    storage = get_storage()
    target = storage.ota_dir / f"fw_{version}.bin"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)

    ota = _ota_manager()
    try:
        info = ota.publish(version, target, changelog=str(file.filename or ""))
    except OtaError as exc:
        return JSONResponse({"ok": False, "msg": f"固件校验失败：{exc}"}, status_code=400)

    # 保留最近 5 版
    keep = int(settings.ota.get("keep_versions", 5))
    versions = ota.load_manifest().get("versions") or {}
    ordered = sorted(versions.keys(), key=lambda v: _version_key(v), reverse=True)
    for old in ordered[keep:]:
        (storage.ota_dir / f"fw_{old}.bin").unlink(missing_ok=True)
        versions.pop(old, None)
    ota.save_manifest({"versions": versions, "latest": ota._pick_latest(versions)})
    return JSONResponse({"ok": True, "msg": "固件已发布", "data": info})


@app.post("/admin/api/ota/policy")
async def admin_ota_policy(req: Request):
    """修改分发策略或回滚历史版本。"""
    if (fail := _require_session(req)):
        return fail
    if (fail := _require_csrf(req)):
        return fail
    data = await _admin_json(req)
    strategy = str(data.get("strategy", "")).upper()
    if strategy not in ("PAUSED", "CANARY", "ALL"):
        return JSONResponse({"ok": False, "msg": "策略只能是 PAUSED / CANARY / ALL"}, status_code=400)
    raw = settings.raw
    raw.setdefault("ota", {})["strategy"] = strategy
    raw["ota"]["canary_devices"] = list(data.get("canary_devices") or [])
    settings.save(raw)

    ota = _ota_manager()
    if data.get("active_version"):
        manifest = ota.load_manifest()
        versions = manifest.get("versions") or {}
        if data["active_version"] in versions:
            manifest["latest"] = data["active_version"]
            ota.save_manifest(manifest)
    return JSONResponse({"ok": True, "msg": "策略已更新"})


# ================================================================ 辅助
_START_TIME = time.time()
_ota_mgr: Optional[OtaManager] = None


def _ota_manager() -> OtaManager:
    global _ota_mgr
    if _ota_mgr is None:
        magic_hex = str(settings.ota.get("magic", "e9"))
        try:
            magic = bytes.fromhex(magic_hex)
        except ValueError:
            magic = b"\xe9"
        _ota_mgr = OtaManager(get_storage().ota_dir, magic=magic)
    return _ota_mgr


def _version_key(v: str) -> tuple:
    out = []
    for p in str(v).lstrip("vV").split("."):
        out.append(int(p) if p.isdigit() else 0)
    return tuple(out)


async def _admin_json(req: Request) -> Dict[str, Any]:
    try:
        return json.loads((await req.body()).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}


def _masked_config() -> Dict[str, Any]:
    """脱敏配置 —— 密钥永不回显完整明文（05 §6.1）。"""
    raw = json.loads(json.dumps(settings.raw, ensure_ascii=False, default=str))

    def mask(section: Dict[str, Any], key: str) -> None:
        if section.get(key):
            section[key] = "sk-••••••••••••"

    for sec in ("ai", "speech"):
        node = raw.get(sec)
        if isinstance(node, dict):
            mask(node, "api_key")
            for sub in node.values():
                if isinstance(sub, dict):
                    mask(sub, "api_key")
    return raw


def clean_book_data(raw: bytes, suffix: str, book_id: str, title: str) -> Dict[str, Any]:
    """`book_cleaner.clean_book` 的内存版（管理台上传用，不落临时文件）。"""
    import tempfile

    from app.book_cleaner import _build_toc, clean_content

    text = clean_content(raw, suffix)
    if not text.strip():
        raise BookCleanError("这份文件没有可提取的文字（可能是扫描图片版），需要另行识别后才能入库")
    toc = _build_toc(text)
    toc_bytes = (
        json.dumps(
            {"schema_version": 1, "book_id": book_id, "title": title, "sections": toc},
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        + b"\n"
    )
    txt_bytes = text.encode("utf-8")
    return {
        "book_id": book_id,
        "txt": txt_bytes,
        "toc": toc_bytes,
        "info": {
            "book_id": book_id,
            "chars": len(text),
            "sections": len(toc),
            "txt_bytes": len(txt_bytes),
        },
    }


# ================================================================ 全局异常 & 静态页
@app.exception_handler(Exception)
async def global_exception_handler(req: Request, exc: Exception):
    """全局兜底 —— 异常不泄到设备端，只记服务端日志。"""
    print(f"[api] 未捕获异常 {req.url.path}: {exc}")
    return err(CODE_UPSTREAM, str(exc))


WEB_DIR = settings.web_dir
if WEB_DIR.exists():
    app.mount("/app", StaticFiles(directory=str(WEB_DIR), html=True), name="web")


@app.get("/")
async def index():
    """零构建单文件管理台。"""
    page = WEB_DIR / "index.html"
    if not page.exists():
        return JSONResponse({"code": CODE_OK, "msg": "web/index.html 未部署"}, status_code=404)
    return FileResponse(str(page))


@app.on_event("startup")
async def on_startup():
    get_storage().init()
    print(f"[gateway] 启动完成，storage 目录：{settings.storage_root}")
