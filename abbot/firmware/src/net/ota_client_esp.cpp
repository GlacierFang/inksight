/**
 * ota_client_esp.c —— OTA 升级客户端的**设备侧**实现
 *
 * 完整流程（doc/04 固件升级章节，顺序不可换）：
 *   1. 前置采样：静置状态连续采样 3 次（间隔 50ms）电池电压取均值，
 *      avg_mv >= 3600mV 且 RSSI >= -75dBm 才允许启动 OTA；
 *   2. 查更新：GET /api/v1/ota/check（走标准设备签名 Header）；
 *   3. 下载：边下边写，**每 4KB 喂一次看门狗**；
 *   4. 三重校验：首字节 0xE9 / 字节数与声明一致且不超分区容量 / 流式 SHA-256；
 *   5. 写备用分区：esp_ota_get_next_update_partition() -> begin -> write -> end；
 *   6. 标记启动：esp_ota_set_boot_partition() + NVS 写「待验证升级」标记；
 *   7. 重启后自检：外设初始化成功 + 稳定 20 秒 + task_ui_fsm 心跳 >= 50
 *      -> 才 esp_ota_mark_app_valid_cancel_rollback()；
 *      不通过 -> 把启动分区指回旧分区并重启（自己实现回滚，不依赖引导器）。
 *
 * ⚠️ **OTA 失败绝不能变砖**（本文件的最高约束）：
 *   - 校验不过 -> esp_ota_abort()，启动分区一个字都没动，设备照常跑旧版本；
 *   - 写分区失败 -> esp_ota_abort()，同上；
 *   - 新固件自检不过 -> 启动分区指回旧分区 + 重启；
 *   - 新固件连续启动都过不了自检（崩溃循环）-> 第 N 次启动直接退回旧分区，
 *     绝不会无限刷成砖。
 *
 * 关于「Arduino 核心会不会自动 mark valid」：
 *   本实现**不依赖**核心的行为 —— 自检与显式 mark 全部由本文件自己做，
 *   且自检放在 setup() 里**阻塞执行**（setup() 不返回，核心就没有机会插手）；
 *   万一核心在 setup() 之前就标了 VALID，回滚也由本文件的 NVS 标记 +
 *   「指回旧分区 + 重启」兜住，不依赖引导器的自动回滚。
 */

#include <Arduino.h>
#include <Preferences.h>
#include <WiFi.h>
#include <esp_ota_ops.h>
#include <esp_system.h>
#include <esp_task_wdt.h>

#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>

#include <string.h>

#include "hal_interfaces.h"
#include "net_api.h"
#include "net_client.h"
#include "net_req.h"
#include "ota_client.h"

static const char *TAG = "ota";

/* ---------- NVS：待验证升级标记 ---------- */
#define NVS_NS_OTA "ota"
#define NVS_KEY_PENDING "pending" /* 待验证升级：目标版本号（非空 = 有待验证的升级） */
#define NVS_KEY_TARGET "target"   /* 待验证升级：目标分区标签（ota_0 / ota_1） */
#define NVS_KEY_TRIES "tries"     /* 待验证升级：本次固件的启动尝试次数 */

/* ---------- 自检参数（doc/04） ---------- */
#define OTA_SELFCHECK_STABLE_MS 20000u /* 稳定运行满 20 秒 */
#define OTA_SELFCHECK_MIN_BEATS 50u    /* task_ui_fsm 主循环心跳计数下限 */
#define OTA_SELFCHECK_MAX_TRIES 2u     /* 连续启动几次过不了自检就退回旧分区 */

/* ===================== 全局状态 ===================== */

static ota_status_t g_status;
static SemaphoreHandle_t g_lock = NULL;
static volatile bool g_running = false;
static volatile bool g_cancel = false;
static volatile uint32_t g_ui_heartbeat = 0;
static bool g_selfcheck_passed = false;

static void ota_lock_init(void)
{
    if (g_lock == NULL) {
        g_lock = xSemaphoreCreateMutex();
    }
}

