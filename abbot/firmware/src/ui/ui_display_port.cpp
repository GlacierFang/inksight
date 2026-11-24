/*
 * ui_display_port.cpp —— LVGL 与 ST7796S 屏幕的显示端口
 *
 * 契约: abbot/doc/03 §二（320x480 竖屏三区）、doc/02 §3.1（LovyanGFX, SPI 40MHz）
 *
 * 分工:
 *   - 屏幕的 SPI/DMA 全部由 hal_display 持有, UI 层**不碰 SPI**。
 *   - 本文件只做 LVGL 侧的缓冲与 flush 回调, 像素搬运交给 hal_display 的内部桥。
 *   - 缓冲区放 PSRAM（8MB OPI PSRAM）, 单块 320x40 像素 = 25.6KB。
 */
#include "ui_display_port.h"

#include <Arduino.h>
#include <esp_heap_caps.h>

#include "board_config.h"
#include "hal_shared.h"
#include "lvgl.h"

static lv_disp_draw_buf_t s_draw_buf;
static lv_disp_drv_t s_disp_drv;
static lv_color_t *s_buf_a = NULL;
static lv_color_t *s_buf_b = NULL;

#define UI_DRAW_BUF_LINES 40

static void ui_flush_cb(lv_disp_drv_t *drv, const lv_area_t *area, lv_color_t *color_p)
{
    int32_t w = area->x2 - area->x1 + 1;
    int32_t h = area->y2 - area->y1 + 1;
    if (w <= 0 || h <= 0) {
        lv_disp_flush_ready(drv);
        return;
    }

    /* 像素搬运交给屏幕 HAL, UI 层不碰 SPI */
    hal_display_blit_rgb565(area->x1, area->y1, w, h, (const uint16_t *)color_p);

    lv_disp_flush_ready(drv);
}

void ui_display_port_init(void)
{
    lv_init();

    size_t px = (size_t)LCD_WIDTH * UI_DRAW_BUF_LINES;
    if (psramFound()) {
        s_buf_a = (lv_color_t *)heap_caps_malloc(px * sizeof(lv_color_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        s_buf_b = (lv_color_t *)heap_caps_malloc(px * sizeof(lv_color_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    }
    if (s_buf_a == NULL || s_buf_b == NULL) {
        /* PSRAM 不可用时退回内部 SRAM, 宁可慢也不能开不了机 */
        if (s_buf_a == NULL) {
            s_buf_a = (lv_color_t *)malloc(px * sizeof(lv_color_t));
        }
        if (s_buf_b == NULL) {
            s_buf_b = (lv_color_t *)malloc(px * sizeof(lv_color_t));
        }
    }

    lv_disp_draw_buf_init(&s_draw_buf, s_buf_a, s_buf_b, px);

    lv_disp_drv_init(&s_disp_drv);
    s_disp_drv.hor_res = LCD_WIDTH;
    s_disp_drv.ver_res = LCD_HEIGHT;
    s_disp_drv.flush_cb = ui_flush_cb;
    s_disp_drv.draw_buf = &s_draw_buf;
    s_disp_drv.full_refresh = 0;
    s_disp_drv.direct_mode = 0;
    lv_disp_drv_register(&s_disp_drv);

    Serial.printf("[ui] display port ready %dx%d buf=%d lines\n",
                  LCD_WIDTH, LCD_HEIGHT, UI_DRAW_BUF_LINES);
}

void ui_display_port_tick(uint32_t elapsed_ms)
{
    lv_tick_inc(elapsed_ms);
}
