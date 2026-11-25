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

lv_obj_t *ui_overlay_root(ui_overlay_t overlay);
void ui_overlay_set_text(ui_overlay_t overlay, const char *text);
void ui_overlay_set_visible(ui_overlay_t overlay, bool visible);

#ifdef __cplusplus
}
#endif

#endif /* ABBOT_UI_PAGES_H */
