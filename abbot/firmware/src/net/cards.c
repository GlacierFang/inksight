/**
 * cards.c —— 学科卡片包的解包与解密
 *
 * 契约：abbot/doc/04 §2.1 目录树、§四（`index.idx` 索引头 + CTR 局部块偏移精确算法）。
 * 包格式与服务端 `server/app/pack_builder.py` 逐字对齐（见 `include/cards.h` 的说明）。
 *
 * 分两层：
 *   - **纯逻辑层**（索引解析 / 局部解密 / 单卡 JSON 解析）：不碰 IO，主机侧可单测。
 *   - **传输层**（读 `meta.json` / `index.idx` / `cards.enc`）：走
 *     `hal_storage_req_queue()` 请求/应答 —— **不碰 SPI、不直接调 hal_storage_***
 *     （doc/02 §4.2：TF 卡由 task_storage 独占串行化）。
 *
 * 解密为什么在这一层：设备密钥只存在 NVS，只有网络任务拿得到；
 * 解出来的卡片经 `q_ui_command`（kind=6）投回 Core 1 渲染 —— UI 不碰密钥、不碰加密。
 */

#include "cards.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "net_client.h"
#include "storage_io.h"

/* 请求应答超时：读 TF 卡一个小文件正常在几十毫秒内完成，给 2 秒余量。 */
#define CARDS_TIMEOUT_MS 2000

/* 单张卡的密文长度上限（doc/04 §4.2 的越界防线：cipher_len <= 4096）。 */
#define CARDS_ITEM_MAX 4096

/*
 * 固定演示密钥（`key_type=demo` 的包用）。
 *
 * 与服务端 `pack_builder.py` 的 `DEMO_KEY` **逐字一致**，且必须是**恰好 16 字节**
 * —— AES-128 的密钥长度是硬约束。（服务端原来的 `b"STUDY_DEMO_KEY_16"` 是 17 字节，
 * 打包会直接崩，已修正为 16 字节。）
 */
static const uint8_t CARDS_DEMO_KEY[16] = {
    'S', 'T', 'U', 'D', 'Y', '_', 'D', 'E', 'M', 'O', '_', 'K', 'E', 'Y', '1', '6'
};

/* ============================================================
 * 纯逻辑层
 * ============================================================ */

/**
 * CRC-32（IEEE 802.3，多项式 0xEDB88320 反转）—— 与服务端 `zlib.crc32` 一致。
 *
 * 自己实现而不用 mbedtls：这一层要保持「纯 C + 主机侧可单测」，
 * 少一个外部依赖少一处版本差异（mbedtls 的 crc32 在 2.x 与 3.x 命名不同）。
 */
static uint32_t cards_crc32(const uint8_t *data, size_t len)
{
    uint32_t crc = 0xFFFFFFFFu;
    for (size_t i = 0; i < len; i++) {
        crc ^= data[i];
        for (int b = 0; b < 8; b++) {
            uint32_t mask = (uint32_t)0 - (crc & 1u);
            crc = (crc >> 1) ^ (0xEDB88320u & mask);
        }
    }
    return crc ^ 0xFFFFFFFFu;
}

