/*
 * ui_book_guard.c —— 电子书阅读护栏的纯逻辑实现（进度重算 + 宵禁 + 每日上限）
 *
 * 契约来源: abbot/doc/07 §2.3（阅读进度）/ §2.4（防沉迷护栏）。
 *
 * 本文件是**纯 C**，不依赖 Arduino.h / LVGL / WiFi —— 主机侧可单测。
 * 不碰 TF 卡、不碰网络、不拼用户文案。
 */
#include "ui_book_guard.h"

#include <stdio.h>
#include <string.h>

/* ===================== 最小 JSON 扫描工具 =====================
 *
 * 只支持我们真正需要的形状（对象 / 数组 / 字符串 / 无符号整数），
 * 遇到看不懂的值就**跳过**，不做完整校验 —— 解析失败一律退化成「没有数据」，
 * 绝不因为 JSON 里多了一个字段就报错。
 */

static const char *js_skip_ws(const char *p, const char *e)
{
    while (p < e && (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n')) {
        p++;
    }
    return p;
}

/** 解析字符串字面量（p 指向开头的 '"'），结果写入 out（可能为 NULL）。 */
static const char *js_parse_string(const char *p, const char *e, char *out, size_t cap)
{
    if (p >= e || *p != '"') {
        return NULL;
    }
    p++;
    size_t n = 0;
    while (p < e && *p != '"') {
        char c = *p;
        if (c == '\\' && p + 1 < e) {
            p++;
            c = *p;
            switch (c) {
            case 'n': c = '\n'; break;
            case 't': c = '\t'; break;
            case 'r': c = '\r'; break;
            case 'b': c = '\b'; break;
            case 'f': c = '\f'; break;
            default: break;   /* \" \\ \/ 与 \uXXXX 原样留 \u 的 'u' */
            }
        }
        if (out != NULL && cap > 0 && n + 1 < cap) {
            out[n] = c;
        }
        n++;
        p++;
    }
    if (p >= e) {
        return NULL;   /* 没闭合 */
    }
    if (out != NULL && cap > 0) {
        out[n < cap ? n : cap - 1] = '\0';
    }
    return p + 1;
}

/** 跳过一个值（返回值末尾指针；失败返回 NULL）。 */
static const char *js_skip_value(const char *p, const char *e)
{
    p = js_skip_ws(p, e);
    if (p >= e) {
        return NULL;
    }
    if (*p == '"') {
        return js_parse_string(p, e, NULL, 0);
    }
    if (*p == '{' || *p == '[') {
        char open = *p;
        char close = (open == '{') ? '}' : ']';
        int depth = 0;
        while (p < e) {
            char c = *p;
            if (c == '"') {
                p = js_parse_string(p, e, NULL, 0);
                if (p == NULL) {
                    return NULL;
                }
                continue;
            }
            if (c == open) {
                depth++;
            } else if (c == close) {
                depth--;
                if (depth == 0) {
                    return p + 1;
                }
            }
            p++;
        }
        return NULL;
    }
    /* number / true / false / null：一直吃到分隔符 */
    while (p < e && *p != ',' && *p != '}' && *p != ']' && *p != ' ' && *p != '\t' &&
           *p != '\r' && *p != '\n') {
        p++;
    }
    return p;
}

/** 解析无符号整数；不是纯数字返回 false。 */
static bool js_parse_u32(const char *p, const char *e, uint32_t *out)
{
    p = js_skip_ws(p, e);
    if (p >= e || *p < '0' || *p > '9') {
        return false;
    }
    uint64_t v = 0;
    while (p < e && *p >= '0' && *p <= '9') {
        v = v * 10u + (uint64_t)(*p - '0');
        if (v > 0xFFFFFFFFull) {
            return false;
        }
        p++;
    }
    *out = (uint32_t)v;
    return true;
}

/**
 * 在 JSON 对象里找 key（key 不带引号）。
 * `p` 指向 '{' 之后。找到返回指向 value 的指针；没找到返回 NULL。
 */
static const char *js_obj_find(const char *p, const char *e, const char *key)
{
    if (p >= e || *p != '{') {
        return NULL;
    }
    p++;
    for (;;) {
        p = js_skip_ws(p, e);
        if (p >= e || *p == '}') {
            return NULL;
        }
        if (*p != '"') {
            return NULL;
        }
        char name[64];
        const char *after = js_parse_string(p, e, name, sizeof(name));
        if (after == NULL) {
            return NULL;
        }
        after = js_skip_ws(after, e);
        if (after >= e || *after != ':') {
            return NULL;
        }
        after = js_skip_ws(after + 1, e);
        if (strcmp(name, key) == 0) {
            return after;
        }
        const char *next = js_skip_value(after, e);
        if (next == NULL) {
            return NULL;
        }
        p = js_skip_ws(next, e);
        if (p < e && *p == ',') {
            p++;
            continue;
        }
        return NULL;
    }
}

/* ===================== 时间解析 ===================== */

int ui_book_time_to_minutes(const ui_book_time_t *t)
{
    if (t == NULL || t->hour < 0 || t->hour > 23 || t->minute < 0 || t->minute > 59) {
        return -1;
    }
    return t->hour * 60 + t->minute;
}

int ui_book_parse_hhmm(const char *text)
{
    if (text == NULL) {
        return -1;
    }
    if (text[0] < '0' || text[0] > '9' || text[1] < '0' || text[1] > '9' ||
        text[2] != ':') {
        return -1;
    }
    if (text[3] < '0' || text[3] > '9' || text[4] < '0' || text[4] > '9') {
        return -1;
    }
    if (text[5] != '\0' && text[5] != ' ' && text[5] != '\t') {
        return -1;
    }
    int hh = (text[0] - '0') * 10 + (text[1] - '0');
    int mm = (text[3] - '0') * 10 + (text[4] - '0');
    if (hh > 23 || mm > 59) {
        return -1;
    }
    return hh * 60 + mm;
}

/* ===================== 护栏配置 ===================== */

void ui_book_guard_defaults(ui_book_guard_t *out)
{
    if (out == NULL) {
        return;
    }
    out->curfew.start_min = UI_BOOK_CURFEW_START_DEFAULT_MIN;
    out->curfew.end_min = UI_BOOK_CURFEW_END_DEFAULT_MIN;
    out->curfew.valid = true;
    out->daily_limit_min = UI_BOOK_DAILY_LIMIT_DEFAULT_MIN;
}

void ui_book_guard_parse(const char *json, size_t len, ui_book_guard_t *out)
{
    ui_book_guard_defaults(out);
    if (json == NULL || out == NULL) {
        return;
    }
    const char *e = json + len;

    /* --- curfew_window: ["22:00","06:30"] --- */
    const char *win = js_obj_find(json, e, "curfew_window");
    if (win != NULL && win < e && *win == '[') {
        const char *p = js_skip_ws(win + 1, e);
        char a[16];
        char b[16];
        a[0] = '\0';
        b[0] = '\0';
        if (p < e && *p == '"') {
            const char *after = js_parse_string(p, e, a, sizeof(a));
            if (after != NULL) {
                after = js_skip_ws(after, e);
                if (after < e && *after == ',') {
                    after = js_skip_ws(after + 1, e);
                    if (after < e && *after == '"') {
                        js_parse_string(after, e, b, sizeof(b));
                    }
                }
            }
        }
        int sa = ui_book_parse_hhmm(a);
        int eb = ui_book_parse_hhmm(b);
        if (sa >= 0 && eb >= 0) {
            out->curfew.start_min = sa;
            out->curfew.end_min = eb;
            out->curfew.valid = true;
        }
    }

    /* --- daily_book_limit_min --- */
    const char *lim = js_obj_find(json, e, "daily_book_limit_min");
    if (lim != NULL) {
        uint32_t v = 0;
        if (js_parse_u32(lim, e, &v) && v <= 24u * 60u) {
            out->daily_limit_min = (int)v;
        }
    }
}

bool ui_book_curfew_active(const ui_book_curfew_t *curfew, int now_min)
{
    if (curfew == NULL || !curfew->valid) {
        return false;
    }
    if (now_min < 0 || now_min > 1439) {
        return false;
    }
    int start = curfew->start_min;
    int end = curfew->end_min;
    if (start < 0 || start > 1439 || end < 0 || end > 1439) {
        return false;
    }
    if (start == end) {
        /* 起止相同：视为全天锁定（保守 —— 宁可少读也不让宵禁失效） */
        return true;
    }
    if (start < end) {
        /* 同日区间 */
        return (now_min >= start && now_min < end);
    }
    /* 跨午夜区间：22:00 → 06:30 */
    return (now_min >= start || now_min < end);
}

bool ui_book_daily_limit_reached(const ui_book_guard_t *guard, uint32_t read_seconds)
{
    if (guard == NULL || guard->daily_limit_min <= 0) {
        return false;
    }
    uint32_t limit_sec = (uint32_t)guard->daily_limit_min * 60u;
    return read_seconds >= limit_sec;
}

/* ===================== 进度重算 ===================== */

int ui_book_progress_percent(uint32_t offset, uint32_t total_bytes)
{
    if (total_bytes == 0) {
        return 0;
    }
    if (offset >= total_bytes) {
        return 100;
    }
    uint32_t pct = (uint32_t)(((uint64_t)offset * 100ull) / (uint64_t)total_bytes);
    if (pct > 100) {
        pct = 100;
    }
    return (int)pct;
}

/* ===================== 进度表 ===================== */

void ui_bookmarks_parse(const char *json, size_t len, ui_bookmarks_t *out)
{
    if (out == NULL) {
        return;
    }
    out->schema_version = 0;
    out->daily_date[0] = '\0';
    out->daily_used_sec = 0;
    out->count = 0;
    if (json == NULL) {
        return;
    }
    const char *e = json + len;

    /* --- 顶层字段：schema_version / daily_date / daily_used_sec（doc/07 §2.3） --- */
    const char *sv = js_obj_find(json, e, "schema_version");
    if (sv != NULL) {
        uint32_t v = 0;
        if (js_parse_u32(sv, e, &v)) {
            out->schema_version = (int)v;
        }
    }
    const char *dd = js_obj_find(json, e, "daily_date");
    if (dd != NULL && dd < e && *dd == '"') {
        js_parse_string(dd, e, out->daily_date, sizeof(out->daily_date));
    }
    const char *du = js_obj_find(json, e, "daily_used_sec");
    if (du != NULL) {
        uint32_t v = 0;
        if (js_parse_u32(du, e, &v)) {
            out->daily_used_sec = v;
        }
    }

    const char *books = js_obj_find(json, e, "books");
    if (books == NULL || books >= e || *books != '{') {
        return;
    }
    const char *p = js_skip_ws(books + 1, e);
    while (p < e && *p != '}') {
        if (*p != '"') {
            return;
        }
        char name[UI_BOOK_MARK_NAME_MAX];
        const char *after = js_parse_string(p, e, name, sizeof(name));
        if (after == NULL) {
            return;
        }
        after = js_skip_ws(after, e);
        if (after >= e || *after != ':') {
            return;
        }
        after = js_skip_ws(after + 1, e);
        if (after < e && *after == '{') {
            uint32_t offset = 0;
            uint32_t total = 0;
            uint32_t ts = 0;
            /* doc/07 §2.3 的字段名是 byte_offset；offset 作旧别名兼容。 */
            const char *v = js_obj_find(after, e, "byte_offset");
            if (v == NULL) {
                v = js_obj_find(after, e, "offset");
            }
            if (v != NULL) {
                js_parse_u32(v, e, &offset);
            }
            v = js_obj_find(after, e, "total_bytes");
            if (v != NULL) {
                js_parse_u32(v, e, &total);
            }
            v = js_obj_find(after, e, "last_read_ts");
            if (v != NULL) {
                js_parse_u32(v, e, &ts);
            }
            if (out->count < UI_BOOK_MARK_MAX) {
                ui_book_mark_t *m = &out->items[out->count];
                snprintf(m->name, sizeof(m->name), "%s", name);
                m->offset = offset;
                m->total_bytes = total;
                m->last_read_ts = ts;
                out->count++;
            }
        }
        /* 跳过这一本书的对象（否则只能解析到第一本）。 */
        const char *next = js_skip_value(after, e);
        if (next == NULL) {
            return;
        }
        p = js_skip_ws(next, e);
        if (p < e && *p == ',') {
            p = js_skip_ws(p + 1, e);
            continue;
        }
        break;
    }
}

/** 检查 JSON 的括号是否配平（字符串字面量里的括号不算）。 */
static bool js_balanced(const char *json, size_t len)
{
    const char *e = json + len;
    const char *p = json;
    int depth = 0;
    while (p < e) {
        char c = *p;
        if (c == '"') {
            p = js_parse_string(p, e, NULL, 0);
            if (p == NULL) {
                return false;   /* 字符串没闭合 —— 残缺文件 */
            }
            continue;
        }
        if (c == '{' || c == '[') {
            depth++;
        } else if (c == '}' || c == ']') {
            depth--;
            if (depth < 0) {
                return false;
            }
        }
        p++;
    }
    return depth == 0;
}

bool ui_bookmarks_valid(const char *json, size_t len)
{
    if (json == NULL || len == 0) {
        return false;
    }
    if (!js_balanced(json, len)) {
        return false;   /* 残缺 / 被截断的文件 */
    }
    const char *e = json + len;
    const char *books = js_obj_find(json, e, "books");
    return (books != NULL && books < e && *books == '{');
}

int ui_book_pick_source(const char *primary, size_t primary_len, const char *backup,
                        size_t backup_len, const char **out_json, size_t *out_len)
{
    if (out_json != NULL) {
        *out_json = primary;
    }
    if (out_len != NULL) {
        *out_len = primary_len;
    }
    if (ui_bookmarks_valid(primary, primary_len)) {
        return 0;
    }
    if (ui_bookmarks_valid(backup, backup_len)) {
        if (out_json != NULL) {
            *out_json = backup;
        }
        if (out_len != NULL) {
            *out_len = backup_len;
        }
        return 1;
    }
    return -1;
}

int ui_bookmarks_format(const ui_bookmarks_t *marks, char *out, size_t cap)
{
    if (marks == NULL || out == NULL || cap == 0) {
        return -1;
    }
    size_t pos = 0;
    int n = snprintf(out + pos, cap - pos,
                     "{\"schema_version\":%d,\"daily_date\":\"%s\",\"daily_used_sec\":%lu,"
                     "\"books\":{",
                     marks->schema_version, marks->daily_date,
                     (unsigned long)marks->daily_used_sec);
    if (n < 0 || (size_t)n >= cap - pos) {
        return -1;
    }
    pos += (size_t)n;
    int i;
    for (i = 0; i < marks->count && i < UI_BOOK_MARK_MAX; i++) {
        const ui_book_mark_t *m = &marks->items[i];
        /* percent 只是导出快照 —— 显示端永远重算，这里存的仅供外部参考。 */
        int pct = ui_book_progress_percent(m->offset, m->total_bytes);
        n = snprintf(out + pos, cap - pos,
                     "%s\"%s\":{\"byte_offset\":%lu,\"total_bytes\":%lu,"
                     "\"percent\":%d,\"last_read_ts\":%lu}",
                     (i > 0) ? "," : "", m->name, (unsigned long)m->offset,
                     (unsigned long)m->total_bytes, pct, (unsigned long)m->last_read_ts);
        if (n < 0 || (size_t)n >= cap - pos) {
            return -1;
        }
        pos += (size_t)n;
    }
    n = snprintf(out + pos, cap - pos, "}}");
    if (n < 0 || (size_t)n >= cap - pos) {
        return -1;
    }
    pos += (size_t)n;
    return (int)pos;
}

int ui_bookmarks_find(const ui_bookmarks_t *marks, const char *name)
{
    if (marks == NULL || name == NULL) {
        return -1;
    }
    int i;
    for (i = 0; i < marks->count && i < UI_BOOK_MARK_MAX; i++) {
        if (strcmp(marks->items[i].name, name) == 0) {
            return i;
        }
    }
    return -1;
}

int ui_bookmarks_set(ui_bookmarks_t *marks, const char *name, uint32_t offset,
                     uint32_t total_bytes)
{
    if (marks == NULL || name == NULL || name[0] == '\0') {
        return -1;
    }
    int idx = ui_bookmarks_find(marks, name);
    if (idx >= 0) {
        marks->items[idx].offset = offset;
        marks->items[idx].total_bytes = total_bytes;
        return idx;
    }
    if (marks->count >= UI_BOOK_MARK_MAX) {
        return -1;
    }
    idx = marks->count;
    snprintf(marks->items[idx].name, sizeof(marks->items[idx].name), "%s", name);
    marks->items[idx].offset = offset;
    marks->items[idx].total_bytes = total_bytes;
    marks->count++;
    return idx;
}

/* ===================== 每日阅读时长统计（doc/07 §2.4） ===================== */

bool ui_book_daily_rollover(ui_bookmarks_t *marks, const char *today)
{
    if (marks == NULL || today == NULL || today[0] == '\0') {
        return false;
    }
    if (strcmp(marks->daily_date, today) == 0) {
        return false;
    }
    snprintf(marks->daily_date, sizeof(marks->daily_date), "%s", today);
    marks->daily_used_sec = 0;
    return true;
}
