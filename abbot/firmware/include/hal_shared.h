/**
 * hal_shared.h —— HAL 层内部共享设施
 *
 * 契约出处: abbot/doc/02 §2.1 (I2C 互斥)、§3.4 (摇杆事件)、§4.2 (跨核队列)
 *
 * doc/02 §六的 hal_interfaces.h 是「UI 层可见」的唯一契约，本文件是 HAL 内部实现细节，
 * 不新增对外行为 —— 只把契约里已经写死的锁、队列、事件类型落到代码。
 */
#pragma once

#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/semphr.h>

#include "hal_interfaces.h"
#include "net_api.h"
#include "storage_io.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ===================== §2.1 共享 I2C 总线 =====================
 * OV5640(SCCB) 与 MAX17043 **物理共享** I2C_NUM_0 (GPIO 4/5)，频率锁定 100kHz。
 * 全机只有一条 I2C 驱动实例（本文件安装），摄像头侧通过
 * camera_config.pin_sccb_sda = -1 + sccb_i2c_port = I2C_NUM_0 复用它，
 * 因此不会出现两个驱动实例抢同一条物理总线。
 *
 * 任何跨设备访问（读电量、写 OV5640 寄存器、跑 AF）都必须先取锁，
 * 取锁超时 100ms —— 取不到说明对面还在用总线，本次操作直接放弃，不阻塞等待。
 *
 * **锁的粒度是「一段操作」而不是「一次事务」**：底层 _raw 系列函数**不取锁**，
 * 调用方自己拿锁。AF 触发 + 状态轮询必须整段持锁，否则电量读会插进对焦序列中间。
 */
SemaphoreHandle_t hal_i2c_mutex(void);
bool hal_i2c_acquire(void);
void hal_i2c_release(void);
#define HAL_I2C_LOCK_TIMEOUT_MS 100

/* ---------- 底层 I2C 裸操作（调用方必须已持 g_i2c_bus_mutex） ---------- */
bool hal_i2c_write_raw(uint8_t dev, const uint8_t *buf, size_t len);
bool hal_i2c_read_raw(uint8_t dev, uint8_t *buf, size_t len);
/* 写后重复起始再读（MAX17043 读寄存器用这个） */
bool hal_i2c_write_read_raw(uint8_t dev, const uint8_t *wbuf, size_t wlen, uint8_t *rbuf,
                            size_t rlen);
/* 16 位寄存器写：两字节寄存器号（高字节在前）+ 一字节数据 —— OV5640 用 */
bool hal_i2c_write_reg16_raw(uint8_t dev, uint16_t reg, uint8_t val);
/* 16 位寄存器读：写两字节寄存器号后另起一次读事务取一字节 —— OV5640 用 */
bool hal_i2c_read_reg16_raw(uint8_t dev, uint16_t reg, uint8_t *val);
/* 8 位寄存器读（写寄存器号 + 重复起始读）—— MAX17043 用 */
bool hal_i2c_read_reg8_raw(uint8_t dev, uint8_t reg, uint8_t *out, size_t len);

/**
 * 浅休眠唤醒后重建 I2C 驱动实例（休眠期间外设电源域掉电，驱动状态已失效）。
 */
void hal_i2c_reinstall(void);

/**
 * 一次性初始化：总线锁 + 跨核队列 + 共享 I2C 总线。
 * 必须在任何 HAL 初始化之前调用（setup 最开头）。
 */
void hal_shared_init(void);

/* ===================== §3.4 摇杆事件 =====================
 * 按压归类只在 HAL 裁定一次:
 *   CLICK              30ms ~ 600ms(不含 600) 于释放瞬间判定
 *   LONG_PRESS_START   按住 >= 600ms 时触发
 *   LONG_PRESS_END     松开瞬间触发(UI 在 END 上执行退出动作)
 * 不产出双击事件 —— doc/02 §3.4 已废除 250ms 双击窗口。
 */
typedef enum {
    JOY_EVENT_NONE = 0,
    JOY_EVENT_UP,
    JOY_EVENT_DOWN,
    JOY_EVENT_LEFT,
    JOY_EVENT_RIGHT,
    JOY_EVENT_CLICK,
    JOY_EVENT_LONG_PRESS_START,
    JOY_EVENT_LONG_PRESS_END,
    JOY_EVENT_REPEAT /* 连发滚动: 首次触发后 400ms 进入, 每 150ms 一次 */
} joystick_event_type_t;

