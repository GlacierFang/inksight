/**
 * test_ui_data.c —— UI 数据读取层的**主机侧**单元测试（纯 C）
 *
 * 覆盖 doc/07 §3 的两条硬约束：
 *   1. UTF-8 边界对齐（多字节字符绝不切坏）；
 *   2. 正文排版折行与向前翻页回溯。
 * 以及 doc/04 §3.2 的课表解析规则（单双周 / 调课 / 节假日 / 周末）。
 *
 * 这一层不碰 TF 卡：这里只测**纯逻辑**（解析 + 排版 + 边界），
 * 读卡通道（请求/应答）在固件里跑，host 侧只保证算法正确。
 *
 * 编译（在 firmware/ 目录下）：
 *   gcc -Wall -Wextra -Werror -I include -I src test/test_ui_data.c src/ui/ui_data.c -o /tmp/test_ui_data
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "ui_data.h"

/* ============================================================
 * 主机侧的假后端
 *
 * 固件里 TF 卡由 task_storage 单线程串行化，UI 侧经队列请求/应答。
 * 这里给 host 测试塞一个**假存储任务**：队列收到请求就地应答，
 * 数据放在内存里的假 TF 卡上 —— 于是请求/应答通道本身也能被测到。
 * ============================================================ */
#include "storage_io.h"

/* --- 假 TF 卡 --- */
typedef struct {
    char path[72];
    char data[4096];
    uint32_t len;
    int used;
} fake_file_t;

#define FAKE_MAX_FILES 8
static fake_file_t g_files[FAKE_MAX_FILES];
static int g_fake_mounted = 1;

static fake_file_t *fake_find(const char *path)
{
    int i;
    for (i = 0; i < FAKE_MAX_FILES; i++) {
        if (g_files[i].used && strcmp(g_files[i].path, path) == 0) {
            return &g_files[i];
        }
    }
    return NULL;
}

static fake_file_t *fake_add(const char *path)
{
    int i;
    for (i = 0; i < FAKE_MAX_FILES; i++) {
        if (!g_files[i].used) {
            g_files[i].used = 1;
            snprintf(g_files[i].path, sizeof(g_files[i].path), "%s", path);
            g_files[i].len = 0;
            g_files[i].data[0] = '\0';
            return &g_files[i];
        }
    }
    return NULL;
}

/** 假存储任务：就地处理一条请求（等价固件里 task_storage 的 switch）。 */
static void fake_service(storage_req_t *req)
{
    storage_io_t *io = req->io;
    if (io == NULL) {
        return;
    }
    switch (req->op) {
    case 5: { /* 读文本 */
        fake_file_t *f = fake_find(req->path);
        if (f == NULL) {
            io->result = UI_DATA_ERR_NOT_FOUND;
            break;
        }
        uint32_t n = f->len;
        if (n > io->buf_cap - 1) {
            n = io->buf_cap - 1;
        }
        memcpy(io->buf, f->data, n);
        io->buf[n] = '\0';
        io->out_len = n;
        io->result = UI_DATA_OK;
        break;
    }
    case 6: { /* 读区间 */
        fake_file_t *f = fake_find(req->path);
        if (f == NULL) {
            io->result = UI_DATA_ERR_NOT_FOUND;
            break;
        }
        if (req->offset >= f->len) {
            io->out_len = 0;
            io->result = UI_DATA_ERR_IO; /* 固件里 EOF 也是 IO 错 */
            break;
        }
        uint32_t n = f->len - req->offset;
        if (n > io->buf_cap) {
            n = io->buf_cap;
        }
        memcpy(io->buf, f->data + req->offset, n);
        io->out_len = n;
        io->result = UI_DATA_OK;
        break;
    }
    case 8: { /* 文件大小 */
        fake_file_t *f = fake_find(req->path);
        if (f == NULL) {
            io->result = UI_DATA_ERR_NOT_FOUND;
            break;
        }
        io->out_size = f->len;
        io->result = UI_DATA_OK;
        break;
    }
    case 9: { /* 原子写 */
        fake_file_t *f = fake_find(req->path);
        if (f == NULL) {
            f = fake_add(req->path);
        }
        if (f == NULL) {
            io->result = UI_DATA_ERR_NO_SPACE;
            break;
        }
        uint32_t n = req->len;
        if (n > sizeof(f->data) - 1) {
            n = (uint32_t)sizeof(f->data) - 1;
        }
        memcpy(f->data, req->text, n);
        f->data[n] = '\0';
        f->len = n;
        io->result = UI_DATA_OK;
        break;
    }
    case 11: /* 是否挂载 */
        io->mounted = (uint8_t)(g_fake_mounted ? 1 : 0);
        io->result = UI_DATA_OK;
        break;
    default:
        io->result = UI_DATA_ERR_UNSUPPORTED;
        break;
    }
    /* 应答信号量必须给（等价固件里 task_storage 末尾的 xSemaphoreGive） */
    if (req->reply != NULL) {
        xSemaphoreGive(req->reply);
    }
}

