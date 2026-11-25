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

#include <stdlib.h>
#include <string.h>

#include "lvgl.h"
#include "ui_pages.h"

static ui_page_t s_current = PAGE_OOBE;
static ui_book_page_t s_current_book = PAGE_BOOKSHELF;
static bool s_in_book = false;

/* 浮层可见状态, 索引即优先级(高->低) */
static bool s_overlay_visible[OVERLAY_COUNT] = {false, false, false};

/* 最近一次云端应答（解密后的业务明文）—— 由网络任务经队列投递过来 */
static char *s_last_reply = NULL;
static uint32_t s_last_reply_len = 0;
static int32_t s_last_biz = -1;

/* 提醒弹窗打断前的页面, 供短按关闭后精准恢复 */
static ui_page_t s_reminder_restore_page = PAGE_HOME;
static bool s_reminder_had_page = false;

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
}

ui_page_t ui_router_current(void)
{
    return s_current;
}

bool ui_router_in_book_module(void)
{
    return s_in_book;
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

    /* 页面级: 短按 = 确定, 长按 = 退出 */
    switch (gesture) {
    case GESTURE_CLICK:
        /* 各页的"确定"动作由各页自己的选择器处理; 这里只做页面级路由 */
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
    /* LVGL 5ms 心跳由 task_ui_fsm 的 lv_timer_handler 驱动; 这里留一个空钩子,
     * 供后续接 toast 自动消失与倒计时刷新。 */
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
    if (s_last_biz == 0) {
        ui_toast("已收到云端应答");
    } else if (s_last_biz == 4004) {
        ui_toast("请先在设置里完成学段选择");
    } else {
        ui_toast("网络请求未成功");
    }
}

const char *ui_router_last_reply(uint32_t *out_len)
{
    if (out_len != NULL) {
        *out_len = s_last_reply_len;
    }
    return s_last_reply;
}
