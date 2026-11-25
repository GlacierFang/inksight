/*
 * ui_router.cpp —— 页面路由 + 浮层优先级 + 六手势宪法落地
 *
 * 契约来源: abbot/doc/03 §三 浮层优先级 / §3.2 六手势宪法 / §五 13 页状态。
 *
 * 铁律:
 *   - 长按 600ms 的判定只由 HAL 做, 本文件只消费 HAL 交来的 GESTURE_LONG。
 *   - 语音一律从页面操作菜单进入, 长按全局唯一含义是"退出"。
 *   - 阅读模块内全局屏蔽录音浮层 (doc/07)。
 *   - 提醒弹窗短按关闭并恢复原页, 长按忽略。
 */
#include "ui_router.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "lvgl.h"
#include "net_req.h"
#include "ui_data.h"
#include "ui_pages.h"

#include "hal_interfaces.h"   /* 背光、音频（Arduino.h 也从这里进来） */

static ui_page_t s_current = PAGE_OOBE;
static ui_book_page_t s_current_book = PAGE_BOOKSHELF;
static bool s_in_book = false;

/* 浮层可见状态, 索引即优先级(高->低) */
static bool s_overlay_visible[OVERLAY_COUNT] = {false, false, false};

/* 本题会话号计数器（doc/05 §5.1：每题唯一，同题追问沿用同一个） */
static uint32_t s_session_seq = 0;

/* 最近一次云端应答（解密后的业务明文）—— 由网络任务经队列投递过来 */
static char *s_last_reply = NULL;
static uint32_t s_last_reply_len = 0;
static int32_t s_last_biz = -1;

/* 提醒弹窗打断前的页面, 供短按关闭后精准恢复 */
static ui_page_t s_reminder_restore_page = PAGE_HOME;
static bool s_reminder_had_page = false;

/* 是否已配对（读 /config/profile.json）：未配对时端云功能给温和提示，
 * 本地页（番茄钟/设置/书架/课表）照常可用。 */
static int s_paired = 0;

/* 设备接入状态（自动登记，doc/04 §1.2）：由网络任务经队列投来。 */
static int s_pair_state = UI_PAIR_STATE_NONE;

/* 读配对标志（定义在“页面动作”段） */
static void ui_paired_refresh(void);

/* ===================== 浮层 ===================== */

static void ui_overlay_sync_visibility(void)
{
    /* 高优先级浮层出现时, 低优先级必须让位 */
    int i;
    for (i = OVERLAY_COUNT - 1; i >= 0; i--) {
        if (s_overlay_visible[i]) {
            break;
        }
    }
    /* i 是当前最高优先级的可见浮层; 比它低的全部隐藏 */
    for (int j = OVERLAY_COUNT - 1; j > i; j--) {
        s_overlay_visible[j] = false;
    }
    for (int j = 0; j < OVERLAY_COUNT; j++) {
        ui_overlay_set_visible((ui_overlay_t)j, s_overlay_visible[j]);
    }
}

ui_overlay_t ui_overlay_push(ui_overlay_t overlay)
{
    if (overlay >= OVERLAY_COUNT) {
        return ui_overlay_top();
    }
    /* 录音浮层在阅读模块里被屏蔽 */
    if (overlay == OVERLAY_RECORDING && s_in_book) {
        return ui_overlay_top();
    }
    s_overlay_visible[overlay] = true;
    ui_overlay_sync_visibility();
    return ui_overlay_top();
}

void ui_overlay_pop(ui_overlay_t overlay)
{
    if (overlay >= OVERLAY_COUNT) {
        return;
    }
    s_overlay_visible[overlay] = false;
    ui_overlay_sync_visibility();
}

ui_overlay_t ui_overlay_top(void)
{
    int i;
    for (i = 0; i < OVERLAY_COUNT; i++) {
        if (s_overlay_visible[i]) {
            return (ui_overlay_t)i;
        }
    }
    return OVERLAY_NONE;
}

/* ===================== 页面 ===================== */

