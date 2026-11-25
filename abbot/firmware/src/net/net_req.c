/**
 * net_req.c —— 设备端请求组装与响应解析（纯 C，不依赖 Arduino.h）
 *
 * 对齐 abbot/doc/05-云端接口与AI推理引擎契约.md §3.2/§5.1 与
 * abbot/doc/04-无线网络OTA与本地存储契约.md §六。
 *
 * 铁律：
 *   - 签名 PATH 恒为纯 API 路径（不含域名、不含 query、不含 `/abbot` 挂载前缀）；
 *   - 业务明文的 `edu_stage` / `enrollment_year` / `device_remaining_quota`
 *     由本层自动注入（调用方不得自行拼装）；
 *   - 响应验签必须用**原始响应体字节**，所以解析结果里的 body 指向调用方的原缓冲。
 */

#include "net_req.h"
#include "net_client.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ===================== 可测长度的字符串缓冲 ===================== */

typedef struct {
    char  *buf;      /* NULL = 只测量长度 */
    size_t cap;
    size_t len;
    int    overflow;
} nr_sb_t;

static void sb_init(nr_sb_t *sb, char *buf, size_t cap)
{
    sb->buf = buf;
    sb->cap = cap;
    sb->len = 0;
    sb->overflow = 0;
}

static void sb_putc(nr_sb_t *sb, char c)
{
    if (sb->overflow) {
        return;
    }
    if (sb->buf != NULL) {
        if (sb->len + 1 >= sb->cap) { /* 留 1 字节给结尾 NUL */
            sb->overflow = 1;
            return;
        }
        sb->buf[sb->len] = c;
    }
    sb->len++;
}

static void sb_putn(nr_sb_t *sb, const char *s, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        sb_putc(sb, s[i]);
    }
}

static void sb_puts(nr_sb_t *sb, const char *s)
{
    sb_putn(sb, s, strlen(s));
}

static void sb_printf(nr_sb_t *sb, const char *fmt, ...)
{
    char tmp[160];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(tmp, sizeof tmp, fmt, ap);
    va_end(ap);
    if (n < 0) {
        sb->overflow = 1;
        return;
    }
    if ((size_t)n >= sizeof tmp) {
        sb->overflow = 1;
        return;
    }
    sb_putn(sb, tmp, (size_t)n);
}

/** JSON 字符串值（含引号与转义）。 */
static void sb_json_str(nr_sb_t *sb, const char *s)
{
    const unsigned char *p = (const unsigned char *)(s ? s : "");
    sb_putc(sb, '"');
    for (; *p != '\0'; p++) {
        switch (*p) {
        case '"':  sb_puts(sb, "\\\""); break;
        case '\\': sb_puts(sb, "\\\\"); break;
        case '\n': sb_puts(sb, "\\n"); break;
        case '\r': sb_puts(sb, "\\r"); break;
        case '\t': sb_puts(sb, "\\t"); break;
        case '\b': sb_puts(sb, "\\b"); break;
        case '\f': sb_puts(sb, "\\f"); break;
        default:
            if (*p < 0x20) {
                sb_printf(sb, "\\u%04x", (unsigned)*p);
            } else {
                sb_putc(sb, (char)*p);
            }
        }
    }
    sb_putc(sb, '"');
}

/**
 * Base64 逐 3 字节块编码 —— 与一次性编码结果逐字节一致（3 字节对齐，padding 只落在末块）。
 * 复用 net_client.c 的已验证实现，不另写一份。
 */
static void sb_base64(nr_sb_t *sb, const uint8_t *data, size_t len)
{
    char chunk[8];
    size_t out_len = 0;
    size_t i = 0;
    if (data == NULL) {
        return;
    }
    while (i + 3 <= len) {
        if (nc_base64_encode(data + i, 3, chunk, sizeof chunk, &out_len) != NC_OK) {
            sb->overflow = 1;
            return;
        }
        sb_putn(sb, chunk, out_len);
        i += 3;
    }
    if (i < len) {
        if (nc_base64_encode(data + i, len - i, chunk, sizeof chunk, &out_len) != NC_OK) {
            sb->overflow = 1;
            return;
        }
        sb_putn(sb, chunk, out_len);
    }
}

/* ===================== URL ===================== */

static size_t url_needed(const char *host, const char *mount, const char *path)
{
    return strlen("http://") + strlen(host) + strlen(mount ? mount : "") + strlen(path) + 1;
}

int nr_build_url(const char *host, const char *mount, const char *path,
                 char *out, size_t out_cap)
{
    if (host == NULL || path == NULL) {
        return -1;
    }
    size_t need = url_needed(host, mount, path);
    if (out == NULL) {
        return (int)need;
    }
    if (out_cap < need) {
        return -2;
    }
    snprintf(out, out_cap, "http://%s%s%s", host, mount ? mount : "", path);
    return (int)need;
}

int nr_build_ota_url(const char *host, const char *mount,
                     const char *device_id, const char *current_ver,
                     char *out, size_t out_cap)
{
    if (host == NULL || device_id == NULL || current_ver == NULL) {
        return -1;
    }
    size_t need = url_needed(host, mount, NR_PATH_OTA_CHECK) +
                  strlen("?device_id=") + strlen(device_id) +
                  strlen("&current_ver=") + strlen(current_ver) + 1;
    if (out == NULL) {
        return (int)need;
    }
    if (out_cap < need) {
        return -2;
    }
    snprintf(out, out_cap, "http://%s%s%s?device_id=%s&current_ver=%s",
             host, mount ? mount : "", NR_PATH_OTA_CHECK, device_id, current_ver);
    return (int)need;
}

