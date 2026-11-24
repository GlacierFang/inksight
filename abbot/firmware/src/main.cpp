/**
 * main.cpp —— abbot 固件入口 (骨架阶段)
 *
 * 当前仅做启动自检与串口横幅, 用于打通「编译 → 产出 bin」链路。
 * HAL / LVGL / 网络任务在后续里程碑接入, 接入时以 doc/02 §六 的
 * hal_interfaces.h 为唯一对外契约, 严禁直接调用底层 API。
 */
#include <Arduino.h>

#include "board_config.h"

static const char *TAG = "abbot";

void setup() {
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
}

void loop() {
    /* 骨架阶段无业务逻辑 */
    delay(1000);
}
