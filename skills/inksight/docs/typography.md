# 排版与字号：位图字体的量化陷阱

适用于 `modes-ours/*.json` + `backend/core/modes/builtin/custom_*.json` 的排版修改。

## 核心事实：请求字号 ≠ 屏上字号

后端默认 `INKSIGHT_FONT_ENGINE=bitmap`，走 `backend/fonts/bitmap/` 下的 PCF 位图字体。
位图只有固定档位，**没有你要的字号**。`load_font()` 会就近挑一个最接近的档位，
于是配置文件里的 `font_size` 和屏上实际字面高不是一回事。

实测（NotoSerifSC-Regular，400×300 屏，scale=1）：

| 配置 font_size | 实际加载 | 字面高 |
|---|---|---|
| 12 | Regular-9.pcf | 11 |
| 13 | Regular-10.pcf | 12 |
| 14 | Regular-13.pcf | 13 |
| 15 | Regular-11.pcf | 14 |
| 16 / 17 / 18 / 19 | Regular-12.pcf | 16 |
| 20+ | Regular.ttf（抗锯齿 TTF） | 19+ |

位图档位全集只有 **9 / 10 / 11 / 12 / 13** 五档。

## 后果

1. **两个相邻请求值会塌成同一级。** 配 16 和配 18 屏上一样大 —— 以为的"层级差"不存在。
2. **想要两档相邻差，就不能配相邻值。** 例如想让 16 和 14 不同，得配 16 和 13 或 12。
3. **超过 12 就跳到 TTF。** TTF 是抗锯齿的，落在 400×300 上会发灰，
   而且和旁边的位图正文不是一套渲染 —— 同一屏里混两套引擎。
   `Regular-12.pcf`（字面高 16）是位图能达到的最大正文尺寸。
4. **正文用位图、标题用 TTF 是当前唯一可行解**（标题 26 走 TTF，正文 16 走位图）。

## 排版检查清单

改字号前先算实际档位，别拿配置值算高度：

- [ ] `load_font(key, size)` 拿到 font，读 `font.path` 看落到了哪个 .pcf
- [ ] `font.getbbox('国')` 读真实字面高
- [ ] `wrap_text(text, font, 可用宽)` 算真实折行数，不要用字数估算
- [ ] 行距按**字面高**算，不是按配置字号：`line_height` - 字面高 = 实际行缝
      （face 16 配 line_height 26 → 行缝 10px；配 24 → 行缝只有 8px，屏上挤成一坨）
- [ ] 标题是 TTF，配 `margin_bottom` 要够：字面高 23 的 TTF 标题，
      margin_bottom=2 会让副标题墨迹从 y=64 起而标题到 y=69 —— 叠 5px

## 渲染器支持的布局键

`backend/core/json_renderer.py` 已实现：

- `min_height`：块的实际占位高度下限（逻辑单位 × scale）
- `reserve_line_height`：文本块按整行高占位，避免折行后末行孤字撞到下一块的墨迹
- `body_align: "center"`：正文区垂直居中，不再全贴顶（底部留白会摊开）

> `mode_schema.json` 目前没有声明 `min_height` / `reserve_line_height`，
> 但 schema 允许附加属性，`test_mode_json_validates` 正常通过。

## 验证方式

不要靠肉眼看 PIL 输出下结论。全量渲染所有站点，量墨迹边界：

```python
bb = font.getbbox(text)
ink_top = y + bb[1]
ink_bot = y + bb[3]
```

四项必须全部为 0：

- 叠字：后一块 `ink_top` < 前一块 `ink_bot`
- 越界：正文墨迹 `ink_bot` > 262（页脚线之上）
- `+N more`：列表项被截断
- 省略号：引文溢出 `max_lines`

**只看一个样张会漏。** 汴京梦华 83 站里，叠字只出现在 1 站（导语折行后
末尾孤字行撞第一条名录），样张恰好没抽到它。