static void ota_lock(void)
{
    if (g_lock != NULL) {
        xSemaphoreTake(g_lock, portMAX_DELAY);
    }
}

static void ota_unlock(void)
{
    if (g_lock != NULL) {
        xSemaphoreGive(g_lock);
    }
}

static void ota_copy(char *dst, size_t cap, const char *src)
{
    if (dst == NULL || cap == 0) {
        return;
    }
    if (src == NULL) {
        dst[0] = '\0';
        return;
    }
    size_t n = strlen(src);
    if (n >= cap) {
        n = cap - 1;
    }
    memcpy(dst, src, n);
    dst[n] = '\0';
}

static void ota_set_state(ota_state_t st, ota_result_t res, const char *msg)
{
    ota_lock();
    g_status.state = st;
    g_status.result = res;
    if (msg != NULL) {
        ota_copy(g_status.message, sizeof(g_status.message), msg);
    }
    g_status.seq++;
    ota_unlock();
}

static void ota_set_progress(int pct)
{
    ota_lock();
    if (g_status.progress_pct != pct) {
        g_status.progress_pct = pct;
        g_status.seq++;
    }
    ota_unlock();
}

void ota_init(void)
{
    ota_lock_init();
    ota_lock();
    if (g_status.seq == 0) {
        g_status.state = OTA_STATE_IDLE;
        g_status.result = OTA_OK;
        g_status.progress_pct = 0;
        ota_copy(g_status.current_ver, sizeof(g_status.current_ver), NET_FW_VERSION);
        g_status.latest_ver[0] = '\0';
        g_status.changelog[0] = '\0';
        g_status.message[0] = '\0';
        g_status.seq++;
    }
    ota_unlock();
}

void ota_get_status(ota_status_t *out)
{
    if (out == NULL) {
        return;
    }
    ota_lock();
    *out = g_status;
    ota_unlock();
}

void ota_cancel(void)
{
    g_cancel = true;
}

void ota_selfcheck_heartbeat(void)
{
    g_ui_heartbeat++;
}

bool ota_selfcheck_passed(void)
{
    return g_selfcheck_passed;
}

/* ===================== NVS 小工具 ===================== */

static void ota_nvs_get(const char *key, char *out, size_t cap, const char *fallback)
{
    if (out == NULL || cap == 0) {
        return;
    }
    Preferences prefs;
    if (!prefs.begin(NVS_NS_OTA, false)) {
        ota_copy(out, cap, fallback);
        return;
    }
    String v = prefs.getString(key, fallback);
    prefs.end();
    ota_copy(out, cap, v.c_str());
}

static void ota_nvs_set(const char *key, const char *value)
{
    Preferences prefs;
    if (!prefs.begin(NVS_NS_OTA, false)) {
        return;
    }
    prefs.putString(key, (value != NULL) ? value : "");
    prefs.end();
}

static int ota_nvs_get_int(const char *key, int fallback)
{
    Preferences prefs;
    if (!prefs.begin(NVS_NS_OTA, false)) {
        return fallback;
    }
    int v = prefs.getInt(key, fallback);
    prefs.end();
    return v;
}

static void ota_nvs_set_int(const char *key, int value)
{
    Preferences prefs;
    if (!prefs.begin(NVS_NS_OTA, false)) {
        return;
    }
    prefs.putInt(key, value);
    prefs.end();
}

static void ota_nvs_clear_pending(void)
{
    ota_nvs_set(NVS_KEY_PENDING, "");
    ota_nvs_set(NVS_KEY_TARGET, "");
    ota_nvs_set_int(NVS_KEY_TRIES, 0);
}

/* ===================== 分区标签 ===================== */

static void ota_partition_tag(const esp_partition_t *p, char *out, size_t cap)
{
    if (out == NULL || cap == 0) {
        return;
    }
    out[0] = '\0';
    if (p == NULL) {
        return;
    }
    if (p->subtype == ESP_PARTITION_SUBTYPE_APP_OTA_0) {
        ota_copy(out, cap, "ota_0");
    } else if (p->subtype == ESP_PARTITION_SUBTYPE_APP_OTA_1) {
        ota_copy(out, cap, "ota_1");
    } else {
        ota_copy(out, cap, "app");
    }
}

