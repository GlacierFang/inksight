/**
 * ui_data.c —— UI 层本地数据读取（TF 卡）
 *
 * 契约: abbot/doc/02 §4.2（TF 卡由存储任务独占串行化）、doc/04 §二/§三、doc/07 §3。
 *
 * 本文件是**纯 C**（不依赖 Arduino.h），主机侧可单测：
 *   - 传输层：把请求投进 `hal_storage_req_queue()`，等存储任务回执（含超时）。
 *   - 逻辑层：UTF-8 边界对齐、正文排版折行、课表解析、设置解析 —— 全部纯函数。
 *
 * 为什么走队列而不是直接调 hal_storage_*：
 *   doc/02 §4.2 规定 TF 卡由 `task_storage` 单线程串行化，UI 任务直接读卡会与
 *   存储任务抢总线（SD_MMC 不是线程安全的）。因此 UI 侧一律走请求/应答。
 *
 * 请求/应答模式照抄 net_api.cpp 的资产落盘范式：
 *   二值信号量 → 投递请求 → 等信号量 → 读应答块 → 删信号量。
 */

#include "ui_data.h"

#include <stdio.h>
#include <string.h>
#include <time.h>

#include "storage_io.h"

/* 请求应答超时：TF 卡读一个小文件正常在几十毫秒内完成，给 1.5 秒余量。
 * 超时后 UI 走温和降级，不会卡死。 */
#define UI_DATA_TIMEOUT_MS 1500

/* ============================================================
 * 传输层：请求 / 应答
 * ============================================================ */

/**
 * 投递一条存储请求并等回执。
 * 返回 UI_DATA_OK 或负的错误码（错误码与 hal_status_t 语义一致）。
 */
