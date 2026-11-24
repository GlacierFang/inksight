# 04-无线网络OTA与本地存储契约

## 一、 无线网络管理与时间连续性契约

### 1.1 异步 Wi-Fi 扫描与多热点记忆（NVS 持久化）
* **异步非阻塞扫描**：调用 `net_wifi_start_async_scan()` 在 Core 0 后台执行 `WiFi.scanNetworks(true)`，扫描期间绝不阻塞 Core 1 界面渲染与摇杆采样；扫描完成后按信号强度（RSSI）降序排列并去重同名 SSID，最多返回前 `12` 个热点供 `PAGE_WIFI_SCAN` 展示。
* **多热点自动切换**：固件在板载 `NVS`（`wifi_creds` 命名空间）中最多记忆最近成功连接过的 **3 组** `(SSID, Password)` 凭证（LRU 淘汰最久未用项）。开机或断线重连时，先扫描周边可见热点，自动匹配已保存列表中信号最强的一组发起连接；密码连接成功后方写入 NVS，连接失败（密码错误/超时 `12 秒`）则提示认证失败并保留在输入界面供核对修正。

### 1.2 首次 6 位配对码握手鉴权
* **零预烧录密钥**：固件出厂或重刷后不硬编码任何服务器密钥。用户在树莓派 Web 控制台生成 **6 位一次性数字配对码**（有效期 `5 分钟`），在设备 `PAGE_OOBE` 或 `PAGE_SETTINGS -> 设备配对绑定` 中输入后，设备携带芯片唯一 `device_id`（格式 `esp32s3_<chip_mac_hex8>`）请求 `POST /api/v1/pair`。
* **密钥落盘**：握手成功后，设备将网关下发的 `device_secret`（**64 位小写十六进制字符串，解码后为 32 字节原始密钥**）与目标网关域名存入板载 `NVS`（`auth_cfg` 命名空间），并同步更新 TF 卡 `profile.json` 中的 `paired: true`。
* **网关基址配置**：固件的网关基址（`host` + `base_path`）是可配置项，默认 **`http://ruohan.site/abbot`**（本机 frp 只绑定 `ruohan.site` 这一个 Host，子域名不可用；`443` 无证书，只能走 `80`）。请求 URL = 基址 + 纯 API 路径（如 `http://ruohan.site/abbot/api/v1/pair`）。
* **签名路径口径**：固件计算 `X-Signature` 时，`PATH` **只取纯 API 路径**（如 `/api/v1/study/solve`），**不含基址、不含 `/abbot` 前缀** —— 口径以 `05` 第 3.2 节为准，本节不重复定义。网关侧由反代剥前缀后按同一路径验签，故同一套签名在公网与本地直连下都成立。
* **下载地址为绝对 URL**：网关下发的固件/音频下载链接是**带挂载前缀的绝对地址**（如 `http://ruohan.site/abbot/api/v1/content/download?token=...`），设备**直接使用即可，不得自行拼接基址** —— 基址会随部署变更，自行拼接会拿到打不开的地址。

### 1.3 北京时间（UTC+8）校时、低磨损检查点与时钟超窗熔断
* **在线 NTP 校时**：每次 Wi-Fi 连接成功后，自动向国内公共 NTP 服务器（`ntp.aliyun.com`、`cn.pool.ntp.org`）同步 `UTC+8` 北京时间并写入 ESP32 内部 RTC，同时更新 `profile.json` 的 `last_ntp_ts`。
* **NVS 写磨损控制策略（防 Flash 寿命损耗）**：NVS 20KB 分区自带磨损均衡，仅在以下 3 种时机将当前 Unix 时间戳写入 `NVS`（`sys_clock`）：
  1. 系统整点时刻（每 **60 分钟** 写一次，全天仅 24 次）；
  2. 执行在线 NTP 校时或用户在 `PAGE_SETTINGS -> 离线手动校时` 保存时间后；
  3. 触发低电保护进入深度休眠前。
