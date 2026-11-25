/*
 * ui_pages.h —— 页面与浮层的构建/显隐接口
 *
 * 契约来源: abbot/doc/03 §二 三区骨架 / §三 浮层优先级 / §五 13 页线框,
 *           abbot/doc/07 电子书子页面。
 *
 * 本头只暴露"建页面"与"显隐", 不暴露路由决策 —— 路由在 ui_router.h。
 */
#ifndef ABBOT_UI_PAGES_H
#define ABBOT_UI_PAGES_H

#include <stdbool.h>

#include "lvgl.h"
#include "ui_router.h"

#ifdef __cplusplus
extern "C" {
#endif

/* 一次性构建全部页面与浮层。parent 一般是 LVGL 根屏幕或一个全屏容器。 */
void ui_pages_init(lv_obj_t *parent);

lv_obj_t *ui_page_root(ui_page_t page);
lv_obj_t *ui_page_content(ui_page_t page);
lv_obj_t *ui_page_root_book(ui_book_page_t page);
lv_obj_t *ui_page_content_book(ui_book_page_t page);

/* ---------- 列表光标（doc/03 §三 页面级仲裁表） ---------- */
/**
 * 移动页面内的列表光标。返回移动后的光标索引；页面无可选行时返回 -1。
 * 光标渲染成行首标记（`> ` 选中 / `  ` 未选），与 doc/03 线框一致。
 */
int ui_pages_cursor_move(ui_page_t page, int delta);
/** 当前光标索引（无可选行时 -1）。 */
int ui_pages_cursor_index(ui_page_t page);
/** 光标复位到首行（进入页面时调用）。 */
void ui_pages_cursor_reset(ui_page_t page);

/* ---------- 解析结果页（接云端真实内容） ---------- */
/**
 * 写入解析结果页：解题步骤正文 + 费曼反问。
 * 正文按行分屏显示，滚动由 `ui_pages_solve_scroll` 控制。
 */
void ui_pages_set_solve_result(const char *markdown, const char *question);
/** 正文滚动（delta=+1 下一屏，-1 上一屏）。返回是否真的滚动了。 */
bool ui_pages_solve_scroll(int delta);

/* ---------- 番茄钟（真计时器，doc/03 §五 第 8 页） ---------- */
/**
 * 进入番茄钟：读本地设置的时长并开始倒计时。
 * 短按 = 暂停 / 继续，长按 = 结束并退出（doc/03 §3.2）。
 */
void ui_pages_pomodoro_enter(void);
/** 计时推进（由 UI 任务每轮调用，now_ms 为毫秒计数）。到点时出声并进入「已完成」态。 */
void ui_pages_pomodoro_tick(uint32_t now_ms);
/** 短按：暂停 / 继续（已完成后再按一次则重新开始）。 */
void ui_pages_pomodoro_toggle(void);
/** 长按：结束并退出（停止计时，不弹提示）。 */
void ui_pages_pomodoro_stop(void);

/* ---------- 本地内容（读 TF 卡，doc/04 §二） ---------- */
/** 刷新首页「今日课程」（读 `/config/schedule.json`）。读不到时出温和空态。 */
void ui_pages_refresh_home(void);
/** 刷新书架列表（读 `/books/` 下的真实书籍）。 */
void ui_pages_refresh_bookshelf(void);
/** 书架光标移动（-1 上一项 / +1 下一项）。返回移动后的索引。 */
int ui_pages_bookshelf_move(int delta);
/** 打开光标所在的那本书并渲染第一页。成功返回 0。 */
int ui_pages_bookshelf_open_selected(void);
/** 阅读页翻页：下一页 / 上一页（UTF-8 边界安全）。 */
void ui_pages_reader_next_page(void);
void ui_pages_reader_prev_page(void);

/* ---------- 专注时长设置（PAGE_SETTINGS） ---------- */
/** 当前专注时长（分钟）。 */
int ui_pages_focus_minutes(void);
/** 调整专注时长（delta = -1 减一档 / +1 加一档，步长 5 分钟），并立即落盘。 */
void ui_pages_focus_adjust(int delta);

/* ---------- 状态栏 ---------- */
/** 设置某页状态栏文字（例如离线提示）。 */
void ui_pages_set_status(ui_page_t page, const char *text);

/* ---------- 设备接入状态（自动登记，doc/04 §1.2） ---------- */
/**
 * 刷新 OOBE 页的接入进度文案。
 * `state` 取 `UI_PAIR_STATE_*`（hal_shared.h）；UI 只渲染，不自己发网络请求、不自己计时。
 */
void ui_pages_set_pair_state(int state);

lv_obj_t *ui_overlay_root(ui_overlay_t overlay);
void ui_overlay_set_text(ui_overlay_t overlay, const char *text);
void ui_overlay_set_visible(ui_overlay_t overlay, bool visible);

#ifdef __cplusplus
}
#endif

#endif /* ABBOT_UI_PAGES_H */
