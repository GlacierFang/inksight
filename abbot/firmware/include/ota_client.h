/**
 * ota_client.h —— abbot 固件 OTA 升级客户端（设备侧）
 *
 * 对齐 doc/04「固件升级」章节与 doc/05 §三 的 `/api/v1/ota/check` 契约。
 *
 * 分工（与 net_client.h / net_req.h 同一分层思路）：
 *   - 本头文件的**纯 C 部分**（校验 / 版本比较 / 容量计算 / 状态模型）
 *     **不依赖 Arduino.h 也不依赖 ESP-IDF**，因此可以在主机侧编译与单测
 *     （见 test/test_ota.c）。实现文件：src/net/ota_client.c。
 *   - **设备侧部分**（HTTP 下载 / esp_ota_* 写入 / NVS 待验证标记 / 看门狗 /
 *     重启后健康自检）依赖 ESP-IDF，实现文件：src/net/ota_client_esp.c。
 *
 * ⚠️ 绝不失败的铁律（doc/04）：**OTA 失败绝不能变砖**。
 *   - 校验不通过 → 不写分区、不改启动分区、不重启；
 *   - 写分区失败 → esp_ota_abort()，旧分区照常启动；
 *   - 新固件自检失败 → 不调 esp_ota_mark_app_valid_cancel_rollback()，
 *     由引导器按 ESP_OTA_IMG_PENDING_VERIFY 状态回滚到旧分区。
 */
#ifndef ABBOT_OTA_CLIENT_H
#define ABBOT_OTA_CLIENT_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include <mbedtls/sha256.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ================= 分区几何（对应 firmware/ota_partitions.csv） ================= */

/** app0 / app1 各 4MB（0x400000）—— 两个分区必须等长。 */
#define OTA_APP_PARTITION_SIZE ((uint32_t)(4u * 1024u * 1024u))

/** app0 起始偏移 0x20000，app1 起始偏移 0x420000。 */
#define OTA_APP0_OFFSET ((uint32_t)0x20000u)
#define OTA_APP1_OFFSET ((uint32_t)0x420000u)

/** ESP32 应用镜像首字节魔数 —— CI 产出的 firmware.bin 首字节为 0xE9。 */
#define OTA_FW_MAGIC_BYTE ((uint8_t)0xE9u)

/** 下载块大小：每块喂一次看门狗（doc/04 OTA 章节）。 */
#define OTA_DOWNLOAD_CHUNK 4096u

/** 升级前置条件：平均电池电压下限（mV）与 RSSI 下限（dBm）。 */
#define OTA_MIN_BATTERY_MV 3600
#define OTA_MIN_RSSI_DBM (-75)

/* ================= 结果码 ================= */

typedef enum {
    OTA_OK = 0,
    OTA_ERR_ARG = -1,        /* 入参非法 */
    OTA_ERR_NO_UPDATE = -2,  /* 已是最新版本 */
    OTA_ERR_PRECONDITION = -3, /* 电量 / 信号不满足 */
    OTA_ERR_NET = -4,        /* 连不上网关 */
    OTA_ERR_HTTP = -5,       /* 下载失败 / 状态码异常 */
    OTA_ERR_MAGIC = -6,      /* 首字节不是 0xE9 —— 不是可用的 ESP32 应用镜像 */
    OTA_ERR_SIZE_MISMATCH = -7, /* 实际字节数与网关声明的 size 不一致 */
    OTA_ERR_TOO_LARGE = -8,  /* 超出目标分区容量 */
    OTA_ERR_SHA256 = -9,     /* 摘要与网关声明不一致 */
    OTA_ERR_FLASH = -10,     /* 写备用分区失败 */
    OTA_ERR_ROLLBACK = -11,  /* 新固件自检未通过，已回滚旧分区 */
    OTA_ERR_INTERNAL = -12,  /* 内部错误（分配失败等） */
} ota_result_t;

/** 结果码 -> 中文短说明（**不含 AES / HTTP / signature / token 等技术术语**）。 */
const char *ota_result_text(ota_result_t r);

/* ================= 状态模型（UI 只读这一份） ================= */

typedef enum {
    OTA_STATE_IDLE = 0,
    OTA_STATE_PRECHECK = 1,    /* 采样电量与信号 */
    OTA_STATE_CHECKING = 2,    /* 查询是否有新版本 */
    OTA_STATE_DOWNLOADING = 3, /* 下载中（progress_pct 实时更新） */
    OTA_STATE_VERIFYING = 4,   /* 三重校验 */
    OTA_STATE_WRITING = 5,     /* 写入备用分区 */
    OTA_STATE_REBOOTING = 6,   /* 等待重启 */
    OTA_STATE_DONE = 7,        /* 升级成功 */
    OTA_STATE_FAILED = 8,      /* 失败（result 里是原因） */
} ota_state_t;

typedef struct {
    ota_state_t state;
    ota_result_t result;
    uint32_t seq;           /* 每次状态变化 +1 —— UI 靠它判断要不要重绘 */
    int progress_pct;        /* 0..100，下载阶段实时更新 */
    bool has_update;         /* 网关说有新版本 */
    bool force_update;       /* 网关说必须升级 */
    bool reboot_pending;     /* 写完待重启 */
    char current_ver[24];
    char latest_ver[24];
    char changelog[256];     /* 更新说明（原文） */
    char message[128];       /* 中文结果提示 */
} ota_status_t;

