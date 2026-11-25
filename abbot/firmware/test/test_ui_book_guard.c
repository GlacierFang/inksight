/**
 * test_ui_book_guard.c —— 阅读进度 + 防沉迷护栏（纯逻辑层）主机侧测试
 *
 * 契约来源: abbot/doc/07 §2.3（阅读进度）/ §2.4（防沉迷护栏）
 *
 * 编译（仓库内既有格式）:
 *   gcc -Wall -Wextra -Werror -I test/host_stubs -I include -I src \
 *       test/test_ui_book_guard.c src/ui/ui_book_guard.c -o /tmp/t && /tmp/t
 */
#include <stdio.h>
#include <string.h>

#include "ui_book_guard.h"

static int g_checks = 0;
static int g_failures = 0;

#define CHECK(cond, msg)                                                        \
    do {                                                                        \
        g_checks++;                                                            \
        if (!(cond)) {                                                         \
            g_failures++;                                                      \
            printf("FAIL line %d: %s\n", __LINE__, (msg));                      \
        }                                                                       \
    } while (0)

/* ---------- 默认值 ---------- */
static void test_defaults(void)
{
    ui_book_guard_t g;
    ui_book_guard_defaults(&g);

    CHECK(g.curfew.valid, "默认配置里宵禁应当有效");
    CHECK(g.curfew.start_min == UI_BOOK_CURFEW_START_DEFAULT_MIN,
          "默认宵禁开始应为 22:00");
    CHECK(g.curfew.end_min == UI_BOOK_CURFEW_END_DEFAULT_MIN,
          "默认宵禁结束应为 06:30");
    CHECK(g.daily_limit_min == UI_BOOK_DAILY_LIMIT_DEFAULT_MIN,
          "默认每日上限应为 45 分钟");
}

/* ---------- HH:MM 解析 ---------- */
static void test_parse_hhmm(void)
{
    CHECK(ui_book_parse_hhmm("22:00") == 22 * 60, "22:00 应解析为 1320");
    CHECK(ui_book_parse_hhmm("06:30") == 6 * 60 + 30, "06:30 应解析为 390");
    CHECK(ui_book_parse_hhmm("00:00") == 0, "00:00 应解析为 0");
    CHECK(ui_book_parse_hhmm("23:59") == 23 * 60 + 59, "23:59 应解析为 1439");
    CHECK(ui_book_parse_hhmm("24:00") < 0, "24:00 非法");
    CHECK(ui_book_parse_hhmm("12:60") < 0, "12:60 非法");
    CHECK(ui_book_parse_hhmm("abc") < 0, "非 HH:MM 非法");
    CHECK(ui_book_parse_hhmm("") < 0, "空串非法");
    CHECK(ui_book_parse_hhmm(NULL) < 0, "NULL 非法");
}

/* ---------- 宵禁：跨午夜区间 ---------- */
static void test_curfew_cross_midnight(void)
{
    ui_book_guard_t g;
    ui_book_guard_defaults(&g);
    CHECK(g.curfew.start_min > g.curfew.end_min, "默认区间应当跨午夜");

    CHECK(ui_book_curfew_active(&g.curfew, 23 * 60), "23:00 应处于宵禁");
    CHECK(ui_book_curfew_active(&g.curfew, 0 * 60 + 30), "00:30 应处于宵禁");
    CHECK(ui_book_curfew_active(&g.curfew, 5 * 60 + 59), "05:59 应处于宵禁");
    CHECK(ui_book_curfew_active(&g.curfew, 22 * 60), "22:00 起点应触发");

    CHECK(!ui_book_curfew_active(&g.curfew, 12 * 60), "12:00 不应锁定");
    CHECK(!ui_book_curfew_active(&g.curfew, 7 * 60), "07:00 不应锁定");
    CHECK(!ui_book_curfew_active(&g.curfew, 21 * 60 + 59), "21:59 不应锁定");
    CHECK(!ui_book_curfew_active(&g.curfew, 6 * 60 + 30), "06:30 终点不应锁定");
}

