/**
 * outbox.c —— 离线待问队列（TF 卡索引 + 关联媒体文件）
 *
 * 契约：abbot/doc/04 §2.1 目录树、§3.5 `/outbox/queue.json` 队列契约、
 *        §2.3 防掉电原子写入、PLAN-pair-outbox.md 第 2 条。
 *
 * 分两层：
 *   - **纯逻辑层**（解析 / 序列化 / 入队 / 出队 / 过期判定）：不碰任何 IO，
 *     主机侧可单测（`test/test_outbox.c`）。
 *   - **传输层**（读索引 / 写索引 / 删文件 / 读媒体文件）：走
 *     `hal_storage_req_queue()` 请求/应答，**不碰 SPI、不直接调 hal_storage_***。
 *
 * 为什么不复用 `ui_data.c` 的传输层：那是 UI 数据读取层的契约（`ui_data.h`），
 * 网络层去 include UI 头会把两层耦在一起。本模块自带一小段请求/应答样板，
 * 换来的是**两个模块各自独立可测**、层次边界清楚。
 *
 * 为什么 TF 卡访问要走队列：doc/02 §4.2 规定 TF 卡由 task_storage 单线程串行化，
 * 别的任务直接读卡会与它抢 SD_MMC 总线。
 */

#include "outbox.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "storage_io.h"

/* 请求应答超时：TF 卡读一个小文件正常在几十毫秒内完成，给 2 秒余量。 */
#define OUTBOX_TIMEOUT_MS 2000

/* 关联媒体文件大小上限（防御异常大文件导致 PSRAM 吃紧）。 */
#define OUTBOX_MEDIA_MAX (512 * 1024)

/* ============================================================
 * 纯逻辑层
 * ============================================================ */

/**
 * 在 [s,e) 里找 "key" 对应的值，返回值区间。
 * 字符串值返回的是**引号内**的内容（不含引号）；标量值返回原文。
 */
static int json_find_value(const char *s, const char *e, const char *key,
                           const char **out_start, const char **out_end)
{
    size_t klen = strlen(key);
    const char *p;
    for (p = s; p + klen + 2 < e; p++) {
        if (*p != '"') {
            continue;
        }
        if (memcmp(p + 1, key, klen) != 0) {
            continue;
        }
        if (p[1 + klen] != '"') {
            continue;
        }
        const char *q = p + 2 + klen;
        while (q < e && (*q == ' ' || *q == '\t' || *q == '\n' || *q == '\r')) {
            q++;
        }
        if (q >= e || *q != ':') {
            continue;
        }
        q++;
        while (q < e && (*q == ' ' || *q == '\t' || *q == '\n' || *q == '\r')) {
            q++;
        }
        if (q >= e) {
            return -1;
        }
        if (*q == '"') {
            q++;
            const char *start = q;
            while (q < e && *q != '"') {
                if (*q == '\\' && q + 1 < e) {
                    q++;   /* 跳过被转义的那个字符 */
                }
                q++;
            }
            if (q >= e) {
                return -1;
            }
            *out_start = start;
            *out_end = q;
            return 0;
        }
        const char *start = q;
        while (q < e && *q != ',' && *q != '}' && *q != ']') {
            q++;
        }
        *out_start = start;
        *out_end = q;
        return 0;
    }
    return -1;
}

/**
 * 在 [s,e) 里找 `"key"` 对应的**数组**值，返回数组内部区间（不含方括号）。
 *
 * 为什么要单独一个：通用取值函数碰到 `[` 会当成标量值（遇到 `]` 就停），
 * 而 tasks 是数组，得先定位到配对的 `]`。
 */
static int json_find_array(const char *s, const char *e, const char *key,
                           const char **out_start, const char **out_end)
{
    size_t klen = strlen(key);
    const char *p;
    for (p = s; p + klen + 2 < e; p++) {
        if (*p != '"') {
            continue;
        }
        if (memcmp(p + 1, key, klen) != 0) {
            continue;
        }
        if (p[1 + klen] != '"') {
            continue;
        }
        const char *q = p + 2 + klen;
        while (q < e && (*q == ' ' || *q == '\t' || *q == '\n' || *q == '\r')) {
            q++;
        }
        if (q >= e || *q != ':') {
            continue;
        }
        q++;
        while (q < e && (*q == ' ' || *q == '\t' || *q == '\n' || *q == '\r')) {
            q++;
        }
        if (q >= e || *q != '[') {
            continue;
        }
        q++;
        const char *start = q;
        int depth = 1;
        while (q < e) {
            if (*q == '[') {
                depth++;
            } else if (*q == ']') {
                depth--;
                if (depth == 0) {
                    break;
                }
            }
            q++;
        }
        if (q >= e) {
            return -1;
        }
        *out_start = start;
        *out_end = q;
        return 0;
    }
    return -1;
}

