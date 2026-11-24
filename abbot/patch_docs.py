#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""abbot 契约文档修订脚本 —— 每处替换均带断言，失败即抛错。"""
import io, os, sys

DOC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "doc")
changed = []

def load(name):
    p = os.path.join(DOC, name)
    with io.open(p, encoding="utf-8") as f:
        return f.read()

def save(name, txt):
    p = os.path.join(DOC, name)
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(txt)
    changed.append(name)

def splice(txt, start_marker, end_marker, new_block, label):
    i = txt.find(start_marker)
    assert i >= 0, "[%s] 未找到起始锚点" % label
    j = txt.find(end_marker, i)
    assert j >= 0, "[%s] 未找到结束锚点" % label
    return txt[:i] + new_block + txt[j:]

def sub1(txt, old, new, label):
    assert txt.count(old) == 1, "[%s] 期望命中 1 次，实际 %d 次" % (label, txt.count(old))
    return txt.replace(old, new)

# ============================================================
# 05 —— 修正 §3.4 联调测试向量组 + §6.3 控制台线框模型名
# ============================================================
N = "05-云端接口与AI推理引擎契约.md"
t = load(N)

VEC = """### 3.4 AES-128-CTR 与 HMAC-SHA256 锁定联调测试向量
开发端云安全模块时，必须先用以下固定向量完成单元测试对齐（两端必须输出逐字节一致的结果）。

> **向量生成与校验规范（强制）**：
> 1. 本组向量由**权威标准实现**（服务端 `cryptography` / 固件 `mbedtls`）逐字节产出，两端各自复现同一组值即视为对齐；
> 2. 实施方必须**先用 NIST SP 800-38A F.5.1 CTR-AES128 官方向量自检自身实现**，确认 AES-CTR 分组与 128-bit 大端序计数器推进正确后，再核对本组向量；
> 3. 本组向量配有可执行验证脚本 `doc/verify/verify_envelope.py`，端到端覆盖「请求验签 → 信封解密回环 → 响应验签」，安全模块任何改动后必须重跑；
> 4. **严禁为了让测试变绿而反向修改实现** —— 实现与本组向量冲突时，一律以本组向量为准修正实现。

* **`device_secret` (Hex)**: `00112233445566778899aabbccddeeff0123456789abcdef0123456789abcdef`
* **AES-128 密钥 (前 16B Hex)**: `00112233445566778899aabbccddeeff`
* **固定测试 `IV` (16B Hex)**: `0102030405060708090a0b0c0d0e0f10`
* **明文 JSON**: `{"ping":1}`（10 字节 ASCII：`7b2270696e67223a317d`）
* **期望密文 (10B Hex)**: `c44759d703127191d6c9`
* **期望信封 `payload` (`Base64(IV + Ciphertext)`)**: `AQIDBAUGBwgJCgsMDQ4PEMRHWdcDEnGR1sk=`
* **局部块偏移解密测试向量（验证 `cards.enc` 第 `block_index = 2` 块按需解密）**：
  * 沿用同一 `AES-128` 密钥与 `IV_base` `0102030405060708090a0b0c0d0e0f10`；
  * 大端序 128-bit 推进 `block_index = 2` 后的计数器 `nonce_counter` (16B Hex): `0102030405060708090a0b0c0d0e0f12`；
  * 明文 `{"ping":1}`（10B Hex：`7b2270696e67223a317d`）在 `block_index = 2` 处的期望密文 (10B Hex): `41da37cc48e5aa3f8d06`
* **当 `METHOD="POST"`, `PATH="/api/v1/ping"`, `X-Timestamp="1795412000"`, `RAW_BODY='{"payload":"AQIDBAUGBwgJCgsMDQ4PEMRHWdcDEnGR1sk="}'` (50 字节 ASCII) 时**：
  * `RAW_BODY_SHA256`: `0ac4d1b2be7fff8b8d2fabfd520add4656718547d456b728ab3f0049244fee0a`
  * 期望请求签名 `X-Signature`: `e93d4a3e4aaeb49a2b413c5edfb7c7b877d9f7a85bf1d4e5c01fcdab7cf45b35`
* **当响应体 `RAW_RESP_BODY='{"code":0,"msg":"ok","payload":""}'` (34 字节 ASCII) 且 `REQ_X_TIMESTAMP="1795412000"` 时**：
  * `RAW_RESP_BODY_SHA256`: `5eaa24073527cf9b66a37318bd50123aac4d649814ea6d65281189b6bef49ef3`
  * 期望响应签名 `X-Resp-Signature`: `c4fc10efeec73e428b7e63bcb3777658e11a850ccf2a75047847771556f0ce3c`
* **空请求体 `SHA-256`（`GET` 或无 Body 场景）**: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`

"""

