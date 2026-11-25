/**
 * net_api.cpp —— 设备端云业务请求层（Arduino / ESP32 侧实现）
 *
 * 对齐 abbot/doc/04-无线网络OTA与本地存储契约.md §六 与
 * abbot/doc/05-云端接口与AI推理引擎契约.md §3.2 / §5.1。
 *
 * 分工：
 *   - `net_client.c`（纯 C）：SHA-256 / HMAC / AES-128-CTR / Base64 / 签名 / 验签 / 解密；
 *   - `net_req.c`（纯 C）：业务明文 JSON 组装、URL 拼装、HTTP 响应解析；
 *   - 本文件（Arduino）：Wi-Fi、NTP、NVS 密钥、HTTP 收发，把上面两层串起来。
 *
 * 铁律：
 *   - 签名 PATH 恒为**纯 API 路径**（不含域名 / query / `/abbot` 挂载前缀），
 *     而实际请求 URL 带挂载前缀 —— 两端口径必须一致（doc/05 §3.2）；
 *   - `edu_stage` / `enrollment_year` / `device_remaining_quota` 由本层自动注入，
 *     调用方（UI 状态机）不感知、也不得自行拼装（doc/05 §5.1）；
 *   - 响应必须先验签再解密，验签用**原始响应体字节**；
 *   - Core 1 严禁执行阻塞式网络 —— 本文件只被 `task_net_worker`（Core 0）调用。
 */

#include <Arduino.h>
#include <WiFi.h>
#include <Preferences.h>
#include <time.h>

#include <stdlib.h>
#include <string.h>

/* configTime 在 core 2.x 里由 esp32-hal-time.h 声明；该头文件不一定在 include 路径上，
 * 所以先探测再决定要不要自己声明（签名与 IDF 一致，重复声明合法）。 */
#if defined(__has_include)
#  if __has_include("esp32-hal-time.h")
#    include "esp32-hal-time.h"
#    define ABBOT_HAVE_ESP32_HAL_TIME 1
#  endif
#endif
#ifndef ABBOT_HAVE_ESP32_HAL_TIME
extern "C" void configTime(long gmtOffset_sec, int daylightOffset_sec,
                            const char *server1, const char *server2, const char *server3);
#endif

#include "net_api.h"
#include "net_client.h"
#include "net_req.h"
#include "hal_interfaces.h"

/* ===================== 常量 ===================== */

#define NET_TAG "abbot_net"

#define NVS_NS_AUTH   "auth_cfg"   /* 设备密钥 + 网关地址（doc/04 §1） */
#define NVS_NS_WIFI   "wifi_cfg"   /* 已保存热点 */
#define NVS_KEY_SECRET   "device_secret"
#define NVS_KEY_HOST     "gateway_host"
#define NVS_KEY_MOUNT    "gateway_mount"
#define NVS_KEY_DEVICE   "device_id"

#define DEFAULT_GATEWAY_HOST  "ruohan.site"
#define DEFAULT_GATEWAY_MOUNT "/abbot"
#define GATEWAY_PORT          80

#define NET_RESP_BUF_SIZE 12288
#define NET_HTTP_TIMEOUT_MS 20000

/* ===================== 内部状态 ===================== */

static WifiConnState g_wifi_state = WIFI_STATE_DISCONNECTED;
static char g_device_id[40] = { 0 };

/** HTTP 往返的静态声明（实现见文件后半段，配对流程要先用到）。 */
static bool net_http_exchange(const char *host, const char *method, const char *url,
                              const char *timestamp, const char *signature,
                              const char *body, size_t body_len,
                              bool need_sign, size_t *resp_len, nr_http_response_t *parsed);

static nr_profile_t g_profile;
static int  g_remaining_quota = -1;   /* 网关下发的剩余配额；-1 = 尚未联网同步过 */
static bool g_profile_loaded = false;

/* 共享响应缓冲：只由 net 任务串行使用，不做重入保护 */
static char g_resp[NET_RESP_BUF_SIZE];

/* ===================== 小工具 ===================== */

/**
 * 安全字符串拷贝。
 * 不用 `snprintf(dst, cap, "%s", src)` —— 目标定长时 GCC 会因为「源长度未知」
 * 报 -Wformat-truncation，-Werror 下直接编译失败。
 */
