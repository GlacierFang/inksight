# abbot 本轮实现计划（本地能力增量）

## 范围（五件，不扩）
- A. `include/ui_data.h` + `src/ui/ui_data.c`：UI 侧 TF 卡数据读取层，走 storage 请求/应答（`hal_storage_req_queue`），不直接调 `hal_storage_*`。
- B. 番茄钟真计时器 + 时长可设（默认 25 分钟）+ 到点提示音（新增 `hal_audio_play_tone`，设备端合成，不引入 LittleFS）。
- C. 首页今日课程读 `/config/schedule.json`；书架读 `/books/` 下 `.txt` + `.toc`；UTF-8 分页按字符边界回退；空态温和中文。
- D. 离线降级最小实现：本地页照常，端云功能温和中文提示，读卡失败不崩 UI。
- E. 字库：新中文文案加进 `tools/font_charset.txt` 并重生成 `src/ui/fonts/ui_font_cn_16.c`；若 lv_font_conv 装不上则只用现有字。

## 硬约束
- 固件编译只走 GitHub Actions；本机禁 PlatformIO，只做 gcc `-fsyntax-only`。
- 交互宪法：四向导航 + 短按确定 + 长按 600ms 松开退出；长按阈值只由 HAL 裁定。
- 错误文案不得出现 AES/HTTP/signature/Token/Python/Traceback。
- 真相源 doc/02 HAL、doc/03 交互、doc/04 存储无线、doc/05 端云；冲突必须同步修文档。

## 已读结论

### storage 请求/应答现状
- `storage_req_t`（hal_shared.h）现有 op：`1`=写文本、`2`=删除、`3`=读文本、`4`=写资产。
  op 1~3 **没有回执**，只有 op=4 通过 `storage_asset_job_t.done` 信号量回执。
- `task_storage`（tasks.cpp）单线程消费 `hal_storage_req_queue()`，TF 卡访问全部在此串行化。
- 队列投递范式见 `src/net/net_api.cpp:780-815`：`xSemaphoreCreateBinary()` + `xQueueSend` +
  `xSemaphoreTake(done, pdMS_TO_TICKS(30000))`，作业对象由投递方释放。
- **结论**：UI 数据层要照抄这个范式 —— 给 `storage_req_t` 扩展回执字段
  （done 信号量 / result / out_read / out_size / out_bytes / 区间缓冲 / 文件名表），
  新增 op `5`=读文本回执、`6`=读区间回执、`7`=列目录回执、`8`=取文件大小、
  `9`=原子写回执、`10`=目录占用、`11`=是否挂载。由 `task_storage` 处理并回填。

## 已读结论（二）：ui_pages.cpp 现状

- `ui_build_pomodoro`（约 372 行）：静态标签「25:00」+「专注中 · 完成后休息 5 分钟」+ 一个假列表项「> [ 暂停 ]」。
- `ui_build_home`（约 280 行）：今日课程是**写死文案**「今日课程 (周三 · 七年级上)\n1.语文 2.数学 …」；
  列表项 7 条：拍题导学 / 语音提问 / 学科卡片 / 电子书架 / 专注番茄 / 待问队列 / 系统设置。
- `ui_build_bookshelf` / `ui_build_reader`：写死的《小王子》三本书与固定正文。
- `ui_build_settings`：6 项（无线网络/音量/屏幕亮度/固件升级/电池与省电/关于设备）——
  「专注时长」需新增一项。
- 列表光标机制：`ui_make_list_item` 在 `s_build_page >= 0` 时登记条目（text 去行首标记），
  `s_item_count[PAGE_COUNT]`、`s_cursor[PAGE_COUNT]`；`ui_cursor_render` 按光标重写行首标记。
  → **条目是构建期静态登记的**，要动态刷新书架/课程列表，需要新增「重建某页内容」的能力。
- 提示文案表 `s_page_hints[]`：`[PAGE_POMODORO] = "上下选择 | 短按确定 | 长按退出"`。
- 样式：`s_style_root/card/title/body/hint/status`，字体统一 `ui_font_cn_16`。
- 浮层：`OVERLAY_REMINDER / OVERLAY_RECORDING / OVERLAY_TOAST`，`ui_overlay_set_text/visible`。
- 页面表 `s_page_builders[PAGE_COUNT]`，书页表 `s_book_builders[]`。

## 已读结论（三）：hal_audio.cpp 与路由

