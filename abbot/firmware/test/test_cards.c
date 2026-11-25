/**
 * test_cards.c —— 学科卡片包（解包 / 局部解密 / 单卡解析）的主机侧单测
 *
 * 纯 C，主机侧可跑（不需要硬件、不需要 PlatformIO）。
 *
 * 最重要的一条是 **doc/05 §3.4 的官方锁定向量**：
 *   密钥 `00112233445566778899aabbccddeeff`
 *   IV_base `0102030405060708090a0b0c0d0e0f10`
 *   `block_index = 2` 处明文 `{"ping":1}` 的期望密文 `41da37cc48e5aa3f8d06`
 * 这条向量就是为「cards.enc 局部按需解密」准备的，实现必须逐字节对上。
 *
 * 覆盖：官方向量、随机偏移往返、索引解析（魔数/版本/CRC/越界）、
 * 单卡 JSON 解析（转义与 \uXXXX）、条目长度推导，
 * 以及 **`cards_load` 端到端**（假存储任务喂一个真包进去，看能不能解出卡片）。
 *
 * CRC32 用 zlib 的实现做**独立对照** —— 自己写的 CRC 与 zlib 对不上就说明有问题。
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <zlib.h>

#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"

#include "cards.h"
#include "net_client.h"
#include "storage_io.h"

/* ============================================================
 * 假 TF 卡（照抄 test_ui_data.c 的做法）
 * ============================================================ */

#define FAKE_MAX_FILES 6
#define FAKE_FILE_MAX 8192

typedef struct {
    char name[STORAGE_PATH_MAX];
    bool used;
    uint32_t len;
    uint8_t data[FAKE_FILE_MAX];
} fake_file_t;

static fake_file_t g_files[FAKE_MAX_FILES];

static fake_file_t *fake_find(const char *path)
{
    for (int i = 0; i < FAKE_MAX_FILES; i++) {
        if (g_files[i].used && strcmp(g_files[i].name, path) == 0) {
            return &g_files[i];
        }
    }
    return NULL;
}

static fake_file_t *fake_put(const char *path, const uint8_t *data, uint32_t len)
{
    fake_file_t *f = fake_find(path);
    if (f == NULL) {
        for (int i = 0; i < FAKE_MAX_FILES; i++) {
            if (!g_files[i].used) {
                f = &g_files[i];
                f->used = true;
                snprintf(f->name, sizeof(f->name), "%s", path);
                break;
            }
        }
    }
    if (f == NULL) {
        return NULL;
    }
    if (len > FAKE_FILE_MAX) {
        len = FAKE_FILE_MAX;
    }
    memcpy(f->data, data, len);
    f->len = len;
    return f;
}

static void fake_service(storage_req_t *req)
{
    switch (req->op) {
    case 5: /* 读文本 */
        {
            fake_file_t *f = fake_find(req->path);
            if (f == NULL) {
                req->io->result = -4;
                break;
            }
            uint32_t n = f->len;
            if (n > req->io->buf_cap) {
                n = req->io->buf_cap;
            }
            memcpy(req->io->buf, f->data, n);
            if (n < req->io->buf_cap) {
                req->io->buf[n] = '\0';
            }
            req->io->out_len = n;
            req->io->result = 0;
        }
        break;
    case 6: /* 读区间 */
        {
            fake_file_t *f = fake_find(req->path);
            if (f == NULL) {
                req->io->result = -4;
                break;
            }
            if (req->offset > f->len) {
                req->io->result = -3;
                req->io->out_len = 0;
                break;
            }
            uint32_t avail = f->len - req->offset;
            uint32_t n = (req->io->buf_cap < avail) ? req->io->buf_cap : avail;
            memcpy(req->io->buf, f->data + req->offset, n);
            req->io->out_len = n;
            req->io->result = 0;
        }
        break;
    case 8: /* 取文件大小 */
        {
            fake_file_t *f = fake_find(req->path);
            if (f == NULL) {
                req->io->result = -4;
                break;
            }
            req->io->out_size = f->len;
            req->io->result = 0;
        }
        break;
    default:
        req->io->result = -6;
        break;
    }
    if (req->reply != NULL) {
        xSemaphoreGive(req->reply);
    }
}

typedef struct {
    int given;
} fake_sem_t;

static QueueHandle_t g_fake_queue = (QueueHandle_t)0x1;

QueueHandle_t hal_storage_req_queue(void)
{
    return g_fake_queue;
}

BaseType_t xQueueSend(QueueHandle_t q, const void *item, TickType_t wait)
{
    (void)q;
    (void)wait;
    if (item == NULL) {
        return pdFALSE;
    }
    fake_service((storage_req_t *)item);
    return pdTRUE;
}

