/**
 * cards.h —— 学科卡片包（解包 / 解密 / 渲染的数据形状）
 *
 * 契约：abbot/doc/04 §2.1 目录树（`/subjects/<pack_id>/cards.enc` + `index.idx`）、
 *        abbot/doc/04 §四（`index.idx` 索引头与 CTR 局部块偏移精确算法）。
 *
 * 包格式（与服务端 `server/app/pack_builder.py` 逐字对齐）：
 *   - `cards.enc` = `IV(16B) || AES-128-CTR(明文 JSONL 流)`；
 *     密钥按 `meta.json` 的 `key_type` 取：`device` → `device_secret[0:16]`，
 *     `demo` → 固定 16 字节 ASCII 密钥。
 *   - 明文是 JSONL，一行一张卡：
 *     `{"id":"c0000","question":"…","answer":"…","hint1":"…","hint2":"…","analysis":"…"}`
 *   - `index.idx` = `"IDX1"` + `uint16 版本(1)` + `uint16 张数 N` + `uint32 CRC32`
 *     + N 个 `uint32` 起始字节偏移（第 0 张恒为 `16`，即跳过文件头 IV）。
 *
 * **分层铁律**：解密只发生在**网络任务**（设备密钥只存在 NVS，UI 层不碰密钥、不碰加密）；
 * 解出的卡片经 `q_ui_command`（kind=6）投回 Core 1 渲染。
 * 读卡一律走存储任务队列（doc/02 §4.2：TF 卡由 task_storage 独占串行化）。
 *
 * 本头是**纯 C**（不依赖 Arduino.h），主机侧可单测（`test/test_cards.c`）。
 */
#ifndef ABBOT_CARDS_H
#define ABBOT_CARDS_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ---------- 数据形状 ---------- */

/** 一个学科包里最多带多少张卡。 */
#define CARDS_MAX_COUNT 64

/** 单卡各字段上限（含结尾 NUL）。 */
#define CARD_ID_MAX       16
#define CARD_QUESTION_MAX 256
#define CARD_ANSWER_MAX   512

/** 一张卡。 */
typedef struct {
    char id[CARD_ID_MAX];             /* 卡号（如 c0007） */
    char question[CARD_QUESTION_MAX]; /* 题干（正面） */
    char answer[CARD_ANSWER_MAX];     /* 答案（背面） */
} card_item_t;

/** 一个学科包的卡片集。 */
typedef struct {
    char pack_id[32];
    int count;
    card_item_t items[CARDS_MAX_COUNT];
} card_pack_t;

/* ---------- 错误码（负数，语义与 hal_status_t 一致） ---------- */
#define CARDS_OK           0
#define CARDS_ERR_INVALID -1 /* 参数非法 */
#define CARDS_ERR_IO      -3 /* 底层读写失败 */
#define CARDS_ERR_NOTFOUND -4 /* 包或文件不存在 */
#define CARDS_ERR_CORRUPT -5 /* 索引 / 密文损坏 */
#define CARDS_ERR_KEY     -6 /* 没有可用密钥（未配对） */

/* ---------- 纯逻辑层（主机侧可单测） ---------- */

/**
 * 解析 `index.idx`（doc/04 §4.1）。
 *
 * 校验：魔数 `"IDX1"`、版本号 `1`、张数与偏移区长度一致、偏移区 CRC32 对得上、
 * 每个偏移 `>= 16`（不能指到 IV 里去）。
 *
 * 成功返回张数；失败返回负的错误码（`CARDS_ERR_CORRUPT` / `CARDS_ERR_INVALID`）。
 */
int cards_parse_index(const uint8_t *idx, size_t idx_len,
                      uint32_t *out_offsets, int max_count, int *out_count);

/**
 * 按 doc/04 §4.2 的公式**局部解密**一段密文（一张卡的 JSONL 行）。
 *
 * `payload_offset` 是这段密文在**去掉 IV 之后**的载荷区里的起始偏移；
 * `iv` 是 `cards.enc` 文件头那 16 字节（`IV_base`）。
 *
 * 公式：`block_index = payload_offset / 16`；`byte_in_block = payload_offset % 16`；
 * `nonce_counter = BigEndian128(IV_base) + block_index`。
 *
 * 输出是**明文**（不含结尾 NUL）。成功返回明文长度；失败返回负的错误码。
 */
int cards_decrypt_range(const uint8_t key[16], const uint8_t iv[16],
                        const uint8_t *ct, size_t ct_len, uint32_t payload_offset,
                        char *out_plain, size_t out_cap);

/**
 * 解析一行卡片 JSON（解转义）到 `card_item_t`。
 * 成功返回 0；失败返回负的错误码。
 */
int cards_parse_item(const char *json, size_t len, card_item_t *out);

/* ---------- 传输层（走存储任务队列） ---------- */

/**
 * 载入并解密一个学科包的卡片。
 *
 * `device_key` 是设备密钥的前 16 字节（`device_secret[0:16]`）；
 * 包自身的 `meta.json` 若标的是 `key_type=demo`，则改用固定演示密钥。
 *
 * `out` 由调用方分配（`card_pack_t`）。
 * 成功返回卡片张数；失败返回负的错误码。
 */
int cards_load(const char *pack_id, const uint8_t device_key[16], card_pack_t *out);

/** 某张卡在密文载荷区里的长度（由相邻偏移与文件大小推出）。 */
int cards_item_length(const uint32_t *offsets, int count, int index, uint32_t file_size);

#ifdef __cplusplus
}
#endif

#endif /* ABBOT_CARDS_H */
