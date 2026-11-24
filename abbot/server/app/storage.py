"""
05 §2.2 storage/ 目录持久化层。

原子写协议（05 §4.2 强制）：写 `.tmp` → `flush()` → `os.fsync()` → `os.replace()`。
磁盘写满时捕获 `OSError`、删除残缺 `.tmp`，并保持进程内状态可用（服务不中断）。

铁律（05 §1）：本层是**存储层持久化状态**，不是网关进程的会话状态。
配额账本落在这里 —— 见 05 §4.2「无状态网关」与「服务端配额账本」不矛盾的澄清。
"""

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import settings


class StorageError(RuntimeError):
    """存储层不可用（磁盘满、权限错误等）。"""


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def atomic_write_bytes(path: Path, data: bytes, mode: int = 0o600) -> bool:
    """
    原子写二进制文件。返回 True 表示落盘成功。

    失败时**不抛异常打断业务**，而是清理残缺 `.tmp` 后返回 False ——
    05 §4.2 要求磁盘写满时保持服务可用（内存态继续维持）。
    """
    _ensure_dir(path.parent)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        try:
            os.chmod(path, mode)
        except OSError:
            pass
        return True
    except OSError as exc:
        # 磁盘写满 / 权限错误：清理残缺文件，保持进程内状态可用
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        print(f"[storage] 写入失败 {path}: {exc}")
        return False


def atomic_write_json(path: Path, obj: Any, mode: int = 0o600) -> bool:
    """原子写 JSON（UTF-8，不转义中文，便于人工排查）。"""
    payload = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
    return atomic_write_bytes(path, payload, mode=mode)


def read_json(path: Path, default: Any = None) -> Any:
    """
    读 JSON。文件不存在或损坏时返回 `default` 并记 Error 日志。

    注意：**不抛异常** —— 05 §4.2 规定账本损坏时按降级规则处理（配额归零重算），
    而不是让整个网关起不来。
    """
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[storage] 读取失败 {path}: {exc}")
        return default


class Storage:
    """storage/ 目录的单一入口。所有路径都从这里派生，避免各处硬编码相对路径。"""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.devices_file = self.root / "devices.json"
        self.quota_file = self.root / "quota.json"
        self.schedules_dir = self.root / "schedules"
        self.packs_dir = self.root / "packs"
        self.books_dir = self.root / "books"
        self.ota_dir = self.root / "ota"
        self.cache_dir = self.root / "cache"
        self.tts_cache_dir = self.cache_dir / "tts"
        self.weather_file = self.cache_dir / "weather.json"

    # ---------------------------------------------------------------- 初始化
    def init(self) -> None:
        """首次启动创建目录骨架并初始化空账本。"""
        for d in (
            self.root,
            self.schedules_dir,
            self.packs_dir,
            self.books_dir,
            self.ota_dir,
            self.cache_dir,
            self.tts_cache_dir,
        ):
            _ensure_dir(d)
        if not self.devices_file.exists():
            self.write_devices({})
        if not self.quota_file.exists():
            self.write_quota({})
        try:
            os.chmod(self.devices_file, 0o600)
            os.chmod(self.quota_file, 0o600)
        except OSError:
            pass

    # ---------------------------------------------------------------- 设备
    def read_devices(self) -> Dict[str, Any]:
        return read_json(self.devices_file, {}) or {}

    def write_devices(self, devices: Dict[str, Any]) -> bool:
        return atomic_write_json(self.devices_file, devices)

    def get_device(self, device_id: str) -> Optional[Dict[str, Any]]:
        return self.read_devices().get(device_id)

    def upsert_device(self, device_id: str, secret_hex: str) -> bool:
        devices = self.read_devices()
        devices[device_id] = {
            "device_id": device_id,
            "device_secret_hex": secret_hex,
            "last_seen": _now_iso(),
        }
        return self.write_devices(devices)

    def touch_device(self, device_id: str) -> None:
        devices = self.read_devices()
        if device_id in devices:
            devices[device_id]["last_seen"] = _now_iso()
            self.write_devices(devices)

    # ---------------------------------------------------------------- 配额
    def read_quota(self) -> Dict[str, Any]:
        """
        读配额账本。文件损坏时返回空账本 —— 由调用方按 05 §4.2 的降级规则
        （used_count 归零重算 + 告警）处理，**不得**在此用设备上报值顶替。
        """
        raw = read_json(self.quota_file, None)
        if raw is None:
            return {}
        return raw

    def write_quota(self, quota: Dict[str, Any]) -> bool:
        return atomic_write_json(self.quota_file, quota)

    # ---------------------------------------------------------------- 课表
    def write_schedule(self, schedule: Any) -> bool:
        return atomic_write_json(self.schedules_dir / "latest.json", schedule)

    def read_schedule(self) -> Any:
        return read_json(self.schedules_dir / "latest.json", None)

    # ---------------------------------------------------------------- 通用
    def pack_dir(self, pack_id: str) -> Path:
        return self.packs_dir / pack_id

    def write_pack_file(self, pack_id: str, name: str, data: bytes) -> bool:
        return atomic_write_bytes(self.pack_dir(pack_id) / name, data, mode=0o644)

    def write_book_file(self, book_id: str, name: str, data: bytes) -> bool:
        return atomic_write_bytes(self.books_dir / book_id / name, data, mode=0o644)

    def write_ota_file(self, name: str, data: bytes) -> bool:
        return atomic_write_bytes(self.ota_dir / name, data, mode=0o644)

    def tts_cache_path(self, cache_key: str) -> Path:
        return self.tts_cache_dir / f"{cache_key}.wav"

    def tts_cache_hit(self, cache_key: str) -> Optional[bytes]:
        p = self.tts_cache_path(cache_key)
        if p.exists() and p.stat().st_size > 0:
            return p.read_bytes()
        return None

    def store_tts_cache(self, cache_key: str, wav_bytes: bytes) -> bool:
        return atomic_write_bytes(self.tts_cache_path(cache_key), wav_bytes, mode=0o644)

    def list_pack_ids(self) -> List[str]:
        if not self.packs_dir.exists():
            return []
        return sorted(p.name for p in self.packs_dir.iterdir() if p.is_dir())

    def list_book_ids(self) -> List[str]:
        if not self.books_dir.exists():
            return []
        return sorted(p.name for p in self.books_dir.iterdir() if p.is_dir())


def _now_iso() -> str:
    from datetime import datetime, timezone, timedelta

    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).isoformat(timespec="seconds")


_storage: Optional[Storage] = None


def get_storage() -> Storage:
    """全局单例。`storage.root` 来自 config.yaml，测试可覆盖。"""
    global _storage
    if _storage is None:
        _storage = Storage(settings.storage_root)
        _storage.init()
    return _storage


def set_storage(storage: Storage) -> None:
    """供测试注入临时目录。"""
    global _storage
    _storage = storage
