/**
 * main.cpp —— abbot 固件入口
 *
 * 初始化顺序有依赖，不能乱：
 *   1. hal_shared_init —— 总线锁 + 跨核队列 + 共享 I2C 总线（必须最先）
 *   2. 屏幕（ST7796S 竖屏 + 5 档背光）
 *   3. TF 卡（SD_MMC 1-Bit）+ 空白卡自愈建目录
 *   4. 摄像头（OV5640）+ AF 固件装载
 *   5. 音频（I2S 半双工）
 *   6. 摇杆（动态中点校准）
 *   7. 电量计（MAX17043）
 *   8. FreeRTOS 双核任务
 *
 * HAL 接入一律经 doc/02 §六 的 hal_interfaces.h，本文件不直接碰 SPI/LovyanGFX。
 */
#include <Arduino.h>

#include "board_config.h"
#include "hal_interfaces.h"
#include "hal_shared.h"
#include "ota_client.h"

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
    Serial.printf("[%s] long-press threshold = %dms (HAL only)\n", TAG, LONG_PRESS_MS);

    /*
     * 外设初始化结果汇总 —— 升级后的健康自检要看它（doc/04）。
     * TF 卡**不算**：没插卡是合法状态（纯在线模式），而固件升级不碰卡，
     * 不能因为没插卡就把一次成功的升级判成失败。
     */
    bool peripherals_ok = true;

    /* 1. 共享设施：总线锁 + 队列 + I2C 总线 */
    hal_shared_init();

    /* 2. 屏幕 */
    if (hal_display_init() != HAL_OK) {
        Serial.printf("[%s] display init failed\n", TAG);
        peripherals_ok = false;
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

    /* 3. TF 卡：没插卡不崩溃，切「纯在线阅后即焚模式」（doc/02 §3.5） */
    if (hal_storage_init_and_scaffold() != HAL_OK) {
        Serial.printf("[%s] no TF card -> online-only mode\n", TAG);
    }

    /* 4. 摄像头 */
    if (hal_camera_init() != HAL_OK) {
        Serial.printf("[%s] camera init failed\n", TAG);
        peripherals_ok = false;
    }

    /* 5. 音频 */
    if (hal_audio_init() != HAL_OK) {
        Serial.printf("[%s] audio init failed\n", TAG);
        peripherals_ok = false;
    }

    /* 6. 摇杆：开机 300ms 动态中点校准 */
    if (hal_joystick_init() != HAL_OK) {
        Serial.printf("[%s] joystick init failed\n", TAG);
        peripherals_ok = false;
    }

    /* 7. 电量计 */
    if (hal_power_init() != HAL_OK) {
        Serial.printf("[%s] power init failed\n", TAG);
        peripherals_ok = false;
    }

    /* 8. 双核任务 */
    abbot_tasks_start();

    /*
     * 9. 升级自检（doc/04）——
     *    只在「本次是待验证升级」时才真正跑：查 NVS 标记 ->
     *    外设初始化成功 + 稳定 20 秒 + 界面核心跳 >= 50 ->
     *    才确认新版本；否则把启动分区指回旧分区并重启。
     *
     *    本函数在 setup() 里**阻塞执行**：setup() 不返回，
     *    「Arduino 核心在 setup() 之后自动 mark valid」那条路径就没有机会插手。
     */
    ota_boot_selfcheck_run(peripherals_ok);

    Serial.printf("[%s] ready\n", TAG);
}

void loop()
{
    /* 所有业务在 FreeRTOS 任务里；loop 只负责看门狗与空闲降功耗 */
    hal_power_tick_idle();
    delay(200);
}
