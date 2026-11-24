/**
 * net_client.c —— 端云安全信封（设备端实现）
 *
 * 对齐 abbot/doc/05 §3.1 / §3.2 / §3.4。
 *
 * 关键实现约定：
 *   - HMAC-SHA256 用 RFC 2104 手工构造（ipad/opad），避免 mbedtls 2.x/3.x 的
 *     `mbedtls_md_hmac_*` 在 3.x 被标记 deprecated 而撞上 -Werror；
 *   - AES-128-CTR 手工推进**完整 128-bit 大端序**计数器（不依赖 mbedtls
 *     `mbedtls_aes_crypt_ctr` 的 32-bit 尾数计数器），这样 `cards.enc`
 *     的 block_index 局部块解密与 NIST SP 800-38A F.5.1 语义一致；
 *   - Base64 用 mbedtls（2.x/3.x API 一致）。
 *
 * 设备密钥只从调用方传入（生产从 NVS 读），本文件不含任何密钥。
 */

#include "net_client.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "mbedtls/aes.h"
#include "mbedtls/base64.h"
#include "mbedtls/sha256.h"
#include "mbedtls/version.h"

/**
 * mbedtls 版本兼容层
 *
 * mbedtls 3.x 把 `mbedtls_sha256_starts_ret()` 系列重命名为 `mbedtls_sha256_starts()`
 * 并改为返回 int；mbedtls 2.16（ESP32 Arduino 2.x / IDF 4.4 自带）只有 `_ret` 变体，
 * 而无 `_ret` 名的老函数返回 void。两边都要能编，故按版本宏选一套。
 */
#if MBEDTLS_VERSION_MAJOR >= 3
#define nc_sha256_start(ctx, is224)  mbedtls_sha256_starts((ctx), (is224))
#define nc_sha256_update(ctx, b, l)  mbedtls_sha256_update((ctx), (b), (l))
#define nc_sha256_finish(ctx, out)   mbedtls_sha256_finish((ctx), (out))
#else
#define nc_sha256_start(ctx, is224)  mbedtls_sha256_starts_ret((ctx), (is224))
#define nc_sha256_update(ctx, b, l)  mbedtls_sha256_update_ret((ctx), (b), (l))
#define nc_sha256_finish(ctx, out)   mbedtls_sha256_finish_ret((ctx), (out))
#endif

#if defined(ESP32)
#include "esp_system.h"
#else
#include <stdio.h>
#endif

/* ------------------------------------------------------------------ 十六进制 */
static void nc_hex_encode(const uint8_t *in, size_t len, char *out)
{
    static const char kHex[] = "0123456789abcdef";
    for (size_t i = 0; i < len; i++) {
        out[i * 2] = kHex[in[i] >> 4];
        out[i * 2 + 1] = kHex[in[i] & 0x0f];
    }
    out[len * 2] = '\0';
}

/* ------------------------------------------------------------------ SHA-256 */
nc_status_t nc_sha256_hex(const uint8_t *data, size_t len, char out[NC_SHA256_HEX_LEN + 1])
{
    if (!out) {
        return NC_ERR_INVALID;
    }
    if (!data && len > 0) {
        return NC_ERR_INVALID;
    }

    uint8_t digest[NC_DIGEST_LEN];
    mbedtls_sha256_context ctx;
    mbedtls_sha256_init(&ctx);

    int rc = nc_sha256_start(&ctx, 0);
    if (rc == 0) {
        rc = nc_sha256_update(&ctx, data ? data : (const uint8_t *)"", len);
    }
    if (rc == 0) {
        rc = nc_sha256_finish(&ctx, digest);
    }
    mbedtls_sha256_free(&ctx);

    if (rc != 0) {
        return NC_ERR_CRYPTO;
    }
    nc_hex_encode(digest, sizeof(digest), out);
    return NC_OK;
}

