/**
 * esp_system.h —— **主机侧 stub**，只为让 `src/net/ota_client_esp.cpp` 能在 host g++ 上做语法检查。
 *
 * 签名抄自 ESP-IDF **4.4** 的 `components/esp_system/include/esp_system.h`：
 *   - `void esp_restart(void);` —— 立刻重启（OTA 激活 / 回滚都用它）。
 */
#ifndef ABBOT_STUB_ESP_SYSTEM_H
#define ABBOT_STUB_ESP_SYSTEM_H

#include "esp_err.h"

void esp_restart(void);

#endif /* ABBOT_STUB_ESP_SYSTEM_H */