t = splice(t, "### 3.4 AES-128-CTR 与 HMAC-SHA256 锁定联调测试向量",
            "### 3.5 统一业务状态码表", VEC, "05/3.4向量组")

t = sub1(t,
    "|   轻量文本模型: [ MiniMax-Text-01              ]   |\n"
    "|   多模态视觉模型:[ MiniMax-VL-01               ]   |\n"
    "|   云端ASR模型:  [ whisper-1                    ]   |\n"
    "|   云端TTS音色ID:[ male-qn-qingse               ]   |",
    "|   轻量文本模型: [ MiniMax-M2.5                 ]   |\n"
    "|   多模态视觉模型:[ <按服务商填写，部署前校验>   ]   |\n"
    "|   云端ASR模型:  [ <按服务商填写，部署前校验>   ]   |\n"
    "|   云端TTS音色ID:[ <按服务商填写，部署前校验>   ]   |",
    "05/6.3模型名")

t = sub1(t,
    "|   每日深度导学配额: [ 10 ] 次/天                   |\n"
    "|   TTS 磁盘缓存: 已占用 18.4 MB  [ 清空音频缓存 ]   |",
    "|   每日深度导学配额: [ 10 ] 次/天                   |\n"
    "|   TTS 磁盘缓存: 已占用 18.4 MB  [ 清空音频缓存 ]   |\n"
    "|                                                  |\n"
    "|   ⚠ 模型名仅为占位示例，部署前必须逐个校验可用性   |\n"
    "|   ⚠ 模型下线时接口仍返回 HTTP 200，须解析 body    |\n"
    "|     的 base_resp.status_code（2069 = 模型已下架）|",
    "05/6.3模型名校验提示")

save(N, t)

# ============================================================
# 06 —— 统一 Phase 1 交付物与验收口径到 05 契约
# ============================================================
N = "06-模块化验收标准与联调排坑指南.md"
t = load(N)

