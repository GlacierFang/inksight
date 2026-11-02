---
name: InkSight-节气倒计时
description: 「节气倒计时」模式（CUSTOM_SOLAR_TERMS）的口径、数据来源与排版约定
updated: 2026-11-02
---
# InkSight · 节气倒计时（CUSTOM_SOLAR_TERMS）
<!-- project: path:/home/open/.openclaw/workspace -->

## 是什么

当年 24 个节气的逐个倒计时屏。冰河大人 2026-11-02 定的「B 方案」。

## 口径（不要改）

- 两列各 12 个：**左列第 1–12 个、右列第 13–24 个**，每列自上而下按年度时间顺序。
- 过去的节气显示「已过 N 天」，将来的显示「还有 N 天」，当天正好是节气显示「今日」。
- 头部：当前节气名 + 第 N/24 个；右侧「距 <下一节气>」+ 数字 + 天。
- 纯本地确定性 provider：**不联网、不调 LLM**。节气是确定数据，模型只会加废话。

## 数据来源

- 表内 `2024–2030`：`core/config.py:SOLAR_TERMS`，精确到日。
- 表外（`2031` 起）：寿星公式推算，误差约 ±1 天；与 `core/year_progress.py` 的清明推算同源同精度。
- 跨年：12/31 时下一节气取次年第一个（`小寒`）。

## 文件

| 角色 | 路径 |
| --- | --- |
| 源定义（改这个） | `skills/inksight/modes-ours/solar_terms.json` |
| 部署副本（gitignore，不入库） | `skills/inksight/backend/core/modes/builtin/custom_solar_terms.json` |
| provider | `skills/inksight/backend/core/solar_terms.py` |
| 派发 | `skills/inksight/backend/core/json_content.py` → `_generate_computed_content` |
| 目录 | `skills/inksight/backend/core/mode_catalog.py` → `BUILTIN_CATALOG` |
| 测试 | `skills/inksight/backend/tests/test_solar_terms.py`（16 项） |

改完源定义要 `cp` 到部署副本，两份必须逐字节一致（有测试卡这个）。

## 未做

- 回顾页存档：`year_progress`/`dayoverview` 这类文本模式都不在 gallery 里，本模式按同样惯例未加。

## 提交

- `8c389d9` feat(inksight): 新增「节气倒计时」模式（CUSTOM_SOLAR_TERMS）
