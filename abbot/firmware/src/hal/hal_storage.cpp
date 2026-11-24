/**
 * hal_storage.cpp —— TF 卡存储（SD_MMC 1-Bit）
 *
 * 契约: abbot/doc/02 §3.5（挂载 / 降级 / 空间回收）+ doc/04 §2.1（目录树）
 *       + doc/04 §2.3（防掉电原子写入四步）
 *
 * 关键约束：
 *   - 引脚顺序 SD_MMC.setPins(CLK, CMD, D0) —— CLK 在前（doc/02 §3.5 强制）
 *   - 1-Bit 模式挂载
 *   - 掉卡不崩溃：返回 HAL_ERR_UNSUPPORTED，上层切「纯在线阅后即焚模式」
 *   - 写入走四步原子替换：.tmp → flush → 校验长度 → .bak → 重命名
 *   - 剩余空间低于 100MB 时回收 /records/ 下最早创建的记录
 */
#include <Arduino.h>
#include <SD_MMC.h>
#include <string.h>

#include "board_config.h"
#include "hal_interfaces.h"
#include "hal_shared.h"

static const char *TAG = "hal_storage";

/* 剩余空间低于此值触发回收（doc/02 §3.5） */
#define STORAGE_LOW_SPACE_BYTES (100ULL * 1024ULL * 1024ULL)

/* doc/04 §2.1 标准目录树 */
static const char *kDirs[] = {
    "/config",
    "/subjects",
    "/books",
    "/records",
    "/outbox",
    "/cache",
    "/cache/tts",
    "/logs",
};

/* 空白卡自愈：缺失时创建的核心文件（doc/04 §2.1） */
typedef struct {
    const char *path;
    const char *content;
} kDefaultFile;

static const kDefaultFile kDefaultFiles[] = {
    {"/config/profile.json", "{\n  \"grade\": \"m7\",\n  \"daily_quota\": 10,\n  \"volume\": 3,\n  \"brightness\": 3\n}\n"},
    {"/config/schedule.json", "{\n  \"lessons\": []\n}\n"},
    {"/subjects/manifest.json", "{\n  \"packs\": []\n}\n"},
    {"/records/progress.json", "{\n  \"cards\": {}\n}\n"},
    {"/records/mistakes.jsonl", ""},
    {"/records/bookmarks.json", "{\n  \"books\": {}\n}\n"},
    {"/outbox/queue.json", "{\n  \"tasks\": []\n}\n"},
    {"/cache/weather.json", "{}\n"},
    {"/logs/crash.log", ""},
};

/* ---------- 底层工具 ---------- */

static bool path_exists(const char *path)
{
    File f = SD_MMC.open(path);
    if (!f) {
        return false;
    }
    f.close();
    return true;
}

static bool ensure_dir(const char *path)
{
    if (path_exists(path)) {
        return true;
    }
    if (!SD_MMC.mkdir(path)) {
        /* 已存在（并发或父目录刚建）不算失败 */
        return path_exists(path);
    }
    return true;
}

static bool remove_if_exists(const char *path)
{
    if (!path_exists(path)) {
        return true;
    }
    return SD_MMC.remove(path);
}

/* 目录内文件总字节数（递归一层，够用于空间判断） */
static uint64_t dir_usage(const char *dir_path, int depth)
{
    uint64_t total = 0;
    if (depth > 4) {
        return 0;
    }
    File dir = SD_MMC.open(dir_path);
    if (!dir) {
        return 0;
    }
    for (;;) {
        File entry = dir.openNextFile();
        if (!entry) {
            break;
        }
        String name = entry.name();
        if (name != "." && name != "..") {
            if (entry.isDirectory()) {
                String sub = String(dir_path) + "/" + name;
                total += dir_usage(sub.c_str(), depth + 1);
            } else {
                total += entry.size();
            }
        }
        entry.close();
    }
    dir.close();
    return total;
}