/* ---------- 宵禁：同日区间 ---------- */
static void test_curfew_same_day(void)
{
    ui_book_curfew_t c;
    c.start_min = 13 * 60;
    c.end_min = 14 * 60;
    c.valid = true;

    CHECK(ui_book_curfew_active(&c, 13 * 60), "13:00 起点应触发");
    CHECK(ui_book_curfew_active(&c, 13 * 60 + 30), "13:30 应触发");
    CHECK(!ui_book_curfew_active(&c, 14 * 60), "14:00 终点（now < end）不应触发");
    CHECK(!ui_book_curfew_active(&c, 12 * 60 + 59), "12:59 不应触发");
}

/* ---------- 宵禁：无效配置不锁 ---------- */
static void test_curfew_invalid_config(void)
{
    ui_book_curfew_t c;
    c.start_min = 0;
    c.end_min = 0;
    c.valid = false;

    CHECK(!ui_book_curfew_active(&c, 12 * 60), "无效配置不应锁定");
}

/* ---------- 每日上限 ---------- */
static void test_daily_limit(void)
{
    ui_book_guard_t g;
    ui_book_guard_defaults(&g);

    CHECK(!ui_book_daily_limit_reached(&g, 44 * 60), "44 分钟未达上限");
    CHECK(ui_book_daily_limit_reached(&g, 45 * 60), "45 分钟应判定达到上限");
    CHECK(ui_book_daily_limit_reached(&g, 45 * 60 + 30), "超过上限应判定达到");

    g.daily_limit_min = 0;
    CHECK(!ui_book_daily_limit_reached(&g, 100000), "limit<=0 视为不限制");
}

/* ---------- percent 实时重算 ---------- */
static void test_progress_percent(void)
{
    CHECK(ui_book_progress_percent(0, 100) == 0, "0/100 应为 0%");
    CHECK(ui_book_progress_percent(50, 100) == 50, "50/100 应为 50%");
    CHECK(ui_book_progress_percent(100, 100) == 100, "100/100 应为 100%");
    CHECK(ui_book_progress_percent(1, 3) == 33, "1/3 应为 33%");
    CHECK(ui_book_progress_percent(0, 0) == 0, "total==0 应为 0%");
    CHECK(ui_book_progress_percent(100, 0) == 0, "total==0 应为 0%");
    CHECK(ui_book_progress_percent(200, 100) == 100, "offset>total 应钳位 100%");
    CHECK(ui_book_progress_percent(0xFFFFFFFFu, 1) == 100, "极大 offset 应钳位 100%");
}

/* ---------- bookmarks 解析（doc/07 §2.3 形状） ---------- */
static void test_bookmarks_parse(void)
{
    const char *json =
        "{\"schema_version\":1,\"daily_date\":\"2026-11-23\",\"daily_used_sec\":1320,"
        "\"books\":{\"shiji_01.txt\":{\"byte_offset\":15420,\"total_bytes\":482900,"
        "\"percent\":3.2,\"last_read_ts\":1795412000},"
        "\"guoxue.txt\":{\"byte_offset\":0,\"total_bytes\":100}}}";
    ui_bookmarks_t marks;
    ui_bookmarks_parse(json, strlen(json), &marks);

    CHECK(marks.schema_version == 1, "schema_version 应为 1");
    CHECK(strcmp(marks.daily_date, "2026-11-23") == 0, "daily_date 应解析出来");
    CHECK(marks.daily_used_sec == 1320, "daily_used_sec 应为 1320");

    CHECK(marks.count == 2, "应解析出 2 本书");
    int idx = ui_bookmarks_find(&marks, "shiji_01.txt");
    CHECK(idx >= 0, "应找到 shiji_01.txt");
    if (idx >= 0) {
        CHECK(marks.items[idx].offset == 15420, "byte_offset 应为 15420");
        CHECK(marks.items[idx].total_bytes == 482900, "total_bytes 应为 482900");
        CHECK(marks.items[idx].last_read_ts == 1795412000, "last_read_ts 应解析出来");
    }
    CHECK(ui_bookmarks_find(&marks, "guoxue.txt") >= 0, "应找到 guoxue.txt");
    CHECK(ui_bookmarks_find(&marks, "english.txt") < 0, "english.txt 不该有进度");
}