### hal_audio.cpp
- 半双工状态机 `AUDIO_IDLE / RECORDING / PLAYING`；播放前必 `i2s_driver_uninstall(I2S_NUM_0)` 再装 TX。
- `hal_audio_play_wav_file(path)` 走 `SD_MMC.open` —— **HAL 层自己可以碰 SD_MMC**（doc/02 §六例外）。
- 音量 `s_volume` 1~5，软件增益 `kVolumeGain[]` = {0,1,2,3,4,5}（gain/5）。
- `s_muted` 静音开关存在。
- **新增 `hal_audio_play_tone(freq_hz, duration_ms)`**：设备端合成方波/正弦 → PCM → 走 `hal_audio_write_pcm`
  同一路径（不碰 SD_MMC，不引入 LittleFS）。
  ⚠ `hal_audio_write_pcm` 装 TX 后 `s_state=PLAYING`，播完不卸载 → tone 播完需自行
  `i2s_driver_uninstall` 回 IDLE，否则后续录音被拒。需在 tone 函数内自行管理 TX 生命周期。
- 注意：hal_audio 是 .cpp（Arduino），tone 实现放这里。

### ui_router.cpp 现状（路由/手势）
- `ui_router_handle_gesture`：浮层优先 → 页面级 GESTURE_CLICK=确定、GESTURE_UP/DOWN=导航、GESTURE_LONG=退出。
- `ui_page_action(page)` 按页分派：`PAGE_HOME` idx 0..6；`PAGE_SETTINGS` idx 0→WiFi、3→OTA，
  其余 `ui_toast("这个设置还在准备中")`。
- `ui_page_nav(page, delta)`：PAGE_SOLVE_RESULT 走正文滚动，其余走列表光标。
- 书页：GESTURE_CLICK 在 `s_in_book` 时**不处理**（电子书子页的确认动作未实现）。
- `ui_router_tick()` 是空钩子 —— **倒计时刷新挂这里**（LVGL 5ms 心跳由 task_ui_fsm 驱动）。
- `ui_toast(text)` 走 OVERLAY_TOAST。

### doc/03 §五 关键线框（已读）
- PAGE_POMODORO 线框：**「短按暂停/继续 | 长按结束退出」**（这两条不许变）；
  页面文案：「专注计时」标题 + 大字计时「24 : 59」+「专注进行中」+「目标: 完成数学几何练习」。
  doc 现写「到点播放 `ding.wav` 并弹出完成提醒」—— 与实现矛盾（本轮改）。
  doc 现写「计时状态仅驻留内存，默认 25 分钟倒计时（断电不持久化）」——
  本轮要求时长可设且**持久化到 TF 卡**（只持久化「时长设置值」，不是计时状态）→ doc 需改。
- PAGE_SETTINGS doc 里是 6 项：无线网络配置 / 设备配对绑定 / 播报音量 / 背光亮度 / 离线手动校时 /
  固件版本与 OTA。**注意 doc 的 6 项与代码的 6 项名字不一样**（代码是：无线网络/音量/屏幕亮度/
  固件升级/电池与省电/关于设备）。本轮加第 7 项「专注时长」。
- PAGE_HOME 线框的今日课程卡片就是「今日课程 (周三 · 七年级上)」+ 课程名列表 → 本轮改为读卡。

## 已读结论（四）：字库与主机测试

### tools/font_charset.txt
- 第 1~2 行注释，**第 3 行是一整行连续字符集**（含中英文标点）。
- 现有字符集已含大量常用字（含「专注」「分钟」「倒计时」「暂停」「继续」「结束」「书架」「课程」
  「语文」「数学」「英语」「历史」「地理」「生物」「体育」等），但**未见**「尚未」「下载」「内容」
  「专注时长」「分钟」等是否齐全需逐字核对（见下）。
- 末尾 ASCII 标点段：`！（），：；？`（全角）。半角 `: ` `,` 等在 gen_font.sh 里自动补 ASCII 0x20~0x7E。

### tools/gen_font.sh
- 依赖：fontTools（`tools/subset_font.py`）+ node `lv_font_conv@1.5.3`（npm 直连，unset 代理）。
- 源字体 `/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc`，size=16，bpp=4。
- 产出 `src/ui/fonts/ui_font_cn_16.c`，提交进仓（CI 只编译不生成）。

### test/host_check_arduino.sh
- 目前只检查 `src/net/net_api.cpp` 一份源文件，g++ `-fsyntax-only -Wall -Wextra -Werror
  -Wformat=2 -Wformat-truncation=2`，include 路径 `test/host_stubs include src`。
