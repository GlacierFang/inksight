/**
 * FreeRTOS 主机侧 stub —— 只为让 host gcc 能编 `src/ui/ui_data.c` 与 `src/net/net_api.cpp`。
 * 签名对齐真机（ESP-IDF / Arduino-ESP32 core 2.x）。本目录不进固件构建。
 *
 * ⚠️ 头文件必须同时能编 **C 与 C++**：
 *    - 真实 FreeRTOS 头是 C 头，函数是 C 链接；stub 用 `extern "C"` 包起来才与真机一致。
 *    - 不用 <cstddef>/<cstdint>（C++ 专有），改用 <stddef.h>/<stdint.h>（C/C++ 通用）。
 */
#ifndef ABBOT_STUB_FREERTOS_H
#define ABBOT_STUB_FREERTOS_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef uint32_t TickType_t;
typedef int BaseType_t;
typedef uint32_t UBaseType_t;

#define pdTRUE  1
#define pdFALSE 0
#define pdPASS  1
#define pdFAIL 0
#define portMAX_DELAY ((TickType_t)0xFFFFFFFFUL)
#define pdMS_TO_TICKS(ms) ((TickType_t)(ms))

TickType_t xTaskGetTickCount(void);
void vTaskDelay(TickType_t t);
void vTaskDelayUntil(TickType_t *prev, TickType_t inc);

#ifdef __cplusplus
}
#endif

#endif /* ABBOT_STUB_FREERTOS_H */
