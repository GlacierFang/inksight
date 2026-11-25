/**
 * test_net_dispatch.c —— 网络请求调度的**路由**测试（纯 C，主机侧可跑）
 *
 * 验证的是「op 是否路由到正确的端点、参数是否原样传下去」——
 * 这件事真机上没法验（要真发一次请求），但恰恰是最容易写错的地方。
 *
 * 做法：给 net_api 层的一组函数提供**桩实现**，记录每次被调用的是哪个端点，
 * 然后喂不同的 `net_request_t.op`，断言路由结果。
 * 桩实现放在本文件里 —— 因为 net_dispatch.c 只依赖 net_api.h 的声明，
 * 链接时不会把真的 net_api.cpp 带进来。
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "net_api.h"

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

/* ---------- 桩：记录被调用的端点 ---------- */

typedef enum {
    CALL_NONE = 0,
    CALL_SOLVE,
    CALL_VOICE,
    CALL_BOOTSTRAP,
    CALL_PAIR,
    CALL_PROFILE,
    CALL_REGISTER,
    CALL_ENQUEUE,
} call_t;

static call_t g_last_call = CALL_NONE;
static int g_last_biz = 0;
static char g_last_session[32];
static char g_last_page[16];
static char g_last_pack[24];
static char g_last_card[24];
static char g_last_code[8];
static char g_last_media[72];   /* 媒体路径（TF 卡路径上限 72） */
static char g_last_stage[16];
static int g_last_year = 0;
static int g_last_quota = 0;
static int g_last_hint = 0;
static size_t g_last_jpg_len = 0;
static size_t g_last_pcm_len = 0;
static int g_cleanup_count = 0;
static bool g_solve_ok = true;   /* 桩的拍题结果：默认成功，测试里可改成失败 */

/* 桩返回的应答明文（net_dispatch 会把所有权交给调用方） */
static char *make_resp(void)
{
    char *p = (char *)malloc(8);
    if (p != NULL) {
        memcpy(p, "{\"ok\":1}", 8);
    }
    return p;
}

bool net_study_solve(const char *session_id, const uint8_t *jpg_data, size_t jpg_len,
                     const uint8_t *pcm_data, size_t pcm_len, uint8_t hint_level,
                     int *out_biz_code, char **out_resp_json)
{
    (void)jpg_data;
    (void)pcm_data;
    g_last_call = CALL_SOLVE;
    snprintf(g_last_session, sizeof g_last_session, "%s", session_id ? session_id : "");
    g_last_jpg_len = jpg_len;
    g_last_pcm_len = pcm_len;
    g_last_hint = hint_level;
    g_last_biz = 0;
    if (out_biz_code != NULL) {
        *out_biz_code = 0;
    }
    if (out_resp_json != NULL) {
        *out_resp_json = make_resp();
    }
    /* g_solve_ok 用来模拟「传输失败」（断网 / 未配对）——
       调度层看到失败就该把这条入队，而不是丢掉用户的拍照。 */
    return g_solve_ok;
}

bool net_study_voice(const char *context_page, const char *pack_id, const char *card_id,
                     const uint8_t *pcm_data, size_t pcm_len,
                     int *out_biz_code, char **out_resp_json)
{
    (void)pcm_data;
    g_last_call = CALL_VOICE;
    snprintf(g_last_page, sizeof g_last_page, "%s", context_page ? context_page : "");
    snprintf(g_last_pack, sizeof g_last_pack, "%s", pack_id ? pack_id : "");
    snprintf(g_last_card, sizeof g_last_card, "%s", card_id ? card_id : "");
    g_last_pcm_len = pcm_len;
    g_last_biz = 0;
    if (out_biz_code != NULL) {
        *out_biz_code = 0;
    }
    if (out_resp_json != NULL) {
        *out_resp_json = make_resp();
    }
    return true;
}

bool net_sync_bootstrap_auto(int *out_biz_code, char **out_resp_json)
{
    g_last_call = CALL_BOOTSTRAP;
    g_last_biz = 0;
    if (out_biz_code != NULL) {
        *out_biz_code = 0;
    }
    if (out_resp_json != NULL) {
        *out_resp_json = make_resp();
    }
    return true;
}

