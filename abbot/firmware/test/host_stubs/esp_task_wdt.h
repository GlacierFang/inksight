/**
 * esp_task_wdt.h —— **主机侧 stub**，只为让 `src/net/ota_client_esp.cpp` 能在 host g++ 上做语法检查。
 *
 * 签名抄自 ESP-IDF **4.4** 的 `components/esp_system/include/esp_task_wdt.h`：
 *   - `esp_err_t esp_task_wdt_reset(void);` —— 给**当前任务**的看门狗喂狗。
 *
 * 真实固件里 OTA 下载每 4KB 调一次它（doc/04：边下边喂看门狗）。
 */
#ifndef ABBOT_STUB_ESP_TASK_WDT_H
#define ABBOT_STUB_ESP_TASK_WDT_H

#include "esp_err.h"

esp_err_t esp_task_wdt_reset(void);

#endif /* ABBOT_STUB_ESP_TASK_WDT_H */