t = sub1(t,
    "* **目录边界**：`server/`（Python 3.11+ / FastAPI / SQLite / Uvicorn）。\n"
    "* **核心交付物**：\n"
    "  1. `server/app/main.py` 与路由层：严格实现 `04` 契约规定的全部 RESTful API 端点、统一响应信封与错误码映射；\n"
    "  2. `server/app/database.py`：初始化 SQLite WAL 模式及 `questions`、`followups`、`schedules` 三张核心表；\n"
    "  3. `server/app/ai_pipeline.py`：封装 VLM 拍题多模态识别（强制输出符合 `03` 契约的 4 段式阶梯解析 JSON）、ASR 语音转写、多轮追问上下文拼接与流式 TTS 音频生成（输出 `16kHz / 16-bit / Mono` 原始 PCM/WAV）；\n"
    "  4. `server/cli_sim.py`（**内置控制台设备模拟器**）：轻量级命令行交互脚本，直接在终端模拟 ESP32 设备发起拍题图片上传、长按语音追问音频上传、课表拉取与设备状态上报，打印校验返回的结构化 JSON 与音频流落盘结果。",

    "* **目录边界**：`server/`（Python 3.10+ / FastAPI / Uvicorn；持久化仅用 `05` 契约第 2.2 节规定的 `storage/*.json` 原子写文件，**不引入关系型数据库**，以严守 `05` 第 1 节「无状态算力网关」铁律）。\n"
    "* **核心交付物**（文件名与 `05` 契约第 2.2 节 `app/` 目录树严格一致）：\n"
    "  1. `server/app/main.py`：FastAPI 入口、全局异常处理、静态页面挂载；路由层严格实现 `05` 契约第 5 节规定的全部 RESTful API 端点、统一响应信封（`{code, msg, payload}`）与错误码映射；\n"
    "  2. `server/app/config.py`：`config.yaml` 加载与热重载；模型、ASR、TTS、配额、OTA 策略一律由此配置，严禁代码硬编码厂商或模型名；\n"
    "  3. `server/app/security.py`：双向 HMAC-SHA256 验签、AES-128-CTR 加解密、下载 Token 签发与校验（须通过 `05` 第 3.4 节向量组与 `doc/verify/verify_envelope.py`）；\n"
    "  4. `server/app/router_ai.py`：三级意图分流、每设备 `asyncio.Lock` 并发锁、会话级配额持久化仲裁与 `05` 第 4.3 节防依赖教学 System Prompt 注入；\n"
    "  5. `server/app/speech_engine.py`：纯云端 ASR 识别与纯云端 TTS 合成、Hex 解码落盘（**严禁误用 Base64 解码**）、MD5 缓存池；\n"
    "  6. `server/app/storage.py`：`storage/` 目录持久化层 —— `devices.json`、`quota.json`、`schedules/latest.json`、`packs/`、`books/`、`ota/`、`cache/`；全部写入执行 `05` 第 4.2 节「写 `.tmp` → `flush()` → `os.fsync()` → `os.replace()`」原子替换协议，磁盘写满时捕获 `OSError`、删除残缺 `.tmp` 并保持进程内状态可用；\n"
    "  7. `server/app/pack_builder.py`：原始文本 → 结构化 `source.json` → AES-128 加密 `cards.enc` + `index.idx`（含 `IDX1` 文件头与 CRC32）；\n"
    "  8. `server/app/book_cleaner.py`：**零 AI 调用**的纯本地电子书清洗（`.epub` / `.pdf` / `.docx` / `.txt` → 标准 UTF-8 `.txt` + `.toc`）；\n"
    "  9. `server/app/ota_manager.py`：固件魔数与 SHA-256 校验、多版本仓库、灰度白名单与熔断控制；\n"
    "  10. `server/cli_sim.py`（**内置控制台设备模拟器**）：轻量级命令行交互脚本，直接在终端模拟 ESP32 设备发起拍题图片上传、长按语音追问音频上传、课表拉取与设备状态上报，打印校验返回的结构化 JSON 与音频流落盘结果。",

    "06/Phase1交付物")

t = sub1(t,
    "* [ ] **后端服务启动正常**：`uvicorn` 启动零报错，SQLite 数据库表自动创建成功。\n"
    "* [ ] **控制台模拟拍题闭环**：运行 `python cli_sim.py` 发送本地测试习题照片，后端在预期时间内返回完整 4 段式 JSON 解析（含题干识别、核心考点、分步提示、最终答案），且字段类型与 `04` 契约 100% 一致。\n"
    "* [ ] **控制台模拟语音追问闭环**：通过 `cli_sim.py` 发送测试 WAV 语音，后端准确完成 ASR 转写、关联历史题干推理，并返回可正常播放的 `16kHz 16-bit Mono` TTS 音频流。\n"
    "* [ ] **固件零报错编译通过**：ESP32-S3 工程执行 `pio run` 编译全部通过，无内部 SRAM 溢出告警，正确启用 `BOARD_HAS_PSRAM` 与 `qio_opi`。",

    "* [ ] **后端服务启动正常**：`uvicorn` 启动零报错，`storage/` 目录与核心 JSON 文件自动创建成功。\n"
    "* [ ] **端云安全信封自检通过**：运行 `python doc/verify/verify_envelope.py`，请求验签、信封解密回环、响应验签三项全部通过（`05` 第 3.4 节向量组逐字节一致）。\n"
    "* [ ] **控制台模拟拍题闭环**：运行 `python cli_sim.py` 发送本地测试习题照片，后端在预期时间内返回阶梯解析 JSON，其四个字段 `status` / `speech_text` / `display_markdown` / `feynman_question` 与 `05` 契约第 4.3 节 System Prompt 约束及第 5 节接口表 **100% 一致**。\n"
    "* [ ] **控制台模拟语音追问闭环**：通过 `cli_sim.py` 发送测试 WAV 语音，后端准确完成 ASR 转写、关联历史题干推理，并返回可正常播放的 `16kHz 16-bit Mono` TTS 音频流。\n"
    "* [ ] **固件零报错编译通过**：ESP32-S3 工程执行 `pio run` 编译全部通过，无内部 SRAM 溢出告警，正确启用 `BOARD_HAS_PSRAM` 与 `qio_opi`。",

    "06/无硬件验收清单")

save(N, t)