/* --- FreeRTOS 假实现（固件里由 IDF 提供） --- */
typedef struct {
    int given;
} fake_sem_t;

static QueueHandle_t g_fake_queue = (QueueHandle_t)0x1;

QueueHandle_t hal_storage_req_queue(void)
{
    return g_fake_queue;
}

BaseType_t xQueueSend(QueueHandle_t q, const void *item, TickType_t wait)
{
    (void)q;
    (void)wait;
    if (item == NULL) {
        return pdFALSE;
    }
    fake_service((storage_req_t *)item);
    return pdTRUE;
}

BaseType_t xQueueReceive(QueueHandle_t q, void *item, TickType_t wait)
{
    (void)q;
    (void)item;
    (void)wait;
    return pdFALSE;
}

SemaphoreHandle_t xSemaphoreCreateBinary(void)
{
    fake_sem_t *s = (fake_sem_t *)calloc(1, sizeof(fake_sem_t));
    return (SemaphoreHandle_t)s;
}

BaseType_t xSemaphoreGive(SemaphoreHandle_t s)
{
    fake_sem_t *fs = (fake_sem_t *)s;
    if (fs != NULL) {
        fs->given = 1;
    }
    return pdTRUE;
}

BaseType_t xSemaphoreTake(SemaphoreHandle_t s, TickType_t wait)
{
    (void)wait;
    fake_sem_t *fs = (fake_sem_t *)s;
    if (fs != NULL && fs->given) {
        fs->given = 0;
        return pdTRUE;
    }
    return pdFALSE;
}

void vSemaphoreDelete(SemaphoreHandle_t s)
{
    free(s);
}

static int g_fail = 0;
static int g_checks = 0;

#define CHECK(cond, ...)                                                      \
    do {                                                                      \
        g_checks++;                                                           \
        if (!(cond)) {                                                        \
            g_fail++;                                                         \
            printf("FAIL %s:%d ", __FILE__, __LINE__);                       \
            printf(__VA_ARGS__);                                              \
            printf("\n");                                                     \
        }                                                                     \
    } while (0)

/* ---------- UTF-8 边界 ---------- */

static void test_utf8_align(void)
{
    /* "中" = E4 B8 AD（三字节），"𠮷" = F0 A0 AE B7（四字节） */
    const uint8_t buf[] = {
        'a', 0xE4, 0xB8, 0xAD, 'b', 0xF0, 0xA0, 0xAE, 0xB7, 'c'
    };
    uint32_t len = (uint32_t)sizeof(buf);

    /* 落在 '中' 的中间字节上 -> 向前对齐到下一个字符首字节 */
    CHECK(ui_data_utf8_align_forward(buf, len, 2) == 4, "forward(2) 应为 4");
    /* 落在 4 字节字符的中间 -> 向前对齐 */
    CHECK(ui_data_utf8_align_forward(buf, len, 7) == 9, "forward(7) 应为 9");
    /* 本来就在首字节 -> 不动 */
    CHECK(ui_data_utf8_align_forward(buf, len, 1) == 1, "forward(1) 应为 1");

    /* 向后对齐：落在中间字节 -> 退回该字符首字节 */
    CHECK(ui_data_utf8_align_backward(buf, len, 3) == 1, "backward(3) 应为 1");
    CHECK(ui_data_utf8_align_backward(buf, len, 8) == 5, "backward(8) 应为 5");
    CHECK(ui_data_utf8_align_backward(buf, len, 0) == 0, "backward(0) 应为 0");
}

