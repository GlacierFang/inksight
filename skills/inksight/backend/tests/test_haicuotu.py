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
    # 调用点必须存在（尺寸变量名可以变，但必须走共享层的默认参数）
    assert "prepare_direct_art(src_img, " in source
    assert "downsample=" not in source
    assert "ink_gamma=" not in source


# ── 名称题签（2026-10-29）────────────────────────────────────
def test_name_is_stamped_into_art_band():
    """生物名必须烙在图心上缘的题签里。

    起因：名称原先放在版面正文，但图占满整个正文区（y=36..270），
    那些文字被推到页脚底下裁掉了 —— 屏上根本看不到名称。
    """
    from core import haicuotu

    assert haicuotu.LABEL_BAND_H > 0
    assert haicuotu.ART_PAINT_H + haicuotu.LABEL_BAND_H == haicuotu.ART_DISPLAY[1]

    from PIL import Image

    art = Image.new("RGB", (haicuotu.ART_DISPLAY[0], haicuotu.ART_PAINT_H), (255, 255, 255))
    stamped = haicuotu._stamp_name(art, "墨尾鲛姥", "第 1 则")

    assert stamped.size == (haicuotu.ART_DISPLAY[0], haicuotu.ART_DISPLAY[1])
    # 题签带上要有实黑像素（名称写进去了）
    band = stamped.crop((0, 0, haicuotu.ART_DISPLAY[0], haicuotu.LABEL_BAND_H))
    dark = sum(1 for px in band.getdata() if sum(px) < 120)
    assert dark > 100, f"题签带上没有黑字：{dark}"


def test_stamp_without_name_is_noop():
    """没名称时不能画空题签带 —— 那会白白吃掉 38px。"""
    from PIL import Image

    from core import haicuotu

    art = Image.new("RGB", (haicuotu.ART_DISPLAY[0], haicuotu.ART_PAINT_H), (255, 255, 255))
    assert haicuotu._stamp_name(art, "") is art


def test_prepare_art_file_stamps_name():
    """prepare_art_file 传了名称就要把题签烙上去。"""
    import tempfile

    from PIL import Image

    from core import haicuotu

    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "src.png"
        dst = Path(td) / "dst.png"
        Image.new("RGB", (400, 234), (255, 255, 255)).save(src)

        ok, reason = haicuotu.prepare_art_file(src, dst, name="墨尾鲛姥", index_label="第 1 则")
        assert ok, reason
        out = Image.open(dst)
        assert out.size == haicuotu.ART_DISPLAY
        band = out.crop((0, 0, 400, haicuotu.LABEL_BAND_H))
        dark = sum(1 for px in band.getdata() if sum(px) < 120)
        assert dark > 100, f"成品图题签上没有黑字：{dark}"


def test_does_not_touch_shared_layers():
    """海错图不得改动共享归色层 —— eink_snap 与其它 provider 必须原样。"""
    from core import eink_snap

    source = inspect.getsource(eink_snap.prepare_direct_art)
    assert "haicuotu" not in source