/* ---------- byte_offset 的旧别名 offset 也认 ---------- */
static void test_bookmarks_legacy_offset_alias(void)
{
    const char *json = "{\"books\":{\"a.txt\":{\"offset\":250,\"total_bytes\":500}}}";
    ui_bookmarks_t marks;
    ui_bookmarks_parse(json, strlen(json), &marks);
    int idx = ui_bookmarks_find(&marks, "a.txt");
    CHECK(idx >= 0, "应找到 a.txt");
    if (idx >= 0) {
        CHECK(marks.items[idx].offset == 250, "旧别名 offset 应被接受");
    }
}

/* ---------- bookmarks 坏数据 ---------- */
static void test_bookmarks_bad_json(void)
{
    ui_bookmarks_t marks;
    ui_bookmarks_parse("", 0, &marks);
    CHECK(marks.count == 0, "空输入应解析出 0 条");

    ui_bookmarks_parse("not json", 8, &marks);
    CHECK(marks.count == 0, "非法 JSON 应解析出 0 条");

    ui_bookmarks_parse("{\"books\":[]}", 12, &marks);
    CHECK(marks.count == 0, "books 结构不对应解析出 0 条");
}

/* ---------- bookmarks 写入：唯一最新进度 ---------- */
static void test_bookmarks_set_unique(void)
{
    ui_bookmarks_t marks;
    ui_bookmarks_t tmp;
    marks.count = 0;

    CHECK(ui_bookmarks_set(&marks, "语文", 100, 1000) >= 0, "首次写入应成功");
    CHECK(marks.count == 1, "首次写入后应有 1 条");

    CHECK(ui_bookmarks_set(&marks, "语文", 700, 1000) >= 0, "更新进度应成功");
    CHECK(marks.count == 1, "同一本书只能有一条进度");
    CHECK(marks.items[0].offset == 700, "应保留最新 offset");
    CHECK(marks.items[0].total_bytes == 1000, "应保留 total_bytes");

    CHECK(ui_bookmarks_set(&marks, "数学", 0, 500) >= 0, "第二本书应写入成功");
    CHECK(marks.count == 2, "此时应有 2 条");
    CHECK(ui_bookmarks_find(&marks, "数学") >= 0, "应能找到 数学");

    /* 容量上限：超出返回 -1，且不覆盖别人的进度 */
    int i;
    for (i = 0; i < UI_BOOK_MARK_MAX - 2; i++) {
        char name[UI_BOOK_MARK_NAME_MAX];
        snprintf(name, sizeof(name), "书%d", i);
        CHECK(ui_bookmarks_set(&marks, name, 0, 10) >= 0, "批量写入应成功");
    }
    CHECK(marks.count == UI_BOOK_MARK_MAX, "应写满容量");
    CHECK(ui_bookmarks_set(&marks, "溢出", 0, 10) == -1, "超出容量应返回 -1");
    CHECK(marks.count == UI_BOOK_MARK_MAX, "溢出不应改变条数");
    CHECK(ui_bookmarks_find(&marks, "溢出") < 0, "溢出书不应被写入");

    (void)tmp;
}

/* ---------- bookmarks 格式化往返 ---------- */
static void test_bookmarks_format_roundtrip(void)
{
    ui_bookmarks_t marks;
    marks.count = 0;
    marks.schema_version = 1;
    snprintf(marks.daily_date, sizeof(marks.daily_date), "%s", "2026-11-25");
    marks.daily_used_sec = 600;
    ui_bookmarks_set(&marks, "语文", 1200, 4800);
    ui_bookmarks_set(&marks, "数学", 25, 100);

    char buf[1024];
    int n = ui_bookmarks_format(&marks, buf, sizeof(buf));
    CHECK(n > 0, "格式化应成功");
    CHECK((size_t)n < sizeof(buf), "格式化长度应在容量内");
    CHECK(strstr(buf, "\"byte_offset\":1200") != NULL, "应写出 byte_offset 字段");
    CHECK(strstr(buf, "\"daily_used_sec\":600") != NULL, "应写出 daily_used_sec 字段");
    CHECK(strstr(buf, "\"daily_date\":\"2026-11-25\"") != NULL, "应写出 daily_date 字段");

    ui_bookmarks_t back;
    ui_bookmarks_parse(buf, (size_t)n, &back);
    CHECK(back.count == 2, "往返后应有 2 条");
    CHECK(back.daily_used_sec == 600, "往返后当日秒数应一致");
    CHECK(strcmp(back.daily_date, "2026-11-25") == 0, "往返后日期应一致");
    int idx = ui_bookmarks_find(&back, "语文");
    CHECK(idx >= 0, "往返后应找到 语文");
    if (idx >= 0) {
        CHECK(back.items[idx].offset == 1200, "往返后 offset 应一致");
        CHECK(back.items[idx].total_bytes == 4800, "往返后 total 应一致");
    }
    idx = ui_bookmarks_find(&back, "数学");
    CHECK(idx >= 0, "往返后应找到 数学");
    if (idx >= 0) {
        CHECK(back.items[idx].offset == 25, "往返后 offset 应一致");
    }

    /* 容量不足应返回 -1 */
    char tiny[16];
    CHECK(ui_bookmarks_format(&marks, tiny, sizeof(tiny)) == -1, "容量不足应返回 -1");
}

