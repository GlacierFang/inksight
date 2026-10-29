"""「大宋梦影」模式（CUSTOM_SONGMENG）测试。

覆盖：
  - 模式 JSON：源文件/部署副本合法且一致，catalog 与 registry 已注册；
  - provider 接线：computed 通道能派发到 songmeng；
  - 梦景解析：LLM 输出按「字段：值」逐行取，缺字段用兜底；
  - 生图提示词：四色盘 + 卡通造型要求都在，禁止第五色相；
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

MODE_SOURCE = BACKEND_DIR.parent / "modes-ours" / "songmeng.json"
MODE_DEPLOY = BACKEND_DIR / "core" / "modes" / "builtin" / "custom_songmeng.json"

CONTENT_FIELDS = {
    "scene", "colophon", "credit", "art_url", "has_image",
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
        assert definition["mode_id"] == "CUSTOM_SONGMENG"
        assert definition["content"]["type"] == "computed"
        assert definition["content"]["provider"] == "songmeng"


def test_mode_source_and_deploy_match():
    assert MODE_SOURCE.read_text(encoding="utf-8") == MODE_DEPLOY.read_text(encoding="utf-8")


def test_registered_in_catalog():
    from core.mode_catalog import BUILTIN_CATALOG

    item = next(i for i in BUILTIN_CATALOG if i.mode_id == "CUSTOM_SONGMENG")
    assert item.category == "custom"
    assert item.zh.name == "大宋梦影"


def test_registered_in_mode_registry():
    from core.mode_registry import get_registry

    assert "CUSTOM_SONGMENG" in get_registry().get_supported_ids()


def test_fallback_covers_every_field():
    fallback = _mode()["content"]["fallback"]
    assert set(fallback) == CONTENT_FIELDS
    # art_url 是运行期才有的 URL，兜底为空是对的
    runtime_urls = {"art_url"}
    for field, value in fallback.items():
        if field in runtime_urls:
            assert value == "", f"{field} 兜底应当为空"
            continue
        assert isinstance(value, str), f"{field} 兜底必须是字符串"


def test_provider_is_wired():
    """computed 通道必须能派发到 songmeng provider。"""
    import core.json_content as jc

    source = inspect.getsource(jc._generate_computed_content)
    assert 'provider == "songmeng"' in source
    assert "build_songmeng_content" in source


def test_art_display_matches_mode_json():
    """图心显示尺寸必须与模式 JSON 的 image 块一致，否则渲染器会二次缩放。"""
    from core.songmeng import ART_DISPLAY

    body = _mode()["layout"]["body"]
    img = body[0]["conditions"][0]["children"][0]
    assert (img["width"], img["height"]) == ART_DISPLAY


# ── 梦景解析 ────────────────────────────────────────────────
def test_parse_dream_reads_labelled_lines():
    from core.songmeng import _parse_dream

    raw = (
        "场景：夜市\n"
        "题记：灯下有人把一盏盏灯笼点亮\n"
        "画面：宋代夜市，灯笼连成一条街\n"
        "多余的一句话\n"
    )
    dream = _parse_dream(raw)
    assert dream["场景"] == "夜市"
    assert dream["题记"] == "灯下有人把一盏盏灯笼点亮"
    assert "多余的一句话" not in dream


def test_parse_dream_ignores_malformed():
    from core.songmeng import _parse_dream

    assert _parse_dream("") == {}
    assert _parse_dream("没有冒号的行\n") == {}


def test_dream_keys_cover_prompt_fields():
    from core.songmeng import DREAM_KEYS, FALLBACK_DREAM

    for key in ("场景", "题记", "画面"):
        assert key in DREAM_KEYS
    assert FALLBACK_DREAM.get("场景")
    assert FALLBACK_DREAM.get("题记")
    assert FALLBACK_DREAM.get("画面")


def test_dream_prompt_allows_fiction():
    """梦影即虚构 —— 提示词不应强制对得上史实，但也不该出现现代物。"""
    from core.songmeng import DREAM_PROMPT

    assert "梦" in DREAM_PROMPT
    assert "现代" in DREAM_PROMPT


# ── 生图提示词 ──────────────────────────────────────────────
def test_palette_spec_forbids_fifth_hue():
    from core.songmeng import PALETTE_SPEC

    assert "#000000" in PALETTE_SPEC
    assert "#E8B000" in PALETTE_SPEC
    assert "#C80000" in PALETTE_SPEC
    assert "不要出现第五种色相" in PALETTE_SPEC
    assert "不要写实材质纹理" in PALETTE_SPEC


def test_cartoon_spec_is_present():
    """卡通造型是本模式与躺平巴拉的关键差别 —— 少了它就会画回写实。"""
    from core.songmeng import CARTOON_SPEC, PALETTE_SPEC_RETRY

    for spec in (CARTOON_SPEC, PALETTE_SPEC_RETRY):
        assert "卡通" in spec
    assert "大色块" in CARTOON_SPEC
    assert "Q 版" in CARTOON_SPEC
    assert "不要写实" in CARTOON_SPEC


def test_image_prompt_includes_scene_and_palette():
    from core.songmeng import _image_prompt

    prompt = _image_prompt("宋代夜市，灯笼连成一条街")
    assert "宋代夜市" in prompt
    assert "#E8B000" in prompt
    assert "卡通" in prompt

    retry = _image_prompt("宋代夜市", retry=True)
    assert "连续不断" in retry


def test_image_prompt_without_scene_still_has_palette():
    from core.songmeng import _image_prompt

    prompt = _image_prompt("")
    assert "#E8B000" in prompt
    assert "卡通" in prompt


# ── 隔离红线 ────────────────────────────────────────────────
def test_prepare_art_uses_shared_default_params():
    """必须用 prepare_direct_art 默认参数 —— 改共享层会动到躺平巴拉。"""
    from core import songmeng

    source = inspect.getsource(songmeng.prepare_art_file)
    assert "prepare_direct_art(src_img, dw, dh)" in source
    assert "downsample=" not in source
    assert "ink_gamma=" not in source


def test_does_not_touch_shared_palette_helpers():
    """本模式不得调用共享层的四色归色/分类函数（那些是写实图的路子）。"""
    from core import songmeng

    source = inspect.getsource(songmeng)
    for banned in ("snap_illustration", "snap_line_art", "snap_to_palette"):
        assert banned not in source


def test_tangping_provider_untouched():
    """躺平巴拉已冻结 —— 它的路径必须与本模式无关。"""
    from core import tangping

    source = inspect.getsource(tangping._prepare_card)
    assert "prepare_direct_art(img, dw, dh)" in source
    assert "songmeng" not in source


# ── 内容构建 ────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_build_content_fills_from_state(monkeypatch, tmp_path):
    from core import songmeng

    monkeypatch.setattr(songmeng, "STATE_DIR", tmp_path)
    monkeypatch.setattr(songmeng, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(songmeng, "ART_DIR", tmp_path)

    state = {
        "date": "2026-10-29",
        "today": {
            "date": "2026-10-29",
            "scene": "斗茶",
            "colophon": "一盏茶起，满室烟气",
            "art_url": "/static/songmeng/dream_20261029.png",
            "has_image": "yes",
        },
    }
    songmeng._write_state(state)

    content = await songmeng.build_songmeng_content()
    assert content["scene"] == "斗茶"
    assert content["colophon"] == "一盏茶起，满室烟气"
    assert content["has_image"] == "yes"
    assert content["credit"] == "斗茶 · 一盏茶起，满室烟气"
    assert content["art_url"] == "/static/songmeng/dream_20261029.png"


@pytest.mark.asyncio
async def test_build_content_applies_fallback(monkeypatch, tmp_path):
    from core import songmeng

    monkeypatch.setattr(songmeng, "STATE_DIR", tmp_path)
    monkeypatch.setattr(songmeng, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(songmeng, "ART_DIR", tmp_path)
    monkeypatch.setattr(songmeng, "_generate_once", _async_none)

    content = await songmeng.build_songmeng_content(
        fallback={"scene": "夜市", "colophon": "保底", "art_url": "", "has_image": "no"}
    )
    assert content["scene"] == "夜市"
    assert content["colophon"] == "保底"


async def _async_none(*args, **kwargs):
    return None