- stub：Arduino.h / esp_heap_caps.h / Preferences.h / WiFi.h / freertos/{FreeRTOS,queue,semphr}.h
- stub 的 FreeRTOS 只声明 `xTaskGetTickCount/vTaskDelay/vTaskDelayUntil`；
  queue.h 声明 `xQueueCreate/xQueueSend/xQueueReceive`；semphr.h 声明
  `xSemaphoreCreateBinary/xSemaphoreTake/xSemaphoreGive/vSemaphoreDelete`。
- **本轮需扩 host_check 脚本**：把 `src/ui/ui_data.c`（纯 C）与新逻辑纳入主机侧检查，
  并为 ui_data 补最小 stub（xQueueSend/xSemaphoreTake/…）。
### doc/04 §二/§三 关键点（已读）
- **存储架构铁律（§二 开头）**：板载 `LittleFS` 仅存只读静态中文字库、内置提示音 `ding.wav`、静态图标；
  所有用户配置 / 学习记录 / 加密题库 / 离线队列 / 音频缓存 / 电子书**全部**在外部 TF 卡。
  ⚠ **铁律与实现矛盾**：仓库无 LittleFS 分区/挂载实现，也无任何 .wav 资产
  （platformio.ini 用 `huge_app.csv`，无 littlefs 分区表；源码无 LittleFS 代码）。
  → 本轮：新增 `hal_audio_play_tone()` 设备端合成提示音；**同步改 doc/04 §二 铁律**为
  「提示音为设备端合成，不依赖 LittleFS 资产；静态中文字库编译进固件」。
- 目录树（§2.1）：`/config/{profile.json,schedule.json}`、`/subjects/…`、
  **`/books/<book_name>.txt` + `<book_name>.toc`**、`/records/…`、`/outbox/…`、`/cache/…`、`/logs/…`。
  ⚠ 目录树里**没有**本地设置的落盘位置 → 番茄钟时长设置值需要新位置。
  **决定**：存 `/config/settings.json`（新增文件，写进 §2.1 目录树）。
- §2.3 原子写四步（.tmp → flush → 校验长度 → .bak → 重命名）+ 开机 .bak 自愈 → 沿用 `hal_storage_atomic_write_text`。
- §3.2 `schedule.json` 结构（**按文档写，不自造**）：
  `schema_version` / `updated_at` / `semester_start_date` / `weekend` / `holidays` /
  `period_times[{period,start,end}]` / `reminders[{id,time,type,enabled}]` /
  `class_bell_enabled` / `mute_window` / `days{"1":[{period,subject,week_type}]}` /
  `override_days{"2026-11-28":[…]}`
  - `week_type` ∈ ALL/ODD/EVEN，按 `semester_start_date` 算教学周奇偶。
  - 日期优先级：override_days > holidays/weekend（休息日）。
  - 节次时间从 `period_times` 取。
- doc/04 §2.1 里**没有** LittleFS 分区表。

### CI workflow（.github/workflows/build.yml）
- 触发：push 到 main / 手动 dispatch。
- 步骤：checkout → Python 3.11.9 → 缓存 PlatformIO 包 → 装 platformio==6.1.18 →
  **主机侧向量测试**（test_net_client.c / test_net_req.c / test_net_dispatch.c + `bash test/host_check_arduino.sh`）→
  `pio run -v` → 提取编译错误 → 收集产物（glob `*.bin` + manifest.txt）→ 推 `fw-bin` →
  upload-artifact → `gh release create`（要 GH_TOKEN）→ 回写 `ci-logs`（build.log / manifest.txt / steps.txt）。
- 主机侧测试步骤是**硬门**：新加的 `test/test_ui_data.c` 必须在这里挂上。
- 产物：`dist/*.bin` + `flash_args.txt` + `manifest.txt`（含 sha256）。

### platformio.ini
- `board_build.partitions = huge_app.csv`（无 littlefs 分区）。
- `-Wall -Wextra -Werror` 对我们自己的源码生效。
- LVGL 8.3.11。

### doc/07（电子书）关键点（已读）
- §2.2 `.toc` 格式：**每行 `章节标题|起始字节偏移量`**；无 `|` / 标题空 / 偏移非十进制 /
  `offset >= txt_size` → **丢弃该条目**（静默忽略）。无 `.toc` → 隐藏目录入口。
