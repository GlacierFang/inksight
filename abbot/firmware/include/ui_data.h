/**
 * ui_data.h —— UI 层本地数据读取（TF 卡）
 *
 * 契约: abbot/doc/02 §4.2（TF 卡由存储任务独占串行化）、doc/04 §二/§三（目录树与 Schema）、
 *       doc/07 §3（流式分页与 UTF-8 边界）。
 *
 * 铁律:
 *   1. UI 任务**不得**直接调 `hal_storage_*` —— TF 卡只由 `task_storage` 单线程访问，
 *      本模块所有读卡动作都经 `hal_storage_req_queue()` 的请求/应答完成（含超时与失败返回）。
 *   2. 本模块**不碰 SPI、不碰网络、不碰加密**，只取文本与字节区间。
 *   3. 只回错误码，**不拼用户文案**；文案一律由 UI 层按 doc/03 §四 出。
 *
 * 本头是**纯 C**：实现文件不依赖 Arduino.h，主机侧可单测。
 */
#ifndef ABBOT_UI_DATA_H
#define ABBOT_UI_DATA_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ===================== 返回码（与 hal_status_t 语义一致） ===================== */
#define UI_DATA_OK              0
#define UI_DATA_ERR_INVALID     (-1)
#define UI_DATA_ERR_TIMEOUT     (-2)
#define UI_DATA_ERR_IO          (-3)
#define UI_DATA_ERR_NOT_FOUND   (-4)
#define UI_DATA_ERR_NO_SPACE    (-5)
#define UI_DATA_ERR_UNSUPPORTED (-6)

/* ===================== 路径与容量 ===================== */
#define UI_DATA_MAX_PATH       72
#define UI_DATA_MAX_NAME       64
#define UI_DATA_MAX_FILES      24
#define UI_DATA_MAX_LESSONS    10
#define UI_DATA_MAX_SUBJECT    24
#define UI_DATA_MAX_TIME       6
#define UI_DATA_MAX_SUBJECT_LEN 24

/* doc/04 §2.1 目录树 */
#define UI_DATA_PATH_SETTINGS  "/config/settings.json"
#define UI_DATA_PATH_SCHEDULE  "/config/schedule.json"
#define UI_DATA_PATH_PROFILE   "/config/profile.json"
#define UI_DATA_DIR_BOOKS      "/books"
#define UI_DATA_DIR_SUBJECTS   "/subjects"   /* 学科包目录（每个子目录是一个包） */

/* doc/07 §3 单页读取与排版上限 */
#define UI_DATA_PAGE_BYTES     1024  /* 单次读卡字节数（恒定 < 3KB 内存） */
#define UI_DATA_PAGE_LINES     14    /* 一屏最多行数 */
#define UI_DATA_LINE_UNITS     38    /* 一行宽度（半角单位；全角汉字算 2） */
#define UI_DATA_BACK_WINDOW    2048  /* 向前翻页的回溯窗口 */

/* 番茄钟时长可调范围（分钟） */
#define UI_DATA_FOCUS_MIN      5
#define UI_DATA_FOCUS_MAX      60
#define UI_DATA_FOCUS_DEFAULT  25
#define UI_DATA_FOCUS_STEP     5

/* ===================== 数据结构 ===================== */
typedef struct {
    char name[UI_DATA_MAX_NAME];
    uint32_t size;
} ui_data_file_t;

typedef struct {
    uint8_t period;                 /* 节次（1~8） */
    char subject[UI_DATA_MAX_SUBJECT_LEN];
    char start[UI_DATA_MAX_TIME];   /* "08:00" */
    char end[UI_DATA_MAX_TIME];     /* "08:45" */
} ui_data_lesson_t;

/** 今天的日期（公历）。星期由本模块按 ISO 规则（周一=1 … 周日=7）自算。 */
typedef struct {
    int year;
    int month;
    int day;
} ui_data_date_t;

/* ===================== 基础读卡原语 ===================== */
/** TF 卡是否已挂载。 */
int ui_data_is_mounted(void);
/** 文件是否存在：存在返回 UI_DATA_OK，不存在返回 UI_DATA_ERR_NOT_FOUND。 */
int ui_data_file_exists(const char *path);
/** 取文件字节数。 */
int ui_data_file_size(const char *path, uint32_t *out_size);
/** 读整份文本（buf 末尾补 '\0'）。返回读到的字节数，<0 为错误码。 */
int ui_data_read_text(const char *path, char *buf, uint32_t cap, uint32_t *out_len);
/**
 * 读指定字节区间（分页用）。
 * 返回读到的字节数；读到文件末尾返回 0（不是错误）；<0 为错误码。
 */
int ui_data_read_range(const char *path, uint32_t offset, uint8_t *buf, uint32_t cap,
                       uint32_t *out_read);
