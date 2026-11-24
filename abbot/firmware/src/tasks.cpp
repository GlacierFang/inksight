/**
 * tasks.cpp —— FreeRTOS 双核任务骨架
 *
 * 契约: abbot/doc/02 §4.1（双核硬划分）/ §4.2（跨核队列规范）
 *
 *   Core 1（界面核）
 *     task_joystick —— 50Hz 摇杆采样（方向/短按/长按归类，doc/02 §3.4）
 *     task_ui_fsm   —— 事件分发与页面路由骨架
 *   Core 0（IO 核）
 *     task_camera   —— 取景预览 / AF 对焦 / UXGA 抓拍
 *     task_audio    —— I2S 半双工录音与 TTS 流式播放
 *     task_storage  —— TF 卡读写（单线程串行化，不加锁）
 *     task_net_worker —— 网络请求投递（HTTP/TLS 传输尚未接入）
 *
 * 铁律（doc/02 §4.1）：
 *   - Core 1 任务严禁执行阻塞式网络或 TF 卡 IO
 *   - Core 0 产出的结果一律经队列投递回 Core 1，严禁 Core 0 直接调 LVGL
 *   - 任务栈分配在内部 SRAM，大块缓冲走 PSRAM
 */
#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/task.h>
#include <string.h>

#include "board_config.h"
#include "hal_interfaces.h"
#include "hal_shared.h"
#include "lvgl.h"
#include "ui_display_port.h"
#include "ui_router.h"

static const char *TAG = "tasks";

/* ================= Core 1：界面核 ================= */

/** 50Hz 摇杆采样：方向 + 按压归类，全部在 HAL 内裁定 */
static void task_joystick(void *arg)
{
    (void)arg;
    TickType_t last_wake = xTaskGetTickCount();
    for (;;) {
        hal_joystick_poll();
        /* 50Hz —— doc/02 §3.4 的连发时序（400ms 进入 / 150ms 一次）依赖这个节拍 */
        vTaskDelayUntil(&last_wake, pdMS_TO_TICKS(20));
    }
}

/** 界面核：LVGL 渲染 + 事件分发 + 页面路由与浮层优先级 */
static void task_ui_fsm(void *arg)
{
    (void)arg;
    QueueHandle_t q = hal_joystick_queue();
    QueueHandle_t qcmd = hal_ui_command_queue();

    /* LVGL 必须在**使用它的那个任务**里初始化 —— 页面对象与 lv_timer_handler
     * 必须同属一个任务, 否则对象树会跨任务踩内存。 */
    ui_display_port_init();
    ui_router_init();

    TickType_t last_wake = xTaskGetTickCount();
    for (;;) {
        joystick_event_t ev;
        if (q != NULL && xQueueReceive(q, &ev, pdMS_TO_TICKS(10)) == pdTRUE) {
            /* 有用户操作 —— 重置低功耗空闲计时 */
            hal_power_note_activity();
            /* 事件交给 LVGL 页面状态机（doc/03 §3.2 六手势宪法） */
            ui_router_handle_joystick(&ev);
        }

        ui_command_t cmd;
        if (qcmd != NULL && xQueueReceive(qcmd, &cmd, 0) == pdTRUE) {
            if (cmd.kind == 1) {
                ui_toast(cmd.text);
            }
        }

        lv_tick_inc(5);
        lv_timer_handler();
        vTaskDelayUntil(&last_wake, pdMS_TO_TICKS(5));
    }
}

/* ================= Core 0：IO 核 ================= */