* **冷启动时钟偏差熔断策略（防长期搁置掉电导致 4001 验签死锁）**：开机从 `NVS` 恢复时钟基线时，若距离上次 NTP 校时（`last_ntp_ts`）已超过 `7 天`（或从未校准），固件置 `g_clock_unverified = true`：
  1. 本地全部离线功能（电子书阅读、学科刷卡、番茄钟、本地课表查看）照常运行；
  2. **暂缓发起任何加密端云业务 API 请求**（避免因本地 RTC 滞后导致 `X-Timestamp` 超出网关 `±60s` 窗口反复返回 `4001`），用户触发在线功能时弹出 `ERR_CLOCK_NEED_SYNC` 提示；
  3. 一旦连接 Wi-Fi 并完成 NTP 校时（或在设置中手动保存当前时间），立即解除 `g_clock_unverified` 恢复正常端云通信。

---

## 二、 TF 卡全目录结构与开箱自愈规范

> **存储架构铁律**：板载 `LittleFS` 仅存放只读的静态中文字库、内置提示音 `ding.wav` 及静态 UI 图标资产；**所有用户配置、学习记录、加密题库、离线队列、音频缓存与电子书必须全部存放在外部 TF 卡（`/sdcard`）**，确保固件 OTA 升级绝不擦除用户数据。

### 2.1 标准目录树结构
```text
/sdcard/
├── config/
│   ├── profile.json            # 全局档案、学段锚点、每日配额、电子书宵禁与音量亮度
│   └── schedule.json           # 每周课表、节次起止时间及准点提醒规则
├── subjects/
│   ├── manifest.json           # 本地已安装学科包索引清单
│   └── <pack_id>/              # 独立学科包目录（如 demo_pack、m7_geo）
│       ├── meta.json           # 学科名称、总题数、版本号、加密密钥类型与 SHA-256
│       ├── cards.enc           # AES-128-CTR 加密的 JSONL 卡片正文密文
│       └── index.idx           # 带 IDX1 文件头的定长二进制字节偏移索引表
├── books/
│   ├── <book_name>.txt         # 纯本地阅读器标准 UTF-8 文本
│   └── <book_name>.toc         # 配套章节字节偏移索引（可选）
├── records/
│   ├── progress.json           # 卡片掌握度与复习状态记录
│   ├── mistakes.jsonl          # 错题本追加日志（每行一条 JSON）
│   └── bookmarks.json          # 电子书全局阅读进度与每日阅读时长统计
├── outbox/
│   ├── queue.json              # 离线待问任务索引表（严格上限 <= 10 条，原子写入保护）
│   └── task_<ts>.<jpg|pcm>     # 单条离线任务关联的压缩图像或原始 PCM 音频
├── cache/
│   ├── weather.json            # 本地气象快照缓存（TTL = 2 小时）
│   └── tts/                    # 云端下发的 16kHz WAV 语音缓存（<tts_cache_key>.wav，上限 50MB）
└── logs/
    └── crash.log               # 最近一次异常复位原因与关键错误日志（上限 64KB 循环覆盖）
```

### 2.2 空白卡开箱自愈（`hal_storage_init_and_scaffold`）
开机挂载 `SD_MMC`（1-Bit 模式）后，固件自动检查上述目录与核心配置文件是否存在；若插上一张全新空白 FAT32 卡，自动创建全套目录并生成默认初始化文件（含内置示例学科包 `/subjects/demo_pack/`，其 `meta.json` 标记 `"key_type": "demo"`，使用内置默认密钥 `STUDY_DEMO_KEY_16` 解密）。

### 2.3 防掉电原子写入契约（Atomic Write）
所有核心 JSON 文件（包括 `profile.json`、`schedule.json`、`progress.json`、`bookmarks.json`、`manifest.json` 及 **`/outbox/queue.json`**）在写入时必须严格执行四步原子替换协议：
1. 先将完整新内容写入同目录临时文件 `<filename>.tmp` 并执行 `flush()`；
2. 校验临时文件实际写入字节数与预期长度一致（若 TF 卡空间已满导致写入不全，立即删除 `.tmp` 并**保留原正式文件不覆盖**）；
3. 若正式文件已存在，将其重命名为 `<filename>.bak`（先删除旧 `.bak`）；
4. 将 `<filename>.tmp` 重命名为正式文件名 `<filename>`。
* **开机损坏自愈**：读取任何核心 JSON（含 `/outbox/queue.json`）失败时，自动检测同目录是否存在 `.bak` 备份文件，存在则自动恢复并重构正式文件；若无可用备份则重建空默认结构并清理孤立的过期媒体文件。

