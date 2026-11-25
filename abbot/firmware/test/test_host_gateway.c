/**
 * test_host_gateway.c —— 主机侧「设备模拟器」：用**固件自己的代码**与本机真实网关对话
 *
 * 为什么需要它（doc/06 的 DoD 缺口）：
 *   固件编译通过 ≠ 协议对得上。Actions 只能证明「编得过」，证明不了
 *   「设备端发出的签名网关认、网关回的加密包设备解得开」。
 *   本测试**直接复用固件的纯 C 层**（net_client.c 的密码学 + net_req.c 的协议形状），
 *   对本机真实网关发一条真实签名的加密请求，再验证响应签名与解密回环 ——
 *   **不需要硬件**，让「坐等硬件验证」这件事真正成立。
 *
 * 它验证的是「设备端协议实现 ↔ 真实网关」这一对，而不是再实现一遍协议。
 *
 * 用法：
 *   ./test_host_gateway <device_id> <device_secret_hex> [host] [port] [prefix]
 *   例：./test_host_gateway esp32s3_test01 <64位hex> 127.0.0.1 80 /abbot
 *
 * 端口与前缀说明：
 *   - 走 nginx（默认 127.0.0.1:80 + /abbot）= **生产真实路径**：
 *     nginx 会剥掉挂载前缀，网关看到的仍是纯路径 /api/v1/xxx，
 *     与设备端签名口径（doc/05 §3.2）天然一致。
 *   - 直连后端 18843 且前缀留空 = 绕过 nginx 的对照路径。
 *
 * ⚠️ 密钥只从命令行传入（不入库、不写文件）；devices.json 已被 .gitignore 排除。
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <sys/socket.h>
#include <netdb.h>

#include "net_client.h"
#include "net_req.h"

static int g_pass = 0;
static int g_fail = 0;

#define CHECK(cond, msg)                                                      \
    do {                                                                      \
        if (cond) {                                                           \
            g_pass++;                                                         \
            printf("  PASS: %s\n", (msg));                                    \
        } else {                                                              \
            g_fail++;                                                         \
            printf("  FAIL: %s\n", (msg));                                    \
        }                                                                     \
    } while (0)

/* ---------------------------------------------------------------- TCP */

static int tcp_connect(const char *host, const char *port)
{
    struct addrinfo hints;
    struct addrinfo *res = NULL;
    struct addrinfo *rp = NULL;
    int fd = -1;

    memset(&hints, 0, sizeof hints);
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    if (getaddrinfo(host, port, &hints, &res) != 0) {
        return -1;
    }
    for (rp = res; rp != NULL; rp = rp->ai_next) {
        fd = socket(rp->ai_family, rp->ai_socktype, rp->ai_protocol);
        if (fd < 0) {
            continue;
        }
        if (connect(fd, rp->ai_addr, rp->ai_addrlen) == 0) {
            break;
        }
        close(fd);
        fd = -1;
    }
    freeaddrinfo(res);
    return fd;
}

/**
 * 裸 HTTP/1.1 POST（Connection: close）。不用 curl / libcurl：
 * 签名必须覆盖**原始请求体字节**，多一层库就多一层不确定性。
 */