static long json_number(const char *s, const char *e, long fallback)
{
    if (s == NULL || e == NULL || s >= e) {
        return fallback;
    }
    char tmp[24];
    size_t n = (size_t)(e - s);
    if (n == 0 || n >= sizeof(tmp)) {
        return fallback;
    }
    memcpy(tmp, s, n);
    tmp[n] = '\0';
    return strtol(tmp, NULL, 10);
}

/** 把 [s,e) 的字符串内容拷进定长缓冲（超长截断）。 */
static void copy_str(char *dst, size_t cap, const char *s, const char *e)
{
    if (dst == NULL || cap == 0) {
        return;
    }
    size_t n = 0;
    if (s != NULL && e != NULL && e > s) {
        n = (size_t)(e - s);
        if (n >= cap) {
            n = cap - 1;
        }
        memcpy(dst, s, n);
    }
    dst[n] = '\0';
}

/** 类型字段的字符串 -> 内部枚举。 */
static uint8_t parse_type(const char *s, const char *e)
{
    if (s == NULL || e == NULL || e <= s) {
        return OUTBOX_TYPE_SOLVE;
    }
    size_t n = (size_t)(e - s);
    if (n == 5 && memcmp(s, "SOLVE", 5) == 0) {
        return OUTBOX_TYPE_SOLVE;
    }
    if (n == 5 && memcmp(s, "VOICE", 5) == 0) {
        return OUTBOX_TYPE_VOICE;
    }
    return OUTBOX_TYPE_SOLVE;
}

/** 内部枚举 -> 类型字段的字符串。 */
static const char *type_name(uint8_t type)
{
    return (type == OUTBOX_TYPE_VOICE) ? "VOICE" : "SOLVE";
}

/** 解析一个条目对象（`{...}` 内部，不含外层大括号）。 */
static void parse_entry(const char *s, const char *e, outbox_entry_t *entry)
{
    const char *vs = NULL;
    const char *ve = NULL;

    memset(entry, 0, sizeof(*entry));
    entry->type = OUTBOX_TYPE_SOLVE;
    entry->status = OUTBOX_STATUS_PENDING;

    if (json_find_value(s, e, "task_id", &vs, &ve) == 0) {
        copy_str(entry->task_id, sizeof(entry->task_id), vs, ve);
    }
    if (json_find_value(s, e, "type", &vs, &ve) == 0) {
        entry->type = parse_type(vs, ve);
    }
    if (json_find_value(s, e, "status", &vs, &ve) == 0) {
        entry->status = (vs != NULL && ve != NULL && (ve - vs) == 5 &&
                         memcmp(vs, "retry", 5) == 0) ? OUTBOX_STATUS_RETRY
                                                     : OUTBOX_STATUS_PENDING;
    }
    if (json_find_value(s, e, "retry_count", &vs, &ve) == 0) {
        long v = json_number(vs, ve, 0);
        if (v < 0) {
            v = 0;
        }
        if (v > 255) {
            v = 255;
        }
        entry->retry_count = (uint8_t)v;
    }
    if (json_find_value(s, e, "created_at", &vs, &ve) == 0) {
        entry->created_at = (uint32_t)json_number(vs, ve, 0);
    }
    if (json_find_value(s, e, "hint_level", &vs, &ve) == 0) {
        long v = json_number(vs, ve, 0);
        if (v < 0) {
            v = 0;
        }
        if (v > 255) {
            v = 255;
        }
        entry->hint_level = (uint8_t)v;
    }
    if (json_find_value(s, e, "image_path", &vs, &ve) == 0) {
        copy_str(entry->image_path, sizeof(entry->image_path), vs, ve);
    }
    if (json_find_value(s, e, "pcm_path", &vs, &ve) == 0) {
        copy_str(entry->pcm_path, sizeof(entry->pcm_path), vs, ve);
    }
    if (json_find_value(s, e, "session_id", &vs, &ve) == 0) {
        copy_str(entry->session_id, sizeof(entry->session_id), vs, ve);
    }
    if (json_find_value(s, e, "context", &vs, &ve) == 0) {
        copy_str(entry->context, sizeof(entry->context), vs, ve);
    }
}

