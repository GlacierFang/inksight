# 04-无线网络OTA与本地存储契约

## 一、 无线网络管理与时间连续性契约

### 1.1 异步 Wi-Fi 扫描与多热点记忆（NVS 持久化）
* **异步非阻塞扫描**：调用 `net_wifi_start_async_scan()` 在 Core 0 后台执行 `WiFi.scanNetworks(true)`，扫描期间绝不阻塞 Core 1 界面渲染与摇杆采样；扫描完成后按信号强度（RSSI）降序排列并去重同名 SSID，最多返回前 `12` 个热点供 `PAGE_WIFI_SCAN` 展示。
* **多热点自动切换**：固件在板载 `NVS`（`wifi_creds` 命名空间）中最多记忆最近成功连接过的 **3 组** `(SSID, Password)` 凭证（LRU 淘汰最久未用项）。开机或断线重连时，先扫描周边可见热点，自动匹配已保存列表中信号最强的一组发起连接；密码连接成功后方写入 NVS，连接失败（密码错误/超时 `12 秒`）则提示认证失败并保留在输入界面供核对修正。

### 1.2 设备自动登记握手鉴权（无码；6 位配对码降级为兜底）

> **2026-11-25 修订**：原「首次 6 位配对码握手」升格为「设备自动登记」——
> 去掉「家长认领 / 审批弹窗」这一层仪式。设备开机联网后自报身份，网关签发密钥。
> 旧配对码路径**保留为兜底**（网关不支持自动登记时用），但**默认不暴露**。

* **零预烧录密钥**：固件出厂或重刷后不硬编码任何服务器密钥。
* **自动登记（主路径，设备侧零输入）**：
  1. 设备联网（NTP 校时）后，若本地 `NVS` 里还没有 `device_secret`，自动携带芯片唯一 `device_id`（格式 `esp32s3_<chip_mac_hex8>`）请求 `POST /api/v1/device/register`（**明文 JSON**，此时设备还没有密钥、无法签名）；
  2. 网关按 `device_id` **幂等**签发 `device_secret`：已登记的设备拿回**同一份**密钥（不重签、不覆盖），未登记的设备新签一份并落盘；
  3. 设备把密钥写入 `NVS`，并向 UI 投 `ui_command_t.kind=5`（`UI_PAIR_STATE_READY`），
     `PAGE_OOBE` 的进度页刷新为「已经接上啦」并**自动跳转首页** —— 家长不需要按任何键。
  4. **幂等是硬要求**：设备可能在没收到应答时重试；若每次重签，设备手上那份旧密钥就永久失效。
* **兜底路径（6 位配对码，默认不暴露）**：自动登记连续失败时，用户可在树莓派 Web 控制台生成 **6 位一次性数字配对码**（有效期 `5 分钟`），在设备 `PAGE_SETTINGS -> 设备配对绑定` 中输入，设备请求 `POST /api/v1/pair` 换密钥。
* **密钥落盘**：握手成功后，设备将网关下发的 `device_secret`（**64 位小写十六进制字符串，解码后为 32 字节原始密钥**）与目标网关域名存入板载 `NVS`（`auth_cfg` 命名空间）。
* **「已接入」的真相源是 NVS，不是 TF 卡**：设备是否已接入，以 `NVS` 里有没有 `device_secret` 为准。
  TF 卡 `profile.json` 的 `paired` 字段**当前不写**（空白卡自愈生成的那份根本没有这个键），
  因此 UI 判定取两者「或」：网关注册成功过就算已接入 —— 否则首页的「拍题导学」会一直误报「还没连上学习助手」。
* **密钥绝不回传浏览器**：管理台只显示「这台设备已接入」与配额状态，**不显示也不回传任何密钥**；设备密钥只在设备 `NVS` 与网关 `storage/devices.json`（`0600`）里。
* **网关基址配置**：固件的网关基址（`host` + `base_path`）是可配置项，默认 **`http://ruohan.site/abbot`**（本机 frp 只绑定 `ruohan.site` 这一个 Host，子域名不可用；`443` 无证书，只能走 `80`）。请求 URL = 基址 + 纯 API 路径（如 `http://ruohan.site/abbot/api/v1/device/register`）。
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

