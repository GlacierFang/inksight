# abbot 固件：Wi-Fi 真扫描 + 虚拟键盘（单项执行计划）

> 2026-11-25 18:44 起。单件事一个会话，做完立刻提交 + 跑 CI。

## 范围（只做这一件）
- `firmware/src/ui/ui_pages.cpp`：Wi-Fi 扫描页（真扫描结果）、虚拟键盘页（真键盘）
- `firmware/src/net/**`、`firmware/include/**`：必要时补纯逻辑模块
- `firmware/test/test_*.c`：主机侧纯逻辑测试
- `abbot/doc/02*.md` / `doc/03*.md`：Wi-Fi 相关描述改成如实

## 硬约束
- 交互宪法：四向 + 短按确定 + 长按 600ms 松开退出（阈值只由 HAL 裁定）
- UI 不碰 SPI；文件读取只经 `hal_storage_*`；网络任务不扫 TF 卡
- 文案不含 HTTP/AES/signature/Token/Python/Traceback
- LVGL v8.3.11（不用 v9 API）
- 本机禁 PlatformIO；只跑 gcc 主机侧测试
- UI 核栈 4KB → 页面刷新/列表构建用静态缓冲，不开大栈缓冲
- ESP-IDF 4.4 API 签名
- 密码不写日志、不明文进 Serial.printf
- 每项单独提交 + 单独跑 CI；推送只用 `scripts/gh-push.sh /tmp/abbot-firmware main`

## 已确认（读代码所得）

### 1. HAL 层 API 已在（`include/net_api.h`）
```c
struct WifiApInfo { char ssid[33]; int32_t rssi; bool is_encrypted; bool is_saved; };
enum WifiConnState { DISCONNECTED, SCANNING, CONNECTING, CONNECTED, AUTH_FAILED };
void net_wifi_init_auto_connect(void);
void net_wifi_start_async_scan(void);
int  net_wifi_get_scan_results(struct WifiApInfo *out_list, int max_count);
bool net_wifi_connect_and_save(const char *ssid, const char *password);
enum WifiConnState net_wifi_get_state(void);
int8_t net_wifi_get_rssi(void);
```
→ 结构与状态枚举**够用，不需要新造**。缺口全在 UI 接线。

### 2. 已保存网络的既有机制 = **NVS**
`net_api.cpp` 用 `nvs_get_str(NVS_NS_WIFI, "ssid" / "pass")` 读，
`net_wifi_connect_and_save` 用 `nvs_set_str(NVS_NS_WIFI, "ssid"/"pass")` 写，
`net_wifi_init_auto_connect` 开机自动重连。
→ 保存沿用 NVS，不另造一套。密码在 NVS（不由 UI 明文打印）。

### 3. UI 现状
`ui_pages.cpp:584 ui_build_wifi_scan`（写死三条）+ `:602 ui_build_keyboard`（写死五行文本），
路由入口在 `ui_router.cpp:260 / :273`（设置页与 OOBE 都进 `PAGE_WIFI_SCAN`），
`:336` 有 `PAGE_WIFI_SCAN` 分支。

## 待确认（继续读）
4. `net_api.cpp` Wi-Fi 实现细节（扫描状态机、返回数量语义、加密判定）
5. `ui_pages.cpp` 两个页面的现有写法（行高、标签、列表组件、按键语义）
6. `ui_router.cpp` 的按键分发（短按确定/长按退出怎么落到页面）
7. 现有测试的编译/运行方式（`test/host_check_arduino.sh` 与 CI 步骤）

## 步骤
1. 读 net_api.cpp / net_api.h / ui_pages.cpp 的 Wi-Fi 与键盘段 / ui_router.cpp
2. 抽纯逻辑：`wifi_scan_model`（解析+排序+信号分级+加密判定）、`keypad_editor`（光标/输入/退格/空格/大小写/掩码）
3. 主机侧测试 test_wifi_scan.c / test_keypad.c，gcc -Wall -Wextra -Werror 跑到 ALL PASS
4. UI 接线：扫描页进页触发扫描 → 轮询结果 → 列表真数据 → 选中加密网络 → 键盘页输密码 → 连接 → 三态反馈 + 重试
5. 保存 SSID+密码到既有配置存储；重启自动重连
6. 改 doc/02、doc/03 的 Wi-Fi 段落为如实描述
7. 提交 → 推 /tmp/abbot-firmware → CI → fetch fw-bin 核 sha256 与 manifest 逐一比对
8. 交付报告
