# abbot 本轮 UI 层实现计划（续作）

## 2.1 测试 bug（已修）
- `test/test_ui_data.c`：原断言按「一页 57 字节」硬编码 → 改为按实际排版规则收集页起点，
  锁三条不变量：①一页消耗 ≤ 1024 字节读缓冲 ②页起点落在 UTF-8 字符首字节
  ③`ui_data_prev_page_offset` 返回值一定是**真页起点**（新增端到端用例，含混排文本）。
- **顺带修了实现里的真 bug**：`ui_data_prev_page_offset` 原先用「2048 字节回溯窗口」反推，
  窗口起点落在字符中间时重排的行/页边界与全局不一致 → 上一页算偏几个字
  （实测偏 1 字节、落在字符中间，会切坏字）。
  改法：页网格是**全局流式**的（14 行 × 38 半角单位），无法从局部窗口反推；
  改为用与 `ui_data_layout_page` **同一套折行规则**从文件头重扫（`ui_scan_chunk`），
  取最后一个 < offset 的页起点。扫描只到 offset+1 页，成本可控。
- 结果：`69 checks, 0 failures`。

## 2.2 UI 层要做的（src/ui/ui_pages.cpp + src/ui/ui_router.cpp）

### 番茄钟（PAGE_POMODORO）
- 状态：`IDLE / RUNNING / PAUSED / DONE`，用**毫秒**计数（`ui_pages_pomodoro_tick(now_ms)`），
  不做秒级累加，避免漂移。
- 时长来源：`ui_pages_focus_minutes()`（默认 25，步长 5，范围 5~120）→ 持久化 `/config/settings.json`。
- 到点：`hal_audio_request_tone(freq, ms)`（非阻塞，音频任务真正出声）+ 进入 DONE 态。
- 手势（doc/03 §五 线框，不许变）：**短按 = 暂停/继续**（DONE 后再按 = 重新开始），
  **长按 600ms 松开 = 结束并退出**。
- 提示文案表 `s_page_hints[]` 同步改。

### 设置页「专注时长」
- PAGE_SETTINGS 加第 7 项「专注时长」。
- 入口：上下键移动光标到该项 → **左/右** 调时长（-5/+5 分钟），短按 = 进入调整模式。
  （四向导航宪法：左/右天然是「减/加」，不引入新手势。）

### 首页今日课程
- `ui_data_read_schedule` + `ui_data_today` → 渲染真实课程行；
  读不到 / 今天没课 → 温和中文空态（「今天没有课程安排」）。

### 书架与阅读页
- `ui_data_list_files("/books")` → 书架条目（.txt）。
- 打开：`ui_data_load_page` 渲染首页；`ui_pages_reader_next_page/prev_page` 翻页。
- 多字节字符不许切坏（`ui_data_layout_page` 保证）。
- 空态：「还没有下载内容」/「暂时读不到内容」。

### 离线降级
- 本地页（番茄钟/设置/书架/课表）不依赖网关；
- 端云功能在未配对/不可达时给温和中文提示；
- 读卡失败不崩 UI（全部走 ui_data 的错误码 → 文案）。

## 2.3 字库
- `tools/font_charset.txt` 第 3 行加新中文 → `tools/gen_font.sh` 重生成
  `src/ui/fonts/ui_font_cn_16.c`（lv_font_conv 在 `/tmp/lvtest/node_modules/.bin/`）。

## 2.4 文档
- doc/03 手势表（番茄钟页 + 设置页第 7 项）
- doc/04 存储铁律（ding.wav → 设备端合成提示音，说明为什么不引 LittleFS）
- doc/02 §3.3 音频接口（hal_audio_play_tone / hal_audio_request_tone）
- doc/07 分页/读缓冲语义（一页 14 行 × 38 半角单位、1024 字节读缓冲、页网格全局性）
- ALIGN-NOTES.md §9.20 回写
