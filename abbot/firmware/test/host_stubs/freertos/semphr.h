/**
 * semphr 主机侧 stub —— 只为让 host gcc 语法检查与 ui_data 主机侧单测通过，签名对齐真机。
 *
 * ⚠️ 名字必须与真机一致：ESP-IDF 提供的是 vSemaphoreDelete，没有 xSemaphoreDelete。
 * stub 里编造不存在的名字会把「真机才暴露的编译错」在本机掩盖掉 —— 别再犯。
 */
#ifndef ABBOT_STUB_SEMPHR_H
#define ABBOT_STUB_SEMPHR_H

#include "freertos/FreeRTOS.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef void *SemaphoreHandle_t;

SemaphoreHandle_t xSemaphoreCreateBinary(void);
BaseType_t xSemaphoreTake(SemaphoreHandle_t s, TickType_t wait);
BaseType_t xSemaphoreGive(SemaphoreHandle_t s);
void vSemaphoreDelete(SemaphoreHandle_t s);

#ifdef __cplusplus
}
#endif

#endif /* ABBOT_STUB_SEMPHR_H */
