/**
 * net_api.h —— 设备端云业务请求层（Arduino / ESP32 侧）
 *
 * 与 `include/net_client.h` 的分工（doc/04 §六）：
 *   - net_client.h ：纯 C 安全信封原语（SHA-256 / HMAC / AES-CTR / Base64 / 签名 / 验签 / 解密），
 *                    **不依赖 Arduino.h**，可在固件与主机侧 native 测试中编译；
 *   - net_api.h    ：本文件。Wi-Fi / NTP / 配对 / 加密 POST / 资产下载等**业务请求层**，
 *                    依赖 Arduino 侧能力，供 `task_net_worker` 调用。
 *
 * 业务明文的字段形状真相源是 doc/05 §5.1；本层不得自行改变字段语义。
 * 签名 PATH 恒为**纯 API 路径**（如 "/api/v1/study/solve"），与网关挂载前缀无关（doc/05 §3.2）。
 */
#ifndef ABBOT_NET_API_H
#define ABBOT_NET_API_H

#include <Arduino.h>
#include <stddef.h>
#include <stdint.h>

/* ===================== 档案注入上下文（doc/05 §5.1） ===================== */
/**
 * 由 net 层从 TF 卡 `/config/profile.json` 读取并维护，组装
 * `/api/v1/study/solve` 与 `/api/v1/study/voice` 明文时**自动注入**：
 *   - edu_stage             缺省或不在枚举内 -> 服务端 4004 -> 屏幕显示「请先在设置里完成学段选择」
 *   - enrollment_year      同上，必填
 *   - device_remaining_quota 仅供服务端对账，不参与放行判定（doc/05 §4.2）
 * UI 状态机不得自行拼装这三个字段。
 */
struct NetProfileContext {
    char edu_stage[16];         /* 例："JUNIOR" */
    int enrollment_year;        /* 例：2026 */
    int device_remaining_quota; /* 本地估算上报值，服务端不信任 */
};

bool net_profile_load(NetProfileContext* out);
/** 以网关下发的 `effective_remaining_quota` 覆盖本地显示值（doc/04 §3.1）。 */
bool net_profile_update_quota(int effective_remaining_quota);
/**
 * 注入档案上下文（`edu_stage` / `enrollment_year` / 本地估算的剩余配额）。
 *
 * **为什么不是 net 层自己去读 TF 卡**：TF 卡由 `task_storage` 独占串行化访问
 * （doc/02 §4.1），net 任务另开一路 SD 访问会与它抢总线。
 * 因此由读档案的一侧解析完 `profile.json` 后调本函数写入 net 层缓存，
 * net 层只负责在组装明文时**自动注入**（doc/05 §5.1）。
 */
bool net_profile_set(const char* edu_stage, int enrollment_year, int remaining_quota);
/** 读取最近一次由网关覆盖的剩余配额（未联网过则返回 -1）。 */
int net_profile_remaining_quota(void);

/* ===================== Wi-Fi ===================== */
struct WifiApInfo {
    char ssid[33];
    int32_t rssi;
    bool is_encrypted;
    bool is_saved;
};

enum WifiConnState {
    WIFI_STATE_DISCONNECTED = 0,
    WIFI_STATE_SCANNING,
    WIFI_STATE_CONNECTING,
    WIFI_STATE_CONNECTED,
    WIFI_STATE_AUTH_FAILED
};

void net_wifi_init_auto_connect();
void net_wifi_start_async_scan();
int net_wifi_get_scan_results(WifiApInfo* out_list, int max_count);
bool net_wifi_connect_and_save(const char* ssid, const char* password);
WifiConnState net_wifi_get_state();
int8_t net_wifi_get_rssi();

/* ===================== 配对与时间 ===================== */
bool net_sync_ntp_time();
bool net_pair_with_code(const char* six_digit_code);

/* ===================== 端云加密通信 ===================== */
/**
 * 内部自动完成：明文 AES-128-CTR 加密 -> 请求签名 -> HTTP POST ->
 * 响应签名校验 -> 信封解密。api_path 必须是**纯 API 路径**。
 * out_resp_plain_json 为解密后的业务明文（调用方负责 free）。
 */
bool net_post_encrypted_json(const char* api_path, const char* plain_req_json,
                             int* out_biz_code, char** out_resp_plain_json);

/* 业务高级封装（内部组装 JSON 并调用 net_post_encrypted_json，HTTP 超时锁定 20s） */
bool net_sync_bootstrap(const char* bootstrap_req_json, int* out_biz_code, char** out_resp_json);
/**
 * 支持纯拍照(pcm_data=NULL)与边说边拍(pcm_data 为 16kHz PCM 缓冲，内部 Base64 编码至 pcm_base64)。
 * edu_stage / enrollment_year / device_remaining_quota 由本层从 profile.json 自动注入。
 */
bool net_study_solve(const char* session_id, const uint8_t* jpg_data, size_t jpg_len,
                     const uint8_t* pcm_data, size_t pcm_len, uint8_t hint_level,
                     int* out_biz_code, char** out_resp_json);
bool net_study_voice(const char* context_page, const char* pack_id, const char* card_id,
                     const uint8_t* pcm_data, size_t pcm_len,
                     int* out_biz_code, char** out_resp_json);

#endif /* ABBOT_NET_API_H */