/* ---------- 排版折行 ---------- */

static void test_layout(void)
{
    /* 30 个全角汉字：每字 2 单位，一行装 19 字（38 单位） */
    const char *text =
        "一二三四五六七八九十一二三四五六七八九十"
        "一二三四五六七八九十";
    uint32_t consumed = 0;
    int lines = 0;
    char out[256];

    CHECK(ui_data_layout_page((const uint8_t *)text, (uint32_t)strlen(text), &consumed,
                               &lines, out, (uint32_t)sizeof(out)) == UI_DATA_OK,
          "layout 应成功");

    /* 折成 2 行：第一行 19 字（38 单位），第二行 11 字 */
    CHECK(lines == 2, "应折成 2 行，实际 %d", lines);
    /* 两行合计 30 字 × 3 字节 = 90 字节 */
    CHECK(consumed == 90, "本页应消耗 90 字节，实际 %u", (unsigned)consumed);

    /* 折行点必须落在字符边界：第 19 个字后是换行 */
    CHECK((uint8_t)out[57] == (uint8_t)'\n', "第 19 字后应为换行");

    /* 空输入 */
    CHECK(ui_data_layout_page((const uint8_t *)"", 0, &consumed, &lines, out,
                               (uint32_t)sizeof(out)) == UI_DATA_OK,
          "空输入应成功");
    CHECK(lines == 0, "空输入 0 行");
    CHECK(consumed == 0, "空输入消耗 0 字节");
}

/** 折行不得把多字节字符切坏：把一页的输出再对齐一次应不变。 */
static void test_layout_no_split(void)
{
    const char *text = "中文中文中文中文中文中文中文中文中文中文";
    uint32_t consumed = 0;
    int lines = 0;
    char out[256];
    CHECK(ui_data_layout_page((const uint8_t *)text, (uint32_t)strlen(text), &consumed,
                               &lines, out, (uint32_t)sizeof(out)) == UI_DATA_OK,
          "layout 应成功");
    /* consumed 必须是完整字符的整数倍 */
    CHECK(consumed % 3 == 0, "消耗字节数应为 3 的倍数，实际 %u", (unsigned)consumed);
}

/* ---------- 向前翻页回溯 ---------- */

/** 往假 TF 卡里塞一个文件（字节内容）。 */
static int fake_put(const char *path, const uint8_t *data, uint32_t len)
{
    fake_file_t *f = fake_find(path);
    if (f == NULL) {
        f = fake_add(path);
    }
    if (f == NULL) {
        return -1;
    }
    if (len > sizeof(f->data) - 1) {
        len = (uint32_t)sizeof(f->data) - 1;
    }
    memcpy(f->data, data, len);
    f->len = len;
    return 0;
}

/**
 * 走一遍 layout，把各页起点收集起来。
 * 页步长随内容混排而变（doc/07 §3.3：14 行 × 38 半角单位），**不能硬编码**。
 */
static uint32_t collect_page_starts(const uint8_t *text, uint32_t len, uint32_t *starts,
                                    uint32_t max_starts)
{
    uint32_t n = 0;
    uint32_t offset = 0;
    while (offset < len && n < max_starts) {
        uint32_t consumed = 0;
        int lines = 0;
        char out[2048];
        if (ui_data_layout_page(text + offset, len - offset, &consumed, &lines, out,
                                (uint32_t)sizeof(out)) != UI_DATA_OK) {
            break;
        }
        if (consumed == 0) {
            break;
        }
        starts[n++] = offset;
        offset += consumed;
    }
    return n;
}

