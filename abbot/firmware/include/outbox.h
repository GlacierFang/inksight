/**
 * outbox.h —— 离线待问队列（TF 卡索引 + 关联媒体文件）
 *
 * 契约：abbot/doc/04 §2.1 目录树、§3.5 `/outbox/queue.json` 队列契约、
 *        §2.3 防掉电原子写入、PLAN-pair-outbox.md 第 2 条。
 *
 * 要解决的缺口（PLAN 第 2 条）：
 *   拍照 / 语音这类**需要云端**的请求，断网或未配对时不该直接失败 ——
 *   它们应该**入队**，等联网后逐条补传。
 *
 * 四条硬约束（本模块的写法都由它们决定）：
 *   1. **TF 卡由 task_storage 独占串行化**（doc/02 §4.2）——
 *      本模块**不碰 SPI、不直接调 hal_storage_***，一切走
 *      `hal_storage_req_queue()` 请求/应答（与 `ui_data.c` 同一范式）。
 *   2. **队列上限 10 条**（doc/04 §3.5）—— 满了淘汰最旧的一条，
 *      并删掉它的关联媒体文件，不让 TF 卡被离线任务撑爆。
 *   3. **原子写 + `.bak` 自愈**（doc/04 §2.3）—— 索引写走
 *      `STORAGE_OP_WRITE_TEXT_BUF`，由存储任务做 `.tmp → .bak → 重命名` 四步替换；
 *      读时正式文件缺失会回退 `.bak`（`hal_storage_read_text` 内建），
 *      正式文件**内容损坏**时本模块再自己回退 `.bak`（`outbox_load` 里做）。
 *   4. **索引要放得下 10 条** —— 文档 §3.5 的完整字段（10 条约 2.2KB）
 *      超出 `storage_req_t.text` 的 1KB 固定缓冲，所以走 op=12
 *      （`WRITE_TEXT_BUF`，数据放在调用方缓冲里），队列本身不占队列项空间。
 *
 * 本头与 `src/net/outbox.c` 都是**纯 C**（不依赖 Arduino.h），
 * 主机侧可单测（`test/test_outbox.c`）。
 */
#ifndef ABBOT_OUTBOX_H
#define ABBOT_OUTBOX_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ---------- 队列常量（doc/04 §2.1 / §3.5 / PLAN 第 2 条） ---------- */

/** 索引文件路径（TF 卡）。 */
#define OUTBOX_INDEX_PATH "/outbox/queue.json"

/** 索引的 `.bak` 副本（自愈用）。 */
#define OUTBOX_INDEX_BAK_PATH "/outbox/queue.json.bak"

/** 关联媒体的存放目录。 */
#define OUTBOX_DIR "/outbox/"

/** 队列条目上限（doc/04 §3.5：严格 ≤ 10 条）。 */
#define OUTBOX_MAX_TASKS 10

/** 单条关联文件路径上限（含结尾 NUL）。 */
#define OUTBOX_PATH_MAX 72

/** 索引文本上限 —— 10 条满载约 2.2KB，留一倍余量。 */
#define OUTBOX_INDEX_MAX 4096

/**
 * 重试上限：同一条任务补传失败达到该次数后**丢弃**（并删掉关联媒体文件）。
 * 不让一条坏数据永久占着队列位置。
 */
#define OUTBOX_MAX_RETRY 5

/**
 * 过期时限：创建时间超过该秒数的条目在补传时**丢弃**（并删掉关联媒体文件）。
 * 72 小时 = 3 天，够家长周末补传，也不至于留下陈年旧账。
 */
#define OUTBOX_MAX_AGE_SEC (72 * 3600)

/**
 * 时钟可信下限：Unix 时间小于该值说明设备没校过时（RTC 停在 1970 附近），
 * 此时**不做过期判定** —— 否则一开机就会把队列全当成陈年旧账清掉。
 */
#define OUTBOX_TIME_TRUSTED_MIN 1600000000

/* ---------- 条目类型（doc/04 §3.5 的 `type` 字段） ---------- */
#define OUTBOX_TYPE_SOLVE 1 /* 拍题 */
#define OUTBOX_TYPE_VOICE 2 /* 语音 */

/* ---------- 条目状态（doc/04 §3.5 的 `status` 字段） ---------- */
#define OUTBOX_STATUS_PENDING 0 /* 待补传 */
#define OUTBOX_STATUS_RETRY   1 /* 补传失败，等下次 */

/* ---------- 错误码（与 hal_status_t 语义一致，负数） ---------- */
#define OUTBOX_OK              0
#define OUTBOX_ERR_INVALID    -1 /* 参数非法 */
#define OUTBOX_ERR_IO         -3 /* 底层读写失败 */
#define OUTBOX_ERR_NOT_FOUND  -4 /* 索引或关联文件不存在 */
#define OUTBOX_ERR_NO_SPACE   -5 /* 缓冲不足 */
#define OUTBOX_ERR_UNSUPPORTED -6 /* 队列不可用（未挂卡 / 队列未建） */

/* ---------- 队列条目 ---------- */

