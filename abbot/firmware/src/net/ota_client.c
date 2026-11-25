/**
 * ota_client.c —— OTA 升级客户端的**纯 C 部分**
 *
 * 涵盖：三重校验的判定原语、流式 SHA-256、版本比较、分区容量计算、前置条件判定。
 *
 * 本文件**不依赖 Arduino.h，也不依赖 ESP-IDF** —— 主机侧可用 gcc 直接编译与单测
 * （见 test/test_ota.c，CI 的「主机侧向量测试」步骤会跑）。
 * 设备侧部分（HTTP 下载 / esp_ota_* / NVS 待验证标记 / 看门狗 / 重启后自检）
 * 在 src/net/ota_client_esp.c。
 *
 * 铁律（doc/04）：**OTA 失败绝不能变砖**。本文件里的所有判定函数都只做
 * 「返回错误码」，不做任何不可逆动作；不可逆动作全在 esp 侧，且都有兜底。
 */

#include "ota_client.h"

#include <ctype.h>
#include <stdio.h>
#include <string.h>

#include <mbedtls/version.h>

/*
 * mbedtls 3.x 把 `mbedtls_sha256_starts_ret()` 系列改名为 `mbedtls_sha256_starts()`；
 * 2.x 只有 _ret 系列。与 net_client.c 用的是同一套兼容写法。
 */
#if MBEDTLS_VERSION_MAJOR >= 3
#define ota_sha_start(ctx, is224) mbedtls_sha256_starts((ctx), (is224))
#define ota_sha_update(ctx, b, l) mbedtls_sha256_update((ctx), (b), (l))
#define ota_sha_finish(ctx, out)  mbedtls_sha256_finish((ctx), (out))
#else
#define ota_sha_start(ctx, is224) mbedtls_sha256_starts_ret((ctx), (is224))
#define ota_sha_update(ctx, b, l) mbedtls_sha256_update_ret((ctx), (b), (l))
#define ota_sha_finish(ctx, out)  mbedtls_sha256_finish_ret((ctx), (out))
#endif

/* ===================== 结果码 -> 中文短说明 ===================== */

const char *ota_result_text(ota_result_t r)
{
    switch (r) {
    case OTA_OK:
        return "升级完成";
    case OTA_ERR_NO_UPDATE:
        return "当前已是最新版本";
    case OTA_ERR_PRECONDITION:
        return "电量不足或信号太弱，请稍后再试";
    case OTA_ERR_NET:
        return "网络连接失败，请检查网络";
    case OTA_ERR_HTTP:
        return "下载失败，请稍后重试";
    case OTA_ERR_MAGIC:
        return "下载到的文件不是可用的固件，已放弃本次升级";
    case OTA_ERR_SIZE_MISMATCH:
        return "固件大小与服务器不一致，已放弃本次升级";
    case OTA_ERR_TOO_LARGE:
        return "固件超出设备可用空间，已放弃本次升级";
    case OTA_ERR_SHA256:
        return "固件校验未通过，已放弃本次升级";
    case OTA_ERR_FLASH:
        return "写入固件失败，设备保持原版本";
    case OTA_ERR_ROLLBACK:
        return "新版本自检未通过，已退回原版本";
    case OTA_ERR_ARG:
    case OTA_ERR_INTERNAL:
    default:
        return "升级出错，设备保持原版本";
    }
}

/* ===================== 流式 SHA-256 ===================== */

int ota_sha_stream_begin(ota_sha_stream_t *s)
{
    if (s == NULL) {
        return -1;
    }
    memset(s, 0, sizeof(*s));
    mbedtls_sha256_init(&s->ctx);
    if (ota_sha_start(&s->ctx, 0) != 0) {
        mbedtls_sha256_free(&s->ctx);
        return -1;
    }
    s->started = true;
    return 0;
}

int ota_sha_stream_update(ota_sha_stream_t *s, const uint8_t *data, size_t len)
{
    if (s == NULL || !s->started || s->finished) {
        return -1;
    }
    if (len == 0) {
        return 0;
    }
    if (data == NULL) {
        return -1;
    }
    if (ota_sha_update(&s->ctx, data, len) != 0) {
        return -1;
    }
    s->total += (uint32_t)len;
    return 0;
}

