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
#include <esp_heap_caps.h>
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
#include "cards.h"
#include "outbox.h"
#include "hal_interfaces.h"
#include "hal_shared.h"

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

/**
 * 从「明文信封」应答里取出 `device_secret` 并写入 NVS。
 *
 * 配对与自动登记的应答形状完全一致（payload 是明文 JSON 串，内含
 * `device_secret` 的 64 位 Hex），所以两条路径共用这一段解析。
 *
 * 返回 true = 密钥已落 NVS。
 */
static bool net_store_secret_from_plain_reply(size_t resp_len)
{
    char inner[384];
    int inner_len = nr_json_get_string(g_resp, resp_len, "payload", inner, sizeof inner);
    if (inner_len <= 0) {
        return false;
    }
    char secret_hex[NC_SECRET_LEN * 2 + 1] = { 0 };
    int slen = nr_json_get_string(inner, (size_t)inner_len, "device_secret",
                                 secret_hex, sizeof secret_hex);
    if (slen != (int)(NC_SECRET_LEN * 2)) {
        return false;
    }
    nvs_set_str(NVS_NS_AUTH, NVS_KEY_SECRET, secret_hex);
    nvs_set_str(NVS_NS_AUTH, NVS_KEY_DEVICE, net_device_id());
    return true;
}

/** 发一个明文 POST（不加签、不加密）并把应答交给调用方解析。 */
static bool net_post_plain(const char *api_path, const char *plain, size_t plain_len,
                           size_t *out_resp_len)
{
    char host[128];
    char mount[64];
    net_gateway(host, sizeof host, mount, sizeof mount);

    char url[256];
    if (nr_build_url(host, mount, api_path, url, sizeof url) < 0) {
        return false;
    }
    size_t resp_len = 0;
    nr_http_response_t parsed;
    /* 明文请求：不加签、不加密 */
    if (!net_http_exchange(host, "POST", url, NULL, NULL, plain, plain_len,
                           false, &resp_len, &parsed)) {
        return false;
    }
    if (out_resp_len != NULL) {
        *out_resp_len = resp_len;
    }
    return true;
}

