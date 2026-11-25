/**
 * queue 主机侧 stub —— 只为让 host gcc 语法检查与 ui_data 主机侧单测通过，签名对齐真机。
 *
 * 真实 FreeRTOS 的 queue 函数是 C 链接，这里用 `extern "C"` 与真机保持一致。
 */
#ifndef ABBOT_STUB_QUEUE_H
#define ABBOT_STUB_QUEUE_H

#include "freertos/FreeRTOS.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef void *QueueHandle_t;

QueueHandle_t xQueueCreate(UBaseType_t length, UBaseType_t item_size);
BaseType_t xQueueSend(QueueHandle_t q, const void *item, TickType_t wait);
BaseType_t xQueueReceive(QueueHandle_t q, void *item, TickType_t wait);

#ifdef __cplusplus
}
#endif

#endif /* ABBOT_STUB_QUEUE_H */
