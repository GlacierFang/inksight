/*
 * ui_display_port.h —— LVGL 显示端口
 *
 * 把 LVGL 的绘制缓冲交给屏幕 HAL 搬运, UI 层不碰 SPI。
 */
#pragma once

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* LVGL 初始化 + 注册显示驱动。必须在使用 UI 的任务里调用。 */
void ui_display_port_init(void);

/* 推进 LVGL 时钟 (毫秒)。 */
void ui_display_port_tick(uint32_t elapsed_ms);

#ifdef __cplusplus
}
#endif
