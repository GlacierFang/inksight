/**
 * hal_shared.cpp —— HAL 层共享设施实现（总线锁 + 跨核队列 + 共享 I2C 总线）
 *
 * 契约出处: abbot/doc/02 §2.1 (I2C 互斥) / §4.2 (跨核队列规范)
 *
 * 共享总线设计（doc/02 §2.1）：
 *   OV5640 的 SCCB 与 MAX17043 物理共享 I2C_NUM_0（GPIO 4/5），频率锁定 100kHz。
 *   全机**只有一条 I2C 驱动实例** —— 由本文件安装。
 *   摄像头侧通过 camera_config.pin_sccb_sda = -1 + sccb_i2c_port = I2C_NUM_0
 *   复用这条已配置好的总线（SDK 的 esp_camera.h 明确写着
 *   "If pin_sccb_sda is -1, use the already configured I2C bus by number"），
 *   不会出现两个驱动实例抢同一条物理线。
 *   因此所有跨设备访问都必须持 g_i2c_bus_mutex —— 这把锁不是"防打架"的装饰，
 *   而是保证「同一时刻只有一个人在驱动这条物理总线」。
 *
 * 底层用 IDF 的 legacy i2c master API（i2c_master_write_to_device 等）：
 * 它与 esp32-camera 的 SCCB 走的是同一套 legacy 驱动，因此「摄像头复用我们
 * 装的这条总线」这件事才成立。
 */
#include "hal_shared.h"

#include <Arduino.h>
#include <driver/i2c.h>

#include "board_config.h"

/* ---------- 全局 I2C 总线互斥锁 ---------- */
static SemaphoreHandle_t s_i2c_mutex = NULL;

/* ---------- 跨核队列 ---------- */
static QueueHandle_t s_q_joystick = NULL;
static QueueHandle_t s_q_ui = NULL;
static QueueHandle_t s_q_photo = NULL;
static QueueHandle_t s_q_audio = NULL;
static QueueHandle_t s_q_net = NULL;
static QueueHandle_t s_q_storage = NULL;

SemaphoreHandle_t hal_i2c_mutex(void)
{
    return s_i2c_mutex;
}

QueueHandle_t hal_joystick_queue(void) { return s_q_joystick; }
QueueHandle_t hal_ui_command_queue(void) { return s_q_ui; }
QueueHandle_t hal_photo_request_queue(void) { return s_q_photo; }
QueueHandle_t hal_audio_pcm_queue(void) { return s_q_audio; }
QueueHandle_t hal_net_request_queue(void) { return s_q_net; }
QueueHandle_t hal_storage_req_queue(void) { return s_q_storage; }

/** 安装共享 I2C 总线：GPIO 4/5 @ 100kHz（doc/02 §2.1 强制） */
static void hal_i2c_install(void)
{
    i2c_config_t conf;
    memset(&conf, 0, sizeof(conf));
    conf.mode = I2C_MODE_MASTER;
    conf.sda_io_num = POWER_I2C_SDA;
    conf.scl_io_num = POWER_I2C_SCL;
    conf.sda_pullup_en = true;
    conf.scl_pullup_en = true;
    conf.master.clk_speed = I2C_BUS_FREQ_HZ;

    esp_err_t err = i2c_param_config(I2C_NUM_0, &conf);
    if (err != ESP_OK) {
        Serial.printf("[hal_shared] i2c_param_config failed: %s\n", esp_err_to_name(err));
        return;
    }
    err = i2c_driver_install(I2C_NUM_0, I2C_MODE_MASTER, 0, 0, 0);
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        /* INVALID_STATE = 已被安装（例如摄像头先初始化了），可继续复用 */
        Serial.printf("[hal_shared] i2c_driver_install failed: %s\n", esp_err_to_name(err));
    }
}

void hal_i2c_reinstall(void)
{
    /* 浅休眠后驱动句柄已失效：先删再装，保证引脚与频率重新落到配置上 */
    i2c_driver_delete(I2C_NUM_0);
    hal_i2c_install();
}

/**
 * 一次性初始化：总线锁 + 队列 + 共享 I2C 总线。
 * 必须在任何 HAL 初始化之前调用（setup 最开头）。
 */
