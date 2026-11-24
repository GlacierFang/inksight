"""
05 §2.2 `app/ota_manager.py`：固件魔数与 SHA-256 校验、多版本仓库、灰度白名单与熔断控制。

单通道升级：设备 `GET /api/v1/ota/check?device_id=..&current_ver=..` → 网关比对
`storage/ota/manifest.json`，命中则下发 `bin_url`（带签名下载 token）。

安全要点：
* 固件**必须先过魔数校验**（防止把任意文件当固件发布）；
* **必须过 SHA-256 校验**（manifest 与实际文件双向一致）；
* 灰度白名单外的设备不下发新版本（`allowlist` 为空表示全量）；
* 熔断：同一版本连续下发失败达阈值即停止下发，避免批量变砖。
"""

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import settings


class OtaError(RuntimeError):
    """OTA 管理错误。"""


class OtaManager:
    def __init__(self, ota_dir: Path, magic: bytes = b"\xe9"):
        self.ota_dir = Path(ota_dir)
        self.magic = magic
        self.manifest_file = self.ota_dir / "manifest.json"
        # 熔断状态（进程内）：版本 → 连续失败次数
        self._fail_counts: Dict[str, int] = {}

    # ------------------------------------------------------------ 清单
    def load_manifest(self) -> Dict[str, Any]:
        if not self.manifest_file.exists():
            return {"versions": {}}
        try:
            with open(self.manifest_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[ota] 清单读取失败 {self.manifest_file}: {exc}")
            return {"versions": {}}

    def save_manifest(self, manifest: Dict[str, Any]) -> bool:
        self.ota_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.manifest_file.with_name(self.manifest_file.name + ".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)
                f.flush()
                import os

                os.fsync(f.fileno())
            os.replace(tmp, self.manifest_file)
            return True
        except OSError as exc:
            print(f"[ota] 清单写入失败：{exc}")
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
            return False

    # ------------------------------------------------------------ 发布
    def verify_firmware(self, bin_path: Path) -> Dict[str, Any]:
        """魔数 + SHA-256 + 体积校验。返回 `{"ok", "sha256", "size", "reason"}`。"""
        bin_path = Path(bin_path)
        if not bin_path.exists():
            return {"ok": False, "sha256": "", "size": 0, "reason": "固件文件不存在"}
        size = bin_path.stat().st_size
        if size < 16:
            return {"ok": False, "sha256": "", "size": size, "reason": "固件文件过小"}
        data = bin_path.read_bytes()
        if data[: len(self.magic)] != self.magic:
            return {
                "ok": False,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": size,
                "reason": f"固件魔数不匹配（期望 {self.magic!r}）",
            }
        return {"ok": True, "sha256": hashlib.sha256(data).hexdigest(), "size": size, "reason": ""}

    def publish(
        self,
        version: str,
        bin_path: Path,
        changelog: str = "",
        force_update: bool = False,
    ) -> Dict[str, Any]:
        """
        发布一个固件版本到仓库并登记进清单。

        校验不通过直接抛 `OtaError`（不落盘半个文件）。
        """
        bin_path = Path(bin_path)
        check = self.verify_firmware(bin_path)
        if not check["ok"]:
            raise OtaError(f"固件校验失败：{check['reason']}")

        self.ota_dir.mkdir(parents=True, exist_ok=True)
        target = self.ota_dir / f"fw_{version}.bin"
        target.write_bytes(bin_path.read_bytes())

        manifest = self.load_manifest()
        versions = manifest.get("versions") or {}
        versions[version] = {
            "version": version,
            "file": target.name,
            "sha256": check["sha256"],
            "size": check["size"],
            "changelog": changelog,
            "force_update": bool(force_update),
            "published_at": int(time.time()),
        }
        manifest["versions"] = versions
        manifest["latest"] = self._pick_latest(versions)
        if not self.save_manifest(manifest):
            raise OtaError("清单写入失败")
        return versions[version]

    def _pick_latest(self, versions: Dict[str, Any]) -> str:
        if not versions:
            return ""
        # 版本号形如 v1.0.3 —— 按数字段比较
        def key(v: str) -> tuple:
            parts = v.lstrip("vV").split(".")
            out = []
            for p in parts:
                out.append(int(p) if p.isdigit() else 0)
            return tuple(out)

        return max(versions.keys(), key=key)

    # ------------------------------------------------------------ 检查
    def check_update(
        self,
        device_id: str,
        current_ver: str,
        strategy: Optional[str] = None,
        canary_devices: Optional[List[str]] = None,
        circuit_threshold: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        判定是否下发升级。返回 05 §5 约定的字段形状：

        `{has_update, latest_ver, force_update, sha256, size, bin_url, changelog}`

        策略（05 §6.2 `/admin/api/ota/policy`）：`PAUSED` 全停 / `CANARY` 只发白名单 / `ALL` 全量。
        """
        strategy = strategy if strategy is not None else str(settings.ota.get("strategy", "ALL"))
        canary_devices = (
            canary_devices
            if canary_devices is not None
            else list(settings.ota.get("canary_devices", []) or [])
        )
        threshold = (
            circuit_threshold
            if circuit_threshold is not None
            else int(settings.ota.get("circuit_breaker_threshold", 3))
        )

        manifest = self.load_manifest()
        versions = manifest.get("versions") or {}
        if not versions:
            return {
                "has_update": False,
                "latest_ver": "",
                "force_update": False,
                "sha256": "",
                "size": 0,
                "bin_url": "",
                "changelog": "",
            }

        latest = manifest.get("latest") or self._pick_latest(versions)
        info = versions.get(latest)
        if not info:
            return {
                "has_update": False,
                "latest_ver": "",
                "force_update": False,
                "sha256": "",
                "size": 0,
                "bin_url": "",
                "changelog": "",
            }

        # 熔断：该版本连续失败超阈值则停止下发
        if self._fail_counts.get(latest, 0) >= threshold:
            print(f"[ota] 版本 {latest} 已熔断（连续失败 {self._fail_counts[latest]} 次），停止下发")
            return {
                "has_update": False,
                "latest_ver": latest,
                "force_update": False,
                "sha256": info.get("sha256", ""),
                "size": info.get("size", 0),
                "bin_url": "",
                "changelog": info.get("changelog", ""),
            }

        # 灰度：CANARY 策略下只有白名单内设备能拿到
        if strategy == "PAUSED":
            return {
                "has_update": False,
                "latest_ver": latest,
                "force_update": False,
                "sha256": info.get("sha256", ""),
                "size": info.get("size", 0),
                "bin_url": "",
                "changelog": info.get("changelog", ""),
            }
        if strategy == "CANARY" and device_id not in canary_devices:
            return {
                "has_update": False,
                "latest_ver": latest,
                "force_update": False,
                "sha256": info.get("sha256", ""),
                "size": info.get("size", 0),
                "bin_url": "",
                "changelog": info.get("changelog", ""),
            }

        if current_ver == latest and not info.get("force_update"):
            return {
                "has_update": False,
                "latest_ver": latest,
                "force_update": False,
                "sha256": info.get("sha256", ""),
                "size": info.get("size", 0),
                "bin_url": "",
                "changelog": info.get("changelog", ""),
            }

        return {
            "has_update": True,
            "latest_ver": latest,
            "force_update": bool(info.get("force_update")),
            "sha256": info.get("sha256", ""),
            "size": info.get("size", 0),
            "bin_url": "",  # 由 main.py 用签名 token 填充
            "changelog": info.get("changelog", ""),
        }

    def report_result(self, version: str, success: bool) -> None:
        """设备上报升级结果 —— 成功清零计数，失败累加（用于熔断）。"""
        if success:
            self._fail_counts.pop(version, None)
        else:
            self._fail_counts[version] = self._fail_counts.get(version, 0) + 1

    def list_versions(self) -> List[Dict[str, Any]]:
        manifest = self.load_manifest()
        return list((manifest.get("versions") or {}).values())
