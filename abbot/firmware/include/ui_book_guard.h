/**
 * ui_book_guard.h —— 电子书阅读护栏的**纯逻辑层**
 *
 * 契约来源: abbot/doc/07 §2.3（阅读进度）/ §2.4（防沉迷护栏）。
 *
 * 本头是**纯 C**：不依赖 Arduino.h / LVGL / WiFi / TF 卡，主机侧可单测。
 * 只放纯函数与纯解析：
 *   - 读卡与落盘一律由 ui_data 层（走 hal_storage 请求/应答）负责；
 *   - 本模块不拼用户文案（文案由 UI 层按 doc/03 §四 出）。
 *
 * 铁律:
 *   1. percent **必须实时重算**（offset * 100 / total），**严禁直接信 JSON 里的 percent**。
 *   2. 宵禁跨午夜区间（start > end）的判定是 `now >= start || now < end`。
 *
 * 落盘格式 = doc/07 §2.3 的 bookmarks.json：
 *   {
 *     "schema_version": 1,
 *     "daily_date": "2026-11-23",
 *     "daily_used_sec": 1320,
 *     "books": { "shiji_01.txt": { "byte_offset": 15420, "total_bytes": 482900,
 *                                 "percent": 3, "last_read_ts": 1795412000 } }
 *   }
 */
#ifndef ABBOT_UI_BOOK_GUARD_H
#define ABBOT_UI_BOOK_GUARD_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ===================== 默认值（doc/07 §2.4） ===================== */
#define UI_BOOK_CURFEW_START_DEFAULT_MIN  (22 * 60)   /* 22:00 */
#define UI_BOOK_CURFEW_END_DEFAULT_MIN    (6 * 60 + 30) /* 06:30 */
#define UI_BOOK_DAILY_LIMIT_DEFAULT_MIN   45

/* 进度表最多记多少本书 */
#define UI_BOOK_MARK_MAX  16
/* 书名长度上限（含 '\0'） */
#define UI_BOOK_MARK_NAME_MAX 64
/* 日期串长度上限（"YYYY-MM-DD" + '\0'） */
#define UI_BOOK_DATE_MAX  12

/* ===================== 数据结构 ===================== */

/** 一天中的时刻（换算成 0..1439 的分钟）。 */
typedef struct {
    int hour;   /* 0..23 */
    int minute; /* 0..59 */
} ui_book_time_t;

/** 宵禁时段（一天中的第几分钟）。valid=false 表示配置缺失，用默认值。 */
typedef struct {
    int start_min;
    int end_min;
    bool valid;
} ui_book_curfew_t;

/** 防沉迷护栏配置（来自 /config/profile.json）。 */
typedef struct {
    ui_book_curfew_t curfew;
    int daily_limit_min;
} ui_book_guard_t;

/** 一本书的阅读进度（每本书只维护**唯一最新进度**）。 */
typedef struct {
    char name[UI_BOOK_MARK_NAME_MAX];
    uint32_t offset;      /**< 当前字节偏移（doc/07 §2.3 的 byte_offset） */
    uint32_t total_bytes; /**< 全书字节数 */
    uint32_t last_read_ts;/**< 最后阅读时刻（epoch 秒，0 = 未知） */
} ui_book_mark_t;

/** 进度表（/records/bookmarks.json 的内存形态）。 */
typedef struct {
    int schema_version;              /**< doc/07 §2.3 的 schema_version */
    char daily_date[UI_BOOK_DATE_MAX];/**< 当日日期 "YYYY-MM-DD" */
    uint32_t daily_used_sec;         /**< 当日累计阅读秒数（精确计时累加） */
    ui_book_mark_t items[UI_BOOK_MARK_MAX];
    int count;
} ui_bookmarks_t;

/* ===================== 时间解析 ===================== */
/**
 * 解析 "HH:MM" → 当天第几分钟（0..1439）。
 * 格式不对返回 -1（调用方回退默认值）。
 */
int ui_book_parse_hhmm(const char *text);

/** 把 (hour, minute) 折成当天第几分钟；hour/minute 越界返回 -1。 */
int ui_book_time_to_minutes(const ui_book_time_t *t);

