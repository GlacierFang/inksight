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
/* 自动登记（无码配对）：设备开机联网后自报身份，网关签发密钥并落盘（doc/04 §1.2）。 */
#define NR_PATH_REGISTER  "/api/v1/device/register"
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

/**
 * 拍题应答（doc/05 §5 的 `/api/v1/study/solve` 响应字段）。
 * 协议字段名只存在于 net_req.c 这一层，UI 只认这个结构 —— UI 不解析协议。
 */
typedef struct {
    char status[24];                /* SOLVED / NEED_DRAFT 等 */
    char display_markdown[1024];     /* 逐步解析正文 */
    char feynman_question[256];      /* 费曼反问 */
    char speech_text[256];           /* 语音转写（边说边拍时） */
    bool has_audio;                  /* 是否附带语音 */
    int effective_remaining_quota;   /* 网关判定的剩余配额 */
} nr_solve_result_t;

/** `/config/profile.json` 解析结果（设备本地档案，doc/04 §3.1）。 */
typedef struct {
    char edu_stage[16];
    int enrollment_year;
    int daily_quota;
    int quota_used_count;
} nr_profile_doc_t;

/**
 * 天气读数（doc/05 §5.2：`weather{temp, text}`）。
 *
 * `valid == false` 表示**暂时没有天气**：服务端回空对象、缺字段、缺 temp
 * 都归到这一种（同一个空态），UI 不得显示 0℃ 或破折号。
 */
typedef struct {
    bool valid;                       /* 有没有可用读数 */
    int temp;                         /* 摄氏度（整数） */
    char text[32];                    /* 中文天气描述，如「晴间多云」 */
} nr_weather_t;

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

/**
 * `POST /api/v1/device/register` 明文（自动登记，**明文请求，不加签不加密**）。
 *
 * 设备开机联网后自动调用，网关按 `device_id` 幂等签发 `device_secret`：
 * 同一个 device_id 重复登记会拿到**同一份**密钥（doc/04 §1.2）。
 *
 * `fw_version` 可传 NULL（不下发固件版本）。
 */
int nr_build_register_plain(const char *device_id, const char *fw_version,
                            char *out, size_t out_cap);

/**
 * `POST /api/v1/sync/bootstrap` 明文（心跳同步，doc/05 §5）。
 *
 * `city` 是**上报的城市名**（doc/05 §5.2）：服务端解密后读 `payload["city"]`，
 * 写进自己的权威源并用它抓天气。传 NULL 或空串 = 不带这个字段
 * （服务端不会因此清掉已有城市）。
 */
int nr_build_bootstrap_plain(const char *device_id, const char *fw_version,
                             int battery_pct, uint32_t schedule_updated_at,
                             const char *local_packs_json, const char *local_books_json,
                             const char *city,
                             char *out, size_t out_cap);

/**
 * 解析 `/config/profile.json`，取学段与配额基准。
 * 返回 0 成功；-1 解析失败（缺字段或格式不对）。
 */
int nr_parse_profile(const char *json, size_t len, nr_profile_doc_t *out);

/* ---------- JSON 数组遍历（bootstrap 的待同步清单是数组） ---------- */

/** 数组 `key` 的元素个数（空数组返回 0；没有该键返回 -1）。 */
int nr_array_count(const char *json, size_t len, const char *key);

/**
 * 取对象字段 `key` 指向的**对象片段**（`{...}` 内部），`*out_start` / `*out_len`
 * 指向该对象在原 JSON 中的位置。
 *
 * 为什么需要它：bootstrap 应答里 `schedule_download` 是对象，而 `rel_path` 这个键
 * 在 `pending_packs[]` 的每个元素里也出现过 —— 平铺查找会拿错那一条。
 * 返回 0 成功；-1 没有该键或不是对象。
 */
int nr_json_get_object(const char *json, size_t len, const char *key,
                       const char **out_start, size_t *out_len);

