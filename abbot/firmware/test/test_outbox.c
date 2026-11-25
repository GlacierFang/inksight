/**
 * test_outbox.c —— 离线队列（outbox）的主机侧单测
 *
 * 纯 C，主机侧可跑（不需要硬件、不需要 PlatformIO）。
 *
 * 验两件事：
 *   1. **纯逻辑层**：解析 / 序列化 / 入队淘汰 / 出队 / 过期与重试判定；
 *   2. **传输层**：索引读写走存储任务队列（假存储任务），原子写 + `.bak` 自愈。
 *
 * 假存储任务的做法照抄 `test_ui_data.c`：本文件自己实现
 * `hal_storage_req_queue` / `xQueueSend` / 信号量那几件套，
 * 模拟一块内存里的 TF 卡。
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"

#include "outbox.h"
#include "storage_io.h"

/* ============================================================
 * 假 TF 卡
 * ============================================================ */

#define FAKE_MAX_FILES 8
#define FAKE_FILE_MAX 8192

typedef struct {
    char name[STORAGE_PATH_MAX];
    bool used;
    uint32_t len;
    uint8_t data[FAKE_FILE_MAX];
} fake_file_t;

static fake_file_t g_files[FAKE_MAX_FILES];
static bool g_fake_mounted = true;

static fake_file_t *fake_find(const char *path)
{
    for (int i = 0; i < FAKE_MAX_FILES; i++) {
        if (g_files[i].used && strcmp(g_files[i].name, path) == 0) {
            return &g_files[i];
        }
    }
    return NULL;
}

static fake_file_t *fake_create(const char *path)
{
    fake_file_t *f = fake_find(path);
    if (f != NULL) {
        return f;
    }
    for (int i = 0; i < FAKE_MAX_FILES; i++) {
        if (!g_files[i].used) {
            g_files[i].used = true;
            snprintf(g_files[i].name, sizeof(g_files[i].name), "%s", path);
            g_files[i].len = 0;
            return &g_files[i];
        }
    }
    return NULL;
}

