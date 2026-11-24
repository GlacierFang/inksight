/**
 * test_net_client.c —— 端云信封主机侧向量测试
 *
 * 目的：在没有实体硬件的情况下，证明固件侧的 `net_client.c` 与服务端
 * `security.py` 逐字节一致（doc/05 §3.4 锁定向量）。
 *
 * 两段验证：
 *   1) NIST SP 800-38A F.5.1 CTR-AES128 官方向量自检 —— 先证明本实现的
 *      AES-CTR 分组与 128-bit 大端序计数器推进正确，再核对 05 §3.4 向量
 *      （05 §3.4 强制要求第 2 条）。期望值与 `doc/verify/gen_vectors.py` 的
 *      `NIST_PAIRS` 逐字一致，不手抄、不另立一套。
 *   2) 05 §3.4 锁定向量全量核对 —— 信封密文、payload、请求签名、响应签名、
 *      局部块偏移解密、空 body SHA-256。
 *
 * 编译（主机侧）：
 *   gcc -I include -I src test/test_net_client.c src/net/net_client.c \
 *       -o /tmp/test_net_client -lmbedtls -lmbedcrypto
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "net_client.h"

static int g_pass = 0;
static int g_fail = 0;

static void check_hex(const char *name, const char *got, const char *want)
{
    if (got && strcmp(got, want) == 0) {
        printf("  [PASS] %s\n", name);
        g_pass++;
    } else {
        printf("  [FAIL] %s\n        got : %s\n        want: %s\n", name,
               got ? got : "(null)", want);
        g_fail++;
    }
}

static void check_bytes(const char *name, const unsigned char *got, size_t got_len,
                         const unsigned char *want, size_t want_len)
{
    if (got_len == want_len && memcmp(got, want, want_len) == 0) {
        printf("  [PASS] %s\n", name);
        g_pass++;
    } else {
        printf("  [FAIL] %s (len %zu vs %zu)\n", name, got_len, want_len);
        g_fail++;
    }
}

static void hex_to_bytes(const char *hex, unsigned char *out, size_t out_len)
{
    for (size_t i = 0; i < out_len; i++) {
        char buf[3] = {hex[i * 2], hex[i * 2 + 1], 0};
        out[i] = (unsigned char)strtoul(buf, NULL, 16);
    }
}

/* ------------------------------------------------------------------ 1. NIST 自检 */
static void test_nist_ctr_aes128(void)
{
    printf("\n[1] NIST SP 800-38A F.5.1 CTR-AES128 官方向量自检\n");

    /* 与 doc/verify/gen_vectors.py 的 NIST_PAIRS 逐字一致。
     * 语义：第 i 块的 counter = NIST_IV + (i-1)（整体 128-bit 大端序递增）。 */
    const char *kKey = "2b7e151628aed2a6abf7158809cf4f3c";
    const char *kIvt = "f0f1f2f3f4f5f6f7f8f9fafbfcfdfeff";
    struct {
        const char *pt;
        const char *ct;
    } pairs[4] = {
        {"6bc1bee22e409f96e93d7e117393172a", "874d6191b620e3261bef6864990db6ce"},
        {"ae2d8a571e03ac9c9eb76fac45af8e51", "9806f66b7970fdff8617187bb9fffdff"},
        {"30c81c46a35ce411e5fbc1191a0a52ef", "5ae4df3edbd5d35e5b4f09020db03eab"},
        {"f69f2445df4f9b17ad2b417be66c3710", "1e031dda2fbe03d1792170a0f3009cee"},
    };

    unsigned char key[16], iv[16];
    hex_to_bytes(kKey, key, 16);
    hex_to_bytes(kIvt, iv, 16);

    for (int i = 0; i < 4; i++) {
        uint8_t block_iv[16];
        nc_add_counter(iv, (uint64_t)i, block_iv);

        unsigned char pt[16], want[16], got[16];
        hex_to_bytes(pairs[i].pt, pt, 16);
        hex_to_bytes(pairs[i].ct, want, 16);

        if (nc_aes_ctr_crypt(key, block_iv, pt, 16, got) != NC_OK) {
            printf("  [FAIL] NIST block#%d 加密调用失败\n", i + 1);
            g_fail++;
            continue;
        }
        char name[64];
        snprintf(name, sizeof(name), "NIST F.5.1 block#%d", i + 1);
        check_bytes(name, got, 16, want, 16);
    }
}