void hal_shared_init(void)
{
    if (s_i2c_mutex == NULL) {
        s_i2c_mutex = xSemaphoreCreateMutex();
    }

    if (s_q_joystick == NULL) {
        s_q_joystick = xQueueCreate(Q_JOYSTICK_LEN, sizeof(joystick_event_t));
    }
    if (s_q_ui == NULL) {
        s_q_ui = xQueueCreate(Q_UI_COMMAND_LEN, sizeof(ui_command_t));
    }
    if (s_q_photo == NULL) {
        s_q_photo = xQueueCreate(Q_PHOTO_REQUEST_LEN, sizeof(photo_request_t));
    }
    if (s_q_audio == NULL) {
        s_q_audio = xQueueCreate(Q_AUDIO_PCM_LEN, sizeof(audio_msg_t));
    }
    if (s_q_net == NULL) {
        s_q_net = xQueueCreate(Q_NET_REQUEST_LEN, sizeof(net_request_t));
    }
    if (s_q_storage == NULL) {
        s_q_storage = xQueueCreate(Q_STORAGE_REQ_LEN, sizeof(storage_req_t));
    }

    hal_i2c_install();
}

bool hal_i2c_acquire(void)
{
    if (s_i2c_mutex == NULL) {
        return false;
    }
    return xSemaphoreTake(s_i2c_mutex, pdMS_TO_TICKS(HAL_I2C_LOCK_TIMEOUT_MS)) == pdTRUE;
}

void hal_i2c_release(void)
{
    if (s_i2c_mutex != NULL) {
        xSemaphoreGive(s_i2c_mutex);
    }
}

/* ---------- 底层裸操作：调用方必须已持锁 ---------- */

bool hal_i2c_write_raw(uint8_t dev, const uint8_t *buf, size_t len)
{
    return i2c_master_write_to_device(I2C_NUM_0, dev, buf, len,
                                       pdMS_TO_TICKS(HAL_I2C_LOCK_TIMEOUT_MS)) == ESP_OK;
}

bool hal_i2c_read_raw(uint8_t dev, uint8_t *buf, size_t len)
{
    return i2c_master_read_from_device(I2C_NUM_0, dev, buf, len,
                                        pdMS_TO_TICKS(HAL_I2C_LOCK_TIMEOUT_MS)) == ESP_OK;
}

bool hal_i2c_write_read_raw(uint8_t dev, const uint8_t *wbuf, size_t wlen, uint8_t *rbuf,
                            size_t rlen)
{
    /* 写后重复起始再读 —— MAX17043 读寄存器的时序 */
    return i2c_master_write_read_device(I2C_NUM_0, dev, wbuf, wlen, rbuf, rlen,
                                         pdMS_TO_TICKS(HAL_I2C_LOCK_TIMEOUT_MS)) == ESP_OK;
}

bool hal_i2c_write_reg16_raw(uint8_t dev, uint16_t reg, uint8_t val)
{
    /* 两字节寄存器号（高字节在前）+ 一字节数据 —— 对齐上游 SCCB_Write16 */
    uint8_t buf[3];
    buf[0] = (uint8_t)(reg >> 8);
    buf[1] = (uint8_t)(reg & 0xFF);
    buf[2] = val;
    return hal_i2c_write_raw(dev, buf, sizeof(buf));
}

bool hal_i2c_read_reg16_raw(uint8_t dev, uint16_t reg, uint8_t *val)
{
    /* 写两字节寄存器号后另起一次读事务取一字节 —— 对齐上游 SCCB_Read16 */
    uint8_t addr[2];
    addr[0] = (uint8_t)(reg >> 8);
    addr[1] = (uint8_t)(reg & 0xFF);
    if (!hal_i2c_write_raw(dev, addr, sizeof(addr))) {
        return false;
    }
    return hal_i2c_read_raw(dev, val, 1);
}

bool hal_i2c_read_reg8_raw(uint8_t dev, uint8_t reg, uint8_t *out, size_t len)
{
    /* 写一字节寄存器号 + 重复起始读 —— MAX17043 的读寄存器时序 */
    return hal_i2c_write_read_raw(dev, &reg, 1, out, len);
}
