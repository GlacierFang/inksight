"""树莓派状态 provider（computed: pi_status）。

渲染时直接读本机，不装 smartctl、不加定时器、不写状态文件。
五项指标：CPU 温度、CPU 占用率、内存占用率、SSD 剩余空间、开机时长。
读不到的指标直接不出现，绝不填假值。
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

THERMAL_ZONE = "/sys/class/thermal/thermal_zone0/temp"
MEMINFO = "/proc/meminfo"
UPTIME = "/proc/uptime"


def _read_int(path: str) -> int | None:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


def _cpu_temp() -> str | None:
    raw = _read_int(THERMAL_ZONE)
    if raw is None or raw <= 0:
        return None
    return f"{round(raw / 1000):d}°C"


def _cpu_usage() -> str | None:
    """用 1 分钟平均负载折算成占用率（htop 同样的算法）。"""
    try:
        load1 = os.getloadavg()[0]
    except OSError:
        return None
    cores = os.cpu_count() or 1
    return f"{min(100, round(load1 / cores * 100)):d}%"


def _mem_usage() -> str | None:
    total = avail = None
    try:
        with open(MEMINFO, "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1])
                elif line.startswith("MemAvailable:"):
                    avail = int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    if not total or avail is None:
        return None
    return f"{round((total - avail) / total * 100):d}%"


def _disk_free() -> str | None:
    try:
        st = os.statvfs("/")
    except OSError:
        return None
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    if total <= 0:
        return None
    gb = 1024 ** 3
    return f"{round(free / gb):d}G / {round(total / gb):d}G"


def _uptime() -> str | None:
    try:
        with open(UPTIME, "r", encoding="utf-8") as fh:
            raw = float(fh.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None
    days, rem = divmod(int(raw), 86400)
    hours = rem // 3600
    if days:
        return f"{days}天{hours}小时"
    minutes = rem // 60
    if hours:
        return f"{hours}小时{minutes}分"
    return f"{minutes}分"


def build_pi_status_content(fallback: dict | None = None) -> dict:
    """构造树莓派状态内容。"""
    metrics: list[dict[str, str]] = []
    for label, value in (
        ("CPU 温度", _cpu_temp()),
        ("CPU 占用", _cpu_usage()),
        ("内存占用", _mem_usage()),
        ("SSD 剩余", _disk_free()),
        ("开机时长", _uptime()),
    ):
        if value:
            metrics.append({"label": label, "value": value})

    content: dict[str, Any] = {
        "title": "树莓派",
        "metrics": metrics,
        "updated_at": datetime.now().strftime("%H:%M"),
    }
    if fallback:
        merged = dict(fallback)
        merged.update({k: v for k, v in content.items() if v not in ("", [], None)})
        return merged
    return content