static void net_str_copy(char *dst, size_t dst_cap, const char *src)
{
    if (dst == NULL || dst_cap == 0) {
        return;
    }
    if (src == NULL) {
        dst[0] = '\0';
        return;
    }
    size_t n = strlen(src);
    if (n >= dst_cap) {
        n = dst_cap - 1;
    }
    memcpy(dst, src, n);
    dst[n] = '\0';
}

/** 写两位十进制（不依赖 printf 家族，避免 -Wformat-truncation）。 */
static void net_put2(char *p, unsigned v)
{
    p[0] = (char)('0' + ((v / 10) % 10));
    p[1] = (char)('0' + (v % 10));
}

/** 写四位十进制。 */
static void net_put4(char *p, unsigned v)
{
    p[0] = (char)('0' + ((v / 1000) % 10));
    p[1] = (char)('0' + ((v / 100) % 10));
    p[2] = (char)('0' + ((v / 10) % 10));
    p[3] = (char)('0' + (v % 10));
}

/**
 * 把 Unix 时间戳格式化成 UTC+8 的 `YYYY-MM-DDTHH:MM:SS`。
 * 固定偏移 +8，不走 gmtime/localtime（不依赖时区数据），也不用 printf。
 */
static void net_local_time_utc8(time_t epoch, char *out, size_t cap)
{
    if (out == NULL || cap < 20) {
        if (out != NULL && cap > 0) {
            out[0] = '\0';
        }
        return;
    }
    long long local = (long long)epoch + 28800LL;   /* UTC+8 = 28800 秒 */
    long long days = local / 86400LL;
    long long secs = local % 86400LL;
    if (secs < 0) {
        secs += 86400LL;
        days -= 1;
    }
    /* civil_from_days（Howard Hinnant 算法）：days since 1970-01-01 -> 年月日 */
    long long z = days + 719468;
    long long era = (z >= 0 ? z : z - 146096) / 146097;
    unsigned long long doe = (unsigned long long)(z - era * 146097);
    unsigned long long yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    long long y = (long long)yoe + era * 400;
    unsigned long long doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    unsigned long long mp = (5 * doy + 2) / 153;
    unsigned long long d = doy - (153 * mp + 2) / 5 + 1;
    unsigned long long m = mp + (mp < 10 ? 3 : -9);
    if (m <= 2) {
        y += 1;
    }
    net_put4(out, (unsigned)y);
    out[4] = '-';
    net_put2(out + 5, (unsigned)m);
    out[7] = '-';
    net_put2(out + 8, (unsigned)d);
    out[10] = 'T';
    net_put2(out + 11, (unsigned)(secs / 3600));
    out[13] = ':';
    net_put2(out + 14, (unsigned)((secs % 3600) / 60));
    out[16] = ':';
    net_put2(out + 17, (unsigned)(secs % 60));
    out[19] = '\0';
}

static bool hex_decode(const char *hex, uint8_t *out, size_t out_len)
{
    size_t n = strlen(hex);
    if (n != out_len * 2) {
        return false;
    }
    for (size_t i = 0; i < out_len; i++) {
        int hi = -1;
        int lo = -1;
        char a = hex[i * 2];
        char b = hex[i * 2 + 1];
        if (a >= '0' && a <= '9') { hi = a - '0'; }
        else if (a >= 'a' && a <= 'f') { hi = a - 'a' + 10; }
        else if (a >= 'A' && a <= 'F') { hi = a - 'A' + 10; }
        if (b >= '0' && b <= '9') { lo = b - '0'; }
        else if (b >= 'a' && b <= 'f') { lo = b - 'a' + 10; }
        else if (b >= 'A' && b <= 'F') { lo = b - 'A' + 10; }
        if (hi < 0 || lo < 0) {
            return false;
        }
        out[i] = (uint8_t)((hi << 4) | lo);
    }
    return true;
}

/** 设备唯一标识：取芯片 eFuse MAC，形如 `esp32s3_a1b2c3d4`。 */
const char *net_device_id(void)
{
    if (g_device_id[0] == '\0') {
        uint64_t mac = ESP.getEfuseMac();
        snprintf(g_device_id, sizeof g_device_id, "esp32s3_%04x%04x",
                 (unsigned)((mac >> 32) & 0xFFFF), (unsigned)(mac & 0xFFFF));
    }
    return g_device_id;
}

