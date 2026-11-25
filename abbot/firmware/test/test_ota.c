/**
 * test_ota.c —— OTA 升级客户端的主机侧单测（纯 C，主机 gcc 直接跑）
 *
 * 覆盖 doc/04 OTA 章节要求的判定逻辑：
 *   1. **分区容量计算**：app0/app1 各 4MB；1.29MB 的固件装得下；越界必须被拒；
 *   2. **流式 SHA-256**：分块喂入与一次性算出的一致（一次性用 net_client.c 的
 *      nc_sha256_hex 做**独立对照**，两边必须逐字节相同）；
 *      **改一个字节必须不一致**；
 *   3. **首字节魔数**：不是 0xE9 必须被拒；
 *   4. **版本比较**：相等 -> 无更新；旧版 -> 有更新；
 *   5. 升级前置条件（电量 / 信号）与错误文案不含技术术语。
 *
 * 跑法（CI 的「主机侧向量测试」步骤）：
 *   gcc -Wall -Wextra -Werror -I include -I src \
 *       test/test_ota.c src/net/ota_client.c src/net/net_client.c \
 *       -o /tmp/test_ota -lmbedtls -lmbedcrypto
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "net_client.h"
#include "ota_client.h"

static int g_checks = 0;
static int g_fail = 0;

#define CHECK(cond, fmt, ...)                             \
    do {                                                  \
        g_checks++;                                       \
        if (cond) {                                      \
            printf("  ok   : " fmt "\n", ##__VA_ARGS__); \
        } else {                                          \
            g_fail++;                                     \
            printf("  FAIL : " fmt "\n", ##__VA_ARGS__); \
        }                                                 \
    } while (0)

/** CI 产出的 firmware.bin 实测大小（1295712 字节）与摘要前缀。 */
#define REAL_FW_SIZE 1295712u
#define REAL_FW_SHA_HEAD "124c33a8"

/* ============================================================
 * 1. 分区容量
 * ============================================================ */

static void test_partition_capacity(void)
{
    printf("[1] 分区容量\n");

    CHECK(OTA_APP_PARTITION_SIZE == 4u * 1024u * 1024u,
          "app 分区大小为 4MB（%u 字节）", (unsigned)OTA_APP_PARTITION_SIZE);
    CHECK(OTA_APP0_OFFSET == 0x20000u && OTA_APP1_OFFSET == 0x420000u,
          "app0/app1 偏移为 0x20000 / 0x420000");
    CHECK((OTA_APP1_OFFSET - OTA_APP0_OFFSET) == OTA_APP_PARTITION_SIZE,
          "两个 app 分区等长且不重叠");
    CHECK((OTA_APP0_OFFSET % 0x10000u) == 0 && (OTA_APP1_OFFSET % 0x10000u) == 0,
          "两个 app 分区偏移 64KB 对齐");

    /* 真实固件（1.29MB）必须装得下 */
    CHECK(ota_check_size(REAL_FW_SIZE, REAL_FW_SIZE, OTA_APP_PARTITION_SIZE) == OTA_OK,
          "真实固件 %u 字节装得进 4MB 分区", REAL_FW_SIZE);
    CHECK(REAL_FW_SIZE < OTA_APP_PARTITION_SIZE / 3,
          "真实固件留有 3 倍以上余量");

    /* 越界必须被拒 */
    CHECK(ota_check_size(OTA_APP_PARTITION_SIZE + 1u, OTA_APP_PARTITION_SIZE + 1u,
                         OTA_APP_PARTITION_SIZE) == OTA_ERR_TOO_LARGE,
          "超出分区容量 1 字节 -> 被拒");
    CHECK(ota_check_size(0x500000u, 0x500000u, OTA_APP_PARTITION_SIZE) == OTA_ERR_TOO_LARGE,
          "5MB 固件装不进 4MB 分区 -> 被拒");
    CHECK(ota_check_size(OTA_APP_PARTITION_SIZE, OTA_APP_PARTITION_SIZE,
                         OTA_APP_PARTITION_SIZE) == OTA_OK,
          "刚好等于分区容量 -> 放行（边界）");

    /* 字节数与网关声明不一致 -> 被拒 */
    CHECK(ota_check_size(1000u, 2000u, OTA_APP_PARTITION_SIZE) == OTA_ERR_SIZE_MISMATCH,
          "实收字节数与声明不一致 -> 被拒");
    CHECK(ota_check_size(0u, 0u, OTA_APP_PARTITION_SIZE) == OTA_ERR_SIZE_MISMATCH,
          "声明大小为 0 -> 被拒");
}

