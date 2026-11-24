/**
 * hal_camera.cpp —— OV5640 拍题与自动对焦
 *
 * 契约: abbot/doc/02 §3.2
 *   - XCLK = 12MHz（降频提升长排线抗干扰，消除画面绿条/花屏）
 *   - PIXFORMAT_JPEG，jpeg_quality = 10（高质量压缩）
 *   - fb_count = 1，fb_location = CAMERA_FB_IN_PSRAM
 *   - 两级分辨率：预览 FRAMESIZE_HVGA；抓拍切 FRAMESIZE_UXGA，
 *     丢弃切换后的前 2 帧缓冲（等自动曝光/白平衡稳定），抓第 3 帧
 *   - VCM 自动对焦固件（约 4KB 寄存器序列）以 const uint8_t 数组编译进固件 Flash，
 *     开机经 SCCB 写入摄像头寄存器；**严禁从 TF 卡动态读取加载补丁**
 *   - 触发单次对焦（0x3022 = 0x03），轮询状态寄存器 0x3029，
 *     **1500ms 强制超时熔断** —— 超时立即终止等待并直接抓拍，绝不卡死
 *
 * 总线设计：OV5640 的 SCCB 与 MAX17043 物理共享 I2C_NUM_0（GPIO 4/5）。
 * 这里让摄像头**复用已配置好的共享总线**（pin_sccb_sda = -1 + sccb_i2c_port），
 * 而不是自己再装一个驱动 —— 全机只有一条 I2C 驱动实例，
 * 所有跨设备访问由 g_i2c_bus_mutex 串行化。
 */
#include <Arduino.h>
#include <driver/ledc.h>
#include <driver/i2c.h>
#include <esp_camera.h>
#include <esp_heap_caps.h>
#include <sensor.h>
#include <string.h>

#include "board_config.h"
#include "hal_interfaces.h"
#include "hal_shared.h"
#include "ov5640_af_firmware.h"

static const char *TAG = "hal_camera";

/* LEDC 通道分配：背光占 channel 0（见 hal_display.cpp 的 BL_PWM_CHANNEL），
 * 摄像头 XCLK 另取 channel 2 / timer 1，两者互不干扰。 */
#define CAM_LEDC_CHANNEL LEDC_CHANNEL_2
#define CAM_LEDC_TIMER LEDC_TIMER_1

/* OV5640 AF 相关寄存器（对齐上游 ov5640_af.c 与 doc/02 §3.2） */
#define OV5640_CMD_MAIN 0x3022  /* AF 命令 */
#define OV5640_CMD_ACK 0x3023
#define OV5640_CMD_PARA0 0x3024
#define OV5640_CMD_PARA4 0x3028
#define OV5640_CMD_FW_STATUS 0x3029 /* AF 固件状态 */
#define OV5640_MCU_CTRL 0x3000 /* AF MCU 控制 */

#define OV5640_AF_TRIG_SINGLE 0x03
#define OV5640_FW_STATUS_FOCUSED 0x10

/* AF 固件装载区起始地址 */
#define OV5640_AF_FW_BASE 0x8000

/* 对焦轮询上限（doc/02 §3.2 强制） */
#define AF_POLL_TIMEOUT_MS 1500

static bool s_initialized = false;
static uint8_t s_sensor_addr = 0;

/* ---------- 底层寄存器访问（16 位寄存器号 + 1 字节数据） ----------
 * 裸操作，不自己取锁 —— 锁由调用方整段持有（AF 触发 + 状态轮询必须在同一把锁里，
 * 否则电量读会插进对焦序列中间）。 */

static bool cam_write_reg(uint16_t reg, uint8_t val)
{
    return hal_i2c_write_reg16_raw(s_sensor_addr, reg, val);
}

static bool cam_read_reg(uint16_t reg, uint8_t *val)
{
    return hal_i2c_read_reg16_raw(s_sensor_addr, reg, val);
}

/**
 * 装载 VCM 自动对焦固件（编译进 Flash 的常量数组）。
 * 顺序对齐上游 ov5640_af.c：复位 AF MCU → 逐字节写入 0x8000 起 →
 * 清命令寄存器 → 置状态寄存器 → 启动 AF MCU。
 */