static void nvs_get_str(const char *ns, const char *key, char *out, size_t cap, const char *fallback)
{
    Preferences prefs;
    if (!prefs.begin(ns, false)) {
        net_str_copy(out, cap, fallback);
        return;
    }
    String v = prefs.getString(key, fallback);
    prefs.end();
    net_str_copy(out, cap, v.c_str());
}

static void nvs_set_str(const char *ns, const char *key, const char *value)
{
    Preferences prefs;
    if (!prefs.begin(ns, false)) {
        return;
    }
    prefs.putString(key, value);
    prefs.end();
}

static bool net_load_secret(uint8_t secret[NC_SECRET_LEN])
{
    char hex[NC_SECRET_LEN * 2 + 1] = { 0 };
    nvs_get_str(NVS_NS_AUTH, NVS_KEY_SECRET, hex, sizeof hex, "");
    if (hex[0] == '\0') {
        return false;
    }
    return hex_decode(hex, secret, NC_SECRET_LEN);
}

static void net_gateway(char *host, size_t host_cap, char *mount, size_t mount_cap)
{
    nvs_get_str(NVS_NS_AUTH, NVS_KEY_HOST, host, host_cap, DEFAULT_GATEWAY_HOST);
    nvs_get_str(NVS_NS_AUTH, NVS_KEY_MOUNT, mount, mount_cap, DEFAULT_GATEWAY_MOUNT);
}

/* ===================== Wi-Fi ===================== */

void net_wifi_init_auto_connect()
{
    WiFi.mode(WIFI_STA);
    WiFi.setAutoReconnect(true);
    WiFi.setSleep(false);   /* 交互设备要低延迟，别为了省电把连接打盹 */

    char ssid[33] = { 0 };
    char pass[65] = { 0 };
    nvs_get_str(NVS_NS_WIFI, "ssid", ssid, sizeof ssid, "");
    nvs_get_str(NVS_NS_WIFI, "pass", pass, sizeof pass, "");
    if (ssid[0] != '\0') {
        net_wifi_connect_and_save(ssid, pass);
    } else {
        g_wifi_state = WIFI_STATE_DISCONNECTED;
    }
}

void net_wifi_start_async_scan()
{
    g_wifi_state = WIFI_STATE_SCANNING;
    WiFi.scanNetworks(true, false);   /* 异步扫描，不阻塞调用方 */
}

int net_wifi_get_scan_results(WifiApInfo *out_list, int max_count)
{
    if (out_list == NULL || max_count <= 0) {
        return 0;
    }
    int n = WiFi.scanComplete();
    if (n < 0) {
        return 0;   /* 还没扫完 */
    }
    int count = (n < max_count) ? n : max_count;
    for (int i = 0; i < count; i++) {
        uint8_t idx = (uint8_t)i;
        net_str_copy(out_list[i].ssid, sizeof out_list[i].ssid, WiFi.SSID(idx).c_str());
        out_list[i].rssi = WiFi.RSSI(idx);
        uint8_t enc = (uint8_t)WiFi.encryptionType(idx);
        out_list[i].is_encrypted = (enc != (uint8_t)WIFI_AUTH_OPEN);
        out_list[i].is_saved = false;
    }
    WiFi.scanDelete();
    if (g_wifi_state == WIFI_STATE_SCANNING) {
        g_wifi_state = (WiFi.status() == WL_CONNECTED) ? WIFI_STATE_CONNECTED : WIFI_STATE_DISCONNECTED;
    }
    return count;
}

bool net_wifi_connect_and_save(const char *ssid, const char *password)
{
    if (ssid == NULL || ssid[0] == '\0') {
        return false;
    }
    g_wifi_state = WIFI_STATE_CONNECTING;
    WiFi.begin(ssid, password ? password : "");

    uint32_t start = millis();
    while (WiFi.status() != WL_CONNECTED && (millis() - start) < 20000) {
        delay(200);
    }
    if (WiFi.status() != WL_CONNECTED) {
        g_wifi_state = WIFI_STATE_AUTH_FAILED;
        return false;
    }
    g_wifi_state = WIFI_STATE_CONNECTED;
    nvs_set_str(NVS_NS_WIFI, "ssid", ssid);
    nvs_set_str(NVS_NS_WIFI, "pass", password ? password : "");
    Serial.printf("[%s] wifi connected: %s\n", NET_TAG, ssid);
    return true;
}