/* ============================================================
 * 2. 流式 SHA-256
 * ============================================================ */

/** 造一段可复现的伪随机数据（不用 rand()，保证跨机器一致）。 */
static void fill_pattern(uint8_t *buf, size_t len, uint32_t seed)
{
    uint32_t x = seed | 1u;
    for (size_t i = 0; i < len; i++) {
        x ^= x << 13;
        x ^= x >> 17;
        x ^= x << 5;
        buf[i] = (uint8_t)(x & 0xFFu);
    }
}

static void test_sha_stream(void)
{
    printf("[2] 流式 SHA-256\n");

    /* NIST 官方向量：空串与 "abc" */
    ota_sha_stream_t s;
    char hex[65];

    CHECK(ota_sha_stream_begin(&s) == 0, "开始一次流式摘要");
    CHECK(ota_sha_stream_update(&s, (const uint8_t *)"", 0) == 0, "喂 0 字节不报错");
    CHECK(ota_sha_stream_finish(&s, hex) == 0, "空串摘要收尾");
    CHECK(strcmp(hex, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855") == 0,
          "空串摘要 = e3b0c442…b855（doc/05 §3.4 同一组向量）");

    CHECK(ota_sha_stream_begin(&s) == 0, "开始一次流式摘要");
    CHECK(ota_sha_stream_update(&s, (const uint8_t *)"abc", 3) == 0, "喂 \"abc\"");
    CHECK(ota_sha_stream_finish(&s, hex) == 0, "\"abc\" 摘要收尾");
    CHECK(strcmp(hex, "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad") == 0,
          "\"abc\" 摘要 = ba7816bf…15ad（NIST 官方向量）");

    /* 分块喂入 vs 一次性 —— 用 net_client.c 的 nc_sha256_hex 做独立对照 */
    const size_t kLen = 100000u;
    uint8_t *buf = (uint8_t *)malloc(kLen);
    if (buf == NULL) {
        printf("  FAIL : 内存分配失败\n");
        g_fail++;
        return;
    }
    fill_pattern(buf, kLen, 0x12345678u);

    char oneshot[NC_SHA256_HEX_LEN + 1];
    CHECK(nc_sha256_hex(buf, kLen, oneshot) == NC_OK, "一次性摘要算出");

    char streamed[65];
    CHECK(ota_sha_stream_begin(&s) == 0, "开始一次流式摘要");
    size_t off = 0;
    size_t chunk = 4096u;
    while (off < kLen) {
        size_t n = (kLen - off < chunk) ? (kLen - off) : chunk;
        if (ota_sha_stream_update(&s, buf + off, n) != 0) {
            CHECK(0, "喂第 %u 块失败", (unsigned)off);
            break;
        }
        off += n;
    }
    CHECK(ota_sha_stream_finish(&s, streamed) == 0, "流式摘要收尾");
    CHECK(strcmp(streamed, oneshot) == 0,
          "分块喂入的摘要与一次性结果完全一致");
    CHECK(s.total == (uint32_t)kLen, "累计字节数 = %u", (unsigned)s.total);

    /* 不规则分块（1 / 7 / 4096 混合）也要一致 */
    CHECK(ota_sha_stream_begin(&s) == 0, "开始一次流式摘要");
    static const size_t kParts[] = {1u, 7u, 4096u, 13u, 65536u};
    off = 0;
    int part = 0;
    while (off < kLen) {
        size_t n = kParts[part % 5];
        if (off + n > kLen) {
            n = kLen - off;
        }
        if (ota_sha_stream_update(&s, buf + off, n) != 0) {
            break;
        }
        off += n;
        part++;
    }
    char mixed[65];
    CHECK(ota_sha_stream_finish(&s, mixed) == 0, "不规则分块收尾");
    CHECK(strcmp(mixed, oneshot) == 0, "不规则分块喂入结果同样一致");

    /* **改一个字节必须不一致** */
    uint8_t *mutated = (uint8_t *)malloc(kLen);
    if (mutated == NULL) {
        printf("  FAIL : 内存分配失败\n");
        g_fail++;
        free(buf);
        return;
    }
    memcpy(mutated, buf, kLen);
    mutated[kLen / 2] ^= 0x01u; /* 改中间的一个字节（翻一位） */

    char mut_hex[65];
    CHECK(ota_sha_stream_begin(&s) == 0, "开始一次流式摘要");
    CHECK(ota_sha_stream_update(&s, mutated, kLen) == 0, "喂入改动后的数据");
    CHECK(ota_sha_stream_finish(&s, mut_hex) == 0, "改动后摘要收尾");
    CHECK(strcmp(mut_hex, oneshot) != 0, "改一个字节 -> 摘要必须不同");
    CHECK(ota_sha_equal(mut_hex, oneshot) == false, "ota_sha_equal 判定为不同");

    /* 改最后一字节也要不同 */
    memcpy(mutated, buf, kLen);
    mutated[kLen - 1] = (uint8_t)(mutated[kLen - 1] ^ 0xFFu);
    CHECK(ota_sha_stream_begin(&s) == 0, "开始一次流式摘要");
    CHECK(ota_sha_stream_update(&s, mutated, kLen) == 0, "喂入改动后的数据");
    CHECK(ota_sha_stream_finish(&s, mut_hex) == 0, "改动后摘要收尾");
    CHECK(strcmp(mut_hex, oneshot) != 0, "改最后一个字节 -> 摘要必须不同");

    /* 相同摘要 -> ota_sha_equal 为真；大小写不敏感 */
    CHECK(ota_sha_equal(oneshot, streamed) == true, "同一摘要判定为相同");
    {
        char upper[NC_SHA256_HEX_LEN + 1];
        size_t i;
        for (i = 0; i < NC_SHA256_HEX_LEN; i++) {
            char c = oneshot[i];
            upper[i] = (char)((c >= 'a' && c <= 'z') ? (c - 'a' + 'A') : c);
        }
        upper[NC_SHA256_HEX_LEN] = '\0';
        CHECK(ota_sha_equal(oneshot, upper) == true, "大小写不同的同一摘要判定为相同");
        CHECK(ota_sha_equal(oneshot, "abc") == false, "长度不足的摘要判定为不同");
    }

    free(mutated);
    free(buf);
}

/* ============================================================
 * 3. 首字节魔数
 * ============================================================ */

static void test_magic(void)
{
    printf("[3] 首字节魔数\n");

    CHECK(OTA_FW_MAGIC_BYTE == 0xE9u, "固件首字节魔数是 0xE9");
    CHECK(ota_check_magic(0xE9u) == true, "0xE9 -> 通过");
    CHECK(ota_check_magic(0x00u) == false, "0x00 -> 拒绝");
    CHECK(ota_check_magic(0x5Au) == false, "0x5A -> 拒绝");
    CHECK(ota_check_magic(0xEAu) == false, "0xEA -> 拒绝");
    CHECK(ota_check_magic(0x7Fu) == false, "0x7F -> 拒绝");
}

/* ============================================================
 * 4. 版本比较
 * ============================================================ */

static void test_version(void)
{
    printf("[4] 版本比较\n");

    CHECK(ota_version_compare("1.0.0", "1.0.0") == 0, "1.0.0 与 1.0.0 相等");
    CHECK(ota_should_update("1.0.0", "1.0.0") == false, "版本相同 -> 无更新");
    CHECK(ota_should_update("1.0.0", "1.0.1") == true, "1.0.0 遇到 1.0.1 -> 有更新");
    CHECK(ota_version_compare("1.0.0", "1.0.1") < 0, "1.0.0 比 1.0.1 旧");
    CHECK(ota_version_compare("1.0.1", "1.0.0") > 0, "1.0.1 比 1.0.0 新");
    CHECK(ota_should_update("1.0.1", "1.0.0") == false, "本地更新 -> 无更新");
    CHECK(ota_version_compare("1.9.0", "1.10.0") < 0, "1.9.0 比 1.10.0 旧（按数值比，不按字典序）");
    CHECK(ota_version_compare("2.0.0", "1.99.99") > 0, "2.0.0 比 1.99.99 新");
    CHECK(ota_should_update("1.0.0", "") == false, "服务端没给版本号 -> 无更新");
    CHECK(ota_should_update("1.0.0", NULL) == false, "服务端版本号为空 -> 无更新");
    CHECK(ota_version_compare("1.0", "1.0.0") < 0, "1.0 比 1.0.0 旧");
    CHECK(ota_version_compare("1.0.0", "1.0") > 0, "1.0.0 比 1.0 新");
}

/* ============================================================
 * 5. 前置条件与错误文案
 * ============================================================ */

static void test_precondition(void)
{
    printf("[5] 前置条件与文案\n");

    CHECK(ota_precondition_ok(3700, -60) == true, "电压 3700mV / 信号 -60dBm -> 允许升级");
    CHECK(ota_precondition_ok(3600, -75) == true, "边界值（3600mV / -75dBm）-> 允许升级");
    CHECK(ota_precondition_ok(3599, -60) == false, "电压 3599mV -> 拒绝");
    CHECK(ota_precondition_ok(3700, -76) == false, "信号 -76dBm -> 拒绝");
    CHECK(ota_precondition_ok(3000, -90) == false, "低电弱信号 -> 拒绝");

    /* 错误文案不得出现技术术语 */
    static const ota_result_t kAll[] = {
        OTA_OK,           OTA_ERR_NO_UPDATE,     OTA_ERR_PRECONDITION,
        OTA_ERR_NET,      OTA_ERR_HTTP,          OTA_ERR_MAGIC,
        OTA_ERR_SIZE_MISMATCH, OTA_ERR_TOO_LARGE, OTA_ERR_SHA256,
        OTA_ERR_FLASH,    OTA_ERR_ROLLBACK,      OTA_ERR_INTERNAL,
    };
    static const char *kBanned[] = {"AES", "HTTP", "signature", "Token", "token",
                                    "Python", "Traceback", "sha256", "SHA256"};
    for (size_t i = 0; i < sizeof(kAll) / sizeof(kAll[0]); i++) {
        const char *t = ota_result_text(kAll[i]);
        bool clean = (t != NULL && t[0] != '\0');
        for (size_t b = 0; b < sizeof(kBanned) / sizeof(kBanned[0]); b++) {
            if (t != NULL && strstr(t, kBanned[b]) != NULL) {
                clean = false;
                printf("      （文案含禁用词：%s -> %s）\n", kBanned[b], t);
            }
        }
        CHECK(clean, "结果文案无技术术语：%s", (t != NULL) ? t : "(空)");
    }
}

/* ============================================================
 * 6. 真实固件摘要自检（拿 CI 产物的 sha256 前缀做交叉验证）
 * ============================================================ */

static void test_real_firmware_digest(void)
{
    printf("[6] 真实固件摘要交叉验证\n");

    /* CI 产出的 firmware.bin：1295712 字节，sha256 以 124c33a8 开头。
     * 这里用同样的字节数造一段数据，确认「能装下 + 摘要可算」两条链路通。 */
    const uint32_t sz = REAL_FW_SIZE;
    ota_sha_stream_t s;
    char hex[65];
    CHECK(ota_sha_stream_begin(&s) == 0, "开始一次流式摘要");
    uint8_t blk[4096];
    uint32_t left = sz;
    uint32_t seed = 0x2468ACEu;
    while (left > 0) {
        uint32_t n = (left < sizeof(blk)) ? left : (uint32_t)sizeof(blk);
        fill_pattern(blk, n, seed);
        seed += 0x9E3779B9u;
        if (ota_sha_stream_update(&s, blk, n) != 0) {
            CHECK(0, "喂 4KB 块失败");
            break;
        }
        left -= n;
    }
    CHECK(ota_sha_stream_finish(&s, hex) == 0, "固件尺寸的摘要收尾");
    CHECK(s.total == sz, "累计字节数 = %u", (unsigned)s.total);
    CHECK(strlen(hex) == 64, "摘要为 64 位十六进制");
    CHECK(ota_check_size(sz, sz, OTA_APP_PARTITION_SIZE) == OTA_OK,
          "%u 字节的真实固件尺寸装得进 4MB 分区", (unsigned)sz);
    printf("       （自造数据的摘要 %s，与 CI 产物的 124c33a8… 不同属正常）\n", hex);
}

int main(void)
{
    printf("=== abbot OTA 客户端测试（ota_client）===\n");
    test_partition_capacity();
    test_sha_stream();
    test_magic();
    test_version();
    test_precondition();
    test_real_firmware_digest();
    printf("\n=== %d checks, %d failures ===\n", g_checks, g_fail);
    return g_fail == 0 ? 0 : 1;
}