typedef struct {
    joystick_event_type_t type;
    uint32_t hold_ms; /* SW 按下时长(方向事件为 0) */
    int32_t delta;    /* 方向事件的 ADC 偏移量(相对动态中点) */
} joystick_event_t;

/* ===================== §4.2 跨核队列 ===================== */
#define Q_JOYSTICK_LEN 8
#define Q_UI_COMMAND_LEN 16
#define Q_PHOTO_REQUEST_LEN 4
#define Q_AUDIO_PCM_LEN 8
#define Q_NET_REQUEST_LEN 8
#define Q_STORAGE_REQ_LEN 8 /* task_storage 单线程串行化 TF 卡访问的投递口 */

QueueHandle_t hal_joystick_queue(void);
QueueHandle_t hal_ui_command_queue(void);
QueueHandle_t hal_photo_request_queue(void);
QueueHandle_t hal_audio_pcm_queue(void);
QueueHandle_t hal_net_request_queue(void);
/* hal_storage_req_queue() 与 storage_req_t 已在 storage_io.h 里声明 —— 
 * 那是 UI 读卡层（纯 C）唯一需要看到的存储契约。 */

/* ---------- 内部消息类型 ---------- */
typedef struct {
    uint8_t kind; /* 1=提示 2=网络应答 3=刷新页面 4=文件已落盘 */
    int32_t biz_code; /* kind=2 时的业务码（0 成功；-1 网络/协议失败；4004 学段未设置等） */
    char text[128];   /* 提示文案，或网络应答的中文摘要 */
    /* kind=2 时：解密后的业务明文（堆内存，**所有权随队列转移给接收方，由 UI 释放） */
    char *payload;
    uint32_t payload_len;
} ui_command_t;

typedef struct {
    uint8_t want_uxga; /* 1=UXGA 抓拍, 0=HVGA 预览 */
    uint8_t with_voice; /* 边说边拍 */
    char session_id[32]; /* 本题会话号（doc/05 §5.1：同题追问沿用同一个） */
} photo_request_t;

typedef struct {
    uint8_t op;     /* 1=播放 PCM 分块 2=开始录音 3=结束录音 4=播放 WAV 文件
                     * 5=播放设备端合成提示音（freq_hz / duration_ms） */
    uint16_t len;   /* op=1 时的采样点数 */
    int16_t samples[320];
    char path[72]; /* op=4 时的文件路径 */
    uint16_t freq_hz;     /* op=5 时的提示音频率 */
    uint16_t duration_ms; /* op=5 时的提示音时长 */
} audio_msg_t;

/* storage_asset_job_t / storage_req_t / hal_storage_req_queue() 已移入 storage_io.h：
 * 那里只依赖 FreeRTOS 与标准 C，UI 侧纯 C 读卡层可以直接包含。 */

/* ===================== HAL 内部互调（不在 §六 对外契约里） ===================== */
/* LVGL 绘制缓冲 -> 屏幕的像素搬运 (RGB565)。UI 层不碰 SPI, 只能走这里。 */
void hal_display_blit_rgb565(int x, int y, int w, int h, const uint16_t *pixels);
hal_status_t hal_storage_write_binary(const char *path, const uint8_t *data, uint32_t len);
void hal_camera_release_jpeg(uint8_t *jpg);
/* 取一段 PCM：>=0 为实际取到的采样点数；<0 为 hal_status_t 错误码 */
int hal_audio_read_chunk(int16_t *dst, size_t samples);
hal_status_t hal_audio_write_pcm(const int16_t *pcm, size_t samples);
void hal_audio_set_muted(bool muted);

/* ---------- FreeRTOS 双核任务启动（doc/02 §4.1） ---------- */
void abbot_tasks_start(void);

/* ---------- §3.6 低功耗状态机（内部） ---------- */
/* 有用户操作时调用，重置空闲计时 */
void hal_power_note_activity(void);
/* 由 Core 1 的监控任务周期调用（建议 1Hz），推进 60s/180s 两级降功耗 */
void hal_power_tick_idle(void);

#ifdef __cplusplus
}
#endif
