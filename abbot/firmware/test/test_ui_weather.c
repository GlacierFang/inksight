/**
 * test_ui_weather.c —— 天气与城市的**主机侧**单元测试（纯 C）
 *
 * 覆盖 doc/05 §5.2 的三条硬约束：
 *   1. 「没有天气」只有一种表达（空态文案），不出现 0℃ / 破折号；
 *   2. 离线兜底用本地快照时**标明是缓存**，不冒充实时；
 *   3. 城市名必须是**能直接给服务端用的地名**（不是经纬度，也不是 JSON 危险字符）。
 *
 * 以及协议层 `nr_parse_weather()` 的行为：缺字段 / 空对象 / 缺 temp / 缺 text
 * 全归到同一个空态。
 *
 * 编译（在 firmware/ 目录下）：
 *   gcc -Wall -Wextra -Werror -I include -I src \
 *       test/test_ui_weather.c src/ui/ui_weather.c src/net/net_req.c src/net/net_client.c \
 *       -o /tmp/test_ui_weather -lmbedtls -lmbedcrypto
 */

#include <stdio.h>
#include <string.h>

#include "net_req.h"
#include "ui_weather.h"

static int g_fail = 0;
static int g_checks = 0;

#define CHECK(cond, msg)                                                    \
    do {                                                                     \
        g_checks++;                                                          \
        if (!(cond)) {                                                       \
            g_fail++;                                                        \
            printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, (msg));           \
        }                                                                    \
    } while (0)

#define CHECK_STR(actual, expect, msg)                                      \
    do {                                                                     \
        g_checks++;                                                          \
        if (strcmp((actual), (expect)) != 0) {                               \
            g_fail++;                                                        \
            printf("FAIL %s:%d  %s\n  实际: \"%s\"\n  期望: \"%s\"\n",       \
                   __FILE__, __LINE__, (msg), (actual), (expect));           \
        }                                                                    \
    } while (0)

/* ===================== 协议层：天气解析 ===================== */

static void test_parse_weather(void)
{
    nr_weather_t w;

    /* 正常：有温度有描述 */
    const char *ok = "{\"weather\":{\"temp\":4,\"text\":\"晴间多云\"},\"server_time\":123}";
    CHECK(nr_parse_weather(ok, strlen(ok), &w) == 0, "有数据时应解析成功");
    CHECK(w.valid, "valid 应为 true");
    CHECK(w.temp == 4, "温度应为 4");
    CHECK_STR(w.text, "晴间多云", "描述应为晴间多云");

    /* 空对象 —— 服务端「暂时没有数据」的形状 */
    const char *empty = "{\"weather\":{},\"server_time\":123}";
    CHECK(nr_parse_weather(empty, strlen(empty), &w) == 1, "空对象应为空态");
    CHECK(!w.valid, "空对象时 valid 应为 false");
    CHECK(w.temp == 0, "空对象时 temp 不应有值");
    CHECK_STR(w.text, "", "空对象时描述应为空串");

    /* 完全没有 weather 字段 */
    const char *none = "{\"server_time\":123}";
    CHECK(nr_parse_weather(none, strlen(none), &w) == 1, "缺 weather 应为空态");
    CHECK(!w.valid, "缺 weather 时 valid 应为 false");

    /* 有 temp 没 text —— 不算数据（不能显示「25℃」配空白） */
    const char *no_text = "{\"weather\":{\"temp\":25}}";
    CHECK(nr_parse_weather(no_text, strlen(no_text), &w) == 1, "缺 text 应为空态");
    CHECK(!w.valid, "缺 text 时 valid 应为 false");

    /* 有 text 没 temp —— 同样空态 */
    const char *no_temp = "{\"weather\":{\"text\":\"晴\"}}";
    CHECK(nr_parse_weather(no_temp, strlen(no_temp), &w) == 1, "缺 temp 应为空态");
    CHECK(!w.valid, "缺 temp 时 valid 应为 false");

    /* 负温度（服务端可以是负数） */
    const char *neg = "{\"weather\":{\"temp\":-7,\"text\":\"小雪\"}}";
    CHECK(nr_parse_weather(neg, strlen(neg), &w) == 0, "负温度应解析成功");
    CHECK(w.temp == -7, "温度应为 -7");
    CHECK_STR(w.text, "小雪", "描述应为小雪");

    /* 浮点温度（缓存文件里可能留着浮点） */
    const char *flt = "{\"weather\":{\"temp\":4.6,\"text\":\"多云\"}}";
    CHECK(nr_parse_weather(flt, strlen(flt), &w) == 0, "浮点温度应解析成功");
    CHECK(w.temp == 5, "4.6 应四舍五入成 5");

    /* 入参非法 */
    CHECK(nr_parse_weather(NULL, 0, &w) == -1, "NULL 入参应返回 -1");
}

/* ===================== 显示策略 ===================== */