/* ===================== 1. 前置采样 ===================== */

/**
 * 静置状态连续采样 3 次（间隔 50ms）取电池电压均值。
 * 读不到电量一律按「不满足」处理 —— 宁可不升级，也不能升到一半断电。
 */
static bool ota_sample_battery(int *out_avg_mv)
{
    if (out_avg_mv == NULL) {
        return false;
    }
    uint32_t sum = 0;
    for (int i = 0; i < 3; i++) {
        float volts = 0.0f;
        uint8_t soc = 0;
        if (hal_power_read_battery(&volts, &soc) != HAL_OK) {
            *out_avg_mv = 0;
            return false;
        }
        sum += (uint32_t)(volts * 1000.0f);
        if (i < 2) {
            delay(50);
        }
    }
    *out_avg_mv = (int)(sum / 3u);
    return true;
}

/* ===================== 下载（边下边写 + 流式摘要） ===================== */

/** 解析绝对地址：scheme://host[:port]/path?query */
static bool ota_parse_url(const char *url, char *host, size_t host_cap,
                           uint16_t *port, char *path, size_t path_cap)
{
    if (url == NULL || host == NULL || path == NULL) {
        return false;
    }
    const char *scheme = strstr(url, "://");
    if (scheme == NULL) {
        return false;
    }
    const char *host_start = scheme + 3;
    const char *slash = strchr(host_start, '/');
    if (slash == NULL) {
        return false;
    }
    size_t host_len = (size_t)(slash - host_start);
    uint16_t p = 80;
    const char *colon = (const char *)memchr(host_start, ':', host_len);
    if (colon != NULL) {
        host_len = (size_t)(colon - host_start);
        p = (uint16_t)atoi(colon + 1);
    }
    if (host_len == 0 || host_len >= host_cap) {
        return false;
    }
    memcpy(host, host_start, host_len);
    host[host_len] = '\0';
    if (strlen(slash) + 1 > path_cap) {
        return false;
    }
    strcpy(path, slash);
    if (port != NULL) {
        *port = p;
    }
    return true;
}

/** 从响应头里取某个字段的值（必须落在行首，避免前缀误匹配）。 */
static bool ota_header_value(const char *head, const char *name, char *out, size_t cap)
{
    if (head == NULL || name == NULL || out == NULL || cap == 0) {
        return false;
    }
    size_t name_len = strlen(name);
    const char *p = head;
    while ((p = strstr(p, name)) != NULL) {
        if (p == head || p[-1] == '\n') {
            const char *v = p + name_len;
            while (*v == ' ' || *v == ':' || *v == '\t') {
                v++;
            }
            size_t i = 0;
            while (v[i] != '\0' && v[i] != '\r' && v[i] != '\n' && i + 1 < cap) {
                out[i] = v[i];
                i++;
            }
            out[i] = '\0';
            return (i > 0);
        }
        p += name_len;
    }
    return false;
}

/**
 * 下载固件 -> 三重校验 -> 写备用分区 -> 激活（切启动分区）。
 *
 * ⚠️ 关于「先写分区还是先校验」：这里边下边写（写进**备用**分区），
 * 校验不过就 esp_ota_abort() 丢弃 —— 启动分区自始至终没被动过，
 * 设备照常跑旧版本，**不会变砖**。这与 doc/04「三重校验后才算成功」的要求
 * 等价：没有通过全部三项校验就不会走到激活那一步。
 */