BaseType_t xQueueReceive(QueueHandle_t q, void *item, TickType_t wait)
{
    (void)q;
    (void)item;
    (void)wait;
    return pdFALSE;
}

SemaphoreHandle_t xSemaphoreCreateBinary(void)
{
    return (SemaphoreHandle_t)calloc(1, sizeof(fake_sem_t));
}

BaseType_t xSemaphoreGive(SemaphoreHandle_t s)
{
    fake_sem_t *fs = (fake_sem_t *)s;
    if (fs != NULL) {
        fs->given = 1;
    }
    return pdTRUE;
}

BaseType_t xSemaphoreTake(SemaphoreHandle_t s, TickType_t wait)
{
    (void)wait;
    fake_sem_t *fs = (fake_sem_t *)s;
    if (fs != NULL && fs->given) {
        fs->given = 0;
        return pdTRUE;
    }
    return pdFALSE;
}

void vSemaphoreDelete(SemaphoreHandle_t s)
{
    free(s);
}

/* ============================================================
 * 断言
 * ============================================================ */

static int g_checks = 0;
static int g_fail = 0;

#define CHECK(cond, ...)                                                      \
    do {                                                                      \
        g_checks++;                                                           \
        if (!(cond)) {                                                        \
            g_fail++;                                                         \
            printf("FAIL %s:%d ", __FILE__, __LINE__);                       \
            printf(__VA_ARGS__);                                              \
            printf("\n");                                                     \
        }                                                                     \
    } while (0)

/* ---------- 十六进制辅助 ---------- */
static int hex2bin(const char *hex, uint8_t *out, size_t out_len)
{
    size_t n = strlen(hex);
    if (n != out_len * 2) {
        return -1;
    }
    for (size_t i = 0; i < out_len; i++) {
        unsigned v = 0;
        for (int k = 0; k < 2; k++) {
            char c = hex[i * 2 + k];
            unsigned d;
            if (c >= '0' && c <= '9') {
                d = (unsigned)(c - '0');
            } else if (c >= 'a' && c <= 'f') {
                d = (unsigned)(c - 'a' + 10);
            } else if (c >= 'A' && c <= 'F') {
                d = (unsigned)(c - 'A' + 10);
            } else {
                return -1;
            }
            v = (v << 4) | d;
        }
        out[i] = (uint8_t)v;
    }
    return 0;
}

/* ============================================================
 * 1. doc/05 §3.4 官方锁定向量
 * ============================================================ */
static void test_official_vector(void)
{
    printf("[1] doc/05 §3.4 局部块偏移锁定向量\n");
    uint8_t key[16];
    uint8_t iv[16];
    hex2bin("00112233445566778899aabbccddeeff", key, sizeof(key));
    hex2bin("0102030405060708090a0b0c0d0e0f10", iv, sizeof(iv));

    /* block_index = 2 -> payload_offset = 32，byte_in_block = 0 */
    uint8_t ct[10];
    uint8_t expect[16];
    hex2bin("41da37cc48e5aa3f8d06", ct, sizeof(ct));

    char plain[64];
    int n = cards_decrypt_range(key, iv, ct, sizeof(ct), 32u, plain, sizeof(plain));
    CHECK(n == 10, "解出 10 字节，实际 %d", n);
    CHECK(n > 0 && memcmp(plain, "{\"ping\":1}", 10) == 0, "明文逐字节吻合：%.*s",
          (n > 0) ? n : 0, plain);

    /* 计数器公式：BigEndian128(IV_base) + 2 */
    uint8_t counter[16];
    CHECK(nc_add_counter(iv, 2, counter) == NC_OK, "计数器推进成功");
    hex2bin("0102030405060708090a0b0c0d0e0f12", expect, sizeof(expect));
    CHECK(memcmp(counter, expect, 16) == 0, "nonce_counter 与文档一致");
}

/* ============================================================
 * 2. 随机偏移的往返解密
 * ============================================================ */