static int http_post(const char *host, const char *port, const char *url_path,
                     const char *device_id, const char *ts, const char *sig,
                     const char *body, size_t body_len,
                     char *resp, size_t resp_cap, size_t *resp_len)
{
    int fd = tcp_connect(host, port);
    if (fd < 0) {
        printf("  连接失败 %s:%s\n", host, port);
        return -1;
    }
    char req[4096];
    int n = snprintf(req, sizeof req,
                     "POST %s HTTP/1.1\r\n"
                     "Host: %s\r\n"
                     "X-Device-Id: %s\r\n"
                     "X-Timestamp: %s\r\n"
                     "X-Signature: %s\r\n"
                     "Content-Type: application/json\r\n"
                     "Content-Length: %zu\r\n"
                     "Connection: close\r\n"
                     "\r\n",
                     url_path, host, device_id, ts, sig, body_len);
    if (n <= 0 || (size_t)n >= sizeof req) {
        close(fd);
        return -1;
    }
    size_t sent = 0;
    while (sent < (size_t)n) {
        ssize_t w = write(fd, req + sent, (size_t)n - sent);
        if (w <= 0) {
            close(fd);
            return -1;
        }
        sent += (size_t)w;
    }
    if (body_len > 0) {
        size_t bsent = 0;
        while (bsent < body_len) {
            ssize_t w = write(fd, body + bsent, body_len - bsent);
            if (w <= 0) {
                close(fd);
                return -1;
            }
            bsent += (size_t)w;
        }
    }
    size_t total = 0;
    for (;;) {
        ssize_t r = read(fd, resp + total, resp_cap - 1 - total);
        if (r <= 0) {
            break;
        }
        total += (size_t)r;
        if (total + 1 >= resp_cap) {
            break;
        }
    }
    close(fd);
    resp[total] = '\0';
    *resp_len = total;
    return 0;
}

/* ---------------------------------------------------------------- main */