/* ===================== 业务明文组装 ===================== */

static void fill_solve_plain(nr_sb_t *sb, const nr_profile_t *prof, const char *session_id,
                             const uint8_t *jpg, size_t jpg_len,
                             const uint8_t *pcm, size_t pcm_len, uint8_t hint_level)
{
    sb_putc(sb, '{');
    sb_puts(sb, "\"solve_session_id\":");
    sb_json_str(sb, session_id);
    sb_puts(sb, ",\"image_b64\":\"");
    sb_base64(sb, jpg, jpg_len);
    sb_puts(sb, "\"");
    if (pcm != NULL && pcm_len > 0) {
        sb_puts(sb, ",\"pcm_base64\":\"");
        sb_base64(sb, pcm, pcm_len);
        sb_puts(sb, "\"");
    }
    sb_puts(sb, ",\"edu_stage\":");
    sb_json_str(sb, prof->edu_stage);
    sb_printf(sb, ",\"enrollment_year\":%d,\"hint_level\":%u,\"device_remaining_quota\":%d}",
              prof->enrollment_year, (unsigned)hint_level, prof->device_remaining_quota);
}

int nr_build_solve_plain(const nr_profile_t *prof, const char *session_id,
                         const uint8_t *jpg, size_t jpg_len,
                         const uint8_t *pcm, size_t pcm_len,
                         uint8_t hint_level,
                         char *out, size_t out_cap)
{
    if (prof == NULL) {
        return -1;
    }
    if (out == NULL) {
        nr_sb_t sb;
        sb_init(&sb, NULL, 0);
        fill_solve_plain(&sb, prof, session_id, jpg, jpg_len, pcm, pcm_len, hint_level);
        return (int)(sb.len + 1);
    }
    nr_sb_t sb;
    sb_init(&sb, out, out_cap);
    fill_solve_plain(&sb, prof, session_id, jpg, jpg_len, pcm, pcm_len, hint_level);
    if (sb.overflow) {
        return -2;
    }
    out[sb.len] = '\0';
    return (int)(sb.len + 1);
}

static void fill_voice_plain(nr_sb_t *sb, const nr_profile_t *prof, const char *context_page,
                             const char *pack_id, const char *card_id,
                             const uint8_t *pcm, size_t pcm_len, const char *local_time)
{
    sb_putc(sb, '{');
    sb_puts(sb, "\"context_page\":");
    sb_json_str(sb, context_page);
    sb_puts(sb, ",\"context\":{\"pack_id\":");
    sb_json_str(sb, pack_id);
    sb_puts(sb, ",\"card_id\":");
    sb_json_str(sb, card_id);
    sb_puts(sb, "},\"pcm_base64\":\"");
    sb_base64(sb, pcm, pcm_len);
    sb_puts(sb, "\",\"edu_stage\":");
    sb_json_str(sb, prof->edu_stage);
    sb_printf(sb, ",\"enrollment_year\":%d,\"local_time\":", prof->enrollment_year);
    sb_json_str(sb, local_time);
    sb_putc(sb, '}');
}

int nr_build_voice_plain(const nr_profile_t *prof, const char *context_page,
                         const char *pack_id, const char *card_id,
                         const uint8_t *pcm, size_t pcm_len,
                         const char *local_time,
                         char *out, size_t out_cap)
{
    if (prof == NULL) {
        return -1;
    }
    if (out == NULL) {
        nr_sb_t sb;
        sb_init(&sb, NULL, 0);
        fill_voice_plain(&sb, prof, context_page, pack_id, card_id, pcm, pcm_len, local_time);
        return (int)(sb.len + 1);
    }
    nr_sb_t sb;
    sb_init(&sb, out, out_cap);
    fill_voice_plain(&sb, prof, context_page, pack_id, card_id, pcm, pcm_len, local_time);
    if (sb.overflow) {
        return -2;
    }
    out[sb.len] = '\0';
    return (int)(sb.len + 1);
}

int nr_build_pair_plain(const char *device_id, const char *pair_code,
                        char *out, size_t out_cap)
{
    if (device_id == NULL || pair_code == NULL) {
        return -1;
    }
    nr_sb_t probe;
    sb_init(&probe, NULL, 0);
    sb_puts(&probe, "{\"device_id\":");
    sb_json_str(&probe, device_id);
    sb_puts(&probe, ",\"pair_code\":");
    sb_json_str(&probe, pair_code);
    sb_puts(&probe, "}");

    if (out == NULL) {
        return (int)(probe.len + 1);
    }
    if (out_cap < probe.len + 1) {
        return -2;
    }
    nr_sb_t sb;
    sb_init(&sb, out, out_cap);
    sb_puts(&sb, "{\"device_id\":");
    sb_json_str(&sb, device_id);
    sb_puts(&sb, ",\"pair_code\":");
    sb_json_str(&sb, pair_code);
    sb_puts(&sb, "}");
    if (sb.overflow) {
        return -2;
    }
    out[sb.len] = '\0';
    return (int)(sb.len + 1);
}

