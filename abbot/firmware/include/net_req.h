/**
 * net_req.h —— 设备端请求组装与响应解析（纯 C，**不依赖 Arduino.h**）
 *
 * 与 `include/net_client.h` 同层：同一份实现可在 ESP32 固件与主机侧 native 测试中编译。
 * `net_client.h` 管密码学原语（签名/加密/解密），本文件管**协议形状**：
 *   - 组装业务明文 JSON（study/solve、study/voice、pair）；
 *   - 按 doc/05 §5.1 自动注入 `edu_stage` / `enrollment_year` / `device_remaining_quota`；
 *   - 拼 URL（区分「签名用的纯路径」与「实际请求用的挂载前缀路径」）；
 *   - 解析 HTTP 响应（Content-Length 与 chunked 两种收包方式），取出
 *     `X-Resp-Signature` 与响应体原始字节（验签必须用**原始字节**）。
 *
 * 铁律（doc/05 §3.2）：签名 PATH 恒为**纯 API 路径**，不含域名、不含 query、
 * 不含 `/abbot` 挂载前缀；实际请求 URL 才带挂载前缀。
 */
#ifndef ABBOT_NET_REQ_H
#define ABBOT_NET_REQ_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ---------- 纯 API 路径（签名口径，doc/05 §3.2） ---------- */
#define NR_PATH_PING      "/api/v1/ping"
#define NR_PATH_PAIR      "/api/v1/pair"
#define NR_PATH_BOOTSTRAP "/api/v1/sync/bootstrap"
#define NR_PATH_SOLVE     "/api/v1/study/solve"
#define NR_PATH_VOICE     "/api/v1/study/voice"
#define NR_PATH_OTA_CHECK "/api/v1/ota/check"

#define NR_HTTP_TIMEOUT_MS 20000

/* ---------- 档案注入上下文（doc/05 §5.1） ---------- */
typedef struct {
    char edu_stage[16];         /* 例："JUNIOR" */
    int enrollment_year;        /* 例：2026 */
    int device_remaining_quota; /* 仅供对账，不参与放行判定 */
} nr_profile_t;

/** `/config/profile.json` 解析结果（设备本地档案，doc/04 §3.1）。 */
typedef struct {
    char edu_stage[16];
    int enrollment_year;
    int daily_quota;
    int quota_used_count;
} nr_profile_doc_t;

/* ---------- HTTP 响应解析结果 ---------- */
typedef struct {
    int status_code;             /* HTTP 状态码，未解析出时为 0 */
    char resp_signature[65];     /* `X-Resp-Signature`（小写十六进制），缺失时为空串 */
    const char *body;            /* 响应体原始字节起点（指向调用方给的 raw 缓冲） */
    size_t body_len;             /* 响应体长度（chunked 时为解码后的长度） */
    bool chunked;                /* 是否走了 chunked 收包 */
} nr_http_response_t;

/**
 * 拼实际请求 URL：`http://<host><mount><path>`。
 * mount 传 "/abbot" 或 ""；path 是纯 API 路径。
 * 返回写入长度（不含结尾 NUL），负数为错误。
 */
int nr_build_url(const char *host, const char *mount, const char *path,
                 char *out, size_t out_cap);

/** OTA 检查的实际请求 URL（含 query），**签名仍用 NR_PATH_OTA_CHECK 纯路径**。 */
int nr_build_ota_url(const char *host, const char *mount,
                     const char *device_id, const char *current_ver,
                     char *out, size_t out_cap);

/* ---------- 业务明文组装（out 可为 NULL，此时只算所需长度，含结尾 NUL） ---------- */

/** `POST /api/v1/study/solve` 明文（doc/05 §5.1）。 */
int nr_build_solve_plain(const nr_profile_t *prof, const char *session_id,
                         const uint8_t *jpg, size_t jpg_len,
                         const uint8_t *pcm, size_t pcm_len,
                         uint8_t hint_level,
                         char *out, size_t out_cap);

/** `POST /api/v1/study/voice` 明文。local_time 传 "YYYY-MM-DDTHH:MM:SS"。 */
int nr_build_voice_plain(const nr_profile_t *prof, const char *context_page,
                         const char *pack_id, const char *card_id,
                         const uint8_t *pcm, size_t pcm_len,
                         const char *local_time,
                         char *out, size_t out_cap);

/** `POST /api/v1/pair` 明文（**明文请求，不加签不加密**，此时双方都还没有密钥）。 */
int nr_build_pair_plain(const char *device_id, const char *pair_code,
                        char *out, size_t out_cap);

/** `POST /api/v1/sync/bootstrap` 明文（心跳同步，doc/05 §5）。 */
int nr_build_bootstrap_plain(const char *device_id, const char *fw_version,
                             int battery_pct, uint32_t schedule_updated_at,
                             const char *local_packs_json, const char *local_books_json,
                             char *out, size_t out_cap);

/**
 * 解析 `/config/profile.json`，取学段与配额基准。
 * 返回 0 成功；-1 解析失败（缺字段或格式不对）。
 */
int nr_parse_profile(const char *json, size_t len, nr_profile_doc_t *out);

/* ---------- JSON 取值（扁平对象，值为 string / number / bool） ---------- */

/** 取字符串字段（自动解转义）。返回写入长度（不含结尾 NUL），负数为错误。 */
int nr_json_get_string(const char *json, size_t len, const char *key,
                       char *out, size_t out_cap);
int nr_json_get_int(const char *json, size_t len, const char *key, int fallback);
int nr_json_get_bool(const char *json, size_t len, const char *key, bool fallback);

/* ---------- HTTP 响应解析 ---------- */

/**
 * 解析 `状态行 + 头 + 体` 的原始字节。支持 Content-Length 与 chunked 两种收包方式。
 * body 指针指向 raw 内部（chunked 时为解码后的原地结果），raw 需由调用方保持存活。
 * 返回 0 成功，负数为错误。
 */
int nr_parse_http_response(char *raw, size_t raw_len, nr_http_response_t *out);

#ifdef __cplusplus
}
#endif

#endif /* ABBOT_NET_REQ_H */