static ota_result_t ota_download_write_and_activate(const char *bin_url,
                                                    const char *expect_sha256,
                                                    uint32_t expect_size,
                                                    const char *target_ver)
{
    char host[128];
    char path[384];
    uint16_t port = 80;
    if (!ota_parse_url(bin_url, host, sizeof(host), &port, path, sizeof(path))) {
        return OTA_ERR_HTTP;
    }

    WiFiClient client;
    if (!client.connect(host, port, 30000)) {
        return OTA_ERR_NET;
    }
    client.setTimeout(30);
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

    /* ---- 读响应头 ---- */
    char head[512];
    size_t head_len = 0;
    bool head_done = false;
    while (!head_done) {
        int c = client.read();
        if (c < 0) {
            if (!client.connected()) {
                break;
            }
            esp_task_wdt_reset();
            delay(2);
            continue;
        }
        if (head_len + 1 >= sizeof(head)) {
            client.stop();
            return OTA_ERR_HTTP;
        }
        head[head_len++] = (char)c;
        if (head_len >= 4 && memcmp(head + head_len - 4, "\r\n\r\n", 4) == 0) {
            head_done = true;
        }
    }
    head[head_len] = '\0';
    if (!head_done || strncmp(head, "HTTP/1.", 7) != 0) {
        client.stop();
        return OTA_ERR_HTTP;
    }
    int status = atoi(head + 9);
    if (status != 200) {
        client.stop();
        Serial.printf("[%s] 下载返回状态码 %d\n", TAG, status);
        return OTA_ERR_HTTP;
    }
    char len_text[24];
    if (!ota_header_value(head, "Content-Length", len_text, sizeof(len_text))) {
        client.stop();
        return OTA_ERR_HTTP;
    }
    long content_length = atol(len_text);
    if (content_length <= 0 || (uint32_t)content_length != expect_size) {
        client.stop();
        Serial.printf("[%s] 长度不符（头 %ld / 声明 %u）\n", TAG, content_length,
                      (unsigned)expect_size);
        return OTA_ERR_SIZE_MISMATCH;
    }

    /* ---- 目标分区（备用分区，绝不是正在跑的那个） ---- */
    /* 传 NULL = 从当前运行的分区往后找下一个可写分区（跑 app0 得 app1，反之亦然） */
    const esp_partition_t *target = esp_ota_get_next_update_partition(NULL);
    if (target == NULL || target->size < OTA_APP_PARTITION_SIZE) {
        client.stop();
        return OTA_ERR_INTERNAL;
    }
    /* 声明的大小超了分区容量 —— 不用下，直接拒 */
    if (expect_size > target->size) {
        client.stop();
        Serial.printf("[%s] 固件 %u 字节超分区 %u 字节\n", TAG, (unsigned)expect_size,
                      (unsigned)target->size);
        return OTA_ERR_TOO_LARGE;
    }

    esp_ota_handle_t handle = 0;
    if (esp_ota_begin(target, OTA_SIZE_UNKNOWN, &handle) != ESP_OK) {
        client.stop();
        return OTA_ERR_FLASH;
    }

    ota_sha_stream_t sha;
    if (ota_sha_stream_begin(&sha) != 0) {
        esp_ota_abort(handle);
        client.stop();
        return OTA_ERR_INTERNAL;
    }

    uint8_t buf[OTA_DOWNLOAD_CHUNK];
    uint32_t received = 0;
    bool magic_checked = false;
    ota_result_t err = OTA_OK;

    while (received < expect_size) {
        int avail = client.available();
        if (avail > 0) {
            int want = (avail > (int)sizeof(buf)) ? (int)sizeof(buf) : avail;
            size_t n = client.readBytes(buf, (size_t)want);
            if (n == 0) {
                err = OTA_ERR_HTTP;
                break;
            }
            /* 校验 1：首字节魔数 —— 不是 0xE9 就不是可用的 ESP32 应用镜像 */
            if (!magic_checked) {
                if (!ota_check_magic(buf[0])) {
                    Serial.printf("[%s] 首字节不是应用镜像（0x%02X）\n", TAG, buf[0]);
                    err = OTA_ERR_MAGIC;
                    break;
                }
                magic_checked = true;
            }
            if (ota_sha_stream_update(&sha, buf, n) != 0) {
                err = OTA_ERR_INTERNAL;
                break;
            }
            if (esp_ota_write(handle, buf, n) != ESP_OK) {
                /* 真实 API 返回 esp_err_t（不是字节数），成功即 ESP_OK */
                err = OTA_ERR_FLASH;
                break;
            }
            received += (uint32_t)n;
            ota_set_progress((int)((uint64_t)received * 100ull / (uint64_t)expect_size));
            esp_task_wdt_reset(); /* 每 4KB 喂一次看门狗 */
        } else {
            if (!client.connected()) {
                err = OTA_ERR_HTTP;
                break;
            }
            if (g_cancel) {
                err = OTA_ERR_INTERNAL;
                break;
            }
            esp_task_wdt_reset();
            delay(2);
        }
    }
    client.stop();

    if (err != OTA_OK) {
        esp_ota_abort(handle);
        return err;
    }

    /* 校验 2：字节数与网关声明一致，且不超分区容量 */
    ota_result_t sz = ota_check_size(received, expect_size, target->size);
    if (sz != OTA_OK) {
        esp_ota_abort(handle);
        return sz;
    }

    /* 校验 3：流式 SHA-256 与网关声明一致 */
    char hex[65];
    if (ota_sha_stream_finish(&sha, hex) != 0) {
        esp_ota_abort(handle);
        return OTA_ERR_INTERNAL;
    }
    if (!ota_sha_equal(hex, expect_sha256)) {
        Serial.printf("[%s] 摘要不一致（算到 %s）\n", TAG, hex);
        esp_ota_abort(handle);
        return OTA_ERR_SHA256;
    }

    /* ---- 6. NVS 标记「待验证升级」 ----
     * 写在 esp_ota_end() **之前**：esp_ota_end() 只做「校验镜像 + 结束写入」，
     * **不切启动分区**；真正切分区的是下面的 esp_ota_set_boot_partition()。
     * 标记先写，是为了「已写入备用分区但还没切换」时掉电也能在下次启动识别出来；
     * 若反过来先切换后写标记，设备会带着新固件启动却没有待验证标记 —— 自检形同虚设。
     * 先写标记再切换启动分区，掉电最坏也只是白标记一次
     * （旧固件启动时发现标记的目标分区不是自己，会自己清掉）。
     */
    char tag[12];
    ota_partition_tag(target, tag, sizeof(tag));
    ota_nvs_set(NVS_KEY_PENDING, target_ver);
    ota_nvs_set(NVS_KEY_TARGET, tag);
    ota_nvs_set_int(NVS_KEY_TRIES, 0);

    /* ---- 5. 写完并激活：esp_ota_end() 结束写入（不切分区），
     *      esp_ota_set_boot_partition() 才把启动分区切到新分区 ---- */
    if (esp_ota_end(handle) != ESP_OK) {
        Serial.printf("[%s] 固件写入收尾失败\n", TAG);
        return OTA_ERR_FLASH;
    }
    esp_ota_set_boot_partition(target);
    Serial.printf("[%s] 新固件已写入 %s，待重启\n", TAG, tag);
    return OTA_OK;
}

