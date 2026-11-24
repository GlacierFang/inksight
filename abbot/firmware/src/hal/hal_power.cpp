/**
 * hal_power.cpp —— MAX17043 电量计与三级低功耗调度
 *
 * 契约: abbot/doc/02 §3.6
 *   - 电量读取持 g_i2c_bus_mutex 读 MAX17043（I2C 地址 0x36）的
 *     VCELL（原始电压）与 SOC（百分比，钳位 0~100%）
 *   - 正常活跃态：屏幕全亮，Wi-Fi 按需连接
 *   - 无操作 60 秒：背光降到最低档
 *   - 无操作 180 秒：背光关闭 + 配置 GPIO 0 唤醒与 RTC 定时唤醒，
 *     进入 esp_light_sleep_start()
 *   - 浅休眠唤醒后外设状态丢失，必须重建 SPI 屏 / I2C / I2S（hal_recover_after_sleep）
 *   - 低电保护：电压持续低于 3.40V（SOC <= 5%）时提示 3 秒后强制深度睡眠
 *
 * MAX17043 与 OV5640 物理共享 I2C_NUM_0（GPIO 4/5，100kHz），
 * 所有访问持同一把总线锁 —— 拍照捕获帧数据期间禁轮询电量计（doc/02 §2.1）。
 */
#include <Arduino.h>
#include <esp_sleep.h>
#include <string.h>

#include "board_config.h"
#include "hal_interfaces.h"
#include "hal_shared.h"

static const char *TAG = "hal_power";

/* MAX17043 寄存器 */
#define MAX17043_REG_VCELL 0x02 /* 16 位，78.125µV/LSB */
#define MAX17043_REG_SOC 0x04   /* 16 位，1/256 %/LSB */

/* 低电判定阈值（doc/02 §3.6） */
#define LOW_VOLT_THRESHOLD_MV 3400
#define LOW_SOC_THRESHOLD 5

/* 无操作降功耗阈值 */
#define IDLE_TO_DIM_MS 60000
#define IDLE_TO_SLEEP_MS 180000

/* 浅休眠唤醒：GPIO 0 下压（低有效） */
#define WAKE_GPIO JOY_PIN_SW

static uint32_t s_last_activity_ms = 0;
static bool s_initialized = false;

/* ---------- 电量读取 ---------- */

hal_status_t hal_power_read_battery(float *out_volts, uint8_t *out_soc)
{
    if (out_volts == NULL || out_soc == NULL) {
        return HAL_ERR_INVALID;
    }
    *out_volts = 0.0f;
    *out_soc = 0;

    /* 总线锁：与摄像头 SCCB 互斥，拍照期间读电量会被挡在门外 */
    if (!hal_i2c_acquire()) {
        return HAL_ERR_TIMEOUT;
    }

    uint8_t raw[2];
    bool ok = hal_i2c_read_reg8_raw(POWER_I2C_ADDR, MAX17043_REG_VCELL, raw, sizeof(raw));
    uint16_t vcell = 0;
    if (ok) {
        vcell = (uint16_t)((raw[0] << 8) | raw[1]);
    }

    uint16_t soc_raw = 0;
    ok = ok && hal_i2c_read_reg8_raw(POWER_I2C_ADDR, MAX17043_REG_SOC, raw, sizeof(raw));
    if (ok) {
        soc_raw = (uint16_t)((raw[0] << 8) | raw[1]);
    }

    hal_i2c_release();

    if (!ok) {
        return HAL_ERR_IO;
    }

    /* 78.125µV/LSB → 毫伏 */
    uint32_t mv = (uint32_t)vcell * 78125UL / 1000000UL;
    /* SOC 1/256 %/LSB → 百分比，钳位 0~100 */
    uint32_t pct = (uint32_t)soc_raw / 256UL;
    if (pct > 100) {
        pct = 100;
    }

    *out_volts = (float)mv / 1000.0f;
    *out_soc = (uint8_t)pct;
    return HAL_OK;
}

/* ---------- 低功耗 ---------- */

