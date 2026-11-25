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

static void test_register_plain(void)
{
    printf("[5b] register 明文组装（自动登记）\n");
    int need = nr_build_register_plain("esp32s3_a1b2c3d4", "1.0.0", NULL, 0);
    char *buf = (char *)malloc((size_t)need);
    if (buf == NULL) {
        CHECK(0, "分配缓冲区");
        return;
    }
    nr_build_register_plain("esp32s3_a1b2c3d4", "1.0.0", buf, (size_t)need);
    CHECK_STR(buf, "{\"device_id\":\"esp32s3_a1b2c3d4\",\"fw_version\":\"1.0.0\"}",
              "register 明文");
    free(buf);

    /* fw_version 传 NULL 时不该带上这个字段 */
    need = nr_build_register_plain("esp32s3_a1b2c3d4", NULL, NULL, 0);
    buf = (char *)malloc((size_t)need);
    if (buf == NULL) {
        CHECK(0, "分配缓冲区（无版本号）");
        return;
    }
    nr_build_register_plain("esp32s3_a1b2c3d4", NULL, buf, (size_t)need);
    CHECK_STR(buf, "{\"device_id\":\"esp32s3_a1b2c3d4\"}", "register 明文（无 fw_version）");
    free(buf);

    /* device_id 为空 -> 拒绝 */
    CHECK(nr_build_register_plain("", "1.0.0", NULL, 0) < 0, "空 device_id 被拒");
}

