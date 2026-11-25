/*
 * ui_ota_page.h —— 固件升级页（PAGE_OTA）的正文与刷新接口
 *
 * 单独一个头 + 一个实现文件（src/ui/ui_ota_page.cpp），是为了让固件升级页
 * 的改动不与其它页面的改动互相踩。
 *
 * 数据源只有一个：ota_get_status()（网络任务写入，UI 只读）。
 * UI 不发网络请求、不碰密钥、不做校验 —— 那是网络任务与 OTA 客户端的活。
 */
#ifndef ABBOT_UI_OTA_PAGE_H
#define ABBOT_UI_OTA_PAGE_H

#include "lvgl.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * 建固件升级页的正文（当前版本 / 最新版本 / 更新说明 / 下载进度 / 结果提示）。
 * 正文标签由本模块持有并定时刷新；**不要**在别处删除这个标签。
 */
void ui_ota_page_build(lv_obj_t *content);

/** 立刻刷新一次正文（进入页面时调，让用户不用等下一个刷新周期）。 */
void ui_ota_page_refresh(void);

#ifdef __cplusplus
}
#endif

#endif /* ABBOT_UI_OTA_PAGE_H */