/* ------------------------------------------------------------------ HMAC-SHA256 (RFC 2104) */
nc_status_t nc_hmac_sha256_hex(const uint8_t *key, size_t key_len,
                               const uint8_t *msg, size_t msg_len,
                               char out[NC_SIGNATURE_HEX_LEN + 1])
{
    if (!key || key_len == 0 || !out) {
        return NC_ERR_INVALID;
    }
    if (!msg && msg_len > 0) {
        return NC_ERR_INVALID;
    }

    uint8_t k[64];
    uint8_t ipad[64];
    uint8_t opad[64];
    uint8_t inner[NC_DIGEST_LEN];
    uint8_t outer[NC_DIGEST_LEN];
    mbedtls_sha256_context ctx;

    memset(k, 0, sizeof(k));
    if (key_len > 64) {
        /* 超长密钥先做一次 SHA-256 缩到 64 字节 */
        int rc0 = nc_sha256_start(&ctx, 0);
        if (rc0 == 0) {
            rc0 = nc_sha256_update(&ctx, key, key_len);
        }
        if (rc0 == 0) {
            rc0 = nc_sha256_finish(&ctx, k);
        }
        mbedtls_sha256_free(&ctx);
        if (rc0 != 0) {
            return NC_ERR_CRYPTO;
        }
    } else {
        memcpy(k, key, key_len);
    }

    for (size_t i = 0; i < 64; i++) {
        ipad[i] = (uint8_t)(k[i] ^ 0x36);
        opad[i] = (uint8_t)(k[i] ^ 0x5c);
    }

    mbedtls_sha256_init(&ctx);
    int rc = nc_sha256_start(&ctx, 0);
    if (rc == 0) {
        rc = nc_sha256_update(&ctx, ipad, 64);
    }
    if (rc == 0 && msg_len > 0) {
        rc = nc_sha256_update(&ctx, msg, msg_len);
    }
    if (rc == 0) {
        rc = nc_sha256_finish(&ctx, inner);
    }
    mbedtls_sha256_free(&ctx);
    if (rc != 0) {
        return NC_ERR_CRYPTO;
    }

    mbedtls_sha256_init(&ctx);
    rc = nc_sha256_start(&ctx, 0);
    if (rc == 0) {
        rc = nc_sha256_update(&ctx, opad, 64);
    }
    if (rc == 0) {
        rc = nc_sha256_update(&ctx, inner, sizeof(inner));
    }
    if (rc == 0) {
        rc = nc_sha256_finish(&ctx, outer);
    }
    mbedtls_sha256_free(&ctx);
    if (rc != 0) {
        return NC_ERR_CRYPTO;
    }

    nc_hex_encode(outer, sizeof(outer), out);
    return NC_OK;
}

/* ------------------------------------------------------------------ 计数器 */
nc_status_t nc_add_counter(const uint8_t iv[NC_IV_LEN], uint64_t n, uint8_t out[NC_IV_LEN])
{
    if (!iv || !out) {
        return NC_ERR_INVALID;
    }
    memcpy(out, iv, NC_IV_LEN);

    /* 大端序 128-bit 进位 */
    uint8_t carry = 0;
    for (int i = NC_IV_LEN - 1; i >= 0; i--) {
        uint16_t sum = (uint16_t)out[i] + (uint16_t)(n & 0xff) + carry;
        out[i] = (uint8_t)(sum & 0xff);
        carry = (uint8_t)(sum >> 8);
        n >>= 8;
    }
    return NC_OK;
}