static void test_bootstrap_plain(void)
{
    printf("[5c] bootstrap 明文组装（心跳，doc/05 §5.2）\n");

    /* 不带城市：不应该出现 city 字段（服务端不会因此清掉已有城市） */
    int need = nr_build_bootstrap_plain("esp32s3_a1b2c3d4", "1.0.0", 80, 0,
                                        "[]", "[]", NULL, NULL, 0);
    CHECK(need > 0, "能算出所需长度");
    char *buf = (char *)malloc((size_t)need);
    if (buf == NULL) {
        CHECK(0, "分配缓冲区");
        return;
    }
    nr_build_bootstrap_plain("esp32s3_a1b2c3d4", "1.0.0", 80, 0,
                             "[]", "[]", NULL, buf, (size_t)need);
    CHECK_STR(buf,
              "{\"device_id\":\"esp32s3_a1b2c3d4\",\"fw_version\":\"1.0.0\","
              "\"battery_pct\":80,\"schedule_updated_at\":0,"
              "\"local_packs\":[],\"local_books\":[]}",
              "bootstrap 明文（无城市）");
    free(buf);

    /* 带城市：城市名要在心跳明文里（服务端解密后读 payload["city"]） */
    need = nr_build_bootstrap_plain("esp32s3_a1b2c3d4", "1.0.0", 80, 0,
                                    "[]", "[]", "Shanghai", NULL, 0);
    buf = (char *)malloc((size_t)need);
    if (buf == NULL) {
        CHECK(0, "分配缓冲区（带城市）");
        return;
    }
    nr_build_bootstrap_plain("esp32s3_a1b2c3d4", "1.0.0", 80, 0,
                             "[]", "[]", "Shanghai", buf, (size_t)need);
    CHECK(strstr(buf, "\"city\":\"Shanghai\"") != NULL, "心跳明文里应带上报城市");
    free(buf);

    /* 城市名为空串 = 不带这个字段 */
    need = nr_build_bootstrap_plain("esp32s3_a1b2c3d4", "1.0.0", 80, 0,
                                    "[]", "[]", "", NULL, 0);
    buf = (char *)malloc((size_t)need);
    if (buf == NULL) {
        CHECK(0, "分配缓冲区（空城市）");
        return;
    }
    nr_build_bootstrap_plain("esp32s3_a1b2c3d4", "1.0.0", 80, 0,
                             "[]", "[]", "", buf, (size_t)need);
    CHECK(strstr(buf, "city") == NULL, "空城市不应带上 city 字段");
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

static void test_solve_result(void)
{
    printf("[8] 拍题应答解析\n");
    /* 云端真实应答形状（字段名见 doc/05 §5） */
    const char *json =
        "{\"status\":\"SOLVED\","
        "\"speech_text\":\"这道题用勾股定理\","
        "\"display_markdown\":\"1. 先圈出已知条件\\n2. 找出联系\\n3. 往下推\","
        "\"feynman_question\":\"为什么要这样想？\","
        "\"has_audio\":true,\"tts_cache_key\":\"abc\","
        "\"effective_remaining_quota\":9}";
    nr_solve_result_t r;
    int rc = nr_parse_solve_result(json, strlen(json), &r);
    CHECK(rc == 0, "拍题应答解析成功");
    CHECK(strcmp(r.status, "SOLVED") == 0, "status 正确");
    CHECK(strcmp(r.display_markdown, "1. 先圈出已知条件\n2. 找出联系\n3. 往下推") == 0,
          "display_markdown 正确（转义已解开）");
    CHECK(strcmp(r.feynman_question, "为什么要这样想？") == 0, "feynman_question 正确");
    CHECK(strcmp(r.speech_text, "这道题用勾股定理") == 0, "speech_text 正确");
    CHECK(r.has_audio == true, "has_audio 为真");
    CHECK(r.effective_remaining_quota == 9, "effective_remaining_quota 正确");

    /* 字段缺失时不得崩，应给空串 / 默认值 */
    const char *poor = "{\"status\":\"NEED_DRAFT\"}";
    rc = nr_parse_solve_result(poor, strlen(poor), &r);
    CHECK(rc == 0, "字段缺失时仍解析成功");
    CHECK(r.display_markdown[0] == '\0', "缺失字段为空串");
    CHECK(r.has_audio == false, "缺失 has_audio 为假");
    CHECK(r.effective_remaining_quota == -1, "缺失配额为 -1");

    /* 超长正文必须截断而不是失败 */
    {
        char big[4096];
        size_t n = 0;
        n += (size_t)snprintf(big + n, sizeof(big) - n, "{\"display_markdown\":\"");
        for (size_t i = 0; i < 3000; i++) {
            big[n++] = 'A';
        }
        big[n++] = '"';
        big[n++] = '}';
        big[n] = '\0';
        rc = nr_parse_solve_result(big, n, &r);
        CHECK(rc == 0, "超长正文解析成功");
        CHECK(strlen(r.display_markdown) == sizeof(r.display_markdown) - 1, "超长正文被截断");
    }
}

static void test_pending_assets(void)
{
    printf("[9] 待同步资产解析与路径映射\n");
    /* 心跳应答的真实形状（doc/05 §5） */
    const char *json =
        "{\"weather\":{},"
        "\"schedule_download\":{\"rel_path\":\"schedules/latest.json\","
        "\"updated_at\":1795400000,\"token\":\"tk-sched\"},"
        "\"pending_packs\":[{\"pack_id\":\"m7_geo\","
        "\"rel_path\":\"packs/m7_geo/cards.enc\",\"token\":\"tk-p1\"}],"
        "\"pending_books\":[{\"book_id\":\"xw\","
        "\"rel_path\":\"books/xw.txt\",\"token\":\"tk-b1\"}],"
        "\"effective_remaining_quota\":9,\"server_time\":1795412000}";
    size_t len = strlen(json);

    /* 数组遍历 */
    CHECK(nr_array_count(json, len, "pending_packs") == 1, "pending_packs 元素数正确");
    CHECK(nr_array_count(json, len, "pending_books") == 1, "pending_books 元素数正确");
    CHECK(nr_array_count(json, len, "nonexistent") == -1, "不存在的键返回 -1");

    /* 取数组元素 */
    const char *item = NULL;
    size_t item_len = 0;
    CHECK(nr_array_item(json, len, "pending_packs", 0, &item, &item_len) == 0, "取到 pending_packs[0]");
    char pid[16];
    CHECK(nr_json_get_string(item, item_len, "pack_id", pid, sizeof(pid)) > 0, "pack_id 存在");

    /* 待同步资产解析 */
    nr_pending_t items[NR_MAX_PENDING];
    int count = 0;
    CHECK(nr_parse_pending(json, len, "pending_packs", "pack_id", items, NR_MAX_PENDING, &count) == 0,
          "pending_packs 解析成功");
    CHECK(count == 1, "pending_packs 解析出 1 条");
    CHECK(strcmp(items[0].id, "m7_geo") == 0, "pack_id 正确");
    CHECK(strcmp(items[0].rel_path, "packs/m7_geo/cards.enc") == 0, "rel_path 正确");
    CHECK(strcmp(items[0].token, "tk-p1") == 0, "token 正确");

    count = 0;
    CHECK(nr_parse_pending(json, len, "pending_books", "book_id", items, NR_MAX_PENDING, &count) == 0,
          "pending_books 解析成功");
    CHECK(count == 1, "pending_books 解析出 1 条");
    CHECK(strcmp(items[0].id, "xw") == 0, "book_id 正确");

    /* 课表是对象 —— 平铺查找会拿错 pending_packs 里的 rel_path，所以必须按对象取 */
    const char *obj = NULL;
    size_t obj_len = 0;
    CHECK(nr_json_get_object(json, len, "schedule_download", &obj, &obj_len) == 0,
          "取到 schedule_download 对象");
    char rel[96];
    char token[256];
    CHECK(nr_json_get_string(obj, obj_len, "rel_path", rel, sizeof(rel)) > 0, "对象内 rel_path 正确");
    CHECK(strcmp(rel, "schedules/latest.json") == 0, "课表 rel_path 正确");
    CHECK(nr_json_get_string(obj, obj_len, "token", token, sizeof(token)) > 0, "对象内 token 存在");
    CHECK(strcmp(token, "tk-sched") == 0, "课表 token 正确");

    /* 服务端相对路径 -> 设备 TF 卡路径 */
    char dev[96];
    CHECK(nr_map_rel_path("packs/m7_geo/cards.enc", dev, sizeof(dev)) == 0, "学科包路径映射成功");
    CHECK(strcmp(dev, "/subjects/m7_geo/cards.enc") == 0, "学科包映射到 /subjects");
    CHECK(nr_map_rel_path("books/xw.txt", dev, sizeof(dev)) == 0, "电子书路径映射成功");
    CHECK(strcmp(dev, "/books/xw.txt") == 0, "电子书映射到 /books");
    CHECK(nr_map_rel_path("schedules/latest.json", dev, sizeof(dev)) == 0, "课表路径映射成功");
    CHECK(strcmp(dev, "/config/schedule.json") == 0, "课表映射到 /config/schedule.json");
    CHECK(nr_map_rel_path("unknown/x.bin", dev, sizeof(dev)) == -1, "未知前缀映射失败");

    /* 下载链接拼装 */
    char url[320];
    int n = nr_build_download_url("http://host/abbot", "tk-p1", url, sizeof(url));
    CHECK(n > 0, "下载链接拼装成功");
    CHECK(strcmp(url, "http://host/abbot/api/v1/content/download?token=tk-p1") == 0, "下载链接正确");
    CHECK(nr_build_download_url("http://host/abbot", "tk", url, 8) == -1, "链接被截断时返回 -1");
}

static void test_ota_reply(void)
{
    printf("[10] OTA 检查应答解析\n");
    const char *json =
        "{\"has_update\":true,\"latest_ver\":\"1.0.1\","
        "\"sha256\":\"00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff\","
        "\"size\":826368,\"bin_url\":\"http://host/abbot/api/v1/content/download?token=tk\"}";
    nr_ota_t ota;
    CHECK(nr_parse_ota(json, strlen(json), &ota) == 0, "OTA 应答解析成功");
    CHECK(ota.has_update == true, "has_update 为真");
    CHECK(strcmp(ota.latest_ver, "1.0.1") == 0, "latest_ver 正确");
    CHECK(strlen(ota.sha256) == 64, "sha256 长度正确");
    CHECK(ota.size == 826368, "size 正确");
    CHECK(strstr(ota.bin_url, "content/download") != NULL, "bin_url 正确");

    const char *none = "{\"has_update\":false}";
    CHECK(nr_parse_ota(none, strlen(none), &ota) == 0, "无更新时解析成功");
    CHECK(ota.has_update == false, "has_update 为假");
    CHECK(ota.size == 0, "无更新时 size 为 0");
}

int main(void)
{
    printf("=== abbot 设备端请求层测试（net_req）===\n");
    test_url();
    test_solve_plain();
    test_solve_plain_no_pcm();
    test_voice_plain();
    test_pair_plain();
    test_register_plain();
    test_bootstrap_plain();
    test_json_get();
    test_http_response();
    test_solve_result();
    test_pending_assets();
    test_ota_reply();
    printf("\n=== %d PASS / %d FAIL ===\n", g_pass, g_fail);
    return g_fail == 0 ? 0 : 1;
}