---

## 三、 全量数据 Schema 正式规范（含版本控制与迁移）

> **统一版本约束**：所有 JSON 配置文件均包含顶层整数字段 `"schema_version": 1`。当固件读取到旧版本数据时，执行向后兼容的默认值填充；若读取到高于当前固件支持的版本号，保留原文件不破坏并提示升级固件。

### 3.1 `/config/profile.json`（全局档案、派生学段与配额基准）
```json
{
  "schema_version": 1,
  "device_id": "esp32s3_a1b2c3d4",
  "paired": true,
  "skip_oobe_offline": false,
  "edu_stage": "JUNIOR",
  "enrollment_year": 2026,
  "daily_quota": 10,
  "quota_used_date": "2026-11-23",
  "quota_used_count": 2,
  "curfew_window": ["22:00", "06:30"],
  "daily_book_limit_min": 45,
  "volume_level": 3,
  "brightness_level": 3,
  "last_ntp_ts": 1795412000
}
```
* **跨日配额仲裁基准**：每日导学配额的跨日重置与剩余次数仲裁严格以网关侧 `UTC+8` 服务器日期为准；设备端 `profile.json` 中的 `quota_used_date` 与 `quota_used_count` 仅作为离线状态下的 UI 显示参考，每次联网请求返回后始终以网关下发的 `effective_remaining_quota` 覆盖同步。
* **确定性派生学段（`grade_anchor`）参考算法（端云双方严格一致）**：
  ```text
  // 输入: edu_stage ("PRIMARY"|"JUNIOR"|"SENIOR"|"ADULT"), enrollment_year (int), cur_year, cur_month, cur_day
  if edu_stage == "ADULT": return "通用进修"
  // 每年 9月1日 起算新学年；1月1日~8月31日归属上一自然年开启的学年
  academic_year = cur_year if (cur_month >= 9) else (cur_year - 1)
  grade_num = academic_year - enrollment_year + 1
  // 学期判定: 9月1日 ~ 次年2月15日为"上"，2月16日 ~ 8月31日为"下"（兼容闰年2月29日）
  term = "上" if (cur_month >= 9 or cur_month == 1 or (cur_month == 2 and cur_day <= 15)) else "下"
  max_grade = 6 if edu_stage == "PRIMARY" else (3 if edu_stage in ["JUNIOR", "SENIOR"] else 0)
  if grade_num < 1 or grade_num > max_grade: return "通用进修"
  prefix = "小学" if edu_stage == "PRIMARY" else ("初中" if edu_stage == "JUNIOR" else "高中")
  return f"{prefix}{grade_num}年级{term}"
  ```

