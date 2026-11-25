/*
 * ui_router.h —— LVGL 页面路由 + 浮层优先级 + 手势仲裁
 *
 * 契约来源:
 *   - abbot/doc/03 §二  三区固定屏幕骨架
 *   - abbot/doc/03 §三  三层全局浮层抢占优先级
 *   - abbot/doc/03 §四  页面级摇杆手势仲裁表
 *   - abbot/doc/07      电子书模块子页面与语音屏蔽规则
 *
 * 铁律:
 *   1. 长按 600ms 的判定**只由 HAL 裁定**（board_config.h 的 LONG_PRESS_MS），
 *      UI 层严禁自己计时、严禁另设阈值 —— 这里只消费 HAL 给出的结论。
 *   2. 短按 = 确定；长按 = 退出。按住 >=600ms 后**松开**才生效。
 *   3. 录音浮层里短按 = 结束并提交，上推 = 取消，长按 = 取消。
 *   4. 阅读模块（PAGE_BOOKSHELF / PAGE_READER / PAGE_BOOK_MENU / PAGE_BOOK_TOC /
 *      PAGE_BOOK_JUMP / PAGE_BOOK_LOCKED）内**全局屏蔽录音浮层**，摇杆只做翻页/亮度/跳章/返回。
 *   5. UI 层严禁执行任何阻塞式网络或 TF 卡 IO —— 网络归 task_net_worker，存储归 task_storage。
 */
#ifndef ABBOT_UI_ROUTER_H
#define ABBOT_UI_ROUTER_H

#include <stdint.h>

#include <stdbool.h>
#include <stdint.h>

#include "hal_shared.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ===================== 页面 ID ===================== */

/* doc/03 的 13 个核心页面 */
typedef enum {
    PAGE_HOME = 0,          /* 首页仪表盘 */
    PAGE_OOBE,              /* 首次开机引导 */
    PAGE_WIFI_SCAN,         /* WiFi 扫描与选择 */
    PAGE_SETTINGS,          /* 设置 */
    PAGE_SUBJECT_LIST,      /* 学科包列表 */
    PAGE_CARD_STUDY,        /* 学科包卡片 */
    PAGE_CAMERA,            /* 取景器 */
    PAGE_SOLVE_RESULT,      /* 解析结果 */
    PAGE_OUTBOX,            /* 离线发件箱 */
    PAGE_KEYBOARD,          /* 文本输入（答案录入等） */
    PAGE_OTA,               /* 固件升级 */
    PAGE_POMODORO,          /* 番茄钟 */
    PAGE_COUNT
} ui_page_t;

/* doc/07 电子书模块子页面（不进 13 页计数） */
typedef enum {
    PAGE_BOOKSHELF = 100,   /* 书架 */
    PAGE_READER,            /* 阅读正文 */
    PAGE_BOOK_MENU,         /* 阅读菜单 */
    PAGE_BOOK_TOC,          /* 目录 */
    PAGE_BOOK_JUMP,         /* 跳转 */
    PAGE_BOOK_LOCKED,       /* 宵禁锁屏 */
    PAGE_BOOK_COUNT
} ui_book_page_t;

/* ===================== 浮层 ===================== */

/* 抢占优先级高 -> 低。同一时刻只有最高优先级浮层可见。 */
typedef enum {
    OVERLAY_NONE = 0,
    OVERLAY_REMINDER,       /* 高：课表提醒弹窗 */
    OVERLAY_RECORDING,      /* 中：语音录音浮层 */
    OVERLAY_TOAST,          /* 低：底部 2 秒提示条，严禁覆盖上面两层 */
    OVERLAY_COUNT
} ui_overlay_t;

/* ===================== 手势 ===================== */

typedef enum {
    GESTURE_NONE = 0,
    GESTURE_UP,
    GESTURE_DOWN,
    GESTURE_LEFT,
    GESTURE_RIGHT,
    GESTURE_CLICK,          /* 短按：确定 */
    GESTURE_LONG            /* 长按 >=600ms 后松开：退出 */
} ui_gesture_t;

/* ===================== 对外 API ===================== */

/* 建 LVGL 页面骨架：一次性创建所有页面与浮层，之后只做显隐切换。 */
void ui_router_init(void);

/* 切页。切页会自动关掉低优先级浮层（提醒弹窗除外，它只允许被自己替换）。 */
void ui_router_go(ui_page_t page);
void ui_router_go_book(ui_book_page_t page);
ui_page_t ui_router_current(void);
bool ui_router_in_book_module(void);

/* 浮层：push 后按优先级抢占，返回真正显示的那一层。 */
ui_overlay_t ui_overlay_push(ui_overlay_t overlay);
void ui_overlay_pop(ui_overlay_t overlay);
ui_overlay_t ui_overlay_top(void);

/* 手势入口。gesture 由 HAL 裁定短按/长按；本函数只做路由，不自己计时。 */
void ui_router_handle_gesture(ui_gesture_t gesture);

/* HAL 摇杆事件入口：把 HAL 归类好的事件翻译成 UI 手势。 */
void ui_router_handle_joystick(const joystick_event_t *ev);

/* 轻量提示条（最低优先级浮层，2 秒自动消失）。 */
void ui_toast(const char *text);

/* 渲染循环：由 task_ui_fsm 调用，内部用 lv_timer 驱动，不阻塞。 */
void ui_router_tick(void);

/**
 * 收到云端应答（Core 1 侧入口，由 task_ui_fsm 调用）。
 *
 * `payload` 是解密后的业务明文，**所有权转移给 UI 层**（本函数负责释放旧缓冲，
 * 新缓冲由本层持有并最终释放）。业务码语义：
 *   0    成功
 *   4004 学段未设置（doc/05 §5.1：显示「请先在设置里完成学段选择」）
 *   其他 网关业务码或 -1 网络/协议失败
 */
void ui_router_handle_net_reply(int32_t biz_code, char *payload, uint32_t len);

/** 取最近一次云端应答的业务明文（供页面渲染；无应答时返回 NULL）。 */
const char *ui_router_last_reply(uint32_t *out_len);

#ifdef __cplusplus
}
#endif

#endif /* ABBOT_UI_ROUTER_H */