WifiConnState net_wifi_get_state()
{
    if (g_wifi_state == WIFI_STATE_CONNECTED && WiFi.status() != WL_CONNECTED) {
        g_wifi_state = WIFI_STATE_DISCONNECTED;
    }
    return g_wifi_state;
}

int8_t net_wifi_get_rssi()
{
    if (WiFi.status() != WL_CONNECTED) {
        return 0;
    }
    return (int8_t)WiFi.RSSI();
}

/* ===================== NTP 与配对 ===================== */

bool net_sync_ntp_time()
{
    /* doc/04 §1：连上 Wi-Fi 后自动向国内 NTP 校时（UTC+8） */
    configTime(8 * 3600, 0, "ntp.aliyun.com", "cn.pool.ntp.org");

    time_t now = 0;
    uint32_t start = millis();
    while ((millis() - start) < 15000) {
        time(&now);
        if (now > 1700000000) {   /* 2023-11-14 之后才算校时成功 */
            return true;
        }
        delay(500);
    }
    Serial.printf("[%s] NTP 校时失败\n", NET_TAG);
    return false;
}

bool net_pair_with_code(const char *six_digit_code)
{
    if (six_digit_code == NULL || strlen(six_digit_code) != 6) {
        return false;
    }
    char host[128];
    char mount[64];
    net_gateway(host, sizeof host, mount, sizeof mount);

    char url[256];
    if (nr_build_url(host, mount, NR_PATH_PAIR, url, sizeof url) < 0) {
        return false;
    }

    int need = nr_build_pair_plain(net_device_id(), six_digit_code, NULL, 0);
    if (need <= 0) {
        return false;
    }
    char *plain = (char *)malloc((size_t)need);
    if (plain == NULL) {
        return false;
    }
    if (nr_build_pair_plain(net_device_id(), six_digit_code, plain, (size_t)need) < 0) {
        free(plain);
        return false;
    }

    size_t resp_len = 0;
    nr_http_response_t parsed;
    /* 配对是明文请求：不加签、不加密 */
    bool ok = false;
    if (net_http_exchange(host, "POST", url, NULL, NULL, plain, strlen(plain),
                          false, &resp_len, &parsed)) {
        /* 响应信封的 payload 是明文 JSON 串（此时双方都还没有密钥） */
        char inner[384];
        int inner_len = nr_json_get_string(g_resp, resp_len, "payload", inner, sizeof inner);
        if (inner_len > 0) {
            char secret_hex[NC_SECRET_LEN * 2 + 1] = { 0 };
            int slen = nr_json_get_string(inner, (size_t)inner_len, "device_secret",
                                         secret_hex, sizeof secret_hex);
            if (slen == (int)(NC_SECRET_LEN * 2)) {
                nvs_set_str(NVS_NS_AUTH, NVS_KEY_SECRET, secret_hex);
                nvs_set_str(NVS_NS_AUTH, NVS_KEY_DEVICE, net_device_id());
                ok = true;
            }
        }
    }
    free(plain);
    if (ok) {
        Serial.printf("[%s] 配对成功，密钥已入 NVS\n", NET_TAG);
    } else {
        Serial.printf("[%s] 配对失败\n", NET_TAG);
    }
    return ok;
}

/* ===================== 档案上下文 ===================== */

bool net_profile_set(const char *edu_stage, int enrollment_year, int remaining_quota)
{
    if (edu_stage == NULL || edu_stage[0] == '\0') {
        return false;
    }
    net_str_copy(g_profile.edu_stage, sizeof g_profile.edu_stage, edu_stage);
    g_profile.enrollment_year = enrollment_year;
    g_profile.device_remaining_quota = remaining_quota;
    g_remaining_quota = remaining_quota;
    g_profile_loaded = true;
    return true;
}

