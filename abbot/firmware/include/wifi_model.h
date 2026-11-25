/*
 * wifi_model.h —— Wi-Fi 扫描结果的**解析与连接状态机**（纯逻辑，主机侧可测）
 *
 * 契约来源: abbot/doc/03 §五 第 11 页（Wi-Fi 热点自扫描列表）、
 *           abbot/doc/03 §三 错误码表（ERR_WIFI_AUTH_FAIL：连接超时 12 秒）。
 *
 * 为什么单独抽出来：
 *   - 「页面上显示的是不是真扫描结果」这件事必须可证；
 *   - 本模块**不依赖 Arduino / LVGL / ESP-IDF**，主机侧可用 gcc 直接编译并跑测试
 *     （见 test/test_wifi_model.c）。
 *
 * 分层：
 *   - HAL 层（net_api.cpp）负责真扫描与真连接（异步，不阻塞 UI）；
 *   - 本模块负责把扫描结果**整理成能显示的样子**（排序 / 去重 / 信号分级 / 文案）
 *     并维护**连接阶段状态机**（扫描中 → 连接中 → 成功 / 失败 → 可重试）。
 */
#ifndef ABBOT_WIFI_MODEL_H
#define ABBOT_WIFI_MODEL_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "net_api.h" /* WifiApInfo */

#ifdef __cplusplus
extern "C" {
#endif

/** 一屏最多显示的热点数（与 UI 列表行数一致）。 */
#define WIFI_MODEL_MAX_APS 12
/** SSID 最长 32 字节（802.11 限制）。 */
#define WIFI_MODEL_SSID_MAX 33

/* ---- 信号强度分级（doc/03 §五 第 11 页） ---- */
#define WIFI_SIGNAL_WEAK 0   /* < -75dBm */
#define WIFI_SIGNAL_MID 1    /* -75dBm ~ -60dBm */
#define WIFI_SIGNAL_STRONG 2 /* > -60dBm */

/** 扫描阶段超时（毫秒）：超过就按「扫不到」处理。 */
#define WIFI_SCAN_TIMEOUT_MS 15000
/** 连接阶段超时（毫秒）：doc/03 §三 规定 12 秒。 */
#define WIFI_CONNECT_TIMEOUT_MS 12000
/** 同一个网络最多尝试连接次数（超过提示换网络 / 核对密码）。 */
#define WIFI_CONNECT_MAX_ATTEMPT 3

/** RSSI → 信号分级。 */
int wifi_model_signal_level(int32_t rssi);
/** 信号分级 → 中文文案（"信号强" / "信号中" / "信号弱"）。 */
const char *wifi_model_level_text(int level);

/**
 * 扫描结果整理：按信号从强到弱排序，**同名 SSID 只保留信号最强的一个**
 * （同一 SSID 可能同时有 2.4G 与 5G 两个射频，合并显示更符合使用直觉）。
 * 返回保留后的条数（就地修改数组）。
 */
int wifi_model_sort_aps(WifiApInfo *aps, int count);

/** 把已保存网络的 SSID 标记出来（is_saved = true）。saved_ssid 为 NULL/空则不清标记。 */
void wifi_model_mark_saved(WifiApInfo *aps, int count, const char *saved_ssid);

/**
 * 生成一行显示文案（不含行首光标标记 "> " / "  "，那是 UI 层的活）：
 *   - 已保存的网络前面加 "★ "；
 *   - 隐藏网络（SSID 为空）显示 "(隐藏网络)"；
 *   - 末尾跟信号分级与加密标记。
 * 例："★ Home_WiFi_5G [信号强] [加密]"。
 */
void wifi_model_format_ap(char *out, size_t cap, const WifiApInfo *ap);

/* ===================== 连接阶段状态机 ===================== */

typedef enum {
    WIFI_CONN_IDLE = 0,    /* 还没开始 */
    WIFI_CONN_SCANNING,    /* 正在扫描 */
    WIFI_CONN_CONNECTING,  /* 正在连接 */
    WIFI_CONN_CONNECTED,   /* 连上了 */
    WIFI_CONN_FAILED,      /* 失败（可重试） */
} wifi_conn_phase_t;

typedef struct {
    wifi_conn_phase_t phase;
    int attempt;            /* 当前网络已尝试次数 */
    int max_attempt;        /* 最多尝试次数 */
    uint32_t started_ms;    /* 当前阶段起始时刻 */
    uint32_t timeout_ms;    /* 当前阶段超时 */
    int result_count;       /* 最近一次扫描到的网络数 */
    char ssid[WIFI_MODEL_SSID_MAX]; /* 正在连接 / 已连接的网络 */
} wifi_conn_state_t;

/** 复位状态机（进页时调用）。 */
void wifi_conn_state_init(wifi_conn_state_t *st);

/** 开始扫描。 */
void wifi_conn_scan_start(wifi_conn_state_t *st, uint32_t now_ms);
/** 扫描结束（count = 扫到的网络数，0 = 没扫到）。 */
void wifi_conn_scan_finish(wifi_conn_state_t *st, int count);
/** 扫描阶段是否超时（用于兜底：HAL 没回结果时给温和提示）。 */
bool wifi_conn_scan_expired(const wifi_conn_state_t *st, uint32_t now_ms);

/** 开始连接某个网络。 */
void wifi_conn_connect_start(wifi_conn_state_t *st, const char *ssid, uint32_t now_ms);
/** 连接结束（ok = true 连上了）。 */
void wifi_conn_connect_finish(wifi_conn_state_t *st, bool ok);
/** 连接阶段是否超时。 */
bool wifi_conn_connect_expired(const wifi_conn_state_t *st, uint32_t now_ms);
/** 失败后还能不能再试（未超过尝试上限）。 */
bool wifi_conn_can_retry(const wifi_conn_state_t *st);
/** 当前阶段给用户看的提示文案（温和中文，不含技术术语）。 */
const char *wifi_conn_hint(const wifi_conn_state_t *st);

#ifdef __cplusplus
}
#endif

#endif /* ABBOT_WIFI_MODEL_H */