static void test_display_text(void)
{
    nr_weather_t live;
    memset(&live, 0, sizeof live);
    live.valid = true;
    live.temp = 25;
    snprintf(live.text, sizeof live.text, "晴");

    nr_weather_t cached;
    memset(&cached, 0, sizeof cached);
    cached.valid = true;
    cached.temp = 4;
    snprintf(cached.text, sizeof cached.text, "晴间多云");

    char buf[64];

    /* 在线数据 */
    ui_weather_display_text(UI_WX_LIVE, &live, &cached, buf, sizeof buf);
    CHECK_STR(buf, "晴 25℃", "在线应显示服务端给的温度与描述");

    /* 离线兜底：同一份快照要标明是缓存 */
    ui_weather_display_text(UI_WX_CACHE, &live, &cached, buf, sizeof buf);
    CHECK(strstr(buf, "缓存") != NULL, "离线兜底必须标明是缓存");
    CHECK(strstr(buf, "4℃") != NULL, "离线兜底应显示本地快照的温度");
    CHECK(strstr(buf, "晴间多云") != NULL, "离线兜底应显示本地快照的描述");

    /* 空态：一句温和提示，不出现 0℃ 或破折号 */
    ui_weather_display_text(UI_WX_EMPTY, &live, &cached, buf, sizeof buf);
    CHECK_STR(buf, "暂时拿不到天气", "空态文案应固定");
    CHECK(strstr(buf, "0℃") == NULL, "空态不得出现 0℃");
    CHECK(strstr(buf, "-") == NULL, "空态不得出现破折号");

    /* 什么都没有 */
    ui_weather_display_text(UI_WX_NONE, &live, &cached, buf, sizeof buf);
    CHECK_STR(buf, "暂时拿不到天气", "无数据应与空态同一句文案");

    /* 在线但数据无效（valid=false）也不能显示假数据 */
    nr_weather_t bad;
    memset(&bad, 0, sizeof bad);
    ui_weather_display_text(UI_WX_LIVE, &bad, &cached, buf, sizeof buf);
    CHECK_STR(buf, "暂时拿不到天气", "无效的在线读数应落到空态");

    /* 离线但本地也没有快照 */
    ui_weather_display_text(UI_WX_CACHE, &live, &bad, buf, sizeof buf);
    CHECK_STR(buf, "暂时拿不到天气", "离线且无快照应落到空态");
}

/* ===================== 城市名校验 ===================== */

static void test_validate_city(void)
{
    CHECK(ui_weather_validate_city("Beijing") == 0, "普通地名应通过");
    CHECK(ui_weather_validate_city("Xi'an") == 0, "带撇号的地名应通过");
    CHECK(ui_weather_validate_city(NULL) == -1, "NULL 应不通过");
    CHECK(ui_weather_validate_city("") == -1, "空串应不通过");
    CHECK(ui_weather_validate_city(" ") == -1, "空格应不通过");
    CHECK(ui_weather_validate_city(" Beijing") == -1, "前导空格应不通过");
    CHECK(ui_weather_validate_city("Beijing ") == -1, "尾随空格应不通过");
    CHECK(ui_weather_validate_city("New York") == 0, "词中间有空格的地名应通过");
    CHECK(ui_weather_validate_city("Bei\ning") == -1, "含换行应不通过");
    CHECK(ui_weather_validate_city("Bei\tjing") == -1, "含制表符应不通过");
    CHECK(ui_weather_validate_city("Bei\"jing") == -1, "含引号应不通过");
    CHECK(ui_weather_validate_city("Bei\\jing") == -1, "含反斜杠应不通过");
    CHECK(ui_weather_validate_city("Bei{jing") == -1, "含花括号应不通过");

    char longname[64];
    memset(longname, 'A', sizeof longname);
    longname[40] = '\0';
    CHECK(ui_weather_validate_city(longname) == -1, "超长应不通过");
}

/* ===================== 候选城市表 ===================== */

static void test_city_candidates(void)
{
    int n = ui_weather_city_candidate_count();
    CHECK(n > 0, "候选城市不应为空");

    int i;
    int j;
    for (i = 0; i < n; i++) {
        const ui_city_candidate_t *c = ui_weather_city_candidate(i);
        CHECK(c != NULL, "候选不应为空指针");
        if (c == NULL) {
            continue;
        }
        CHECK(c->zh != NULL && c->zh[0] != '\0', "候选应有中文名");
        CHECK(c->en != NULL && c->en[0] != '\0', "候选应有上报用的英文名");
        /* 上报用的名字必须是能直接给服务端用的地名 */
        CHECK(ui_weather_validate_city(c->en) == 0, "上报用的地名必须通过校验");
        /* 候选之间不能重名（重名会让用户选错） */
        for (j = i + 1; j < n; j++) {
            const ui_city_candidate_t *o = ui_weather_city_candidate(j);
            if (o != NULL) {
                CHECK(strcmp(c->zh, o->zh) != 0, "候选中文名不应重复");
                CHECK(strcmp(c->en, o->en) != 0, "候选上报名不应重复");
            }
        }
    }
    /* 越界返回 NULL */
    CHECK(ui_weather_city_candidate(-1) == NULL, "负索引应返回 NULL");
    CHECK(ui_weather_city_candidate(n) == NULL, "越界索引应返回 NULL");

    /* 按上报名反查 */
    const ui_city_candidate_t *first = ui_weather_city_candidate(0);
    if (first != NULL) {
        const ui_city_candidate_t *back = ui_weather_city_find_by_wire(first->en);
        CHECK(back == first, "按上报名应能反查到同一个候选");
    }
    CHECK(ui_weather_city_find_by_wire("Nowhere-City") == NULL, "不存在的地名应返回 NULL");
    CHECK(ui_weather_city_find_by_wire(NULL) == NULL, "NULL 应返回 NULL");
}

int main(void)
{
    test_parse_weather();
    test_display_text();
    test_validate_city();
    test_city_candidates();

    if (g_fail == 0) {
        printf("ALL PASS (%d checks)\n", g_checks);
        return 0;
    }
    printf("FAILED: %d / %d checks\n", g_fail, g_checks);
    return 1;
}
