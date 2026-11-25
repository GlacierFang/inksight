/**
 * test_net_req.c —— 设备端请求组装 / 响应解析的主机侧单元测试
 *
 * 与 `test_net_client.c` 同构：**纯主机编译**（gcc + mbedtls），不碰 PlatformIO。
 * 固件编译仍在 GitHub Actions 上做（硬线），本测试只验证纯 C 的协议形状逻辑。
 *
 * 覆盖：
 *   1. URL 拼装（挂载前缀 / OTA query）；
 *   2. `study/solve` 明文组装 —— 三个注入字段是否自动带上（doc/05 §5.1）；
 *   3. `study/voice` 明文组装；
 *   4. `pair` 明文组装；
 *   5. JSON 取值（string / int / bool，含转义与 \uXXXX）；
 *   6. HTTP 响应解析（Content-Length 与 chunked 两种收包）。
 */

#include <stdio.h>
#include <string.h>
#include <stdlib.h>

#include "net_req.h"
#include "net_client.h"

static int g_pass = 0;
static int g_fail = 0;

#define CHECK(cond, msg)                                                      \
    do {                                                                      \
        if (cond) {                                                           \
            g_pass++;                                                         \
        } else {                                                              \
            g_fail++;                                                         \
            printf("  FAIL: %s (%s:%d)\n", (msg), __FILE__, __LINE__);        \
        }                                                                     \
    } while (0)

#define CHECK_STR(actual, expect, msg)                                        \
    do {                                                                      \
        const char *a_ = (actual);                                            \
        if (a_ != NULL && strcmp(a_, (expect)) == 0) {                        \
            g_pass++;                                                         \
        } else {                                                              \
            g_fail++;                                                         \
            printf("  FAIL: %s\n    期望: %s\n    实际: %s\n", (msg),         \
                   (expect), a_ ? a_ : "(null)");                             \
        }                                                                     \
    } while (0)

static void test_url(void)
{
    printf("[1] URL 拼装\n");
    char buf[160];

    int need = nr_build_url("ruohan.site", "/abbot", NR_PATH_SOLVE, NULL, 0);
    CHECK(need > 0, "需要长度应为正");

    int rc = nr_build_url("ruohan.site", "/abbot", NR_PATH_SOLVE, buf, sizeof buf);
    CHECK(rc == need, "返回长度应与预测量一致");
    CHECK_STR(buf, "http://ruohan.site/abbot/api/v1/study/solve", "带挂载前缀的 URL");

    rc = nr_build_url("ruohan.site", "", NR_PATH_PING, buf, sizeof buf);
    CHECK_STR(buf, "http://ruohan.site/api/v1/ping", "无挂载前缀的 URL");

    rc = nr_build_url("ruohan.site", "/abbot", NR_PATH_OTA_CHECK, buf, sizeof buf);
    CHECK_STR(buf, "http://ruohan.site/abbot/api/v1/ota/check", "OTA 基础路径");

    rc = nr_build_ota_url("ruohan.site", "/abbot", "esp32s3_a1b2", "1.0.3", buf, sizeof buf);
    CHECK(rc > 0, "OTA URL 应拼出");
    CHECK_STR(buf, "http://ruohan.site/abbot/api/v1/ota/check?device_id=esp32s3_a1b2&current_ver=1.0.3",
              "OTA URL 带 query");

    /* 缓冲区不足必须报错而不是截断 */
    char small[10];
    rc = nr_build_url("ruohan.site", "/abbot", NR_PATH_SOLVE, small, sizeof small);
    CHECK(rc == -2, "缓冲区不足应返回 -2");
}