int cards_parse_index(const uint8_t *idx, size_t idx_len,
                      uint32_t *out_offsets, int max_count, int *out_count)
{
    if (idx == NULL || out_offsets == NULL || out_count == NULL || max_count <= 0) {
        return CARDS_ERR_INVALID;
    }
    *out_count = 0;
    /* 头部 12 字节：魔数 4 + 版本 2 + 张数 2 + CRC 4 */
    if (idx_len < 12) {
        return CARDS_ERR_CORRUPT;
    }
    if (idx[0] != 'I' || idx[1] != 'D' || idx[2] != 'X' || idx[3] != '1') {
        return CARDS_ERR_CORRUPT;
    }
    uint16_t version = (uint16_t)(((uint16_t)idx[4] << 8) | idx[5]);
    uint16_t count = (uint16_t)(((uint16_t)idx[6] << 8) | idx[7]);
    uint32_t crc = ((uint32_t)idx[8] << 24) | ((uint32_t)idx[9] << 16) |
                   ((uint32_t)idx[10] << 8) | (uint32_t)idx[11];
    if (version != 1) {
        return CARDS_ERR_CORRUPT;
    }
    if (count == 0) {
        return CARDS_ERR_CORRUPT;   /* 没有卡的包不算有效学科包 */
    }
    if (count > (uint16_t)max_count) {
        return CARDS_ERR_CORRUPT;   /* 张数超出本固件能处理的规模 */
    }
    /* 偏移区长度必须与张数一致 —— 不一致说明文件被截断 */
    if (idx_len < (size_t)12 + (size_t)count * 4u) {
        return CARDS_ERR_CORRUPT;
    }
    const uint8_t *area = idx + 12;
    if (cards_crc32(area, (size_t)count * 4u) != crc) {
        return CARDS_ERR_CORRUPT;
    }
    for (int i = 0; i < (int)count; i++) {
        uint32_t off = ((uint32_t)area[i * 4 + 0] << 24) | ((uint32_t)area[i * 4 + 1] << 16) |
                       ((uint32_t)area[i * 4 + 2] << 8) | (uint32_t)area[i * 4 + 3];
        /* 越界防线：第 0 张偏移恒为 16（跳过 IV），不能指到 IV 里去 */
        if (off < 16) {
            return CARDS_ERR_CORRUPT;
        }
        out_offsets[i] = off;
    }
    *out_count = (int)count;
    return (int)count;
}

int cards_decrypt_range(const uint8_t key[16], const uint8_t iv[16],
                        const uint8_t *ct, size_t ct_len, uint32_t payload_offset,
                        char *out_plain, size_t out_cap)
{
    if (key == NULL || iv == NULL || ct == NULL || out_plain == NULL) {
        return CARDS_ERR_INVALID;
    }
    if (ct_len == 0) {
        return CARDS_ERR_INVALID;
    }
    if (out_cap < ct_len + 1) {
        return CARDS_ERR_INVALID;
    }

    /*
     * doc/04 §4.2 的公式：
     *   block_index    = payload_offset / 16
     *   byte_in_block  = payload_offset % 16
     *   nonce_counter  = BigEndian128(IV_base) + block_index
     */
    uint32_t block_index = payload_offset / 16u;
    uint32_t byte_in_block = payload_offset % 16u;

    uint8_t counter[NC_IV_LEN];
    if (nc_add_counter(iv, (uint64_t)block_index, counter) != NC_OK) {
        return CARDS_ERR_CORRUPT;
    }

    /*
     * 第一块可能是不完整的（起点落在块中间）。
     *
     * `nc_aes_ctr_crypt` 只能从它给的计数器的**块首**开始产生密钥流，
     * 所以这里把第一块补齐成 16 字节（缺的字节填 0），解完之后只取
     * `byte_in_block` 之后的那几字节 —— 填 0 的部分解出来是垃圾，丢掉即可。
     * 密钥流本身是对的：第 `block_index` 块的密钥流 = AES(IV_base + block_index)。
     *
     * ⚠️ 密文必须摆在 `block[byte_in_block]` —— 那是它在**块内**的真实位置。
     * 摆在 `block[0]` 会让块尾那几个字节用错密钥流（本块最后一个字节会解成垃圾）。
     */
    size_t head = (size_t)(NC_IV_LEN - byte_in_block);   /* 第一块里属于本卡的部分 */
    if (head > ct_len) {
        head = ct_len;
    }
    uint8_t block[NC_IV_LEN];
    uint8_t plain_block[NC_IV_LEN];
    memset(block, 0, sizeof(block));
    memcpy(block + byte_in_block, ct, head);
    if (nc_aes_ctr_crypt(key, counter, block, sizeof(block), plain_block) != NC_OK) {
        return CARDS_ERR_IO;
    }
    memcpy(out_plain, plain_block + byte_in_block, head);

    /* 剩下的部分是整块：计数器 +1，从块首开始解。 */
    size_t rest = ct_len - head;
    if (rest > 0) {
        uint8_t counter2[NC_IV_LEN];
        if (nc_add_counter(counter, 1, counter2) != NC_OK) {
            return CARDS_ERR_CORRUPT;
        }
        if (nc_aes_ctr_crypt(key, counter2, ct + head, rest,
                             (uint8_t *)out_plain + head) != NC_OK) {
            return CARDS_ERR_IO;
        }
    }
    out_plain[ct_len] = '\0';
    return (int)ct_len;
}

