/**
 * main.cpp —— abbot 固件入口
 *
 * 骨架阶段：点亮 ST7796S 竖屏、跑通 5 档背光、打印启动自检横幅。
 * 目的是打通「编译 → 产出 bin → 首次刷写」的链路，硬件到位当天即可验证
 * 屏幕接线与 320x480 竖屏骨架。
 *
 * HAL 接入一律经 doc/02 §六 的 hal_interfaces.h，本文件不直接碰 SPI/LovyanGFX。
 */

#include <Arduino.h>

#include "board_config.h"
#include "hal_interfaces.h"

static const char *TAG = "abbot";

void setup()
{
    Serial.begin(115200);
    const uint32_t t0 = millis();
    while (!Serial && (millis() - t0) < 1000) {
        delay(10);
    }

    Serial.println();
    Serial.printf("[%s] boot, chip=%s rev=%d\n", TAG,
                  ESP.getChipModel(), ESP.getChipRevision());
    Serial.printf("[%s] flash=%dMB psram=%dMB\n", TAG,
                  (int)(ESP.getFlashChipSize() / (1024 * 1024)),
                  (int)(ESP.getPsramSize() / (1024 * 1024)));
    Serial.printf("[%s] lcd=%dx%d backlight gpio=%d\n", TAG,
                  LCD_WIDTH, LCD_HEIGHT, LCD_PIN_BL);
    Serial.printf("[%s] long-press threshold = %dms (HAL only)\n", TAG,
                  LONG_PRESS_MS);

    /* 屏幕 */
    if (hal_display_init() != HAL_OK) {
        Serial.printf("[%s] display init failed\n", TAG);
    } else {
        Serial.printf("[%s] display ready\n", TAG);
        /* 逐档点亮背光：真机首刷时能肉眼确认 5 档亮度梯度 */
        for (uint8_t lv = 1; lv <= 5; lv++) {
            hal_display_set_backlight(lv);
            Serial.printf("[%s] backlight level %d\n", TAG, lv);
            delay(300);
        }
        hal_display_set_backlight(2);
        hal_display_flush();
    }
}

void loop()
{
    /* 骨架阶段无业务逻辑 */
    delay(1000);
}