/** 假存储任务：处理一条请求（等价固件里 task_storage 的 switch）。 */
static void fake_service(storage_req_t *req)
{
    switch (req->op) {
    case 2: /* 删除文件 */
        {
            fake_file_t *f = fake_find(req->path);
            if (f != NULL) {
                f->used = false;
            }
        }
        break;
    case 5: /* 读文本（带应答） */
        {
            fake_file_t *f = fake_find(req->path);
            if (f == NULL) {
                req->io->result = -4; /* NOT_FOUND */
                break;
            }
            uint32_t n = f->len;
            if (n > req->io->buf_cap) {
                n = req->io->buf_cap;
            }
            memcpy(req->io->buf, f->data, n);
            if (n < req->io->buf_cap) {
                req->io->buf[n] = '\0';
            }
            req->io->out_len = n;
            req->io->result = 0;
        }
        break;
    case 6: /* 读区间 */
        {
            fake_file_t *f = fake_find(req->path);
            if (f == NULL) {
                req->io->result = -4;
                break;
            }
            if (req->offset > f->len) {
                req->io->result = -3;
                req->io->out_len = 0;
                break;
            }
            uint32_t avail = f->len - req->offset;
            uint32_t n = (req->io->buf_cap < avail) ? req->io->buf_cap : avail;
            memcpy(req->io->buf, f->data + req->offset, n);
            req->io->out_len = n;
            req->io->result = 0;
        }
        break;
    case 8: /* 取文件大小 */
        {
            fake_file_t *f = fake_find(req->path);
            if (f == NULL) {
                req->io->result = -4;
                break;
            }
            req->io->out_size = f->len;
            req->io->result = 0;
        }
        break;
    case 9: /* 原子写文本（数据取自 req.text） */
        {
            fake_file_t *f = fake_create(req->path);
            if (f == NULL) {
                req->io->result = -5;
                break;
            }
            uint32_t n = req->len;
            if (n > FAKE_FILE_MAX) {
                n = FAKE_FILE_MAX;
            }
            memcpy(f->data, req->text, n);
            f->len = n;
            req->io->result = 0;
        }
        break;
    case 12: /* 原子写文本（数据取自调用方缓冲 io->buf） */
        {
            fake_file_t *f = fake_create(req->path);
            if (f == NULL) {
                req->io->result = -5;
                break;
            }
            uint32_t n = req->io->buf_cap;
            if (n > FAKE_FILE_MAX) {
                n = FAKE_FILE_MAX;
            }
            memcpy(f->data, req->io->buf, n);
            f->len = n;
            req->io->result = 0;
        }
        break;
    case 11: /* 是否挂载 */
        req->io->mounted = g_fake_mounted ? 1u : 0u;
        req->io->result = 0;
        break;
    default:
        req->io->result = -6;
        break;
    }
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
    return (SemaphoreHandle_t)calloc(1, sizeof(fake_sem_t));
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

/* ============================================================
 * 断言
 * ============================================================ */

static int g_checks = 0;
static int g_fail = 0;

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

static void fake_reset(void)
{
    memset(g_files, 0, sizeof(g_files));
    g_fake_mounted = true;
}

/** 造一条标准条目。 */
static void make_entry(outbox_entry_t *e, uint32_t ts, uint8_t type, uint8_t retry)
{
    memset(e, 0, sizeof(*e));
    snprintf(e->task_id, sizeof(e->task_id), "%u", (unsigned)ts);
    e->type = type;
    e->status = OUTBOX_STATUS_PENDING;
    e->retry_count = retry;
    e->hint_level = 0;
    e->created_at = ts;
    snprintf(e->image_path, sizeof(e->image_path), "/outbox/task_%u.jpg", (unsigned)ts);
    snprintf(e->session_id, sizeof(e->session_id), "sess_%u", (unsigned)ts);
}

/* ============================================================
 * 纯逻辑层
 * ============================================================ */

static void test_parse(void)
{
    printf("[1] 索引解析（doc/04 §3.5 形状）\n");
    const char *json =
        "{\"schema_version\":1,\"tasks\":["
        "{\"task_id\":\"1795413001\",\"type\":\"SOLVE\",\"status\":\"pending\",\"retry_count\":2,"
        "\"created_at\":1795413001,\"hint_level\":1,"
        "\"image_path\":\"/outbox/task_1795413001.jpg\",\"pcm_path\":\"\","
        "\"session_id\":\"sess_1795413001\",\"context\":\"\"},"
        "{\"task_id\":\"1795414002\",\"type\":\"VOICE\",\"status\":\"retry\",\"retry_count\":0,"
        "\"created_at\":1795414002,\"hint_level\":0,"
        "\"image_path\":\"\",\"pcm_path\":\"/outbox/task_1795414002.pcm\","
        "\"session_id\":\"\",\"context\":\"READER|m7_geo|c008\"}]}";
    outbox_queue_t q;
    int n = outbox_parse(json, strlen(json), &q);
    CHECK(n == 2, "解析出 2 条，实际 %d", n);
    CHECK(q.count == 2, "count = 2，实际 %d", q.count);

    CHECK(strcmp(q.items[0].task_id, "1795413001") == 0, "第 1 条任务号");
    CHECK(q.items[0].type == OUTBOX_TYPE_SOLVE, "第 1 条类型 = 拍题");
    CHECK(q.items[0].status == OUTBOX_STATUS_PENDING, "第 1 条状态 = pending");
    CHECK(q.items[0].retry_count == 2, "第 1 条重试次数 = 2");
    CHECK(q.items[0].created_at == 1795413001u, "第 1 条创建时间");
    CHECK(q.items[0].hint_level == 1, "第 1 条提示档位");
    CHECK(strcmp(q.items[0].image_path, "/outbox/task_1795413001.jpg") == 0, "第 1 条图片路径");
    CHECK(q.items[0].pcm_path[0] == '\0', "第 1 条无音频路径");
    CHECK(strcmp(q.items[0].session_id, "sess_1795413001") == 0, "第 1 条会话号");

    CHECK(q.items[1].type == OUTBOX_TYPE_VOICE, "第 2 条类型 = 语音");
    CHECK(q.items[1].status == OUTBOX_STATUS_RETRY, "第 2 条状态 = retry");
    CHECK(strcmp(q.items[1].pcm_path, "/outbox/task_1795414002.pcm") == 0, "第 2 条音频路径");
    CHECK(strcmp(q.items[1].context, "READER|m7_geo|c008") == 0, "第 2 条上下文");
}

static void test_parse_empty_and_bad(void)
{
    printf("[2] 索引解析 —— 空与异常\n");
    outbox_queue_t q;

    int n = outbox_parse("{\"schema_version\":1,\"tasks\":[]}", 31, &q);
    CHECK(n == 0 && q.count == 0, "空队列解析出 0 条");

    n = outbox_parse("", 0, &q);
    CHECK(n == 0 && q.count == 0, "空串解析出 0 条");

    /* 格式不对时判为**无效**（而不是空队列）——
       两种情况必须分开：损坏的索引要能触发 .bak 回退，
       而真正空的队列会写成 `{"tasks": []}`，能被正常解析。 */
    n = outbox_parse("这不是 JSON", 9, &q);
    CHECK(n < 0, "垃圾输入判为无效，实际 %d", n);
    CHECK(q.count == 0, "垃圾输入不污染队列");
}

static void test_serialize_roundtrip(void)
{
    printf("[3] 索引序列化 —— 往返\n");
    outbox_queue_t q;
    memset(&q, 0, sizeof(q));
    q.count = 2;
    make_entry(&q.items[0], 1795413001u, OUTBOX_TYPE_SOLVE, 3);
    q.items[0].hint_level = 1;
    make_entry(&q.items[1], 1795414002u, OUTBOX_TYPE_VOICE, 0);
    q.items[1].image_path[0] = '\0';
    snprintf(q.items[1].pcm_path, sizeof(q.items[1].pcm_path),
             "/outbox/task_1795414002.pcm");
    snprintf(q.items[1].context, sizeof(q.items[1].context), "READER|m7_geo|c008");

    char buf[OUTBOX_INDEX_MAX + 1];
    int n = outbox_serialize(&q, buf, sizeof(buf));
    CHECK(n > 0, "序列化成功");
    CHECK(strstr(buf, "\"schema_version\":1") != NULL, "含 schema_version");
    CHECK(strstr(buf, "\"task_id\":\"1795413001\"") != NULL, "含任务号");
    CHECK(strstr(buf, "\"type\":\"SOLVE\"") != NULL, "含类型");
    CHECK(strstr(buf, "\"type\":\"VOICE\"") != NULL, "含类型（语音）");
    CHECK(strstr(buf, "\"retry_count\":3") != NULL, "含重试次数");

    outbox_queue_t back;
    int m = outbox_parse(buf, (size_t)n, &back);
    CHECK(m == 2, "往返后仍是 2 条，实际 %d", m);
    CHECK(back.items[0].retry_count == 3, "往返后重试次数保持");
    CHECK(back.items[0].created_at == 1795413001u, "往返后创建时间保持");
    CHECK(back.items[0].hint_level == 1, "往返后提示档位保持");
    CHECK(strcmp(back.items[0].image_path, q.items[0].image_path) == 0, "往返后图片路径保持");
    CHECK(strcmp(back.items[0].session_id, q.items[0].session_id) == 0, "往返后会话号保持");
    CHECK(back.items[1].type == OUTBOX_TYPE_VOICE, "往返后类型保持");
    CHECK(strcmp(back.items[1].context, "READER|m7_geo|c008") == 0, "往返后上下文保持");
}

static void test_serialize_full_load(void)
{
    printf("[4] 索引序列化 —— 满 10 条仍放得下\n");
    outbox_queue_t q;
    memset(&q, 0, sizeof(q));
    q.count = OUTBOX_MAX_TASKS;
    for (int i = 0; i < OUTBOX_MAX_TASKS; i++) {
        make_entry(&q.items[i], 1795413001u + (uint32_t)i,
                   (uint8_t)((i % 2) ? OUTBOX_TYPE_VOICE : OUTBOX_TYPE_SOLVE),
                   (uint8_t)i);
    }
    char buf[OUTBOX_INDEX_MAX + 1];
    int n = outbox_serialize(&q, buf, sizeof(buf));
    CHECK(n > 0, "满 10 条序列化成功，实际 %d 字节", n);
    CHECK(n <= OUTBOX_INDEX_MAX, "满 10 条不超过 %d 字节，实际 %d", OUTBOX_INDEX_MAX, n);

    outbox_queue_t back;
    int m = outbox_parse(buf, (size_t)n, &back);
    CHECK(m == OUTBOX_MAX_TASKS, "满 10 条往返后仍是 10 条，实际 %d", m);
    CHECK(back.items[9].retry_count == 9, "最后一条重试次数正确");
}

static void test_enqueue_and_evict(void)
{
    printf("[5] 入队与淘汰\n");
    outbox_queue_t q;
    memset(&q, 0, sizeof(q));
    int evicted = -1;

    for (int i = 0; i < OUTBOX_MAX_TASKS; i++) {
        outbox_entry_t e;
        make_entry(&e, 1000u + (uint32_t)i, OUTBOX_TYPE_SOLVE, 0);
        int idx = outbox_enqueue(&q, &e, &evicted);
        CHECK(idx == i, "第 %d 条入队下标正确", i);
        CHECK(evicted == -1, "未满时不淘汰");
    }
    CHECK(q.count == OUTBOX_MAX_TASKS, "队列满 10 条，实际 %d", q.count);

    /* 第 11 条：淘汰最旧的（第 0 条） */
    outbox_entry_t e;
    make_entry(&e, 2000u, OUTBOX_TYPE_SOLVE, 0);
    int idx = outbox_enqueue(&q, &e, &evicted);
    CHECK(evicted == 0, "淘汰的是最旧那条（下标 0），实际 %d", evicted);
    CHECK(idx == OUTBOX_MAX_TASKS - 1, "新条目下标 = 9，实际 %d", idx);
    CHECK(q.count == OUTBOX_MAX_TASKS, "淘汰后仍是 10 条，实际 %d", q.count);
    CHECK(strcmp(q.items[0].image_path, "/outbox/task_1001.jpg") == 0, "最旧的已让位");
    CHECK(strcmp(q.items[9].image_path, "/outbox/task_2000.jpg") == 0, "新的排在最后");
}

static void test_enqueue_rejects_empty(void)
{
    printf("[6] 入队 —— 拒绝没有关联文件的条目\n");
    outbox_queue_t q;
    memset(&q, 0, sizeof(q));
    outbox_entry_t e;
    memset(&e, 0, sizeof(e));
    int rc = outbox_enqueue(&q, &e, NULL);
    CHECK(rc < 0, "没有关联文件的条目被拒，实际 %d", rc);
    CHECK(q.count == 0, "被拒的条目没进队列");
}

static void test_remove(void)
{
    printf("[7] 出队\n");
    outbox_queue_t q;
    memset(&q, 0, sizeof(q));
    for (int i = 0; i < 3; i++) {
        outbox_entry_t e;
        make_entry(&e, 1000u + (uint32_t)i, OUTBOX_TYPE_SOLVE, 0);
        outbox_enqueue(&q, &e, NULL);
    }
    outbox_remove(&q, 1);
    CHECK(q.count == 2, "出队后剩 2 条，实际 %d", q.count);
    CHECK(strcmp(q.items[0].image_path, "/outbox/task_1000.jpg") == 0, "第 1 条没动");
    CHECK(strcmp(q.items[1].image_path, "/outbox/task_1002.jpg") == 0, "第 3 条前移到第 2 位");

    outbox_remove(&q, 99); /* 越界不应崩 */
    CHECK(q.count == 2, "越界出队不改变队列");
}

static void test_expire_policy(void)
{
    printf("[8] 过期与重试策略\n");
    outbox_queue_t q;
    memset(&q, 0, sizeof(q));

    /* 条目 A：重试次数已到上限 */
    outbox_entry_t a;
    make_entry(&a, 1795593315u, OUTBOX_TYPE_SOLVE, OUTBOX_MAX_RETRY);
    /* 条目 B：正常 */
    outbox_entry_t b;
    make_entry(&b, 1795593315u, OUTBOX_TYPE_SOLVE, 0);
    /* 条目 C：很旧 */
    outbox_entry_t c;
    make_entry(&c, 1795593315u - (uint32_t)OUTBOX_MAX_AGE_SEC - 60u, OUTBOX_TYPE_VOICE, 0);

    outbox_enqueue(&q, &a, NULL);
    outbox_enqueue(&q, &b, NULL);
    outbox_enqueue(&q, &c, NULL);

    int dropped[8];
    uint32_t now = 1795593315u;
    int n = outbox_select_expired(&q, now, dropped, 8);
    CHECK(n == 2, "该丢弃 2 条（重试超限 + 过期），实际 %d", n);

    /* 时钟不可信时不做过期判定 */
    dropped[0] = -1;
    dropped[1] = -1;
    int m = outbox_select_expired(&q, 1000u, dropped, 8);
    CHECK(m == 1, "时钟没校准时只按重试上限挑（1 条），实际 %d", m);
    CHECK(dropped[0] == 0, "挑中的是重试超限那条");
}

static void test_media_path(void)
{
    printf("[9] 关联媒体路径\n");
    char buf[OUTBOX_PATH_MAX];
    int n = outbox_make_media_path(buf, sizeof(buf), 1795593315u, 42u, ".jpg");
    CHECK(n > 0, "生成路径成功");
    CHECK(strcmp(buf, "/outbox/task_1795593315_42.jpg") == 0, "路径内容正确：%s", buf);

    n = outbox_make_media_path_by_id(buf, sizeof(buf), "1795413001", ".pcm");
    CHECK(n > 0, "按任务号生成路径成功");
    CHECK(strcmp(buf, "/outbox/task_1795413001.pcm") == 0, "路径内容正确：%s", buf);

    char small[8];
    CHECK(outbox_make_media_path(small, sizeof(small), 1u, 2u, ".jpg") < 0,
          "缓冲不够时报错而不是溢出");
}

/* ============================================================
 * 传输层
 * ============================================================ */

static void test_store_and_load(void)
{
    printf("[10] 索引存取（走存储队列）\n");
    fake_reset();

    outbox_queue_t q;
    memset(&q, 0, sizeof(q));
    outbox_entry_t e;
    make_entry(&e, 1795593315u, OUTBOX_TYPE_SOLVE, 1);
    outbox_enqueue(&q, &e, NULL);

    int rc = outbox_store(&q);
    CHECK(rc == OUTBOX_OK, "写索引成功，rc=%d", rc);
    CHECK(fake_find(OUTBOX_INDEX_PATH) != NULL, "索引文件已落假卡");

    outbox_queue_t back;
    int n = outbox_load(&back);
    CHECK(n == 1, "读回 1 条，实际 %d", n);
    CHECK(strcmp(back.items[0].image_path, e.image_path) == 0, "读回路径一致");
    CHECK(back.items[0].retry_count == 1, "读回重试次数一致");
    CHECK(strcmp(back.items[0].task_id, e.task_id) == 0, "读回任务号一致");
}

static void test_load_missing_is_empty(void)
{
    printf("[11] 索引缺失 -> 空队列\n");
    fake_reset();
    outbox_queue_t q;
    int n = outbox_load(&q);
    CHECK(n == 0 && q.count == 0, "索引不存在时返回空队列");
}

static void test_load_corrupt_falls_back_to_bak(void)
{
    printf("[12] 索引损坏 -> 回退 .bak\n");
    fake_reset();

    /* 先写一份好的 .bak */
    outbox_queue_t q;
    memset(&q, 0, sizeof(q));
    outbox_entry_t e;
    make_entry(&e, 1795593315u, OUTBOX_TYPE_SOLVE, 0);
    outbox_enqueue(&q, &e, NULL);
    char buf[OUTBOX_INDEX_MAX + 1];
    int n = outbox_serialize(&q, buf, sizeof(buf));
    fake_file_t *bak = fake_create(OUTBOX_INDEX_BAK_PATH);
    memcpy(bak->data, buf, (size_t)n);
    bak->len = (uint32_t)n;

    /* 正式文件写坏内容 */
    fake_file_t *main_f = fake_create(OUTBOX_INDEX_PATH);
    const char *garbage = "{\"tasks\":[garbage";
    memcpy(main_f->data, garbage, strlen(garbage));
    main_f->len = (uint32_t)strlen(garbage);

    outbox_queue_t back;
    int m = outbox_load(&back);
    CHECK(m == 1, "损坏时回退 .bak 读出 1 条，实际 %d", m);
    CHECK(strcmp(back.items[0].image_path, e.image_path) == 0, "回退读到的是 .bak 那份");
    CHECK(fake_find(OUTBOX_INDEX_PATH) == NULL, "坏掉的正式文件被清掉了");
}

static void test_delete_and_read_media(void)
{
    printf("[13] 关联文件删与读\n");
    fake_reset();

    fake_file_t *f = fake_create("/outbox/task_1.jpg");
    const char *payload = "JPEGDATA";
    memcpy(f->data, payload, strlen(payload));
    f->len = (uint32_t)strlen(payload);

    uint8_t *buf = NULL;
    uint32_t len = 0;
    int rc = outbox_read_file("/outbox/task_1.jpg", &buf, &len);
    CHECK(rc == OUTBOX_OK, "读关联文件成功，rc=%d", rc);
    CHECK(buf != NULL && len == strlen(payload), "读出的长度正确");
    CHECK(buf != NULL && memcmp(buf, payload, len) == 0, "读出的内容正确");
    free(buf);

    rc = outbox_delete_file("/outbox/task_1.jpg");
    CHECK(rc == OUTBOX_OK, "删关联文件成功");
    CHECK(fake_find("/outbox/task_1.jpg") == NULL, "文件已删");

    rc = outbox_read_file("/outbox/task_1.jpg", &buf, &len);
    CHECK(rc != OUTBOX_OK, "删掉后读不到");
}

int main(void)
{
    printf("=== abbot 离线队列（outbox）测试 ===\n");
    test_parse();
    test_parse_empty_and_bad();
    test_serialize_roundtrip();
    test_serialize_full_load();
    test_enqueue_and_evict();
    test_enqueue_rejects_empty();
    test_remove();
    test_expire_policy();
    test_media_path();
    test_store_and_load();
    test_load_missing_is_empty();
    test_load_corrupt_falls_back_to_bak();
    test_delete_and_read_media();
    printf("\n=== %d checks, %d failures ===\n", g_checks, g_fail);
    return g_fail == 0 ? 0 : 1;
}