/**
 * 在 [s,e) 里找 `"key"` 对应的**字符串值**，解转义后拷进定长缓冲。
 * 返回写入长度（不含结尾 NUL）；找不到返回 -1。
 */
static int cards_json_str(const char *s, const char *e, const char *key,
                          char *dst, size_t cap)
{
    size_t klen = strlen(key);
    const char *p;
    for (p = s; p + klen + 2 < e; p++) {
        if (*p != '"') {
            continue;
        }
        if (memcmp(p + 1, key, klen) != 0) {
            continue;
        }
        if (p[1 + klen] != '"') {
            continue;
        }
        const char *q = p + 2 + klen;
        while (q < e && (*q == ' ' || *q == '\t' || *q == '\n' || *q == '\r')) {
            q++;
        }
        if (q >= e || *q != ':') {
            continue;
        }
        q++;
        while (q < e && (*q == ' ' || *q == '\t' || *q == '\n' || *q == '\r')) {
            q++;
        }
        if (q >= e || *q != '"') {
            return -1;   /* 键存在但不是字符串值 */
        }
        q++;
        size_t n = 0;
        while (q < e && *q != '"') {
            char c = *q;
            if (c == '\\' && q + 1 < e) {
                q++;
                char esc = *q;
                switch (esc) {
                case 'n': c = '\n'; break;
                case 't': c = '\t'; break;
                case 'r': c = '\r'; break;
                case 'b': c = '\b'; break;
                case 'f': c = '\f'; break;
                case '/': c = '/'; break;
                case '"': c = '"'; break;
                case '\\': c = '\\'; break;
                case 'u': {
                    /* \uXXXX —— 只处理 BMP 内的基本平面（代理对不展开） */
                    unsigned code = 0;
                    int ok = 1;
                    for (int i = 1; i <= 4; i++) {
                        if (q + i >= e) {
                            ok = 0;
                            break;
                        }
                        char h = q[i];
                        unsigned d;
                        if (h >= '0' && h <= '9') {
                            d = (unsigned)(h - '0');
                        } else if (h >= 'a' && h <= 'f') {
                            d = (unsigned)(h - 'a' + 10);
                        } else if (h >= 'A' && h <= 'F') {
                            d = (unsigned)(h - 'A' + 10);
                        } else {
                            ok = 0;
                            break;
                        }
                        code = (code << 4) | d;
                    }
                    if (!ok) {
                        return -1;
                    }
                    q += 4;
                    /* UTF-8 编码（3 字节的 BMP 码位） */
                    if (code >= 0x0800u) {
                        if (n + 3 >= cap) {
                            return -1;
                        }
                        dst[n++] = (char)(0xE0u | (code >> 12));
                        dst[n++] = (char)(0x80u | ((code >> 6) & 0x3Fu));
                        dst[n++] = (char)(0x80u | (code & 0x3Fu));
                    } else if (code >= 0x080u) {
                        if (n + 2 >= cap) {
                            return -1;
                        }
                        dst[n++] = (char)(0xC0u | (code >> 6));
                        dst[n++] = (char)(0x80u | (code & 0x3Fu));
                    } else {
                        if (n + 1 >= cap) {
                            return -1;
                        }
                        dst[n++] = (char)code;
                    }
                    q++;
                    continue;
                }
                default: c = esc; break;
                }
            }
            if (n + 1 >= cap) {
                return -1;   /* 超长就截断在这里算失败 —— 宁可整张卡不显示，
                               也不要显示一半的内容让人误读 */
            }
            dst[n++] = c;
            q++;
        }
        dst[n] = '\0';
        return (int)n;
    }
    return -1;
}

int cards_parse_item(const char *json, size_t len, card_item_t *out)
{
    if (json == NULL || out == NULL) {
        return CARDS_ERR_INVALID;
    }
    memset(out, 0, sizeof(*out));
    const char *end = json + len;

    (void)cards_json_str(json, end, "id", out->id, sizeof(out->id));
    (void)cards_json_str(json, end, "question", out->question, sizeof(out->question));
    (void)cards_json_str(json, end, "answer", out->answer, sizeof(out->answer));

    if (out->question[0] == '\0') {
        return CARDS_ERR_CORRUPT;   /* 没有题干的卡没法学 */
    }
    return 0;
}