/* ===================== 护栏配置解析 ===================== */
/**
 * 从 /config/profile.json 的 JSON 里解析护栏配置。
 *
 * 缺 `curfew_window` 或 `daily_book_limit_min` 时用默认值
 * （22:00 / 06:30 / 45 分钟），**不因字段缺失而失败**。
 *
 * `curfew_window` 是两个字符串组成的数组：`["22:00","06:30"]`。
 */
void ui_book_guard_parse(const char *json, size_t len, ui_book_guard_t *out);

/** 用默认配置填一份护栏配置。 */
void ui_book_guard_defaults(ui_book_guard_t *out);

/**
 * 当前时刻是否落在宵禁时段内。
 *
 * - 同日区间（start <= end）：`now >= start && now < end`；
 * - **跨午夜区间（start > end）**：`now >= start || now < end`。
 *
 * `now_min` 是当天第几分钟。配置无效时返回 false（不锁）。
 */
bool ui_book_curfew_active(const ui_book_curfew_t *curfew, int now_min);

/* ===================== 每日时长上限 ===================== */
/**
 * 今日已读秒数是否已经达到上限。
 * `limit_min <= 0` 视为不限制。
 */
bool ui_book_daily_limit_reached(const ui_book_guard_t *guard, uint32_t read_seconds);

/* ===================== 进度重算 ===================== */
/**
 * 实时重算阅读百分比：`offset * 100 / total_bytes`，钳位 0..100。
 * `total_bytes == 0` 返回 0。
 *
 * **不要把 JSON 里存的 percent 直接拿来显示** —— 必须调这个函数重算。
 */
int ui_book_progress_percent(uint32_t offset, uint32_t total_bytes);

/* ===================== 进度表解析与序列化 ===================== */
/**
 * 解析 /records/bookmarks.json（doc/07 §2.3）。
 *
 * 结构：
 *   {"schema_version":1,"daily_date":"YYYY-MM-DD","daily_used_sec":N,
 *    "books":{"书名":{"byte_offset":1234,"total_bytes":5678,"last_read_ts":1795412000}}}
 *
 * `byte_offset` 也接受旧别名 `offset`；**`percent` 字段一律忽略**（只作导出快照）。
 * 结构不对时 `out->count = 0`（上层当作「没有进度」）。
 */
void ui_bookmarks_parse(const char *json, size_t len, ui_bookmarks_t *out);

/**
 * 结构上是不是一份合法的 bookmarks.json（有 `books` 对象）。
 *
 * 与 `ui_bookmarks_parse` 的区别：合法但**一本书都没有**的进度表
 * （`{"books":{}}`）是合法文件 —— 用它来判断「主文件坏了要不要退回 .bak」。
 */
bool ui_bookmarks_valid(const char *json, size_t len);

/**
 * 主文件 / 备份文件二选一（.bak 自愈路径，纯逻辑）。
 *
 * 主文件结构合法 → 用主文件；主文件坏了而备份合法 → 用备份；
 * 两份都坏 → 用主文件（上层当作「没有进度」）。
 *
 * 返回 0 = 选中主文件，1 = 选中备份，-1 = 两份都不合法。
 */
int ui_book_pick_source(const char *primary, size_t primary_len, const char *backup,
                        size_t backup_len, const char **out_json, size_t *out_len);

/**
 * 把进度表序列化成 JSON（doc/07 §2.3 的形状）。
 * 长度不足返回 -1。
 */
int ui_bookmarks_format(const ui_bookmarks_t *marks, char *out, size_t cap);

/** 找一本书的进度。找到返回索引，没找到返回 -1。 */
int ui_bookmarks_find(const ui_bookmarks_t *marks, const char *name);

/**
 * 记一本书的最新进度（同一本书只保留一条）。
 * 超出容量时返回 -1（不覆盖别的书）。
 */
int ui_bookmarks_set(ui_bookmarks_t *marks, const char *name, uint32_t offset,
                     uint32_t total_bytes);

/* ===================== 每日阅读时长（doc/07 §2.4） ===================== */
/**
 * 跨日清零：日期与 `today` 不同就清零当日秒数并更新日期。
 * 返回 true = 发生了清零。
 */
bool ui_book_daily_rollover(ui_bookmarks_t *marks, const char *today);

#ifdef __cplusplus
}
#endif

#endif /* ABBOT_UI_BOOK_GUARD_H */