/* ===================== 主流程 ===================== */

/**
 * 跑一次完整升级。返回 OTA_OK 表示已写好并准备重启；
 * 其余返回码都是失败原因（设备仍可正常启动）。
 */
static ota_result_t ota_run(void)
{
    /* ---- 1. 前置采样 ---- */
    ota_set_state(OTA_STATE_PRECHECK, OTA_OK, "正在检查升级条件…");
    int avg_mv = 0;
    if (!ota_sample_battery(&avg_mv)) {
        ota_set_state(OTA_STATE_FAILED, OTA_ERR_PRECONDITION,
                      ota_result_text(OTA_ERR_PRECONDITION));
        return OTA_ERR_PRECONDITION;
    }
    int rssi = (int)net_wifi_get_rssi();
    if (!ota_precondition_ok(avg_mv, rssi)) {
        Serial.printf("[%s] 前置不满足：%dmV / %ddBm\n", TAG, avg_mv, rssi);
        ota_set_state(OTA_STATE_FAILED, OTA_ERR_PRECONDITION,
                      ota_result_text(OTA_ERR_PRECONDITION));
        return OTA_ERR_PRECONDITION;
    }

    /* ---- 2. 查更新 ---- */
    ota_set_state(OTA_STATE_CHECKING, OTA_OK, "正在检查更新…");
    nr_ota_t info;
    memset(&info, 0, sizeof(info));
    if (!net_ota_check(&info)) {
        ota_set_state(OTA_STATE_FAILED, OTA_ERR_NET, ota_result_text(OTA_ERR_NET));
        return OTA_ERR_NET;
    }

    ota_lock();
    g_status.has_update = (info.has_update != 0);
    g_status.force_update = (info.force_update != 0);
    ota_copy(g_status.latest_ver, sizeof(g_status.latest_ver), info.latest_ver);
    ota_copy(g_status.changelog, sizeof(g_status.changelog), info.changelog);
    g_status.seq++;
    ota_unlock();

    if (!info.has_update) {
        ota_set_state(OTA_STATE_DONE, OTA_ERR_NO_UPDATE, "当前已是最新版本");
        return OTA_ERR_NO_UPDATE;
    }
    /* 网关说有新版本，但版本号没往前走 —— 以本地版本为准，不折腾设备 */
    if (!ota_should_update(NET_FW_VERSION, info.latest_ver)) {
        ota_set_state(OTA_STATE_DONE, OTA_ERR_NO_UPDATE, "当前已是最新版本");
        return OTA_ERR_NO_UPDATE;
    }
    if (info.size == 0u || info.sha256[0] == '\0' || info.bin_url[0] == '\0') {
        ota_set_state(OTA_STATE_FAILED, OTA_ERR_INTERNAL, ota_result_text(OTA_ERR_INTERNAL));
        return OTA_ERR_INTERNAL;
    }

    /* ---- 3~5. 下载 -> 三重校验 -> 写备用分区 -> 激活 ---- */
    ota_set_state(OTA_STATE_DOWNLOADING, OTA_OK, "正在下载新版本…");
    ota_set_progress(0);

    ota_result_t rc = ota_download_write_and_activate(info.bin_url, info.sha256, info.size,
                                                       info.latest_ver);
    if (rc != OTA_OK) {
        ota_set_state(OTA_STATE_FAILED, rc, ota_result_text(rc));
        return rc;
    }

    ota_set_state(OTA_STATE_REBOOTING, OTA_OK, "正在重启完成升级…");
    ota_lock();
    g_status.reboot_pending = true;
    g_status.progress_pct = 100;
    g_status.seq++;
    ota_unlock();
    delay(300);
    esp_restart();
    return OTA_OK; /* 不会到这里 */
}

