# InkSight 版面调整记录（2026-10-31）

## 背景
冰河大人看画廊页说「海错图图片展示的少」，躺平巴拉/丹青谱/大宋梦影同样。

## 我跑偏的三次（记录下来别再犯）
1. 理解成「图片数量少」→ 去查 catalog 条目数
2. 理解成「网页卡片里的图显示得小」→ 去查原画有没有、能不能取到
3. 提出改栅格列数（3 列 → 2 列）→ 方向完全错

真实需求是：**屏上那张 400x300 整屏渲染图里，图片框只占了一半高度，
文字区末行到页脚横线之间一大片死白。**

## 量出来的基准（可复用）

正文区可用高度 = 画幅高 - 状态栏(36) - 页脚(30)
  400x300 → 正文区 234px
页脚横线在 y=270。死白 = 270 - (正文末行 y + 1)。

## 改了什么
| 模式 | 删掉的重复文字 | 图高 | 死白 |
|---|---|---|---|
| 海错图 | 题识（页脚有 colophon） | 176 → 196 | 9 → 8px |
| 躺平巴拉 | place + 呓语（页脚 credit） | 192 → 230 | 51 → 4px |
| 大宋梦影 | scene + 题记（页脚 credit） | 192 → 230 | 52 → 4px |
| 丹青谱 | 题跋（页脚 colophon） | 176 不动 | 26 → 22px |

丹青谱图不动的原因：原画天生 392x176（`core/danqing.py` 的 `ART_DISPLAY`），
生图 1152:512 同比例。加高就必然放大 15%（糊）或横向裁 13%。
冰河大人拍板选 (a) 方案。

## 关键联动
**删文字和加图高是一对。** 只删文字 → 腾出的空间变成新的死白。
只加图高 → 文字被挤出正文区。

## 只改有图分支
`has_image=yes` 分支才删 —— 无图时 `credit` 为空、页脚没内容，文字必须保留。

## 提交与同步
两份 JSON 必须同步：
- `modes-ours/<mode>.json` —— 入库源（git 跟踪）
- `backend/core/modes/builtin/custom_<mode>.json` —— 运行时副本（被 .gitignore 忽略）

registry 是**进程内单例**，改完 JSON 必须重启 `openclaw-inksight.service` 才生效。

## 渲染命令
```
cd skills/inksight/backend
../venv/bin/python3 scripts/history_backfill.py CUSTOM_HAICUOTU CUSTOM_TANGPING ...
```
注意：**必须用 `../venv/bin/python3`**，系统 python3 没装 fastapi/aiosqlite。
脚本跳过已存在的文件 —— 要重渲染得先 `rm runtime_cache/<mode>/screens/*.png`。