int nr_build_register_plain(const char *device_id, const char *fw_version,
                            char *out, size_t out_cap)
{
    if (device_id == NULL || device_id[0] == '\0') {
        return -1;
    }
    nr_sb_t probe;
    sb_init(&probe, NULL, 0);
    sb_puts(&probe, "{\"device_id\":");
    sb_json_str(&probe, device_id);
    if (fw_version != NULL && fw_version[0] != '\0') {
        sb_puts(&probe, ",\"fw_version\":");
        sb_json_str(&probe, fw_version);
    }
    sb_puts(&probe, "}");

    if (out == NULL) {
        return (int)(probe.len + 1);
    }
    if (out_cap < probe.len + 1) {
        return -2;
    }
    nr_sb_t sb;
    sb_init(&sb, out, out_cap);
    sb_puts(&sb, "{\"device_id\":");
    sb_json_str(&sb, device_id);
    if (fw_version != NULL && fw_version[0] != '\0') {
        sb_puts(&sb, ",\"fw_version\":");
        sb_json_str(&sb, fw_version);
    }
    sb_puts(&sb, "}");
    if (sb.overflow) {
        return -2;
    }
    out[sb.len] = '\0';
    return (int)(sb.len + 1);
}

static void fill_bootstrap_plain(nr_sb_t *sb, const char *device_id, const char *fw_version,
                                 int battery_pct, uint32_t schedule_updated_at,
                                 const char *local_packs_json, const char *local_books_json,
                                 const char *city)
{
    sb_putc(sb, '{');
    sb_puts(sb, "\"device_id\":");
    sb_json_str(sb, device_id);
    sb_puts(sb, ",\"fw_version\":");
    sb_json_str(sb, fw_version);
    sb_printf(sb, ",\"battery_pct\":%d", battery_pct);
    sb_printf(sb, ",\"schedule_updated_at\":%lu", (unsigned long)schedule_updated_at);
    sb_puts(sb, ",\"local_packs\":");
    sb_puts(sb, (local_packs_json != NULL) ? local_packs_json : "[]");
    sb_puts(sb, ",\"local_books\":");
    sb_puts(sb, (local_books_json != NULL) ? local_books_json : "[]");
    /* 城市（doc/05 §5.2）：服务端解密后读 payload["city"]，写进自己的权威源。
     * 为空就不带这个字段 —— 服务端不会把已有城市清掉。 */
    if (city != NULL && city[0] != '\0') {
        sb_puts(sb, ",\"city\":");
        sb_json_str(sb, city);
    }
    sb_putc(sb, '}');
}

int nr_build_bootstrap_plain(const char *device_id, const char *fw_version,
                             int battery_pct, uint32_t schedule_updated_at,
                             const char *local_packs_json, const char *local_books_json,
                             const char *city,
                             char *out, size_t out_cap)
{
    if (device_id == NULL || fw_version == NULL) {
        return -1;
    }
    if (out == NULL) {
        nr_sb_t sb;
        sb_init(&sb, NULL, 0);
        fill_bootstrap_plain(&sb, device_id, fw_version, battery_pct, schedule_updated_at,
                             local_packs_json, local_books_json, city);
        return (int)(sb.len + 1);
    }
    nr_sb_t sb;
    sb_init(&sb, out, out_cap);
    fill_bootstrap_plain(&sb, device_id, fw_version, battery_pct, schedule_updated_at,
                         local_packs_json, local_books_json, city);
    if (sb.overflow) {
        return -2;
    }
    out[sb.len] = '\0';
    return (int)(sb.len + 1);
}

int nr_parse_profile(const char *json, size_t len, nr_profile_doc_t *out)
{
    if (json == NULL || out == NULL) {
        return -1;
    }
    memset(out, 0, sizeof *out);
    char stage[16];
    int stage_len = nr_json_get_string(json, len, "edu_stage", stage, sizeof stage);
    if (stage_len <= 0) {
        return -1;
    }
    int year = nr_json_get_int(json, len, "enrollment_year", -1);
    if (year <= 0) {
        return -1;
    }
    snprintf(out->edu_stage, sizeof out->edu_stage, "%s", stage);
    out->enrollment_year = year;
    out->daily_quota = nr_json_get_int(json, len, "daily_quota", 0);
    out->quota_used_count = nr_json_get_int(json, len, "quota_used_count", 0);
    return 0;
}

/* ===================== JSON 数组遍历 ===================== */

/** 定位 `key` 对应的数组体，返回数组体起止位置；找不到返回 -1。 */
static int nr_find_array(const char *json, size_t len, const char *key,
                         size_t *out_start, size_t *out_end)
{
    char pat[64];
    int pn = snprintf(pat, sizeof(pat), "\"%s\"", key);
    if (json == NULL || key == NULL || pn <= 0 || (size_t)pn >= sizeof(pat)) {
        return -1;
    }
    const char *end = json + len;
    const char *p = json;
    while ((size_t)(end - p) >= (size_t)pn) {
        const char *hit = NULL;
        for (const char *q = p; q + pn <= end; q++) {
            if (memcmp(q, pat, (size_t)pn) == 0) {
                hit = q;
                break;
            }
        }
        if (hit == NULL) {
            return -1;
        }
        const char *c = hit + pn;
        while (c < end && (*c == ' ' || *c == '\t')) {
            c++;
        }
        if (c >= end || *c != ':') {
            p = hit + pn;
            continue;
        }
        c++;
        while (c < end && (*c == ' ' || *c == '\t')) {
            c++;
        }
        if (c >= end || *c != '[') {
            p = hit + pn;
            continue;
        }
        c++;
        size_t start = (size_t)(c - json);
        int in_str = 0;
        int esc = 0;
        while (c < end) {
            char ch = *c;
            if (esc) {
                esc = 0;
            } else if (in_str) {
                if (ch == '\\') {
                    esc = 1;
                } else if (ch == '"') {
                    in_str = 0;
                }
            } else if (ch == '"') {
                in_str = 1;
            } else if (ch == ']') {
                *out_start = start;
                *out_end = (size_t)(c - json);
                return 0;
            }
            c++;
        }
        return -1;
    }
    return -1;
}

