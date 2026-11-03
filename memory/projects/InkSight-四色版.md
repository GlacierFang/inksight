---
name: InkSight-四色版
description: 屏上四色（黑/白/金黄/朱红）的使用口径与 colors 参数的下传链路
updated: 2026-11-03
---
# InkSight · 四色版
<!-- project: path:/home/open/.openclaw/workspace -->

## 屏上四色

- 定义：`core/config.py:EINK_4COLOR_PALETTE` —— 黑 `#000000`、白 `#FFFFFF`、金黄 `#E8B000`、朱红 `#C80000`。
- 设备是 4 色屏：`core/history_render.py:DEVICE_COLORS = 4`。
- 2 色屏自动降级：`EINK_COLOR_AVAILABILITY[2]` 是空集，`ctx.color_index()` 找不到颜色名就落回墨黑。

## colors 参数下传链路（2026-11-03 打通）

固件 `GET /api/render?...&colors=N` → `RenderQuery.colors` → `api/shared.py:build_image(colors=)`
→ `core/pipeline.py:generate_and_render(colors=)` → `_generate_content_for_persona(colors=)`
→ `core/json_content.py:generate_json_mode_content(colors=)` → `common_args['colors']` → provider 的 `kwargs['colors']`。

**provider 之前拿不到 colors**：四色图按 2 色屏抖动，金黄会变成噪点。现在 provider 按 `colors` 选画法。

## 画法原则

- **原色平涂，不做渐变**：e-ink 四色走 Atkinson 抖动，中间色会抖成噪点。只用盘上原色填充才干净。
- 画成 PNG 再交给 `convert_image_block` 量化：用盘上原色时量化是恒等的，不会被糊。
- 图片文件名带色数后缀（`_c2_` / `_c4_`），换色数不会读到旧的错图。
- 朱红留给「日落」这类概念性强调；金黄留给「白昼/月光」。不要三种色同时上屏。

## 日昼与月相的四色改造

- 月相图（`render_moon_png`）：受光面金黄、背影墨黑、墨黑勾边。2 色屏受光面退回白纸面（天文图惯例）。
- 日轨弧（`render_day_arc_png`）：金色白昼带 + 墨黑弧线，弧高按真实昼长，黑点标当前时刻。只白天生成。
- 文字：日出金黄、日落朱红、月相名金黄；白昼时长和 LLM 那句话保持墨黑。

## 提交

- `ea91961` fix(inksight): 补回 generate_and_render 的 archive_screen 参数
  （HEAD 的 api/shared.py 已经在传 archive_screen，pipeline 没收，走 shared.build_image 会 TypeError）
- `0604dc5` feat(inksight): 日昼与月相四色版
- `d9a4dbb` feat(inksight): 日昼与月相支持自选城市