### 3.2 `/config/schedule.json`（课表、节次时间表与准点提醒调度）
```json
{
  "schema_version": 1,
  "updated_at": 1795400000,
  "semester_start_date": "2026-09-01",
  "weekend": [6, 7],
  "holidays": ["2026-10-01", "2026-10-02", "2026-10-03"],
  "period_times": [
    {"period": 1, "start": "08:00", "end": "08:45"},
    {"period": 2, "start": "08:55", "end": "09:40"},
    {"period": 3, "start": "10:00", "end": "10:45"},
    {"period": 4, "start": "10:55", "end": "11:40"},
    {"period": 5, "start": "14:00", "end": "14:45"},
    {"period": 6, "start": "14:55", "end": "15:40"},
    {"period": 7, "start": "15:50", "end": "16:35"}
  ],
  "reminders": [
    {"id": "rem_morning", "time": "07:15", "type": "MORNING_BRIEF", "enabled": true},
    {"id": "rem_evening", "time": "21:00", "type": "EVENING_PREVIEW", "enabled": true}
  ],
  "class_bell_enabled": true,
  "mute_window": ["12:30", "14:00"],
  "days": {
    "1": [
      {"period": 1, "subject": "语文", "week_type": "ALL"},
      {"period": 2, "subject": "数学", "week_type": "ALL"},
      {"period": 3, "subject": "物理实验", "week_type": "ODD"}
    ]
  },
  "override_days": {
    "2026-11-28": [
      {"period": 1, "subject": "数学", "week_type": "ALL"}
    ]
  }
}
```
* **调度与去重规则**：
  1. **日期优先级**：若当日日期命中 `override_days`，优先按调课表执行；否则若命中 `holidays` 或 `weekend`（周六/周日），自动标记为休息日（不触发上课铃与早晚课表播报）；
  2. **单双周计算**：`week_type` 支持 `"ALL"`（每周）、`"ODD"`（单周）、`"EVEN"`（双周），依据 `semester_start_date` 计算当前教学周奇偶性；
  3. **准点提醒与防重**：当 `class_bell_enabled == true` 时，每节课 `start` 时间自动触发轻量课程开始提示（播放本地 `ding.wav` 并弹出 `OVERLAY_REMINDER`）；内存中记录 `last_triggered_date_id` 防止同一分钟重复弹窗；处于 `mute_window` 午休静音窗内自动静默；错过提醒时间超过 `15 分钟` 才开机则不再补弹过期提醒。

### 3.3 `/subjects/manifest.json` 与 `<pack_id>/meta.json`
```json
// /subjects/manifest.json
{
  "schema_version": 1,
  "packs": [
    {"pack_id": "m7_geo", "title": "七上几何母题精选", "version": 2, "total_cards": 45}
  ]
}

// /subjects/m7_geo/meta.json
{
  "schema_version": 1,
  "pack_id": "m7_geo",
  "title": "七上几何母题精选",
  "version": 2,
  "total_cards": 45,
  "key_type": "device",
  "enc_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "updated_at": 1795410000
}
```
* **`key_type` 密钥路由规则**：取值为 `"demo"`（内置演示包或未配对状态下导出的包，固定使用 16 字节 ASCII 密钥 `"STUDY_DEMO_KEY_16"` 解密）或 `"device"`（已配对设备专用加密包，使用 NVS 中 `device_secret` 的前 16 字节解密）。

### 3.4 `/records/progress.json` 与 `mistakes.jsonl`
```json
// /records/progress.json
{
  "schema_version": 1,
  "packs": {
    "m7_geo": {
      "last_card_idx": 12,
      "mastered_ids": ["c001", "c002", "c005"]
    }
  }
}

// /records/mistakes.jsonl (每行一条独立 JSON，支持高效追加写)
{"ts":1795412500,"pack_id":"m7_geo","card_id":"c008","summary":"辅助线构造未考虑全等条件"}
```

### 3.5 `/outbox/queue.json`（离线待问任务队列，上限 `10 条`）
```json
{
  "schema_version": 1,
  "tasks": [
    {
      "task_id": "1795413001",
      "type": "SOLVE",
      "status": "pending",
      "retry_count": 0,
      "created_at": 1795413001,
      "hint_level": 0,
      "image_path": "/sdcard/outbox/task_1795413001.jpg",
      "pcm_path": "/sdcard/outbox/task_1795413001.pcm"
    }
  ]
}
```
* **媒体垃圾文件清理铁律**：当 `/outbox/` 中的某条离线任务成功上传至网关并从 `queue.json` 移除（或用户手动删除该任务）时，固件必须同步调用 `hal_storage_delete_file()` 删除其关联的 `image_path`（`.jpg`）与 `pcm_path`（`.pcm`）文件，严禁在 TF 卡残留孤立媒体文件。

---

## 四、 加密题库二进制索引与 CTR 块偏移精确算法

### 4.1 `index.idx` 带校验二进制索引头
为杜绝损坏索引文件导致越界读取，树莓派打包时生成如下二进制结构（全部整数采用大端序 Big-Endian）：
* `Byte 0 ~ 3`：固定魔数 `"IDX1"`（`0x49 0x44 0x58 0x31`）
* `Byte 4 ~ 5`：`uint16_t` 格式版本号（当前为 `1`）
* `Byte 6 ~ 7`：`uint16_t` 卡片总数量 `N`
* `Byte 8 ~ 11`：`uint32_t` 索引偏移区 `CRC32` 校验值
* `Byte 12 ~ (12 + 4*N - 1)`：`N` 个 `uint32_t` 起始字节偏移量（第 0 题偏移恒为 `16`，即跳过 `cards.enc` 前 16 字节 IV）。