/** 列目录下的文件（不含子目录）。 */
int ui_data_list_files(const char *dir, ui_data_file_t *out, uint32_t max_count,
                        uint32_t *out_count);
/** 目录占用字节数。 */
int ui_data_dir_usage(const char *dir, uint64_t *out_bytes);
/** 原子写文本（.tmp → flush → 校验长度 → .bak → 重命名，doc/04 §2.3）。 */
int ui_data_write_text(const char *path, const char *text, uint32_t len);

/* ===================== UTF-8 边界（doc/07 §3.2） ===================== */
/**
 * 向后对齐到 UTF-8 字符首字节：偏移处字节满足 `(byte & 0xC0) == 0x80`
 * （延续字节）时，向后步进直到字符首字节。
 */
uint32_t ui_data_utf8_align_forward(const uint8_t *buf, uint32_t len, uint32_t offset);
/**
 * 向前对齐到 UTF-8 字符首字节：把落在字符中间的偏移拉回该字符的首字节。
 */
uint32_t ui_data_utf8_align_backward(const uint8_t *buf, uint32_t len, uint32_t offset);

/* ===================== 正文排版与分页（doc/07 §3） ===================== */
/**
 * 把一段字节排版成一屏正文（纯函数，主机侧可测）。
 *
 * 规则（doc/07 §3.3）：逐行折行，每行最多 UI_DATA_LINE_UNITS 个半角单位
 * （全角汉字算 2 个单位），一屏最多 UI_DATA_PAGE_LINES 行；遇 '\n' 强制换行。
 * 多字节字符**绝不切坏**：折行只在字符边界上发生。
 * `out_consumed` 回填本页实际消耗的字节数，`out_lines` 回填渲染行数，
 * `out_text` 写入排好版的正文（行间以 '\n' 分隔）。
 */
int ui_data_layout_page(const uint8_t *buf, uint32_t len, uint32_t *out_consumed,
                         int *out_lines, char *out_text, uint32_t out_cap);

/**
 * 在一段字节里反推「紧邻 current 之前那一页的起始偏移」（纯函数）。
 * 传入的是从 `max(0, current - UI_DATA_BACK_WINDOW)` 起预读的窗口，
 * `current` 是**窗口内**的相对偏移。
 */
int ui_data_layout_prev_start(const uint8_t *buf, uint32_t len, uint32_t current,
                               uint32_t *out_start);

/** 读一页正文（含读卡）：路径 + 起始偏移 → 排好版的正文与消耗字节数。 */
int ui_data_load_page(const char *path, uint32_t offset, uint32_t *out_consumed,
                       int *out_lines, char *out_text, uint32_t out_cap);

/** 求上一页的起始偏移（用回溯窗口反推，doc/07 §3.3）。 */
int ui_data_prev_page_offset(const char *path, uint32_t offset, uint32_t *out_start);

/* ===================== 课表（doc/04 §3.2） ===================== */
/**
 * 解析课表 JSON，按给定日期算出当天课程（纯函数，主机侧可测）。
 *
 * 规则（doc/04 §3.2）：
 *   1. 日期优先级：`override_days` > `holidays` / `weekend`（休息日）；
 *   2. `week_type` ∈ ALL/ODD/EVEN，按 `semester_start_date` 算教学周奇偶；
 *   3. 节次起止时间从 `period_times` 取。
 * 没有课程时 `*out_count = 0`（由 UI 层出空态文案）。
 */
int ui_data_parse_schedule(const char *json, size_t len, const ui_data_date_t *date,
                            ui_data_lesson_t *out, uint32_t max_count, uint32_t *out_count);

/** 读 `/config/schedule.json` 并算出当天课程。 */
int ui_data_read_schedule(const ui_data_date_t *date, ui_data_lesson_t *out,
                           uint32_t max_count, uint32_t *out_count);

/**
 * 取设备当前日期（UTC+8 本地时间，NTP 校时后有效）。
 * 未校时返回 UI_DATA_ERR_NOT_FOUND —— 上层据此不编课程。
 */
int ui_data_today(ui_data_date_t *out);

/* ===================== 本地设置（番茄钟时长等） ===================== */
/** 解析设置 JSON 里的专注时长（纯函数）。缺字段时用 fallback。 */
int ui_data_parse_focus_minutes(const char *json, size_t len, int fallback);
/** 读 `/config/settings.json` 里的专注时长（分钟）。读不到时回填默认值。 */
int ui_data_focus_minutes_get(int *out_minutes);
/** 把专注时长写回 `/config/settings.json`（原子写，doc/04 §2.3）。 */
int ui_data_focus_minutes_set(int minutes);

/* ===================== 档案（离线降级用） ===================== */
/** 读 `/config/profile.json` 里的配对标志。读不到时按未配对处理。 */
int ui_data_read_paired(int *out_paired);

#ifdef __cplusplus
}
#endif

#endif /* ABBOT_UI_DATA_H */