# ============================================================
# 03 —— 长按阈值统一到 600ms
# ============================================================
N = "03-竖屏界面布局与交互规范.md"
t = load(N)
t = sub1(t,
    "   * 在允许全局语音的页面长按摇杆 `450ms` 呼出，顶部动态显示实时音量波形条与倒计时（最长 `10 秒`）；",
    "   * 在允许全局语音的页面长按摇杆 `600ms` 呼出（阈值由 HAL 层 `02` 文档第 3.4 节**唯一裁定**，UI 层严禁自行计时判定），顶部动态显示实时音量波形条与倒计时（最长 `10 秒`）；",
    "03/长按阈值")
save(N, t)

# ============================================================
# 02 —— 修正短按窗口；补写双核任务/14 条防线/hal_interfaces.h
# ============================================================
N = "02-硬件抽象层与底层驱动契约.md"
t = load(N)

t = sub1(t,
    "  * `EVENT_CLICK`（短按确认）：按下时长 `30ms ~ 500ms`，且释放后 `250ms` 内无第二次按下。\n"
    "  * `EVENT_DOUBLE_CLICK`（双击返回上一级）：两次有效短按间隔 `< 250ms`。\n"
    "  * `EVENT_LONG_PRESS_START` / `EVENT_LONG_PRESS_END`（长按语音追问）：持续按住 **`>= 600ms`** 立即触发开始录音事件，松开瞬间触发结束录音事件（避免长按后误触发短按）。",

    "  * **唯一裁定铁律**：按下时长与「短按 / 双击 / 长按」的归类**只由 HAL 层本节裁定一次**，UI 层（`03` 文档浮层与页面状态机）严禁自行计时判定或另设阈值。\n"
    "  * `EVENT_CLICK`（短按确认）：按下时长 `30ms ~ 600ms`（**不含 `600ms`**）且释放后 `250ms` 内无第二次按下时，于释放瞬间判定为短按。\n"
    "  * `EVENT_DOUBLE_CLICK`（双击返回上一级）：两次有效短按间隔 `< 250ms`。\n"
    "  * `EVENT_LONG_PRESS_START` / `EVENT_LONG_PRESS_END`（长按语音追问）：持续按住 **`>= 600ms`** 时**立即**触发开始录音事件，松开瞬间触发结束录音事件。\n"
    "  * **互斥保证**：短按窗口上界（`600ms` 不含）与长按下界（`600ms` 含）严格相接且不重叠 —— 按住不足 `600ms` 释放必判短按（够 `600ms` 未释放必判长按），**不存在既判短按又判长按的歧义区间**；`30ms` 以下的抖动按消抖丢弃，不产生任何事件。\n"
    "  * **开机屏蔽 `500ms`**：仅指固件启动后屏蔽 `SW` 事件的静默窗口，与长按阈值无关，二者不可混用。",

    "02/短按窗口")

