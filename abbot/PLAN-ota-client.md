# abbot OTA 客户端设计（固件侧）—— 已拍板的实现细节

> 2026-11-25 18:30 立。给下一个子会话直接照做，**不要重新推导**。
> 需求在 `PLAN-block4.md` §1.2；真相源是 `doc/04` 的 OTA 章节与 `doc/05` §三。

## 一、分区表（16MB Flash 双应用分区）—— 已定，照抄

现状：`platformio.ini` 的 `board_build.partitions = huge_app.csv`，**只有 app0**，
而 doc/04 要求经 `esp_ota_get_next_update_partition()` 写入未激活的备用分区。

新增文件 `firmware/ota_partitions.csv`：

```
# Name,   Type, SubType, Offset,   Size,     Flags
nvs,      data, nvs,     0x9000,   0x5000,
otadata,  data, ota,     0xE000,   0x2000,
phy_init, data, phy,     0x10000,  0x1000,
app0,     app,  ota_0,   0x20000,  0x400000,
app1,     app,  ota_1,   0x420000, 0x400000,
```

- app0/app1 各 **4MB**，当前固件 1.29MB，3 倍余量；两个 app 分区必须**等长**且
  `SubType` 分别是 `ota_0` / `ota_1`（`esp_ota_get_next_update_partition()` 靠这个选备用分区）。
- 偏移 0x20000 与 0x420000 都 0x10000 对齐；大小 0x400000 是 0x1000 的整数倍 → 满足 ESP-IDF 分区表对齐要求。
- `otadata` 必须留（引导器据此选择启动分区）。
- 16MB 剩余的 0x7E0000 **不划任何分区**（不引 LittleFS、不引 spiffs —— doc/04 §存储铁律
  「零 LittleFS 依赖 = 零分区表 = 零挂载失败分支」）。未划分的空间闲置，Arduino 框架不要求
  存在文件系统分区（本项目从不挂载文件系统）。

`platformio.ini` 改一行：
```ini
board_build.partitions = ota_partitions.csv
```
（文件放 `firmware/` 根目录，PlatformIO 会在工程目录里找。）

⚠️ **改分区表会影响整个 CI 构建产物大小**，改完必须核三个 bin 仍能产出、sha256 与 `manifest.txt` 对得上。

## 二、升级流程（doc/04 的三重校验 + 回滚，顺序不可换）

1. **前置采样**：静置状态连续采样 3 次（间隔 50ms）电池电压取均值，
   `avg_mv >= 3600mV` 且 `RSSI >= -75dBm` 才允许启动 OTA。不满足 → 温和中文提示，不启动。
2. **查更新**：`GET /api/v1/ota/check?device_id=&current_ver=`（走标准设备签名 Header）→
   `has_update / latest_ver / sha256 / size / bin_url / changelog`。接口形状已定，不许改。
3. **下载**：`bin_url` 是带签名 token 的 `/api/v1/content/download?token=...`（10 分钟有效）。
   边下边写，**每 4KB 喂一次看门狗**。
4. **三重校验**：
   - 首包首字节必须 `0xE9`（已核：CI 产出的 `firmware.bin` 首字节确实是 `e9`）
   - 总接收字节数与 `size` 一致，且不超过目标分区实际可用容量（4MB）
   - 流式 `mbedtls_sha256` 与网关声明的 `sha256` 完全一致
5. **写备用分区**：`esp_ota_get_next_update_partition()` → `esp_ota_begin()` →
   `esp_ota_write()`（分块）→ `esp_ota_end()`。
   ⚠️ Arduino 框架的 `Update` 类也可以，但 doc/04 明确点了 `esp_ota_get_next_update_partition()`，
   所以**直接用 `esp_ota_ops.h` 的 API**（`#include <esp_ota_ops.h>`，Arduino 工程里可用）。
6. **标记启动**：`esp_ota_set_boot_partition()` 把新分区设为 boot。
   同时在 **NVS** 写一个「本次是待验证升级」标记（含目标版本号）。
7. **重启后自检**：新固件启动时先查 NVS 那个标记 + `esp_ota_get_state_partition()`。
   - 若状态是 `ESP_OTA_IMG_PENDING_VERIFY` → 跑健康自检：
     所有 HAL 外设初始化成功、稳定运行满 **20 秒**、且 `task_ui_fsm` 主循环健康心跳计数 **>= 50**
     → 才调 `esp_ota_mark_app_valid_cancel_rollback()`，并清掉 NVS 标记。
   - 20 秒内死锁/崩溃 → 看门狗 15 秒硬复位 → 引导器自动回滚旧分区。
