"""
05 §2.2 `app/config.py`：config.yaml 加载与热重载。

铁律：模型、ASR、TTS、配额、OTA 策略**一律由此配置驱动，严禁代码硬编码厂商或模型名**。
热重载：文件 mtime 变化即自动重载，无需重启进程。
"""

import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

BASE_DIR = Path(__file__).resolve().parents[1]


class ConfigError(RuntimeError):
    """配置文件缺失或格式错误。"""


class Settings:
    """点号访问的配置对象，同时保留原始 dict 便于 Admin 台回写。"""

    def __init__(self, raw: Dict[str, Any], path: Path):
        self._raw = raw
        self.path = path
        self.reload()

    # ------------------------------------------------------------ 热重载
    def reload(self) -> None:
        with open(self.path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            raise ConfigError(f"配置文件格式错误（应为映射）：{self.path}")
        self._data = data
        self._server = data.get("server", {}) or {}
        self._ai = data.get("ai", {}) or {}
        self._speech = data.get("speech", {}) or {}
        self._quota = data.get("quota", {}) or {}
        self._ota = data.get("ota", {}) or {}
        self._admin = data.get("admin", {}) or {}

    @property
    def raw(self) -> Dict[str, Any]:
        return self._data

    # ------------------------------------------------------------ 分段访问
    @property
    def server(self) -> Dict[str, Any]:
        return self._server

    @property
    def ai(self) -> Dict[str, Any]:
        return self._ai

    @property
    def speech(self) -> Dict[str, Any]:
        return self._speech

    @property
    def quota(self) -> Dict[str, Any]:
        return self._quota

    @property
    def ota(self) -> Dict[str, Any]:
        return self._ota

    @property
    def admin(self) -> Dict[str, Any]:
        return self._admin

    # ------------------------------------------------------------ 派生路径
    @property
    def storage_root(self) -> Path:
        """storage/ 目录。相对路径按 BASE_DIR 解析，保证从任何 cwd 启动都一致。"""
        raw = Path(self._server.get("storage_dir", "storage"))
        return raw if raw.is_absolute() else (BASE_DIR / raw)

    @property
    def web_dir(self) -> Path:
        raw = Path(self._server.get("web_dir", "web"))
        return raw if raw.is_absolute() else (BASE_DIR / raw)

    @property
    def host(self) -> str:
        return self._server.get("host", "0.0.0.0")

    @property
    def port(self) -> int:
        return int(self._server.get("port", 18770))

    # ------------------------------------------------------------ 常用取值
    @property
    def daily_quota(self) -> int:
        return int(self._quota.get("daily_limit", 10))

    def env(self, name: str, default: str = "") -> str:
        """读环境变量（.env 已由部署脚本注入进程环境）。"""
        return os.environ.get(name, default)


class ConfigManager:
    """持有 Settings 并按 mtime 自动热重载。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._mtime = 0.0
        self._settings: Optional[Settings] = None

    def get(self) -> Settings:
        with self._lock:
            if self._settings is None:
                self._settings = Settings(self._read_yaml(), self.path)
                self._mtime = self._current_mtime()
            elif self._current_mtime() != self._mtime:
                try:
                    self._settings.reload()
                    self._mtime = self._current_mtime()
                    print(f"[config] 热重载生效：{self.path}")
                except (OSError, ConfigError, yaml.YAMLError) as exc:
                    # 热重载失败**保留旧配置继续服务**，不因配置写错导致网关起不来
                    print(f"[config] 热重载失败，沿用旧配置：{exc}")
            return self._settings

    def save(self, data: Dict[str, Any]) -> bool:
        """Admin 台回写配置（原子写 + 立即热重载）。"""
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
                f.flush()
                import os as _os

                _os.fsync(f.fileno())
            _os.replace(tmp, self.path)
            try:
                _os.chmod(self.path, 0o600)
            except OSError:
                pass
        except OSError as exc:
            print(f"[config] 保存失败：{exc}")
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
            return False
        with self._lock:
            self._mtime = self._current_mtime()
        return True

    def _read_yaml(self) -> Dict[str, Any]:
        if not self.path.exists():
            raise ConfigError(f"配置文件不存在：{self.path}")
        with open(self.path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _current_mtime(self) -> float:
        try:
            return self.path.stat().st_mtime
        except OSError:
            return 0.0


_manager: Optional[ConfigManager] = None


def init_settings(config_path: Optional[Path] = None) -> Settings:
    """进程启动时调用一次。之后 `settings` 是热重载代理。"""
    global _manager
    path = Path(config_path) if config_path else (BASE_DIR / "config.yaml")
    _manager = ConfigManager(path)
    return _manager.get()


class _SettingsProxy:
    """`settings.xxx` 语法糖 —— 每次取属性都走热重载检查。"""

    def _s(self) -> Settings:
        if _manager is None:
            init_settings()
        return _manager.get()

    def __getattr__(self, item: str) -> Any:
        return getattr(self._s(), item)


settings = _SettingsProxy()