int outbox_parse(const char *json, size_t len, outbox_queue_t *out)
{
    if (json == NULL || out == NULL) {
        return OUTBOX_ERR_INVALID;
    }
    memset(out, 0, sizeof(*out));
    if (len == 0) {
        return 0;
    }

    const char *end = json + len;
    const char *vs = NULL;
    const char *ve = NULL;
    if (json_find_array(json, end, "tasks", &vs, &ve) != 0) {
        /* 没有可识别的 tasks 数组：判为**无效**（而不是当成空队列）。
           区分这两种情况很重要 —— 损坏的索引要能触发 .bak 回退，
           而真正空的队列会写成 `{"tasks": []}`，能被正常解析。 */
        return OUTBOX_ERR_INVALID;
    }

    const char *p = vs;
    while (p < ve) {
        if (*p != '{') {
            p++;
            continue;
        }
        const char *obj_end = p + 1;
        while (obj_end < ve && *obj_end != '}') {
            obj_end++;
        }
        if (obj_end >= ve) {
            break;
        }
        if (out->count < OUTBOX_MAX_TASKS) {
            parse_entry(p + 1, obj_end, &out->items[out->count]);
            out->count++;
        }
        p = obj_end + 1;
    }
    return out->count;
}

/** 写 JSON 字符串值（含转义）。 */
static int json_put_string(char *dst, size_t cap, size_t pos, const char *s)
{
    if (pos >= cap) {
        return -1;
    }
    dst[pos++] = '"';
    for (; s != NULL && *s != '\0'; s++) {
        char c = *s;
        if (c == '"' || c == '\\') {
            if (pos + 2 >= cap) {
                return -1;
            }
            dst[pos++] = '\\';
            dst[pos++] = c;
        } else {
            if (pos + 1 >= cap) {
                return -1;
            }
            dst[pos++] = c;
        }
    }
    if (pos + 1 >= cap) {
        return -1;
    }
    dst[pos++] = '"';
    return (int)pos;
}

/** 追加 `"key":` （含前导逗号可选）。 */
static int json_put_key(char *dst, size_t cap, size_t pos, const char *key, int leading_comma)
{
    size_t klen = strlen(key);
    if (pos + klen + 2 >= cap) {
        return -1;
    }
    if (leading_comma) {
        dst[pos++] = ',';
    }
    dst[pos++] = '"';
    memcpy(dst + pos, key, klen);
    pos += klen;
    dst[pos++] = '"';
    dst[pos++] = ':';
    return (int)pos;
}

/** 追加 `"key":"value"`。 */
static int json_put_str_field(char *dst, size_t cap, size_t pos, const char *key,
                              const char *value, int leading_comma)
{
    int p = json_put_key(dst, cap, pos, key, leading_comma);
    if (p < 0) {
        return -1;
    }
    return json_put_string(dst, cap, (size_t)p, value);
}

