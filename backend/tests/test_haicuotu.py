"""「海错图」模式（CUSTOM_HAICUOTU）测试。

覆盖：
  - 模式 JSON：源文件/部署副本合法且一致，catalog 与 registry 已注册；
  - provider 接线：computed 通道能派发到 haicuotu；
  - 条目解析：LLM 输出按「字段：值」逐行取，缺字段用兜底；
  - 生图提示词：四色盘 + 卡通造型 + 留白画水都在，禁止第五色相；
  - 隔离红线：prepare_direct_art 用默认参数（不改共享层），躺平巴拉不受影响。
"""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

MODE_SOURCE = BACKEND_DIR.parent / "modes-ours" / "haicuotu.json"
MODE_DEPLOY = BACKEND_DIR / "core" / "modes" / "builtin" / "custom_haicuotu.json"

CONTENT_FIELDS = {
    "index_label", "name", "alias", "size", "origin",
    "feature", "colophon", "credit", "art_url", "has_image",
}


def _mode() -> dict:
    return json.loads(MODE_DEPLOY.read_text(encoding="utf-8"))


# ── 模式文件 ────────────────────────────────────────────────
def test_mode_json_validates():
    from core.mode_registry import _validate_mode_def_with_error

    for path in (MODE_SOURCE, MODE_DEPLOY):
        assert path.exists(), f"缺少模式文件：{path}"
        definition = json.loads(path.read_text(encoding="utf-8"))
        ok, err = _validate_mode_def_with_error(definition)
        assert ok, f"{path}: {err}"
        assert definition["mode_id"] == "CUSTOM_HAICUOTU"
        assert definition["content"]["type"] == "computed"
        assert definition["content"]["provider"] == "haicuotu"


def test_mode_source_and_deploy_match():
    assert MODE_SOURCE.read_text(encoding="utf-8") == MODE_DEPLOY.read_text(encoding="utf-8")


def test_registered_in_catalog():
    from core.mode_catalog import BUILTIN_CATALOG

    item = next(i for i in BUILTIN_CATALOG if i.mode_id == "CUSTOM_HAICUOTU")
    assert item.zh.name == "海错图"


def test_registered_in_mode_registry():
    from core.mode_registry import get_registry

    assert "CUSTOM_HAICUOTU" in get_registry().get_supported_ids()


def test_fallback_covers_every_field():
    fallback = _mode()["content"]["fallback"]
    assert set(fallback) == CONTENT_FIELDS
    runtime_urls = {"art_url"}
    for field, value in fallback.items():
        if field in runtime_urls:
            assert value == "", f"{field} 兜底应当为空"
            continue
        assert isinstance(value, str), f"{field} 兜底必须是字符串"


def test_provider_is_wired():
    """computed 通道必须能派发到 haicuotu provider。"""
    import core.json_content as jc

    source = inspect.getsource(jc._generate_computed_content)
    assert 'provider == "haicuotu"' in source
    assert "build_haicuotu_content" in source


def test_art_display_matches_mode_json():
    """图心显示尺寸必须与模式 JSON 的 image 块一致，否则渲染器会二次缩放。"""
    from core.haicuotu import ART_DISPLAY

    body = _mode()["layout"]["body"]
    img = body[0]["conditions"][0]["children"][0]
    assert (img["width"], img["height"]) == ART_DISPLAY


# ── 条目解析 ────────────────────────────────────────────────
def test_parse_entry_reads_labelled_lines():
    from core.haicuotu import _parse_entry

    raw = (
        "名：墨尾鲛姥\n"
        "异名：鲛婆\n"
        "特征：圆头无须，尾如墨条\n"
        "多余的一句话\n"
    )
    entry = _parse_entry(raw)
    assert entry["名"] == "墨尾鲛姥"
    assert entry["异名"] == "鲛婆"
    assert "多余的一句话" not in entry


def test_parse_entry_ignores_malformed():
    from core.haicuotu import _parse_entry

    assert _parse_entry("") == {}
    assert _parse_entry("没有冒号的行\n") == {}


def test_entry_keys_cover_prompt_fields():
    from core.haicuotu import ENTRY_KEYS, FALLBACK_ENTRY

    for key in ("名", "异名", "尺寸", "产地", "特征", "题识", "画"):
        assert key in ENTRY_KEYS
    for key in ("名", "异名", "尺寸", "产地", "特征", "题识", "画"):
        assert FALLBACK_ENTRY.get(key)


def test_biol_prompt_allows_fiction_and_forbids_repeat():
    """图鉴所载皆虚构 —— 提示词要写明虚构，并带上防重复的最近目录。"""
    from core.haicuotu import BIOL_PROMPT

    assert "虚构" in BIOL_PROMPT
    assert "不要重样" in BIOL_PROMPT
    assert "{recent}" in BIOL_PROMPT
    assert "{index}" in BIOL_PROMPT


# ── 生图提示词 ──────────────────────────────────────────────
def test_palette_spec_forbids_fifth_hue():
    from core.haicuotu import PALETTE_SPEC

    assert "#000000" in PALETTE_SPEC
    assert "#E8B000" in PALETTE_SPEC
    assert "#C80000" in PALETTE_SPEC
    assert "不要出现第五种色相" in PALETTE_SPEC
    assert "不要写实材质纹理" in PALETTE_SPEC


def test_cartoon_spec_is_present():
    """卡通造型是本模式的关键 —— 少了它就会画回写实，掉进中间调的坑。"""
    from core.haicuotu import CARTOON_SPEC, PALETTE_SPEC_RETRY

    for spec in (CARTOON_SPEC, PALETTE_SPEC_RETRY):
        assert "卡通" in spec
    assert "大色块" in CARTOON_SPEC
    assert "Q 版" in CARTOON_SPEC
    assert "不要写实" in CARTOON_SPEC


def test_water_is_left_blank_not_colored():
    """水用留白表示 —— 这是冰河大人 2026-10-29 定的路子。

    屏上没有蓝/青，真蓝在四色里没有归属，会被 Atkinson 抖成脏噪点。
    """
    from core.haicuotu import WATER_SPEC

    assert "留白" in WATER_SPEC
    assert "不要给水填色" in WATER_SPEC
    assert "波纹" in WATER_SPEC


def test_image_prompt_includes_scene_and_palette():
    from core.haicuotu import _image_prompt

    prompt = _image_prompt("一条圆润的鱼形海兽跃出水面")
    assert "鱼形海兽" in prompt
    assert "#E8B000" in prompt
    assert "卡通" in prompt
    assert "留白" in prompt


def test_image_prompt_without_desc_still_has_palette():
    from core.haicuotu import _image_prompt

    prompt = _image_prompt("")
    assert "#E8B000" in prompt
    assert "卡通" in prompt
    assert "留白" in prompt

    retry = _image_prompt("", retry=True)
    assert "连续不断" in retry


# ── 隔离红线 ────────────────────────────────────────────────
def test_prepare_art_uses_shared_default_params():
    """必须用 prepare_direct_art 默认参数 —— 改共享层会动到躺平巴拉。"""
    from core import haicuotu

    source = inspect.getsource(haicuotu.prepare_art_file)
    assert "prepare_direct_art(src_img, dw, dh)" in source
    assert "downsample=" not in source
    assert "ink_gamma=" not in source


def test_does_not_touch_shared_layers():
    """海错图不得改动共享归色层 —— eink_snap 与其它 provider 必须原样。"""
    from core import eink_snap

    source = inspect.getsource(eink_snap.prepare_direct_art)
    assert "haicuotu" not in source