static void cam_load_af_firmware(void)
{
    /* 复位 AF MCU */
    cam_write_reg(OV5640_MCU_CTRL, 0x20);

    const uint16_t n = (uint16_t)(sizeof(ABBOT_OV5640_AF_FIRMWARE) / sizeof(ABBOT_OV5640_AF_FIRMWARE[0]));
    for (uint16_t i = 0; i < n; i++) {
        cam_write_reg((uint16_t)(OV5640_AF_FW_BASE + i), ABBOT_OV5640_AF_FIRMWARE[i]);
    }

    /* 清命令与参数寄存器 */
    cam_write_reg(OV5640_CMD_MAIN, 0x00);
    cam_write_reg(OV5640_CMD_ACK, 0x00);
    for (uint16_t r = OV5640_CMD_PARA0; r <= OV5640_CMD_PARA4; r++) {
        cam_write_reg(r, 0x00);
    }
    cam_write_reg(OV5640_CMD_FW_STATUS, 0x7f);

    /* 启动 AF MCU */
    cam_write_reg(OV5640_MCU_CTRL, 0x00);
    Serial.printf("[%s] AF firmware loaded: %u bytes\n", TAG, (unsigned)n);
}

/**
 * 触发单次对焦并等待锁定。
 * 返回 true = 已锁定；false = 1500ms 超时（调用方必须照常抓拍，不得卡死）。
 */
static bool cam_af_focus(void)
{
    const uint32_t t0 = millis();
    if (!cam_write_reg(OV5640_CMD_MAIN, OV5640_AF_TRIG_SINGLE)) {
        return false;
    }
    while (millis() - t0 < AF_POLL_TIMEOUT_MS) {
        uint8_t st = 0;
        if (cam_read_reg(OV5640_CMD_FW_STATUS, &st) && st == OV5640_FW_STATUS_FOCUSED) {
            return true;
        }
        delay(10);
    }
    Serial.printf("[%s] AF timeout %dms -> capture anyway\n", TAG, AF_POLL_TIMEOUT_MS);
    return false;
}

/* ---------- 对外接口 ---------- */

hal_status_t hal_camera_init(void)
{
    if (s_initialized) {
        return HAL_OK;
    }

    camera_config_t config = {};
    config.ledc_channel = CAM_LEDC_CHANNEL;
    config.ledc_timer = CAM_LEDC_TIMER;
    config.pin_d0 = CAM_PIN_D0;
    config.pin_d1 = CAM_PIN_D1;
    config.pin_d2 = CAM_PIN_D2;
    config.pin_d3 = CAM_PIN_D3;
    config.pin_d4 = CAM_PIN_D4;
    config.pin_d5 = CAM_PIN_D5;
    config.pin_d6 = CAM_PIN_D6;
    config.pin_d7 = CAM_PIN_D7;
    config.pin_vsync = CAM_PIN_VSYNC;
    config.pin_href = CAM_PIN_HREF;
    config.pin_pclk = CAM_PIN_PCLK;
    config.pin_xclk = CAM_PIN_XCLK;
    config.pin_pwdn = -1;
    config.pin_reset = -1;
    config.xclk_freq_hz = CAM_XCLK_HZ;
    /* 复用共享 I2C_NUM_0（由 hal_shared_init 统一安装，GPIO 4/5 @100kHz） */
    config.pin_sccb_sda = -1;
    config.pin_sccb_scl = -1;
    config.sccb_i2c_port = I2C_NUM_0;
    config.pixel_format = PIXFORMAT_JPEG;
    config.frame_size = FRAMESIZE_HVGA;
    config.jpeg_quality = 10;
    config.fb_count = 1;
    config.fb_location = CAMERA_FB_IN_PSRAM;
    config.grab_mode = CAMERA_GRAB_LATEST;

    esp_err_t err = esp_camera_init(&config);
    if (err != ESP_OK) {
        Serial.printf("[%s] esp_camera_init failed: %s\n", TAG, esp_err_to_name(err));
        return HAL_ERR_IO;
    }

    sensor_t *s = esp_camera_sensor_get();
    if (s == NULL) {
        Serial.printf("[%s] no sensor\n", TAG);
        return HAL_ERR_IO;
    }
    s_sensor_addr = s->slv_addr;

    /* AF 固件装载（编译进 Flash，不读 TF 卡） */
    if (!hal_i2c_acquire()) {
        Serial.printf("[%s] i2c busy, skip AF firmware load\n", TAG);
        return HAL_ERR_TIMEOUT;
    }
    cam_load_af_firmware();
    hal_i2c_release();

    s_initialized = true;
    Serial.printf("[%s] ready, sensor addr=0x%02X, xclk=%dHz\n", TAG, s_sensor_addr,
                  (int)CAM_XCLK_HZ);
    return HAL_OK;
}

