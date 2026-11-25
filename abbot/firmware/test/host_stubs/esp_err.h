/**
 * esp_err.h —— **主机侧 stub**，只为让 ESP-IDF 相关源码能在 host g++ 上做语法检查。
 *
 * 签名抄自 ESP-IDF **4.4**（arduino-esp32 2.0.17 自带版本）
 * `components/esp_common/include/esp_err.h`：
 *   - `typedef int esp_err_t;`
 *   - `#define ESP_OK 0`
 *
 * ⚠️ 本目录**不进固件构建**，只在主机侧语法检查里用 `-I` 引进来。
 */
#ifndef ABBOT_STUB_ESP_ERR_H
#define ABBOT_STUB_ESP_ERR_H

typedef int esp_err_t;

#define ESP_OK 0
#define ESP_FAIL -1

#endif /* ABBOT_STUB_ESP_ERR_H */