static void ui_show_page(ui_page_t page)
{
    if (page >= PAGE_COUNT) {
        return;
    }
    if (s_in_book) {
        lv_obj_add_flag(ui_page_root_book(s_current_book), LV_OBJ_FLAG_HIDDEN);
        s_in_book = false;
    }
    lv_obj_t *root = ui_page_root(page);
    if (root == NULL) {
        return;
    }
    lv_obj_clear_flag(root, LV_OBJ_FLAG_HIDDEN);
    /* 切页时关掉浮层, 提醒弹窗除外 —— 它要保留到用户短按关闭 */
    s_overlay_visible[OVERLAY_RECORDING] = false;
    s_overlay_visible[OVERLAY_TOAST] = false;
    ui_overlay_sync_visibility();
    s_current = page;
}

void ui_router_go(ui_page_t page)
{
    ui_show_page(page);
    ui_pages_cursor_reset(page);
    switch (page) {
    case PAGE_POMODORO:
        /* 进入就开始倒计时（时长取本地设置） */
        ui_pages_pomodoro_enter();
        break;
    case PAGE_HOME:
        ui_paired_refresh();
        ui_pages_refresh_home();
        break;
    default:
        break;
    }
}

void ui_router_go_book(ui_book_page_t page)
{
    lv_obj_t *root = ui_page_root_book(page);
    if (root == NULL) {
        return;
    }
    if (!s_in_book) {
        lv_obj_add_flag(ui_page_root(s_current), LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(ui_page_root_book(s_current_book), LV_OBJ_FLAG_HIDDEN);
    }
    lv_obj_clear_flag(root, LV_OBJ_FLAG_HIDDEN);
    s_overlay_visible[OVERLAY_RECORDING] = false;
    s_overlay_visible[OVERLAY_TOAST] = false;
    ui_overlay_sync_visibility();
    s_current_book = page;
    s_in_book = true;
    if (page == PAGE_BOOKSHELF) {
        /* 书架内容来自 TF 卡，进页时重新读一遍 */
        ui_pages_refresh_bookshelf();
    }
}

ui_page_t ui_router_current(void)
{
    return s_current;
}

bool ui_router_in_book_module(void)
{
    return s_in_book;
}

/* ===================== 页面动作 ===================== */

/** 读配对标志（/config/profile.json）。读不到就按未配对处理。 */
static void ui_paired_refresh(void)
{
    int paired = 0;
    if (ui_data_read_paired(&paired) == UI_DATA_OK) {
        s_paired = paired;
    } else {
        s_paired = 0;
    }
    /*
     * 「已接入」的真相源是 **NVS 里有没有 device_secret**（自动登记写入，
     * doc/04 §1.2），不是 TF 卡上 profile.json 的 paired 字段 ——
     * 那张卡可能压根没有这个字段（空白卡自愈生成的那份就没有）。
     * 所以取两者的「或」：只要网关注册成功过，就算已接入，
     * 不然首页的「拍题导学」会一直误报「还没连上学习助手」。
     */
    if (s_pair_state == UI_PAIR_STATE_READY) {
        s_paired = 1;
    }
}

/**
 * 页面级「确定」动作（doc/03 §三 仲裁表的短按列）。
 *
 * 只有**已有真实实现**的入口才跳转；尚未打通的入口给一句温和提示，
 * 不用假动作糊弄过去。
 */
static void ui_page_action(ui_page_t page)
{
    int idx = ui_pages_cursor_index(page);
    switch (page) {
    case PAGE_CAMERA: {
        /* 短按 = 拍照：投递抓拍请求（UI 核 → 相机任务，doc/02 §4.2） */
        char sid[32];
        s_session_seq++;
        snprintf(sid, sizeof(sid), "sess_%u", (unsigned)s_session_seq);
        if (hal_photo_request_submit(sid, 1, 0) == HAL_OK) {
            ui_toast("已拍照，正在解题");
        } else {
            ui_toast("相机忙，请稍后再试");
        }
        break;
    }
    case PAGE_HOME:
        switch (idx) {
        case 0:
            /* 拍题导学要靠云端解题：没配对时先给一句温和提示 */
            if (!s_paired) {
                ui_toast("还没连上学习助手，请检查一下网络");
                break;
            }
            ui_router_go(PAGE_CAMERA);
            break;
        case 2: ui_router_go(PAGE_SUBJECT_LIST); break;
        case 3: ui_router_go_book(PAGE_BOOKSHELF); break;
        case 4: ui_router_go(PAGE_POMODORO); break;
        case 5: ui_router_go(PAGE_OUTBOX); break;
        case 6: ui_router_go(PAGE_SETTINGS); break;
        case 1: ui_toast("语音提问还在准备中"); break;
        default: break;
        }
        break;
    case PAGE_SETTINGS:
        switch (idx) {
        case 0: ui_router_go(PAGE_WIFI_SCAN); break;
        case 3: ui_router_go(PAGE_OTA); break;
        case 6: ui_toast("用左右键调整专注时长"); break;
        default: ui_toast("这个设置还在准备中"); break;
        }
        break;
    case PAGE_POMODORO:
        /* 短按 = 暂停/继续（已完成后再按 = 重新开始） */
        ui_pages_pomodoro_toggle();
        break;
    case PAGE_OOBE:
        switch (idx) {
        case 0:
            ui_router_go(PAGE_WIFI_SCAN);
            break;
        case 1:
            /* 重新尝试接入：UI 只发请求，实际网络活儿由网络任务做 */
            {
                net_request_t req = {};
                req.op = NET_OP_REGISTER;
                if (hal_net_request_queue() == NULL ||
                    xQueueSend(hal_net_request_queue(), &req, 0) != pdTRUE) {
                    ui_toast("网络还在准备中，等一下就好");
                } else {
                    ui_toast("正在重新接入");
                }
            }
            break;
        case 2:
            /* 先离线体验：本地页（课表/书架/番茄钟/设置）照常可用 */
            ui_router_go(PAGE_HOME);
            break;
        default:
            break;
        }
        break;
    case PAGE_SUBJECT_LIST:
        if (idx >= 0) {
            ui_router_go(PAGE_CARD_STUDY);
        }
        break;
    case PAGE_SOLVE_RESULT:
        /* 菜单项「口述你的思路」—— 语音链路尚未打通 */
        ui_toast("口述思路还在准备中");
        break;
    case PAGE_WIFI_SCAN:
        ui_toast("连接热点还在准备中");
        break;
    case PAGE_OTA:
        ui_toast("检查更新还在准备中");
        break;
    default:
        break;
    }
}

/** 方向键：列表页移动光标；解析结果页滚动正文（doc/03 §三）。 */
static void ui_page_nav(ui_page_t page, int delta)
{
    if (page == PAGE_SOLVE_RESULT) {
        ui_pages_solve_scroll(delta);
        return;
    }
    ui_pages_cursor_move(page, delta);
}

/**
 * 左右键：设置页调专注时长；其它页面的左右暂不作用途。
 * 不新增手势 —— 四向导航里左右本来就是「减/加」。
 */
static void ui_page_side(ui_page_t page, int delta)
{
    if (page == PAGE_SETTINGS) {
        if (ui_pages_cursor_index(page) == 6) {
            ui_pages_focus_adjust(delta);
            return;
        }
    }
}

/* ===================== 电子书模块手势（doc/07） ===================== */

/** 调背光（阅读页上下键用）。 */
static void ui_brightness(int delta)
{
    uint8_t level = hal_display_get_backlight();
    int next = (int)level + delta;
    if (next < 0) {
        next = 0;
    }
    if (next > 5) {
        next = 5;
    }
    if ((uint8_t)next == level) {
        return;
    }
    hal_display_set_backlight((uint8_t)next);
}

static void ui_book_gesture(ui_gesture_t gesture)
{
    switch (gesture) {
    case GESTURE_UP:
        if (s_current_book == PAGE_BOOKSHELF) {
            ui_pages_bookshelf_move(-1);
        } else if (s_current_book == PAGE_READER) {
            ui_brightness(-1);
        }
        break;
    case GESTURE_DOWN:
        if (s_current_book == PAGE_BOOKSHELF) {
            ui_pages_bookshelf_move(1);
        } else if (s_current_book == PAGE_READER) {
            ui_brightness(1);
        }
        break;
    case GESTURE_LEFT:
        if (s_current_book == PAGE_READER) {
            ui_pages_reader_prev_page();
        }
        break;
    case GESTURE_RIGHT:
        if (s_current_book == PAGE_READER) {
            ui_pages_reader_next_page();
        }
        break;
    case GESTURE_CLICK:
        if (s_current_book == PAGE_BOOKSHELF) {
            if (ui_pages_bookshelf_open_selected() == 0) {
                ui_router_go_book(PAGE_READER);
            } else {
                ui_toast("暂时读不到内容");
            }
        } else if (s_current_book == PAGE_READER) {
            ui_router_go_book(PAGE_BOOK_MENU);
        }
        break;
    case GESTURE_LONG:
    default:
        /* 长按退出在主分支里统一处理 */
        break;
    }
}

/* ===================== 手势 ===================== */

static void ui_handle_recording_gesture(ui_gesture_t gesture)
{
    switch (gesture) {
    case GESTURE_CLICK:
        /* 短按结束并提交: 具体提交动作由 task_audio 监听浮层状态完成 */
        ui_overlay_pop(OVERLAY_RECORDING);
        break;
    case GESTURE_UP:
        /* 上推取消 */
        ui_overlay_pop(OVERLAY_RECORDING);
        break;
    case GESTURE_LONG:
        /* 长按等同于取消 */
        ui_overlay_pop(OVERLAY_RECORDING);
        break;
    default:
        break;
    }
}

void ui_router_handle_gesture(ui_gesture_t gesture)
{
    if (gesture == GESTURE_NONE) {
        return;
    }

    /* 浮层优先: 有浮层时手势先交给浮层, 不落到页面 */
    ui_overlay_t top = ui_overlay_top();
    if (top == OVERLAY_REMINDER) {
        if (gesture == GESTURE_CLICK) {
            /* 短按关闭提醒并恢复打断前的页面 */
            ui_overlay_pop(OVERLAY_REMINDER);
            if (s_reminder_had_page) {
                s_reminder_had_page = false;
                if (s_in_book) {
                    ui_router_go_book(s_current_book);
                } else {
                    ui_router_go(s_reminder_restore_page);
                }
            }
        }
        /* 长按忽略 */
        return;
    }
    if (top == OVERLAY_RECORDING) {
        ui_handle_recording_gesture(gesture);
        return;
    }
    if (top == OVERLAY_TOAST) {
        /* 提示条不响应手势 */
        return;
    }

    /* 页面级: 短按 = 确定, 长按 = 退出, 方向键 = 页内导航 */
    switch (gesture) {
    case GESTURE_CLICK:
        if (s_in_book) {
            ui_book_gesture(gesture);
            break;
        }
        ui_page_action(s_current);
        break;
    case GESTURE_UP:
        if (s_in_book) {
            ui_book_gesture(gesture);
            break;
        }
        if (s_current != PAGE_POMODORO) {
            ui_page_nav(s_current, -1);
        }
        break;
    case GESTURE_DOWN:
        if (s_in_book) {
            ui_book_gesture(gesture);
            break;
        }
        if (s_current != PAGE_POMODORO) {
            ui_page_nav(s_current, 1);
        }
        break;
    case GESTURE_LEFT:
    case GESTURE_RIGHT:
        if (s_in_book) {
            ui_book_gesture(gesture);
            break;
        }
        ui_page_side(s_current, (gesture == GESTURE_LEFT) ? -1 : 1);
        break;
    case GESTURE_LONG:
        /* 退出: 退回上一级 */
        if (s_in_book) {
            switch (s_current_book) {
            case PAGE_READER:
            case PAGE_BOOK_MENU:
            case PAGE_BOOK_TOC:
            case PAGE_BOOK_JUMP:
                ui_router_go_book(PAGE_BOOKSHELF);
                break;
            case PAGE_BOOKSHELF:
            case PAGE_BOOK_LOCKED:
            default:
                ui_router_go(PAGE_HOME);
                break;
            }
        } else if (s_current == PAGE_POMODORO) {
            /* 番茄钟: 长按 = 结束并退出 */
            ui_pages_pomodoro_stop();
            ui_router_go(PAGE_HOME);
        } else if (s_current != PAGE_HOME && s_current != PAGE_OOBE) {
            ui_router_go(PAGE_HOME);
        }
        break;
    default:
        /* 四向导航由页面自己的光标处理 */
        break;
    }
}

void ui_router_handle_joystick(const joystick_event_t *ev)
{
    if (ev == NULL) {
        return;
    }
    ui_gesture_t gesture = GESTURE_NONE;
    switch (ev->type) {
    case JOY_EVENT_UP:
        gesture = GESTURE_UP;
        break;
    case JOY_EVENT_DOWN:
        gesture = GESTURE_DOWN;
        break;
    case JOY_EVENT_LEFT:
        gesture = GESTURE_LEFT;
        break;
    case JOY_EVENT_RIGHT:
        gesture = GESTURE_RIGHT;
        break;
    case JOY_EVENT_CLICK:
        gesture = GESTURE_CLICK;
        break;
    case JOY_EVENT_LONG_PRESS_END:
        /* 长按在**松开瞬间**才生效 (doc/03 §3.2) */
        gesture = GESTURE_LONG;
        break;
    case JOY_EVENT_LONG_PRESS_START:
    case JOY_EVENT_REPEAT:
    case JOY_EVENT_NONE:
    default:
        /* 长按开始不触发动作; 连发滚动由页面自己处理 */
        break;
    }
    ui_router_handle_gesture(gesture);
}

void ui_toast(const char *text)
{
    if (text == NULL || text[0] == '\0') {
        return;
    }
    ui_overlay_set_text(OVERLAY_TOAST, text);
    ui_overlay_push(OVERLAY_TOAST);
}

/* ===================== 初始化与刷新 ===================== */

void ui_router_init(void)
{
    lv_obj_t *scr = lv_scr_act();
    ui_pages_init(scr);
    s_current = PAGE_OOBE;
    s_in_book = false;
    ui_show_page(PAGE_OOBE);
}

void ui_router_tick(void)
{
    /* LVGL 5ms 心跳由 task_ui_fsm 的 lv_timer_handler 驱动;
     * 番茄钟的倒计时挂在这里 —— 用毫秒计数，不做秒级累加。 */
    ui_pages_pomodoro_tick((uint32_t)millis());
}

/* ===================== 云端应答 ===================== */

void ui_router_handle_net_reply(int32_t biz_code, char *payload, uint32_t len)
{
    /* 旧缓冲先释放 —— 所有权始终在 UI 层 */
    if (s_last_reply != NULL) {
        free(s_last_reply);
        s_last_reply = NULL;
        s_last_reply_len = 0;
    }
    s_last_biz = biz_code;
    if (payload != NULL) {
        s_last_reply = payload;
        s_last_reply_len = len;
    }

    /* 文案一律由 UI 层出（doc/03 §四）：HAL / net 层不拼面向用户的中文 */
    if (s_last_biz == 4004) {
        ui_toast("请先在设置里完成学段选择");
        return;
    }
    if (s_last_biz != 0) {
        ui_toast("网络请求未成功");
        return;
    }

    /* 成功：把解题步骤与费曼反问渲染到解析结果页 */
    nr_solve_result_t res;
    if (nr_parse_solve_result(s_last_reply, s_last_reply_len, &res) == 0 &&
        res.status[0] != '\0') {
        ui_pages_set_solve_result(res.display_markdown, res.feynman_question);
        ui_router_go(PAGE_SOLVE_RESULT);
    } else {
        ui_toast("已收到云端应答");
    }
}

const char *ui_router_last_reply(uint32_t *out_len)
{
    if (out_len != NULL) {
        *out_len = s_last_reply_len;
    }
    return s_last_reply;
}

/* ===================== 设备接入状态（自动登记，doc/04 §1.2） ===================== */

void ui_router_set_pair_state(int state)
{
    s_pair_state = state;
    ui_pages_set_pair_state(state);

    if (state == UI_PAIR_STATE_READY) {
        /* 接入成功：顺手刷新「已配对」标志，让首页的端云入口可用。
         * 读卡失败也不阻断跳转 —— 本地页照常。 */
        ui_paired_refresh();
        if (s_current == PAGE_OOBE) {
            /* 设备侧零输入：接入好了直接进首页，不需要家长按任何键 */
            ui_router_go(PAGE_HOME);
        }
    }
}