hal_status_t hal_camera_start_preview(void)
{
    if (!s_initialized) {
        return HAL_ERR_INVALID;
    }
    sensor_t *s = esp_camera_sensor_get();
    if (s == NULL || s->set_framesize == NULL) {
        return HAL_ERR_IO;
    }
    s->set_framesize(s, FRAMESIZE_HVGA);
    return HAL_OK;
}

hal_status_t hal_camera_stop_preview(void)
{
    /* 停流由驱动侧处理：不再取帧即停。保留接口以满足 doc/02 §六 契约。 */
    return s_initialized ? HAL_OK : HAL_ERR_INVALID;
}

hal_status_t hal_camera_capture_jpeg(uint8_t **out_jpg, size_t *out_len)
{
    if (out_jpg == NULL || out_len == NULL) {
        return HAL_ERR_INVALID;
    }
    *out_jpg = NULL;
    *out_len = 0;
    if (!s_initialized) {
        return HAL_ERR_INVALID;
    }

    sensor_t *s = esp_camera_sensor_get();
    if (s == NULL || s->set_framesize == NULL) {
        return HAL_ERR_IO;
    }

    /* 对焦：持总线锁，1500ms 熔断，超时照常抓拍 */
    if (hal_i2c_acquire()) {
        cam_af_focus();
        hal_i2c_release();
    }

    /* 切 UXGA 高清分辨率 */
    s->set_framesize(s, FRAMESIZE_UXGA);

    /* 丢弃前 2 帧缓冲，等自动曝光/白平衡稳定；抓第 3 帧。
     * 取帧有上限 —— 驱动异常时不能无限重试卡死任务。 */
    camera_fb_t *fb = NULL;
    int frame_no = 0;
    for (int attempt = 0; attempt < 30 && frame_no < 3; attempt++) {
        camera_fb_t *f = esp_camera_fb_get();
        if (f == NULL) {
            delay(20);
            continue;
        }
        if (frame_no < 2) {
            esp_camera_fb_return(f); /* 丢弃 */
            frame_no++;
        } else {
            fb = f;
            frame_no++;
        }
    }
    if (fb == NULL) {
        Serial.printf("[%s] no frame captured\n", TAG);
        s->set_framesize(s, FRAMESIZE_HVGA);
        return HAL_ERR_TIMEOUT;
    }

    /* 帧缓冲在 PSRAM，但归驱动所有 —— 拷进自有 PSRAM 缓冲再归还 */
    uint8_t *copy = (uint8_t *)heap_caps_malloc(fb->len, MALLOC_CAP_SPIRAM);
    if (copy == NULL) {
        esp_camera_fb_return(fb);
        s->set_framesize(s, FRAMESIZE_HVGA);
        return HAL_ERR_NO_SPACE;
    }
    memcpy(copy, fb->buf, fb->len);
    size_t len = fb->len;
    esp_camera_fb_return(fb);

    /* 切回预览分辨率 */
    s->set_framesize(s, FRAMESIZE_HVGA);

    *out_jpg = copy;
    *out_len = len;
    return HAL_OK;
}

void hal_camera_release_jpeg(uint8_t *jpg)
{
    if (jpg != NULL) {
        heap_caps_free(jpg);
    }
}