bool net_profile_load(NetProfileContext *out)
{
    if (out == NULL) {
        return false;
    }
    if (!g_profile_loaded) {
        memset(&g_profile, 0, sizeof g_profile);
        g_remaining_quota = -1;
    }
    net_str_copy(out->edu_stage, sizeof out->edu_stage, g_profile.edu_stage);
    out->enrollment_year = g_profile.enrollment_year;
    out->device_remaining_quota = g_profile.device_remaining_quota;
    return g_profile_loaded;
}

bool net_profile_update_quota(int effective_remaining_quota)
{
    if (effective_remaining_quota < 0) {
        return false;
    }
    g_remaining_quota = effective_remaining_quota;
    g_profile.device_remaining_quota = effective_remaining_quota;
    return true;
}

int net_profile_remaining_quota(void)
{
    return g_remaining_quota;
}

/* ===================== HTTP 收发 ===================== */

/**
 * 阻塞式 HTTP 往返（只在 Core 0 的 `task_net_worker` 里调用）。
 * need_sign=true 时按 doc/05 §3.2 带设备签名头；timestamp/signature 由调用方给出，
 * **必须与组装信封时用的是同一份**，否则服务端复算的签名对不上。
 * 响应体落在共享缓冲 g_resp，返回原始字节（验签必须用它）。
 */
static bool net_http_exchange(const char *host, const char *method, const char *url,
                              const char *timestamp, const char *signature,
                              const char *body, size_t body_len,
                              bool need_sign, size_t *resp_len, nr_http_response_t *parsed)
{
    if (resp_len == NULL || parsed == NULL) {
        return false;
    }
    if (need_sign && (timestamp == NULL || signature == NULL ||
                      timestamp[0] == '\0' || signature[0] == '\0')) {
        Serial.printf("[%s] 缺少签名参数\n", NET_TAG);
        return false;
    }
    if (WiFi.status() != WL_CONNECTED) {
        return false;
    }

    WiFiClient client;
    if (!client.connect(host, GATEWAY_PORT, NET_HTTP_TIMEOUT_MS)) {
        Serial.printf("[%s] 连接网关失败\n", NET_TAG);
        return false;
    }
    /* ⚠️ WiFiClient::setTimeout 收的是**秒**（core 2.0.17 的实现是 seconds * 1000），
     * 传毫秒会被当成几小时 —— 这里显式换算。 */
    client.setTimeout((uint32_t)(NET_HTTP_TIMEOUT_MS / 1000));

    client.print(method);
    client.print(" ");
    client.print(url);
    client.print(" HTTP/1.1\r\n");
    client.print("Host: ");
    client.print(host);
    client.print("\r\n");
    client.print("X-Device-Id: ");
    client.print(net_device_id());
    client.print("\r\n");
    if (need_sign) {
        client.print("X-Timestamp: ");
        client.print(timestamp);
        client.print("\r\n");
        client.print("X-Signature: ");
        client.print(signature);
        client.print("\r\n");
    }
    client.print("Content-Type: application/json\r\n");
    client.print("Content-Length: ");
    client.print((unsigned long)body_len);
    client.print("\r\n");
    client.print("Connection: close\r\n\r\n");
    if (body != NULL && body_len > 0) {
        client.write((const uint8_t *)body, body_len);
    }

    size_t total = 0;
    int grace = 0;
    uint32_t start = millis();
    while ((millis() - start) < NET_HTTP_TIMEOUT_MS) {
        while (client.available() > 0) {
            int c = client.read();
            if (c < 0) {
                break;
            }
            if (total + 1 >= NET_RESP_BUF_SIZE) {
                client.stop();
                Serial.printf("[%s] 响应超出缓冲区\n", NET_TAG);
                return false;
            }
            g_resp[total++] = (char)c;
        }
        if (!client.connected()) {
            /* 服务端关连接不等于字节已收齐 —— 内核里可能还压着一段，
             * 空转一小段（内层循环已把 available 的字节抽干）再收工。 */
            if (++grace >= 20) {
                break;
            }
        } else {
            grace = 0;
        }
        delay(2);
    }
    client.stop();
    g_resp[total] = '\0';
    *resp_len = total;
    if (total == 0) {
        return false;
    }
    if (nr_parse_http_response(g_resp, total, parsed) != 0) {
        Serial.printf("[%s] 响应解析失败\n", NET_TAG);
        return false;
    }
    return true;
}

/* ===================== 端云加密通信 ===================== */

