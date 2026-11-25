#!/usr/bin/env python3
"""
OTA 通道自检：真调一次 `/api/v1/ota/check`，再真下载一次固件并比对 SHA-256。

用法：
    cd server
    python3 scripts/ota_selfcheck.py [--device esp32s3_test01] [--current-ver 1.0.0]
                                     [--base http://127.0.0.1:18843]

流程（与固件端走同一条路，不削弱鉴权）：
1. 从 `storage/devices.json` 取设备密钥；
2. 用 `app.security.sign_request` 给 `GET /api/v1/ota/check` 签名，带
   `X-Device-Id` / `X-Timestamp` / `X-Signature` 三个头；
3. 解密响应（`app.security.decrypt_json`）拿业务字段；
4. 逐字段与 `storage/ota/manifest.json` 对照；
5. 用返回的 `bin_url`（换成本地 base，但保留同一 token）下载固件，
   比对下载内容的 SHA-256 与清单声明一致，并核对响应头 `X-File-Sha256`。

退出码：0 = 全通过；非 0 = 有项不通过（逐条打印原因）。
"""

import argparse
import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from app import security  # noqa: E402
from app.storage import get_storage  # noqa: E402


def _get_device_secret(device_id: str) -> str:
    device = get_storage().get_device(device_id)
    if not device:
        raise SystemExit(f"设备未配对：{device_id}")
    return device["device_secret_hex"]


def _opener() -> urllib.request.OpenerDirector:
    """本机自检走回环地址，**必须绕开出口代理**（代理会挡 127.0.0.1 之外的路径）。"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _signed_get(url: str, path: str, device_id: str, secret: str) -> dict:
    ts = str(int(time.time()))
    sig = security.sign_request("GET", path, ts, b"", secret)
    req = urllib.request.Request(
        url,
        headers={
            "X-Device-Id": device_id,
            "X-Timestamp": ts,
            "X-Signature": sig,
        },
    )
    with _opener().open(req, timeout=30) as resp:
        body = resp.read()
        resp_sig = resp.headers.get("X-Resp-Signature", "")
        headers = dict(resp.headers)
    if not security.verify_response(ts, body, resp_sig, secret):
        raise SystemExit("响应签名校验不通过")
    env = json.loads(body.decode("utf-8"))
    payload = security.decrypt_json(env["payload"], secret)
    return {"code": env.get("code"), "msg": env.get("msg"), "payload": payload, "headers": headers}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="esp32s3_test01")
    ap.add_argument("--current-ver", default="1.0.0")
    ap.add_argument("--base", default="http://127.0.0.1:18843")
    ap.add_argument(
        "--expect-no-update",
        action="store_true",
        help="断言「无更新」（用设备当前版本号等于 latest 时）",
    )
    args = ap.parse_args()

    storage = get_storage()
    manifest = json.loads((storage.ota_dir / "manifest.json").read_text(encoding="utf-8"))
    secret = _get_device_secret(args.device)

    path = "/api/v1/ota/check"
    query = f"?device_id={args.device}&current_ver={args.current_ver}"
    url = f"{args.base}{path}{query}"
    res = _signed_get(url, path, args.device, secret)
    payload = res["payload"]

    print(f"[check] {url}")
    print(f"[check] 应答 = {json.dumps(payload, ensure_ascii=False, indent=2)}")

    fails = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"  [{'OK ' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
        if not ok:
            fails.append(name)

    latest = manifest.get("latest") or ""
    info = (manifest.get("versions") or {}).get(latest) or {}

    check("业务码为 0", res["code"] == 0, str(res["code"]))
    if args.expect_no_update:
        # 设备已是最新版本：应当没有更新，也就没有 bin_url
        check("has_update 为假", not bool(payload.get("has_update")),
              str(payload.get("has_update")))
        check("latest_ver 与清单一致", payload.get("latest_ver") == latest,
              f"应答={payload.get('latest_ver')} 清单={latest}")
        check("无 bin_url（不白下一份旧包）", not (payload.get("bin_url") or ""))
        print("\n[结果] " + ("全部通过" if not fails else f"未通过 {len(fails)} 项：{fails}"))
        return 0 if not fails else 1

    check("has_update 为真", bool(payload.get("has_update")))
    check("latest_ver 与清单一致", payload.get("latest_ver") == latest,
          f"应答={payload.get('latest_ver')} 清单={latest}")
    check("sha256 与清单一致", payload.get("sha256") == info.get("sha256"),
          f"应答={payload.get('sha256')} 清单={info.get('sha256')}")
    check("size 与清单一致", int(payload.get("size") or 0) == int(info.get("size") or 0),
          f"应答={payload.get('size')} 清单={info.get('size')}")
    check("changelog 非空", bool(payload.get("changelog")))

    bin_url = payload.get("bin_url") or ""
    check("bin_url 非空", bool(bin_url))
    if not bin_url:
        print("\n[结果] bin_url 为空，后续下载跳过")
        return 1

    parts = urlsplit(bin_url)
    check("bin_url 指向下载端点", parts.path.endswith("/api/v1/content/download"), parts.path)
    check("bin_url 带 token", "token=" in parts.query)

    # 换成本地 base 下载（token 与路径绑定，换 host 不影响校验），走同一套鉴权
    token = parts.query.split("token=", 1)[1]
    local_url = f"{args.base}/api/v1/content/download?token={token}"
    req = urllib.request.Request(local_url, headers={"X-Device-Id": args.device})
    with _opener().open(req, timeout=60) as resp:
        blob = resp.read()
        file_sha = resp.headers.get("X-File-Sha256", "")
    got = hashlib.sha256(blob).hexdigest()
    print(f"[download] {local_url}")
    print(f"[download] 下载 {len(blob)} 字节，X-File-Sha256={file_sha}")
    check("下载体积与清单一致", len(blob) == int(info.get("size") or 0), f"{len(blob)}")
    check("下载内容 sha256 与清单一致", got == info.get("sha256"), f"下载={got}")
    check("响应头 X-File-Sha256 与清单一致", file_sha == info.get("sha256"), file_sha)

    print("\n[结果] " + ("全部通过" if not fails else f"未通过 {len(fails)} 项：{fails}"))
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