/**
 * 一条待补传的任务（字段对应 doc/04 §3.5 的队列条目）。
 *
 * PLAN 第 2 条要求的四件事都在：**类型**（type）、**创建时间**（created_at）、
 * **关联文件路径**（image_path / pcm_path）、**重试次数**（retry_count）。
 */
typedef struct {
    char task_id[16];               /* 任务号（= 创建时间戳字符串） */
    uint8_t type;                   /* OUTBOX_TYPE_* */
    uint8_t status;                 /* OUTBOX_STATUS_* */
    uint8_t retry_count;            /* 已补传失败次数 */
    uint8_t hint_level;             /* 阶梯提示档位（0 = 首次求解） */
    uint32_t created_at;            /* 创建时间（Unix 秒；未校时则为 0） */
    char image_path[OUTBOX_PATH_MAX]; /* 关联图片（.jpg）；没有则空串 */
    char pcm_path[OUTBOX_PATH_MAX];   /* 关联音频（.pcm）；没有则空串 */
    char session_id[24];            /* 拍题会话号（补传时沿用） */
    char context[64];               /* 语音上下文："PAGE|pack_id|card_id" */
} outbox_entry_t;

/** 队列（最多 OUTBOX_MAX_TASKS 条）。 */
typedef struct {
    outbox_entry_t items[OUTBOX_MAX_TASKS];
    int count;
} outbox_queue_t;

/* ===================== 纯逻辑层（主机侧可单测） ===================== */

/**
 * 解析索引 JSON（doc/04 §3.5 的形状）。
 * 返回解析出的条数（0 = 空队列）；负数为错误码。
 *
 * 索引格式：
 * ```json
 * {"schema_version":1,"tasks":[
 *   {"task_id":"1795413001","type":"SOLVE","status":"pending","retry_count":0,
 *    "created_at":1795413001,"hint_level":0,
 *    "image_path":"/outbox/task_1795413001.jpg","pcm_path":"",
 *    "session_id":"sess_1795413001","context":""}]}
 * ```
 */
int outbox_parse(const char *json, size_t len, outbox_queue_t *out);

/**
 * 序列化索引 JSON（doc/04 §3.5 的形状）。
 * 返回写入长度（不含结尾 NUL）；缓冲不足返回 `OUTBOX_ERR_NO_SPACE`。
 */
int outbox_serialize(const outbox_queue_t *q, char *out, size_t cap);

/**
 * 入队一条任务。
 *
 * 队列已满时**淘汰最旧的一条**（把它的下标写进 `*out_evicted`，
 * 调用方负责删掉那条的关联媒体文件）；没满时 `*out_evicted` 置 -1。
 *
 * 返回新条目的下标；负数为错误码。
 */
int outbox_enqueue(outbox_queue_t *q, const outbox_entry_t *entry, int *out_evicted);

/** 移除第 `index` 条（移除后其余条目下标前移）。 */
void outbox_remove(outbox_queue_t *q, int index);

/**
 * 按策略挑出「该丢弃」的条目：已过重试上限、或已过期。
 *
 * `out_dropped` 收下标（最多 `max_dropped` 个），调用方据此删掉关联媒体文件。
 * 返回被挑出的条数。
 */
int outbox_select_expired(const outbox_queue_t *q, uint32_t now_ts,
                          int *out_dropped, int max_dropped);

/**
 * 生成关联媒体的路径：`/outbox/task_<unix>_<ms>.jpg`。
 * `ms` 取自设备毫秒计数（同一秒内也不会撞名）。
 * 返回写入长度（不含结尾 NUL）；负数为错误码。
 */
int outbox_make_media_path(char *out, size_t cap, uint32_t unix_ts, uint32_t ms,
                           const char *ext);

/** 按任务号生成关联媒体的路径：`/outbox/task_<task_id>.jpg`（doc/04 §3.5 命名法）。 */
int outbox_make_media_path_by_id(char *out, size_t cap, const char *task_id,
                                 const char *ext);

/* ===================== 传输层（走存储任务队列） ===================== */

/** 读索引到 `q`。成功返回条数；负数为错误码。索引不存在视为空队列。 */
int outbox_load(outbox_queue_t *q);

/** 把队列写回索引（原子写，由存储任务做 `.bak` 自愈）。 */
int outbox_store(const outbox_queue_t *q);

/** 删掉一个文件（关联媒体文件清理）。 */
int outbox_delete_file(const char *path);

/**
 * 取一个文件的大小（字节）。不存在返回 `OUTBOX_ERR_NOT_FOUND`。
 * 入队前用它确认关联媒体真的在卡上（没媒体的条目没有补传价值）。
 */
int outbox_file_size(const char *path, uint32_t *out_size);

/**
 * 读一个关联文件到堆缓冲。
 * 成功时 `*out_buf` 是**调用方负责 free** 的缓冲。
 */
int outbox_read_file(const char *path, uint8_t **out_buf, uint32_t *out_len);

#ifdef __cplusplus
}
#endif

#endif /* ABBOT_OUTBOX_H */
