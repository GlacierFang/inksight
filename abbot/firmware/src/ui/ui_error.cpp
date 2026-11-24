/*
 * ui_error.cpp —— 错误码到中文文案的唯一查表处
 *
 * 文案逐条抄自 abbot/doc/03 §四, 一字不改。
 * 静默类错误不返回任何文案。
 */
#include "ui_error.h"

#include "ui_pages.h"
#include "ui_router.h"

typedef struct {
    const char *text;
    err_show_mode_t mode;
} ui_error_entry_t;

static const ui_error_entry_t s_table[ERR_COUNT] = {
    [ERR_NONE]            = { "",                                 ERR_SHOW_SILENT },
    [ERR_AUTH_4001]       = { "设备时钟或安全凭证已过期，请联网校准时间或重新配对。", ERR_SHOW_CARD },
    [ERR_CLOCK_NEED_SYNC] = { "设备时间尚未校准，请连接无线网络自动校时后再使用在线导学。", ERR_SHOW_CARD },
    [ERR_PAIR_4002]       = { "配对码无效或已过期，请在网页端重新生成。",           ERR_SHOW_TOAST },
    [ERR_ASR_4003]        = { "没有听清刚才的话，请靠近麦克风再说一次。",           ERR_SHOW_TOAST },
    [ERR_RATE_4290]       = { "老师正在思考上一题，请稍等几秒再试。",               ERR_SHOW_TOAST },
    [ERR_QUOTA_4291]      = { "今日深度导学次数已用完，明天再来或先刷本地卡片吧。",   ERR_SHOW_CARD },
    [ERR_SERVER_5001]     = { "云端暂时开小差了，题目已帮你存进待问收件箱。",         ERR_SHOW_CARD },
    [ERR_WIFI_AUTH_FAIL]  = { "无线网络连接失败，请核对密码后重试。",               ERR_SHOW_TOAST },
    [ERR_NTP_SYNC_FAIL]   = { "自动校时暂未成功，可在设置中手动调整时间。",         ERR_SHOW_TOAST },
    [ERR_OFFLINE_SAVED]   = { "已自动存入待问收件箱（联网后自动发送）。",           ERR_SHOW_TOAST },
    [ERR_OUTBOX_FULL]     = { "待问收件箱已满（10条），请先联网同步后再试。",       ERR_SHOW_TOAST },
    [ERR_CAM_AF_FAIL]     = { "画面对焦不够清晰，请保持平稳后重新拍摄。",           ERR_SHOW_TOAST },
    [ERR_TTS_FALLBACK]    = { "",                                 ERR_SHOW_SILENT },
    [ERR_PACK_CORRUPT]    = { "该学习卡片包文件不完整，请在网页端重新同步。",         ERR_SHOW_CARD },
    [ERR_TOC_INVALID]     = { "",                                 ERR_SHOW_SILENT },
    [ERR_OTA_LOW_BAT]     = { "当前电量偏低，请先充电至 35% 以上再升级固件。",       ERR_SHOW_TOAST },
    [ERR_OTA_LOW_RSSI]    = { "当前无线信号较弱，请靠近路由器后再升级固件。",       ERR_SHOW_TOAST },
    [ERR_OTA_VERIFY_FAIL] = { "固件安装包校验未通过，已取消升级，设备可正常使用。",   ERR_SHOW_CARD },
    [ERR_NO_TF_CARD]      = { "未检测到存储卡，请插入 TF 卡后继续使用。",           ERR_SHOW_RESIDENT },
    [ERR_DISK_FULL]       = { "存储卡空间已满，请清理部分旧缓存后再试。",           ERR_SHOW_TOAST },
    [ERR_LOW_BAT_WARN]    = { "电量偏低，请及时连接充电线。",                       ERR_SHOW_TOAST },
    [ERR_LOW_BAT_SLEEP]   = { "电量即将耗尽，设备即将进入休眠保护，充电后长按摇杆唤醒。", ERR_SHOW_FULLSCREEN },
    [ERR_PSRAM_OOM]       = { "内存空间紧张，请稍后再试。",                         ERR_SHOW_TOAST },
    [ERR_HW_INIT_FAIL]    = { "设备硬件自检异常，请检查排线连接后重启电源。",         ERR_SHOW_FULLSCREEN },
};

const char *ui_error_text(ui_error_code_t code)
{
    if (code >= ERR_COUNT) {
        return "设备遇到一点小状况，请稍后再试。";
    }
    return s_table[code].text;
}

err_show_mode_t ui_error_mode(ui_error_code_t code)
{
    if (code >= ERR_COUNT) {
        return ERR_SHOW_TOAST;
    }
    return s_table[code].mode;
}

bool ui_show_friendly_error(ui_error_code_t code)
{
    err_show_mode_t mode = ui_error_mode(code);
    const char *text = ui_error_text(code);

    switch (mode) {
    case ERR_SHOW_SILENT:
        return false;

    case ERR_SHOW_CARD:
    case ERR_SHOW_RESIDENT:
    case ERR_SHOW_FULLSCREEN:
        /* 提醒弹窗承载: 短按关闭后恢复原页 (doc/03 §3.1) */
        ui_overlay_set_text(OVERLAY_REMINDER, text);
        ui_overlay_push(OVERLAY_REMINDER);
        return true;

    case ERR_SHOW_TOAST:
    default:
        ui_overlay_set_text(OVERLAY_TOAST, text);
        ui_overlay_push(OVERLAY_TOAST);
        return true;
    }
}
