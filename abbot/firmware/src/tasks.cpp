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
#include <esp_heap_caps.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/semphr.h>
#include <freertos/task.h>
#include <string.h>

#include "board_config.h"
#include "hal_interfaces.h"
#include "hal_shared.h"
#include "net_req.h"
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
            } else if (cmd.kind == 2) {
                /* 云端应答：所有权转给 UI 层（UI 负责释放与渲染） */
                ui_router_handle_net_reply(cmd.biz_code, cmd.payload, cmd.payload_len);
                cmd.payload = NULL;
            } else if (cmd.kind == 5) {
                /* 设备接入状态变化（自动登记，doc/04 §1.2） */
                ui_router_set_pair_state((int)cmd.biz_code);
            }
        }

        lv_tick_inc(5);
        lv_timer_handler();
        /* 番茄钟等需要持续推进的界面状态（毫秒计数） */
        ui_router_tick();
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

        if (req.want_uxga) {
            /* 抓拍（UXGA）：JPEG 所有权转给网络任务，由它上传后释放 */
            net_request_t nreq = {};
            nreq.op = NET_OP_SOLVE;
            nreq.hint_level = 0;
            nreq.with_voice = req.with_voice;
            snprintf(nreq.session_id, sizeof(nreq.session_id), "%s", req.session_id);
            nreq.jpg = jpg;
            nreq.jpg_len = (uint32_t)len;
            if (xQueueSend(hal_net_request_queue(), &nreq, 0) != pdTRUE) {
                Serial.printf("[%s] net queue full, jpg dropped\n", TAG);
                hal_camera_release_jpeg(jpg);
            }
        } else {
            /* 预览帧（HVGA）：不上传，直接释放 */
            hal_camera_release_jpeg(jpg);
        }
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
        case 5: /* 设备端合成提示音（不阻塞 UI 任务：UI 只投递，音频任务出声） */
            hal_audio_play_tone(msg.freq_hz, msg.duration_ms);
            break;
        default:
            break;
        }
    }
}

/**
 * 读一次 TF 卡档案并投给网络任务。
 *
 * TF 卡由本任务独占串行化访问（doc/02 §4.2），**net 任务不得直接扫盘**；
 * 因此档案的读取与解析都在这里做，net 层只收 `edu_stage` / `enrollment_year` /
 * 剩余配额三个值，之后组装明文时自动注入（doc/05 §5.1）。
 */
static void storage_publish_profile(void)
{
    char buf[512];
    uint32_t len = 0;
    if (hal_storage_read_text("/config/profile.json", buf, (uint32_t)sizeof(buf), &len) != HAL_OK ||
        len == 0) {
        return;
    }
    nr_profile_doc_t doc;
    if (nr_parse_profile(buf, (size_t)len, &doc) != 0) {
        Serial.printf("[%s] profile.json 解析失败\n", TAG);
        return;
    }
    int remaining = doc.daily_quota - doc.quota_used_count;
    if (remaining < 0) {
        remaining = 0;
    }
    net_request_t req;
    memset(&req, 0, sizeof(req));
    req.op = NET_OP_PROFILE;
    snprintf(req.edu_stage, sizeof(req.edu_stage), "%s", doc.edu_stage);
    req.enrollment_year = doc.enrollment_year;
    req.remaining_quota = remaining;
    if (xQueueSend(hal_net_request_queue(), &req, 0) != pdTRUE) {
        Serial.printf("[%s] profile 投递失败（网络队列满）\n", TAG);
        return;
    }
    Serial.printf("[%s] profile 已投递：%s %d 剩余 %d\n", TAG, doc.edu_stage,
                  doc.enrollment_year, remaining);
}

/**
 * 列目录回调（跑在存储任务里，所以能直接调 hal_storage_get_file_size）。
 * 每个文件名一行，'\n' 分隔；超出缓冲就停。
 */
static void storage_list_cb(const char *name, void *ctx)
{
    storage_io_t *io = (storage_io_t *)ctx;
    if (io == NULL || io->list_buf == NULL || name == NULL) {
        return;
    }
    size_t n = strlen(name);
    if (io->out_len + n + 2u > io->list_cap) {
        return;
    }
    memcpy(io->list_buf + io->out_len, name, n);
    io->out_len += (uint32_t)n;
    io->list_buf[io->out_len++] = '\n';
    io->list_buf[io->out_len] = '\0';
    io->list_count++;
}