### 4.2 `cards.enc` 局部按需解密 CTR 计数器公式与越界校验
读取第 `i` 题时，从 `index.idx` 获取起始偏移 `start_offset` 与长度 `cipher_len`：
1. **索引越界前置防线**：必须先校验 `start_offset >= 16` 且 `start_offset + cipher_len <= cards_enc_file_size`（且 `cipher_len <= 4096`）；若越界或文件大小不符，立即中止读取并返回 `ERR_PACK_CORRUPT`，严禁传入 `hal_storage_read_range()`；
2. 计算密文相对载荷起点的字节偏移：`payload_offset = start_offset - 16`；
3. 计算 AES-128 块索引与块内字节偏移：
   * `block_index = payload_offset / 16`
   * `byte_in_block = payload_offset % 16`
4. 构造当前块初始计数器：`nonce_counter = BigEndian128(IV_base) + block_index`（无符号 128 位大端序加法进位，开发时必须先通过 `05` 文档第 3.4 节局部块偏移向量单测）。就地核对值：`IV_base = 0102030405060708090a0b0c0d0e0f10`、`block_index = 2` 时 `nonce_counter = 0102030405060708090a0b0c0d0e0f12`，明文 `7b2270696e67223a317d`（`{"ping":1}`）的期望密文为 `41da37cc48e5aa3f8d06`；
   * **`IV_base` 来源与作用域（勿与请求信封混淆）**：`IV_base` 取自 **`cards.enc` 文件头前 16 字节**（即第 4.1 节索引里「第 0 题偏移恒为 16」所跳过的那个 `IV`），由**打包时逐包随机生成**并写入文件头；上面那个固定 `IV_base` **仅是 `05` 第 3.4 节的单测核对值**，严禁把它当成打包默认值写死 —— 同一密钥下复用 `IV` 会造成 CTR 密钥流重用，两个包同偏移密文可直接异或还原。`IV_base` 的作用域是**单个学科包文件**（包内 `block_index` 递增），与 `05` 第 3.2 节「每个请求信封独立随机 `IV`、计数器从 0 起」是**两套独立作用域**，互不影响；
5. 调用 `mbedtls_aes_crypt_ctr()` 时传入 `nc_off = byte_in_block`、`nonce_counter` 与 `stream_block` 缓冲，即可在单题 `< 10ms` 内精确解密单行 JSONL 明文，无需解密整本文件。

---

## 五、 设备端 TTS 音频缓存管理（`/cache/tts/`）

1. **缓存文件名映射**：网关下发响应时同时返回 `tts_cache_key`（32 位小写十六进制）与 `audio_url`。设备收到响应后优先检查 `/sdcard/cache/tts/<tts_cache_key>.wav` 是否已存在；若命中本地缓存则**零流量直接播放**，否则调用 `net_download_signed_asset(audio_url, tmp_path, NULL)` 异步下载并校验响应 Header `X-File-Sha256` 后重命名落盘播报。
2. **WAV 文件头解析与播放**：所有云端下发音频均为标准 `16000Hz / 16bit / Mono` WAV 格式；`hal_audio_play_wav_file()` 校验前 4 字节为 `"RIFF"` 后跳过 `44 字节` 标准文件头，将后续 PCM 裸流分段送入 `I2S_NUM_0` 播放。
3. **50MB 容量上限与 LRU 淘汰**：每次写入新 `.wav` 缓存前通过 `hal_storage_get_dir_usage()` 检查 `/cache/tts/` 目录总占用；若超过 `50MB`，遍历文件调用 `hal_storage_get_file_mtime()` 按最后修改时间升序删除最旧的音频文件，直至总占用降至 `40MB` 以下。

---

## 六、 无线网络、业务请求与 OTA 底层接口契约（`include/net_client.h`）