bool net_post_encrypted_json(const char *api_path, const char *plain_req_json,
                             int *out_biz_code, char **out_resp_plain_json)
{
    if (api_path == NULL || plain_req_json == NULL) {
        return false;
    }
    uint8_t secret[NC_SECRET_LEN];
    if (!net_load_secret(secret)) {
        return false;
    }
    char host[128];
    char mount[64];
    net_gateway(host, sizeof host, mount, sizeof mount);

    char url[256];
    if (nr_build_url(host, mount, api_path, url, sizeof url) < 0) {
        return false;
    }

    size_t plain_len = strlen(plain_req_json);
    /* 信封长度上界：Base64(IV 16B + 明文) + JSON 包装 */
    size_t body_cap = ((plain_len + NC_IV_LEN + 2) / 3) * 4 + 64;
    char *body = (char *)malloc(body_cap);
    if (body == NULL) {
        return false;
    }
    size_t body_len = 0;
    char sig[NC_SIGNATURE_HEX_LEN + 1];
    char ts[24];
    time_t now = 0;
    time(&now);
    snprintf(ts, sizeof ts, "%ld", (long)now);

    if (nc_build_request("POST", api_path, ts, (const uint8_t *)plain_req_json, plain_len,
                         secret, body, body_cap, &body_len, sig) != NC_OK) {
        free(body);
        return false;
    }

    size_t resp_len = 0;
    nr_http_response_t parsed;
    bool ok = false;
    int biz_code = -1;
    char *resp_plain = NULL;

    if (net_http_exchange(host, "POST", url, ts, sig, body, body_len, true,
                          &resp_len, &parsed)) {
        biz_code = nr_json_get_int(parsed.body, parsed.body_len, "code", -1);
        if (parsed.resp_signature[0] == '\0') {
            /* 没签名头 = 网关在鉴权阶段就拒了（那时 payload 必为空，不解密） */
            Serial.printf("[%s] 响应无签名（鉴权阶段被拒？）code=%d\n", NET_TAG, biz_code);
        } else if (nc_verify_response(ts, (const uint8_t *)parsed.body, parsed.body_len,
                                      parsed.resp_signature, secret) != NC_OK) {
            Serial.printf("[%s] 响应验签失败\n", NET_TAG);
            biz_code = -1;
        } else {
            char *payload_b64 = (char *)malloc(NET_RESP_BUF_SIZE);
            /* nc_extract_payload_field 返回的是状态码，长度要看 payload_b64 本身 */
            nc_status_t pst = (payload_b64 == NULL)
                                  ? NC_ERR_INVALID
                                  : nc_extract_payload_field(parsed.body, parsed.body_len,
                                                             payload_b64, NET_RESP_BUF_SIZE);
            size_t plen = (pst == NC_OK) ? strlen(payload_b64) : 0;
            if (plen > 0) {
                size_t out_cap = plen * 2 + 64;
                resp_plain = (char *)malloc(out_cap);
                if (resp_plain != NULL) {
                    size_t out_len = 0;
                    if (nc_decrypt_envelope(payload_b64, secret, (uint8_t *)resp_plain,
                                            out_cap, &out_len) == NC_OK) {
                        resp_plain[out_len] = '\0';
                        ok = true;
                    } else {
                        free(resp_plain);
                        resp_plain = NULL;
                    }
                }
                free(payload_b64);
            } else {
                /* 无业务数据（如错误响应）也属正常返回 */
                ok = true;
                free(payload_b64);
            }
        }
    }
    free(body);

    if (out_biz_code != NULL) {
        *out_biz_code = ok ? biz_code : -1;
    }
    if (out_resp_plain_json != NULL) {
        *out_resp_plain_json = resp_plain;
    } else if (resp_plain != NULL) {
        free(resp_plain);
    }
    return ok;
}

/* ===================== 业务高级封装 ===================== */

bool net_sync_bootstrap(const char *bootstrap_req_json, int *out_biz_code, char **out_resp_json)
{
    return net_post_encrypted_json(NR_PATH_BOOTSTRAP, bootstrap_req_json, out_biz_code, out_resp_json);
}