/** 存储任务：TF 卡访问在此单线程串行化 */
static void task_storage(void *arg)
{
    (void)arg;
    QueueHandle_t q = hal_storage_req_queue();

    /* 开机先把档案交给网络任务 —— 之后每次联网请求都会带上学段 */
    storage_publish_profile();

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
        case 4: /* 写资产文件（net 任务下载好、交给本任务落盘） */
            if (req.asset != NULL) {
                hal_status_t st = hal_storage_write_binary(req.path, req.asset->data,
                                                           req.asset->len);
                req.asset->result = (int32_t)st;
                /* 只回填结果 + 给信号；**不释放** data 与作业对象 ——
                 * 它们归网络任务，网络任务醒来后自己释放。 */
                if (req.asset->done != NULL) {
                    xSemaphoreGive(req.asset->done);
                }
            }
            break;
        case 2: /* 删除文件 */
            hal_storage_delete_file(req.path);
            break;
        case 3: /* 读文本文件 */
            hal_storage_read_text(req.path, req.text, (uint32_t)sizeof(req.text), &req.len);
            break;
        case 5: /* 读文本（带应答）：UI 数据读取层用 */
            if (req.io != NULL) {
                uint32_t n = 0;
                hal_status_t st = HAL_ERR_INVALID;
                if (req.io->buf != NULL && req.io->buf_cap >= 2) {
                    st = hal_storage_read_text(req.path, (char *)req.io->buf,
                                               req.io->buf_cap, &n);
                }
                req.io->out_len = n;
                req.io->result = (int32_t)st;
            }
            break;
        case 6: /* 读区间（带应答）：分页用 */
            if (req.io != NULL) {
                uint32_t got = 0;
                hal_status_t st = HAL_ERR_INVALID;
                if (req.io->buf != NULL && req.io->buf_cap > 0) {
                    st = hal_storage_read_range(req.path, req.offset, req.io->buf,
                                                req.io->buf_cap, &got);
                }
                req.io->out_len = got;
                req.io->result = (int32_t)st;
            }
            break;
        case 7: /* 列目录（带应答）：文件名以 '\n' 分隔 */
            if (req.io != NULL) {
                hal_status_t st = hal_storage_list_files(req.path, storage_list_cb, req.io);
                req.io->result = (int32_t)st;
            }
            break;
        case 8: /* 取文件大小（带应答） */
            if (req.io != NULL) {
                uint32_t size = 0;
                hal_status_t st = hal_storage_get_file_size(req.path, &size);
                req.io->out_size = size;
                req.io->result = (int32_t)st;
            }
            break;
        case 9: /* 原子写文本（带应答） */
            if (req.io != NULL) {
                hal_status_t st = hal_storage_atomic_write_text(req.path, req.text, req.len);
                req.io->result = (int32_t)st;
            }
            break;
        case 10: /* 目录占用（带应答） */
            if (req.io != NULL) {
                uint64_t bytes = 0;
                hal_status_t st = hal_storage_get_dir_usage(req.path, &bytes);
                req.io->dir_bytes = bytes;
                req.io->result = (int32_t)st;
            }
            break;
        case 11: /* 是否挂载（带应答） */
            if (req.io != NULL) {
                req.io->mounted = hal_storage_is_mounted() ? 1u : 0u;
                req.io->result = HAL_OK;
            }
            break;
        default:
            break;
        }
        /* 带应答的操作：回执信号量在结果写好之后才给（调用方 take 到即数据已就绪） */
        if (req.reply != NULL) {
            xSemaphoreGive(req.reply);
        }
    }
}

/**
 * 网络任务：端云请求的**实际执行者**（Core 0，doc/02 §4.1）。
 *
 * 职责：取请求 → 交给 net 层加签 / 加密 / 收发 / 验签 / 解密 →
 * 把解密后的业务明文经 q_ui_command（kind=2）投回 Core 1。
 * UI 层不碰协议细节，net 层也不碰 UI（doc/02 §4.1 双核铁律）。
 */
static void task_net_worker(void *arg)
{
    (void)arg;
    QueueHandle_t q = hal_net_request_queue();

    /* 上电先联网：Wi-Fi 连接 + NTP 校时（doc/04 §1）。 */
    net_wifi_init_auto_connect();
    net_sync_ntp_time();

    /*
     * 自动登记（无码配对，doc/04 §1.2）：
     * 联网后如果本机还没有密钥，就自报身份向网关登记一次。
     * **设备侧零输入** —— 不弹码、不进虚拟键盘、不要求家长敲任何东西。
     * 没接上就**自动重试**（下方主循环里做），不把家长拖进配对流程。
     */
    bool paired = net_is_paired();
    uint32_t next_register_ms = 0;

    ui_command_t st = {};
    st.kind = 5;
    st.biz_code = paired ? UI_PAIR_STATE_READY : UI_PAIR_STATE_PENDING;
    xQueueSend(hal_ui_command_queue(), &st, 0);

    for (;;) {
        /* 显式清零：xQueueReceive 只有在返回 pdTRUE 时才写 req，
         * 留着未初始化的内容会让 -Wmaybe-uninitialized 在 -O 下报错。 */
        net_request_t req = {};
        bool got = (q != NULL) && (xQueueReceive(q, &req, pdMS_TO_TICKS(500)) == pdTRUE);
        if (got) {
            hal_power_note_activity();

            char *resp = NULL;
            int biz = net_dispatch(&req, &resp);

            /* JPEG 所有权随请求转移过来 —— 用完必须在这里释放 */
            if (req.jpg != NULL) {
                hal_camera_release_jpeg(req.jpg);
            }

            ui_command_t cmd = {};
            cmd.kind = 2;
            cmd.biz_code = biz;
            cmd.payload = resp;              /* 所有权转移给 Core 1 */
            cmd.payload_len = resp ? (uint32_t)strlen(resp) : 0;
            xQueueSend(hal_ui_command_queue(), &cmd, 0);

            /* 手动「重新尝试接入」可能已经登记成功 —— 刷新本地标志，
               免得主循环又去重复登记（网关幂等，但没必要白跑一趟）。 */
            if (!paired) {
                paired = net_is_paired();
            }
        }

        /*
         * 自动登记重试：没接上就每隔 15 秒再试一次，直到成功。
         * 放在主循环里（而不是阻塞在上电那一处），是为了**不占住请求队列** ——
         * 等待期间照样能处理拍照/语音等业务请求。
         */
        if (!paired) {
            uint32_t now = millis();
            if ((int32_t)(now - next_register_ms) >= 0) {
                if (net_auto_register()) {
                    paired = true;
                    st.biz_code = UI_PAIR_STATE_READY;
                } else {
                    st.biz_code = UI_PAIR_STATE_FAILED;
                    next_register_ms = now + 15000;
                }
                xQueueSend(hal_ui_command_queue(), &st, 0);
            }
        }
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
