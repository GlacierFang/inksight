/**
 * esp_ota_ops.h —— **主机侧 stub**，只为让 `src/net/ota_client_esp.cpp` 能在 host g++ 上做语法检查。
 *
 * 签名逐条抄自 ESP-IDF **4.4**（arduino-esp32 2.0.17 自带版本）的真实头文件：
 *   - components/bootloader_support/include/esp_ota_ops.h
 *   - components/spi_flash/include/esp_partition.h
 *
 * ⚠️ 关键点（真实 API 与直觉不同，写错了编译能过、跑起来就砖）：
 *   - `esp_ota_write()` 返回的是 **esp_err_t**（不是写进去的字节数），
 *     成功就是 ESP_OK —— 老版本 ESP-IDF 才是返回字节数。
 *   - `esp_ota_end()` 会**顺手把启动分区切到刚写的那个分区**。
 *   - `esp_ota_get_next_update_partition()` **带一个参数**：
 *     `esp_ota_get_next_update_partition(const esp_partition_t *start_from)`。
 *     传 `NULL` = 从当前运行的分区往后找：跑 ota_0 时返回 ota_1，跑 ota_1 时
 *     返回 ota_0。老版本 IDF 才是无参的那个 —— 写错编译期就报
 *     「too few arguments」。
 */
#ifndef ABBOT_STUB_ESP_OTA_OPS_H
#define ABBOT_STUB_ESP_OTA_OPS_H

#include <cstddef>
#include <cstdint>

#include "esp_err.h"

typedef uint32_t esp_ota_handle_t;

#define OTA_SIZE_UNKNOWN 0xFFFFFFFF

typedef enum {
    ESP_PARTITION_TYPE_APP = 0x00,
    ESP_PARTITION_TYPE_DATA = 0x01,
} esp_partition_type_t;

typedef enum {
    ESP_PARTITION_SUBTYPE_APP_FACTORY = 0x00,
    ESP_PARTITION_SUBTYPE_APP_OTA_0 = 0x10,
    ESP_PARTITION_SUBTYPE_APP_OTA_1 = 0x11,
    ESP_PARTITION_SUBTYPE_ANY = 0xff,
} esp_partition_subtype_t;

/** 注意 label 是 uint32_t[16]（真实头文件为了对齐这么定义）。 */
typedef struct {
    esp_partition_type_t type;
    esp_partition_subtype_t subtype;
    uint32_t address;
    uint32_t size;
    uint32_t label[16];
    bool encrypted;
} esp_partition_t;

typedef enum {
    ESP_OTA_IMG_VALID = 0,
    ESP_OTA_IMG_PENDING_VERIFY = 1,
    ESP_OTA_IMG_INVALID = 2,
    ESP_OTA_IMG_ABORTED = 0x80,
    ESP_OTA_IMG_NEW = 0x81,
} esp_ota_img_states_t;

const esp_partition_t *esp_ota_get_next_update_partition(const esp_partition_t *start_from);
const esp_partition_t *esp_ota_get_running_partition(void);
const esp_partition_t *esp_ota_get_boot_partition(void);
esp_err_t esp_ota_begin(const esp_partition_t *p, size_t update_size,
                        esp_ota_handle_t *out_handle);
esp_err_t esp_ota_write(esp_ota_handle_t handle, const void *data, size_t size);
esp_err_t esp_ota_end(esp_ota_handle_t handle);
esp_err_t esp_ota_abort(esp_ota_handle_t handle);
esp_err_t esp_ota_set_boot_partition(const esp_partition_t *partition);
esp_err_t esp_ota_get_state_partition(esp_partition_type_t type,
                                      esp_partition_subtype_t subtype,
                                      esp_ota_img_states_t *ota_state);
esp_err_t esp_ota_mark_app_valid_cancel_rollback(void);

#endif /* ABBOT_STUB_ESP_OTA_OPS_H */
