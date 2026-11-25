/**
 * FreeRTOS 主机侧 stub —— 只为让 `src/net/net_api.cpp` 能在 host gcc 上做语法检查。
 * 签名对齐真机（ESP-IDF / Arduino-ESP32 core 2.x）。本目录不进固件构建。
 */
#ifndef ABBOT_STUB_FREERTOS_H
#define ABBOT_STUB_FREERTOS_H

#include <cstddef>
#include <cstdint>

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

#endif /* ABBOT_STUB_FREERTOS_H */