> **存储架构铁律**：**所有用户配置、学习记录、加密题库、离线队列、音频缓存与电子书必须全部存放在外部 TF 卡（`/sdcard`）**，确保固件 OTA 升级绝不擦除用户数据。
>
> * **静态资源不再走 LittleFS**：中文字库以 C 数组**编译进固件**（`src/ui/fonts/ui_font_cn_16.c`），UI 图标为内联样式，提示音**由设备端合成**（`hal_audio_play_tone` / `hal_audio_request_tone`）；固件**不划分 LittleFS 分区**（`platformio.ini` 用 `huge_app.csv`，无 littlefs 分区表）。
> * **为什么不引 LittleFS**：一句提示音只需要一个固定频率的短音；为它单独开分区要多占 Flash、多一条挂载与容错路径（挂载失败 / 文件损坏时的降级分支），而合成一声正弦音只要几行 PCM 生成代码。中文字库直接编译进固件，省掉「文件读不到就变方块」的失败模式。**零 LittleFS 依赖 = 零分区表 = 零挂载失败分支**。
> * **仓库里不存在任何 `.wav` 资产**，文档与实现均不得再提 `ding.wav`；需要提示音的地方一律走设备端合成。

### 2.1 标准目录树结构
```text
/sdcard/
├── config/
│   ├── profile.json            # 全局档案、学段锚点、每日配额、电子书宵禁与音量亮度
│   ├── schedule.json           # 每周课表、节次起止时间及准点提醒规则
│   └── settings.json           # 设备本地设置（当前：番茄钟专注时长 focus_minutes）
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
开机挂载 `SD_MMC`（1-Bit 模式）后，固件自动检查上述目录与核心配置文件是否存在；若插上一张全新空白 FAT32 卡，自动创建全套目录与默认初始化文件（`profile.json` / `schedule.json` / `settings.json` / `manifest.json` / `progress.json` / `queue.json` 等）。

**空白卡自愈不预置任何学科包** —— `/subjects/` 下是空的，学科包由网关经心跳下发后写入（`meta.json` 标记 `"key_type": "device"` 时用设备密钥 `device_secret[0:16]` 解密，标记 `"key_type": "demo"` 时用内置默认密钥 `STUDY_DEMO_KEY16` 解密）。设备端的「学科包」列表读的就是 `/subjects/` 下的真实目录。

### 2.3 防掉电原子写入契约（Atomic Write）
所有核心 JSON 文件（包括 `profile.json`、`schedule.json`、`settings.json`、`progress.json`、`bookmarks.json`、`manifest.json` 及 **`/outbox/queue.json`**）在写入时必须严格执行四步原子替换协议：
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
  3. **准点提醒与防重**：当 `class_bell_enabled == true` 时，每节课 `start` 时间自动触发轻量课程开始提示（播放**设备端合成的提示音**并弹出 `OVERLAY_REMINDER`）；内存中记录 `last_triggered_date_id` 防止同一分钟重复弹窗；处于 `mute_window` 午休静音窗内自动静默；错过提醒时间超过 `15 分钟` 才开机则不再补弹过期提醒。

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
* **`key_type` 密钥路由规则**：取值为 `"demo"`（内置演示包或未配对状态下导出的包，固定使用 16 字节 ASCII 密钥 `"STUDY_DEMO_KEY16"` 解密）或 `"device"`（已配对设备专用加密包，使用 NVS 中 `device_secret` 的前 16 字节解密）。

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
      "image_path": "/outbox/task_1795413001.jpg",
      "pcm_path": "",
      "session_id": "sess_1795413001",
      "context": ""
    }
  ]
}
```
* **入队而非失败（PLAN 第 2 条）**：拍照 / 语音这类**需要云端**的请求，断网或**未配对**时
  **入队**，而不是直接失败 —— 用户的拍照不该白拍。**未配对时也允许入队**，
  配对完成后一并补传。
  * 只有**传输失败**（断网 / 未配对 / 响应验签失败）才入队；
    网关返回的**业务错**（如 `4004` 学段未设置）不入队 —— 重试也没用。
  * 相机任务拍完就把 `JPG` 落到 TF 卡（`/outbox/task_<ts>.jpg`），
    并把该路径随请求带进网络任务：上传成功就删掉它，失败就把它记进队列索引。
