# InkSight 诗笺日课双城无法保存（2026-10-31 已修复，commit 4d11e93）

## 现象
设备配置页给 CUSTOM_POETRY_DAY（诗笺日课）选两座城市（poetry_home / poetry_away），保存后不生效。

## 后端已确认无问题（逐条实测）
- ConfigRequest.validate_mode_overrides（backend/core/schemas.py:170）保留嵌套 mode_settings
- save_config → get_active_config 往返实测原样入库（用假 MAC 测的，测试行已删）
- /api/modes/catalog 对 CUSTOM_POETRY_DAY 返回 2 个 location 槽位
- poetry_day._mode_settings()（core/poetry_day.py:246）与 pipeline.get_effective_mode_config() 读取路径都正确（顶层 mode_settings 与 mode_overrides 双通道都认）

## DB 实证（关键）
configs 表所有行的 mode_overrides 里 CUSTOM_POETRY_DAY 从未出现过；
同一份配置里扁平字段的 CUSTOM_SUN_MOON / CUSTOM_DAYOVERVIEW 却存进去了。
→ 这个值根本没进过 POST，不是被后端吃掉的。

## 前端两个缺陷
1. 双城弹窗按钮写「保存并预览」，但 commitModalAction（webapp/app/config/page.tsx:2154）
   只做 updateModeOverride（React state）+ handlePreview，从不 POST /api/config。
   唯一落库路径是页面级「保存配置」（handleSave:1413）、handleSavePreferences(1505)、
   saveVocabReviewSettings(2232)。预览显示正确 → 看着像存了 → 实际只在浏览器内存里，
   刷新或换设备就没。单城市模式（WEATHER / CUSTOM_SUN_MOON 等）同一个坑。
2. 「模式设置」面板不可达：setSettingsMode 全文件只被赋 null，从没赋过 mode id，
   webapp/app/config/page.tsx:3120 那个面板是死代码。

## 修复（2026-10-31，commit 4d11e93）
- 抽 buildConfigPayload() + persistConfig() 作为唯一落库出口，
  handleSave / handleSavePreferences / saveVocabReviewSettings 三处共用
- commitModalAction 自己算最终快照（setState 拿不到同步结果），有变更即落库
  + 通知在线设备刷新；「清除设置」分支同样落库
- 删死代码：setSettingsMode 从未被赋过 mode id 的「模式设置」面板（不可达），
  连同 applySettingsDrafts / clearModeOverride / settingsJsonDrafts / settingsJsonErrors /
  memoSettingsInputRef / activeModeSchema
- 纳管 webapp 两个原创页面（此前整个 webapp/ 被 gitignore）：
  config/page.tsx、gallery/page.tsx

构建通过 + web 服务重启，/ink/config 与 /ink/gallery 均 200。
落库行为需人工在设备配置页实操确认（诗笺日课选双城 → 保存并预览 → 刷新页面看是否还在）。

## 注意
skills/inksight/webapp/ 未被 git 跟踪（.gitignore: skills/inksight/webapp/），
改前端不入库 —— 要先决定是否纳管。