/**
 * 在数组体内定位第 index 个元素。
 * 元素按顶层逗号切分（嵌套的 {} [] 内部逗号不算，字符串内逗号不算）。
 */
static int nr_array_locate(const char *json, size_t start, size_t end, int index,
                            const char **out_start, size_t *out_len)
{
    const char *p = json + start;
    const char *endp = json + end;
    int idx = 0;
    int in_str = 0;
    int esc = 0;
    int depth = 0;
    const char *elem_begin = p;
    while (p <= endp) {
        int at_end = (p == endp);
        char ch = at_end ? ',' : *p;
        {
            /* at_end 时 ch 被当成 ','，让最后一个元素也能走同一条「顶层逗号 -> 收尾」分支。
             * 之前这里用 `if (!at_end)` 包着，数组末尾直接 break，最后一个元素永远吐不出来。 */
            if (esc) {
                esc = 0;
            } else if (in_str) {
                if (ch == '\\') {
                    esc = 1;
                } else if (ch == '"') {
                    in_str = 0;
                }
            } else if (ch == '"') {
                in_str = 1;
            } else if (ch == '{' || ch == '[') {
                depth++;
            } else if (ch == '}' || ch == ']') {
                depth--;
            } else if (ch == ',' && depth == 0) {
                /* 顶层逗号 -> 当前元素结束 */
                const char *b = elem_begin;
                while (b < p && (*b == ' ' || *b == '\t' || *b == '\n' || *b == '\r')) {
                    b++;
                }
                const char *e = p;
                while (e > b && (e[-1] == ' ' || e[-1] == '\t' || e[-1] == '\n' || e[-1] == '\r')) {
                    e--;
                }
                if (e > b) {
                    if (idx == index) {
                        *out_start = b;
                        *out_len = (size_t)(e - b);
                        return 0;
                    }
                    idx++;
                }
                elem_begin = p + 1;
                p++;
                continue;
            }
        }
        if (at_end) {
            break;
        }
        p++;
    }
    return -1;
}

int nr_json_get_object(const char *json, size_t len, const char *key,
                       const char **out_start, size_t *out_len)
{
    if (json == NULL || key == NULL || out_start == NULL || out_len == NULL) {
        return -1;
    }
    char pat[64];
    int pn = snprintf(pat, sizeof(pat), "\"%s\"", key);
    if (pn <= 0 || (size_t)pn >= sizeof(pat)) {
        return -1;
    }
    const char *end = json + len;
    const char *p = json;
    while ((size_t)(end - p) >= (size_t)pn) {
        const char *hit = NULL;
        for (const char *q = p; q + pn <= end; q++) {
            if (memcmp(q, pat, (size_t)pn) == 0) {
                hit = q;
                break;
            }
        }
        if (hit == NULL) {
            return -1;
        }
        const char *c = hit + pn;
        while (c < end && (*c == ' ' || *c == '\t')) {
            c++;
        }
        if (c >= end || *c != ':') {
            p = hit + pn;
            continue;
        }
        c++;
        while (c < end && (*c == ' ' || *c == '\t')) {
            c++;
        }
        if (c >= end || *c != '{') {
            p = hit + pn;
            continue;
        }
        c++;
        const char *start = c;   /* 对象体的起点（指针，不是偏移） */
        int in_str = 0;
        int esc = 0;
        int depth = 1;
        while (c < end) {
            char ch = *c;
            if (esc) {
                esc = 0;
            } else if (in_str) {
                if (ch == '\\') {
                    esc = 1;
                } else if (ch == '"') {
                    in_str = 0;
                }
            } else if (ch == '"') {
                in_str = 1;
            } else if (ch == '{') {
                depth++;
            } else if (ch == '}') {
                depth--;
                if (depth == 0) {
                    *out_start = start;
                    *out_len = (size_t)(c - start);
                    return 0;
                }
            }
            c++;
        }
        return -1;
    }
    return -1;
}

int nr_array_count(const char *json, size_t len, const char *key)
{
    size_t start = 0;
    size_t end = 0;
    if (nr_find_array(json, len, key, &start, &end) != 0) {
        return -1;
    }
    int count = 0;
    const char *s = NULL;
    size_t l = 0;
    for (int i = 0; nr_array_locate(json, start, end, i, &s, &l) == 0; i++) {
        count++;
    }
    return count;
}

int nr_array_item(const char *json, size_t len, const char *key, int index,
                  const char **out_start, size_t *out_len)
{
    if (json == NULL || key == NULL || out_start == NULL || out_len == NULL || index < 0) {
        return -1;
    }
    size_t start = 0;
    size_t end = 0;
    if (nr_find_array(json, len, key, &start, &end) != 0) {
        return -1;
    }
    return nr_array_locate(json, start, end, index, out_start, out_len);
}

/* ===================== 待同步资产 ===================== */

