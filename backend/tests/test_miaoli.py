"""狸行记（CUSTOM_MIAOLI）：喵狸的日漫一日。

极简：一次 LLM 写画面，一次生图出一张日漫图。屏上一句话都没有。
固定下来的是：角色设定、黑白日漫风格、版式（整版图占满，页脚只有署名）。
不固定的是：今天画什么、签名。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

MODE_SOURCE = BACKEND_DIR.parent / "modes-ours" / "miaoli.json"
MODE_DEPLOY = BACKEND_DIR / "core" / "modes" / "builtin" / "custom_miaoli.json"


# ── 模式文件 ────────────────────────────────────────────────────────────


def test_mode_json_validates():
    from core.mode_registry import _validate_mode_def_with_error

    for path in (MODE_SOURCE, MODE_DEPLOY):
        assert path.exists(), f"缺少模式文件：{path}"
        definition = json.loads(path.read_text(encoding="utf-8"))
        ok, err = _validate_mode_def_with_error(definition)
        assert ok, f"{path}: {err}"
        assert definition["mode_id"] == "CUSTOM_MIAOLI"
        assert definition["content"]["type"] == "computed"
        assert definition["content"]["provider"] == "miaoli"


def test_mode_source_and_deploy_match():
    assert MODE_SOURCE.read_text(encoding="utf-8") == MODE_DEPLOY.read_text(encoding="utf-8")


def test_registered_in_catalog():
    from core.mode_catalog import BUILTIN_CATALOG

    item = next(i for i in BUILTIN_CATALOG if i.mode_id == "CUSTOM_MIAOLI")
    assert item.category == "custom"
    assert item.zh.name == "狸行记"


def test_gallery_collects_miaoli():
    from core.gallery import modes

    ids = [m["mode_id"] for m in modes()]
    assert "CUSTOM_MIAOLI" in ids


def test_provider_dispatch_registered():
    from core.json_content import _generate_computed_content  # noqa: F401


# ── 版式：屏上只有图和页脚 ────────────────────────────────────────────


def test_screen_has_no_body_text():
    """出图的那条分支屏上一句话都没有 —— 标题和描述只进 prompt 和回顾页字段。

    没图时允许一句兜底提示（透屏的「未见电路」同理）。
    """
    definition = json.loads(MODE_SOURCE.read_text(encoding="utf-8"))
    body = definition["layout"]["body"]

    def _walk(nodes):
        kinds = set()
        for node in nodes:
            kinds.add(node.get("type"))
            for key in ("children", "body"):
                if isinstance(node.get(key), list):
                    kinds |= _walk(node[key])
            if node.get("type") == "conditional":
                for cond in node.get("conditions") or []:
                    kinds |= _walk(cond.get("children") or [])
        return kinds

    yes_branch = None
    for node in body:
        if node.get("type") != "conditional":
            continue
        for cond in node.get("conditions") or []:
            if cond.get("op") == "eq" and cond.get("value") == "yes":
                yes_branch = cond.get("children") or []

    assert yes_branch is not None, "找不到 has_image=yes 分支"
    kinds = _walk(yes_branch)
    assert "text" not in kinds, f"出图时屏上不该有文字块：{kinds}"
    assert "speech_bubble" not in kinds, f"出图时屏上不该有气泡：{kinds}"
    assert "image" in kinds


def test_footer_shows_name_and_signature():
    definition = json.loads(MODE_SOURCE.read_text(encoding="utf-8"))
    footer = definition["layout"]["footer"]
    assert footer["label"] == "狸行记"
    assert "{name}" in footer["attribution_template"]
    assert "{signature}" in footer["attribution_template"]


def test_status_bar_hidden():
    definition = json.loads(MODE_SOURCE.read_text(encoding="utf-8"))
    assert definition["layout"]["status_bar"].get("hidden") is True


# ── 解析 ──────────────────────────────────────────────────────────────


def test_parse_day():
    from core.miaoli import _parse_day

    raw = (
        "标题：檐上\n"
        "画面：喵狸蹲在旧粮店的檐砖上，风掀乱它的胡子\n"
        "签名：此路有我，概不收租。\n"
    )
    card = _parse_day(raw)
    assert card["title"] == "檐上"
    assert "旧粮店" in card["scene"]
    assert card["signature"] == "此路有我，概不收租。"


def test_parse_day_reads_single_scene():
    """一格单幅（冰河大人 2026-11-02 定的：双格效果太差，改一格）。"""
    from core.miaoli import _parse_day

    raw = (
        "标题：扑蝶\n"
        "画面：喵狸后腿一蹬扑向白蝴蝶，胡须被风拉直\n"
        "签名：来此一爪。\n"
    )
    card = _parse_day(raw)
    assert card["title"] == "扑蝶"
    assert "扑向白蝴蝶" in card["scene"]
    assert "panel1" not in card


def test_parse_day_requires_scene():
    from core.miaoli import _parse_day

    assert _parse_day("标题：只有标题") == {}


def test_signature_does_not_repeat_cat_name():
    """页脚是「喵狸·签名」——签名里再写一遍就成了「喵狸·喵狸来此一爪」。"""
    from core.miaoli import _parse_day, _strip_name

    assert _strip_name("喵狸来此一爪") == "来此一爪"
    assert _strip_name("此路有我，概不收租。") == "此路有我，概不收租。"

    card = _parse_day("画面：喵狸在破碗边捞鱼" + chr(10) + "签名：喵狸来此一爪")
    assert card["signature"] == "来此一爪"


def test_parse_day_defaults_signature():
    from core.miaoli import DEFAULT_SIGNATURE, _parse_day

    card = _parse_day("画面：喵狸在墙根下蹲着")
    assert card["signature"] == DEFAULT_SIGNATURE


# ── 提示词 ────────────────────────────────────────────────────────────


def test_image_prompt_keeps_scene_and_style():
    """提示词结构：前面是风格要求，后面是 LLM 写的动作。

    冰河大人 2026-11-02：「前面是风格要求：日漫风格，狸花猫，
    后面是 llm 生成的动作。」
    """
    from core.miaoli import STYLE_SPEC, _image_prompt

    prompt = _image_prompt("它蹲在旧粮店的檐砖上")
    assert STYLE_SPEC in prompt
    assert "它蹲在旧粮店的檐砖上" in prompt
    # 风格和主体必须在动作前面
    assert prompt.index(STYLE_SPEC) < prompt.index("它蹲在旧粮店的檐砖上")


def test_image_prompt_uses_screen_four_color_palette():
    """屏是四色：黑 / 白 / 金黄 #E8B000 / 朱红 #C80000。

    提示词必须把这四个色值写死给生图模型，不能让它自由发挥；
    也不能再说「不要颜色」—— 那是把没查过的假设当事实。

    浅阴影用金黄网点：屏上没有灰，灰网点会被抖成脏黄网纹。
    """
    from core.config import EINK_4COLOR_PALETTE
    from core.miaoli import _image_prompt

    prompt = _image_prompt("它蹲在旧粮店的檐砖上")
    assert "#E8B000" in prompt
    assert "#C80000" in prompt
    assert "#000000" in prompt
    assert "纯白纸面底" in prompt
    assert "不要颜色" not in prompt
    # 四色屏的物理色必须与调色板一致（扁平 int 列表，每色 3 个通道）
    pal = EINK_4COLOR_PALETTE
    assert tuple(pal[6:9]) == (232, 176, 0)   # 金黄
    assert tuple(pal[9:12]) == (200, 0, 0)    # 朱红


def test_image_prompt_has_no_negative_constraint_block():
    """提示词不做否定清单 —— 照小景 core/xiaojing.py 的教训。

    小景文件里写着：否定约束越多模型越僵，还容易把不该出现的元素招来；
    它自己原来的 400 词约束块就是这么废的。所以只留两句必要的话
    （不要第五种色相 / 不要文字印章），其余全部改成正面描述。
    """
    from core.miaoli import STYLE_SPEC, _image_prompt

    prompt = _image_prompt("某个场景")
    for banned in (
        "不要动物肖像插画", "不要素描", "不要3D渲染", "不要柔焦",
        "不要水彩", "不要儿童绘本", "不要静态坐姿摆拍", "不要两格姿势雷同",
        "不要版权字样", "不要橘色", "不要棕色", "不要橙色",
        "不要第五种色相",
    ):
        assert banned not in prompt, f"不该有否定清单条目：{banned}"
    # 风格段一句话就够（小景：写长了模型会僵住，出图变成照着条文填空）
    assert len(STYLE_SPEC) < 20


def test_image_prompt_has_no_invented_character_spec():
    """不给猫编设定 —— 冰河大人 2026-11-02：

    「一只狸花猫：额头一个 M 字纹……我有说这些么？没有，
    你限制的越多，出图的效果越差！」
    """
    from core.miaoli import _image_prompt

    prompt = _image_prompt("某个场景")
    for invented in (
        "M 字纹", "狸花条纹", "尾巴梢", "耳尖", "铜钱", "项圈",
        "额头", "杏仁眼", "尖下颌", "细腰长腿",
    ):
        assert invented not in prompt, f"不该写猫的设定：{invented}"


def test_image_prompt_only_gives_style():
    """提示词只给风格要求，不限定猫的造型，不写动态表达。

    冰河大人 2026-11-02：「你只需要给风格要求不就好了，
    折腾这么多有的没的意义在哪里？」
    """
    from core.miaoli import _image_prompt

    prompt = _image_prompt("喵狸后腿一蹬扑向白蝴蝶，胡须被风拉直")
    # 不写动态表达
    for banned in ("速度线", "集中线", "斜切构图", "飞溅", "残影", "扑出去"):
        assert banned not in prompt, f"不该写动态表达：{banned}"
    # 不限定造型
    for banned in ("杏仁眼", "尖下颌", "细腰长腿", "Q 版", "Q版", "造型"):
        assert banned not in prompt, f"不该限定猫的造型：{banned}"
    # 不提分格
    assert "两格" not in prompt
    assert "分格" not in prompt
    assert "等宽" not in prompt
    # 画面描述原样保留
    assert "喵狸后腿一蹬扑向白蝴蝶，胡须被风拉直" in prompt


def test_style_spec_is_user_wording():
    """风格段用冰河大人的原话 —— 不自己换词。"""
    from core.miaoli import STYLE_SPEC

    assert STYLE_SPEC == "日漫风格，狸花猫"


def test_image_prompt_retry_only_appends_one_sentence():
    """重试就是原提示词加一句 —— 照小景，不另写一份平行约束。"""
    from core.miaoli import RETRY_PROMPT, _image_prompt

    base = _image_prompt("某个场景")
    retry = _image_prompt("某个场景", retry=True)
    assert retry == base + RETRY_PROMPT
    assert "不要有任何文字" in RETRY_PROMPT


def test_day_prompt_asks_for_one_moving_moment():
    """LLM 写一个动作瞬间，且画面要有速度。"""
    from core.miaoli import DAY_PROMPT

    assert "画面" in DAY_PROMPT
    assert "画面要有速度" in DAY_PROMPT
    assert "第一格" not in DAY_PROMPT
    assert "第二格" not in DAY_PROMPT


def test_day_prompt_uses_third_person():
    """画面用「它」—— 提示词开头已经有「狸花猫」，主语不能打架。"""
    from core.miaoli import DAY_PROMPT

    assert "它" in DAY_PROMPT
    assert "不要用「我」" in DAY_PROMPT


def test_image_prompt_forbids_text_and_bubbles():
    """屏上的字由程序排；模型画的字在屏上会糊成一团。"""
    from core.miaoli import _image_prompt

    prompt = _image_prompt("某个场景")
    assert "不要任何文字" in prompt
    assert "印章" in prompt
    assert "水印" in prompt


def test_day_prompt_has_no_real_system_data():
    """喵狸不读天气、日程等真实数据 —— 它是虚拟生命体，不是仪表盘。"""
    from core.miaoli import DAY_PROMPT

    assert "天气" not in DAY_PROMPT
    assert "电量" not in DAY_PROMPT
    assert "日程" not in DAY_PROMPT


def test_cat_name_is_fixed():
    """名字是喵狸，固定的 —— 不让模型自封。"""
    from core.miaoli import CAT_NAME

    assert CAT_NAME == "喵狸"


# ── 内容装配 ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_build_content_history_day_is_not_invented(monkeypatch, tmp_path):
    """历史日没生成过就是没生成过 —— 如实标「历史缺失」，不补生成。"""
    from core import miaoli

    monkeypatch.setattr(miaoli, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(miaoli, "STATE_FILE", tmp_path / "state" / "state.json")

    async def _boom(*args, **kwargs):
        raise AssertionError("历史日不该触发生成")

    monkeypatch.setattr(miaoli, "generate_today_art", _boom)

    content = await miaoli.build_miaoli_content(
        fallback={},
        date_ctx={"year": 2026, "month": 11, "day": 1},
    )
    assert content["has_image"] == "no"
    assert content["title"] == "历史缺失"


@pytest.mark.asyncio
async def test_generate_today_records_day(monkeypatch, tmp_path):
    from core import miaoli

    monkeypatch.setattr(miaoli, "ART_DIR", tmp_path / "art")
    monkeypatch.setattr(miaoli, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(miaoli, "STATE_FILE", tmp_path / "state" / "state.json")
    monkeypatch.setattr(miaoli, "LOCK_FILE", tmp_path / "state" / "generate.lock")

    async def _fake_day(**kwargs):
        return {
            "title": "檐上",
            "scene": "喵狸蹲在旧粮店的檐砖上，风掀乱它的胡子",
            "signature": "此路有我，概不收租。",
        }

    async def _fake_art(dest, scene, **kwargs):
        from PIL import Image

        dest.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", miaoli.ART_DISPLAY, (255, 255, 255)).save(dest)
        return True, "fake"

    monkeypatch.setattr(miaoli, "_llm_day", _fake_day)
    monkeypatch.setattr(miaoli, "_generate_art", _fake_art)

    record = await miaoli.generate_today_art("2026-11-02")
    assert record is not None
    assert record["title"] == "檐上"
    assert record["art_url"].startswith("/static/miaoli/")
    assert record["has_image"] == "yes"

    state = json.loads((tmp_path / "state" / "state.json").read_text(encoding="utf-8"))
    assert state["days"]["2026-11-02"]["title"] == "檐上"


@pytest.mark.asyncio
async def test_build_content_today_uses_record(monkeypatch, tmp_path):
    from core import miaoli

    monkeypatch.setattr(miaoli, "ART_DIR", tmp_path / "art")
    monkeypatch.setattr(miaoli, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(miaoli, "STATE_FILE", tmp_path / "state" / "state.json")

    async def _fake_art(date_key, **kwargs):
        return {
            "date": date_key,
            "title": "檐上",
            "scene": "喵狸蹲在旧粮店的檐砖上",
            "signature": "此路有我，概不收租。",
            "art_url": "/static/miaoli/miaoli_20261102.png",
            "has_image": "yes",
        }

    monkeypatch.setattr(miaoli, "generate_today_art", _fake_art)

    content = await miaoli.build_miaoli_content(
        fallback={},
        date_ctx={"year": 2026, "month": 11, "day": 2},
    )
    assert content["title"] == "檐上"
    assert "旧粮店" in content["scene"]
    assert content["signature"] == "此路有我，概不收租。"
    assert content["name"] == "喵狸"
    assert content["has_image"] == "yes"