* **重试上限与过期策略（必读，固件与文档必须一致）**：
  * **重试上限 `5` 次**：同一条任务补传失败达到 `5` 次后**丢弃**该条目，
    并删掉它的关联媒体文件 —— 不让一条坏数据永久占着队列位置。
  * **过期时限 `72 小时`**：`created_at` 距当前时间超过 `72 小时`（3 天）的条目，
    在补传时**丢弃**并删掉关联媒体文件 —— 够家长周末补传，也不至于留下陈年旧账。
  * **时钟未校准时不做过期判定**：设备没校过时（`created_at` 与当前时间都不可信）
    只按**重试上限**清理，绝不按时间清理 —— 否则一开机就会把队列全清掉。
* **队列上限 `10` 条**：满了淘汰**最旧**的一条，并删掉它的关联媒体文件，
    保证 TF 卡不被离线任务撑爆。
* **原子写 + `.bak` 自愈**：索引写入走 `.tmp → .bak → 重命名` 四步替换；
  读时正式文件缺失会自动回退 `.bak`，正式文件**内容损坏**时固件自己回退 `.bak`
  并清掉坏文件（不靠猜）。
* **补传时机与节奏**：联网且已配对后逐条补传；**每次只补一条**
  （一次拍题往返含 AI 推理可能十几秒，不能把业务请求饿死），
  且**只在业务请求队列空闲时**才补。
* **媒体垃圾文件清理铁律**：当 `/outbox/` 中的某条离线任务成功上传至网关并从 `queue.json` 移除（或用户手动删除该任务）时，固件必须同步调用 `hal_storage_delete_file()` 删除其关联的 `image_path`（`.jpg`）与 `pcm_path`（`.pcm`）文件，严禁在 TF 卡残留孤立媒体文件。**同理**：入队时淘汰最旧条目、以及条目因重试超限/过期被丢弃时，也必须同步删除其关联文件。

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

> **设备端实现落点（2026-11-25 落地）**：`src/net/cards.c` 按本节公式实现 ——
> `cards_parse_index()` 校验魔数/版本/CRC32/偏移下界；`cards_decrypt_range()` 按
> `block_index` / `byte_in_block` 局部解密（首块起点落在块中间时，把密文摆到
> `block[byte_in_block]` 再解 —— 摆到 `block[0]` 会让块尾用错密钥流）。
> `nc_add_counter()` 提供大端序 128-bit 进位。实现用 **doc/05 §3.4 的官方锁定向量**
> （`block_index = 2` 处明文 `{"ping":1}` ↔ 密文 `41da37cc48e5aa3f8d06`）自检，
> 主机侧单测见 `firmware/test/test_cards.c`。
>
> **解密在哪一层做**：设备密钥只存在 NVS，所以解密只在**网络任务**里做
> （`net_cards_load()` → `cards_load()`），UI 层不碰密钥、不碰加密；
> 解出的卡片经 `q_ui_command`（kind=6）投回 Core 1 渲染。
> 读卡仍走存储任务队列（doc/02 §4.2）。

---

## 五、 设备端 TTS 音频缓存管理（`/cache/tts/`）

1. **缓存文件名映射**：网关下发响应时同时返回 `tts_cache_key`（32 位小写十六进制）与 `audio_url`。设备收到响应后优先检查 `/sdcard/cache/tts/<tts_cache_key>.wav` 是否已存在；若命中本地缓存则**零流量直接播放**，否则调用 `net_download_signed_asset(audio_url, tmp_path, NULL)` 异步下载并校验响应 Header `X-File-Sha256` 后重命名落盘播报。
2. **WAV 文件头解析与播放**：所有云端下发音频均为标准 `16000Hz / 16bit / Mono` WAV 格式；`hal_audio_play_wav_file()` 校验前 4 字节为 `"RIFF"` 后跳过 `44 字节` 标准文件头，将后续 PCM 裸流分段送入 `I2S_NUM_0` 播放。
3. **50MB 容量上限与 LRU 淘汰**：每次写入新 `.wav` 缓存前通过 `hal_storage_get_dir_usage()` 检查 `/cache/tts/` 目录总占用；若超过 `50MB`，遍历文件调用 `hal_storage_get_file_mtime()` 按最后修改时间升序删除最旧的音频文件，直至总占用降至 `40MB` 以下。

---

## 六、 无线网络、业务请求与 OTA 底层接口契约（`include/net_api.h`）