static void test_prev_start(void)
{
    /* 900 个全角汉字 = 2700 字节。一页容量 = 14 行 × 19 全角字 = 266 字 = 798 字节，
     * 2700 字节足以跨 4 页。页起点不硬编码，用 layout 前向走一遍反推。
     * "中" = E4 B8 AD（三字节），直接写字节避免多字符常量。 */
    static const uint8_t han[3] = { 0xE4, 0xB8, 0xAD };
    uint8_t text[2700];
    int i;
    for (i = 0; i < 900; i++) {
        memcpy(text + i * 3, han, 3);
    }
    uint32_t len = 2700;

    uint32_t starts[16];
    uint32_t pages = collect_page_starts(text, len, starts, 16);
    CHECK(pages >= 4, "长文本应至少 4 页，实际 %u", (unsigned)pages);
    CHECK(starts[0] == 0, "第一页起点应为 0");

    /* 不变量 ①：一页消耗不得超过单次读缓冲（doc/07 §3） */
    for (i = 1; i < (int)pages; i++) {
        uint32_t consumed = 0;
        int lines = 0;
        char out[2048];
        uint32_t off = starts[i - 1];
        CHECK(ui_data_layout_page(text + off, len - off, &consumed, &lines, out,
                                  (uint32_t)sizeof(out)) == UI_DATA_OK,
              "第 %d 页 layout 应成功", i);
        CHECK(consumed <= UI_DATA_PAGE_BYTES,
              "第 %d 页消耗 %u 超过读缓冲 %d", i, (unsigned)consumed,
              (int)UI_DATA_PAGE_BYTES);
        CHECK(consumed % 3 == 0, "第 %d 页消耗应为 3 的倍数，实际 %u", i,
              (unsigned)consumed);
    }

    /* 不变量 ②：页起点必须落在 UTF-8 字符首字节上（多字节字符不被切坏） */
    for (i = 0; i < (int)pages; i++) {
        CHECK((text[starts[i]] & 0xC0) != 0x80,
              "第 %d 页起点 %u 落在字符中间", i + 1, (unsigned)starts[i]);
    }
}

/** 端到端：读卡版的 prev_page_offset 必须给出**真页起点**（不是近似值）。 */
static void test_prev_page_offset_real(void)
{
    static const uint8_t han[3] = { 0xE4, 0xB8, 0xAD };
    uint8_t text[2700];
    const char *path = "/books/prev.txt";
    int i;
    for (i = 0; i < 900; i++) {
        memcpy(text + i * 3, han, 3);
    }
    CHECK(fake_put(path, text, 2700) == 0, "假卡应能写入测试书");

    uint32_t starts[16];
    uint32_t pages = collect_page_starts(text, 2700, starts, 16);
    CHECK(pages >= 4, "长文本应至少 4 页，实际 %u", (unsigned)pages);

    for (i = 1; i < (int)pages; i++) {
        uint32_t prev = 999;
        CHECK(ui_data_prev_page_offset(path, starts[i], &prev) == UI_DATA_OK,
              "第 %d 页 prev_page_offset 应成功", i);
        CHECK(prev == starts[i - 1],
              "第 %d 页往前应是 %u，实际 %u", i, (unsigned)starts[i - 1],
              (unsigned)prev);
    }

    /* 第一页往前应是 0 */
    uint32_t prev = 999;
    CHECK(ui_data_prev_page_offset(path, 0, &prev) == UI_DATA_OK, "首页 prev 应成功");
    CHECK(prev == 0, "首页往前应是 0，实际 %u", (unsigned)prev);

    /* 混排内容（半角+全角）下页边界同样必须精确 */
    {
        uint8_t mix[1200];
        uint32_t m = 0;
        for (i = 0; i < 200; i++) {
            mix[m++] = 'A';
            memcpy(mix + m, han, 3);
            m += 3;
        }
        CHECK(fake_put("/books/mix.txt", mix, m) == 0, "假卡应能写入混排书");
        uint32_t mstarts[16];
        uint32_t mpages = collect_page_starts(mix, m, mstarts, 16);
        CHECK(mpages >= 2, "混排文本应至少 2 页，实际 %u", (unsigned)mpages);
        for (i = 1; i < (int)mpages; i++) {
            uint32_t mp = 999;
            CHECK(ui_data_prev_page_offset("/books/mix.txt", mstarts[i], &mp) == UI_DATA_OK,
                  "混排第 %d 页 prev 应成功", i);
            CHECK(mp == mstarts[i - 1],
                  "混排第 %d 页往前应是 %u，实际 %u", i, (unsigned)mstarts[i - 1],
                  (unsigned)mp);
        }
    }
}