static void test_roundtrip_offsets(void)
{
    printf("[2] 各种偏移的往返解密\n");
    uint8_t key[16];
    uint8_t iv[16];
    hex2bin("00112233445566778899aabbccddeeff", key, sizeof(key));
    hex2bin("0102030405060708090a0b0c0d0e0f10", iv, sizeof(iv));

    const char *text = "{\"id\":\"c0001\",\"question\":\"2+2=?\",\"answer\":\"4\"}";
    size_t tlen = strlen(text);

    uint32_t offsets[] = {0u, 5u, 16u, 31u, 32u, 33u, 47u, 64u};
    for (size_t k = 0; k < sizeof(offsets) / sizeof(offsets[0]); k++) {
        uint32_t p = offsets[k];
        size_t region = (size_t)p + tlen;
        /* 拿**原始密钥流**：nc_aes_ctr_crypt 返回的是「输入 XOR 密钥流」，
           所以输入必须是全 0 —— 否则拿到的不是密钥流本身。 */
        uint8_t *zeros = (uint8_t *)calloc(region, 1);
        uint8_t *ks = (uint8_t *)calloc(region, 1);
        uint8_t *ct = (uint8_t *)malloc(tlen);
        CHECK(nc_aes_ctr_crypt(key, iv, zeros, region, ks) == NC_OK, "造密钥流");
        for (size_t i = 0; i < tlen; i++) {
            ct[i] = (uint8_t)(text[i] ^ ks[p + i]);
        }
        char plain[256];
        int n = cards_decrypt_range(key, iv, ct, tlen, p, plain, sizeof(plain));
        CHECK(n == (int)tlen, "偏移 %u：解出长度正确（实际 %d）", (unsigned)p, n);
        CHECK(n > 0 && memcmp(plain, text, tlen) == 0, "偏移 %u：明文吻合", (unsigned)p);
        free(zeros);
        free(ks);
        free(ct);
    }
}

/* ============================================================
 * 3. index.idx 解析
 * ============================================================ */
static void build_index(uint8_t *buf, uint16_t count, const uint32_t *offsets, int version,
                        const char *magic)
{
    memcpy(buf, magic, 4);
    buf[4] = (uint8_t)(version >> 8);
    buf[5] = (uint8_t)(version & 0xFF);
    buf[6] = (uint8_t)(count >> 8);
    buf[7] = (uint8_t)(count & 0xFF);
    for (int i = 0; i < (int)count; i++) {
        uint8_t *p = buf + 12 + i * 4;
        p[0] = (uint8_t)(offsets[i] >> 24);
        p[1] = (uint8_t)(offsets[i] >> 16);
        p[2] = (uint8_t)(offsets[i] >> 8);
        p[3] = (uint8_t)(offsets[i] & 0xFF);
    }
    uint32_t crc = (uint32_t)crc32(0, buf + 12, (uInt)(count * 4));
    buf[8] = (uint8_t)(crc >> 24);
    buf[9] = (uint8_t)(crc >> 16);
    buf[10] = (uint8_t)(crc >> 8);
    buf[11] = (uint8_t)(crc & 0xFF);
}

static void test_parse_index(void)
{
    printf("[3] index.idx 解析\n");
    uint32_t offsets[3] = {16u, 64u, 128u};
    uint8_t buf[12 + 12];
    uint32_t out[8];
    int count = 0;

    build_index(buf, 3, offsets, 1, "IDX1");
    int rc = cards_parse_index(buf, sizeof(buf), out, 8, &count);
    CHECK(rc == 3, "解析出 3 张，实际 %d", rc);
    CHECK(count == 3, "count = 3，实际 %d", count);
    CHECK(out[0] == 16u && out[1] == 64u && out[2] == 128u, "偏移表逐个吻合");

    build_index(buf, 3, offsets, 1, "IDX2");
    CHECK(cards_parse_index(buf, sizeof(buf), out, 8, &count) == CARDS_ERR_CORRUPT,
          "魔数不对时报损坏");

    build_index(buf, 3, offsets, 2, "IDX1");
    CHECK(cards_parse_index(buf, sizeof(buf), out, 8, &count) == CARDS_ERR_CORRUPT,
          "版本不对时报损坏");

    /* CRC 不对（改一个偏移字节但不重算 CRC） */
    build_index(buf, 3, offsets, 1, "IDX1");
    buf[12 + 4] ^= 0xFF;
    CHECK(cards_parse_index(buf, sizeof(buf), out, 8, &count) == CARDS_ERR_CORRUPT,
          "CRC 不对时报损坏");

    /* 偏移指到 IV 里（< 16） */
    uint32_t bad[3] = {8u, 64u, 128u};
    build_index(buf, 3, bad, 1, "IDX1");
    CHECK(cards_parse_index(buf, sizeof(buf), out, 8, &count) == CARDS_ERR_CORRUPT,
          "偏移 < 16 时报损坏");

    build_index(buf, 3, offsets, 1, "IDX1");
    CHECK(cards_parse_index(buf, 8, out, 8, &count) == CARDS_ERR_CORRUPT, "头部截断时报损坏");
    CHECK(cards_parse_index(buf, 16, out, 8, &count) == CARDS_ERR_CORRUPT,
          "偏移区截断时报损坏");
}