/**
 * 取数组 `key` 的第 `index` 个元素（0 起），
 * `*out_start` / `*out_len` 指向该元素在原 JSON 中的位置（不含外层空白）。
 * 返回 0 成功；-1 下标越界或没有该键。
 */
int nr_array_item(const char *json, size_t len, const char *key, int index,
                  const char **out_start, size_t *out_len);

/* ---------- 待同步资产（bootstrap 应答的 pending_packs / pending_books） ---------- */

#define NR_MAX_PENDING 16

typedef struct {
    char id[32];      /* pack_id / book_id */
    char rel_path[80];/* 服务端 storage/ 下的相对路径 */
    char token[200];  /* 短时签名下载 token（doc/05 §3.3） */
} nr_pending_t;

/**
 * 解析待同步资产数组。`array_key` 取 `pending_packs` / `pending_books`，
 * `id_key` 取 `pack_id` / `book_id`。
 * 返回 0 成功（`*out_count` 为解析到的条数）；-1 参数非法。
 */
int nr_parse_pending(const char *json, size_t len, const char *array_key, const char *id_key,
                     nr_pending_t *out, int max, int *out_count);

/* ---------- OTA 检查应答（doc/05 §5 的 /api/v1/ota/check） ---------- */

typedef struct {
    int has_update;
    int force_update;      /* 网关要求必须升级 */
    char latest_ver[24];
    char sha256[65];
    uint32_t size;
    char bin_url[224];
    char changelog[256];   /* 更新说明（超长截断） */
} nr_ota_t;

/** 解析 OTA 检查应答。返回 0 成功；-1 参数非法。 */
int nr_parse_ota(const char *json, size_t len, nr_ota_t *out);

/* ---------- 下载链接拼装 ---------- */

/**
 * 拼签名资产下载地址：`{base}/api/v1/content/download?token={token}`。
 * base 是网关基址（`http://<host><mount>`）。返回写入长度，-1 参数非法或被截断。
 */
int nr_build_download_url(const char *base, const char *token, char *out, size_t out_cap);

/* ---------- 服务端相对路径 -> 设备 TF 卡路径 ---------- */

/**
 * 把服务端 `storage/` 下的相对路径映射成设备 TF 卡路径：
 *   packs/<id>/xxx   -> /subjects/<id>/xxx
 *   books/<name>.xxx -> /books/<name>.xxx
 *   schedules/xxx    -> /config/xxx
 * 返回 0 成功；-1 无法识别的前缀。
 */
int nr_map_rel_path(const char *rel_path, char *out, size_t out_cap);

/* ---------- JSON 取值（扁平对象，值为 string / number / bool） ---------- */

/** 取字符串字段（自动解转义）。返回写入长度（不含结尾 NUL），负数为错误。 */
int nr_json_get_string(const char *json, size_t len, const char *key,
                       char *out, size_t out_cap);
/**
 * 取字符串字段，**超长时截断**而不是失败。
 * 解题步骤正文可能很长，截断比整条丢弃可用；返回值是写入的字节数（不含结尾 NUL）。
 */
int nr_json_get_string_trunc(const char *json, size_t len, const char *key,
                              char *out, size_t out_cap);

/**
 * 解析拍题应答明文 -> `nr_solve_result_t`。
 * 返回 0 成功（字段缺失时对应位置为空串 / 默认值），-1 入参非法。
 */
int nr_parse_solve_result(const char *json, size_t len, nr_solve_result_t *out);

/**
 * 从 bootstrap 应答明文里取天气 -> `nr_weather_t`。
 *
 * 返回值：
 *   0  取到可用读数（temp 与 text 都有）；
 *   1  应答里没有可用天气（缺 `weather`、空对象、缺 temp、缺 text —— 全是同一个空态）；
 *  -1  入参非法。
 */
int nr_parse_weather(const char *json, size_t len, nr_weather_t *out);

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
