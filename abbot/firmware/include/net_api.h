/**
 * net_api.h —— 设备端云业务请求层（设备侧接口契约）
 *
 * 与 `include/net_client.h` 的分工（doc/04 §六）：
 *   - net_client.h ：纯 C 安全信封原语（SHA-256 / HMAC / AES-128-CTR / Base64 / 签名 / 验签 / 解密）。
 *                    **不依赖 Arduino.h**，同一份实现可在固件与主机侧 native 测试中编译。
 *   - net_api.h    ：本文件。Wi-Fi 连接、NTP 校时、配对握手、加密 POST 等**业务请求层**接口，
 *                    由 `task_net_worker` 调用。
 *
 * 业务明文的**字段形状真相源是 doc/05 §5.1**，本层不得自行改变字段语义。
 * 签名 PATH 恒为**纯 API 路径**（如 "/api/v1/study/solve"），与网关挂载前缀无关（doc/05 §3.2）。
 *
 * 本头文件**不依赖 Arduino.h** —— 这样 `src/net/net_dispatch.c` 这类调度逻辑可以在主机侧编译与单测。
 */
#ifndef ABBOT_NET_API_H
#define ABBOT_NET_API_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "cards.h"
#include "net_req.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ---------- 网络请求的 op（UI 核 / 相机任务 → 网络任务） ---------- */
#define NET_OP_SOLVE     1 /* 拍题（带图） */
#define NET_OP_VOICE     2 /* 语音问答 */
#define NET_OP_BOOTSTRAP 3 /* 心跳同步 */
#define NET_OP_PAIR      4 /* 配对 */
#define NET_OP_PROFILE   5 /* 档案同步（学段/配额基准，由存储任务读 TF 卡后投递） */
#define NET_OP_REGISTER  6 /* 自动登记（无码配对，doc/04 §1.2） */
#define NET_OP_CARDS    7 /* 学科卡片包（本地解密 + 解析） */
#define NET_OP_OTA_CHECK 8 /* 固件升级检查（doc/04 固件升级章节） */

/** 固件版本号（心跳上报与 OTA 比对用）。 */
#define NET_FW_VERSION "1.0.0"

/**
 * 网络请求（UI 核 / 相机任务 → 网络任务）。
 *
 * 载荷是**业务参数**，不是密文 —— 加签、加密、信封封装全部在网络任务内完成
 * （doc/05 §5.1：net 层是注入与加密的唯一职责，UI 不感知、也不得自行拼装）。
 * 解密后的业务明文由网络任务经 `q_ui_command`（kind=2）投回 Core 1。
 *
 * `jpg` 的**所有权随本请求转移**：相机任务交出后不再释放，由网络任务用完释放。
 */
typedef struct {
    uint8_t op;          /* 见 NET_OP_* */
    uint8_t hint_level;  /* 0=首次求解 1/2=阶梯提示 */
    uint8_t with_voice;  /* 1=边说边拍（携带 PCM） */
    uint8_t reserved;
    char session_id[32]; /* 本题会话号 */
    uint8_t *jpg;        /* 图像缓冲：所有权随请求转移，用完由网络任务释放 */
    uint32_t jpg_len;
    const int16_t *pcm;  /* 16kHz PCM（可为空） */
    uint32_t pcm_samples;
    char context_page[16]; /* NET_OP_VOICE：当前页面 */
    char pack_id[24];      /* NET_OP_VOICE / NET_OP_CARDS：学科包 id */
    char card_id[24];      /* NET_OP_VOICE */
    char pair_code[8];     /* NET_OP_PAIR：6 位配对码 */
    char edu_stage[16];    /* NET_OP_PROFILE：学段 */
    int enrollment_year;   /* NET_OP_PROFILE：入学年 */
    int remaining_quota;   /* NET_OP_PROFILE：本地估算的剩余配额（仅供对账） */
    /*
     * 关联媒体文件在 TF 卡上的路径（如 `/outbox/task_12345.jpg`）。
     * 相机任务拍完就把 JPG 落到卡上（doc/04 §3.5 命名法），把这个路径带过来：
     *   - 上传成功 -> 删掉它（doc/04 §3.5 媒体垃圾文件清理铁律）；
     *   - 上传失败 -> 把它记进离线队列，等联网后补传。
     */
    char media_path[72];
} net_request_t;

/* ===================== 档案注入上下文（doc/05 §5.1） ===================== */
/**
 * 由 net 层从 TF 卡 `/config/profile.json` 读取并维护，组装
 * `/api/v1/study/solve` 与 `/api/v1/study/voice` 明文时**自动注入**：
 *   - edu_stage             缺省或不在枚举内 -> 服务端 4004 -> 屏幕显示「请先在设置里完成学段选择」
 *   - enrollment_year      同上，必填
 *   - device_remaining_quota 仅供服务端对账，不参与放行判定（doc/05 §4.2）
 * UI 状态机不得自行拼装这三个字段。
 */