hal_status_t hal_power_enter_light_sleep(void)
{
    Serial.printf("[%s] entering light sleep\n", TAG);

    /* 背光先关，进入浅休眠后外设电源域掉电，唤醒需重建 */
    hal_display_set_backlight(0);

    /* GPIO 0 下压唤醒（低有效）+ RTC 定时唤醒（课程闹钟） */
    esp_sleep_enable_ext0_wakeup((gpio_num_t)WAKE_GPIO, 0);
    esp_sleep_enable_timer_wakeup(60ULL * 1000000ULL);

    esp_light_sleep_start();

    /* 唤醒后重建外设：SPI 屏 / I2C / I2S 状态（doc/02 §3.6 强制） */
    hal_recover_after_sleep();
    return HAL_OK;
}

hal_status_t hal_power_enter_deep_sleep(void)
{
    Serial.printf("[%s] entering deep sleep\n", TAG);
    hal_display_set_backlight(0);
    esp_sleep_enable_ext0_wakeup((gpio_num_t)WAKE_GPIO, 0);
    esp_deep_sleep_start();
    /* 不会返回 */
    return HAL_OK;
}

/* ---------- 生命周期 ---------- */

hal_status_t hal_power_init(void)
{
    s_last_activity_ms = millis();
    s_initialized = true;

    float v = 0.0f;
    uint8_t soc = 0;
    if (hal_power_read_battery(&v, &soc) == HAL_OK) {
        Serial.printf("[%s] battery %.3fV soc %d%%\n", TAG, (double)v, (int)soc);
        /* 低电保护（doc/02 §3.6）：电压持续低于 3.40V（SOC <= 5%）时
         * 提示 3 秒后强制深度睡眠，保护锂电池不过放。 */
        uint32_t mv = (uint32_t)(v * 1000.0f);
        if (mv < LOW_VOLT_THRESHOLD_MV || soc <= LOW_SOC_THRESHOLD) {
            ui_command_t cmd = {};
            cmd.kind = 1;
            snprintf(cmd.text, sizeof(cmd.text), "电量耗尽，请充电");
            xQueueSend(hal_ui_command_queue(), &cmd, 0);
            Serial.printf("[%s] low battery -> deep sleep in 3s\n", TAG);
            delay(3000);
            hal_power_enter_deep_sleep();
        }
    } else {
        Serial.printf("[%s] battery read failed\n", TAG);
    }
    return HAL_OK;
}

/* ---------- 休眠唤醒后的外设恢复（doc/02 §3.6 强制） ---------- */

/**
 * 浅休眠唤醒后重建外设状态：
 *   - SPI 屏：重新初始化（背光 PWM 也要重建）
 *   - I2C 总线：驱动句柄在休眠中失效，重新安装
 *   - I2S 音频：复位状态机，下次用时再装驱动
 */
hal_status_t hal_recover_after_sleep(void)
{
    Serial.println("[hal_power] recovering peripherals after sleep");

    hal_i2c_reinstall();

    hal_status_t st = hal_display_init();
    if (st != HAL_OK) {
        Serial.printf("[%s] display recover failed\n", TAG);
    } else {
        hal_display_set_backlight(2);
    }

    hal_audio_init();
    return HAL_OK;
}

/* ---------- 空闲降功耗（内部，doc/02 §3.6） ---------- */

void hal_power_note_activity(void)
{
    s_last_activity_ms = millis();
}

void hal_power_tick_idle(void)
{
    if (!s_initialized) {
        return;
    }
    uint32_t idle = millis() - s_last_activity_ms;
    if (idle < IDLE_TO_DIM_MS) {
        return;
    }
    if (idle < IDLE_TO_SLEEP_MS) {
        /* 60 秒无操作：背光降到最低档 */
        static bool dimmed = false;
        if (!dimmed) {
            hal_display_set_backlight(1);
            dimmed = true;
        }
        return;
    }
    /* 180 秒无操作：关背光并进入浅休眠 */
    hal_power_enter_light_sleep();
    /* 醒来后重置计时，避免立刻再次入睡 */
    s_last_activity_ms = millis();
}