- §3 流式分页内核：
  - **UTF-8 边界对齐**：偏移处字节满足 `(byte & 0xC0) == 0x80` → 循环**向后**步进 1~3 字节
    直到遇到字符首字节（`0xxxxxxx` / `110xxxxx` / `1110xxxx` / `11110xxx`）。
  - 向后翻页：`next_offset = current_offset + page_bytes_consumed`（超界停末页）。
  - 向前翻页：从 `max(0, current_offset - 2048)` 向前预读 `2048` 字节，先 UTF-8 首字节对齐，
    按相同排版规则反推紧邻的上一页起始偏移。
  - 百分比跳转：按 `left` 先对齐到字符起点，再按百分比算 offset。
  - 进度百分比**实时重算** `(float)current_byte_offset * 100.0f / total_bytes`，钳位 0~100%。
- 阅读模块内**全局屏蔽录音浮层**（router 已实现）。
- 所有读取只经 `hal_storage_get_file_size / read_range / read_text / atomic_write_text / list_files`。

### hal_storage.cpp 关键事实
- `hal_storage_read_text(path, buf, max_len, out_len)`：读 `max_len-1` 字节并补 `'\0'`。
- `hal_storage_read_range`：`n>0` 才返回 HAL_OK，否则 HAL_ERR_IO（读到 0 字节算 IO 错）。
- `hal_storage_list_files(dir, cb, ctx)`：回调拿**纯文件名**（不含目录）。
- `hal_storage_atomic_write_text`：四步原子替换 + 写满不覆盖。
- **空白卡自愈默认文件里 `/config/schedule.json` 内容是 `{"lessons": []}`** ——
  与 doc/04 §3.2 的结构（`days` / `period_times` …）**不一致** → 本轮把默认内容改成
  doc §3.2 形状（`days` 空对象 + `period_times` 空数组 + schema_version）。

## 已读结论（五）：待改文件清单（最终）

1. `include/hal_interfaces.h` —— 加 `hal_audio_play_tone(uint16_t freq_hz, uint16_t duration_ms)`。
2. `src/hal/hal_audio.cpp` —— 实现 tone（合成正弦 → PCM → I2S TX → 卸载回 IDLE）。
3. `include/hal_shared.h` —— `storage_req_t` 扩展回执字段 + 新 op 枚举。
4. `src/tasks.cpp` —— `task_storage` 处理新 op 并回填回执。
5. `include/ui_data.h` + `src/ui/ui_data.c` —— UI 数据读取层（请求/应答封装 + UTF-8 边界对齐分页）。
6. `src/ui/ui_pages.cpp` —— 番茄钟真计时器 + 设置页「专注时长」+ 首页课表读卡 + 书架/阅读页读卡 + 动态列表重建。
7. `src/ui/ui_router.cpp` —— 番茄钟手势、设置时长调节、书页短按动作、离线提示。
8. `include/ui_router.h` / `include/ui_pages.h` —— 补新 API 声明。
9. `test/test_ui_data.c` + `test/host_stubs/` 扩充 + `test/host_check_arduino.sh` 挂新检查。
10. `.github/workflows/build.yml` —— 主机侧测试步骤加 `test_ui_data`。
11. `tools/font_charset.txt` + `src/ui/fonts/ui_font_cn_16.c` —— 字库。
12. `doc/02` §3.3/§六、`doc/03` §五、`doc/04` §二、`doc/07` —— 同步。
13. `ALIGN-NOTES.md` + `memory/` —— 同步记录。

### 结论：本轮改动落点
- `include/hal_interfaces.h` 加 `hal_audio_play_tone()`。
- `src/hal/hal_audio.cpp` 实现 tone。
- `src/hal/hal_shared.h` 的 `storage_req_t` 扩展回执字段 + 新 op。
- `src/tasks.cpp` 的 `task_storage` 处理新 op 并回填回执。
- 新增 `include/ui_data.h` + `src/ui/ui_data.c`。
- `src/ui/ui_pages.cpp`：番茄钟真计时器、设置页「专注时长」、首页今日课程读卡、书架读卡。
- `src/ui/ui_router.cpp`：番茄钟手势（短按暂停/继续、长按结束退出）、设置页时长调节、书页确认动作。
2. 备份 → 改主副本 `firmware/`。
3. 同步到 `/tmp/abbot-firmware` → 提交推送 main → 触发 Actions → 读 ci-logs → 循环修到绿。
4. 同步回主副本 → workspace 本地提交（不推 inksight 远端）。