int cards_item_length(const uint32_t *offsets, int count, int index, uint32_t file_size)
{
    if (offsets == NULL || index < 0 || index >= count) {
        return CARDS_ERR_INVALID;
    }
    uint32_t start = offsets[index];
    uint32_t end;
    if (index + 1 < count) {
        end = offsets[index + 1];
    } else {
        end = file_size;
    }
    if (end <= start) {
        return CARDS_ERR_CORRUPT;
    }
    uint32_t len = end - start;
    if (len > CARDS_ITEM_MAX) {
        return CARDS_ERR_CORRUPT;   /* doc/04 §4.2：cipher_len <= 4096 */
    }
    return (int)len;
}

/* ============================================================
 * 传输层：走存储任务队列
 * ============================================================ */

/** 投递一条存储请求并等回执。 */
static int cards_call(uint8_t op, const char *path, uint32_t offset, uint32_t len,
                      storage_io_t *io)
{
    QueueHandle_t q = hal_storage_req_queue();
    if (q == NULL) {
        return CARDS_ERR_IO;
    }
    SemaphoreHandle_t done = xSemaphoreCreateBinary();
    if (done == NULL) {
        return CARDS_ERR_IO;
    }

    storage_req_t req;
    memset(&req, 0, sizeof(req));
    req.op = op;
    req.offset = offset;
    req.len = len;
    req.io = io;
    req.reply = done;
    if (path != NULL) {
        strncpy(req.path, path, sizeof(req.path) - 1);
    }

    if (xQueueSend(q, &req, 0) != pdTRUE) {
        vSemaphoreDelete(done);
        return CARDS_ERR_IO;
    }
    if (xSemaphoreTake(done, pdMS_TO_TICKS(CARDS_TIMEOUT_MS)) != pdTRUE) {
        return CARDS_ERR_IO;
    }
    vSemaphoreDelete(done);
    return (int)io->result;
}

/** 读一段文件到调用方缓冲。 */
static int cards_read_range(const char *path, uint32_t offset, uint8_t *buf, uint32_t cap,
                            uint32_t *out_read)
{
    storage_io_t io;
    memset(&io, 0, sizeof(io));
    io.buf = buf;
    io.buf_cap = cap;
    int rc = cards_call(6 /* READ_RANGE */, path, offset, cap, &io);
    if (rc != 0) {
        return rc;
    }
    if (out_read != NULL) {
        *out_read = io.out_len;
    }
    return 0;
}

/** 取文件大小。 */
static int cards_file_size(const char *path, uint32_t *out_size)
{
    storage_io_t io;
    memset(&io, 0, sizeof(io));
    int rc = cards_call(8 /* GET_SIZE */, path, 0, 0, &io);
    if (rc != 0) {
        return rc;
    }
    if (out_size != NULL) {
        *out_size = io.out_size;
    }
    return 0;
}

/** 读一个文本文件（meta.json）。 */
static int cards_read_text(const char *path, char *buf, uint32_t cap, uint32_t *out_len)
{
    storage_io_t io;
    memset(&io, 0, sizeof(io));
    io.buf = (uint8_t *)buf;
    io.buf_cap = cap;
    int rc = cards_call(5 /* READ_TEXT_IO */, path, 0, 0, &io);
    if (rc != 0) {
        return rc;
    }
    if (out_len != NULL) {
        *out_len = io.out_len;
    }
    return 0;
}

