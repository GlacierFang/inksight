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

#include "cards.h"
#include "lvgl.h"
#include "ui_router.h"
#include "ui_weather.h"

#ifdef __cplusplus
extern "C" {
#endif

/* 一次性构建全部页面与浮层。parent 一般是 LVGL 根屏幕或一个全屏容器。 */
void ui_pages_init(lv_obj_t *parent);

lv_obj_t *ui_page_root(ui_page_t page);
lv_obj_t *ui_page_content(ui_page_t page);
/**
 * 建一个正文 / 标题标签（样式与页面内一致）。
 * 给需要自己持有并刷新文字的 UI 模块用（如固件升级页）。
 */
lv_obj_t *ui_pages_make_label(lv_obj_t *parent, const char *text, bool title);
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
/**
 * 刷新首页天气文案（doc/05 §5.2）。
 *
 * `text` 是 ui_weather_display_text() 生成的成品文案：在线读数 / 缓存标注 / 空态。
 * 本函数不做判断 —— 空态那句「暂时拿不到天气」也是调用方传进来的。
 */
void ui_pages_set_weather_text(const char *text);
/**
 * 刷新设置页「所在城市」右侧的值（给用户看的中文名）。
 * 没设置过时传 NULL → 显示「未设置」。
 */
void ui_pages_set_city_label(const char *zh);

/* ---------- 选择城市页（doc/05 §5.2） ---------- */
/** 进入城市页：光标复位并重画。 */
void ui_pages_city_enter(void);
/** 上下移动光标（-1 上一项 / +1 下一项）。返回移动后的候选序号。 */
int ui_pages_city_move(int delta);
/** 当前选中的候选城市（NULL = 没有候选）。 */
const ui_city_candidate_t *ui_pages_city_selected(void);
/** 刷新书架列表（读 `/books/` 下的真实书籍）。 */
void ui_pages_refresh_bookshelf(void);
/** 书架光标移动（-1 上一项 / +1 下一项）。返回移动后的索引。 */
int ui_pages_bookshelf_move(int delta);
/** 打开光标所在的那本书并渲染第一页。成功返回 0。 */
int ui_pages_bookshelf_open_selected(void);
/** 阅读页翻页：下一页 / 上一页（UTF-8 边界安全）。 */
void ui_pages_reader_next_page(void);
void ui_pages_reader_prev_page(void);

/* ---------- 阅读护栏（doc/07 §2.3 / §2.4） ---------- */
/**
 * 打开书籍前的护栏检查：宵禁时段、或今日课外阅读时长已达上限 → 返回 false，
 * **不进入阅读**。被锁时把温和的中文提示写进宵禁提示页（由路由切过去）。
 */
bool ui_pages_reader_guard_check(void);
/**
 * 阅读计时推进（doc/07 §2.4）：只有阅读页在前台、无浮层遮挡、屏幕点亮时
 * 才累计当日阅读秒数；其余情况一律暂停。
 */
void ui_pages_reader_tick(uint32_t now_ms, bool active);
/** 离开阅读页：保存阅读进度与当日阅读时长（doc/07 §2.3 / §2.4）。 */
void ui_pages_reader_leave(void);
/** 设置宵禁/上限提示页的文案（温和中文，不出现技术术语）。 */
void ui_pages_set_book_locked_text(const char *title, const char *body);

/* ---------- 专注时长设置（PAGE_SETTINGS） ---------- */
/** 当前专注时长（分钟）。 */
int ui_pages_focus_minutes(void);
/** 调整专注时长（delta = -1 减一档 / +1 加一档，步长 5 分钟），并立即落盘。 */
void ui_pages_focus_adjust(int delta);

/* ---------- Wi-Fi 扫描页（真实扫描结果） ---------- */
/**
 * 进入 Wi-Fi 扫描页：清空旧列表并**请求扫描**。
 * 真正的扫描在网络任务（Core 0）里做，UI 只提交请求（doc/02 §4.1）。
 */
void ui_pages_wifi_scan_enter(uint32_t now_ms);
/** 每轮 UI 心跳：轮询扫描结果与连接状态，刷新页面。 */
void ui_pages_wifi_scan_tick(uint32_t now_ms);
/** 上下移动光标（-1 上 / +1 下）。返回移动后的光标索引。 */
int ui_pages_wifi_scan_move(int delta);
/** 短按：选中热点 →（加密的）进键盘输密码；（开放的）直接连接；「重新扫描」→ 重扫。 */
void ui_pages_wifi_scan_action(uint32_t now_ms);
/** 从键盘页确认后回来：用键盘里输的密码去连这个网络。 */
void ui_pages_wifi_scan_connect(const char *ssid, const char *password, uint32_t now_ms);

/* ---------- 虚拟键盘（真键盘，doc/03 §五 第 12 页） ---------- */
/** 进入键盘页：设定输入对象（标题里的 SSID）与是否掩码显示。 */
void ui_pages_keyboard_enter(const char *title, bool masked, uint32_t now_ms);
/** 四向移动光标（dx / dy 取 -1 / 0 / 1）。 */
void ui_pages_keyboard_move(int dx, int dy, uint32_t now_ms);
/** 短按：输入字符 / 触发功能键。 */
void ui_pages_keyboard_action(uint32_t now_ms);
/** 短按了「确认」返回 true（此时用 ui_pages_keyboard_text 取内容）。 */
bool ui_pages_keyboard_confirmed(void);
/** 取当前输入内容（明文；调用方**不得**打印它）。 */
const char *ui_pages_keyboard_text(void);
/** 键盘的输入对象（SSID）；没有就是空串。 */
const char *ui_pages_keyboard_target(void);
/** 键盘页心跳：60 秒无操作返回 true（调用方据此退出）。 */
bool ui_pages_keyboard_tick(uint32_t now_ms);
/** 退出键盘页：清掉内存里的输入内容。 */
void ui_pages_keyboard_leave(void);

/* ---------- 状态栏 ---------- */
/** 设置某页状态栏文字（例如离线提示）。 */
void ui_pages_set_status(ui_page_t page, const char *text);

/* ---------- 学科卡片（doc/04 §四） ---------- */
/** 刷新学科包列表（读卡上 /subjects 下的真实学科包）。 */
void ui_pages_refresh_subject_list(void);
/** 当前选中的学科包 id（没有则空串）。 */
const char *ui_pages_selected_pack_id(void);
/** 载入卡片集（UI 持有指针，不拷贝 —— 上层持有所有权）。 */
void ui_pages_set_cards(const card_pack_t *pack);
/** 已载入的卡片张数（0 = 没有）。 */
int ui_pages_card_count(void);
/** 当前这张卡的序号（0 起）。 */
int ui_pages_card_index(void);
/** 当前是不是显示背面（答案）。 */
bool ui_pages_card_answer_shown(void);
/** 显示第 index 张卡（show_answer = true 显示背面）。 */
void ui_pages_show_card(int index, bool show_answer);

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
