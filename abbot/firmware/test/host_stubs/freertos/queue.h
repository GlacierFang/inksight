/**
 * queue 主机侧 stub —— 只为让 host gcc 语法检查通过，签名对齐真机。
 */
#ifndef ABBOT_STUB_QUEUE_H
#define ABBOT_STUB_QUEUE_H

#include "freertos/FreeRTOS.h"

typedef void *QueueHandle_t;

QueueHandle_t xQueueCreate(UBaseType_t length, UBaseType_t item_size);
BaseType_t xQueueSend(QueueHandle_t q, const void *item, TickType_t wait);
BaseType_t xQueueReceive(QueueHandle_t q, void *item, TickType_t wait);

#endif /* ABBOT_STUB_QUEUE_H */