/** 拍照任务：AF 对焦 → UXGA 抓拍 → 落盘 → 队列投回 Core 1 */
static void task_camera(void *arg)
{
    (void)arg;
    QueueHandle_t q = hal_photo_request_queue();
    for (;;) {
        photo_request_t req;
        if (q == NULL || xQueueReceive(q, &req, portMAX_DELAY) != pdTRUE) {
            continue;
        }
        hal_power_note_activity();

        uint8_t *jpg = NULL;
        size_t len = 0;
        if (hal_camera_capture_jpeg(&jpg, &len) != HAL_OK || jpg == NULL) {
            ui_command_t cmd = {};
            cmd.kind = 1;
            snprintf(cmd.text, sizeof(cmd.text), "拍照失败，请重试");
            xQueueSend(hal_ui_command_queue(), &cmd, 0);
            continue;
        }

        /* 备份到 TF：doc/04 §3.5 的离线任务媒体命名法 */
        char path[72];
        snprintf(path, sizeof(path), "/outbox/task_%u.jpg", (unsigned)millis());
        if (hal_storage_write_binary(path, jpg, (uint32_t)len) == HAL_OK) {
            ui_command_t cmd = {};
            cmd.kind = 4;
            snprintf(cmd.text, sizeof(cmd.text), "%s", path);
            xQueueSend(hal_ui_command_queue(), &cmd, 0);
        }
        hal_camera_release_jpeg(jpg);
    }
}

/** 音频任务：录音分块与 TTS 流式播放 */
static void task_audio(void *arg)
{
    (void)arg;
    QueueHandle_t q = hal_audio_pcm_queue();
    for (;;) {
        audio_msg_t msg;
        if (q == NULL || xQueueReceive(q, &msg, portMAX_DELAY) != pdTRUE) {
            continue;
        }
        hal_power_note_activity();
        switch (msg.op) {
        case 1: /* 播放 PCM 分块 */
            hal_audio_write_pcm(msg.samples, msg.len);
            break;
        case 2: /* 开始录音 */
            hal_audio_start_record();
            break;
        case 3: /* 结束录音 */
            hal_audio_stop_record();
            break;
        case 4: /* 播放 WAV 文件 */
            hal_audio_play_wav_file(msg.path);
            break;
        default:
            break;
        }
    }
}

/** 存储任务：TF 卡访问在此单线程串行化 */
static void task_storage(void *arg)
{
    (void)arg;
    QueueHandle_t q = hal_storage_req_queue();
    for (;;) {
        storage_req_t req;
        if (q == NULL || xQueueReceive(q, &req, portMAX_DELAY) != pdTRUE) {
            continue;
        }
        hal_power_note_activity();
        switch (req.op) {
        case 1: /* 写文本文件（原子四步） */
            hal_storage_atomic_write_text(req.path, req.text, req.len);
            break;
        case 2: /* 删除文件 */
            hal_storage_delete_file(req.path);
            break;
        case 3: /* 读文本文件 */
            hal_storage_read_text(req.path, req.text, (uint32_t)sizeof(req.text), &req.len);
            break;
        default:
            break;
        }
    }
}

/**
 * 网络任务：请求投递口。
 * HTTP/TLS 传输尚未接入（Phase 2 剩余项），这里把请求接住并如实回报，
 * 不假装成功。
 */
static void task_net_worker(void *arg)
{
    (void)arg;
    QueueHandle_t q = hal_net_request_queue();
    for (;;) {
        net_request_t req;
        if (q == NULL || xQueueReceive(q, &req, portMAX_DELAY) != pdTRUE) {
            continue;
        }
        hal_power_note_activity();
        ui_command_t cmd = {};
        cmd.kind = 1;
        snprintf(cmd.text, sizeof(cmd.text), "网络通道尚未接通");
        xQueueSend(hal_ui_command_queue(), &cmd, 0);
    }
}

/* ================= 启动 ================= */

void abbot_tasks_start(void)
{
    /* 栈放内部 SRAM（doc/02 §2.2）；大缓冲走 PSRAM */
    xTaskCreatePinnedToCore(task_joystick, "t_joystick", 3072, NULL, 2, NULL, 1);
    xTaskCreatePinnedToCore(task_ui_fsm, "t_ui_fsm", 4096, NULL, 1, NULL, 1);
    xTaskCreatePinnedToCore(task_camera, "t_camera", 6144, NULL, 3, NULL, 0);
    xTaskCreatePinnedToCore(task_audio, "t_audio", 4096, NULL, 3, NULL, 0);
    xTaskCreatePinnedToCore(task_storage, "t_storage", 4096, NULL, 3, NULL, 0);
    xTaskCreatePinnedToCore(task_net_worker, "t_net", 4096, NULL, 3, NULL, 0);
    Serial.printf("[%s] tasks started (core1: joystick+ui, core0: camera+audio+storage+net)\n", TAG);
}