bool net_sync_bootstrap_auto(int *out_biz_code, char **out_resp_json)
{
    /* 电量来自 HAL（MAX17043）—— 读不到就报 0，不因此中断心跳 */
    float volts = 0.0f;
    uint8_t soc = 0;
    if (hal_power_read_battery(&volts, &soc) != HAL_OK) {
        soc = 0;
    }
    /* 本地已装学科包 / 电子书清单：TF 卡目录树归 task_storage 独占串行化访问，
     * net 任务不得直接扫盘（doc/02 §4.2），故这里报空数组 ——
     * 服务端据此下发全部待同步项，语义正确。 */
    int need = nr_build_bootstrap_plain(net_device_id(), NET_FW_VERSION, (int)soc, 0,
                                        "[]", "[]", NULL, 0);
    if (need <= 0) {
        return false;
    }
    char *plain = (char *)malloc((size_t)need);
    if (plain == NULL) {
        return false;
    }
    if (nr_build_bootstrap_plain(net_device_id(), NET_FW_VERSION, (int)soc, 0,
                                 "[]", "[]", plain, (size_t)need) < 0) {
        free(plain);
        return false;
    }
    bool ok = net_post_encrypted_json(NR_PATH_BOOTSTRAP, plain, out_biz_code, out_resp_json);
    free(plain);
    return ok;
}

bool net_study_solve(const char *session_id, const uint8_t *jpg_data, size_t jpg_len,
                     const uint8_t *pcm_data, size_t pcm_len, uint8_t hint_level,
                     int *out_biz_code, char **out_resp_json)
{
    NetProfileContext ctx;
    if (!net_profile_load(&ctx)) {
        /* 学段没设置 —— 网关会回 4004，但设备端先把字段按空值送出去，
         * 由服务端统一裁定；UI 层拿到业务码后映射成中文提示。 */
        memset(&ctx, 0, sizeof ctx);
        ctx.device_remaining_quota = 0;
    }
    nr_profile_t prof;
    memset(&prof, 0, sizeof prof);
    net_str_copy(prof.edu_stage, sizeof prof.edu_stage, ctx.edu_stage);
    prof.enrollment_year = ctx.enrollment_year;
    prof.device_remaining_quota = ctx.device_remaining_quota;

    int need = nr_build_solve_plain(&prof, session_id, jpg_data, jpg_len,
                                    pcm_data, pcm_len, hint_level, NULL, 0);
    if (need <= 0) {
        return false;
    }
    char *plain = (char *)malloc((size_t)need);
    if (plain == NULL) {
        return false;
    }
    if (nr_build_solve_plain(&prof, session_id, jpg_data, jpg_len,
                             pcm_data, pcm_len, hint_level, plain, (size_t)need) < 0) {
        free(plain);
        return false;
    }
    bool ok = net_post_encrypted_json(NR_PATH_SOLVE, plain, out_biz_code, out_resp_json);
    free(plain);
    return ok;
}

bool net_study_voice(const char *context_page, const char *pack_id, const char *card_id,
                     const uint8_t *pcm_data, size_t pcm_len,
                     int *out_biz_code, char **out_resp_json)
{
    NetProfileContext ctx;
    if (!net_profile_load(&ctx)) {
        memset(&ctx, 0, sizeof ctx);
        ctx.device_remaining_quota = 0;
    }
    nr_profile_t prof;
    memset(&prof, 0, sizeof prof);
    net_str_copy(prof.edu_stage, sizeof prof.edu_stage, ctx.edu_stage);
    prof.enrollment_year = ctx.enrollment_year;
    prof.device_remaining_quota = ctx.device_remaining_quota;

    char local_time[32];
    time_t now = 0;
    time(&now);
    net_local_time_utc8(now, local_time, sizeof local_time);

    int need = nr_build_voice_plain(&prof, context_page, pack_id, card_id,
                                    pcm_data, pcm_len, local_time, NULL, 0);
    if (need <= 0) {
        return false;
    }
    char *plain = (char *)malloc((size_t)need);
    if (plain == NULL) {
        return false;
    }
    if (nr_build_voice_plain(&prof, context_page, pack_id, card_id,
                             pcm_data, pcm_len, local_time, plain, (size_t)need) < 0) {
        free(plain);
        return false;
    }
    bool ok = net_post_encrypted_json(NR_PATH_VOICE, plain, out_biz_code, out_resp_json);
    free(plain);
    return ok;
}