int outbox_serialize(const outbox_queue_t *q, char *out, size_t cap)
{
    if (q == NULL || out == NULL || cap < 2) {
        return OUTBOX_ERR_INVALID;
    }
    size_t pos = 0;
    const char *prefix = "{\"schema_version\":1,\"tasks\":[";
    size_t plen = strlen(prefix);
    if (plen + 2 >= cap) {
        return OUTBOX_ERR_NO_SPACE;
    }
    memcpy(out, prefix, plen);
    pos = plen;

    for (int i = 0; i < q->count; i++) {
        const outbox_entry_t *e = &q->items[i];
        int first = (i == 0);
        int p;

        /* 键名与短标量值（task_id / type / status / 三个数字） */
        char head[192];
        int n = snprintf(head, sizeof(head), "%s{\"task_id\":\"%s\",\"type\":\"%s\",\"status\":\"%s\",\"retry_count\":%u,\"created_at\":%u,\"hint_level\":%u",
                         first ? "" : ",", e->task_id, type_name(e->type),
                         (e->status == OUTBOX_STATUS_RETRY) ? "retry" : "pending",
                         (unsigned)e->retry_count, (unsigned)e->created_at,
                         (unsigned)e->hint_level);
        if (n < 0 || pos + (size_t)n + 1 >= cap) {
            return OUTBOX_ERR_NO_SPACE;
        }
        memcpy(out + pos, head, (size_t)n);
        pos += (size_t)n;

        /* 四个路径/上下文字段 —— **每个都要带自己的键名** */
        p = json_put_str_field(out, cap, pos, "image_path", e->image_path, 1);
        if (p < 0) {
            return OUTBOX_ERR_NO_SPACE;
        }
        pos = (size_t)p;

        p = json_put_str_field(out, cap, pos, "pcm_path", e->pcm_path, 1);
        if (p < 0) {
            return OUTBOX_ERR_NO_SPACE;
        }
        pos = (size_t)p;

        p = json_put_str_field(out, cap, pos, "session_id", e->session_id, 1);
        if (p < 0) {
            return OUTBOX_ERR_NO_SPACE;
        }
        pos = (size_t)p;

        p = json_put_str_field(out, cap, pos, "context", e->context, 1);
        if (p < 0) {
            return OUTBOX_ERR_NO_SPACE;
        }
        pos = (size_t)p;

        if (pos + 2 >= cap) {
            return OUTBOX_ERR_NO_SPACE;
        }
        out[pos++] = '}';
    }

    if (pos + 3 >= cap) {
        return OUTBOX_ERR_NO_SPACE;
    }
    out[pos++] = ']';
    out[pos++] = '}';
    out[pos] = '\0';
    return (int)pos;
}

int outbox_enqueue(outbox_queue_t *q, const outbox_entry_t *entry, int *out_evicted)
{
    if (q == NULL || entry == NULL) {
        return OUTBOX_ERR_INVALID;
    }
    if (entry->image_path[0] == '\0' && entry->pcm_path[0] == '\0') {
        return OUTBOX_ERR_INVALID;   /* 没有关联文件的条目没有补传价值 */
    }
    if (out_evicted != NULL) {
        *out_evicted = -1;
    }
    if (q->count >= OUTBOX_MAX_TASKS) {
        /* 满了：淘汰最旧的一条（第 0 条 —— 队列按入队顺序排列）。
           调用方负责删掉那条的关联媒体文件（doc/04 §3.5 清理铁律）。 */
        int evicted = 0;
        outbox_remove(q, evicted);
        if (out_evicted != NULL) {
            *out_evicted = evicted;
        }
    }
    q->items[q->count] = *entry;
    q->items[q->count].image_path[OUTBOX_PATH_MAX - 1] = '\0';
    q->items[q->count].pcm_path[OUTBOX_PATH_MAX - 1] = '\0';
    return q->count++;
}

void outbox_remove(outbox_queue_t *q, int index)
{
    if (q == NULL || index < 0 || index >= q->count) {
        return;
    }
    for (int i = index; i < q->count - 1; i++) {
        q->items[i] = q->items[i + 1];
    }
    if (q->count > 0) {
        q->count--;
    }
    memset(&q->items[q->count], 0, sizeof(q->items[q->count]));
}

int outbox_select_expired(const outbox_queue_t *q, uint32_t now_ts,
                          int *out_dropped, int max_dropped)
{
    if (q == NULL) {
        return OUTBOX_ERR_INVALID;
    }
    int found = 0;
    /* 时钟没校过时（now_ts 很小）不做过期判定 —— 否则一开机就把队列全清了。 */
    int clock_ok = (now_ts >= OUTBOX_TIME_TRUSTED_MIN);
    for (int i = 0; i < q->count; i++) {
        const outbox_entry_t *e = &q->items[i];
        int over_retry = (e->retry_count >= OUTBOX_MAX_RETRY);
        int too_old = 0;
        if (clock_ok && e->created_at > 0 &&
            (uint32_t)(now_ts - e->created_at) > (uint32_t)OUTBOX_MAX_AGE_SEC) {
            too_old = 1;
        }
        if (!over_retry && !too_old) {
            continue;
        }
        if (out_dropped != NULL && found < max_dropped) {
            out_dropped[found] = i;
        }
        found++;
    }
    return found;
}