int cards_load(const char *pack_id, const uint8_t device_key[16], card_pack_t *out)
{
    if (pack_id == NULL || device_key == NULL || out == NULL) {
        return CARDS_ERR_INVALID;
    }
    memset(out, 0, sizeof(*out));
    snprintf(out->pack_id, sizeof(out->pack_id), "%s", pack_id);

    char path[96];
    snprintf(path, sizeof(path), "/subjects/%s/meta.json", pack_id);

    /* ---- 1. meta.json -> 决定用哪种密钥 ---- */
    char meta[512];
    uint32_t meta_len = 0;
    if (cards_read_text(path, meta, (uint32_t)sizeof(meta), &meta_len) != 0) {
        return CARDS_ERR_NOTFOUND;
    }
    uint8_t key[16];
    memcpy(key, device_key, sizeof(key));
    char key_type[16];
    if (cards_json_str(meta, meta + meta_len, "key_type", key_type, sizeof(key_type)) > 0 &&
        strcmp(key_type, "demo") == 0) {
        /* 通用演示包：用固定演示密钥 */
        memcpy(key, CARDS_DEMO_KEY, sizeof(key));
    }

    /* ---- 2. index.idx -> 偏移表 ---- */
    snprintf(path, sizeof(path), "/subjects/%s/index.idx", pack_id);
    uint32_t idx_size = 0;
    if (cards_file_size(path, &idx_size) != 0) {
        return CARDS_ERR_NOTFOUND;
    }
    if (idx_size > 4096u) {
        return CARDS_ERR_CORRUPT;
    }
    uint8_t *idx = (uint8_t *)malloc(idx_size);
    if (idx == NULL) {
        return CARDS_ERR_IO;
    }
    uint32_t got = 0;
    int rc = cards_read_range(path, 0, idx, idx_size, &got);
    if (rc != 0 || got != idx_size) {
        free(idx);
        return CARDS_ERR_IO;
    }
    uint32_t offsets[CARDS_MAX_COUNT];
    int count = 0;
    rc = cards_parse_index(idx, got, offsets, CARDS_MAX_COUNT, &count);
    free(idx);
    if (rc < 0) {
        return rc;
    }

    /* ---- 3. cards.enc -> IV + 文件大小 ---- */
    snprintf(path, sizeof(path), "/subjects/%s/cards.enc", pack_id);
    uint32_t enc_size = 0;
    if (cards_file_size(path, &enc_size) != 0) {
        return CARDS_ERR_NOTFOUND;
    }
    if (enc_size < 16u) {
        return CARDS_ERR_CORRUPT;
    }
    uint8_t iv[NC_IV_LEN];
    if (cards_read_range(path, 0, iv, (uint32_t)sizeof(iv), &got) != 0 || got != NC_IV_LEN) {
        return CARDS_ERR_IO;
    }

    /* ---- 4. 逐卡：读密文 -> 局部解密 -> 解析 ---- */
    uint8_t *ct = (uint8_t *)malloc(CARDS_ITEM_MAX);
    char *plain = (char *)malloc(CARDS_ITEM_MAX + 1);
    if (ct == NULL || plain == NULL) {
        free(ct);
        free(plain);
        return CARDS_ERR_IO;
    }
    int loaded = 0;
    for (int i = 0; i < count && loaded < CARDS_MAX_COUNT; i++) {
        int len = cards_item_length(offsets, count, i, enc_size);
        if (len < 0) {
            continue;   /* 这张卡的长度不对 —— 跳过，不让整包失败 */
        }
        uint32_t start = offsets[i];
        if (start + (uint32_t)len > enc_size) {
            continue;   /* 越界防线（doc/04 §4.2 第 1 条） */
        }
        if (cards_read_range(path, start, ct, (uint32_t)len, &got) != 0 || got != (uint32_t)len) {
            continue;
        }
        /* payload_offset 是「去掉 IV 之后」的载荷区偏移 */
        uint32_t payload_offset = start - 16u;
        int plen = cards_decrypt_range(key, iv, ct, (size_t)len, payload_offset,
                                        plain, (size_t)CARDS_ITEM_MAX + 1);
        if (plen < 0) {
            continue;
        }
        /* 明文是一行 JSONL（可能带尾部换行） */
        size_t plen_sz = (size_t)plen;
        while (plen_sz > 0 && (plain[plen_sz - 1] == '\n' || plain[plen_sz - 1] == '\r')) {
            plain[--plen_sz] = '\0';
        }
        card_item_t item;
        if (cards_parse_item(plain, plen_sz, &item) != 0) {
            continue;
        }
        out->items[loaded++] = item;
    }
    free(ct);
    free(plain);

    if (loaded == 0) {
        return CARDS_ERR_CORRUPT;
    }
    out->count = loaded;
    return loaded;
}
