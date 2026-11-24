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

lv_obj_t *ui_overlay_root(ui_overlay_t overlay);
void ui_overlay_set_text(ui_overlay_t overlay, const char *text);
void ui_overlay_set_visible(ui_overlay_t overlay, bool visible);

#ifdef __cplusplus
}
#endif

#endif /* ABBOT_UI_PAGES_H */