static int ui_data_call(uint8_t op, const char *path, uint32_t offset, uint32_t len,
                        const char *text, storage_io_t *io)
{
    QueueHandle_t q = hal_storage_req_queue();
    if (q == NULL) {
        return UI_DATA_ERR_UNSUPPORTED;
    }

    SemaphoreHandle_t done = xSemaphoreCreateBinary();
    if (done == NULL) {
        return UI_DATA_ERR_NO_SPACE;
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
    if (text != NULL && len > 0) {
        if (len > sizeof(req.text)) {
            len = (uint32_t)sizeof(req.text);
        }
        memcpy(req.text, text, len);
        req.len = len;
    }

    if (xQueueSend(q, &req, 0) != pdTRUE) {
        vSemaphoreDelete(done);
        return UI_DATA_ERR_NO_SPACE;
    }

    if (xSemaphoreTake(done, pdMS_TO_TICKS(UI_DATA_TIMEOUT_MS)) != pdTRUE) {
        /* 超时：存储任务可能还在忙。信号量由存储任务负责给，这里不删 —— 删掉会
         * 造成存储任务往已释放对象上 give。泄漏一个信号量好过踩内存。 */
        return UI_DATA_ERR_TIMEOUT;
    }
    vSemaphoreDelete(done);

    return (int)io->result;
}

/* ============================================================
 * 基础读卡原语
 * ============================================================ */

int ui_data_is_mounted(void)
{
    storage_io_t io;
    memset(&io, 0, sizeof(io));
    int rc = ui_data_call(11, NULL, 0, 0, NULL, &io);
    if (rc != UI_DATA_OK) {
        return 0;
    }
    return io.mounted ? 1 : 0;
}

int ui_data_file_size(const char *path, uint32_t *out_size)
{
    if (path == NULL || out_size == NULL) {
        return UI_DATA_ERR_INVALID;
    }
    storage_io_t io;
    memset(&io, 0, sizeof(io));
    int rc = ui_data_call(8, path, 0, 0, NULL, &io);
    if (rc == UI_DATA_OK) {
        *out_size = io.out_size;
    }
    return rc;
}

int ui_data_file_exists(const char *path)
{
    uint32_t size = 0;
    int rc = ui_data_file_size(path, &size);
    if (rc == UI_DATA_OK) {
        return UI_DATA_OK;
    }
    if (rc == UI_DATA_ERR_NOT_FOUND) {
        return UI_DATA_ERR_NOT_FOUND;
    }
    return rc;
}

int ui_data_read_text(const char *path, char *buf, uint32_t cap, uint32_t *out_len)
{
    if (path == NULL || buf == NULL || cap < 2) {
        return UI_DATA_ERR_INVALID;
    }
    storage_io_t io;
    memset(&io, 0, sizeof(io));
    io.buf = (uint8_t *)buf;
    io.buf_cap = cap;
    int rc = ui_data_call(5, path, 0, 0, NULL, &io);
    if (rc == UI_DATA_OK) {
        if (out_len != NULL) {
            *out_len = io.out_len;
        }
        return (int)io.out_len;
    }
    return rc;
}

int ui_data_read_range(const char *path, uint32_t offset, uint8_t *buf, uint32_t cap,
                       uint32_t *out_read)
{
    if (path == NULL || buf == NULL || cap == 0) {
        return UI_DATA_ERR_INVALID;
    }
    storage_io_t io;
    memset(&io, 0, sizeof(io));
    io.buf = buf;
    io.buf_cap = cap;
    int rc = ui_data_call(6, path, offset, cap, NULL, &io);
    if (rc == UI_DATA_OK) {
        if (out_read != NULL) {
            *out_read = io.out_len;
        }
        return (int)io.out_len;
    }
    /* 读到文件末尾：底层报 IO 错但 out_len 为 0 —— 这不是错误，是 EOF。 */
    if (rc == UI_DATA_ERR_IO && io.out_len == 0) {
        if (out_read != NULL) {
            *out_read = 0;
        }
        return 0;
    }
    return rc;
}

int ui_data_list_files(const char *dir, ui_data_file_t *out, uint32_t max_count,
                        uint32_t *out_count)
{
    if (dir == NULL || out == NULL || max_count == 0) {
        return UI_DATA_ERR_INVALID;
    }
    /* 名字列表缓冲区：24 个名字 × (64 字节 + 换行) 足够。 */
    static char names[UI_DATA_MAX_FILES * (UI_DATA_MAX_NAME + 2)];
    storage_io_t io;
    memset(&io, 0, sizeof(io));
    io.list_buf = names;
    io.list_cap = (uint32_t)sizeof(names);
    int rc = ui_data_call(7, dir, 0, 0, NULL, &io);
    if (rc != UI_DATA_OK) {
        return rc;
    }

    uint32_t count = 0;
    uint32_t pos = 0;
    while (pos < io.out_len && count < max_count) {
        uint32_t start = pos;
        while (pos < io.out_len && names[pos] != '\n') {
            pos++;
        }
        uint32_t n = pos - start;
        if (pos < io.out_len) {
            pos++; /* 跳过 '\n' */
        }
        if (n == 0 || n >= UI_DATA_MAX_NAME) {
            continue;
        }
        memcpy(out[count].name, names + start, n);
        out[count].name[n] = '\0';
        out[count].size = 0;
        count++;
    }
    if (out_count != NULL) {
        *out_count = count;
    }
    return UI_DATA_OK;
}

int ui_data_dir_usage(const char *dir, uint64_t *out_bytes)
{
    if (dir == NULL || out_bytes == NULL) {
        return UI_DATA_ERR_INVALID;
    }
    storage_io_t io;
    memset(&io, 0, sizeof(io));
    int rc = ui_data_call(10, dir, 0, 0, NULL, &io);
    if (rc == UI_DATA_OK) {
        *out_bytes = io.dir_bytes;
    }
    return rc;
}

int ui_data_write_text(const char *path, const char *text, uint32_t len)
{
    if (path == NULL || (text == NULL && len > 0)) {
        return UI_DATA_ERR_INVALID;
    }
    storage_io_t io;
    memset(&io, 0, sizeof(io));
    return ui_data_call(9, path, 0, len, text, &io);
}

/* ============================================================
 * UTF-8 边界对齐（doc/07 §3.2）
 * ============================================================ */

uint32_t ui_data_utf8_align_forward(const uint8_t *buf, uint32_t len, uint32_t offset)
{
    if (buf == NULL) {
        return offset;
    }
    /* 落在延续字节上（10xxxxxx）就往后步进，直到字符首字节。
     * 最多 3 步：4 字节字符的中间字节最多有 3 个。 */
    uint32_t steps = 0;
    while (offset < len && (buf[offset] & 0xC0) == 0x80 && steps < 4) {
        offset++;
        steps++;
    }
    return offset;
}

uint32_t ui_data_utf8_align_backward(const uint8_t *buf, uint32_t len, uint32_t offset)
{
    if (buf == NULL) {
        return offset;
    }
    /* 往前退到**包含该字节的那个字符**的首字节：
     * 只要当前字节本身是延续字节（10xxxxxx），就再往前退一字节。 */
    uint32_t steps = 0;
    while (offset > 0 && offset < len && steps < 4) {
        if ((buf[offset] & 0xC0) != 0x80) {
            break;
        }
        offset--;
        steps++;
    }
    return offset;
}

/* ============================================================
 * 正文排版折行（doc/07 §3.3）
 * ============================================================ */

/** 取一个 UTF-8 字符的字节长度与显示宽度（半角单位）。 */
static uint32_t ui_data_char_step(const uint8_t *buf, uint32_t len, uint32_t pos,
                                  uint32_t *out_width)
{
    uint8_t c = buf[pos];
    uint32_t n = 1;
    uint32_t width = 1;
    if (c < 0x80) {
        n = 1;
        width = 1;
    } else if ((c & 0xE0) == 0xC0) {
        n = 2;
        width = 2;
    } else if ((c & 0xF0) == 0xE0) {
        n = 3;
        width = 2;
    } else if ((c & 0xF8) == 0xF0) {
        n = 4;
        width = 2;
    }
    if (pos + n > len) {
        /* 半个字符（文件被截断）—— 当 1 字节处理，避免越界读 */
        n = 1;
        width = 1;
    }
    *out_width = width;
    return n;
}

int ui_data_layout_page(const uint8_t *buf, uint32_t len, uint32_t *out_consumed,
                         int *out_lines, char *out_text, uint32_t out_cap)
{
    if (buf == NULL || out_text == NULL || out_cap == 0) {
        return UI_DATA_ERR_INVALID;
    }

    uint32_t pos = 0;
    uint32_t out_pos = 0;
    int lines = 0;

    while (pos < len && lines < UI_DATA_PAGE_LINES) {
        uint32_t line_width = 0;
        uint32_t line_start = pos;
        bool forced_break = false;

        while (pos < len) {
            if (buf[pos] == '\n') {
                forced_break = true;
                break;
            }
            uint32_t width = 0;
            uint32_t n = ui_data_char_step(buf, len, pos, &width);
            if (line_width + width > UI_DATA_LINE_UNITS) {
                /* 放不下就换行 —— 绝不切坏多字节字符 */
                break;
            }
            line_width += width;
            pos += n;
        }

        /* 把这一行写进输出缓冲 */
        uint32_t line_len = pos - line_start;
        if (out_pos + line_len + 2 > out_cap) {
            break;
        }
        memcpy(out_text + out_pos, buf + line_start, line_len);
        out_pos += line_len;
        out_text[out_pos++] = '\n';
        lines++;

        if (forced_break) {
            pos++; /* 吃掉换行符 */
        }
        if (pos == line_start) {
            /* 一个字符都放不下（行宽为 0 的极端情况）：强制前进一字节，避免死循环 */
            pos++;
        }
    }

    out_text[out_pos] = '\0';
    if (out_consumed != NULL) {
        *out_consumed = pos;
    }
    if (out_lines != NULL) {
        *out_lines = lines;
    }
    return UI_DATA_OK;
}

/* ============================================================
 * 全局页网格扫描（向前翻页回溯用）
 *
 * 页边界是**流式**的：第 N 页的起点 = 从文件开头逐行排版时第 14N 行开始的字节偏移。
 * 因此「上一页起点」无法从局部窗口反推 —— 窗口起点一旦落在行中间，
 * 重排出来的行/页边界就与全局不一致（会把上一页算偏几个字）。
 * 这里的做法是：**用与 ui_data_layout_page 完全相同的折行规则**从文件头重扫，
 * 取出最后一个 < offset 的页起点 —— 结果一定是某一页的真起点。
 * ============================================================ */

typedef struct {
    uint32_t line_width;      /* 当前行已用宽度（半角单位） */
    int page_lines;           /* 当前页已排的行数 */
    uint32_t page_start;      /* 当前页起始字节偏移（绝对） */
    uint32_t last_page_start; /* 最近一个**排满**的页的起点（绝对） */
    bool has_last;            /* last_page_start 是否有效 */
} ui_scan_t;

/**
 * 扫描一段字节，推进行/页状态。
 * 返回**已消费**字节数（末尾不完整的 UTF-8 字符不消费，留给下一段接着扫）。
 * 折行规则与 ui_data_layout_page 一致（doc/07 §3.3）：
 *   - 每行最多 UI_DATA_LINE_UNITS 个半角单位（全角字算 2）；
 *   - 遇 '\n' 强制换行并吃掉该 '\n'；
 *   - 放不下的字符留到下一行（不切坏多字节字符）；
 *   - 每 UI_DATA_PAGE_LINES 行换一页。
 */
static uint32_t ui_scan_chunk(const uint8_t *buf, uint32_t len, uint32_t base,
                              ui_scan_t *st)
{
    uint32_t pos = 0;
    while (pos < len) {
        if (buf[pos] == '\n') {
            pos++;
            st->line_width = 0;
            st->page_lines++;
            if (st->page_lines >= UI_DATA_PAGE_LINES) {
                st->last_page_start = st->page_start;
                st->has_last = true;
                st->page_lines = 0;
                st->page_start = base + pos;
            }
            continue;
        }

        uint8_t c = buf[pos];
        uint32_t n = 1;
        uint32_t w = 1;
        if (c < 0x80) {
            n = 1;
            w = 1;
        } else if ((c & 0xE0) == 0xC0) {
            n = 2;
            w = 2;
        } else if ((c & 0xF0) == 0xE0) {
            n = 3;
            w = 2;
        } else if ((c & 0xF8) == 0xF0) {
            n = 4;
            w = 2;
        }
        if (pos + n > len) {
            break; /* 字符被截断 —— 留给下一段，绝不切半个字 */
        }
        if (st->line_width + w > UI_DATA_LINE_UNITS) {
            /* 折行：结束当前行，这个字符留给下一行 */
            st->line_width = 0;
            st->page_lines++;
            if (st->page_lines >= UI_DATA_PAGE_LINES) {
                st->last_page_start = st->page_start;
                st->has_last = true;
                st->page_lines = 0;
                st->page_start = base + pos;
            }
            continue;
        }
        st->line_width += w;
        pos += n;
    }
    return pos;
}

int ui_data_layout_prev_start(const uint8_t *buf, uint32_t len, uint32_t current,
                               uint32_t *out_start)
{
    if (buf == NULL || out_start == NULL) {
        return UI_DATA_ERR_INVALID;
    }
    if (current == 0) {
        *out_start = 0;
        return UI_DATA_OK;
    }

    /* 从窗口开头反复排版，找出**最后一个起点小于 current** 的页。
     * 页起点序列形如 0, 57, 114…；current 落在哪一页的区间里，
     * 它的上一页起点就是答案（doc/07 §3.3）。 */
    uint32_t pos = 0;
    uint32_t last_start = 0;
    uint32_t limit = current;

    /* 排版用的临时缓冲走静态（不能占 UI 任务栈） */
    static char tmp[UI_DATA_PAGE_BYTES * 2];

    while (pos < limit) {
        uint32_t consumed = 0;
        int lines = 0;
        uint32_t cap = (uint32_t)sizeof(tmp);
        if (ui_data_layout_page(buf + pos, len - pos, &consumed, &lines, tmp, cap) != UI_DATA_OK) {
            break;
        }
        if (consumed == 0) {
            break;
        }
        uint32_t next = pos + consumed;
        if (next > limit) {
            /* current 落在「起点为 pos」这一页的区间里 -> 上一页是 last_start */
            break;
        }
        last_start = pos;
        pos = next;
    }

    *out_start = last_start;
    return UI_DATA_OK;
}

/* ============================================================
 * 分页读正文
 * ============================================================ */

int ui_data_load_page(const char *path, uint32_t offset, uint32_t *out_consumed,
                       int *out_lines, char *out_text, uint32_t out_cap)
{
    if (path == NULL || out_text == NULL || out_cap == 0) {
        return UI_DATA_ERR_INVALID;
    }
    static uint8_t buf[UI_DATA_PAGE_BYTES];
    uint32_t got = 0;
    int rc = ui_data_read_range(path, offset, buf, (uint32_t)sizeof(buf), &got);
    if (rc < 0) {
        return rc;
    }
    if (got == 0) {
        /* 到文件末尾 */
        if (out_consumed != NULL) {
            *out_consumed = 0;
        }
        if (out_lines != NULL) {
            *out_lines = 0;
        }
        out_text[0] = '\0';
        return UI_DATA_OK;
    }
    return ui_data_layout_page(buf, got, out_consumed, out_lines, out_text, out_cap);
}

int ui_data_prev_page_offset(const char *path, uint32_t offset, uint32_t *out_start)
{
    if (path == NULL || out_start == NULL) {
        return UI_DATA_ERR_INVALID;
    }
    if (offset == 0) {
        *out_start = 0;
        return UI_DATA_OK;
    }

    /* 页网格是全局的（见上方「全局页网格扫描」注释）：
     * 从文件头重扫一遍，取最后一个 < offset 的页起点。
     * 往前多扫一页，保证「offset 所在的那一页」已经排完、页起点已被记录。 */
    uint32_t scan_limit = offset + UI_DATA_PAGE_BYTES;
    uint32_t file_pos = 0;
    uint32_t best = 0;
    ui_scan_t st;
    memset(&st, 0, sizeof(st));

    static uint8_t chunk[UI_DATA_PAGE_BYTES];
    while (file_pos < scan_limit) {
        uint32_t want = scan_limit - file_pos;
        if (want > (uint32_t)sizeof(chunk)) {
            want = (uint32_t)sizeof(chunk);
        }
        uint32_t got = 0;
        int rc = ui_data_read_range(path, file_pos, chunk, want, &got);
        if (rc < 0) {
            return rc;
        }
        if (got == 0) {
            break; /* 到文件末尾 */
        }
        uint32_t used = ui_scan_chunk(chunk, got, file_pos, &st);
        if (used == 0) {
            break; /* 卡住保护：不可能推进就收手 */
        }
        file_pos += used;
        if (st.has_last && st.last_page_start < offset) {
            best = st.last_page_start;
        }
    }
    /* 文件末尾那一页（可能没排满）也算一页 */
    if (st.page_start > 0 && st.page_start < offset && st.page_start > best) {
        best = st.page_start;
    }
    *out_start = best;
    return UI_DATA_OK;
}

/* ============================================================
 * 极简 JSON 扫描工具（只取本模块需要的形状，不引第三方库）
 * ============================================================ */

/** 在 [start,end) 内找 "key" 的值起点（跳过空白与冒号）。找不到返回 NULL。 */
static const char *ui_json_find_value(const char *json, size_t start, size_t end,
                                       const char *key, size_t *val_len)
{
    if (json == NULL || key == NULL) {
        return NULL;
    }
    size_t klen = strlen(key);
    for (size_t i = start; i < end; i++) {
        if (json[i] != '"') {
            continue;
        }
        if (i + klen + 2 > end) {
            break;
        }
        if (memcmp(json + i + 1, key, klen) != 0) {
            continue;
        }
        if (json[i + 1 + klen] != '"') {
            continue;
        }
        size_t j = i + klen + 2;
        while (j < end && (json[j] == ' ' || json[j] == '\t' || json[j] == '\n' || json[j] == '\r')) {
            j++;
        }
        if (j >= end || json[j] != ':') {
            continue;
        }
        j++;
        while (j < end && (json[j] == ' ' || json[j] == '\t' || json[j] == '\n' || json[j] == '\r')) {
            j++;
        }
        if (j >= end) {
            return NULL;
        }
        size_t vs = j;
        /* 值的结束位置：字符串读到收尾引号，容器读到配对的括号，其余读到分隔符 */
        if (json[j] == '"') {
            j++;
            while (j < end) {
                if (json[j] == '\\' && j + 1 < end) {
                    j += 2;
                    continue;
                }
                if (json[j] == '"') {
                    j++;
                    break;
                }
                j++;
            }
        } else if (json[j] == '[' || json[j] == '{') {
            char open = json[j];
            char close = (open == '[') ? ']' : '}';
            int depth = 0;
            bool in_str = false;
            while (j < end) {
                char c = json[j];
                if (in_str) {
                    if (c == '\\') {
                        j++;
                    } else if (c == '"') {
                        in_str = false;
                    }
                } else if (c == '"') {
                    in_str = true;
                } else if (c == open) {
                    depth++;
                } else if (c == close) {
                    depth--;
                    if (depth == 0) {
                        j++;
                        break;
                    }
                }
                j++;
            }
        } else {
            while (j < end && json[j] != ',' && json[j] != '}' && json[j] != ']') {
                j++;
            }
        }
        if (val_len != NULL) {
            *val_len = j - vs;
        }
        return json + vs;
    }
    return NULL;
}

/** 把 JSON 字符串值（带引号）抄进 C 字符串；处理基本转义。 */
static bool ui_json_copy_string(const char *p, size_t len, char *out, size_t out_cap)
{
    if (p == NULL || out == NULL || out_cap == 0) {
        return false;
    }
    if (len < 2 || p[0] != '"') {
        return false;
    }
    size_t o = 0;
    for (size_t i = 1; i + 1 < len && o + 1 < out_cap; i++) {
        char c = p[i];
        if (c == '\\' && i + 2 < len) {
            i++;
            char e = p[i];
            switch (e) {
            case 'n': c = '\n'; break;
            case 't': c = '\t'; break;
            case 'r': c = '\r'; break;
            case 'b': c = '\b'; break;
            case 'f': c = '\f'; break;
            case 'u': {
                /* 只取 BMP 里的基本汉字区，其余交给字库占位 */
                unsigned cp = 0;
                for (int k = 1; k <= 4 && i + (size_t)k < len; k++) {
                    char h = p[i + (size_t)k];
                    cp <<= 4;
                    if (h >= '0' && h <= '9') {
                        cp |= (unsigned)(h - '0');
                    } else if (h >= 'a' && h <= 'f') {
                        cp |= (unsigned)(h - 'a' + 10);
                    } else if (h >= 'A' && h <= 'F') {
                        cp |= (unsigned)(h - 'A' + 10);
                    }
                }
                i += 4;
                if (cp < 0x80) {
                    c = (char)cp;
                } else if (cp < 0x800) {
                    if (o + 2 >= out_cap) {
                        break;
                    }
                    out[o++] = (char)(0xC0 | (cp >> 6));
                    out[o++] = (char)(0x80 | (cp & 0x3F));
                    continue;
                } else {
                    if (o + 3 >= out_cap) {
                        break;
                    }
                    out[o++] = (char)(0xE0 | (cp >> 12));
                    out[o++] = (char)(0x80 | ((cp >> 6) & 0x3F));
                    out[o++] = (char)(0x80 | (cp & 0x3F));
                    continue;
                }
                break;
            }
            default: c = e; break;
            }
        }
        out[o++] = c;
    }
    out[o] = '\0';
    return true;
}

/** 把 JSON 数字值读成 int。 */
static int ui_json_copy_int(const char *p, size_t len, int fallback)
{
    if (p == NULL || len == 0) {
        return fallback;
    }
    int v = 0;
    bool neg = false;
    size_t i = 0;
    if (p[0] == '-') {
        neg = true;
        i = 1;
    }
    if (i >= len || p[i] < '0' || p[i] > '9') {
        return fallback;
    }
    for (; i < len && p[i] >= '0' && p[i] <= '9'; i++) {
        v = v * 10 + (p[i] - '0');
    }
    return neg ? -v : v;
}

/**
 * 收集 JSON 数组里的各个对象元素。
 * 返回收集到的个数（可能为 0）。
 */
static int ui_json_collect_objects(const char *arr, size_t len, const char **out_start,
                                   size_t *out_len, int max)
{
    int count = 0;
    size_t k = 0;
    while (k < len && count < max) {
        if (arr[k] != '{') {
            k++;
            continue;
        }
        size_t depth = 0;
        bool in_str = false;
        size_t s = k;
        while (k < len) {
            char c = arr[k];
            if (in_str) {
                if (c == '\\') {
                    k++;
                } else if (c == '"') {
                    in_str = false;
                }
            } else if (c == '"') {
                in_str = true;
            } else if (c == '{') {
                depth++;
            } else if (c == '}') {
                depth--;
                if (depth == 0) {
                    k++;
                    break;
                }
            }
            k++;
        }
        out_start[count] = arr + s;
        out_len[count] = k - s;
        count++;
    }
    return count;
}

/** 判断一个 JSON 数组里是否含有给定字符串元素。 */
static bool ui_json_array_has_string(const char *arr, size_t len, const char *needle)
{
    if (arr == NULL || needle == NULL) {
        return false;
    }
    char quoted[64];
    int pn = snprintf(quoted, sizeof(quoted), "\"%s\"", needle);
    if (pn <= 0 || (size_t)pn >= sizeof(quoted)) {
        return false;
    }
    for (size_t i = 0; i + (size_t)pn <= len; i++) {
        if (memcmp(arr + i, quoted, (size_t)pn) == 0) {
            return true;
        }
    }
    return false;
}

/** 从 "YYYY-MM-DD" 解析出年 / 月 / 日。 */
static bool ui_parse_date(const char *s, int *y, int *m, int *d)
{
    if (s == NULL || y == NULL || m == NULL || d == NULL) {
        return false;
    }
    if (strlen(s) < 10) {
        return false;
    }
    *y = (s[0] - '0') * 1000 + (s[1] - '0') * 100 + (s[2] - '0') * 10 + (s[3] - '0');
    *m = (s[5] - '0') * 10 + (s[6] - '0');
    *d = (s[8] - '0') * 10 + (s[9] - '0');
    return (*m >= 1 && *m <= 12 && *d >= 1 && *d <= 31);
}

/** 公历日期 -> 天数（Howard Hinnant 的 days_from_civil 算法）。 */
static long ui_days_from_civil(int y, int m, int d)
{
    y -= (m <= 2);
    long era = (y >= 0 ? y : y - 399) / 400;
    unsigned yoe = (unsigned)(y - era * 400);
    unsigned doy = (unsigned)((153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + d - 1);
    unsigned doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    return era * 146097L + (long)doe - 719468L;
}

/** ISO 星期：周一=1 … 周日=7。 */
static int ui_iso_weekday(int y, int m, int d)
{
    long days = ui_days_from_civil(y, m, d);
    /* 1970-01-01 是周四（ISO=4） */
    return (int)((days % 7 + 7 + 3) % 7) + 1;
}

/* ============================================================
 * 课表解析（doc/04 §3.2）
 * ============================================================ */

int ui_data_parse_schedule(const char *json, size_t len, const ui_data_date_t *date,
                            ui_data_lesson_t *out, uint32_t max_count, uint32_t *out_count)
{
    if (json == NULL || date == NULL || out == NULL || out_count == NULL || max_count == 0) {
        return UI_DATA_ERR_INVALID;
    }
    *out_count = 0;

    int weekday = ui_iso_weekday(date->year, date->month, date->day);
    char today_key[16];
    snprintf(today_key, sizeof(today_key), "%04d-%02d-%02d", date->year, date->month, date->day);

    /* 1) 节假日 -> 休息日 */
    size_t vlen = 0;
    const char *v = ui_json_find_value(json, 0, len, "holidays", &vlen);
    if (v != NULL && ui_json_array_has_string(v, vlen, today_key)) {
        return UI_DATA_OK; /* 休息日 */
    }

    /* 2) 周末标记：weekend 是 [6,7] 这样的数组 */
    v = ui_json_find_value(json, 0, len, "weekend", &vlen);
    if (v != NULL) {
        for (size_t i = 0; i < vlen; i++) {
            if (v[i] < '0' || v[i] > '9') {
                continue;
            }
            if (i > 0 && v[i - 1] != '[' && v[i - 1] != ',' && v[i - 1] != ' ') {
                continue;
            }
            int val = 0;
            size_t j = i;
            while (j < vlen && v[j] >= '0' && v[j] <= '9') {
                val = val * 10 + (v[j] - '0');
                j++;
            }
            if (val == weekday) {
                return UI_DATA_OK; /* 休息日 */
            }
        }
    }

    /* 3) 教学周奇偶：semester_start_date 起算 */
    bool odd_week = false;
    bool have_week_parity = false;
    v = ui_json_find_value(json, 0, len, "semester_start_date", &vlen);
    if (v != NULL) {
        char sem[24];
        sem[0] = '\0';
        ui_json_copy_string(v, vlen, sem, sizeof(sem));
        int sy = 0, sm = 0, sd = 0;
        if (ui_parse_date(sem, &sy, &sm, &sd)) {
            long d0 = ui_days_from_civil(sy, sm, sd);
            long d1 = ui_days_from_civil(date->year, date->month, date->day);
            long diff = d1 - d0;
            if (diff < 0) {
                diff = 0;
            }
            odd_week = ((diff / 7) % 2) == 0;
            have_week_parity = true;
        }
    }

    /* 4) 找当天的课表条目：先 override_days（按日期），再 days（按星期） */
    const char *day_list = NULL;
    size_t day_len = 0;
    char key[24];

    v = ui_json_find_value(json, 0, len, "override_days", &vlen);
    if (v != NULL) {
        size_t olen = 0;
        const char *ov = ui_json_find_value(v, 0, vlen, today_key, &olen);
        if (ov != NULL) {
            day_list = ov;
            day_len = olen;
        }
    }
    if (day_list == NULL) {
        v = ui_json_find_value(json, 0, len, "days", &vlen);
        if (v != NULL) {
            snprintf(key, sizeof(key), "%d", weekday);
            size_t olen = 0;
            const char *ov = ui_json_find_value(v, 0, vlen, key, &olen);
            if (ov != NULL) {
                day_list = ov;
                day_len = olen;
            }
        }
    }
    if (day_list == NULL) {
        return UI_DATA_OK; /* 没排课 */
    }

    /* 5) 节次时间表 */
    struct {
        int period;
        char start[UI_DATA_MAX_TIME];
        char end[UI_DATA_MAX_TIME];
    } periods[16];
    int period_count = 0;
    v = ui_json_find_value(json, 0, len, "period_times", &vlen);
    if (v != NULL) {
        const char *starts[16];
        size_t lens[16];
        int n = ui_json_collect_objects(v, vlen, starts, lens, 16);
        for (int i = 0; i < n && period_count < 16; i++) {
            size_t plen = 0;
            const char *pv = ui_json_find_value(starts[i], 0, lens[i], "period", &plen);
            int period = ui_json_copy_int(pv, plen, 0);
            if (period <= 0) {
                continue;
            }
            periods[period_count].period = period;
            plen = 0;
            pv = ui_json_find_value(starts[i], 0, lens[i], "start", &plen);
            ui_json_copy_string(pv, plen, periods[period_count].start,
                                sizeof(periods[period_count].start));
            plen = 0;
            pv = ui_json_find_value(starts[i], 0, lens[i], "end", &plen);
            ui_json_copy_string(pv, plen, periods[period_count].end,
                                sizeof(periods[period_count].end));
            period_count++;
        }
    }

    /* 6) 逐个课程条目 */
    const char *starts[UI_DATA_MAX_LESSONS * 2];
    size_t lens[UI_DATA_MAX_LESSONS * 2];
    int total = ui_json_collect_objects(day_list, day_len, starts, lens,
                                        (int)(sizeof(starts) / sizeof(starts[0])));
    uint32_t count = 0;
    for (int i = 0; i < total && count < max_count; i++) {
        size_t plen = 0;
        const char *pv = ui_json_find_value(starts[i], 0, lens[i], "period", &plen);
        int period = ui_json_copy_int(pv, plen, 0);

        char subject[UI_DATA_MAX_SUBJECT_LEN];
        subject[0] = '\0';
        plen = 0;
        pv = ui_json_find_value(starts[i], 0, lens[i], "subject", &plen);
        ui_json_copy_string(pv, plen, subject, sizeof(subject));

        char week_type[8];
        week_type[0] = '\0';
        plen = 0;
        pv = ui_json_find_value(starts[i], 0, lens[i], "week_type", &plen);
        ui_json_copy_string(pv, plen, week_type, sizeof(week_type));

        /* 单双周过滤（doc/04 §3.2 规则 2） */
        bool keep = true;
        if (week_type[0] == 'O' && week_type[1] == 'D') {
            keep = have_week_parity ? odd_week : true;
        } else if (week_type[0] == 'E' && week_type[1] == 'V') {
            keep = have_week_parity ? !odd_week : true;
        }
        if (!keep || subject[0] == '\0') {
            continue;
        }

        out[count].period = (uint8_t)period;
        snprintf(out[count].subject, sizeof(out[count].subject), "%s", subject);
        out[count].start[0] = '\0';
        out[count].end[0] = '\0';
        for (int k = 0; k < period_count; k++) {
            if (periods[k].period == period) {
                snprintf(out[count].start, sizeof(out[count].start), "%s", periods[k].start);
                snprintf(out[count].end, sizeof(out[count].end), "%s", periods[k].end);
                break;
            }
        }
        count++;
    }

    *out_count = count;
    return UI_DATA_OK;
}

int ui_data_read_schedule(const ui_data_date_t *date, ui_data_lesson_t *out,
                           uint32_t max_count, uint32_t *out_count)
{
    if (date == NULL || out == NULL || out_count == NULL) {
        return UI_DATA_ERR_INVALID;
    }
    *out_count = 0;

    /* schedule.json 可能不大，但给足 4KB —— doc/04 §3.2 的示例就有 1KB 多。 */
    static char buf[4096];
    uint32_t n = 0;
    int rc = ui_data_read_text(UI_DATA_PATH_SCHEDULE, buf, (uint32_t)sizeof(buf), &n);
    if (rc < 0) {
        return rc;
    }
    if (n == 0) {
        return UI_DATA_OK;
    }
    return ui_data_parse_schedule(buf, (size_t)n, date, out, max_count, out_count);
}

/* ============================================================
 * 今天几号
 * ============================================================ */

/**
 * 取设备当前日期。NTP 校时后（doc/04 §1）拿到的是 UTC+8 的本地时间。
 * 没校过时返回 0 年 —— 上层据此判断「今天不可信」，不编课程。
 */
int ui_data_today(ui_data_date_t *out)
{
    if (out == NULL) {
        return UI_DATA_ERR_INVALID;
    }
    memset(out, 0, sizeof(*out));
    time_t now = time(NULL);
    if (now < 1700000000L) {
        return UI_DATA_ERR_NOT_FOUND; /* 未校时 */
    }
    struct tm tm_now;
    if (localtime_r(&now, &tm_now) == NULL) {
        return UI_DATA_ERR_IO;
    }
    out->year = tm_now.tm_year + 1900;
    out->month = tm_now.tm_mon + 1;
    out->day = tm_now.tm_mday;
    return UI_DATA_OK;
}

/**
 * 取当前本地时刻（当天第几分钟）。
 *
 * 宵禁判定要的就是这个值 —— 未校时（系统时间不对）时返回错误，
 * 上层据此**不锁**（宁可不锁，也不要因为设备没校时而误锁孩子的阅读）。
 */
int ui_data_local_minutes(int *out_min)
{
    if (out_min == NULL) {
        return UI_DATA_ERR_INVALID;
    }
    time_t now = time(NULL);
    if (now < 1700000000L) {
        return UI_DATA_ERR_NOT_FOUND; /* 未校时 */
    }
    struct tm tm_now;
    if (localtime_r(&now, &tm_now) == NULL) {
        return UI_DATA_ERR_IO;
    }
    int minutes = tm_now.tm_hour * 60 + tm_now.tm_min;
    if (minutes < 0 || minutes > 1439) {
        return UI_DATA_ERR_IO;
    }
    *out_min = minutes;
    return UI_DATA_OK;
}

/** 取今天的日期串 "YYYY-MM-DD"（doc/07 §2.4 的 daily_date）。 */
int ui_data_today_str(char *out, size_t cap)
{
    if (out == NULL || cap < UI_DATA_MAX_DATE_STR) {
        return UI_DATA_ERR_INVALID;
    }
    ui_data_date_t d;
    int rc = ui_data_today(&d);
    if (rc != UI_DATA_OK) {
        return rc;
    }
    snprintf(out, cap, "%04d-%02d-%02d", d.year, d.month, d.day);
    return UI_DATA_OK;
}

/* ============================================================
 * 本地设置（番茄钟时长）
 * ============================================================ */

int ui_data_parse_focus_minutes(const char *json, size_t len, int fallback)
{
    if (json == NULL) {
        return fallback;
    }
    size_t vlen = 0;
    const char *v = ui_json_find_value(json, 0, len, "focus_minutes", &vlen);
    if (v == NULL) {
        return fallback;
    }
    int minutes = ui_json_copy_int(v, vlen, fallback);
    if (minutes < UI_DATA_FOCUS_MIN) {
        minutes = UI_DATA_FOCUS_MIN;
    }
    if (minutes > UI_DATA_FOCUS_MAX) {
        minutes = UI_DATA_FOCUS_MAX;
    }
    return minutes;
}

int ui_data_focus_minutes_get(int *out_minutes)
{
    if (out_minutes == NULL) {
        return UI_DATA_ERR_INVALID;
    }
    *out_minutes = UI_DATA_FOCUS_DEFAULT;

    static char buf[256];
    uint32_t n = 0;
    int rc = ui_data_read_text(UI_DATA_PATH_SETTINGS, buf, (uint32_t)sizeof(buf), &n);
    if (rc < 0 || n == 0) {
        /* 没有设置文件 = 用默认值，不算错 */
        return UI_DATA_OK;
    }
    *out_minutes = ui_data_parse_focus_minutes(buf, (size_t)n, UI_DATA_FOCUS_DEFAULT);
    return UI_DATA_OK;
}

int ui_data_focus_minutes_set(int minutes)
{
    if (minutes < UI_DATA_FOCUS_MIN) {
        minutes = UI_DATA_FOCUS_MIN;
    }
    if (minutes > UI_DATA_FOCUS_MAX) {
        minutes = UI_DATA_FOCUS_MAX;
    }
    /* 读-改-写：settings.json 里可能还有城市等字段，不能整文件覆盖。 */
    static char old[256];
    uint32_t old_len = 0;
    char city[UI_DATA_CITY_MAX];
    city[0] = '\0';
    /* 注意：ui_data_read_text() 成功时返回的是**读到的字节数**（>0），
     * 不是 UI_DATA_OK(0)。这里原来判 `== UI_DATA_OK` → 永远不成立 →
     * 读不到旧文件 → 写专注时长时把城市静默丢掉。 */
    if (ui_data_read_text(UI_DATA_PATH_SETTINGS, old, (uint32_t)sizeof(old), &old_len) > 0 &&
        old_len > 0) {
        size_t vlen = 0;
        const char *v = ui_json_find_value(old, 0, old_len, "city", &vlen);
        if (v != NULL) {
            ui_json_copy_string(v, vlen, city, sizeof(city));
        }
    }
    char text[320];
    int n;
    if (city[0] != '\0') {
        n = snprintf(text, sizeof(text),
                     "{\n  \"schema_version\": 1,\n  \"focus_minutes\": %d,\n  \"city\": \"%s\"\n}\n",
                     minutes, city);
    } else {
        n = snprintf(text, sizeof(text),
                     "{\n  \"schema_version\": 1,\n  \"focus_minutes\": %d\n}\n", minutes);
    }
    if (n <= 0 || (size_t)n >= sizeof(text)) {
        return UI_DATA_ERR_INVALID;
    }
    return ui_data_write_text(UI_DATA_PATH_SETTINGS, text, (uint32_t)n);
}

/* ============================================================
 * 城市（doc/05 §5.2）
 * ============================================================ */

int ui_data_city_get(char *out, size_t cap)
{
    if (out == NULL || cap == 0) {
        return UI_DATA_ERR_INVALID;
    }
    out[0] = '\0';

    static char buf[256];
    uint32_t n = 0;
    int rc = ui_data_read_text(UI_DATA_PATH_SETTINGS, buf, (uint32_t)sizeof(buf), &n);
    if (rc < 0 || n == 0) {
        /* 没有设置文件 = 还没设置过城市，不算错 */
        return UI_DATA_OK;
    }
    size_t vlen = 0;
    const char *v = ui_json_find_value(buf, 0, n, "city", &vlen);
    if (v == NULL) {
        return UI_DATA_OK;
    }
    if (!ui_json_copy_string(v, vlen, out, cap)) {
        out[0] = '\0';
    }
    return UI_DATA_OK;
}

int ui_data_city_set(const char *city)
{
    if (city == NULL || city[0] == '\0') {
        return UI_DATA_ERR_INVALID;
    }
    /* 读-改-写：保留 settings.json 里已有的其它字段（专注时长）。 */
    static char old[256];
    uint32_t old_len = 0;
    int focus = UI_DATA_FOCUS_DEFAULT;
    char old_city[UI_DATA_CITY_MAX];
    old_city[0] = '\0';
    /* 同上：成功返回值是字节数（>0），不是 UI_DATA_OK(0)。
     * 原来判 `== UI_DATA_OK` → 读不到旧文件 → 写城市时把专注时长冲回默认 25。 */
    if (ui_data_read_text(UI_DATA_PATH_SETTINGS, old, (uint32_t)sizeof(old), &old_len) > 0 &&
        old_len > 0) {
        focus = ui_data_parse_focus_minutes(old, (size_t)old_len, UI_DATA_FOCUS_DEFAULT);
        size_t vlen = 0;
        const char *v = ui_json_find_value(old, 0, old_len, "city", &vlen);
        if (v != NULL) {
            ui_json_copy_string(v, vlen, old_city, sizeof(old_city));
        }
    }
    char text[320];
    int n = snprintf(text, sizeof(text),
                     "{\n  \"schema_version\": 1,\n  \"focus_minutes\": %d,\n  \"city\": \"%s\"\n}\n",
                     focus, city);
    if (n <= 0 || (size_t)n >= sizeof(text)) {
        return UI_DATA_ERR_INVALID;
    }
    return ui_data_write_text(UI_DATA_PATH_SETTINGS, text, (uint32_t)n);
}

/* ============================================================
 * 天气快照（离线兜底）
 * ============================================================ */

int ui_data_weather_cache_get(char *out, size_t cap, uint32_t *out_len)
{
    if (out == NULL || cap == 0) {
        return UI_DATA_ERR_INVALID;
    }
    out[0] = '\0';
    if (out_len != NULL) {
        *out_len = 0;
    }
    static char buf[512];
    uint32_t n = 0;
    int rc = ui_data_read_text(UI_DATA_PATH_WEATHER, buf, (uint32_t)sizeof(buf), &n);
    if (rc < 0 || n == 0) {
        return rc < 0 ? rc : UI_DATA_ERR_NOT_FOUND;
    }
    if ((size_t)n >= cap) {
        n = (uint32_t)(cap - 1);
    }
    memcpy(out, buf, n);
    out[n] = '\0';
    if (out_len != NULL) {
        *out_len = n;
    }
    return UI_DATA_OK;
}

int ui_data_weather_cache_set(const char *text, uint32_t len)
{
    if (text == NULL || len == 0) {
        return UI_DATA_ERR_INVALID;
    }
    return ui_data_write_text(UI_DATA_PATH_WEATHER, text, len);
}

/* ============================================================
 * 档案（离线降级用）
 * ============================================================ */

int ui_data_read_paired(int *out_paired)
{
    if (out_paired == NULL) {
        return UI_DATA_ERR_INVALID;
    }
    *out_paired = 0;

    static char buf[1024];
    uint32_t n = 0;
    int rc = ui_data_read_text(UI_DATA_PATH_PROFILE, buf, (uint32_t)sizeof(buf), &n);
    if (rc < 0 || n == 0) {
        return UI_DATA_OK;
    }
    size_t vlen = 0;
    const char *v = ui_json_find_value(buf, 0, n, "paired", &vlen);
    if (v != NULL && vlen >= 4 && memcmp(v, "true", 4) == 0) {
        *out_paired = 1;
    }
    return UI_DATA_OK;
}