/* ------------------------------------------------------------------ AES-128-CTR */
nc_status_t nc_aes_ctr_crypt(const uint8_t key[NC_AES_KEY_LEN], const uint8_t iv[NC_IV_LEN],
                              const uint8_t *in, size_t len, uint8_t *out)
{
    if (!key || !iv) {
        return NC_ERR_INVALID;
    }
    if (!in && len > 0) {
        return NC_ERR_INVALID;
    }
    if (!out && len > 0) {
        return NC_ERR_INVALID;
    }
    if (len == 0) {
        return NC_OK;
    }

    mbedtls_aes_context ctx;
    mbedtls_aes_init(&ctx);
    int rc = mbedtls_aes_setkey_enc(&ctx, key, 128);
    if (rc != 0) {
        mbedtls_aes_free(&ctx);
        return NC_ERR_CRYPTO;
    }

    uint8_t counter[NC_IV_LEN];
    uint8_t keystream[NC_IV_LEN];
    memcpy(counter, iv, NC_IV_LEN);

    size_t offset = 0;
    while (offset < len) {
        rc = mbedtls_aes_crypt_ecb(&ctx, MBEDTLS_AES_ENCRYPT, counter, keystream);
        if (rc != 0) {
            mbedtls_aes_free(&ctx);
            return NC_ERR_CRYPTO;
        }
        size_t chunk = len - offset;
        if (chunk > NC_IV_LEN) {
            chunk = NC_IV_LEN;
        }
        for (size_t i = 0; i < chunk; i++) {
            out[offset + i] = (uint8_t)(in[offset + i] ^ keystream[i]);
        }
        offset += chunk;

        /* 计数器 +1（大端序 128-bit） */
        for (int i = NC_IV_LEN - 1; i >= 0; i--) {
            if (++counter[i] != 0) {
                break;
            }
        }
    }

    mbedtls_aes_free(&ctx);
    return NC_OK;
}

/* ------------------------------------------------------------------ Base64 */
nc_status_t nc_base64_encode(const uint8_t *in, size_t len, char *out, size_t out_cap, size_t *out_len)
{
    if (!in || !out || !out_len) {
        return NC_ERR_INVALID;
    }
    size_t olen = 0;
    int rc = mbedtls_base64_encode((unsigned char *)out, out_cap, &olen, in, len);
    if (rc != 0) {
        return (rc == MBEDTLS_ERR_BASE64_BUFFER_TOO_SMALL) ? NC_ERR_TOO_SMALL : NC_ERR_CRYPTO;
    }
    *out_len = olen;
    return NC_OK;
}

nc_status_t nc_base64_decode(const char *in, uint8_t *out, size_t out_cap, size_t *out_len)
{
    if (!in || !out || !out_len) {
        return NC_ERR_INVALID;
    }
    size_t olen = 0;
    int rc = mbedtls_base64_decode(out, out_cap, &olen, (const unsigned char *)in, strlen(in));
    if (rc != 0) {
        return NC_ERR_BASE64;
    }
    *out_len = olen;
    return NC_OK;
}

/* ------------------------------------------------------------------ 随机源 */
void nc_random_bytes(uint8_t *out, size_t len)
{
    if (!out || len == 0) {
        return;
    }
#if defined(ESP32)
    esp_fill_random(out, len);
#else
    FILE *fp = fopen("/dev/urandom", "rb");
    if (fp) {
        size_t got = fread(out, 1, len, fp);
        fclose(fp);
        if (got == len) {
            return;
        }
    }
    /* 兜底：极端情况下用时间戳填充（仅主机测试环境） */
    for (size_t i = 0; i < len; i++) {
        out[i] = (uint8_t)(i * 31 + 7);
    }
#endif
}

/* ------------------------------------------------------------------ 请求构建 */
static nc_status_t nc_encrypt_envelope(const uint8_t *plain, size_t plain_len,
                                       const uint8_t secret[NC_SECRET_LEN],
                                       const uint8_t iv[NC_IV_LEN],
                                       char *b64_out, size_t b64_cap, size_t *b64_len)
{
    uint8_t aes_key[NC_AES_KEY_LEN];
    memcpy(aes_key, secret, NC_AES_KEY_LEN);

    size_t raw_len = NC_IV_LEN + plain_len;
    uint8_t *raw = (uint8_t *)malloc(raw_len);
    if (!raw) {
        return NC_ERR_TOO_SMALL;
    }
    memcpy(raw, iv, NC_IV_LEN);
    nc_status_t st = nc_aes_ctr_crypt(aes_key, iv, plain, plain_len, raw + NC_IV_LEN);
    if (st != NC_OK) {
        free(raw);
        return st;
    }
    st = nc_base64_encode(raw, raw_len, b64_out, b64_cap, b64_len);
    free(raw);
    return st;
}