```cpp
#pragma once
#include <Arduino.h>

struct WifiApInfo {
    char ssid[33];
    int32_t rssi;
    bool is_encrypted;
    bool is_saved;
};

enum WifiConnState {
    WIFI_STATE_DISCONNECTED = 0,
    WIFI_STATE_SCANNING,
    WIFI_STATE_CONNECTING,
    WIFI_STATE_CONNECTED,
    WIFI_STATE_AUTH_FAILED
};

// 1. Wi-Fi 异步扫描与多热点管理
void net_wifi_init_auto_connect();
void net_wifi_start_async_scan();
int net_wifi_get_scan_results(WifiApInfo* out_list, int max_count);
bool net_wifi_connect_and_save(const char* ssid, const char* password);
WifiConnState net_wifi_get_state();
int8_t net_wifi_get_rssi();

// 2. 配对握手与时间同步
bool net_sync_ntp_time();
bool net_pair_with_code(const char* six_digit_code);

// 3. 双向 HMAC-SHA256 + AES-128-CTR 业务通信封装 (供 task_net_worker 调用)
// 内部自动完成明文加密、请求头签名(保存请求 X-Timestamp)、响应头验签 (X-Resp-Signature) 及响应解密
bool net_post_encrypted_json(const char* api_path, const char* plain_req_json, int* out_biz_code, char** out_resp_plain_json);

// 业务高级封装（内部组装 JSON 并调用 net_post_encrypted_json，HTTP 超时锁定 20s）
bool net_sync_bootstrap(const char* bootstrap_req_json, int* out_biz_code, char** out_resp_json);
// 支持纯拍照(pcm_data=NULL)与边说边拍(pcm_data 传入 16kHz PCM 缓冲，内部 Base64 编码至 pcm_base64)
bool net_study_solve(const char* session_id, const uint8_t* jpg_data, size_t jpg_len, const uint8_t* pcm_data, size_t pcm_len, uint8_t hint_level, int* out_biz_code, char** out_resp_json);
bool net_study_voice(const char* context_page, const char* pack_id, const char* card_id, const uint8_t* pcm_data, size_t pcm_len, int* out_biz_code, char** out_resp_json);

// 4. 签名资产流式下载（用于同步课表、学科包、电子书及 TTS .wav）
// expected_sha256 可传 NULL（如 TTS 动态下载），此时自动读取响应头 X-File-Sha256 进行流式完整性校验
bool net_download_signed_asset(const char* signed_url, const char* save_tmp_path, const char* expected_sha256);

// 5. 单通道 HTTP OTA 升级（静置连续采样 3 次电池电压均值 >= 3600mV 且 RSSI >= -75dBm）
bool net_check_and_perform_ota(void (*on_progress_cb)(uint8_t pct));
```

### OTA 防变砖三重校验与双核业务健康度自检回滚
1. **前置采样与分块流式写入**：发起 OTA 前在静置状态下连续采样 `3 次`（间隔 `50ms`）电池电压取平均值，`avg_mv >= 3600mV` 且 `RSSI >= -75dBm` 方可启动；通过 `esp_ota_get_next_update_partition()` 写入未激活的备用分区（`app0` 或 `app1`），每接收 `4KB` 喂一次看门狗；
2. **三重校验**：首包首字节必须为 **`0xE9`**；总接收字节数与 `size` 一致（且不超过分区实际可用容量）；流式计算的 `mbedtls_sha256` 与网关声明的 `sha256` 完全一致；
3. **双核业务健康度自检确认**：重启进入新固件后，`esp_task_wdt` 看门狗保持 `15 秒` 硬超时；固件必须在所有 HAL 外设初始化成功、稳定运行满 **`20 秒`** 且确认 **`task_ui_fsm` 主循环健康心跳计数 `>= 50` 次**（证明界面与双核调度真实存活未卡死）后，方可显式调用 `esp_ota_mark_app_valid_cancel_rollback()`。若在 `20 秒` 内发生死锁或崩溃，看门狗将于 `15 秒` 时触发硬复位并由 Bootloader 自动回滚至旧版本分区。