/* ================= 纯 C 校验原语（主机侧可测） ================= */

/**
 * 流式 SHA-256 上下文。
 *
 * 用的是 mbedtls 的上下文类型 —— mbedtls 在固件（arduino core 2.x 自带 2.16）
 * 与主机侧单测（libmbedtls-dev）两边都在，与 net_client.c 用的是同一套库，
 * 因此不会出现「设备能编、主机编不过」的版本割裂。
 */
typedef struct {
    mbedtls_sha256_context ctx;
    uint32_t total;
    bool started;
    bool finished;
} ota_sha_stream_t;

/** 初始化流式摘要（喂第一块前调用一次）。返回 0 成功。 */
int ota_sha_stream_begin(ota_sha_stream_t *s);
/** 喂入一块数据（可多次调用）。返回 0 成功。 */
int ota_sha_stream_update(ota_sha_stream_t *s, const uint8_t *data, size_t len);
/**
 * 收尾并输出小写十六进制摘要（64 字符 + NUL）。
 * 返回 0 成功；负数为错误码。
 */
int ota_sha_stream_finish(ota_sha_stream_t *s, char out_hex[65]);

/** 首字节魔数检查：是 0xE9 返回 true。 */
bool ota_check_magic(uint8_t first_byte);

/**
 * 字节数检查。
 * - `received` 必须等于 `declared`（网关声明的 size），否则 OTA_ERR_SIZE_MISMATCH；
 * - `declared` 不得超过 `capacity`（分区可用容量），否则 OTA_ERR_TOO_LARGE；
 * - `declared` 为 0 视为非法（OTA_ERR_SIZE_MISMATCH）。
 */
ota_result_t ota_check_size(uint32_t received, uint32_t declared, uint32_t capacity);

/** 摘要比对：两个小写/大写十六进制摘要是否一致（不区分大小写）。 */
bool ota_sha_equal(const char *a, const char *b);

/**
 * 版本比较：`cur` 与 `latest` 都是点分数字串（例 "1.0.0"）。
 * 返回 >0 表示 cur 更新，<0 表示 cur 更旧（需要升级），0 表示相等。
 * 非数字段按 0 处理。
 */
int ota_version_compare(const char *cur, const char *latest);

/**
 * 是否需要升级：latest 严格新于 cur 才返回 true。
 * 版本号相同或 cur 更新 → false。
 */
bool ota_should_update(const char *cur, const char *latest);

/**
 * 升级前置条件：平均电压 >= OTA_MIN_BATTERY_MV 且 RSSI >= OTA_MIN_RSSI_DBM。
 * 不满足时 UI 给温和中文提示，不启动 OTA。
 */
bool ota_precondition_ok(int avg_mv, int rssi_dbm);

/* ================= 设备侧接口（src/net/ota_client_esp.c） ================= */

/** 初始化状态与互斥量（setup() 早期调用一次即可）。 */
void ota_init(void);

/**
 * 启动一次 OTA 流程（异步，跑在网络任务里）。
 * 流程：前置采样 -> 查更新 -> 下载 -> 三重校验 -> 写备用分区 -> 标记启动分区
 *       -> 写 NVS 待验证标记 -> 重启。
 * 重复调用会被忽略（同一时刻只允许一次）。
 * 返回 true 表示已受理。
 */
bool ota_start(void);

/** 取消进行中的 OTA（仅在「下载 / 校验 / 写入」阶段有效）。 */
void ota_cancel(void);

/** 读当前 OTA 状态（UI 线程调用）。 */
void ota_get_status(ota_status_t *out);

/**
 * 重启后健康自检（**启动早期调用**，src/main.cpp 里）：
 *   - 查 NVS 的「待验证升级」标记 + esp_ota_get_state_partition()；
 *   - 状态为 ESP_OTA_IMG_PENDING_VERIFY 时跑自检：
 *     所有 HAL 外设初始化成功 + 稳定运行 20 秒 + task_ui_fsm 心跳计数 >= 50；
 *   - 全部满足才调 esp_ota_mark_app_valid_cancel_rollback() 并清 NVS 标记；
 *   - 任一不满足 → 不标记 valid，把启动分区指回旧分区并重启（自己实现回滚，
 *     不依赖引导器的自动回滚）。
 *
 * 本函数**阻塞执行**（在 setup() 里调用）—— setup() 不返回，
 * 「Arduino 核心在 setup() 之后自动 mark valid」那条路径就没有机会插手。
 *
 * @param peripherals_ok 外设是否全部初始化成功（由 setup() 汇总后传入）。
 * @return true 表示「本次启动已确认有效」（或本来就不是待验证状态）。
 */
bool ota_boot_selfcheck_run(bool peripherals_ok);

/**
 * 自检心跳打点：UI 主循环每转一圈调一次（doc/04 的 task_ui_fsm 心跳计数）。
 */
void ota_selfcheck_heartbeat(void);

/** 自检是否已通过（true = 可以标记 valid）。 */
bool ota_selfcheck_passed(void);

#ifdef __cplusplus
}
#endif

#endif /* ABBOT_OTA_CLIENT_H */
