/**
 * net_client.h —— 端云安全信封（设备端）
 *
 * 严格对齐 abbot/doc/05-云端接口与AI推理引擎契约.md §3：
 *   §3.1 密钥派生：HMAC 用完整 32B device_secret，AES-128 密钥取前 16B；
 *   §3.2 签名串  ：`METHOD\nPATH\nX-Timestamp\nSHA256(raw_body)`，
 *                 PATH 为纯 API 路径（不含域名 / query / `/abbot` 挂载前缀）；
 *                 业务明文 AES-128-CTR 加密后 Base64(IV_16B || Ciphertext)，
 *                 装进顶层信封 `{"payload":"<base64>"}`；
 *                 响应签名 `X-Resp-Signature = HMAC(secret, "REQ_TS\nSHA256(raw_resp_body)")`；
 *   §3.4 锁定向量：所有实现必须先用本组向量对齐，冲突时以向量为准。
 *
 * 本头文件**不依赖 Arduino.h**，同一份实现可在 ESP32 固件与主机侧 native 测试中编译。
 * 设备密钥只存 NVS，严禁出现在源码与仓库中。
 */
#ifndef ABBOT_NET_CLIENT_H
#define ABBOT_NET_CLIENT_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define NC_SHA256_HEX_LEN 64
#define NC_SIGNATURE_HEX_LEN 64
#define NC_IV_LEN 16
#define NC_AES_KEY_LEN 16
#define NC_SECRET_LEN 32
#define NC_DIGEST_LEN 32

typedef enum {
    NC_OK          = 0,
    NC_ERR_INVALID = -1,   /* 参数非法 */
    NC_ERR_TOO_SMALL = -2, /* 输出缓冲区不足 */
    NC_ERR_CRYPTO  = -3,   /* mbedtls 调用失败 */
    NC_ERR_BASE64  = -4,   /* Base64 解码失败 */
    NC_ERR_VERIFY  = -5,   /* 验签不通过 */
} nc_status_t;

/* ---------- 基础原语 ---------- */

/** SHA-256 十六进制小写摘要。空 body 的摘要即 05 §3.4 的 e3b0c442… */
nc_status_t nc_sha256_hex(const uint8_t *data, size_t len, char out[NC_SHA256_HEX_LEN + 1]);

/** HMAC-SHA256 十六进制小写摘要。 */
nc_status_t nc_hmac_sha256_hex(const uint8_t *key, size_t key_len,
                               const uint8_t *msg, size_t msg_len,
                               char out[NC_SIGNATURE_HEX_LEN + 1]);

/** AES-128-CTR 加密/解密（同一函数，CTR 对称）。 */
nc_status_t nc_aes_ctr_crypt(const uint8_t key[NC_AES_KEY_LEN], const uint8_t iv[NC_IV_LEN],
                              const uint8_t *in, size_t len, uint8_t *out);

/** 大端序 128-bit 计数器推进（04 §4.2 的 nonce_counter 公式 / cards.enc 局部块解密）。 */
nc_status_t nc_add_counter(const uint8_t iv[NC_IV_LEN], uint64_t n, uint8_t out[NC_IV_LEN]);

nc_status_t nc_base64_encode(const uint8_t *in, size_t len, char *out, size_t out_cap, size_t *out_len);
nc_status_t nc_base64_decode(const char *in, uint8_t *out, size_t out_cap, size_t *out_len);

/** 平台随机源（ESP32 用 esp_random，主机测试用 /dev/urandom）。 */
void nc_random_bytes(uint8_t *out, size_t len);

/* ---------- 端云信封 ---------- */

/** 仅供单测复现 05 §3.4 固定向量使用；生产路径请用 nc_build_request。 */
nc_status_t nc_build_request_with_iv(const char *method, const char *path, const char *timestamp,
                                     const uint8_t *plain, size_t plain_len,
                                     const uint8_t secret[NC_SECRET_LEN],
                                     const uint8_t iv[NC_IV_LEN],
                                     char *body_out, size_t body_cap, size_t *body_len,
                                     char sig_out[NC_SIGNATURE_HEX_LEN + 1]);

/**
 * 生成请求体与请求签名。IV 每信封独立随机（05 §3.2 强制）。
 * body_out 需容纳 `{"payload":"<base64>"}`；base64 长度约为 (plain_len+16)*4/3。
 */
nc_status_t nc_build_request(const char *method, const char *path, const char *timestamp,
                              const uint8_t *plain, size_t plain_len,
                              const uint8_t secret[NC_SECRET_LEN],
                              char *body_out, size_t body_cap, size_t *body_len,
                              char sig_out[NC_SIGNATURE_HEX_LEN + 1]);

/** 校验响应签名：HMAC(secret, "REQ_X_TIMESTAMP\nSHA256(raw_resp_body)")。 */
nc_status_t nc_verify_response(const char *req_timestamp,
                                const uint8_t *raw_resp_body, size_t resp_len,
                                const char *resp_signature,
                                const uint8_t secret[NC_SECRET_LEN]);

/** 解密业务信封 `Base64(IV||Ciphertext)`。 */
nc_status_t nc_decrypt_envelope(const char *payload_b64, const uint8_t secret[NC_SECRET_LEN],
                                 uint8_t *out, size_t out_cap, size_t *out_len);

/** 响应信封 `{"code":N,"msg":"...","payload":"<b64>"}` 里的 payload 字段（可能为空串）。 */
nc_status_t nc_extract_payload_field(const char *raw_body, size_t len, char *out, size_t out_cap);

#ifdef __cplusplus
}
#endif

#endif /* ABBOT_NET_CLIENT_H */