int nr_parse_pending(const char *json, size_t len, const char *array_key, const char *id_key,
                     nr_pending_t *out, int max, int *out_count)
{
    if (json == NULL || array_key == NULL || id_key == NULL || out == NULL || out_count == NULL || max <= 0) {
        return -1;
    }
    *out_count = 0;
    for (int i = 0; i < max; i++) {
        const char *item = NULL;
        size_t item_len = 0;
        if (nr_array_item(json, len, array_key, i, &item, &item_len) != 0) {
            break;
        }
        nr_pending_t p;
        memset(&p, 0, sizeof(p));
        if (nr_json_get_string(item, item_len, id_key, p.id, sizeof(p.id)) <= 0) {
            continue;
        }
        if (nr_json_get_string(item, item_len, "rel_path", p.rel_path, sizeof(p.rel_path)) <= 0) {
            continue;
        }
        if (nr_json_get_string(item, item_len, "token", p.token, sizeof(p.token)) <= 0) {
            continue;
        }
        out[*out_count] = p;
        (*out_count)++;
    }
    return 0;
}

/* ===================== OTA 检查应答 ===================== */

int nr_parse_ota(const char *json, size_t len, nr_ota_t *out)
{
    if (json == NULL || out == NULL) {
        return -1;
    }
    memset(out, 0, sizeof(*out));
    out->has_update = (nr_json_get_bool(json, len, "has_update", false) != 0);
    if (nr_json_get_string(json, len, "latest_ver", out->latest_ver, sizeof(out->latest_ver)) < 0) {
        out->latest_ver[0] = '\0';
    }
    if (nr_json_get_string(json, len, "sha256", out->sha256, sizeof(out->sha256)) < 0) {
        out->sha256[0] = '\0';
    }
    out->size = (uint32_t)nr_json_get_int(json, len, "size", 0);
    if (nr_json_get_string(json, len, "bin_url", out->bin_url, sizeof(out->bin_url)) < 0) {
        out->bin_url[0] = '\0';
    }
    out->force_update = (nr_json_get_bool(json, len, "force_update", false) != 0);
    if (nr_json_get_string_trunc(json, len, "changelog", out->changelog, sizeof(out->changelog)) < 0) {
        out->changelog[0] = '\0';
    }
    return 0;
}

/* ===================== 服务端相对路径 -> 设备路径 ===================== */

int nr_build_download_url(const char *base, const char *token, char *out, size_t out_cap)
{
    if (base == NULL || token == NULL || out == NULL || out_cap == 0) {
        return -1;
    }
    int n = snprintf(out, out_cap, "%s/api/v1/content/download?token=%s", base, token);
    if (n <= 0 || (size_t)n >= out_cap) {
        return -1;
    }
    return n;
}

int nr_map_rel_path(const char *rel_path, char *out, size_t out_cap)
{
    if (rel_path == NULL || out == NULL || out_cap == 0) {
        return -1;
    }
    if (strncmp(rel_path, "packs/", 6) == 0) {
        snprintf(out, out_cap, "/subjects/%s", rel_path + 6);
        return 0;
    }
    if (strncmp(rel_path, "books/", 6) == 0) {
        snprintf(out, out_cap, "/books/%s", rel_path + 6);
        return 0;
    }
    if (strcmp(rel_path, "schedules/latest.json") == 0) {
        /* 网关侧叫 latest.json，设备侧档案文件名是 schedule.json（doc/04 §3.2） */
        snprintf(out, out_cap, "/config/schedule.json");
        return 0;
    }
    if (strncmp(rel_path, "schedules/", 10) == 0) {
        snprintf(out, out_cap, "/config/%s", rel_path + 10);
        return 0;
    }
    return -1;
}

/* ===================== JSON 取值 ===================== */

static const char *nr_find_value(const char *json, size_t len, const char *key)
{
    size_t klen = strlen(key);
    if (json == NULL || len < klen + 3) {
        return NULL;
    }
    for (size_t i = 0; i + klen + 2 <= len; i++) {
        if (json[i] != '"') {
            continue;
        }
        if (memcmp(json + i + 1, key, klen) != 0) {
            continue;
        }
        if (json[i + 1 + klen] != '"') {
            continue;
        }
        size_t j = i + klen + 2;
        while (j < len && (json[j] == ' ' || json[j] == '\t')) {
            j++;
        }
        if (j >= len || json[j] != ':') {
            continue;
        }
        j++;
        while (j < len && (json[j] == ' ' || json[j] == '\t')) {
            j++;
        }
        return json + j;
    }
    return NULL;
}

static int hex4(const char *p, unsigned *out)
{
    unsigned v = 0;
    for (int i = 0; i < 4; i++) {
        char c = p[i];
        v <<= 4;
        if (c >= '0' && c <= '9') {
            v |= (unsigned)(c - '0');
        } else if (c >= 'a' && c <= 'f') {
            v |= (unsigned)(c - 'a' + 10);
        } else if (c >= 'A' && c <= 'F') {
            v |= (unsigned)(c - 'A' + 10);
        } else {
            return -1;
        }
    }
    *out = v;
    return 0;
}

static void utf8_put(char *out, size_t *pos, unsigned cp)
{
    if (cp < 0x80) {
        out[(*pos)++] = (char)cp;
    } else if (cp < 0x800) {
        out[(*pos)++] = (char)(0xC0 | (cp >> 6));
        out[(*pos)++] = (char)(0x80 | (cp & 0x3F));
    } else if (cp < 0x10000) {
        out[(*pos)++] = (char)(0xE0 | (cp >> 12));
        out[(*pos)++] = (char)(0x80 | ((cp >> 6) & 0x3F));
        out[(*pos)++] = (char)(0x80 | (cp & 0x3F));
    } else {
        out[(*pos)++] = (char)(0xF0 | (cp >> 18));
        out[(*pos)++] = (char)(0x80 | ((cp >> 12) & 0x3F));
        out[(*pos)++] = (char)(0x80 | ((cp >> 6) & 0x3F));
        out[(*pos)++] = (char)(0x80 | (cp & 0x3F));
    }
}