> **头文件分工（2026-11-25 补记）：** 设备端网络栈拆成两层，互不重叠：
> * **`include/net_client.h`** —— 端云安全信封的**纯 C 原语层**（SHA-256 / HMAC / AES-128-CTR / Base64 / 请求签名 / 响应验签 / 信封解密）。**不依赖 `Arduino.h`**，同一份实现可在 ESP32 固件与主机侧 native 测试中编译，禁止混入任何 Wi-Fi / NVS / HTTP 调用。
> * **`include/net_api.h`** —— 设备端**业务请求层**（Wi-Fi 连接、NTP 校时、配对握手、加密 POST、资产下载、OTA），依赖 Arduino 侧能力，向 `task_net_worker` 提供业务级接口。
>
> 业务明文的**字段形状真相源是 `05` §5.1**，本节只声明设备侧调用接口。

```cpp
#pragma once
#include <Arduino.h>

/* ===== 0. 档案注入上下文（doc/05 §5.1：由 net 层自动注入，调用方不得自行拼装） ===== */
/**
 * net 层内部职责：从 TF 卡 `/config/profile.json` 读取并维护下面三个字段，
 * 组装 `/api/v1/study/solve` 与 `/api/v1/study/voice` 明文时**自动注入**。
 *   - edu_stage            缺省或不在枚举内 -> 服务端 4004 -> 屏幕显示「请先在设置里完成学段选择」
 *   - enrollment_year     同上，必填
 *   - device_remaining_quota 仅供服务端对账，不参与放行判定（05 §4.2）
 * UI 状态机不得自行拼装这三个字段。
 */
struct NetProfileContext {
    char edu_stage[16];        /* 例："JUNIOR" */
    int enrollment_year;       /* 例：2026 */
    int device_remaining_quota;/* 上报值，服务端不信任 */
};
bool net_profile_load(NetProfileContext* out);
bool net_profile_update_quota(int effective_remaining_quota); /* 以网关下发值覆盖本地显示值 */

/* ===== 1. Wi-Fi 异步扫描与多热点管理 ===== */
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

void net_wifi_init_auto_connect();
void net_wifi_start_async_scan();
int net_wifi_get_scan_results(WifiApInfo* out_list, int max_count);
bool net_wifi_connect_and_save(const char* ssid, const char* password);
WifiConnState net_wifi_get_state();
int8_t net_wifi_get_rssi();

/* ===== 2. 配对握手与时间同步 ===== */
bool net_sync_ntp_time();
bool net_pair_with_code(const char* six_digit_code);

/* ===== 3. 双向 HMAC-SHA256 + AES-128-CTR 业务通信封装 (供 task_net_worker 调用) ===== */
/**
 * 内部自动完成明文加密、请求头签名(保存请求 X-Timestamp)、响应头验签 (X-Resp-Signature) 及响应解密。
 * api_path 必须是**纯 API 路径**（如 "/api/v1/study/solve"）：签名 PATH 与网关挂载前缀无关，
 * 而实际 URL 由本层按 NVS 中的网关域名 + 挂载前缀拼装（05 §3.2）。
 */
bool net_post_encrypted_json(const char* api_path, const char* plain_req_json, int* out_biz_code, char** out_resp_plain_json);

/* 业务高级封装（内部组装 JSON 并调用 net_post_encrypted_json，HTTP 超时锁定 20s） */
bool net_sync_bootstrap(const char* bootstrap_req_json, int* out_biz_code, char** out_resp_json);
/**
 * 支持纯拍照(pcm_data=NULL)与边说边拍(pcm_data 传入 16kHz PCM 缓冲，内部 Base64 编码至 pcm_base64)。
 * edu_stage / enrollment_year / device_remaining_quota 由本层从 profile.json 自动注入（05 §5.1）。
 */
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
4. **双应用分区表（设备侧落地，`firmware/ota_partitions.csv`）**：
   ```text
   # Name,   Type, SubType, Offset,   Size,     Flags
   nvs,      data, nvs,     0x9000,   0x5000,
   otadata,  data, ota,     0xE000,   0x2000,
   phy_init, data, phy,     0x10000,  0x1000,
   app0,     app,  ota_0,   0x20000,  0x400000,
   app1,     app,  ota_1,   0x420000, 0x400000,
   ```
   * `app0` / `app1` **各 4MB**（`0x400000`）、**必须等长**、偏移按 `0x10000` 对齐 —— `esp_ota_get_next_update_partition()` 靠 `SubType` 是 `ota_0` 还是 `ota_1` 来挑备用分区，两者不等长会让容量校验在两分区之间漂移；
   * `otadata` 必须保留：引导器据此选择启动分区；
   * **不划分任何文件系统分区**（不引 LittleFS / spiffs）—— 存储铁律「零 LittleFS 依赖 = 零分区表 = 零挂载失败分支」，未划分的空间闲置；
   * 当前固件约 `1.29MB`，相对 4MB 有 3 倍以上余量。
5. **待验证升级标记与回滚流程（设备侧自证安全）**：
   * 写完新固件、**切换启动分区之前**，先在 NVS（命名空间 `ota`）写入「待验证升级」标记：目标版本号 `pending` + 目标分区标签 `target`（`ota_0` / `ota_1`）+ 启动尝试计数 `tries`；
   * 新固件启动时读该标记：标记为空 → 不是待验证升级，正常启动；
   * 标记在、但当前运行的分区**不是** `target` → 说明已经退回旧版本（回滚已完成），清掉标记并给出温和中文提示；
   * 标记在、当前跑的正是 `target` → 这是待验证的新固件，进入健康自检；
   * **自检不过 → 把启动分区指回旧分区并重启**（`esp_ota_set_boot_partition(旧分区)` + `esp_restart()`），设备因此自行回退，**不依赖引导器的自动回滚**；
   * **连续 `2` 次启动都过不了自检（多半是起来就崩）→ 第 3 次启动直接退回旧分区**，杜绝「升级后无限重启」把设备刷成砖；
   * 自检通过 → 显式 `esp_ota_mark_app_valid_cancel_rollback()` 并清掉 NVS 标记。
6. **自检的落位与「Arduino 核心自动标记」的处理（关键实现约束）**：
   * 健康自检在 `setup()` 里**阻塞执行**（`ota_boot_selfcheck_run()`）—— 只有 `setup()` 没返回，「Arduino 核心在 `setup()` 之后自动调 `esp_ota_mark_app_valid_cancel_rollback()`」那条路径才没有机会插手；自检期间看门狗由自检函数持续喂，界面核（Core 1）照常运行，`task_ui_fsm` 的心跳计数照常增长；
   * 固件**不依赖**核心是否自动标记为 VALID：回滚由第 5 条的 NVS 标记 + 「指回旧分区 + 重启」自己兜底，即便核心已标 VALID 也退得回去；
   * 自检的外设判定口径：屏幕 / 摄像头 / 音频 / 摇杆 / 电量计**全部初始化成功**才算通过；**TF 卡不算** —— 没插卡是合法状态（纯在线模式），且固件升级不碰卡，不能因为没插卡就把一次成功的升级判成失败。
7. **设备侧代码分层与主机侧自测**：
   * `include/ota_client.h` + `src/net/ota_client.c`：**纯 C 判定层**（流式 SHA-256、首字节魔数 `0xE9`、字节数与分区容量、版本比较、升级前置条件、结果文案），**不依赖 `Arduino.h` / ESP-IDF**，主机 gcc 可编可测；
   * `src/net/ota_client_esp.cpp`：**设备侧实现层**（前置采样、查更新、流式下载、写入备用分区、激活、NVS 标记、重启后自检与回滚）；
   * `test/test_ota.c`：主机侧单测（分区容量计算、流式摘要与一次性一致、**改一个字节必须不一致**、首字节非 `0xE9` 必须被拒、版本比较、前置条件、错误文案不含技术术语），跑进 CI 的主机侧测试步骤。
8. **固件版本号与发布流程的耦合点（发布时必看）**：
   * 设备端版本号取 `include/net_api.h` 的 `NET_FW_VERSION`（当前 `1.0.0`），上报给网关的 `current_ver` 与升级页显示的「当前版本」都取它；
   * **每次发布新固件必须同步抬高 `NET_FW_VERSION`** —— 否则设备与网关比对版本号会认为「已是最新」而拒绝升级（设备侧以本地版本号为准，网关说有新版本但版本号没往前走时，设备不动）；
   * 网关侧的产物文件名是 `fw_{version}.bin`、`manifest.json` 的 `file` 字段记这个名字，设备从 `bin_url` 直接下载，**不自己拼文件名**（拼错就是 404）。