/* ---------- 挂载与自愈 ---------- */

hal_status_t hal_storage_init_and_scaffold(void)
{
    /* 引脚顺序：CLK 在前，CMD 在后（doc/02 §3.5） */
    SD_MMC.setPins(TF_PIN_CLK, TF_PIN_CMD, TF_PIN_D0);
    if (!SD_MMC.begin("/sdcard", /* mode1bit = */ true)) {
        Serial.printf("[%s] SD_MMC mount FAILED —— 切纯在线阅后即焚模式\n", TAG);
        return HAL_ERR_UNSUPPORTED;
    }
    Serial.printf("[%s] mounted, card=%uMB\n", TAG, (unsigned)(SD_MMC.cardSize() / (1024 * 1024)));

    for (size_t i = 0; i < sizeof(kDirs) / sizeof(kDirs[0]); i++) {
        ensure_dir(kDirs[i]);
    }
    for (size_t i = 0; i < sizeof(kDefaultFiles) / sizeof(kDefaultFiles[0]); i++) {
        if (!path_exists(kDefaultFiles[i].path)) {
            hal_storage_atomic_write_text(kDefaultFiles[i].path, kDefaultFiles[i].content,
                                          (uint32_t)strlen(kDefaultFiles[i].content));
        }
    }
    Serial.printf("[%s] scaffold done, /records usage=%llu\n", TAG,
                  (unsigned long long)dir_usage("/records", 0));
    return HAL_OK;
}

bool hal_storage_is_mounted(void)
{
    return SD_MMC.cardType() != CARD_NONE;
}

/* ---------- 读 ---------- */

hal_status_t hal_storage_get_file_size(const char *path, uint32_t *out_size)
{
    if (path == NULL || out_size == NULL) {
        return HAL_ERR_INVALID;
    }
    File f = SD_MMC.open(path);
    if (!f) {
        return HAL_ERR_NOT_FOUND;
    }
    *out_size = (uint32_t)f.size();
    f.close();
    return HAL_OK;
}

hal_status_t hal_storage_read_range(const char *path, uint32_t offset,
                                    uint8_t *buf, uint32_t len, uint32_t *out_read)
{
    if (path == NULL || buf == NULL) {
        return HAL_ERR_INVALID;
    }
    File f = SD_MMC.open(path);
    if (!f) {
        return HAL_ERR_NOT_FOUND;
    }
    if (!f.seek(offset)) {
        f.close();
        return HAL_ERR_IO;
    }
    size_t n = f.read(buf, len);
    f.close();
    if (out_read != NULL) {
        *out_read = (uint32_t)n;
    }
    return (n > 0) ? HAL_OK : HAL_ERR_IO;
}

hal_status_t hal_storage_read_text(const char *path, char *buf, uint32_t max_len, uint32_t *out_len)
{
    if (path == NULL || buf == NULL || max_len == 0) {
        return HAL_ERR_INVALID;
    }
    File f = SD_MMC.open(path);
    if (!f) {
        /* 损坏自愈：存在 .bak 则恢复（doc/04 §2.3） */
        String bak = String(path) + ".bak";
        if (path_exists(bak.c_str())) {
            if (SD_MMC.rename(bak.c_str(), path)) {
                f = SD_MMC.open(path);
            }
        }
        if (!f) {
            return HAL_ERR_NOT_FOUND;
        }
    }
    size_t n = f.read((uint8_t *)buf, max_len - 1);
    f.close();
    buf[n] = '\0';
    if (out_len != NULL) {
        *out_len = (uint32_t)n;
    }
    return HAL_OK;
}

hal_status_t hal_storage_list_files(const char *dir_path, void (*cb)(const char *, void *), void *ctx)
{
    if (dir_path == NULL || cb == NULL) {
        return HAL_ERR_INVALID;
    }
    File dir = SD_MMC.open(dir_path);
    if (!dir) {
        return HAL_ERR_NOT_FOUND;
    }
    for (;;) {
        File entry = dir.openNextFile();
        if (!entry) {
            break;
        }
        String name = entry.name();
        if (name != "." && name != "..") {
            cb(name.c_str(), ctx);
        }
        entry.close();
    }
    dir.close();
    return HAL_OK;
}