/* ------------------------------------------------------------------ 2. 05 §3.4 锁定向量 */
static void test_doc05_vectors(void)
{
    printf("\n[2] doc/05 §3.4 锁定向量核对\n");

    const char *kSecretHex = "00112233445566778899aabbccddeeff0123456789abcdef0123456789abcdef";
    const char *kIvHex = "0102030405060708090a0b0c0d0e0f10";
    const char *kPlain = "{\"ping\":1}";

    unsigned char secret[32], iv[16];
    hex_to_bytes(kSecretHex, secret, 32);
    hex_to_bytes(kIvHex, iv, 16);

    /* 2.1 空 body SHA-256 */
    char sha_empty[NC_SHA256_HEX_LEN + 1];
    nc_sha256_hex((const unsigned char *)"", 0, sha_empty);
    check_hex("空 body SHA-256", sha_empty,
              "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");

    /* 2.2 固定 IV 信封 —— 顶层 payload 应为 Base64(IV || Ciphertext) */
    char body[512];
    size_t body_len = 0;
    char sig[NC_SIGNATURE_HEX_LEN + 1];
    if (nc_build_request_with_iv("POST", "/api/v1/ping", "1795412000",
                                 (const unsigned char *)kPlain, strlen(kPlain),
                                 secret, iv, body, sizeof(body), &body_len, sig) != NC_OK) {
        printf("  [FAIL] 构建请求失败\n");
        g_fail++;
        return;
    }

    const char *kWantBody = "{\"payload\":\"AQIDBAUGBwgJCgsMDQ4PEMRHWdcDEnGR1sk=\"}";
    check_hex("05 §3.4 请求体（固定 IV）", body, kWantBody);

    /* 2.3 局部块偏移解密（block_index = 2）*/
    uint8_t counter[16];
    nc_add_counter(iv, 2, counter);
    char counter_hex[NC_SHA256_HEX_LEN + 1];
    for (int i = 0; i < 16; i++) {
        snprintf(counter_hex + i * 2, 3, "%02x", counter[i]);
    }
    check_hex("block_index=2 的 128-bit 计数器", counter_hex,
              "0102030405060708090a0b0c0d0e0f12");

    unsigned char aes_key[16];
    memcpy(aes_key, secret, 16);
    unsigned char ct[16];
    nc_aes_ctr_crypt(aes_key, counter, (const unsigned char *)kPlain, strlen(kPlain), ct);
    char ct_hex[64];
    for (int i = 0; i < 10; i++) {
        snprintf(ct_hex + i * 2, 3, "%02x", ct[i]);
    }
    ct_hex[20] = '\0';
    check_hex("block_index=2 的期望密文", ct_hex, "41da37cc48e5aa3f8d06");

    /* 2.4 请求签名 */
    char body_sha[NC_SHA256_HEX_LEN + 1];
    nc_sha256_hex((const unsigned char *)body, body_len, body_sha);
    check_hex("05 §3.4 RAW_BODY_SHA256", body_sha,
              "0ac4d1b2be7fff8b8d2fabfd520add4656718547d456b728ab3f0049244fee0a");
    check_hex("05 §3.4 X-Signature", sig,
              "e93d4a3e4aaeb49a2b413c5edfb7c7b877d9f7a85bf1d4e5c01fcdab7cf45b35");

    /* 2.5 响应签名 */
    const char *kRespBody = "{\"code\":0,\"msg\":\"ok\",\"payload\":\"\"}";
    char resp_sha[NC_SHA256_HEX_LEN + 1];
    nc_sha256_hex((const unsigned char *)kRespBody, strlen(kRespBody), resp_sha);
    check_hex("05 §3.4 RAW_RESP_BODY_SHA256", resp_sha,
              "5eaa24073527cf9b66a37318bd50123aac4d649814ea6d65281189b6bef49ef3");

    if (nc_verify_response("1795412000", (const unsigned char *)kRespBody, strlen(kRespBody),
                            "c4fc10efeec73e428b7e63bcb3777658e11a850ccf2a75047847771556f0ce3c",
                            secret) != NC_OK) {
        printf("  [FAIL] 响应验签未通过\n");
        g_fail++;
    } else {
        printf("  [PASS] 05 §3.4 X-Resp-Signature 验签通过\n");
        g_pass++;
    }

    /* 负例: 篡改响应体必须验签失败 */
    const char *kTampered = "{\"code\":0,\"msg\":\"x\",\"payload\":\"\"}";
    if (nc_verify_response("1795412000", (const unsigned char *)kTampered, strlen(kTampered),
                            "c4fc10efeec73e428b7e63bcb3777658e11a850ccf2a75047847771556f0ce3c",
                            secret) == NC_OK) {
        printf("  [FAIL] 篡改响应体竟然验签通过\n");
        g_fail++;
    } else {
        printf("  [PASS] 篡改响应体被拒\n");
        g_pass++;
    }

    /* 2.6 信封回环：解密还原明文 */
    uint8_t plain[128];
    size_t plain_len = 0;
    if (nc_decrypt_envelope("AQIDBAUGBwgJCgsMDQ4PEMRHWdcDEnGR1sk=", secret,
                            plain, sizeof(plain), &plain_len) != NC_OK) {
        printf("  [FAIL] 信封解密失败\n");
        g_fail++;
    } else {
        plain[plain_len] = '\0';
        check_hex("信封回环明文", (const char *)plain, kPlain);
    }

    /* 2.7 提取响应 payload 字段 */
    char payload[256];
    if (nc_extract_payload_field(kRespBody, strlen(kRespBody), payload, sizeof(payload)) != NC_OK) {
        printf("  [FAIL] 提取 payload 字段失败\n");
        g_fail++;
    } else {
        check_hex("响应 payload 字段", payload, "");
    }

    /* 2.8 生产路径（随机 IV）必须每信封不同，且能解密还原 */
    char body2[512];
    size_t body2_len = 0;
    char sig2[NC_SIGNATURE_HEX_LEN + 1];
    if (nc_build_request("POST", "/api/v1/ping", "1795412000",
                         (const unsigned char *)kPlain, strlen(kPlain),
                         secret, body2, sizeof(body2), &body2_len, sig2) != NC_OK) {
        printf("  [FAIL] 生产路径构建请求失败\n");
        g_fail++;
    } else {
        printf("  [PASS] 生产路径构建请求成功\n");
        g_pass++;
        if (strcmp(body2, kWantBody) == 0) {
            printf("  [FAIL] 生产路径 IV 竟然复用了固定向量 IV\n");
            g_fail++;
        } else {
            printf("  [PASS] 生产路径 IV 每信封随机\n");
            g_pass++;
        }
        /* 用同一 secret 解回明文 */
        char p2[256];
        if (nc_extract_payload_field(body2, body2_len, p2, sizeof(p2)) != NC_OK) {
            printf("  [FAIL] 提取生产路径 payload 失败\n");
            g_fail++;
        } else {
            uint8_t dec[128];
            size_t dec_len = 0;
            if (nc_decrypt_envelope(p2, secret, dec, sizeof(dec), &dec_len) != NC_OK) {
                printf("  [FAIL] 生产路径信封解密失败\n");
                g_fail++;
            } else {
                dec[dec_len] = '\0';
                check_hex("生产路径信封回环明文", (const char *)dec, kPlain);
            }
        }
    }
}

int main(void)
{
    printf("=== abbot net_client 向量测试 ===\n");
    test_nist_ctr_aes128();
    test_doc05_vectors();
    printf("\n=== 结果: %d PASS / %d FAIL ===\n", g_pass, g_fail);
    return g_fail == 0 ? 0 : 1;
}