bool ota_start(void)
{
    if (g_running) {
        return false;
    }
    ota_init();
    g_running = true;
    g_cancel = false;

    ota_lock();
    g_status.state = OTA_STATE_PRECHECK;
    g_status.result = OTA_OK;
    g_status.progress_pct = 0;
    g_status.has_update = false;
    g_status.force_update = false;
    g_status.reboot_pending = false;
    ota_copy(g_status.current_ver, sizeof(g_status.current_ver), NET_FW_VERSION);
    g_status.latest_ver[0] = '\0';
    g_status.changelog[0] = '\0';
    g_status.message[0] = '\0';
    g_status.seq++;
    ota_unlock();

    ota_result_t rc = ota_run();
    g_running = false;
    return (rc == OTA_OK);
}

/* ===================== 7. 重启后自检与回滚 ===================== */

/**
 * 把启动分区指回旧分区并重启。
 * 不依赖引导器的自动回滚 —— 就算引导器那侧没开回滚，设备也能自己退回去。
 */
static void ota_revert_to_previous(void)
{
    const esp_partition_t *prev = esp_ota_get_next_update_partition(NULL);
    if (prev != NULL) {
        esp_ota_set_boot_partition(prev);
        Serial.printf("[%s] 启动分区已指回上一版\n", TAG);
    }
    delay(100);
    esp_restart();
}