TAIL = """

## 四、 FreeRTOS 双核任务划分与队列规范

> 本节补齐 `00` 文档地图中承诺但原文缺失的内容。所有划分均以「**界面永不阻塞**」为唯一原则。

### 4.1 双核硬划分（不可违背）

| 核 | 独占任务 | 职责 | 禁止事项 |
| :--- | :--- | :--- | :--- |
| **Core 1（界面核）** | `task_ui_fsm` | LVGL 渲染、页面路由状态机、手势仲裁、三层浮层优先级抢占、错误码查表渲染 | **严禁**执行任何阻塞式网络或 TF 卡 IO |
| **Core 1（界面核）** | `task_joystick` | `50Hz` 摇杆采样、死区滞回、短按/双击/长按归类（见第 3.4 节） | 严禁把未归类事件直接抛给 UI 层自行判定 |
| **Core 0（IO 核）** | `task_net_worker` | Wi-Fi 异步扫描与连接、HTTP 请求与 TLS、加密业务请求收发、OTA 下载与写分区 | 严禁触碰 LVGL 对象 |
| **Core 0（IO 核）** | `task_camera` | 取景预览、AF 对焦（持 `g_i2c_bus_mutex`）、UXGA 抓拍 | 严禁在 Core 1 阻塞等待对焦 |
| **Core 0（IO 核）** | `task_audio` | I2S 半双工录音与 TTS 流式播放 | 严禁与 Core 1 同时占用 `I2S_NUM_0` |
| **Core 0（IO 核）** | `task_storage` | TF 卡读写、原子写入、目录遍历、空间回收 | 严禁在 Core 1 同步调用 |

* **依据**：`04` 文档第 1.1 节「在 Core 0 后台执行 `WiFi.scanNetworks(true)`，扫描期间绝不阻塞 Core 1 界面渲染与摇杆采样」。
* **双核协作铁律**：Core 0 产出的结果（网络应答、提示卡片、TTS 就绪）**必须经队列投递回 Core 1**，严禁 Core 0 直接调用 LVGL API。

### 4.2 跨核通信队列规范（严禁裸共享变量）

| 队列 | 元素类型 | 方向 | 长度 | 载荷要点 |
| :--- | :--- | :--- | :--- | :--- |
| `q_joystick_event` | `joystick_event_t` | 采样 → UI | `8` | 已完成消抖与归类的事件（方向 / CLICK / DBL_CLICK / LONG_START / LONG_END） |
| `q_ui_command` | `ui_command_t` | IO 核 → UI 核 | `16` | 网络应答、提示卡片、刷新页面等 UI 指令 |
| `q_photo_request` | `photo_request_t` | UI 核 → 相机任务 | `4` | 抓拍指令（含「边说边拍」标志与目标分辨率） |
| `q_audio_pcm` | `audio_pcm_chunk_t` | 录音 → 播放 | `8` | 16kHz / 16bit / Mono PCM 分块（总缓冲硬上限 `10 秒`） |
| `q_net_request` | `net_request_t` | UI 核 → 网络任务 | `8` | 已加密业务请求与回调解密结果 |

* **互斥锁**：全局 `g_i2c_bus_mutex`（本文件第 2.1 节，摄像头与电量计共用 `I2C_NUM_0`）；TF 卡独占访问由 `task_storage` 单线程串行化，不再额外加锁。
* **内存铁律**：帧缓冲、LVGL 双缓冲、录音缓冲、HTTP 接收缓冲一律 `heap_caps_malloc(size, MALLOC_CAP_SPIRAM)`（本文件第 2.2 节）；FreeRTOS 各任务栈分配在内部 SRAM。

---

## 五、 14 条底层防线（跨模块不可协商清单）

> 以下 14 条为全机不可协商的底线行为，每条均已在既有契约中有出处，此处仅做**汇总与交叉引用**，不新增行为。

| # | 防线 | 规范出处 |
| :---: | :--- | :--- |
| 1 | I2C 总线互斥：摄像头 SCCB 与电量计共用 `I2C_NUM_0`，所有访问持 `g_i2c_bus_mutex`（`100ms` 超时），拍照期间禁轮询电量 | 本文件 2.1 |
| 2 | 大块内存只用 OPI PSRAM：帧缓冲 / LVGL 缓冲 / 录音缓冲 / HTTP 缓冲走 `MALLOC_CAP_SPIRAM`，内部 SRAM 仅留给任务栈与协议栈 | 本文件 2.2 |
| 3 | 摇杆中点动态校准：开机 `300ms` 内连采 `20` 次取均值作零点，死区 `±650` 滞回判定 | 本文件 3.4 |
| 4 | 按压归类唯一裁定：短按 `< 600ms`、长按 `>= 600ms` 互斥无歧义，UI 层不得另设阈值 | 本文件 3.4 |
| 5 | I2S 半双工独占 + 录音首 `30ms` 瞬态丢弃：播放前必须 `i2s_driver_uninstall` 释放 RX | 本文件 3.3 |
| 6 | AF 超时熔断：VCM 对焦轮询上限 `1500ms`，超时立即抓拍，绝不卡死 | 本文件 3.2 |
| 7 | 低电分级保护：`< 3.40V`（`SOC <= 5%`）弹窗 `3 秒` 后强制深度睡眠 | 本文件 3.6 |
| 8 | 浅休眠唤醒后外设重初始化：退 `Light-Sleep` 后必须重建 SPI 屏、I2C、I2S 状态再渲染 | 本文件 3.6 |
| 9 | TF 卡掉卡降级：不崩溃，切「纯在线阅后即焚模式」，仅跳过本地归档 | 本文件 3.5 |
| 10 | 原子写入四步 + 写满不覆盖：`.tmp` → `flush()` → 校验长度 → `.bak` → 重命名 | `04` 第 2.3 节 |
| 11 | UTF-8 字节边界对齐：偏移落在 `(byte & 0xC0) == 0x80` 时必须向前对齐到字符首字节 | `07` 第 3 节 |
| 12 | 索引与偏移越界熔断：`index.idx` 魔数与 CRC 校验、`start_offset`/长度越界即返回 `ERR_PACK_CORRUPT`；`.toc` 非法条目丢弃 | `04` 第 4.1/4.2 节、`07` 第 2.2 节 |
| 13 | 端云时间窗与 Token 过期：`X-Timestamp` 偏差 `> 60s` 拒绝；下载 Token 有效期 `10 分钟` | `05` 第 3.2/3.3 节 |
| 14 | OTA 三重校验 + 自检回滚：魔数 `0xE9`、长度、SHA-256 三重校验；新固件稳定 `20 秒` 且心跳 `>= 50` 次方标记有效，否则看门狗回滚 | `04` 第六节 |

---

## 六、 `hal_interfaces.h` 统一接口签名（HAL 层唯一对外契约）

> 固件内**任何**模块都只能经下列签名访问 HAL，**严禁直接调用 `Wire` / `SD_MMC` / `i2s_driver_*` / `esp_camera_*` 等底层 API**（唯一例外：`hal_storage` 与 `hal_audio` 的实现文件自身）。

```cpp
#pragma once
#include <Arduino.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ---------- 通用返回码 ---------- */
typedef enum {
    HAL_OK              = 0,
    HAL_ERR_INVALID     = -1,   // 参数非法
    HAL_ERR_TIMEOUT     = -2,   // 超时（含 AF 对焦熔断）
    HAL_ERR_IO          = -3,   // 底层总线 / SD 读写失败
    HAL_ERR_NOT_FOUND   = -4,   // 文件或目标不存在
    HAL_ERR_NO_SPACE    = -5,   // 空间不足（原子写入失败时保留原文件）
    HAL_ERR_UNSUPPORTED = -6,   // 该设备/状态不支持（如无 TF 卡）
} hal_status_t;

/* ---------- hal_storage：TF 卡存储（SD_MMC 1-Bit） ---------- */
hal_status_t hal_storage_init_and_scaffold(void);                 // 挂载 + 空白卡自愈建全套目录
hal_status_t hal_storage_get_file_size(const char *path, uint32_t *out_size);
hal_status_t hal_storage_read_range(const char *path, uint32_t offset,
                                    uint8_t *buf, uint32_t len, uint32_t *out_read);
hal_status_t hal_storage_read_text(const char *path, char *buf, uint32_t max_len, uint32_t *out_len);
hal_status_t hal_storage_atomic_write_text(const char *path, const char *text, uint32_t len);
hal_status_t hal_storage_list_files(const char *dir_path, void (*cb)(const char *name, void *ctx), void *ctx);
hal_status_t hal_storage_delete_file(const char *path);
hal_status_t hal_storage_get_dir_usage(const char *dir_path, uint64_t *out_bytes);
hal_status_t hal_storage_get_file_mtime(const char *path, uint32_t *out_epoch);
bool        hal_storage_is_mounted(void);

/* ---------- hal_display：ST7796S 竖屏 + 背光 PWM ---------- */
hal_status_t hal_display_init(void);
hal_status_t hal_display_flush(void);
hal_status_t hal_display_set_backlight(uint8_t level);             // 0~5 档
uint8_t     hal_display_get_backlight(void);

/* ---------- hal_camera：OV5640 拍题 ---------- */
hal_status_t hal_camera_init(void);
hal_status_t hal_camera_start_preview(void);                        // FRAMESIZE_HVGA
hal_status_t hal_camera_capture_jpeg(uint8_t **out_jpg, size_t *out_len);  // 切 UXGA 抓拍，含 1500ms AF 熔断
hal_status_t hal_camera_stop_preview(void);

/* ---------- hal_audio：ICS43434 + MAX98357 半双工 I2S ---------- */
hal_status_t hal_audio_init(void);
hal_status_t hal_audio_start_record(void);                         // 16kHz/16bit/Mono，丢弃首 30ms
hal_status_t hal_audio_stop_record(void);
hal_status_t hal_audio_play_wav_file(const char *path);            // 校验 RIFF 头，跳过 44 字节
hal_status_t hal_audio_set_volume(uint8_t level);                  // 1~5 档

/* ---------- hal_joystick：双轴摇杆五向事件 ---------- */
hal_status_t hal_joystick_init(void);                              // 开机 300ms 中点校准
hal_status_t hal_joystick_poll(void);                              // 50Hz 调用，完成消抖与归类后投递队列

/* ---------- hal_power：MAX17043 电量计与低功耗 ---------- */
hal_status_t hal_power_init(void);
hal_status_t hal_power_read_battery(float *out_volts, uint8_t *out_soc);  // 持 g_i2c_bus_mutex
hal_status_t hal_power_enter_light_sleep(void);                    // GPIO0 / RTC 闹钟唤醒
hal_status_t hal_power_enter_deep_sleep(void);

/* ---------- 休眠唤醒后的外设恢复（02 第 3.6 节强制） ---------- */
hal_status_t hal_recover_after_sleep(void);                        // 重建 SPI 屏 / I2C / I2S 状态

#ifdef __cplusplus
}
#endif
```

* **实现约束**：`hal_camera` 内置 `const uint8_t` OV5640 VCM 自动对焦固件数组（严禁从 TF 卡动态加载）；`hal_audio` 实现严格半双工状态机与 `MIC_I2S_CHANNEL` 声道切换宏；`hal_power` 负责 `Light-Sleep` 唤醒后的外设恢复编排。
* **错误映射**：HAL 层只返回 `hal_status_t` 语义码，**严禁在 HAL 内拼装任何面向用户的中文提示文案**；所有面向用户的文案一律经 `03` 文档第四节的 `ui_show_friendly_error(ErrorCode)` 查表渲染。
"""