static void test_solve_plain(void)
{
    printf("[2] study/solve 明文组装（含三字段自动注入）\n");
    nr_profile_t prof;
    memset(&prof, 0, sizeof prof);
    snprintf(prof.edu_stage, sizeof prof.edu_stage, "JUNIOR");
    prof.enrollment_year = 2026;
    prof.device_remaining_quota = 8;

    const uint8_t jpg[] = { 0xFF, 0xD8, 0xFF, 0xE0, 0x01, 0x02, 0x03 };
    const uint8_t pcm[] = { 0x01, 0x02, 0x03, 0x04, 0x05 };

    int need = nr_build_solve_plain(&prof, "sess_1795412000", jpg, sizeof jpg,
                                    pcm, sizeof pcm, 1, NULL, 0);
    CHECK(need > 0, "需要长度应为正");

    char *buf = (char *)malloc((size_t)need);
    CHECK(buf != NULL, "分配缓冲区");
    if (buf == NULL) {
        return;
    }
    int rc = nr_build_solve_plain(&prof, "sess_1795412000", jpg, sizeof jpg,
                                  pcm, sizeof pcm, 1, buf, (size_t)need);
    CHECK(rc == need, "返回长度应与预测量一致");

    printf("    明文: %s\n", buf);

    char val[64];
    CHECK(nr_json_get_string(buf, strlen(buf), "solve_session_id", val, sizeof val) > 0,
          "solve_session_id 可取");
    CHECK_STR(val, "sess_1795412000", "solve_session_id 值");

    /* edu_stage / enrollment_year / device_remaining_quota 必须由 net 层自动注入 */
    CHECK(nr_json_get_string(buf, strlen(buf), "edu_stage", val, sizeof val) > 0,
          "edu_stage 已注入");
    CHECK_STR(val, "JUNIOR", "edu_stage 值");
    CHECK(nr_json_get_int(buf, strlen(buf), "enrollment_year", -1) == 2026,
          "enrollment_year 已注入");
    CHECK(nr_json_get_int(buf, strlen(buf), "device_remaining_quota", -1) == 8,
          "device_remaining_quota 已注入");
    CHECK(nr_json_get_int(buf, strlen(buf), "hint_level", -1) == 1, "hint_level 已写入");

    /* image_b64 = Base64(FFD8FFE0010203) */
    CHECK(nr_json_get_string(buf, strlen(buf), "image_b64", val, sizeof val) > 0,
          "image_b64 存在");
    CHECK_STR(val, "/9j/4AECAw==", "image_b64 是标准 Base64");

    /* 分块编码必须与一次性编码逐字节一致（sb_base64 的正确性由这条兜住） */
    {
        char one_shot[64];
        size_t one_len = 0;
        CHECK(nc_base64_encode(jpg, sizeof jpg, one_shot, sizeof one_shot, &one_len) == NC_OK,
              "一次性 Base64 可算");
        char got[64];
        size_t got_len = 0;
        CHECK(nr_json_get_string(buf, strlen(buf), "image_b64", got, sizeof got) > 0,
              "image_b64 可取");
        got_len = strlen(got);
        CHECK(got_len == one_len && memcmp(got, one_shot, one_len) == 0,
              "分块 Base64 与一次性编码逐字节一致");
    }

    /* pcm_base64 = Base64(0102030405) */
    CHECK(nr_json_get_string(buf, strlen(buf), "pcm_base64", val, sizeof val) > 0,
          "pcm_base64 存在");
    CHECK_STR(val, "AQIDBAU=", "pcm_base64 是标准 Base64");

    free(buf);
}

static void test_solve_plain_no_pcm(void)
{
    printf("[3] study/solve 纯拍照（无 pcm）\n");
    nr_profile_t prof;
    memset(&prof, 0, sizeof prof);
    snprintf(prof.edu_stage, sizeof prof.edu_stage, "PRIMARY");
    prof.enrollment_year = 2025;
    prof.device_remaining_quota = 0;

    const uint8_t jpg[] = { 0xFF, 0xD8 };
    int need = nr_build_solve_plain(&prof, "s1", jpg, sizeof jpg, NULL, 0, 0, NULL, 0);
    char *buf = (char *)malloc((size_t)need);
    if (buf == NULL) {
        CHECK(0, "分配缓冲区");
        return;
    }
    nr_build_solve_plain(&prof, "s1", jpg, sizeof jpg, NULL, 0, 0, buf, (size_t)need);
    CHECK(strstr(buf, "pcm_base64") == NULL, "无音频时不应出现 pcm_base64");
    CHECK(strstr(buf, "\"edu_stage\":\"PRIMARY\"") != NULL, "edu_stage 仍然注入");
    CHECK(nr_json_get_int(buf, strlen(buf), "hint_level", -1) == 0, "hint_level=0");
    free(buf);
}

static void test_voice_plain(void)
{
    printf("[4] study/voice 明文组装\n");
    nr_profile_t prof;
    memset(&prof, 0, sizeof prof);
    snprintf(prof.edu_stage, sizeof prof.edu_stage, "SENIOR");
    prof.enrollment_year = 2024;
    prof.device_remaining_quota = 3;

    const uint8_t pcm[] = { 0x0A, 0x0B, 0x0C };
    int need = nr_build_voice_plain(&prof, "READER", "m7_geo", "card_03",
                                    pcm, sizeof pcm, "2026-11-25T09:00:00", NULL, 0);
    char *buf = (char *)malloc((size_t)need);
    if (buf == NULL) {
        CHECK(0, "分配缓冲区");
        return;
    }
    nr_build_voice_plain(&prof, "READER", "m7_geo", "card_03",
                         pcm, sizeof pcm, "2026-11-25T09:00:00", buf, (size_t)need);
    printf("    明文: %s\n", buf);

    char val[64];
    CHECK(nr_json_get_string(buf, strlen(buf), "context_page", val, sizeof val) > 0,
          "context_page 可取");
    CHECK_STR(val, "READER", "context_page 值");
    CHECK(nr_json_get_string(buf, strlen(buf), "pack_id", val, sizeof val) > 0,
          "pack_id 可取");
    CHECK_STR(val, "m7_geo", "pack_id 值");
    CHECK(nr_json_get_string(buf, strlen(buf), "card_id", val, sizeof val) > 0,
          "card_id 可取");
    CHECK_STR(val, "card_03", "card_id 值");
    CHECK(nr_json_get_string(buf, strlen(buf), "pcm_base64", val, sizeof val) > 0,
          "pcm_base64 存在");
    CHECK(nr_json_get_int(buf, strlen(buf), "enrollment_year", -1) == 2024,
          "enrollment_year 已注入");
    free(buf);
}