int outbox_make_media_path(char *out, size_t cap, uint32_t unix_ts, uint32_t ms,
                           const char *ext)
{
    if (out == NULL || cap == 0 || ext == NULL) {
        return OUTBOX_ERR_INVALID;
    }
    int n = snprintf(out, cap, "%stask_%u_%u%s", OUTBOX_DIR, (unsigned)unix_ts,
                     (unsigned)ms, ext);
    if (n < 0) {
        return OUTBOX_ERR_INVALID;
    }
    if ((size_t)n >= cap) {
        return OUTBOX_ERR_NO_SPACE;
    }
    return n;
}

int outbox_make_media_path_by_id(char *out, size_t cap, const char *task_id,
                                 const char *ext)
{
    if (out == NULL || cap == 0 || task_id == NULL || ext == NULL) {
        return OUTBOX_ERR_INVALID;
    }
    int n = snprintf(out, cap, "%stask_%s%s", OUTBOX_DIR, task_id, ext);
    if (n < 0) {
        return OUTBOX_ERR_INVALID;
    }
    if ((size_t)n >= cap) {
        return OUTBOX_ERR_NO_SPACE;
    }
    return n;
}

/* ============================================================
 * 传输层：走存储任务队列
 * ============================================================ */

/** 投递一条存储请求并等回执。 */
static int outbox_call(uint8_t op, const char *path, uint32_t offset, uint32_t len,
                       const char *text, size_t text_len, storage_io_t *io)
{
    QueueHandle_t q = hal_storage_req_queue();
    if (q == NULL) {
        return OUTBOX_ERR_UNSUPPORTED;
    }
    SemaphoreHandle_t done = xSemaphoreCreateBinary();
    if (done == NULL) {
        return OUTBOX_ERR_NO_SPACE;
    }

    storage_req_t req;
    memset(&req, 0, sizeof(req));
    req.op = op;
    req.offset = offset;
    req.len = len;
    req.io = io;
    req.reply = done;
    if (path != NULL) {
        strncpy(req.path, path, sizeof(req.path) - 1);
    }
    if (text != NULL && text_len > 0) {
        /*
         * 索引比 1KB 大（10 条满载约 2.2KB），放不进 `req.text`。
         * op=12（WRITE_TEXT_BUF）从调用方的缓冲取数据 —— 队列项本身不放大，
         * 这是「队列大文件」的正解。
         */
        if (op != 12 /* WRITE_TEXT_BUF */) {
            if (text_len > sizeof(req.text)) {
                vSemaphoreDelete(done);
                return OUTBOX_ERR_NO_SPACE;
            }
            memcpy(req.text, text, text_len);
        }
    }

    if (xQueueSend(q, &req, 0) != pdTRUE) {
        vSemaphoreDelete(done);
        return OUTBOX_ERR_NO_SPACE;
    }
    if (xSemaphoreTake(done, pdMS_TO_TICKS(OUTBOX_TIMEOUT_MS)) != pdTRUE) {
        /* 超时：信号量交给存储任务给，这里不删 —— 删掉会踩它正在写的对象。 */
        return OUTBOX_ERR_IO;
    }
    vSemaphoreDelete(done);
    return (int)io->result;
}

/** 读一个文本文件到调用方缓冲。 */
static int read_text_file(const char *path, char *buf, uint32_t cap, uint32_t *out_len)
{
    storage_io_t io;
    memset(&io, 0, sizeof(io));
    io.buf = (uint8_t *)buf;
    io.buf_cap = cap;
    int rc = outbox_call(5 /* READ_TEXT_IO */, path, 0, 0, NULL, 0, &io);
    if (rc == OUTBOX_OK) {
        if (out_len != NULL) {
            *out_len = io.out_len;
        }
        return OUTBOX_OK;
    }
    return rc;
}