bool net_pair_with_code(const char *six_digit_code)
{
    if (six_digit_code == NULL || strlen(six_digit_code) != 6) {
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
    bool ok = net_post_plain(NR_PATH_PAIR, plain, strlen(plain), &resp_len) &&
              net_store_secret_from_plain_reply(resp_len);
    free(plain);
    if (ok) {
        Serial.printf("[%s] 配对成功，密钥已入 NVS\n", NET_TAG);
    } else {
        Serial.printf("[%s] 配对失败\n", NET_TAG);
    }
    return ok;
}

bool net_is_paired(void)
{
    uint8_t secret[NC_SECRET_LEN];
    return net_load_secret(secret);
}

bool net_auto_register(void)
{
    /* 自动登记：设备自报身份，网关幂等签发密钥（doc/04 §1.2）。
     * 设备侧零输入 —— 不弹码、不进虚拟键盘。 */
    int need = nr_build_register_plain(net_device_id(), NET_FW_VERSION, NULL, 0);
    if (need <= 0) {
        return false;
    }
    char *plain = (char *)malloc((size_t)need);
    if (plain == NULL) {
        return false;
    }
    if (nr_build_register_plain(net_device_id(), NET_FW_VERSION, plain, (size_t)need) < 0) {
        free(plain);
        return false;
    }

    size_t resp_len = 0;
    bool ok = net_post_plain(NR_PATH_REGISTER, plain, strlen(plain), &resp_len) &&
              net_store_secret_from_plain_reply(resp_len);
    free(plain);
    if (ok) {
        Serial.printf("[%s] 自动登记成功，密钥已入 NVS\n", NET_TAG);
    } else {
        Serial.printf("[%s] 自动登记失败，稍后重试\n", NET_TAG);
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

/* ===================== 离线队列（outbox，doc/04 §3.5） ===================== */

/**
 * 把一张拍题请求入队（断网 / 未配对 / 云端忙时用）。
 *
 * **关联的 JPG 已经在 TF 卡上了** —— 相机任务拍完就落盘（doc/04 §3.5 命名法），
 * 本函数只负责把「这张图待补传」记进索引，不重复写一遍图。
 *
 * 队列满了（>10 条）时淘汰**最旧**的一条，并把它的关联文件删掉
 * （doc/04 §3.5 的媒体垃圾文件清理铁律：索引里没有的条目，文件也不能留）。
 */
bool net_outbox_enqueue_solve(const char *session_id, const char *media_path,
                               uint8_t hint_level)
{
    if (media_path == NULL || media_path[0] == '\0') {
        return false;
    }
    /* 没有关联文件的条目没有补传价值 —— 不入队。 */
    uint32_t size = 0;
    if (outbox_file_size(media_path, &size) != OUTBOX_OK || size == 0) {
        return false;
    }

    time_t now = 0;
    time(&now);

    outbox_queue_t q;
    if (outbox_load(&q) < 0) {
        return false;
    }

    outbox_entry_t e;
    memset(&e, 0, sizeof(e));
    snprintf(e.task_id, sizeof(e.task_id), "%ld", (long)now);
    e.type = OUTBOX_TYPE_SOLVE;
    e.status = OUTBOX_STATUS_PENDING;
    e.retry_count = 0;
    e.hint_level = hint_level;
    e.created_at = (uint32_t)now;
    snprintf(e.image_path, sizeof(e.image_path), "%s", media_path);
    if (session_id != NULL) {
        snprintf(e.session_id, sizeof(e.session_id), "%s", session_id);
    }

    int evicted = -1;
    outbox_enqueue(&q, &e, &evicted);
    if (evicted >= 0) {
        /* 淘汰的那条的关联文件要删掉，别在卡上留垃圾。 */
        outbox_delete_file(q.items[evicted].image_path);
        outbox_delete_file(q.items[evicted].pcm_path);
    }
    if (outbox_store(&q) != OUTBOX_OK) {
        return false;
    }
    Serial.printf("[%s] 离线入队：%s（队列 %d 条）\n", NET_TAG, media_path, q.count);
    return true;
}

/** 把一条队列条目发给网关。成功返回 true。 */
static bool net_outbox_send_entry(const outbox_entry_t *e, int *out_biz)
{
    uint8_t *jpg = NULL;
    uint32_t jpg_len = 0;
    if (outbox_read_file(e->image_path, &jpg, &jpg_len) != OUTBOX_OK || jpg_len == 0) {
        /* 关联文件不见了 —— 这条没救，交给上层丢弃。 */
        return false;
    }
    char *resp = NULL;
    int biz = -1;
    bool ok = net_study_solve(e->session_id, jpg, jpg_len, NULL, 0, e->hint_level,
                               &biz, &resp);
    if (resp != NULL) {
        free(resp);
    }
    free(jpg);
    if (out_biz != NULL) {
        *out_biz = biz;
    }
    return ok;
}

void net_media_cleanup(const char *media_path)
{
    if (media_path == NULL || media_path[0] == '\0') {
        return;
    }
    outbox_delete_file(media_path);
}

int net_outbox_flush(void)
{
    if (!net_is_paired()) {
        return 0;   /* 还没接入，补传不了 */
    }
    outbox_queue_t q;
    if (outbox_load(&q) <= 0) {
        return 0;
    }

    /* 先把「该丢的」挑出来（重试超限 / 过期）—— 它们的文件一并删掉。 */
    time_t t = 0;
    time(&t);
    int dropped[OUTBOX_MAX_TASKS];
    int n_drop = outbox_select_expired(&q, (uint32_t)t, dropped, OUTBOX_MAX_TASKS);
    for (int i = 0; i < n_drop; i++) {
        int idx = dropped[i];
        if (idx < 0 || idx >= q.count) {
            continue;
        }
        outbox_delete_file(q.items[idx].image_path);
        outbox_delete_file(q.items[idx].pcm_path);
        outbox_remove(&q, idx);
    }
    if (n_drop > 0) {
        outbox_store(&q);
    }

    /*
     * 补传**一条**就返回（不把整个队列一次发完）。
     *
     * 为什么一次只发一条：一次 HTTP 往返最长 20 秒（`NR_HTTP_TIMEOUT_MS`），
     * 而这个函数跑在网络任务里 —— 一次发完会把业务请求（拍题/语音）饿死。
     * 调用方循环调用直到返回 0 即可。
     */
    for (int i = 0; i < q.count; i++) {
        outbox_entry_t *e = &q.items[i];
        if (e->type != OUTBOX_TYPE_SOLVE) {
            continue;   /* 其它类型暂不支持补传 */
        }
        int biz = -1;
        if (net_outbox_send_entry(e, &biz)) {
            /* 成功：从索引移除 + 删掉关联文件（doc/04 §3.5 清理铁律）。 */
            outbox_delete_file(e->image_path);
            outbox_delete_file(e->pcm_path);
            outbox_remove(&q, i);
            outbox_store(&q);
            Serial.printf("[%s] 补传完成 1 条\n", NET_TAG);
            return 1;
        }
        /* 失败：重试次数 +1，标成待重试。这次不继续发下一条 —— 网络多半还不通。 */
        e->retry_count++;
        e->status = OUTBOX_STATUS_RETRY;
        outbox_store(&q);
        Serial.printf("[%s] 补传失败，重试 %d 次\n", NET_TAG, (int)e->retry_count);
        return 0;
    }
    return 0;
}

int net_outbox_pending(void)
{
    outbox_queue_t q;
    int n = outbox_load(&q);
    return (n > 0) ? n : 0;
}

/* ===================== 学科卡片包（doc/04 §四） ===================== */

int net_cards_load(const char *pack_id, card_pack_t *out)
{
    if (pack_id == NULL || out == NULL) {
        return CARDS_ERR_INVALID;
    }
    /*
     * 设备密钥只存在 NVS —— 解密必须在这一层做（UI 层不碰密钥）。
     * AES-128 的密钥取 `device_secret` 的**前 16 字节**（doc/05 §3.1）。
     */
    uint8_t secret[NC_SECRET_LEN];
    if (!net_load_secret(secret)) {
        return CARDS_ERR_KEY;   /* 还没接入学习助手 */
    }
    return cards_load(pack_id, secret, out);
}

/* ===================== 资产下载 ===================== */

void net_gateway_base(char *out, size_t out_cap)
{
    char host[128];
    char mount[64];
    net_gateway(host, sizeof host, mount, sizeof mount);
    snprintf(out, out_cap, "http://%s%s", host, mount);
}

/** 在响应头里找某个头的值（大小写不敏感）。找不到返回 false。 */
static bool net_header_value(const char *head, size_t head_len, const char *name,
                              char *out, size_t out_cap)
{
    size_t nlen = strlen(name);
    size_t pos = 0;
    while (pos < head_len) {
        size_t line_end = pos;
        while (line_end < head_len && head[line_end] != '\n') {
            line_end++;
        }
        size_t line_len = line_end - pos;
        if (line_len > nlen) {
            bool match = true;
            for (size_t i = 0; i < nlen; i++) {
                char a = head[pos + i];
                char b = name[i];
                if (a >= 'A' && a <= 'Z') { a = (char)(a - 'A' + 'a'); }
                if (b >= 'A' && b <= 'Z') { b = (char)(b - 'A' + 'a'); }
                if (a != b) { match = false; break; }
            }
            if (match && head[pos + nlen] == ':') {
                size_t v = pos + nlen + 1;
                while (v < line_end && (head[v] == ' ' || head[v] == '\t')) {
                    v++;
                }
                size_t vend = line_end;
                while (vend > v && (head[vend - 1] == '\r' || head[vend - 1] == ' ' ||
                                    head[vend - 1] == '\t')) {
                    vend--;
                }
                size_t vlen = vend - v;
                if (vlen + 1 > out_cap) {
                    return false;
                }
                memcpy(out, head + v, vlen);
                out[vlen] = '\0';
                return true;
            }
        }
        pos = line_end + 1;
    }
    return false;
}

/**
 * 明文 GET（token 在 query 里，不加签不加密）。
 * 响应体落 PSRAM 缓冲；`X-File-Sha256` 头一并带出。
 */
static bool net_http_get(const char *url, uint8_t **out_body, uint32_t *out_len,
                          char *out_sha_hex, size_t sha_cap)
{
    *out_body = NULL;
    *out_len = 0;
    out_sha_hex[0] = '\0';

    const char *scheme = strstr(url, "://");
    if (scheme == NULL) {
        return false;
    }
    const char *host_start = scheme + 3;
    const char *slash = strchr(host_start, '/');
    if (slash == NULL) {
        return false;
    }
    char host[128];
    size_t host_len = (size_t)(slash - host_start);
    uint16_t port = 80;
    const char *colon = (const char *)memchr(host_start, ':', host_len);
    if (colon != NULL) {
        host_len = (size_t)(colon - host_start);
        port = (uint16_t)atoi(colon + 1);
    }
    if (host_len == 0 || host_len >= sizeof(host)) {
        return false;
    }
    memcpy(host, host_start, host_len);
    host[host_len] = '\0';
    const char *path = slash;

    WiFiClient client;
    if (!client.connect(host, port, NET_HTTP_TIMEOUT_MS)) {
        return false;
    }
    client.setTimeout(NET_HTTP_TIMEOUT_MS);
    client.print("GET ");
    client.print(path);
    client.print(" HTTP/1.1\r\n");
    client.print("Host: ");
    client.print(host);
    client.print("\r\n");
    client.print("X-Device-Id: ");
    client.print(net_device_id());
    client.print("\r\n");
    client.print("Connection: close\r\n\r\n");

    /* 读响应头（到空行为止） */
    char head[512];
    size_t head_len = 0;
    bool head_done = false;
    while (!head_done) {
        int c = client.read();
        if (c < 0) {
            if (!client.connected()) {
                break;
            }
            delay(2);
            continue;
        }
        if (head_len + 1 >= sizeof(head)) {
            client.stop();
            return false;
        }
        head[head_len++] = (char)c;
        if (head_len >= 4 && memcmp(head + head_len - 4, "\r\n\r\n", 4) == 0) {
            head_done = true;
        }
    }
    head[head_len] = '\0';
    if (!head_done) {
        client.stop();
        return false;
    }
    if (strncmp(head, "HTTP/1.", 7) != 0) {
        client.stop();
        return false;
    }
    int status = atoi(head + 9);
    if (status != 200) {
        client.stop();
        return false;
    }
    char len_text[24];
    if (!net_header_value(head, head_len, "Content-Length", len_text, sizeof(len_text))) {
        client.stop();
        return false;
    }
    long content_length = atol(len_text);
    if (content_length <= 0) {
        client.stop();
        return false;
    }
    net_header_value(head, head_len, "X-File-Sha256", out_sha_hex, sha_cap);

    /* 读响应体到 PSRAM */
    uint8_t *body = (uint8_t *)heap_caps_malloc((size_t)content_length, MALLOC_CAP_SPIRAM);
    if (body == NULL) {
        client.stop();
        return false;
    }
    uint32_t got = 0;
    while (got < (uint32_t)content_length) {
        int c = client.read();
        if (c < 0) {
            if (!client.connected()) {
                break;
            }
            delay(2);
            continue;
        }
        body[got++] = (uint8_t)c;
    }
    client.stop();
    if (got != (uint32_t)content_length) {
        heap_caps_free(body);
        return false;
    }
    *out_body = body;
    *out_len = got;
    return true;
}

/** 下载并校验，产出 PSRAM 缓冲（内部用）。 */
static bool net_fetch_signed_asset(const char *signed_url, const char *expected_sha256,
                                   uint8_t **out_body, uint32_t *out_len)
{
    uint8_t *body = NULL;
    uint32_t len = 0;
    char sha_hex[NC_SHA256_HEX_LEN + 1];
    if (!net_http_get(signed_url, &body, &len, sha_hex, sizeof(sha_hex))) {
        return false;
    }
    char calc[NC_SHA256_HEX_LEN + 1];
    if (nc_sha256_hex(body, len, calc) != NC_OK) {
        heap_caps_free(body);
        return false;
    }
    const char *expect = (expected_sha256 != NULL && expected_sha256[0] != '\0')
                             ? expected_sha256
                             : sha_hex;
    if (expect[0] == '\0' || strcmp(calc, expect) != 0) {
        heap_caps_free(body);
        return false;
    }
    *out_body = body;
    *out_len = len;
    return true;
}

bool net_download_signed_asset(const char *signed_url, const char *save_path,
                               const char *expected_sha256)
{
    if (signed_url == NULL || save_path == NULL) {
        return false;
    }
    uint8_t *body = NULL;
    uint32_t len = 0;
    if (!net_fetch_signed_asset(signed_url, expected_sha256, &body, &len)) {
        return false;
    }

    /* TF 卡归存储任务独占（doc/02 §4.2）—— 交它落盘，等回执。
     * 作业对象与数据缓冲都由**本函数（网络任务）**释放：存储任务只写文件、
     * 回填 result、给信号，之后不再碰作业对象，避免网络任务醒来时作业已被释放。 */
    SemaphoreHandle_t done = xSemaphoreCreateBinary();
    storage_asset_job_t *job = (storage_asset_job_t *)malloc(sizeof(storage_asset_job_t));
    if (done == NULL || job == NULL) {
        if (done != NULL) {
            vSemaphoreDelete(done);
        }
        if (job != NULL) {
            free(job);
        }
        heap_caps_free(body);
        return false;
    }
    memset(job, 0, sizeof(*job));
    job->data = body;
    job->len = len;
    job->result = HAL_ERR_IO;
    job->done = done;

    storage_req_t req;
    memset(&req, 0, sizeof(req));
    req.op = 4;                /* 写资产文件 */
    snprintf(req.path, sizeof(req.path), "%s", save_path);
    req.asset = job;

    bool ok = false;
    if (xQueueSend(hal_storage_req_queue(), &req, 0) != pdTRUE) {
        goto cleanup;
    }
    if (xSemaphoreTake(done, pdMS_TO_TICKS(30000)) == pdTRUE) {
        ok = (job->result == HAL_OK);
    }
cleanup:
    vSemaphoreDelete(done);
    if (job->data != NULL) {
        heap_caps_free(job->data);
    }
    free(job);
    return ok;
}

/* ===================== 待同步资产 ===================== */

/** 下载单个资产：服务端相对路径 -> 设备路径 -> 下载落盘。 */
static bool net_download_one(const char *rel_path, const char *token)
{
    if (rel_path == NULL || token == NULL) {
        return false;
    }
    char dev_path[96];
    if (nr_map_rel_path(rel_path, dev_path, sizeof(dev_path)) != 0) {
        Serial.printf("[%s] 未知资源路径：%s\n", NET_TAG, rel_path);
        return false;
    }
    char base[200];
    net_gateway_base(base, sizeof(base));
    char url[320];
    if (nr_build_download_url(base, token, url, sizeof(url)) != 0) {
        return false;
    }
    if (!net_download_signed_asset(url, dev_path, NULL)) {
        Serial.printf("[%s] 下载失败：%s\n", NET_TAG, dev_path);
        return false;
    }
    Serial.printf("[%s] 已同步：%s\n", NET_TAG, dev_path);
    return true;
}

bool net_sync_pending_assets(const char *bootstrap_json, size_t len)
{
    if (bootstrap_json == NULL || len == 0) {
        return false;
    }
    bool parsed = false;

    /* 课表是对象，不是数组 */
    const char *obj = NULL;
    size_t obj_len = 0;
    if (nr_json_get_object(bootstrap_json, len, "schedule_download", &obj, &obj_len) == 0) {
        char rel[96];
        char token[256];
        if (nr_json_get_string(obj, obj_len, "rel_path", rel, sizeof(rel)) > 0 &&
            nr_json_get_string(obj, obj_len, "token", token, sizeof(token)) > 0) {
            parsed = true;
            net_download_one(rel, token);
        }
    }

    /* 学科包与电子书是数组 */
    nr_pending_t items[NR_MAX_PENDING];
    int count = 0;
    static const char *kArrays[2] = { "pending_packs", "pending_books" };
    static const char *kIdKeys[2] = { "pack_id", "book_id" };
    for (int a = 0; a < 2; a++) {
        count = 0;
        if (nr_parse_pending(bootstrap_json, len, kArrays[a], kIdKeys[a], items,
                            NR_MAX_PENDING, &count) != 0) {
            continue;
        }
        for (int i = 0; i < count; i++) {
            parsed = true;
            net_download_one(items[i].rel_path, items[i].token);
        }
    }
    return parsed;
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