int ota_sha_stream_finish(ota_sha_stream_t *s, char out_hex[65])
{
    static const char kHex[] = "0123456789abcdef";
    uint8_t digest[32];

    if (s == NULL || !s->started || s->finished || out_hex == NULL) {
        return -1;
    }
    if (ota_sha_finish(&s->ctx, digest) != 0) {
        mbedtls_sha256_free(&s->ctx);
        return -1;
    }
    mbedtls_sha256_free(&s->ctx);
    for (int i = 0; i < 32; i++) {
        out_hex[i * 2] = kHex[(digest[i] >> 4) & 0x0F];
        out_hex[i * 2 + 1] = kHex[digest[i] & 0x0F];
    }
    out_hex[64] = '\0';
    s->finished = true;
    return 0;
}

/* ===================== 三重校验的判定原语 ===================== */

bool ota_check_magic(uint8_t first_byte)
{
    return first_byte == OTA_FW_MAGIC_BYTE;
}

ota_result_t ota_check_size(uint32_t received, uint32_t declared, uint32_t capacity)
{
    if (declared == 0u) {
        return OTA_ERR_SIZE_MISMATCH;
    }
    if (declared > capacity) {
        return OTA_ERR_TOO_LARGE;
    }
    if (received != declared) {
        return OTA_ERR_SIZE_MISMATCH;
    }
    return OTA_OK;
}

bool ota_sha_equal(const char *a, const char *b)
{
    if (a == NULL || b == NULL) {
        return false;
    }
    for (int i = 0; i < 64; i++) {
        unsigned char ca = (unsigned char)a[i];
        unsigned char cb = (unsigned char)b[i];
        if (ca == '\0' || cb == '\0') {
            return false; /* 长度不足 64 位 */
        }
        if (tolower(ca) != tolower(cb)) {
            return false;
        }
    }
    return true;
}

/* ===================== 版本比较 ===================== */

/** 取一段点分数字，返回其数值，并给出消耗的字符数（不是数字则为 0）。 */
static int ota_version_segment(const char *s, int *consumed)
{
    int v = 0;
    int i = 0;
    while (s[i] >= '0' && s[i] <= '9') {
        if (v < 1000000) {
            v = v * 10 + (s[i] - '0');
        }
        i++;
    }
    *consumed = i;
    return v;
}

static const char *ota_skip_sep(const char *s)
{
    while (*s == '.' || *s == '-' || *s == '_') {
        s++;
    }
    return s;
}

int ota_version_compare(const char *cur, const char *latest)
{
    if (cur == NULL) {
        cur = "";
    }
    if (latest == NULL) {
        latest = "";
    }
    for (;;) {
        int lc = 0;
        int ll = 0;
        int vc = ota_version_segment(cur, &lc);
        int vl = ota_version_segment(latest, &ll);
        if (vc != vl) {
            return (vc > vl) ? 1 : -1;
        }
        cur = ota_skip_sep(cur + lc);
        latest = ota_skip_sep(latest + ll);
        if (lc == 0 && ll == 0) {
            /* 两边都不是数字了 */
            if (*cur == '\0' && *latest == '\0') {
                return 0;
            }
            if (*cur == '\0') {
                return -1; /* cur 段数少 */
            }
            return 1;
        }
        if (lc == 0) {
            return -1; /* cur 这段不是数字 */
        }
        if (ll == 0) {
            return 1;
        }
    }
}

bool ota_should_update(const char *cur, const char *latest)
{
    if (latest == NULL || latest[0] == '\0') {
        return false;
    }
    return ota_version_compare(cur, latest) < 0;
}

/* ===================== 升级前置条件 ===================== */

bool ota_precondition_ok(int avg_mv, int rssi_dbm)
{
    if (avg_mv < OTA_MIN_BATTERY_MV) {
        return false;
    }
    if (rssi_dbm < OTA_MIN_RSSI_DBM) {
        return false;
    }
    return true;
}