int outbox_load(outbox_queue_t *q)
{
    if (q == NULL) {
        return OUTBOX_ERR_INVALID;
    }
    memset(q, 0, sizeof(*q));

    char *buf = (char *)malloc(OUTBOX_INDEX_MAX + 1);
    if (buf == NULL) {
        return OUTBOX_ERR_NO_SPACE;
    }
    uint32_t len = 0;
    int rc = read_text_file(OUTBOX_INDEX_PATH, buf, (uint32_t)OUTBOX_INDEX_MAX, &len);
    if (rc == OUTBOX_OK) {
        int n = outbox_parse(buf, len, q);
        if (n >= 0) {
            free(buf);
            return n;
        }
        /* 正式文件内容坏了 —— 回退 .bak（doc/04 §2.3 的自愈精神）。 */
        memset(q, 0, sizeof(*q));
    }

    /* 正式文件缺失或坏了：试 .bak。
     * 注意 `hal_storage_read_text` 在正式文件缺失时会自动把 .bak 改回正式文件，
     * 所以走到这里通常意味着正式文件存在但内容坏 —— 这时把坏文件删掉，
     * 让下一次 load 走「缺失 → .bak 恢复」那条路。 */
    uint32_t bak_len = 0;
    int brc = read_text_file(OUTBOX_INDEX_BAK_PATH, buf, (uint32_t)OUTBOX_INDEX_MAX, &bak_len);
    if (brc != OUTBOX_OK) {
        /* 两条都没读到：当作空队列（首次开机就是这样）。 */
        memset(q, 0, sizeof(*q));
        free(buf);
        return 0;
    }
    int n = outbox_parse(buf, bak_len, q);
    if (n < 0) {
        memset(q, 0, sizeof(*q));
        free(buf);
        return 0;
    }
    /* 坏掉的正式文件删掉，别让它下次继续挡在 .bak 前面。 */
    outbox_delete_file(OUTBOX_INDEX_PATH);
    free(buf);
    return n;
}

int outbox_store(const outbox_queue_t *q)
{
    if (q == NULL) {
        return OUTBOX_ERR_INVALID;
    }
    char *buf = (char *)malloc(OUTBOX_INDEX_MAX + 1);
    if (buf == NULL) {
        return OUTBOX_ERR_NO_SPACE;
    }
    int n = outbox_serialize(q, buf, OUTBOX_INDEX_MAX + 1);
    if (n < 0) {
        free(buf);
        return n;
    }
    storage_io_t io;
    memset(&io, 0, sizeof(io));
    io.buf = (uint8_t *)buf;
    io.buf_cap = (uint32_t)n;
    /* op=12 是原子写文本，数据取自调用方缓冲（索引比 1KB 大）。 */
    int rc = outbox_call(12 /* WRITE_TEXT_BUF */, OUTBOX_INDEX_PATH, 0, (uint32_t)n,
                         buf, (size_t)n, &io);
    free(buf);
    return rc;
}

int outbox_delete_file(const char *path)
{
    if (path == NULL || path[0] == '\0') {
        return OUTBOX_ERR_INVALID;
    }
    storage_io_t io;
    memset(&io, 0, sizeof(io));
    return outbox_call(2 /* DELETE */, path, 0, 0, NULL, 0, &io);
}

int outbox_file_size(const char *path, uint32_t *out_size)
{
    if (path == NULL || out_size == NULL) {
        return OUTBOX_ERR_INVALID;
    }
    storage_io_t io;
    memset(&io, 0, sizeof(io));
    int rc = outbox_call(8 /* GET_SIZE */, path, 0, 0, NULL, 0, &io);
    if (rc != OUTBOX_OK) {
        return rc;
    }
    *out_size = io.out_size;
    return OUTBOX_OK;
}

int outbox_read_file(const char *path, uint8_t **out_buf, uint32_t *out_len)
{
    if (path == NULL || out_buf == NULL || out_len == NULL) {
        return OUTBOX_ERR_INVALID;
    }
    *out_buf = NULL;
    *out_len = 0;

    /* 先问大小 */
    storage_io_t io;
    memset(&io, 0, sizeof(io));
    int rc = outbox_call(8 /* GET_SIZE */, path, 0, 0, NULL, 0, &io);
    if (rc != OUTBOX_OK) {
        return rc;
    }
    uint32_t size = io.out_size;
    if (size == 0) {
        return OUTBOX_ERR_NOT_FOUND;
    }
    if (size > OUTBOX_MEDIA_MAX) {
        return OUTBOX_ERR_NO_SPACE;
    }

    uint8_t *buf = (uint8_t *)malloc(size);
    if (buf == NULL) {
        return OUTBOX_ERR_NO_SPACE;
    }
    storage_io_t io2;
    memset(&io2, 0, sizeof(io2));
    io2.buf = buf;
    io2.buf_cap = size;
    rc = outbox_call(6 /* READ_RANGE */, path, 0, size, NULL, 0, &io2);
    if (rc != OUTBOX_OK) {
        free(buf);
        return rc;
    }
    *out_buf = buf;
    *out_len = io2.out_len;
    return OUTBOX_OK;
}