/* ============================================================
 * 4. 单卡 JSON 解析
 * ============================================================ */
static void test_parse_item(void)
{
    printf("[4] 单卡 JSON 解析\n");
    card_item_t item;

    const char *json =
        "{\"id\":\"c0007\",\"question\":\"1+1=?\",\"answer\":\"2\",\"hint1\":\"加法\",\"hint2\":\"\",\"analysis\":\"略\"}";
    CHECK(cards_parse_item(json, strlen(json), &item) == 0, "普通卡片解析成功");
    CHECK(strcmp(item.id, "c0007") == 0, "id 正确");
    CHECK(strcmp(item.question, "1+1=?") == 0, "question 正确");
    CHECK(strcmp(item.answer, "2") == 0, "answer 正确");

    const char *esc =
        "{\"id\":\"c1\",\"question\":\"第一行\\n第二行\",\"answer\":\"说\\\"引号\\\"\",\"hint1\":\"\",\"hint2\":\"\",\"analysis\":\"\"}";
    CHECK(cards_parse_item(esc, strlen(esc), &item) == 0, "带转义的卡片解析成功");
    CHECK(strcmp(item.question, "第一行\n第二行") == 0, "\\n 被解开");
    CHECK(strcmp(item.answer, "说\"引号\"") == 0, "\\\" 被解开");

    const char *uni =
        "{\"id\":\"c2\",\"question\":\"\\u4e2d\\u6587\",\"answer\":\"x\",\"hint1\":\"\",\"hint2\":\"\",\"analysis\":\"\"}";
    CHECK(cards_parse_item(uni, strlen(uni), &item) == 0, "\\uXXXX 卡片解析成功");
    CHECK(strcmp(item.question, "中文") == 0, "\\uXXXX 解成 UTF-8");

    const char *noq = "{\"id\":\"c3\",\"question\":\"\",\"answer\":\"x\"}";
    CHECK(cards_parse_item(noq, strlen(noq), &item) == CARDS_ERR_CORRUPT, "无题干判为损坏");
}

/* ============================================================
 * 5. 条目长度推导
 * ============================================================ */
static void test_item_length(void)
{
    printf("[5] 条目长度推导\n");
    uint32_t offsets[3] = {16u, 40u, 100u};
    CHECK(cards_item_length(offsets, 3, 0, 200u) == 24, "第 1 张长度 = 40-16");
    CHECK(cards_item_length(offsets, 3, 1, 200u) == 60, "第 2 张长度 = 100-40");
    CHECK(cards_item_length(offsets, 3, 2, 200u) == 100, "最后一张用文件大小收尾");
    CHECK(cards_item_length(offsets, 3, 5, 200u) == CARDS_ERR_INVALID, "越界下标被拒");
}

/* ============================================================
 * 6. cards_load 端到端（假存储任务喂一个真包）
 * ============================================================ */