/**
 * 升级后的启动自检（**在 setup() 里阻塞执行**）。
 *
 * 为什么要放在 setup() 里阻塞跑：只有 setup() 没返回，
 * 才轮不到「Arduino 核心在 setup() 之后自动 mark valid」那条路径插手。
 * 自检期间 Arduino 的 loop() 还没起来，但看门狗由本函数持续喂，
 * 界面核（Core 1）照常跑，task_ui_fsm 的心跳计数照常增长。
 *
 * @param peripherals_ok 外设是否全部初始化成功（由 setup() 汇总后传入）。
 * @return true = 本次启动已确认有效（或本来就不是待验证状态）。
 */
bool ota_boot_selfcheck_run(bool peripherals_ok)
{
    ota_init();

    char pending[24];
    ota_nvs_get(NVS_KEY_PENDING, pending, sizeof(pending), "");
    if (pending[0] == '\0') {
        return true; /* 不是待验证升级 —— 什么都不用做 */
    }

    char target_tag[12];
    ota_nvs_get(NVS_KEY_TARGET, target_tag, sizeof(target_tag), "");
    char running_tag[12];
    ota_partition_tag(esp_ota_get_running_partition(), running_tag, sizeof(running_tag));

    if (target_tag[0] != '\0' && strcmp(target_tag, running_tag) != 0) {
        /* 跑的不是目标分区 -> 说明已经退回旧版本了（回滚完成），清标记收工 */
        Serial.printf("[%s] 当前跑的是旧版本，升级已退回\n", TAG);
        ota_nvs_clear_pending();
        ota_set_state(OTA_STATE_DONE, OTA_ERR_ROLLBACK, "新版本自检未通过，已退回原版本");
        return true;
    }

    /* ---- 我们是待验证的新固件 ---- */
    int tries = ota_nvs_get_int(NVS_KEY_TRIES, 0) + 1;
    ota_nvs_set_int(NVS_KEY_TRIES, tries);
    Serial.printf("[%s] 待验证升级（第 %d 次启动）\n", TAG, tries);

    if (tries > (int)OTA_SELFCHECK_MAX_TRIES) {
        /* 连续启动都过不了自检（多半是起来就崩）-> 直接退回旧分区，绝不刷成砖 */
        Serial.printf("[%s] 连续自检失败，直接退回上一版\n", TAG);
        ota_revert_to_previous();
        return false; /* 不会到这里 */
    }

    /* ---- 健康自检：稳定 20 秒 + UI 心跳 >= 50 + 外设初始化成功 ---- */
    uint32_t start = millis();
    uint32_t beats0 = g_ui_heartbeat;
    bool passed = peripherals_ok;
    while ((millis() - start) < OTA_SELFCHECK_STABLE_MS) {
        if (!peripherals_ok) {
            break;
        }
        esp_task_wdt_reset();
        delay(50);
    }
    uint32_t beats = g_ui_heartbeat - beats0;
    if (beats < OTA_SELFCHECK_MIN_BEATS) {
        Serial.printf("[%s] 心跳不足（%u < %u）\n", TAG, (unsigned)beats,
                      (unsigned)OTA_SELFCHECK_MIN_BEATS);
        passed = false;
    }
    if (!passed) {
        g_selfcheck_passed = false;
        ota_set_state(OTA_STATE_FAILED, OTA_ERR_ROLLBACK, ota_result_text(OTA_ERR_ROLLBACK));
        Serial.printf("[%s] 自检未通过，退回上一版\n", TAG);
        ota_revert_to_previous();
        return false; /* 不会到这里 */
    }

    /* ---- 通过：显式标记有效（这一步之前绝不 mark） ---- */
    esp_ota_mark_app_valid_cancel_rollback();
    ota_nvs_clear_pending();
    g_selfcheck_passed = true;
    ota_set_state(OTA_STATE_DONE, OTA_OK, "升级完成");
    Serial.printf("[%s] 自检通过，已确认新版本\n", TAG);
    return true;
}