int nr_json_get_string(const char *json, size_t len, const char *key,
                       char *out, size_t out_cap)
{
    const char *p = nr_find_value(json, len, key);
    if (p == NULL || *p != '"') {
        return -1;
    }
    p++;
    size_t pos = 0;
    while (*p != '\0') {
        /* 边界检查：单字节字符要 1 字节 + 结尾 NUL；`\uXXXX` 最多 4 字节 + NUL。
         * 之前一律按 4 字节留余量，导致「64 字符 sha256 装 65 字节缓冲」被误判放不下。 */
        size_t need = 1;
        if (*p == '\\' && *(p + 1) == 'u') {
            need = 4;
        }
        /* 结束引号不写任何字节，不占容量 —— 必须先放过它，
         * 否则「64 字符 sha256 装 65 字节缓冲」会在最后一个字符处被误判放不下。 */
        if (out != NULL && *p != '"' && (out_cap - pos) < (need + 1)) {
            return -2;
        }
        if (*p == '"') {
            break;
        }
        if (*p == '\\') {
            p++;
            char c = *p;
            switch (c) {
            case '"':  if (out) { out[pos] = '"'; } pos++; break;
            case '\\': if (out) { out[pos] = '\\'; } pos++; break;
            case '/':  if (out) { out[pos] = '/'; } pos++; break;
            case 'n':  if (out) { out[pos] = '\n'; } pos++; break;
            case 'r':  if (out) { out[pos] = '\r'; } pos++; break;
            case 't':  if (out) { out[pos] = '\t'; } pos++; break;
            case 'b':  if (out) { out[pos] = '\b'; } pos++; break;
            case 'f':  if (out) { out[pos] = '\f'; } pos++; break;
            case 'u': {
                unsigned cp = 0;
                if (hex4(p + 1, &cp) != 0) {
                    return -1;
                }
                p += 4;
                if (cp >= 0xD800 && cp <= 0xDBFF && p[1] == '\\' && p[2] == 'u') {
                    unsigned lo = 0;
                    if (hex4(p + 3, &lo) == 0 && lo >= 0xDC00 && lo <= 0xDFFF) {
                        cp = 0x10000u + ((cp - 0xD800u) << 10) + (lo - 0xDC00u);
                        p += 6;
                    }
                }
                if (out) {
                    utf8_put(out, &pos, cp);
                } else {
                    if (cp < 0x80) { pos += 1; }
                    else if (cp < 0x800) { pos += 2; }
                    else if (cp < 0x10000) { pos += 3; }
                    else { pos += 4; }
                }
                break;
            }
            default:
                return -1;
            }
            p++;
            continue;
        }
        if (out) {
            out[pos] = *p;
        }
        pos++;
        p++;
    }
    if (*p != '"') {
        return -1;
    }
    if (out != NULL) {
        if (pos + 1 > out_cap) {
            return -2;
        }
        out[pos] = '\0';
    }
    return (int)pos;
}

int nr_json_get_string_trunc(const char *json, size_t len, const char *key,
                              char *out, size_t out_cap)
{
    const char *p = nr_find_value(json, len, key);
    if (p == NULL || *p != '"') {
        return -1;
    }
    p++;
    size_t pos = 0;
    while (*p != '\0' && *p != '"') {
        char c;
        if (*p == '\\') {
            p++;
            char e = *p;
            switch (e) {
            case '"':  c = '"';  break;
            case '\\': c = '\\'; break;
            case '/':  c = '/';  break;
            case 'n':  c = '\n'; break;
            case 'r':  c = '\r'; break;
            case 't':  c = '\t'; break;
            case 'b':  c = '\b'; break;
            case 'f':  c = '\f'; break;
            case 'u': {
                unsigned cp = 0;
                if (hex4(p + 1, &cp) != 0) {
                    return -1;
                }
                p += 4;
                if (cp >= 0xD800 && cp <= 0xDBFF && p[1] == '\\' && p[2] == 'u') {
                    unsigned lo = 0;
                    if (hex4(p + 3, &lo) == 0 && lo >= 0xDC00 && lo <= 0xDFFF) {
                        cp = 0x10000u + ((cp - 0xD800u) << 10) + (lo - 0xDC00u);
                        p += 6;
                    }
                }
                if (out != NULL && pos + 4 <= out_cap) {
                    utf8_put(out, &pos, cp);
                } else {
                    pos += (cp < 0x80) ? 1 : (cp < 0x800) ? 2 : (cp < 0x10000) ? 3 : 4;
                }
                p++;
                continue;
            }
            default:
                return -1;
            }
            p++;
        } else {
            c = *p;
            p++;
        }
        if (out != NULL && pos + 1 < out_cap) {
            out[pos] = c;
        }
        pos++;
    }
    if (*p != '"') {
        return -1;
    }
    if (out != NULL) {
        if (out_cap == 0) {
            return -2;
        }
        out[pos < out_cap ? pos : out_cap - 1] = '\0';
    }
    return (int)pos;
}

