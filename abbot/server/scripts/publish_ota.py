#!/usr/bin/env python3
"""
把一份已编译好的固件发布进 OTA 仓库（生成 `storage/ota/manifest.json`）。

用法：
    cd server
    python3 scripts/publish_ota.py --bin /tmp/fw/firmware.bin \
        --version v1.0.0 --changelog "首次发布" [--force-update]

说明：
* **本机不编译固件**，固件产物由 GitHub Actions 编译后推到 `fw-bin` 分支；
  本脚本只做「搬运 + 登记」。
* 复用 `app.ota_manager.OtaManager.publish()`，不另造发布逻辑：
  魔数（首字节 0xE9）+ SHA-256 + 体积校验全在 `verify_firmware()` 里。
* `bootloader.bin` / `partitions.bin` 只作归档，不进 manifest（设备端不下载它们）。
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from app.ota_manager import OtaManager, OtaError  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="发布固件到 abbot OTA 仓库")
    ap.add_argument("--bin", required=True, help="firmware.bin 路径")
    ap.add_argument("--version", required=True, help="版本号，如 v1.0.0")
    ap.add_argument("--changelog", default="", help="更新说明")
    ap.add_argument("--force-update", action="store_true", help="强制升级（版本相同也下发）")
    ap.add_argument("--ota-dir", default="", help="OTA 目录，默认取服务端配置")
    ap.add_argument("--extra", default="", help="额外归档文件（逗号分隔），只存不登记")
    args = ap.parse_args()

    if args.ota_dir:
        ota_dir = Path(args.ota_dir).resolve()
    else:
        # 与服务端运行时保持一致：`get_storage().ota_dir`（= storage_root/ota）
        from app.storage import get_storage

        ota_dir = Path(get_storage().ota_dir).resolve()

    manager = OtaManager(ota_dir)
    src = Path(args.bin).resolve()
    if not src.exists():
        print(f"[publish] 找不到固件：{src}", file=sys.stderr)
        return 2

    check = manager.verify_firmware(src)
    if not check["ok"]:
        print(f"[publish] 固件校验失败：{check['reason']}", file=sys.stderr)
        return 3

    try:
        info = manager.publish(
            args.version,
            src,
            changelog=args.changelog,
            force_update=args.force_update,
        )
    except OtaError as exc:
        print(f"[publish] 发布失败：{exc}", file=sys.stderr)
        return 4

    # 附带产物只归档，不登记进 manifest
    for extra in [e for e in args.extra.split(",") if e.strip()]:
        ep = Path(extra.strip()).resolve()
        if not ep.exists():
            continue
        dst = ota_dir / ep.name
        dst.write_bytes(ep.read_bytes())
        print(f"[publish] 归档 {ep.name}（{dst.stat().st_size} 字节，不登记进清单）")

    print(json.dumps(info, ensure_ascii=False, indent=2))
    print(f"[publish] 清单：{manager.manifest_file}")
    print(f"[publish] 相对下载路径：ota/{info['file']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