typedef struct {
    char edu_stage[16];         /* 例："JUNIOR" */
    int enrollment_year;        /* 例：2026 */
    int device_remaining_quota; /* 本地估算上报值，服务端不信任 */
} NetProfileContext;

bool net_profile_load(NetProfileContext *out);
/**
 * 注入档案上下文（`edu_stage` / `enrollment_year` / 本地估算的剩余配额）。
 *
 * **为什么不是 net 层自己去读 TF 卡**：TF 卡由 `task_storage` 独占串行化访问
 * （doc/02 §4.1），net 任务另开一路 SD 访问会与它抢总线。
 * 因此由读档案的一侧解析完 `profile.json` 后调本函数写入 net 层缓存，
 * net 层只负责在组装明文时**自动注入**（doc/05 §5.1）。
 */
bool net_profile_set(const char *edu_stage, int enrollment_year, int remaining_quota);
/** 以网关下发的 `effective_remaining_quota` 覆盖本地显示值（doc/04 §3.1）。 */
bool net_profile_update_quota(int effective_remaining_quota);
/** 读取最近一次由网关覆盖的剩余配额（未联网过则返回 -1）。 */
int net_profile_remaining_quota(void);

/** 设备唯一标识（`esp32s3_xxxxxxxx`），心跳与配对用。 */
const char *net_device_id(void);

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

void net_wifi_init_auto_connect(void);
void net_wifi_start_async_scan(void);
int net_wifi_get_scan_results(struct WifiApInfo *out_list, int max_count);
bool net_wifi_connect_and_save(const char *ssid, const char *password);
enum WifiConnState net_wifi_get_state(void);
int8_t net_wifi_get_rssi(void);

/* ===================== 配对与时间 ===================== */
bool net_sync_ntp_time(void);
/**
 * 旧路径：网页端生成 6 位码、设备敲码换密钥。**默认不暴露**，仅在自动登记不可用时兜底。
 */
bool net_pair_with_code(const char *six_digit_code);

/**
 * 自动登记（无码配对，doc/04 §1.2）。
 *
 * 设备联网后用芯片 MAC 派生的 `device_id`（`esp32s3_<mac8>`）向网关自报身份，
 * 网关幂等签发 `device_secret` 并返回；本函数把密钥写进 NVS。
 * **设备侧零输入**：不弹码、不进虚拟键盘、不要求家长操作。
 */
bool net_auto_register(void);

/** 本机是否已持有设备密钥（NVS 里有 `device_secret`）。 */
bool net_is_paired(void);

/* ===================== 端云加密通信 ===================== */
/**
 * 内部自动完成：明文 AES-128-CTR 加密 -> 请求签名 -> HTTP POST ->
 * 响应签名校验 -> 信封解密。api_path 必须是**纯 API 路径**。
 * out_resp_plain_json 为解密后的业务明文（**堆内存，调用方负责 free**）。
 */
bool net_post_encrypted_json(const char *api_path, const char *plain_req_json,
                             int *out_biz_code, char **out_resp_plain_json);

/* 业务高级封装（内部组装 JSON 并调用 net_post_encrypted_json，HTTP 超时锁定 20s） */
bool net_sync_bootstrap(const char *bootstrap_req_json, int *out_biz_code, char **out_resp_json);
/**
 * 心跳同步（自组装请求明文）：设备标识、固件版本、电量、已装学科包与电子书列表
 * 全部由本层内部采集与填充，调用方不感知。
 */
bool net_sync_bootstrap_auto(int *out_biz_code, char **out_resp_json);
/**
 * 支持纯拍照(pcm_data=NULL)与边说边拍(pcm_data 为 16kHz PCM 缓冲，内部 Base64 编码至 pcm_base64)。
 * edu_stage / enrollment_year / device_remaining_quota 由本层从 profile 缓存自动注入。
 */
bool net_study_solve(const char *session_id, const uint8_t *jpg_data, size_t jpg_len,
                     const uint8_t *pcm_data, size_t pcm_len, uint8_t hint_level,
                     int *out_biz_code, char **out_resp_json);
bool net_study_voice(const char *context_page, const char *pack_id, const char *card_id,
                     const uint8_t *pcm_data, size_t pcm_len,
                     int *out_biz_code, char **out_resp_json);

/* ===================== 固件升级（doc/04 固件升级章节） ===================== */

/**
 * 查更新：`GET /api/v1/ota/check?device_id=&current_ver=`（走标准设备签名 Header）。
 *
 * GET 没有请求体 —— 签名串里的 `SHA256(raw_body)` 取**空串**的摘要
 * （e3b0c442…，与 doc/05 §3.4 的空 body 向量同一组）；签名 PATH 恒为纯 API 路径
 * `/api/v1/ota/check`（不含域名 / query / 挂载前缀）。
 *
 * 应答是加密信封，解开后是 doc/05 §5 约定的字段形状
 * （has_update / latest_ver / force_update / sha256 / size / bin_url / changelog）。
 *
 * 返回 true 表示**已拿到并解开应答**（has_update 为 0 也算成功 ——
 * 「当前已是最新版本」是正常结果，不是错误）。
 */
