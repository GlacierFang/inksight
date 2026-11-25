#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
天气链路主机侧测试（不需要真实网络）。

覆盖：
  1. 缓存文件损坏 → 温和降级（返回空对象，不抛错）
  2. 城市为空 → 温和降级（不抛错、不写脏配置）
  3. 城市不匹配 → 不把别的城市的天气糊弄给设备
  4. 原子写结构与 `_weather_snapshot()` 既有读法兼容（weather / updated_at / ts）
  5. 抓取失败 → 旧缓存原样保留
  6. 设备上报城市 → 落 `storage/settings.json`

两种跑法：
    pytest tests/test_weather.py
    python3 tests/test_weather.py
"""

import json
import os
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import weather as weather_mod  # noqa: E402
from app.storage import Storage  # noqa: E402


def _make_storage(tmpdir):
    """造一个指向临时目录的 Storage，并让 weather 模块用它。"""
    store = Storage(Path(tmpdir))
    store.init()
    original = weather_mod.get_storage
    weather_mod.get_storage = lambda: store
    return store, original


def _restore(original):
    weather_mod.get_storage = original


def _write_raw(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------- 用例
def test_corrupt_cache_degrades():
    """缓存文件损坏 → 返回空对象，不抛错。"""
    with tempfile.TemporaryDirectory() as tmp:
        store, original = _make_storage(tmp)
        try:
            _write_raw(store.weather_file, "{ this is not json ")
            assert weather_mod.read_weather_snapshot() == {}, "损坏缓存应降级为空"
            assert weather_mod.read_weather_snapshot("dev1") == {}, "损坏缓存应降级为空"
        finally:
            _restore(original)


def test_missing_cache_degrades():
    """缓存文件不存在 → 返回空对象。"""
    with tempfile.TemporaryDirectory() as tmp:
        store, original = _make_storage(tmp)
        try:
            assert not store.weather_file.exists()
            assert weather_mod.read_weather_snapshot() == {}
        finally:
            _restore(original)


def test_empty_city_degrades():
    """城市为空 → 不抛错、不写脏配置。"""
    with tempfile.TemporaryDirectory() as tmp:
        store, original = _make_storage(tmp)
        try:
            assert weather_mod.set_city("") is False, "空城市不应写盘"
            assert weather_mod.set_city("   ") is False, "空白城市不应写盘"
            assert not store.settings_file.exists(), "空城市不应产生 settings.json"
            # 配置里也没有城市时，解析结果退到默认值，不炸
            assert weather_mod.resolve_city() != ""
            assert weather_mod.read_weather_snapshot() == {}
        finally:
            _restore(original)


def test_city_mismatch_returns_empty():
    """缓存是别的城市的天气 → 不给设备，返回空对象。"""
    with tempfile.TemporaryDirectory() as tmp:
        store, original = _make_storage(tmp)
        try:
            weather_mod.write_cache({"temp": 21, "text": "晴"}, "Beijing")
            weather_mod.set_city("Shanghai", "dev1")
            assert weather_mod.read_weather_snapshot("dev1") == {}, "城市不符应返回空"
            # 服务端共享城市也跟着变了 → 未单独设城市的设备同样拿不到旧城市的天气
            assert weather_mod.read_weather_snapshot("dev2") == {}, "共享城市不符也应返回空"
        finally:
            _restore(original)


def test_cache_write_shape_compatible():
    """原子写结构与既有读法兼容：weather{temp,text} + updated_at + ts。"""
    with tempfile.TemporaryDirectory() as tmp:
        store, original = _make_storage(tmp)
        try:
            assert weather_mod.write_cache({"temp": 7, "text": "多云"}, "Beijing") is True
            data = json.loads(store.weather_file.read_text(encoding="utf-8"))
            assert data["weather"] == {"temp": 7, "text": "多云"}
            assert isinstance(data["updated_at"], int) and data["updated_at"] > 0
            assert data["ts"] == data["updated_at"], "ts 兼容字段应与 updated_at 同值"
            assert data["city"] == "Beijing"
            assert weather_mod.read_weather_snapshot() == {"temp": 7, "text": "多云"}
        finally:
            _restore(original)


def test_fetch_failure_keeps_old_cache():
    """抓取失败 → 旧缓存原样保留（不写空、不删）。"""
    with tempfile.TemporaryDirectory() as tmp:
        store, original = _make_storage(tmp)
        try:
            weather_mod.write_cache({"temp": 5, "text": "阴"}, "Beijing")
            before = store.weather_file.read_text(encoding="utf-8")
            assert asyncio.run(weather_mod.fetch_weather("")) is None, "空城市不该发出请求"
            after = store.weather_file.read_text(encoding="utf-8")
            assert before == after, "抓取失败不应动缓存"
            assert weather_mod.read_weather_snapshot() == {"temp": 5, "text": "阴"}
        finally:
            _restore(original)


def test_stale_cache_still_served():
    """抓取失败时仍返回上一次缓存（同城市、即使超过 TTL 也不清空）。"""
    with tempfile.TemporaryDirectory() as tmp:
        store, original = _make_storage(tmp)
        try:
            weather_mod.write_cache({"temp": 5, "text": "阴"}, "Beijing")
            data = json.loads(store.weather_file.read_text(encoding="utf-8"))
            data["ts"] = 1
            data["updated_at"] = 1
            store.weather_file.write_text(json.dumps(data), encoding="utf-8")
            assert weather_mod.cache_is_fresh("Beijing") is False, "过期应判为不新鲜"
            assert weather_mod.read_weather_snapshot() == {"temp": 5, "text": "阴"}, "过期仍应返回上一次缓存"
        finally:
            _restore(original)


def test_set_city_persists():
    """设备上报城市 → 落 storage/settings.json（不进 .env / 不进 config.yaml）。"""
    with tempfile.TemporaryDirectory() as tmp:
        store, original = _make_storage(tmp)
        try:
            assert weather_mod.set_city("Shanghai", "dev1") is True
            data = json.loads(store.settings_file.read_text(encoding="utf-8"))
            assert data["city"] == "Shanghai"
            assert data["device_cities"]["dev1"] == "Shanghai"
            assert weather_mod.resolve_city("dev1") == "Shanghai"
            assert weather_mod.resolve_city() == "Shanghai"
        finally:
            _restore(original)


def test_cache_freshness():
    """新鲜度判定：城市一致且未过 TTL 才算新鲜。"""
    with tempfile.TemporaryDirectory() as tmp:
        store, original = _make_storage(tmp)
        try:
            weather_mod.write_cache({"temp": 5, "text": "阴"}, "Beijing")
            assert weather_mod.cache_is_fresh("Beijing") is True
            assert weather_mod.cache_is_fresh("Shanghai") is False, "城市不同应判为不新鲜"
        finally:
            _restore(original)


# ---------------------------------------------------------------- 独立跑
if __name__ == "__main__":
    cases = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for case in cases:
        try:
            case()
            print(f"[ok]   {case.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"[FAIL] {case.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"[FAIL] {case.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n天气链路测试：{len(cases) - failed}/{len(cases)} 通过")
    sys.exit(1 if failed else 0)