nc_status_t nc_build_request_with_iv(const char *method, const char *path, const char *timestamp,
                                     const uint8_t *plain, size_t plain_len,
                                     const uint8_t secret[NC_SECRET_LEN],
                                     const uint8_t iv[NC_IV_LEN],
                                     char *body_out, size_t body_cap, size_t *body_len,
                                     char sig_out[NC_SIGNATURE_HEX_LEN + 1])
{
    if (!method || !path || !timestamp || !secret || !iv || !body_out || !sig_out) {
        return NC_ERR_INVALID;
    }
    if (!plain && plain_len > 0) {
        return NC_ERR_INVALID;
    }

    /* 1. 信封 payload */
    size_t b64_cap = body_cap; /* 复用同一预算, 后面再收缩 */
    char *b64 = (char *)malloc(b64_cap);
    if (!b64) {
        return NC_ERR_TOO_SMALL;
    }
    size_t b64_len = 0;
    nc_status_t st = nc_encrypt_envelope(plain, plain_len, secret, iv, b64, b64_cap, &b64_len);
    if (st != NC_OK) {
        free(b64);
        return st;
    }

    /* 2. 组装顶层 JSON 信封 `{"payload":"<base64>"}` —— 不得有多余空格 */
    /* 前缀 `{"payload":"` 固定 12 字节, 后缀 `"}` 2 字节 */
    static const char kPrefix[] = "{\"payload\":\"";
    static const size_t kPrefixLen = sizeof(kPrefix) - 1; /* 12 */
    size_t need = kPrefixLen + b64_len + 2;
    if (need > body_cap) {
        free(b64);
        return NC_ERR_TOO_SMALL;
    }
    memcpy(body_out, kPrefix, kPrefixLen);
    memcpy(body_out + kPrefixLen, b64, b64_len);
    body_out[kPrefixLen + b64_len] = '"';
    body_out[kPrefixLen + b64_len + 1] = '}';
    body_out[kPrefixLen + b64_len + 2] = '\0';
    free(b64);

    if (body_len) {
        *body_len = kPrefixLen + b64_len + 2;
    }

    /* 3. 签名：`METHOD\nPATH\nX-Timestamp\nSHA256(raw_body)` */
    char body_sha[NC_SHA256_HEX_LEN + 1];
    st = nc_sha256_hex((const uint8_t *)body_out, kPrefixLen + b64_len + 2, body_sha);
    if (st != NC_OK) {
        return st;
    }

    size_t sign_len = strlen(method) + strlen(path) + strlen(timestamp) + strlen(body_sha) + 3;
    char *sign_str = (char *)malloc(sign_len + 1);
    if (!sign_str) {
        return NC_ERR_TOO_SMALL;
    }
    snprintf(sign_str, sign_len + 1, "%s\n%s\n%s\n%s", method, path, timestamp, body_sha);

    st = nc_hmac_sha256_hex(secret, NC_SECRET_LEN, (const uint8_t *)sign_str, strlen(sign_str), sig_out);
    free(sign_str);
    return st;
}

nc_status_t nc_build_request(const char *method, const char *path, const char *timestamp,
                              const uint8_t *plain, size_t plain_len,
                              const uint8_t secret[NC_SECRET_LEN],
                              char *body_out, size_t body_cap, size_t *body_len,
                              char sig_out[NC_SIGNATURE_HEX_LEN + 1])
{
    uint8_t iv[NC_IV_LEN];
    nc_random_bytes(iv, sizeof(iv));
    return nc_build_request_with_iv(method, path, timestamp, plain, plain_len, secret, iv,
                                    body_out, body_cap, body_len, sig_out);
}