/* ---------- 课表解析（doc/04 §3.2） ---------- */

static const char *kSchedule =
    "{"
    "\"schema_version\": 1,"
    "\"semester_start_date\": \"2026-09-01\","
    "\"weekend\": [6, 7],"
    "\"holidays\": [\"2026-11-26\"],"
    "\"period_times\": ["
    "{\"period\": 1, \"start\": \"08:00\", \"end\": \"08:45\"},"
    "{\"period\": 2, \"start\": \"08:55\", \"end\": \"09:40\"}"
    "],"
    "\"days\": {"
    "\"3\": ["
    "{\"period\": 1, \"subject\": \"语文\", \"week_type\": \"ALL\"},"
    "{\"period\": 2, \"subject\": \"数学\", \"week_type\": \"ODD\"}"
    "]"
    "},"
    "\"override_days\": {"
    "\"2026-11-25\": ["
    "{\"period\": 1, \"subject\": \"物理实验\", \"week_type\": \"ALL\"}"
    "]"
    "}"
    "}";

static void test_schedule(void)
{
    ui_data_lesson_t lessons[8];
    uint32_t count = 0;

    /* 2026-11-25 是周三，且有调课表 -> 取 override_days */
    ui_data_date_t d = { 2026, 11, 25 };
    CHECK(ui_data_parse_schedule(kSchedule, strlen(kSchedule), &d, lessons, 8, &count) == UI_DATA_OK,
          "解析调课日应成功");
    CHECK(count == 1, "调课日应有 1 节课，实际 %u", (unsigned)count);
    if (count == 1) {
        CHECK(strcmp(lessons[0].subject, "物理实验") == 0, "科目应为物理实验，实际 %s",
              lessons[0].subject);
        CHECK(strcmp(lessons[0].start, "08:00") == 0, "开始时间应为 08:00，实际 %s",
              lessons[0].start);
    }

    /* 2026-12-02 是周三（两周后）-> 取 days，ODD 的数学按教学周奇偶过滤 */
    ui_data_date_t d2 = { 2026, 12, 2 };
    count = 0;
    CHECK(ui_data_parse_schedule(kSchedule, strlen(kSchedule), &d2, lessons, 8, &count) == UI_DATA_OK,
          "解析普通周三应成功");
    /* semester 2026-09-01 到 2026-12-02 共 92 天 -> 第 14 周（偶数周）-> ODD 课被过滤 */
    CHECK(count == 1, "偶数周应只剩 1 节（ODD 被过滤），实际 %u", (unsigned)count);
    if (count == 1) {
        CHECK(strcmp(lessons[0].subject, "语文") == 0, "科目应为语文，实际 %s", lessons[0].subject);
    }

    /* 2026-11-26 是周四且在 holidays 里 -> 休息日 */
    ui_data_date_t d3 = { 2026, 11, 26 };
    count = 0;
    CHECK(ui_data_parse_schedule(kSchedule, strlen(kSchedule), &d3, lessons, 8, &count) == UI_DATA_OK,
          "解析节假日应成功");
    CHECK(count == 0, "节假日应 0 节课，实际 %u", (unsigned)count);

    /* 2026-11-28 是周六 -> weekend -> 休息日 */
    ui_data_date_t d4 = { 2026, 11, 28 };
    count = 0;
    CHECK(ui_data_parse_schedule(kSchedule, strlen(kSchedule), &d4, lessons, 8, &count) == UI_DATA_OK,
          "解析周末应成功");
    CHECK(count == 0, "周末应 0 节课，实际 %u", (unsigned)count);
}