t = t.rstrip("\n") + "\n" + TAIL
save(N, t)

# ============================================================
# 04 —— §4.2 局部块偏移向量就地补齐可核对值
# ============================================================
N = "04-无线网络OTA与本地存储契约.md"
t = load(N)
t = sub1(t,
    "4. 构造当前块初始计数器：`nonce_counter = BigEndian128(IV_base) + block_index`（无符号 128 位大端序加法进位，开发时必须先通过 `05` 文档第 3.4 节局部块偏移向量单测）；",
    "4. 构造当前块初始计数器：`nonce_counter = BigEndian128(IV_base) + block_index`（无符号 128 位大端序加法进位，开发时必须先通过 `05` 文档第 3.4 节局部块偏移向量单测）。就地核对值：`IV_base = 0102030405060708090a0b0c0d0e0f10`、`block_index = 2` 时 `nonce_counter = 0102030405060708090a0b0c0d0e0f12`，明文 `7b2270696e67223a317d`（`{\"ping\":1}`）的期望密文为 `41da37cc48e5aa3f8d06`；",
    "04/局部块向量")
save(N, t)

# ============================================================
# 00 —— 文档地图补充 verify 目录与 02 新增章节
# ============================================================
N = "00-开发总纲.md"
t = load(N)
t = sub1(t,
    "* **`02-硬件抽象层与底层驱动契约.md`**：PlatformIO 构建参数、双分区防变砖分区表、FreeRTOS 双核任务与队列规范、14 条底层防线及全套 `hal_interfaces.h` 接口定义。",
    "* **`02-硬件抽象层与底层驱动契约.md`**：PlatformIO 构建参数、I2C 互斥与 PSRAM 分配边界、六大 HAL 驱动容错规范、FreeRTOS 双核任务划分与跨核队列规范、14 条底层防线清单及全套 `hal_interfaces.h` 接口签名。",
    "00/02地图")
t = sub1(t,
    "* **`07-纯本地电子书阅读模块契约.md`**：纯本地 UTF-8 `.txt` 流式分页内核、`.toc` 章节索引、跨午夜宵禁/时长护栏及树莓派纯本地电子书清洗工坊规范。",
    "* **`07-纯本地电子书阅读模块契约.md`**：纯本地 UTF-8 `.txt` 流式分页内核、`.toc` 章节索引、跨午夜宵禁/时长护栏及树莓派纯本地电子书清洗工坊规范。\n"
    "* **`verify/`**：端云安全信封的可执行验证脚本 `verify_envelope.py` 与配套向量说明（`05` 第 3.4 节向量组的落地校验入口）。",
    "00/verify地图")
save(N, t)

print("=== 修订完成 ===")
for c in changed:
    print("  -", c)