int main(int argc, char **argv)
{
    const char *device_id = (argc > 1) ? argv[1] : "esp32s3_test01";
    const char *secret_hex = (argc > 2) ? argv[2] : "";
    const char *host = (argc > 3) ? argv[3] : "127.0.0.1";
    const char *port = (argc > 4) ? argv[4] : "80";
    const char *prefix = (argc > 5) ? argv[5] : "/abbot";

    printf("=== 设备模拟器 ↔ 真实网关 ===\n");
    printf("设备 %s | 网关 %s:%s%s | 端点 %s\n",
           device_id, host, port, prefix, NR_PATH_BOOTSTRAP);

    if (strlen(secret_hex) != NC_SECRET_LEN * 2) {
        printf("  FAIL: 密钥长度应为 %d 位十六进制\n", NC_SECRET_LEN * 2);
        return 1;
    }
    uint8_t secret[NC_SECRET_LEN];
    {
        /* 十六进制解码（净_client 不导出 hex_decode，这里就地做） */
        int ok = 1;
        for (size_t i = 0; i < NC_SECRET_LEN && ok; i++) {
            int hi = -1, lo = -1;
            char a = secret_hex[i * 2], b = secret_hex[i * 2 + 1];
            if (a >= '0' && a <= '9') { hi = a - '0'; }
            else if (a >= 'a' && a <= 'f') { hi = a - 'a' + 10; }
            else if (a >= 'A' && a <= 'F') { hi = a - 'A' + 10; }
            if (b >= '0' && b <= '9') { lo = b - '0'; }
            else if (b >= 'a' && b <= 'f') { lo = b - 'a' + 10; }
            else if (b >= 'A' && b <= 'F') { lo = b - 'A' + 10; }
            if (hi < 0 || lo < 0) { ok = 0; break; }
            secret[i] = (uint8_t)((hi << 4) | lo);
        }
        CHECK(ok, "设备密钥十六进制解码");
    }

    /* --- 1. 组装业务明文（doc/05 §5.1 的 bootstrap 字段） --- */
    char plain[512];
    int pn = snprintf(plain, sizeof plain,
                      "{\"device_id\":\"%s\",\"fw_version\":\"1.0.0\",\"battery_pct\":88,"
                      "\"schedule_updated_at\":0,\"local_packs\":[],\"local_books\":[]}",
                      device_id);
    CHECK(pn > 0 && (size_t)pn < sizeof plain, "bootstrap 明文组装");

    /* --- 2. 时间戳（网关要求 |server_now - ts| <= 60） --- */
    char ts[24];
    {
        time_t now = time(NULL);
        snprintf(ts, sizeof ts, "%ld", (long)now);
    }
    printf("  X-Timestamp: %s\n", ts);

    /* --- 3. 加密 + 签名（固件的 nc_build_request） --- */
    size_t body_cap = ((size_t)pn + NC_IV_LEN + 2) / 3 * 4 + 64;
    char *body = (char *)malloc(body_cap);
    if (body == NULL) {
        printf("  FAIL: 分配请求体失败\n");
        return 1;
    }
    size_t body_len = 0;
    char sig[NC_SIGNATURE_HEX_LEN + 1];
    nc_status_t st = nc_build_request("POST", NR_PATH_BOOTSTRAP, ts,
                                      (const uint8_t *)plain, (size_t)pn,
                                      secret, body, body_cap, &body_len, sig);
    CHECK(st == NC_OK, "信封加密 + 请求签名（nc_build_request）");
    if (st != NC_OK) {
        free(body);
        return 1;
    }
    printf("  信封 %zu 字节 | 签名 %s\n", body_len, sig);

    /* --- 4. 真发出去 --- */
    char url[256];
    snprintf(url, sizeof url, "%s%s", prefix, NR_PATH_BOOTSTRAP);
    static char resp[32768];
    size_t resp_len = 0;
    int rc = http_post(host, port, url, device_id, ts, sig, body, body_len,
                       resp, sizeof resp, &resp_len);
    free(body);
    CHECK(rc == 0, "HTTP 请求发出");
    if (rc != 0) {
        printf("\n=== %d PASS / %d FAIL ===\n", g_pass, g_fail);
        return 1;
    }

    /* --- 5. 解析响应（固件的 nr_parse_http_response） --- */
    nr_http_response_t parsed;
    int prc = nr_parse_http_response(resp, resp_len, &parsed);
    CHECK(prc == 0, "响应解析（nr_parse_http_response）");
    if (prc != 0) {
        printf("  原始响应: %.200s\n", resp);
        printf("\n=== %d PASS / %d FAIL ===\n", g_pass, g_fail);
        return 1;
    }
    printf("  HTTP %d | 响应体 %zu 字节 | 响应签名 %s\n",
           parsed.status_code, parsed.body_len, parsed.resp_signature);

    /* --- 6. 响应签名校验（网关用了同一套算法，签名不一致就会在这里挂） --- */
    CHECK(parsed.status_code == 200, "网关接受签名（HTTP 200）");
    CHECK(parsed.resp_signature[0] != '\0', "网关附了 X-Resp-Signature");
    st = nc_verify_response(ts, (const uint8_t *)parsed.body, parsed.body_len,
                            parsed.resp_signature, secret);
    CHECK(st == NC_OK, "响应签名校验通过（nc_verify_response）");

    /* --- 7. 解密业务信封 --- */
    char payload_b64[16384];
    st = nc_extract_payload_field(parsed.body, parsed.body_len,
                                  payload_b64, sizeof payload_b64);
    CHECK(st == NC_OK, "取出响应 payload");
    size_t plain_cap = sizeof payload_b64 * 2 + 64;
    char *out_plain = (char *)malloc(plain_cap);
    if (out_plain == NULL) {
        printf("  FAIL: 分配解密缓冲失败\n");
        return 1;
    }
    size_t out_len = 0;
    st = nc_decrypt_envelope(payload_b64, secret, (uint8_t *)out_plain,
                             plain_cap, &out_len);
    CHECK(st == NC_OK, "响应信封解密（nc_decrypt_envelope）");
    if (st == NC_OK) {
        out_plain[out_len] = '\0';
        printf("  业务明文: %.300s\n", out_plain);

        /* --- 8. 业务字段齐全（doc/05 §5 的 bootstrap 响应契约） --- */
        CHECK(nr_json_get_int(out_plain, out_len, "server_time", -1) > 0,
              "server_time 存在");
        int quota = nr_json_get_int(out_plain, out_len, "effective_remaining_quota", -2);
        CHECK(quota >= -1, "effective_remaining_quota 存在");
        CHECK(strstr(out_plain, "\"pending_packs\"") != NULL, "pending_packs 存在");
        CHECK(strstr(out_plain, "\"pending_books\"") != NULL, "pending_books 存在");
    }
    free(out_plain);

    printf("\n=== %d PASS / %d FAIL ===\n", g_pass, g_fail);
    return g_fail == 0 ? 0 : 1;
}