/* ---------- 设置解析 ---------- */

static void test_settings(void)
{
    CHECK(ui_data_parse_focus_minutes("{\"focus_minutes\": 30}", 21, 25) == 30, "应解析出 30");
    CHECK(ui_data_parse_focus_minutes("{\"focus_minutes\": 30}", 21, 25) == 30, "应解析出 30");
    CHECK(ui_data_parse_focus_minutes("{}", 2, 25) == 25, "缺字段应回退默认");
    CHECK(ui_data_parse_focus_minutes("{\"focus_minutes\": 999}", 23, 25) == 60, "超上限应夹到 60");
    CHECK(ui_data_parse_focus_minutes("{\"focus_minutes\": 1}", 21, 25) == 5, "低于下限应夹到 5");
    CHECK(ui_data_parse_focus_minutes(NULL, 0, 25) == 25, "NULL 应回退默认");
}

/* ---------- 日期 ---------- */

static void test_date(void)
{
    ui_data_date_t d = { 0, 0, 0 };
    /* 不测「今天」（依赖主机时钟），只测接口不崩 */
    (void)ui_data_today(&d);
    (void)ui_data_today(NULL);
}

/* ---------- 城市读写（settings.json 的读-改-写） ---------- */

static void test_city(void)
{
    char city[64];

    /* 还没设置过城市时，读出来应是空串而不是报错 */
    memset(city, 'x', sizeof(city));
    CHECK(ui_data_city_get(city, sizeof(city)) == UI_DATA_OK, "未设置城市时读应成功");
    CHECK(city[0] == '\0', "未设置城市时应读到空串，实际 \"%s\"", city);

    /* 写入城市 */
    CHECK(ui_data_city_set("Shanghai") == UI_DATA_OK, "写城市应成功");
    memset(city, 0, sizeof(city));
    CHECK(ui_data_city_get(city, sizeof(city)) == UI_DATA_OK, "读回城市应成功");
    CHECK(strcmp(city, "Shanghai") == 0, "应读到 Shanghai，实际 \"%s\"", city);

    /* 读-改-写不能把别的字段弄丢（专注时长要保住） */
    int minutes = 0;
    CHECK(ui_data_focus_minutes_get(&minutes) == UI_DATA_OK, "读专注时长应成功");
    CHECK(minutes == 30, "写城市不应弄丢 focus_minutes，实际 %d", minutes);

    /* 改城市：专注时长仍在 */
    CHECK(ui_data_city_set("Beijing") == UI_DATA_OK, "改城市应成功");
    memset(city, 0, sizeof(city));
    CHECK(ui_data_city_get(city, sizeof(city)) == UI_DATA_OK, "读回新城市应成功");
    CHECK(strcmp(city, "Beijing") == 0, "应读到 Beijing，实际 \"%s\"", city);
    minutes = 0;
    CHECK(ui_data_focus_minutes_get(&minutes) == UI_DATA_OK, "读专注时长应成功");
    CHECK(minutes == 30, "改城市后 focus_minutes 应还在，实际 %d", minutes);

    /* 空城市要拒绝（不能把已有城市抹成空） */
    CHECK(ui_data_city_set("") == UI_DATA_ERR_INVALID, "空城市应被拒");
    CHECK(ui_data_city_set(NULL) == UI_DATA_ERR_INVALID, "NULL 城市应被拒");
    memset(city, 0, sizeof(city));
    CHECK(ui_data_city_get(city, sizeof(city)) == UI_DATA_OK, "读城市应成功");
    CHECK(strcmp(city, "Beijing") == 0, "被拒的写入不应改动原值，实际 \"%s\"", city);

    /* 参数不合法 */
    CHECK(ui_data_city_get(NULL, 10) == UI_DATA_ERR_INVALID, "NULL 缓冲应被拒");
    CHECK(ui_data_city_get(city, 0) == UI_DATA_ERR_INVALID, "零容量应被拒");
}

