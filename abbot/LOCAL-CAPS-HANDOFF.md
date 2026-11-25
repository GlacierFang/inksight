# abbot 本地能力增量 —— 交接记录

> **状态：已完成（2026-11-25 15:40）** —— 续作子会话 `agent:main:subagent:620094f0`
> 把「未做」清单全部做完，GitHub Actions 全绿（run `45569228140`）。
> 下面的「一 / 二」保留为**断点当时的原始事实**，阅读时请以本文末尾的
> `abbot/ALIGN-NOTES.md §9.20.1 本轮完成情况` 为准。
>
> 2026-11-25 15:2x 第一次子会话被中断，本文件为断点事实，交接用。
> 真相源仍是 doc/02（HAL）、doc/03（界面交互）、doc/04（网络/OTA/存储）、doc/05（端云协议）。

## 一、已完成（在盘上，改动未提交）

子会话 `agent:main:subagent:9095dc10` 在被中断前已落盘：

- 新增 `include/ui_data.h`、`src/ui/ui_data.c`（约 33KB）、`test/test_ui_data.c`（约 16KB）
- 新增 `include/storage_io.h`
- 改 `include/hal_interfaces.h`、`include/hal_shared.h`、`src/hal/hal_shared.cpp`、`src/hal/hal_audio.cpp`、`src/tasks.cpp`
- 改主机侧 stub：`test/host_stubs/freertos/{FreeRTOS.h,queue.h,semphr.h}`
- 改 `include/ui_pages.h`：已声明
  `ui_pages_pomodoro_enter/tick/toggle/stop`、`ui_pages_refresh_home`、`ui_pages_refresh_bookshelf`、
  `ui_pages_bookshelf_move`、`ui_pages_bookshelf_open_selected`、`ui_pages_reader_next_page/prev_page`、
  `ui_pages_focus_minutes`、`ui_pages_focus_adjust`、`ui_pages_set_status`
- 规划稿 `firmware/PLAN-local-caps.md`

`src/ui/ui_pages.cpp` 与 `src/ui/ui_router.cpp` **未改动**（grep 计数为 0）。

## 二、未做（断点后的全部剩余工作）

1. `ui_pages.cpp` 接新 API：番茄钟真计时、专注时长项、首页课表、书架与阅读页。
2. `ui_router.cpp` 接手势：番茄钟短按暂停/继续、长按结束退出；设置页调时长；书架翻页与打开。
3. 字库：新增中文入 `tools/font_charset.txt`，重跑 `tools/gen_font.sh` 生成 `src/ui/fonts/ui_font_cn_16.c`。
   `lv_font_conv` 是否装上需核实（子会话曾在 `/tmp/lvtest` 后台装）；装不上就只用现有字库里的字。
4. 文档同步：doc/03 手势表、doc/04 存储铁律（LittleFS/ding.wav 与实现矛盾，改成设备端合成提示音）、doc/02 §3.3 音频接口。
5. 同步到 `/tmp/abbot-firmware` → 提交推 main → GitHub Actions 全绿 → 核验 `fw-bin` 三个 bin 与 `manifest.txt`。
6. 同步回 workspace 主副本并本地提交（workspace 仓库只做本地提交，不推 inksight 远端）。

## 二点五、断点后主会话新核实的事实

- `src/ui/ui_pages.cpp`、`src/ui/ui_router.cpp` **仍是原样**，grep `pomodoro_tick|focus_minutes|refresh_bookshelf` 计数为 0 —— UI 层一行未写。
- 主机侧 ui_data 测试现状：`50 checks, 3 failures`
  - `test/test_ui_data.c:320` 期望首页消耗 57 字节，实际 180
  - `:330` 期望上一页起点 57，实际 0
  - `:335` 期望 114，实际 0
  - 判读：被测文本只有 60 个全角字（180 字节），而一页的容量是 14 行 × 38 半角单位，全角字每字 2 单位 → 19 字/行 → 266 字/页，**整段文本本来就在同一页里**，所以"首页消耗全部 180 字节"是对的，**是测试断言写错了**（它按每页 57 字节算）。要测多页必须用足够长的文本，且页步长随全角/半角混合而变，不能硬编码。
  - 真正该锁的不变量：①UTF-8 字符不被切坏（`ui_data_align_forward/backward`）；②一页消耗 ≤ 读缓冲 1024 字节（doc/07）；③`prev_page_offset` 返回的一定是某页起点。
- `lv_font_conv` **已经装上了**：`/tmp/lvtest/node_modules/.bin/lv_font_conv`（npm 直连可通，装的时候 unset 了代理变量）。所以字库重生成**可行**，不再受"只能用现有字"的限制。
- `hal_audio.cpp` 已加 `hal_audio_play_tone(freq_hz, duration_ms)` 与 `hal_audio_request_tone(...)`（音频任务收到后真正出声）。
- `ui_data.h` 已有的接口面：`ui_data_read_text/read_range/list_files/file_size/dir_usage/write_text`、
  `ui_data_utf8_align_forward/backward`、`ui_data_layout_page`、`ui_data_layout_prev_start`、
  `ui_data_load_page`、`ui_data_prev_page_offset`、`ui_data_parse_schedule`、`ui_data_read_schedule`、
  `ui_data_today`、`ui_data_parse_focus_minutes`、`ui_data_focus_minutes_get/set`、`ui_data_read_paired`。

## 三、硬约束（不变）

- 固件只由 GitHub Actions 编译；本机禁止 PlatformIO，只允许 gcc 主机侧测试。
- 四向导航 + 短按确定 + 长按 600ms 松开退出；长按阈值只由 HAL 裁定；保留摇杆；语音只能从菜单进入。
- UI 只经 `hal_storage_*` 读卡，且按 doc/02 §4.2 走存储任务请求/应答，UI 任务不直接摸卡、不碰 SPI。
- 错误文案不得出现 AES/HTTP/signature/Token/Python/Traceback。
- 番茄钟：真实倒计时、时长可设、到点有提示音、持久化到 TF 卡原子写、播放不阻塞 UI 任务。