bool net_ota_check(nr_ota_t *out);

/* ===================== 离线队列（outbox，doc/04 §3.5） ===================== */
/**
 * 把一条拍题请求**入队**（断网或未配对时用，不让用户的拍照白拍）。
 *
 * 关联的 JPG **已经在 TF 卡上了** —— 相机任务拍完就落盘（doc/04 §3.5 命名法），
 * 本函数只负责把「这张图待补传」记进索引，不重复写一遍图。
 *
 * `media_path` 是那张 JPG 在 TF 卡上的路径（如 `/outbox/task_12345.jpg`）。
 * 队列满了（>10 条）时淘汰**最旧**的一条，并删掉它的关联文件
 * （doc/04 §3.5 媒体垃圾文件清理铁律：索引里没有的条目，文件也不能留）。
 *
 * 返回 true = 已入队。
 */
bool net_outbox_enqueue_solve(const char *session_id, const char *media_path,
                               uint8_t hint_level);

/**
 * 补传：把队列里的任务发给网关。
 *
 * **一次只发一条**（HTTP 往返最长 20 秒，跑在网络任务里，发完整个队列会把业务请求饿死）。
 * 调用方循环调用直到返回 0。
 *
 * 每条成功就从索引移除并**删掉关联的 JPG/PCM 文件**（doc/04 §3.5 清理铁律）；
 * 失败则重试次数 +1；超过重试上限或已过期的条目直接丢弃（并删文件）。
 *
 * 返回本次成功补传的条数（0 或 1）。
 */
int net_outbox_flush(void);

/**
 * 删掉一个关联媒体文件（上传成功或队列淘汰时用）。
 * doc/04 §3.5 媒体垃圾文件清理铁律：索引里没有的条目，文件也不能留。
 */
void net_media_cleanup(const char *media_path);

/** 队列里待补传的条数（给 UI 显示「待问队列 N 条」）。 */
int net_outbox_pending(void);

/* ===================== 学科卡片包（doc/04 §四） ===================== */
/**
 * 载入并解密一个学科包的卡片。
 *
 * 解密在**网络任务**里做 —— 设备密钥只存在 NVS，UI 层不碰密钥、不碰加密；
 * 解出来的卡片经 `q_ui_command`（kind=6）投回 Core 1 渲染。
 *
 * `out` 由调用方分配（`card_pack_t`）。
 * 成功返回卡片张数；失败返回负的错误码（`CARDS_ERR_*`）。
 */
int net_cards_load(const char *pack_id, card_pack_t *out);

/* ===================== 资产下载 ===================== */

/** 网关基址（如 `http://ruohan.site/abbot`），用于拼下载地址。 */
void net_gateway_base(char *out, size_t out_cap);

/**
 * 下载一个签名资产、校验完整性并落到 TF 卡（doc/04 §六 第 4 条）。
 *
 * - `signed_url`：完整下载地址（token 在 query 里，不加签不加密）；
 * - `save_path`：设备侧 TF 卡路径（如 `/subjects/m7_geo/cards.enc`）；
 * - `expected_sha256`：传 NULL 时用响应头 `X-File-Sha256` 作期望值（doc/04 §五）。
 *
 * TF 卡由 `task_storage` 独占串行化访问（doc/02 §4.2），所以本函数**不直接写卡**，
 * 而是把已校验的缓冲交给存储任务落盘并等它回执。
 * 返回 true 表示已校验且已落盘。
 */
bool net_download_signed_asset(const char *signed_url, const char *save_path,
                               const char *expected_sha256);

/**
 * 同步心跳应答里「待下载」的资产（课表 / 学科包 / 电子书）。
 *
 * `bootstrap_json` 是心跳应答的解密明文。函数会：
 *   1. 取出 `schedule_download` 与 `pending_packs[]` / `pending_books[]` 里的
 *      `rel_path` 与 `token`；
 *   2. 把服务端相对路径映射成设备 TF 卡路径（doc/04 §2.1 目录树）；
 *   3. 逐个走 `net_download_signed_asset` 下载并落盘（校验 SHA-256）。
 *
 * 返回 true 表示清单解析成功（个别资产下载失败只记日志，不影响其余）。
 */
bool net_sync_pending_assets(const char *bootstrap_json, size_t len);

/* ===================== 调度层（纯 C，可主机侧单测） ===================== */
/**
 * 按 `net_request_t.op` 分派到对应端点。
 *
 * 返回值：**业务码**（0 成功；4004 学段未设置等业务错误；-1 网络/协议失败）。
 * `*out_resp_json` 为解密后的业务明文（**堆内存，调用方负责 free**；无数据时置 NULL）。
 *
 * 调度与「注入 + 加签 + 加密」的职责边界（doc/05 §5.1）：本函数只选端点、传参数；
 * 明文组装与加密一律由 net_api 层完成，UI 不参与。
 */
int net_dispatch(const net_request_t *req, char **out_resp_json);

#ifdef __cplusplus
}
#endif

#endif /* ABBOT_NET_API_H */
