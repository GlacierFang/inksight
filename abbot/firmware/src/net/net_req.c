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
        /* 边界检查：一次循环最多写 4 字节（UTF-8 最长），先确认放得下再写 */
        if (out != NULL && pos + 4 > out_cap) {
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