int nr_parse_solve_result(const char *json, size_t len, nr_solve_result_t *out)
{
    if (json == NULL || out == NULL) {
        return -1;
    }
    memset(out, 0, sizeof *out);
    if (nr_json_get_string_trunc(json, len, "status", out->status, sizeof out->status) < 0) {
        out->status[0] = '\0';
    }
    if (nr_json_get_string_trunc(json, len, "display_markdown", out->display_markdown,
                                 sizeof out->display_markdown) < 0) {
        out->display_markdown[0] = '\0';
    }
    if (nr_json_get_string_trunc(json, len, "feynman_question", out->feynman_question,
                                 sizeof out->feynman_question) < 0) {
        out->feynman_question[0] = '\0';
    }
    if (nr_json_get_string_trunc(json, len, "speech_text", out->speech_text,
                                 sizeof out->speech_text) < 0) {
        out->speech_text[0] = '\0';
    }
    out->has_audio = (nr_json_get_bool(json, len, "has_audio", false) != 0);
    out->effective_remaining_quota = nr_json_get_int(json, len, "effective_remaining_quota", -1);
    return 0;
}

static bool nr_json_get_number(const char *json, size_t len, const char *key, int *out);

int nr_parse_weather(const char *json, size_t len, nr_weather_t *out)
{
    if (json == NULL || out == NULL) {
        return -1;
    }
    memset(out, 0, sizeof *out);

    /* `weather` 是嵌套对象：先取它的内容片段，再在里面取 temp / text。 */
    const char *body = NULL;
    size_t body_len = 0;
    if (nr_json_get_object(json, len, "weather", &body, &body_len) != 0) {
        return 1;   /* 应答里根本没有 weather —— 空态 */
    }
    if (body == NULL || body_len == 0) {
        return 1;   /* `weather: {}` —— 空态（不是故障） */
    }

    int temp = 0;
    if (!nr_json_get_number(body, body_len, "temp", &temp)) {
        return 1;   /* 没有温度 —— 空态 */
    }
    char text[sizeof out->text];
    if (nr_json_get_string_trunc(body, body_len, "text", text, sizeof text) < 0) {
        text[0] = '\0';
    }
    if (text[0] == '\0') {
        /* 有温度但没有描述 —— 当成没有天气：UI 不该显示「25℃」配空白 */
        return 1;
    }
    out->valid = true;
    out->temp = temp;
    memcpy(out->text, text, sizeof out->text);
    return 0;
}

/**
 * 取一个数字字段（整数或小数，小数四舍五入到整数）。
 *
 * 服务端给的是整数（`round()` 过的），但缓存文件里可能留着浮点 ——
 * 所以这里两种都收。找到并能解析返回 true。
 */
static bool nr_json_get_number(const char *json, size_t len, const char *key, int *out)
{
    const char *p = nr_find_value(json, len, key);
    if (p == NULL) {
        return false;
    }
    int sign = 1;
    if (*p == '-') {
        sign = -1;
        p++;
    }
    if (*p < '0' || *p > '9') {
        return false;
    }
    long v = 0;
    while (*p >= '0' && *p <= '9') {
        v = v * 10 + (*p - '0');
        if (v > 2147483647L) {
            v = 2147483647L;
        }
        p++;
    }
    if (*p == '.' || *p == 'e' || *p == 'E') {
        /* 小数：按浮点读再四舍五入（与服务端 round() 的口径一致） */
        char tmp[64];
        size_t n = 0;
        const char *q = p;
        while (*q != '\0' && n + 1 < sizeof tmp &&
               (*q == '.' || *q == 'e' || *q == 'E' || *q == '+' || *q == '-' ||
                (*q >= '0' && *q <= '9'))) {
            tmp[n++] = *q++;
        }
        tmp[n] = '\0';
        double d = (double)v + strtod(tmp, NULL);
        if (d > 2147483647.0) {
            d = 2147483647.0;
        } else if (d < -2147483648.0) {
            d = -2147483648.0;
        }
        v = (long)(d < 0 ? d - 0.5 : d + 0.5);
    }
    if (out != NULL) {
        *out = (int)(sign * v);
    }
    return true;
}

int nr_json_get_int(const char *json, size_t len, const char *key, int fallback)
{
    const char *p = nr_find_value(json, len, key);
    if (p == NULL) {
        return fallback;
    }
    int sign = 1;
    if (*p == '-') {
        sign = -1;
        p++;
    }
    if (*p < '0' || *p > '9') {
        return fallback;
    }
    long v = 0;
    while (*p >= '0' && *p <= '9') {
        v = v * 10 + (*p - '0');
        if (v > 2147483647L) {
            v = 2147483647L;
        }
        p++;
    }
    return (int)(sign * v);
}

int nr_json_get_bool(const char *json, size_t len, const char *key, bool fallback)
{
    const char *p = nr_find_value(json, len, key);
    if (p == NULL) {
        return fallback ? 1 : 0;
    }
    if (strncmp(p, "true", 4) == 0) {
        return 1;
    }
    if (strncmp(p, "false", 5) == 0) {
        return 0;
    }
    return fallback ? 1 : 0;
}

/* ===================== HTTP 响应解析 ===================== */

static int ascii_lower(int c)
{
    return (c >= 'A' && c <= 'Z') ? c - 'A' + 'a' : c;
}

/** 大小写无关比较（不依赖 strncasecmp —— 它不在 ISO C 里）。 */
static int ascii_ncasecmp(const char *a, const char *b, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        int ca = ascii_lower((unsigned char)a[i]);
        int cb = ascii_lower((unsigned char)b[i]);
        if (ca != cb) {
            return ca - cb;
        }
    }
    return 0;
}