/* ------------------------------------------------------------------ 响应校验 */
nc_status_t nc_verify_response(const char *req_timestamp,
                                const uint8_t *raw_resp_body, size_t resp_len,
                                const char *resp_signature,
                                const uint8_t secret[NC_SECRET_LEN])
{
    if (!req_timestamp || !resp_signature || !secret) {
        return NC_ERR_INVALID;
    }
    if (!raw_resp_body && resp_len > 0) {
        return NC_ERR_INVALID;
    }

    char body_sha[NC_SHA256_HEX_LEN + 1];
    nc_status_t st = nc_sha256_hex(raw_resp_body, resp_len, body_sha);
    if (st != NC_OK) {
        return st;
    }

    size_t sign_len = strlen(req_timestamp) + strlen(body_sha) + 1;
    char *sign_str = (char *)malloc(sign_len + 1);
    if (!sign_str) {
        return NC_ERR_TOO_SMALL;
    }
    snprintf(sign_str, sign_len + 1, "%s\n%s", req_timestamp, body_sha);

    char expect[NC_SIGNATURE_HEX_LEN + 1];
    st = nc_hmac_sha256_hex(secret, NC_SECRET_LEN, (const uint8_t *)sign_str, strlen(sign_str), expect);
    free(sign_str);
    if (st != NC_OK) {
        return st;
    }

    /* 定长比较, 避免早期返回 */
    if (strlen(resp_signature) != NC_SIGNATURE_HEX_LEN ||
        strcmp(resp_signature, expect) != 0) {
        return NC_ERR_VERIFY;
    }
    return NC_OK;
}

nc_status_t nc_decrypt_envelope(const char *payload_b64, const uint8_t secret[NC_SECRET_LEN],
                                 uint8_t *out, size_t out_cap, size_t *out_len)
{
    if (!payload_b64 || !secret || !out || !out_len) {
        return NC_ERR_INVALID;
    }

    size_t raw_cap = strlen(payload_b64); /* base64 解码后不超过 3/4 */
    uint8_t *raw = (uint8_t *)malloc(raw_cap);
    if (!raw) {
        return NC_ERR_TOO_SMALL;
    }
    size_t raw_len = 0;
    nc_status_t st = nc_base64_decode(payload_b64, raw, raw_cap, &raw_len);
    if (st != NC_OK) {
        free(raw);
        return st;
    }
    if (raw_len < NC_IV_LEN) {
        free(raw);
        return NC_ERR_INVALID;
    }

    uint8_t aes_key[NC_AES_KEY_LEN];
    memcpy(aes_key, secret, NC_AES_KEY_LEN);

    size_t ct_len = raw_len - NC_IV_LEN;
    if (ct_len > out_cap) {
        free(raw);
        return NC_ERR_TOO_SMALL;
    }
    st = nc_aes_ctr_crypt(aes_key, raw, raw + NC_IV_LEN, ct_len, out);
    free(raw);
    if (st != NC_OK) {
        return st;
    }
    *out_len = ct_len;
    return NC_OK;
}

/* ------------------------------------------------------------------ payload 字段提取 */
nc_status_t nc_extract_payload_field(const char *raw_body, size_t len, char *out, size_t out_cap)
{
    if (!raw_body || !out) {
        return NC_ERR_INVALID;
    }
    static const char kKey[] = "\"payload\":\"";
    const size_t kKeyLen = sizeof(kKey) - 1;

    if (len < kKeyLen) {
        return NC_ERR_INVALID;
    }
    for (size_t i = 0; i + kKeyLen <= len; i++) {
        if (memcmp(raw_body + i, kKey, kKeyLen) == 0) {
            size_t start = i + kKeyLen;
            size_t end = start;
            while (end < len && raw_body[end] != '"') {
                end++;
            }
            if (end >= len) {
                return NC_ERR_INVALID;
            }
            size_t n = end - start;
            if (n + 1 > out_cap) {
                return NC_ERR_TOO_SMALL;
            }
            memcpy(out, raw_body + start, n);
            out[n] = '\0';
            return NC_OK;
        }
    }
    return NC_ERR_INVALID;
}