static void test_cards_load_e2e(void)
{
    printf("[6] cards_load 端到端\n");
    memset(g_files, 0, sizeof(g_files));

    uint8_t key[16];
    uint8_t iv[16];
    hex2bin("00112233445566778899aabbccddeeff", key, sizeof(key));
    hex2bin("0102030405060708090a0b0c0d0e0f10", iv, sizeof(iv));

    /* 两张卡的明文 JSONL（服务端格式：每行一张、紧凑分隔符、行尾换行） */
    const char *line1 = "{\"id\":\"c0000\",\"question\":\"水的化学式？\",\"answer\":\"H2O\",\"hint1\":\"\",\"hint2\":\"\",\"analysis\":\"\"}\n";
    const char *line2 = "{\"id\":\"c0001\",\"question\":\"光合作用的场所？\",\"answer\":\"叶绿体\",\"hint1\":\"\",\"hint2\":\"\",\"analysis\":\"\"}\n";
    size_t l1 = strlen(line1);
    size_t l2 = strlen(line2);

    /* 组密文文件：IV(16) + CTR(明文载荷) */
    uint8_t plain_region[16 + l1 + l2];
    memcpy(plain_region + 16, line1, l1);
    memcpy(plain_region + 16 + l1, line2, l2);
    uint8_t enc[sizeof(plain_region)];
    memcpy(enc, iv, 16);
    CHECK(nc_aes_ctr_crypt(key, iv, plain_region + 16, l1 + l2, enc + 16) == NC_OK,
          "造密文");

    /* 索引：第 0 张偏移恒为 16 */
    uint32_t offs[2] = {16u, (uint32_t)(16 + l1)};
    uint8_t idx[12 + 8];
    build_index(idx, 2, offs, 1, "IDX1");

    /* meta.json：key_type=device */
    const char *meta = "{\"schema_version\":1,\"key_type\":\"device\",\"card_count\":2}";
    fake_put("/subjects/demo_pack/meta.json", (const uint8_t *)meta, (uint32_t)strlen(meta));
    fake_put("/subjects/demo_pack/index.idx", idx, (uint32_t)sizeof(idx));
    fake_put("/subjects/demo_pack/cards.enc", enc, (uint32_t)sizeof(enc));

    card_pack_t pack;
    int n = cards_load("demo_pack", key, &pack);
    CHECK(n == 2, "解出 2 张卡，实际 %d", n);
    CHECK(pack.count == 2, "count = 2，实际 %d", pack.count);
    if (n == 2) {
        CHECK(strcmp(pack.items[0].question, "水的化学式？") == 0, "第 1 张题干正确：%s",
              pack.items[0].question);
        CHECK(strcmp(pack.items[0].answer, "H2O") == 0, "第 1 张答案正确");
        CHECK(strcmp(pack.items[1].question, "光合作用的场所？") == 0, "第 2 张题干正确：%s",
              pack.items[1].question);
        CHECK(strcmp(pack.items[1].answer, "叶绿体") == 0, "第 2 张答案正确");
        CHECK(strcmp(pack.items[1].id, "c0001") == 0, "第 2 张卡号正确");
    }

    /* 包不存在 */
    card_pack_t missing;
    CHECK(cards_load("no_such_pack", key, &missing) == CARDS_ERR_NOTFOUND, "包不存在时报未找到");
}

/* ============================================================
 * 7. 交叉核对：用**服务端真实造出来的包**喂固件自己的解密代码
 * ============================================================ */
/**
 * 参数：目录里放服务端 `pack_builder.build_pack()` 产出的
 * `meta.json` / `index.idx` / `cards.enc`。
 *
 * 这条测的是「Python 服务端 ↔ C 固件」这一对 —— 两边对包格式的理解
 * 必须逐字节一致，否则设备解出来的就是乱码。
 */
static int test_xcheck_pack(const char *dir)
{
    printf("[7] 交叉核对服务端造出来的包（%s）\n", dir);
    memset(g_files, 0, sizeof(g_files));

    const char *names[3] = {"/meta.json", "/index.idx", "/cards.enc"};
    for (int i = 0; i < 3; i++) {
        char path[256];
        snprintf(path, sizeof(path), "%s%s", dir, names[i]);
        FILE *fp = fopen(path, "rb");
        if (fp == NULL) {
            printf("  （跳过：打不开 %s）\n", path);
            return 0;
        }
        static uint8_t buf[8192];
        size_t n = fread(buf, 1, sizeof(buf), fp);
        fclose(fp);
        char dst[256];
        snprintf(dst, sizeof(dst), "/subjects/xcheck%s", names[i]);
        fake_put(dst, buf, (uint32_t)n);
    }

    /* demo 包的密钥是固定的 16 字节演示密钥；传什么都会被 meta.json 覆盖。 */
    uint8_t key[16] = {0};
    card_pack_t pack;
    int n = cards_load("xcheck", key, &pack);
    CHECK(n == 3, "解出 3 张卡，实际 %d", n);
    if (n == 3) {
        CHECK(strcmp(pack.items[0].question, "水的化学式？") == 0, "第 1 张题干一致：%s",
              pack.items[0].question);
        CHECK(strcmp(pack.items[0].answer, "H2O") == 0, "第 1 张答案一致：%s",
              pack.items[0].answer);
        CHECK(strcmp(pack.items[1].answer, "叶绿体") == 0, "第 2 张答案一致：%s",
              pack.items[1].answer);
        CHECK(strcmp(pack.items[2].question, "地球有几个天然卫星？") == 0, "第 3 张题干一致：%s",
              pack.items[2].question);
        CHECK(strcmp(pack.items[2].answer, "1 个") == 0, "第 3 张答案一致：%s",
              pack.items[2].answer);
    }
    return 1;
}

int main(int argc, char **argv)
{
    printf("=== abbot 学科卡片包测试（cards）===\n");
    test_official_vector();
    test_roundtrip_offsets();
    test_parse_index();
    test_parse_item();
    test_item_length();
    test_cards_load_e2e();
    if (argc > 1) {
        test_xcheck_pack(argv[1]);
    }
    printf("\n=== %d checks, %d failures ===\n", g_checks, g_fail);
    return g_fail == 0 ? 0 : 1;
}