static int hdr_eq(const char *line, size_t line_len, const char *name)
{
    size_t nlen = strlen(name);
    if (line_len < nlen) {
        return 0;
    }
    for (size_t i = 0; i < nlen; i++) {
        if (ascii_lower((unsigned char)line[i]) != ascii_lower((unsigned char)name[i])) {
            return 0;
        }
    }
    /* 名字后面必须是 ':' 或行尾 —— 否则 "Content-Length-Foo" 会被误判成 Content-Length */
    if (line_len > nlen && line[nlen] != ':') {
        return 0;
    }
    return 1;
}

static int nr_decode_chunked(char *raw, size_t len, size_t *out_len)
{
    size_t in = 0;
    size_t out = 0;
    while (in < len) {
        size_t eol = in;
        while (eol < len && raw[eol] != '\r') {
            eol++;
        }
        if (eol >= len) {
            return -1;
        }
        char hexbuf[24];
        size_t n = eol - in;
        if (n >= sizeof hexbuf) {
            n = sizeof hexbuf - 1;
        }
        memcpy(hexbuf, raw + in, n);
        hexbuf[n] = '\0';
        char *semi = strchr(hexbuf, ';');
        if (semi != NULL) {
            *semi = '\0';
        }
        unsigned long sz = strtoul(hexbuf, NULL, 16);
        in = eol + 2; /* 跳过 CRLF */
        if (sz == 0) {
            *out_len = out;
            return 0;
        }
        if (in + sz > len) {
            return -1;
        }
        memmove(raw + out, raw + in, sz);
        out += sz;
        in += sz;
        if (in + 1 < len && raw[in] == '\r' && raw[in + 1] == '\n') {
            in += 2;
        } else if (in < len && raw[in] == '\n') {
            in += 1;
        }
    }
    return -1;
}

int nr_parse_http_response(char *raw, size_t raw_len, nr_http_response_t *out)
{
    if (raw == NULL || out == NULL) {
        return -1;
    }
    memset(out, 0, sizeof *out);

    /* 状态行 */
    if (raw_len < 12 || memcmp(raw, "HTTP/1.", 7) != 0) {
        return -1;
    }
    const char *sp = (const char *)memchr(raw, ' ', raw_len);
    if (sp == NULL) {
        return -1;
    }
    out->status_code = (int)strtol(sp + 1, NULL, 10);

    /* 头与体的分界 */
    const char *sep = NULL;
    for (size_t i = 0; i + 3 < raw_len; i++) {
        if (memcmp(raw + i, "\r\n\r\n", 4) == 0) {
            sep = raw + i;
            break;
        }
    }
    if (sep == NULL) {
        return -1;
    }
    size_t header_len = (size_t)(sep - raw);
    char *body = raw + header_len + 4;
    size_t body_len = raw_len - header_len - 4;

    /* 逐行扫头 */
    long content_length = -1;
    int chunked = 0;
    size_t pos = 0;
    while (pos < header_len) {
        /* 行的 CRLF 允许落在 header_len 边界上（最后一个头行的 CRLF 正好是
         * 分隔符 "\r\n\r\n" 的前两个字节）—— 边界判定用 `line_end < header_len`，
         * 用 `line_end + 1 < header_len` 会把最后一行少算一个字节。 */
        size_t line_end = pos;
        while (line_end < header_len && !(raw[line_end] == '\r' && raw[line_end + 1] == '\n')) {
            line_end++;
        }
        size_t line_len = line_end - pos;
        if (hdr_eq(raw + pos, line_len, "Content-Length")) {
            const char *colon = (const char *)memchr(raw + pos, ':', line_len);
            if (colon != NULL) {
                content_length = strtol(colon + 1, NULL, 10);
            }
        } else if (hdr_eq(raw + pos, line_len, "Transfer-Encoding")) {
            const char *colon = (const char *)memchr(raw + pos, ':', line_len);
            if (colon != NULL) {
                const char *v = colon + 1;
                while (v < raw + line_end && (*v == ' ' || *v == '\t')) {
                    v++;
                }
                if ((size_t)(raw + line_end - v) >= 7 && ascii_ncasecmp(v, "chunked", 7) == 0) {
                    chunked = 1;
                }
            }
        } else if (hdr_eq(raw + pos, line_len, "X-Resp-Signature")) {
            const char *colon = (const char *)memchr(raw + pos, ':', line_len);
            if (colon != NULL) {
                const char *v = colon + 1;
                while (v < raw + line_end && (*v == ' ' || *v == '\t')) {
                    v++;
                }
                size_t vlen = (size_t)(raw + line_end - v);
                while (vlen > 0 && (v[vlen - 1] == ' ' || v[vlen - 1] == '\t')) {
                    vlen--;
                }
                if (vlen >= sizeof out->resp_signature) {
                    vlen = sizeof out->resp_signature - 1;
                }
                memcpy(out->resp_signature, v, vlen);
                out->resp_signature[vlen] = '\0';
            }
        }
        pos = line_end + 2;
    }

    if (chunked) {
        size_t decoded = 0;
        if (nr_decode_chunked(body, body_len, &decoded) != 0) {
            return -1;
        }
        out->body = body;
        out->body_len = decoded;
        out->chunked = true;
        return 0;
    }

    if (content_length >= 0) {
        if ((size_t)content_length < body_len) {
            body_len = (size_t)content_length;
        }
    }
    out->body = body;
    out->body_len = body_len;
    return 0;
}
