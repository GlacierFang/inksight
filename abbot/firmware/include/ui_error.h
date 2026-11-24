/*
 * ui_error.h —— 统一中文错误文案映射 (零技术术语红线)
 *
 * 契约来源: abbot/doc/03 §四 统一中文文案与技术错误码映射表。
 *
 * 铁律: 状态机与网络层**严禁**散落硬编码提示语, 所有异常一律走
 *       ui_show_friendly_error(ErrorCode err) 查表渲染。
 * 任何一条文案都不得出现 AES / HTTP / signature / Token / Python / Traceback
 * 等技术术语。
 */
#ifndef ABBOT_UI_ERROR_H
#define ABBOT_UI_ERROR_H

#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    ERR_NONE = 0,
    ERR_AUTH_4001,
    ERR_CLOCK_NEED_SYNC,
    ERR_PAIR_4002,
    ERR_ASR_4003,
    ERR_RATE_4290,
    ERR_QUOTA_4291,
    ERR_SERVER_5001,
    ERR_WIFI_AUTH_FAIL,
    ERR_NTP_SYNC_FAIL,
    ERR_OFFLINE_SAVED,
    ERR_OUTBOX_FULL,
    ERR_CAM_AF_FAIL,
    ERR_TTS_FALLBACK,
    ERR_PACK_CORRUPT,
    ERR_TOC_INVALID,
    ERR_OTA_LOW_BAT,
    ERR_OTA_LOW_RSSI,
    ERR_OTA_VERIFY_FAIL,
    ERR_NO_TF_CARD,
    ERR_DISK_FULL,
    ERR_LOW_BAT_WARN,
    ERR_LOW_BAT_SLEEP,
    ERR_PSRAM_OOM,
    ERR_HW_INIT_FAIL,
    ERR_COUNT
} ui_error_code_t;

/* 展示形式: 弹窗卡片 / Toast / 静默 / 常驻 / 全屏 */
typedef enum {
    ERR_SHOW_CARD = 0,   /* 弹窗卡片 */
    ERR_SHOW_TOAST,      /* Toast 提示 */
    ERR_SHOW_SILENT,     /* 静默忽略 */
    ERR_SHOW_RESIDENT,   /* 常驻提醒 */
    ERR_SHOW_FULLSCREEN  /* 全屏提示 */
} err_show_mode_t;

/* 查表取中文文案。未知错误码返回兜底文案。 */
const char *ui_error_text(ui_error_code_t code);
err_show_mode_t ui_error_mode(ui_error_code_t code);

/* 统一出口: 按错误码选择卡片或 Toast 呈现, 并返回是否应当提示。
 * 静默类错误(ERR_TTS_FALLBACK / ERR_TOC_INVALID)不弹任何东西, 只返回 false。 */
bool ui_show_friendly_error(ui_error_code_t code);

#ifdef __cplusplus
}
#endif

#endif /* ABBOT_UI_ERROR_H */