bool net_pair_with_code(const char *six_digit_code)
{
    g_last_call = CALL_PAIR;
    snprintf(g_last_code, sizeof g_last_code, "%s", six_digit_code ? six_digit_code : "");
    return true;
}

bool net_auto_register(void)
{
    g_last_call = CALL_REGISTER;
    return true;
}

/* 离线队列：入队与媒体清理（块 B 新增） */
bool net_outbox_enqueue_solve(const char *session_id, const char *media_path,
                               uint8_t hint_level)
{
    (void)session_id;
    (void)hint_level;
    g_last_call = CALL_ENQUEUE;
    snprintf(g_last_media, sizeof g_last_media, "%s", media_path ? media_path : "");
    return true;
}

void net_media_cleanup(const char *media_path)
{
    g_cleanup_count++;
    snprintf(g_last_media, sizeof g_last_media, "%s", media_path ? media_path : "");
}

/* 净调度层会拉待下载清单（真实现会调 TF 卡下载）—— 路由测试里只需要它能链上。 */
bool net_sync_pending_assets(const char *bootstrap_json, size_t len)
{
    (void)bootstrap_json;
    (void)len;
    return true;
}

bool net_profile_set(const char *edu_stage, int enrollment_year, int remaining_quota)
{
    g_last_call = CALL_PROFILE;
    snprintf(g_last_stage, sizeof g_last_stage, "%s", edu_stage ? edu_stage : "");
    g_last_year = enrollment_year;
    g_last_quota = remaining_quota;
    return true;
}

/* ---------- 用例 ---------- */

static void reset(void)
{
    g_last_call = CALL_NONE;
    g_last_session[0] = '\0';
    g_last_page[0] = '\0';
    g_last_pack[0] = '\0';
    g_last_card[0] = '\0';
    g_last_code[0] = '\0';
    g_last_stage[0] = '\0';
    g_last_year = 0;
    g_last_quota = 0;
    g_last_hint = 0;
    g_last_jpg_len = 0;
    g_last_pcm_len = 0;
    g_cleanup_count = 0;
    g_last_media[0] = '\0';
}