static void test_pair_plain(void)
{
    printf("[5] pair 明文组装\n");
    int need = nr_build_pair_plain("esp32s3_a1b2c3d4", "123456", NULL, 0);
    char *buf = (char *)malloc((size_t)need);
    if (buf == NULL) {
        CHECK(0, "分配缓冲区");
        return;
    }
    nr_build_pair_plain("esp32s3_a1b2c3d4", "123456", buf, (size_t)need);
    CHECK_STR(buf, "{\"device_id\":\"esp32s3_a1b2c3d4\",\"pair_code\":\"123456\"}",
              "pair 明文");
    free(buf);
}

static void test_json_get(void)
{
    printf("[6] JSON 取值\n");
    const char *json = "{\"status\":\"SOLVED\",\"has_audio\":true,\"quota\":9,"
                       "\"md\":\"第一行\\n第二行\",\"esc\":\"引号\\\"在内\","
                       "\"cn\":\"\\u4e2d\\u6587\"}";
    size_t len = strlen(json);

    char val[128];
    CHECK(nr_json_get_string(json, len, "status", val, sizeof val) > 0, "status 可取");
    CHECK_STR(val, "SOLVED", "status 值");
    CHECK(nr_json_get_bool(json, len, "has_audio", false) == true, "has_audio 为真");
    CHECK(nr_json_get_int(json, len, "quota", -1) == 9, "quota 为 9");
    CHECK(nr_json_get_string(json, len, "md", val, sizeof val) > 0, "md 可取");
    CHECK_STR(val, "第一行\n第二行", "\\n 转义被解开");
    CHECK(nr_json_get_string(json, len, "esc", val, sizeof val) > 0, "esc 可取");
    CHECK_STR(val, "引号\"在内", "\\\" 转义被解开");
    CHECK(nr_json_get_string(json, len, "cn", val, sizeof val) > 0, "cn 可取");
    CHECK_STR(val, "中文", "\\uXXXX 转成 UTF-8");
    CHECK(nr_json_get_int(json, len, "missing", 42) == 42, "缺字段返回兜底值");
    CHECK(nr_json_get_bool(json, len, "missing", true) == true, "缺字段布尔兜底");
}

static void test_http_response(void)
{
    printf("[7] HTTP 响应解析\n");

    /* Content-Length 收包 */
    {
        char raw[] =
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: application/json\r\n"
            "X-Resp-Signature: abc123\r\n"
            "Content-Length: 10\r\n"
            "\r\n"
            "{\"code\":0}\r\n";
        nr_http_response_t r;
        int rc = nr_parse_http_response(raw, strlen(raw), &r);
        CHECK(rc == 0, "Content-Length 响应解析成功");
        CHECK(r.status_code == 200, "状态码 200");
        CHECK_STR(r.resp_signature, "abc123", "取出 X-Resp-Signature");
        CHECK(r.body_len == 10, "按 Content-Length 截断");
        CHECK(memcmp(r.body, "{\"code\":0}", 10) == 0, "响应体内容正确");
        CHECK(r.chunked == false, "不是 chunked");
    }

    /* chunked 收包 */
    {
        char raw[] =
            "HTTP/1.1 200 OK\r\n"
            "Transfer-Encoding: chunked\r\n"
            "X-Resp-Signature: deadbeef\r\n"
            "\r\n"
            "6\r\n{\"a\":1\r\n"
            "2\r\nb}\r\n"
            "0\r\n\r\n";
        nr_http_response_t r;
        int rc = nr_parse_http_response(raw, strlen(raw), &r);
        CHECK(rc == 0, "chunked 响应解析成功");
        CHECK(r.chunked == true, "标记为 chunked");
        CHECK_STR(r.resp_signature, "deadbeef", "chunked 也能取出签名头");
        CHECK(r.body_len == 8, "chunked 解码后长度正确");
        CHECK(memcmp(r.body, "{\"a\":1b}", 8) == 0, "chunked 解码内容正确");
    }

    /* 坏数据必须报错而不是崩 */
    {
        char raw[] = "NOT-HTTP\r\n\r\nbody";
        nr_http_response_t r;
        CHECK(nr_parse_http_response(raw, strlen(raw), &r) < 0, "非 HTTP 响应报错");
    }
}

int main(void)
{
    printf("=== abbot 设备端请求层测试（net_req）===\n");
    test_url();
    test_solve_plain();
    test_solve_plain_no_pcm();
    test_voice_plain();
    test_pair_plain();
    test_json_get();
    test_http_response();
    printf("\n=== %d PASS / %d FAIL ===\n", g_pass, g_fail);
    return g_fail == 0 ? 0 : 1;
}