/* ---------- 天气缓存（离线兜底） ---------- */

static void test_weather_cache(void)
{
    char buf[256];
    uint32_t len = 0;

    /* 还没缓存过天气时，读应温和失败 */
    CHECK(ui_data_weather_cache_get(buf, sizeof(buf), &len) != UI_DATA_OK,
          "没有天气缓存时应失败而不是崩");

    /* 写入服务端下发的天气片段 */
    const char *frag = "{\"weather\":{\"temp\":8,\"text\":\"阴\"}}";
    CHECK(ui_data_weather_cache_set(frag, (uint32_t)strlen(frag)) == UI_DATA_OK,
          "写天气缓存应成功");

    memset(buf, 0, sizeof(buf));
    len = 0;
    CHECK(ui_data_weather_cache_get(buf, sizeof(buf), &len) == UI_DATA_OK, "读天气缓存应成功");
    CHECK(len == (uint32_t)strlen(frag), "长度应为 %u，实际 %u",
          (unsigned)strlen(frag), (unsigned)len);
    CHECK(strcmp(buf, frag) == 0, "内容应原样读回");

    /* 空内容要拒绝 */
    CHECK(ui_data_weather_cache_set(NULL, 4) == UI_DATA_ERR_INVALID, "NULL 内容应被拒");
    CHECK(ui_data_weather_cache_set(frag, 0) == UI_DATA_ERR_INVALID, "零长度应被拒");
    CHECK(ui_data_weather_cache_get(NULL, 10, &len) == UI_DATA_ERR_INVALID, "NULL 缓冲应被拒");
}

int main(void)
{
    test_utf8_align();
    test_layout();
    test_layout_no_split();
    test_prev_start();
    test_prev_page_offset_real();
    test_schedule();
    test_settings();
    test_date();

    /* ---------- 请求/应答通道（假存储任务） ---------- */
    {
        fake_file_t *f = fake_add("/config/settings.json");
        snprintf(f->data, sizeof(f->data), "{\"focus_minutes\": 45}");
        f->len = (uint32_t)strlen(f->data);

        int minutes = 0;
        CHECK(ui_data_focus_minutes_get(&minutes) == UI_DATA_OK, "读设置应成功");
        CHECK(minutes == 45, "应读到 45 分钟，实际 %d", minutes);

        CHECK(ui_data_focus_minutes_set(30) == UI_DATA_OK, "写设置应成功");
        minutes = 0;
        CHECK(ui_data_focus_minutes_get(&minutes) == UI_DATA_OK, "再读设置应成功");
        CHECK(minutes == 30, "写回后应读到 30 分钟，实际 %d", minutes);

        /* 读不存在的文件 -> 温和失败，不崩 */
        uint32_t size = 0;
        CHECK(ui_data_file_size("/config/nope.json", &size) == UI_DATA_ERR_NOT_FOUND,
              "不存在的文件应报 NOT_FOUND");
        CHECK(ui_data_file_exists("/config/settings.json") == UI_DATA_OK, "存在的文件应 OK");
        CHECK(ui_data_is_mounted() == 1, "假卡应处于挂载态");
    }

    /* ---------- 城市与天气缓存（依赖上面已建好的假 TF 卡） ---------- */
    test_city();
    test_weather_cache();

    printf("ui_data host tests: %d checks, %d failures\n", g_checks, g_fail);
    return (g_fail == 0) ? 0 : 1;
}