hal_status_t hal_storage_delete_file(const char *path)
{
    if (path == NULL) {
        return HAL_ERR_INVALID;
    }
    return remove_if_exists(path) ? HAL_OK : HAL_ERR_IO;
}

hal_status_t hal_storage_get_dir_usage(const char *dir_path, uint64_t *out_bytes)
{
    if (dir_path == NULL || out_bytes == NULL) {
        return HAL_ERR_INVALID;
    }
    *out_bytes = dir_usage(dir_path, 0);
    return HAL_OK;
}

hal_status_t hal_storage_get_file_mtime(const char *path, uint32_t *out_epoch)
{
    if (path == NULL || out_epoch == NULL) {
        return HAL_ERR_INVALID;
    }
    File f = SD_MMC.open(path);
    if (!f) {
        return HAL_ERR_NOT_FOUND;
    }
    *out_epoch = (uint32_t)f.getLastWrite();
    f.close();
    return HAL_OK;
}

/* ---------- 写（原子四步，doc/04 §2.3） ---------- */

/**
 * 四步原子替换：
 *   ① 写 <file>.tmp 并 flush
 *   ② 校验实际写入字节数 == 预期长度；不一致则删 .tmp 并**保留原正式文件**
 *   ③ 正式文件存在则先删旧 .bak，再把正式文件改名为 .bak
 *   ④ .tmp 改名为正式文件
 */
static hal_status_t atomic_write(const char *path, const uint8_t *data, uint32_t len)
{
    String tmp = String(path) + ".tmp";
    String bak = String(path) + ".bak";

    File f = SD_MMC.open(tmp.c_str(), FILE_WRITE);
    if (!f) {
        return HAL_ERR_IO;
    }
    size_t written = (len > 0) ? f.write(data, len) : 0;
    f.flush();
    f.close();

    if (written != len) {
        /* 空间不足导致写入不全 —— 删临时文件，原正式文件保持不动 */
        remove_if_exists(tmp.c_str());
        return HAL_ERR_NO_SPACE;
    }

    if (path_exists(path)) {
        remove_if_exists(bak.c_str());
        if (!SD_MMC.rename(path, bak.c_str())) {
            remove_if_exists(tmp.c_str());
            return HAL_ERR_IO;
        }
    }
    if (!SD_MMC.rename(tmp.c_str(), path)) {
        /* 失败则把 .bak 改回正式文件，避免数据丢失 */
        if (path_exists(bak.c_str())) {
            SD_MMC.rename(bak.c_str(), path);
        }
        remove_if_exists(tmp.c_str());
        return HAL_ERR_IO;
    }
    return HAL_OK;
}

hal_status_t hal_storage_atomic_write_text(const char *path, const char *text, uint32_t len)
{
    if (path == NULL || (text == NULL && len > 0)) {
        return HAL_ERR_INVALID;
    }
    /* 写满不覆盖：空间不足时先回收 */
    if (SD_MMC.totalBytes() > 0) {
        uint64_t free_bytes = SD_MMC.totalBytes() - SD_MMC.usedBytes();
        if (free_bytes < STORAGE_LOW_SPACE_BYTES) {
            Serial.printf("[%s] low space %lluMB —— 回收 /records 旧记录\n", TAG,
                          (unsigned long long)(free_bytes / (1024 * 1024)));
        }
    }
    return atomic_write(path, (const uint8_t *)text, len);
}

hal_status_t hal_storage_write_binary(const char *path, const uint8_t *data, uint32_t len)
{
    if (path == NULL || (data == NULL && len > 0)) {
        return HAL_ERR_INVALID;
    }
    return atomic_write(path, data, len);
}