int main(void)
{
    printf("=== 网络请求调度路由测试（net_dispatch）===\n");

    net_request_t req;
    char *resp = NULL;

    /* 1. 拍题 */
    reset();
    memset(&req, 0, sizeof req);
    req.op = NET_OP_SOLVE;
    snprintf(req.session_id, sizeof req.session_id, "sess_1795412000");
    req.jpg = (uint8_t *)"JPEGDATA";
    req.jpg_len = 8;
    req.hint_level = 2;
    int rc = net_dispatch(&req, &resp);
    CHECK(g_last_call == CALL_SOLVE, "op=拍题 路由到 study/solve");
    CHECK(rc == 0, "拍题返回业务码 0");
    CHECK(strcmp(g_last_session, "sess_1795412000") == 0, "session_id 原样传下去");
    CHECK(g_last_jpg_len == 8, "图片长度原样传下去");
    CHECK(g_last_hint == 2, "hint_level 原样传下去");
    CHECK(resp != NULL && strcmp(resp, "{\"ok\":1}") == 0, "业务明文原样返回");
    free(resp);
    resp = NULL;

    /* 2. 语音 */
    reset();
    memset(&req, 0, sizeof req);
    req.op = NET_OP_VOICE;
    snprintf(req.context_page, sizeof req.context_page, "READER");
    snprintf(req.pack_id, sizeof req.pack_id, "m7_geo");
    snprintf(req.card_id, sizeof req.card_id, "card_03");
    req.pcm = (const int16_t *)"PCMDATA";
    req.pcm_samples = 7;
    rc = net_dispatch(&req, &resp);
    CHECK(g_last_call == CALL_VOICE, "op=语音 路由到 study/voice");
    CHECK(strcmp(g_last_page, "READER") == 0, "context_page 原样传下去");
    CHECK(strcmp(g_last_pack, "m7_geo") == 0, "pack_id 原样传下去");
    CHECK(strcmp(g_last_card, "card_03") == 0, "card_id 原样传下去");
    CHECK(g_last_pcm_len == 7, "PCM 长度原样传下去");
    free(resp);
    resp = NULL;

    /* 3. 心跳同步 */
    reset();
    memset(&req, 0, sizeof req);
    req.op = NET_OP_BOOTSTRAP;
    rc = net_dispatch(&req, &resp);
    CHECK(g_last_call == CALL_BOOTSTRAP, "op=心跳 路由到 sync/bootstrap");
    CHECK(rc == 0, "心跳返回业务码 0");
    free(resp);
    resp = NULL;

    /* 4. 配对 */
    reset();
    memset(&req, 0, sizeof req);
    req.op = NET_OP_PAIR;
    snprintf(req.pair_code, sizeof req.pair_code, "123456");
    rc = net_dispatch(&req, &resp);
    CHECK(g_last_call == CALL_PAIR, "op=配对 路由到 pair");
    CHECK(strcmp(g_last_code, "123456") == 0, "配对码原样传下去");
    CHECK(rc == 0, "配对成功返回 0");
    CHECK(resp == NULL, "配对不产生业务明文");
    free(resp);
    resp = NULL;

    /* 5. 自动登记（无码配对） */
    reset();
    memset(&req, 0, sizeof req);
    req.op = NET_OP_REGISTER;
    rc = net_dispatch(&req, &resp);
    CHECK(g_last_call == CALL_REGISTER, "op=自动登记 路由到 net_auto_register");
    CHECK(rc == 0, "自动登记成功返回 0");
    CHECK(resp == NULL, "自动登记不产生业务明文");
    free(resp);
    resp = NULL;

    /* 5b. 拍题失败 -> 入队；拍题成功 -> 删媒体文件 */
    reset();
    memset(&req, 0, sizeof req);
    req.op = NET_OP_SOLVE;
    snprintf(req.session_id, sizeof req.session_id, "sess_1");
    snprintf(req.media_path, sizeof req.media_path, "/outbox/task_42.jpg");
    g_solve_ok = false;          /* 模拟传输失败（断网/未配对） */
    rc = net_dispatch(&req, &resp);
    CHECK(g_last_call == CALL_ENQUEUE, "拍题失败时入队而不是丢掉");
    CHECK(strcmp(g_last_media, "/outbox/task_42.jpg") == 0, "入队带上媒体路径");
    CHECK(rc == -1, "失败返回 -1");
    free(resp);
    resp = NULL;

    reset();
    memset(&req, 0, sizeof req);
    req.op = NET_OP_SOLVE;
    snprintf(req.session_id, sizeof req.session_id, "sess_1");
    snprintf(req.media_path, sizeof req.media_path, "/outbox/task_42.jpg");
    g_solve_ok = true;           /* 模拟上传成功 */
    rc = net_dispatch(&req, &resp);
    CHECK(g_last_call != CALL_ENQUEUE, "拍题成功时不入队");
    CHECK(g_cleanup_count == 1, "拍题成功时删掉卡上的媒体文件");
    CHECK(strcmp(g_last_media, "/outbox/task_42.jpg") == 0, "删的是那张图");
    free(resp);
    resp = NULL;

    /* 6. 档案同步 */
    reset();
    memset(&req, 0, sizeof req);
    req.op = NET_OP_PROFILE;
    snprintf(req.edu_stage, sizeof req.edu_stage, "JUNIOR");
    req.enrollment_year = 2026;
    req.remaining_quota = 8;
    rc = net_dispatch(&req, &resp);
    CHECK(g_last_call == CALL_PROFILE, "op=档案 路由到 profile_set");
    CHECK(strcmp(g_last_stage, "JUNIOR") == 0, "学段原样传下去");
    CHECK(g_last_year == 2026, "入学年原样传下去");
    CHECK(g_last_quota == 8, "剩余配额原样传下去");
    CHECK(rc == 0, "档案同步成功返回 0");
    free(resp);
    resp = NULL;

    /* 6. 未知 op 必须失败，且不产生明文 */
    reset();
    memset(&req, 0, sizeof req);
    req.op = 99;
    rc = net_dispatch(&req, &resp);
    CHECK(g_last_call == CALL_NONE, "未知 op 不调任何端点");
    CHECK(rc == -1, "未知 op 返回 -1");
    CHECK(resp == NULL, "未知 op 不产生业务明文");

    /* 7. 空指针保护 */
    rc = net_dispatch(NULL, &resp);
    CHECK(rc == -1, "空请求返回 -1");

    printf("\n=== %d PASS / %d FAIL ===\n", g_pass, g_fail);
    return g_fail == 0 ? 0 : 1;
}