/* ---------- .bak 自愈路径：主文件坏了就退回备份 ---------- */
static void test_bak_self_heal(void)
{
    const char *good = "{\"books\":{\"a.txt\":{\"byte_offset\":100,\"total_bytes\":200}}}";
    const char *broken = "{\"books\":{\"a.txt\":{\"byte_off";

    CHECK(ui_bookmarks_valid(good, strlen(good)), "合法进度表应通过校验");
    CHECK(!ui_bookmarks_valid(broken, strlen(broken)), "残缺进度表应校验失败");
    CHECK(!ui_bookmarks_valid("", 0), "空内容应校验失败");
    CHECK(ui_bookmarks_valid("{\"books\":{}}", 12), "空进度表也是合法文件");

    const char *picked = NULL;
    size_t picked_len = 0;

    /* 主文件好 —— 用主文件 */
    int rc = ui_book_pick_source(good, strlen(good), broken, strlen(broken), &picked,
                                 &picked_len);
    CHECK(rc == 0, "主文件合法时应选主文件");
    CHECK(picked == good, "应指向主文件内容");

    /* 主文件坏了 —— 退回备份 */
    rc = ui_book_pick_source(broken, strlen(broken), good, strlen(good), &picked,
                             &picked_len);
    CHECK(rc == 1, "主文件残缺时应选备份");
    CHECK(picked == good, "应指向备份内容");
    CHECK(picked_len == strlen(good), "备份长度应正确");

    /* 两份都坏 —— 没有可用源 */
    rc = ui_book_pick_source(broken, strlen(broken), broken, strlen(broken), &picked,
                             &picked_len);
    CHECK(rc == -1, "两份都坏时应返回 -1");
}

/* ---------- 跨日清零（doc/07 §2.4） ---------- */
static void test_daily_rollover(void)
{
    ui_bookmarks_t marks;
    marks.count = 0;
    snprintf(marks.daily_date, sizeof(marks.daily_date), "%s", "2026-11-23");
    marks.daily_used_sec = 1320;

    CHECK(!ui_book_daily_rollover(&marks, "2026-11-23"), "同一天不应清零");
    CHECK(marks.daily_used_sec == 1320, "同一天秒数应保持");

    CHECK(ui_book_daily_rollover(&marks, "2026-11-24"), "跨日应清零");
    CHECK(marks.daily_used_sec == 0, "跨日后秒数应清零");
    CHECK(strcmp(marks.daily_date, "2026-11-24") == 0, "跨日后日期应更新");
}

int main(void)
{
    test_defaults();
    test_parse_hhmm();
    test_curfew_cross_midnight();
    test_curfew_same_day();
    test_curfew_invalid_config();
    test_daily_limit();
    test_progress_percent();
    test_bookmarks_parse();
    test_bookmarks_legacy_offset_alias();
    test_bookmarks_bad_json();
    test_bookmarks_set_unique();
    test_bookmarks_format_roundtrip();
    test_bak_self_heal();
    test_daily_rollover();

    printf("checks=%d failures=%d\n", g_checks, g_failures);
    if (g_failures == 0) {
        printf("ALL PASS\n");
        return 0;
    }
    printf("FAILED\n");
    return 1;
}