8. **上报结果**：`report_result` 回服务端（成功/失败都报）。

### ⚠️ 一个必须查清的风险点

**Arduino ESP32 核心有可能在 `setup()` 之后自动调 `esp_ota_mark_app_valid_cancel_rollback()`**
（若核心的 `main.cpp` 里有这段逻辑）。若核心自动标记为 VALID，
那么「新固件崩溃 → 重启 → 引导器回滚」这条保护就**失效**了 —— 因为状态已是 VALID。

处理办法（子会话必须查清并落地）：
- 在 CI 里能拿到框架源码；先确认 `main.cpp` 是否有自动 mark 的逻辑
  （框架路径 `~/.platformio/packages/framework-arduinoespressif32/cores/esp32/main.cpp`，
  CI 上有；本机没有 platformio，**不要为了读它去装 platformio**）。
- 若核心会自动 mark → 必须用 `build_flags` 覆盖 sdkconfig 关掉它，例如
  `-DCONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=0`（但这会影响预编译 bootloader 的行为，
  需确认预编译 bootloader 是否本身就带回滚能力；**改不动 bootloader 就改策略**）。
- 无论核心行为如何，**NVS 标记 + 自检 + 显式 mark** 这套都要实现，它是自证安全的兜底。

## 三、UI（`src/ui/ui_pages.cpp` 的 `ui_build_ota`，现在写死「当前已是最新版本 / 上次检查：刚刚」）

要显示：
- 当前版本 / 最新版本 / changelog
- 下载进度百分比（实时）
- 结果提示（成功 / 失败原因的中文说法）

「[ 检查更新 ]」按钮要真能触发检查 —— **不做 UI 美化**，只把写死文案换成真实数据。

## 四、主机侧测试（要跑进 CI 的测试步骤）

新建 `test/test_ota.c`，至少覆盖：
- **分区容量计算**：app0/app1 各 4MB，固件 1.29MB 能装下；越界写入必须被拒
- **sha256 流式校验**：分块喂入与一次性算出的结果一致；**改一个字节必须不一致**
- **首字节魔数**：不是 `0xE9` 必须被拒
- **版本比较**：current_ver 等于 latest → `has_update=false`；旧版本 → true

## 五、接口形状（不许改）

- `GET /api/v1/ota/check?device_id=&current_ver=` → `has_update, latest_ver, force_update, sha256, size, bin_url, changelog`
- `bin_url` = `http://ruohan.site/abbot/api/v1/content/download?token=<10分钟有效的签名token>`
- 服务端 `ota_manager.publish()` 落盘文件名是 `fw_{version}.bin`，`manifest.json` 里 `file` 字段记这个名字
  （不要自己拼 `{version}.bin`，对不上会 404）。

## 六、工作方式（照旧）

- 改 `/home/open/.openclaw/workspace/abbot/firmware` → 同步到 `/tmp/abbot-firmware` → 提交推 main → CI → 核产物
- 推送只用 `/home/open/.openclaw/workspace/scripts/gh-push.sh /tmp/abbot-firmware main`
- **固件只由 GitHub Actions 编译；本机禁 PlatformIO**，本机只允许 gcc 主机侧测试
- **小步提交**：分区表先单独提一次并跑一轮 CI（因为它影响整个产物），再做客户端逻辑
- 本机拉不到 Actions logs/artifacts → 失败只读代码 + `ci-logs` 分支
- 核验 `fw-bin`：`git fetch origin '+refs/heads/fw-bin:refs/remotes/origin/fw-bin'`
- 提交前本机 `g++ -fsyntax-only -Wall -Wextra -Werror` 自查（见 `firmware/test/host_check_arduino.sh`）
- host stub 只能写真机真实存在的 API 名字，逐字抄真实头文件
- LVGL v8.3.11：没有 `lv_obj_set_style_all`
- 跨模块非 static 函数，定义它的 .cpp 必须 include 声明它的头
- Arduino 2.x 的 `File::read/write` 收 `uint8_t*` → 显式强转
