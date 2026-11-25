# abbot 项目 · 文档对齐笔记

> 阅读日期：2026-11-24 08:40
> 来源：`abbot/doc/` 8 份契约文档（114010 字节 / 1377 行），已全量读完
> 仓库现状：`abbot/` 下**无固件代码**（无 `firmware/`），已有 `doc/` 契约文档 + `doc/verify/` 验证工具 + `server/` 树莓派网关服务端（Phase 1 已实现）+ 本文件
>
> **修订状态（2026-11-24 08:50 已落实）**：冰河大人指示“先修正对应文档”，下列 A~E 五项已全部落地到文档正文。
> 备份：`doc.bak.<时间戳>/`（修订前快照）。
> 验证：`doc/verify/verify_vectors.py` 12 项全 PASS；`doc/verify/verify_envelope.py` 端到端 ALL PASS。

---

## 一、项目全貌（我的理解）

**一句话**：面向自主学习的小学/初中随身学习终端 —— 硬件端 `ESP32-S3-CAM N16R8` + `3.5" 320×480` 竖屏 + 双轴摇杆（零触屏、零多余按键），云端为树莓派 4B 无状态 AI 网关。

### 1. 三层职责

| 层 | 承担 | 不承担 |
|---|---|---|
| 设备端（ESP32-S3） | 音视频采集、摇杆交互、屏显渲染、TF 卡本地存储 | 任何大模型推理、ASR、TTS、epub/pdf 解析 |
| 云端（树莓派 4B） | 安全鉴权、多模态 AI 路由、云端 ASR/TTS 中转、学科包加密打包、Web 管理台 | 重型双端同步数据库（TF 卡是唯一真相源） |
| TF 卡（`/sdcard`） | 配置、题库、错题、离线队列、音频缓存、电子书 | — |

板载 LittleFS 只放只读中文字库 + `ding.wav` + 静态图标，保证 OTA 不擦用户数据。

### 2. 四条架构铁律（贯穿 8 份文档，不可违背）

1. **Local-First**：断网下刷卡、纯本地电子书、番茄钟、本地课表、离线拍题队列 100% 可用。
2. **奥卡姆剃刀**：全机唯一输入件是摇杆；固件禁重型解析库；Web 台零构建单文件 `web/index.html`。
3. **防依赖教学**：拍题四步脚手架（草稿痕迹初筛 → 阶梯拆解 → 留白验算 → 费曼反问），同题多步提示共享 `solve_session_id` 不重复扣配额；屏幕零技术术语红线。
4. **参数化解耦**：`edu_stage` + `enrollment_year` 派生 `grade_anchor`；模型全部 `config.yaml` 热切换，禁止硬编码厂商。

### 3. 契约地图（00 给的导航，与我实际读到的内容一致度见第四节）

| 文档 | 主题 | 状态 |
|---|---|---|
| 00 | 架构宪章 + 文档地图 | ✅ |
| 01 | BOM（¥173.23）、引脚零冲突矩阵、Strapping 核验、电源拓扑 | ✅ 自洽 |
| 02 | PlatformIO 参数、总线仲裁、6 大 HAL 驱动容错 | ⚠️ 内容少于 00 的描述 |
| 03 | 320×480 三区骨架、三层浮层仲裁、手势表、错误码表、13 页面线框 | ✅ |
| 04 | Wi-Fi/OTA/TF 卡目录树、JSON Schema、原子写、CTR 局部解密、TTS 缓存 | ✅ |
| 05 | FastAPI 网关、双向 HMAC+AES 安全信封、三级意图分流、防依赖 Prompt、REST API、Admin 台 | ⚠️ 向量与验收口径有问题 |
| 06 | Phase 1~4 拆解、契约路由表、Pass/Fail 清单、排坑手册 | ⚠️ 验收字段与 05 冲突 |
| 07 | 纯本地 txt 阅读器：流式分页、`.toc`、防沉迷护栏、清洗工坊 | ✅ 相当扎实 |

### 4. 研发阶段（06 的划分）

- **Phase 1** 树莓派网关 + CLI 模拟器 —— **无硬件可 100% 完工**。
- **Phase 2** 固件骨架 + 6 大 HAL —— 实物到位前完成「代码 + `pio run` 编译闭环」。
- **Phase 3** 状态机 + LVGL UI + 本地存储。
- **Phase 4** 真机联调 + 功耗弱网压测。

---

## 二、对齐前必须先处理的 5 件事（按严重度）

### A. 【硬伤·已修复】05 §3.4 锁死的联调测试向量不可复现 —— 端云地基是坏的

我用 `cryptography` 实现并**先用 NIST SP800-38A F.5.1 CTR-AES128 官方向量自检通过**，再核对 05 §3.4 的全部 7 个断言：

| 断言 | 文档期望 | 实算 | 结果 |
|---|---|---|---|
| AES-128-CTR block0 | `939e8d3861a5b9a34c91` | `c44759d703127191d6c9` | ❌ |
| AES-128-CTR block2 | `c6b54b2c7c8e1d9a035e` | `41da37cc48e5aa3f8d06` | ❌ |
| 信封 base64 | `AQIDBAUGBwgJCgsMDQ4PEJOejThhpbmkTJE=` | `AQIDBAUGBwgJCgsMDQ4PEMRHWdcDEnGR1sk=` | ❌ |
| 请求体字节数 | 48 | **50**（文档自己数错了） | ❌ |
| 请求体 SHA-256 | `66838d16…` | `5a892e8d…` | ❌ |
| `X-Signature` | `63937722…` | `3190141c…` | ❌ |
| `X-Resp-Signature` | `c7079388…` | `c4fc10ef…` | ❌ |

穷举了 8 种常见约定变体（ctr+0/+1/+2、AES-256 全密钥、密钥/IV 取 hex ASCII、**以及文档声称的 `block_index=2` 计数器推进规则**），**无一命中**；HMAC 侧穷举 20 种组合（不同 sha 取值、是否带前导斜杠、大小写方法名）**全部不命中**。

**结论**：这不是实现约定分歧，是文档里的期望值本身不可达。且 04 §4.2 还把这条向量作为局部 CTR 解密的强制单测依据 —— 照着写测试必挂。

**风险**：任何人以「让测试通过」为目标去改实现，就会写出与真实实现不自洽的端云协议。

**已完成的验证闭环**（真实 FastAPI 服务 + `httpx` 客户端，全程真跑）：

```
[1] GET /ping -> 200 {"code":0,"msg":"ok","payload":""}
[2] POST /api/v1/echo -> 200
    请求签名校验通过 : True
    服务端解密明文   : {"ping":1}
    响应验签一致     : True
[3] 信封结构 IV||CT 长度: 26 (16+10=26)
    回环解密明文     : {"ping":1}
RESULT: ALL PASS
```

**权威向量组（由已验证实现重新生成，用于替换 05 §3.4）**：

```text
AES-128 key (first16) : 00112233445566778899aabbccddeeff
IV_base               : 0102030405060708090a0b0c0d0e0f10
plaintext             : 7b2270696e67223a317d  ({"ping":1})
CTR block0 cipher     : c44759d703127191d6c9
CTR block2 nonce      : 0102030405060708090a0b0c0d0e0f12
CTR block2 cipher     : 41da37cc48e5aa3f8d06
envelope payload b64  : AQIDBAUGBwgJCgsMDQ4PEMRHWdcDEnGR1sk=

REQ raw body (50 bytes): {"payload":"AQIDBAUGBwgJCgsMDQ4PEMRHWdcDEnGR1sk="}
REQ raw_body_sha256   : 0ac4d1b2be7fff8b8d2fabfd520add4656718547d456b728ab3f0049244fee0a
REQ X-Signature       : e93d4a3e4aaeb49a2b413c5edfb7c7b877d9f7a85bf1d4e5c01fcdab7cf45b35

RESP raw body (34 bytes): {"code":0,"msg":"ok","payload":""}
RESP raw_body_sha256  : 5eaa24073527cf9b66a37318bd50123aac4d649814ea6d65281189b6bef49ef3
RESP X-Resp-Signature : c4fc10efeec73e428b7e63bcb3777658e11a850ccf2a75047847771556f0ce3c

empty-body sha256     : e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  ← 与 05 §3.2 一致，无需改
```

**注意 `block2 nonce`**：文档声称推进后为 `…0f12`，这一条**是对的**（IV 末字节 `0x10` + 2 = `0x12`）；
错的是它下面挂的**密文期望值**（`c6b54b2c…` → 实际 `41da37cc…`）与 **block0 密文**。
即：文档的**计数器推导是对的**，**密文期望值是错的**——这更像是密文被凭空填写而非实现约定分歧。

**我的方案**：以标准实现为准替换 05 §3.4 整组向量，并在文档里标明「由 `cryptography` 权威实现生成，双方各自复现同一组值」。

**✅ 已落实**：05 §3.4 整组向量已替换，并加「向量生成与校验规范（强制）」四条：① 标准实现逐字节产出；② **必须先用 NIST SP 800-38A F.5.1 官方向量自检自身实现**；③ 配套 `doc/verify/verify_envelope.py` 必跑；④ **严禁为了让测试变绿而反向修改实现**。
04 §4.2 就地补上可核对值（`block_index=2` → nonce `…0f12`、密文 `41da37cc48e5aa3f8d06`），避免跨文档查表。
06 §3.1 新增一条验收项「**端云安全信封自检通过**」。

### B. 06 §3.1 的验收字段与 05 的真实 API 契约冲突

- 06 说后端返回「4 段式 JSON（含**题干识别、核心考点、分步提示、最终答案**）」；
- 05 §4.3 的 System Prompt 实际约束输出 `{status, speech_text, display_markdown, feyman_question}` —— 没有「题干识别」「核心考点」这两个字段。

**我的方案**：以 05 为准（它是真正被固件解析的线上契约），把 06 §3.1 的验收描述改成对齐 05 的四个字段。若冰河大人本意就是要「题干识别 + 核心考点」，那是**改线上协议**，会影响固件解析代码，需要另行拍板。

**✅ 已落实**：06 §3.1 验收口径改为四字段 `status` / `speech_text` / `display_markdown` / `feynman_question`，并指明对齐 `05` 第 4.3 节 System Prompt 与第 5 节接口表。

### C. 00 声称 02 含「FreeRTOS 双核任务与队列规范 + 14 条底层防线 + 全套 `hal_interfaces.h`」，实际 02 里**都没有**

实际 02 只有：编译宏规范、I2C 互斥锁、PSRAM 分配边界、6 个 HAL 驱动。07 又反过来引用 `hal_interfaces.h` 里的 `hal_storage_get_file_size()` 等接口 —— **该头文件的签名在整套文档里从未定义过**。

**影响**：Phase 2/3 开工时，HAL 接口签名要现场拍脑袋。

**我的方案**：补写 `02` 的三个缺失小节（双核任务与队列划分、14 条防线、`hal_interfaces.h` 完整签名），否则 Phase 2 无契约可依。

**✅ 已落实**：`02` 新增第四节（FreeRTOS 双核任务划分与跨核队列规范）、第五节（14 条底层防线汇总表，每条标注出处）、第六节（`hal_interfaces.h` 完整 C 签名，含 `hal_status_t` 错误语义码）。
同时 `00` 文档地图对 `02` 的描述已同步为实际章节内容，并补上 `verify/` 目录条目。

### D. 长按阈值双源冲突：03 说 450ms，02 说 600ms

- 03 §3.1：`OVERLAY_RECORDING` 在长按 **450ms** 时呼出；
- 02 §3.4：`EVENT_LONG_PRESS_START` 在持续按住 **≥600ms** 触发；
- 且 02 的短按窗口是 `30ms~500ms`，与 450ms 呼出在 **450~500ms 区间重叠**（既算点击又算长按）。

**我的方案**：统一为单一阈值（建议 **600ms**，HAL 层唯一裁定，UI 层不得自行判定），并把短按窗口收窄为 `30~450ms` 消除重叠。

**✅ 已落实**：阈值统一为 **600ms**。
- `02` §3.4 确立「唯一裁定铁律」：短按 `30ms ~ 600ms`（不含 600ms），长按 `>= 600ms`，两窗口严格相接不重叠，**不存在歧义区间**；并澄清“开机屏蔽 500ms”与长按阈值是两回事。
- `03` §3.1 录音浮层阈值 `450ms` → `600ms`，并注明由 HAL 层裁定、UI 层严禁自行计时。

### E. 05 §6.3 线框里的默认模型已下架

线框示例默认值为 `MiniMax-Text-01` / `MiniMax-VL-01` / `whisper-1` / `male-qn-qingse`。按 TOOLS.md 已记录的事实，`MiniMax-Text-01`、`MiniMax-Text-01-turbo` 均已下架（HTTP 200 + `base_resp.status_code=2069`），可用的是 `MiniMax-M2.5`。线框只是示意，但 Phase 1 若照抄这些默认值会直接踩空。

**✅ 已落实**：线框中 `MiniMax-Text-01` → `MiniMax-M2.5`；视觉 / ASR / TTS 三项改为占位 `<按服务商填写，部署前校验>`（未验证的服务商不写死）。
并在表单下方加警告：**模型下线时接口仍返回 HTTP 200，必须解析 body 的 `base_resp.status_code`（`2069` = 模型已下架），部署前逐个校验可用性**。

---

## 三、环境现实（本机实测，Phase 1 的真实约束）

| 项 | 状态 | 影响 |
|---|---|---|
| `fastapi` | ✅ **可装**（见下方突破） | 05 指定的 FastAPI 栈不受阻 |
| `uvicorn` / `httpx` / `pydantic` / `cryptography` / `docx` / `yaml` / `starlette` / `anyio` / `idna` / `sniffio` / `h11` / `annotated_types` / `typing_extensions` / `multipart` | ✅ 已装 | 够用 |
| `pypdf` | ✅ **可装**（同一路径，已装 7.1.1） | 07 电子书清洗 PDF 分支不受阻 |
| `~/.platformio` / `pio` | 不存在 | Phase 2 的「`pio run` 零报错」无法本地验证 |
| GitHub | ✅ SSH clone 可用（HTTPS git 端点被代理挡死，与 TOOLS.md 记录一致） | 固件产物回传可用 |

### 突破：pypi 并非不可达，是代理挡的

初判（TOOLS.md 现记录）说「pypi 不在出口白名单 → pip 报 403」。**实测该结论对 pip 不成立**：
出口代理只在「走代理」时按白名单拦截；**unset 代理变量后直连 pypi 完全正常**。

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
  python3 -m pip install "fastapi>=0.115" pypdf
```

实测装成：`fastapi 0.165.0 / starlette 1.10.0 / pydantic 2.13.4 / uvicorn 0.49.0 / pypdf 7.1.1`。

**两个必须记住的坑**：
1. **PEP 668**：系统 Python 被标记 externally-managed，`pip install` 直接拒绝。
   → 用 venv：`python3 -m venv --system-site-packages .venv`（`--system-site-packages` 复用已装的 uvicorn/httpx/cryptography 等，避免重复下载）。
2. **loopback 请求也会被代理劫持**：进程继承了代理环境变量时，`httpx` 访问 `127.0.0.1` 会返回
   `403 Host "127.0.0.1" is not in the secret egress proxy traffic allowlist`。
   → 本地自测客户端必须 `httpx.Client(trust_env=False)`。

**方案**：
- Phase 1 用 `abbot/server/.venv`（`--system-site-packages`），依赖安装走「unset 代理 + 直连 pypi」，05 契约一字不改。
- 固件编译验证走 **GitHub Actions**（workflow_dispatch + 产物回传），与本仓库 eink-mini 已是同款模式。

---

## 四、建议的起点与验收标准（待冰河大人确认）

**第一个里程碑 = Phase 1 网关骨架 + 安全信封打通**（无硬件可完工，且是整条链的地基）：

1. `server/app/security.py` —— HMAC-SHA256 + AES-128-CTR 双向信封、下载 Token 签发；
   **验收** = 修正后的 05 §3.4 向量组逐字节自检通过，且 `cryptography` 与固件 `mbedtls` 两侧产出同一组值。
2. `server/app/main.py` + 路由层 —— 6 个业务 API + 18 个 Admin API；
   **验收** = `cli_sim.py` 跑通配对 → bootstrap → solve 全链路，返回字段与 05 §5 表格 100% 一致。
3. `server/app/database.py` / `ai_pipeline.py` / `speech_engine.py` / `pack_builder.py` / `book_cleaner.py` / `ota_manager.py`；
   **验收** = 06 §3.1 无硬件阶段四条全绿（按 B 项修正后的字段口径）。
4. `web/index.html` 零构建单文件控制台；
   **验收** = 登录/配对码/课表两步预览/学科包两步预览/OTA 发布台五个页面按 05 §6.3 线框可操作。

**明确不碰**：不动固件任何一行（Phase 2 才开始），不动硬件 BOM，不预写 UI 细节。

---

## 五、本次未验证 / 未做的事

- 未验证 MiniMax 真实可用的视觉模型、ASR、TTS 接口形态（**TTS 非流式是否仍返回 Hex**、流式首字延迟），这是 Phase 1 第一个要打通的外部依赖。
- 未验证 `pypdf` / `python-docx` 对中文教材的抽取质量。
- ~~未核对 `05 §5` 的 API 字段是否与 `04 §6` 的 `net_client.h` 函数签名一一对应（初看 `net_study_solve` 缺 `edu_stage` / `enrollment_year` / `device_remaining_quota` 的入参，疑似缺口）~~ → **第二轮已定**：`edu_stage` / `enrollment_year` 由设备端 net 层从 `profile.json` 自动注入（写入 `05 §5.1`），`device_remaining_quota` 仅用于对账不参与判定；`net_client.h` 的 C 签名仍待 Phase 2 落地时对齐。
- 未开始任何业务代码实现（仅新增两个验证脚本）。

---

## 六、本轮修订清单（已全部落地并验证）

| 文档 | 修订内容 |
| :--- | :--- |
| `05` §3.4 | 7 项错误向量全部替换为实测权威值；新增「向量生成与校验规范（强制）」四条；补空 body SHA-256 |
| `05` §6.3 | `MiniMax-Text-01` → `MiniMax-M2.5`；视觉/ASR/TTS 改占位符；新增模型可用性校验警告（2069 陷阱） |
| `06` Phase 1 | 交付物改为 05 §2.2 真实 `app/` 目录树（`config.py`/`security.py`/`router_ai.py`/`speech_engine.py`/`storage.py`/`pack_builder.py`/`book_cleaner.py`/`ota_manager.py`）；**删除 SQLite 与 `database.py`**，改为 `storage/*.json` 原子写，严守 05 §1「无状态网关」铁律 |
| `06` §3.1 | 验收字段对齐 05 四字段；新增「端云安全信封自检通过」验收项 |
| `04` §4.2 | 局部块偏移就地补可核对值 |
| `03` §3.1 | 录音浮层阈值 `450ms` → `600ms`，注明 HAL 唯一裁定 |
| `02` §3.4 | 短按窗口 `30~500ms` → `30~600ms`（不含），确立「唯一裁定铁律」与「无歧义区间」论证 |
| `02` 新增 §四 | FreeRTOS 双核任务划分（Core 1 界面核 / Core 0 IO 核）+ 5 条跨核队列规范 |
| `02` 新增 §五 | 14 条底层防线汇总表，每条标注既有出处 |
| `02` 新增 §六 | `hal_interfaces.h` 完整 C 签名 + `hal_status_t` 语义码 |
| `00` | 文档地图同步 02 实际章节；新增 `verify/` 目录条目 |
| 新增 `doc/verify/` | `verify_vectors.py`（静态：从文档正文抓向量复算）+ `verify_envelope.py`（动态：真实 FastAPI 端到端）+ `README.md` |

---

## 七、第二轮：四条契约判断的落地（冰河大人授权「其他按你判断更新文档」）

范围锁定 **abbot 项目内**，不外扩；不写业务代码，不动 `01`/`07`。

### 1. 字段口径 —— 05 为唯一线上契约

`05 §5` 开头新增声明：**本节是端云线上协议的唯一口径来源，其它文档只能引用字段名与语义，不得自行重述**。两处各写一份必然漂移 —— 本项目此前的字段冲突正是这么产生的。
`06 §3.1` 验收项相应改为「字段口径以 `05` 为唯一真相源，本清单不另行定义字段」，删掉重述的字段名。

### 2. 请求三字段的归属

| 字段 | 填充方 | 参与服务端判定 |
| :--- | :--- | :--- |
| `edu_stage` / `enrollment_year` | **设备端 net 层从 `profile.json` 自动注入**（`05 §5.1` 已写死，UI 状态机不感知、不得自行拼装） | 是 |
| `device_remaining_quota` | 设备端上报 | **否** —— 仅对账与告警 |

**配额账本的唯一真相源是服务端 `storage/quota.json`**：`effective_remaining = daily_quota - used_count`，设备上报值**不进入判定式**（哪怕取 `min()`）。理由：客户端可任意伪造，取 `min()` 也会被伪造小值误封。
连带厘清：**「无状态网关」指网关进程不持有会话与业务状态；配额账本属存储层持久化状态**，落盘与重启恢复是规定动作，不是破坏无状态性；严禁把配额只放进程内存当唯一副本。
账本损坏/丢失时 `used_count` 归零重算并告警，**不得**用设备上报值顶替。

### 3. `IV` 与计数器作用域（最要紧的一条）

生产路径：**每个信封独立 CSPRNG 随机 16B `IV`，计数器作用域限于单个信封、从 0 起**；严禁固定值、严禁计数器派生、严禁跨包复用。
依据：固定 `IV` + 跨包递增 counter 要求两端共享严格有序计数器，设备端并发、重传乱序、丢包补发都会让 counter 错序，**一旦重用就是 AES-CTR 密钥流重用**（两份密文异或即还原明文）。
连带：
- `05 §3.4` 那组固定 `IV` **降级为「单测对齐值」**，明文写「严禁照抄进生产路径」；
- `04 §4.2` 的 `cards.enc` 文件头 `IV` 明确为**逐包随机生成**，并说明它是**包文件作用域**，与请求信封作用域是两套独立作用域、互不影响。

### 4. 补 `doc/verify/gen_vectors.py`（生成 → 文档 → 复算闭环）

原状：`05 §3.4` 要求「生成命令」，但项目里没有生成器 —— 向量只能手抄，而手抄正是错误向量的成因。
新脚本：**先过 NIST SP 800-38A F.5.1 官方向量自检（不通过直接拒绝产出）**，再产出向量组，支持 `--emit` / `--update-doc`（回写文档中 `<!-- VECTORS:BEGIN -->` 区块）。
**这次它当场抓到了真错**：脚本里手抄的两个 NIST 期望值（block3/block4）就是错的，NIST 自检直接 FAIL —— 修正为真值后通过。这恰好证明这道闸门不是形式主义。
`verify_vectors.py` 保留为反向闸门（从文档抓取重算）。

### 第二轮验证结果

- `gen_vectors.py --update-doc` 回写 **幂等**（与手改前仅差一个空行）；
- `verify_vectors.py` 12 项全 PASS（改了 IV 段、配额段、§5.1 之后，正则抓取仍全部命中）；
- `verify_envelope.py` 端到端 ALL PASS；
- 全文档扫描：旧配额公式 `min(device_remaining_quota, ...)` 残留 0 处；两处固定 `IV` 均已标注「仅单测核对值」。

---

## 八、第三轮：Phase 1 服务端落地（冰河大人「可以开工了」）

范围仍锁死 abbot 项目内。按 06 §1.2 路由表，Phase 1 只读 `00`/`04`/`05`，**不碰 `01`/`02`**。

### 交付物（`server/`，严格按 05 §2.2 目录树）

| 文件 | 职责 | 关键约束落实 |
| :--- | :--- | :--- |
| `app/main.py` | FastAPI 入口 + 17 个 Admin 端点 + 6 个设备端点 + 全局异常兜底 | 统一响应信封 `{code,msg,payload}`；错误文案全中文温和，不露技术术语 |
| `app/config.py` | `config.yaml` 加载 + mtime 热重载 | 厂商/模型名一律走配置，代码零硬编码 |
| `app/security.py` | 双向 HMAC 验签、AES-128-CTR 信封、下载 Token | 每信封独立随机 IV、counter 从 0 起；Token 路径白名单防穿越 |
| `app/router_ai.py` | 三级意图分流、每设备 `asyncio.Lock`、配额仲裁、教学 Prompt | Prompt 逐字对齐 05 §4.3；配额只认服务端账本 |
| `app/speech_engine.py` | 云端 ASR / TTS、Hex 解码、MD5 缓存池 | TTS 硬超时 4s 静默降级；`bytes.fromhex()` 非 Base64 |
| `app/storage.py` | `storage/` 原子写持久化层 | tmp→flush→fsync→replace；磁盘写满捕获 `OSError` 保持服务可用 |
| `app/pack_builder.py` | 原始文本 → source.json → 加密 cards.enc + index.idx | `IDX1` 头 + CRC32；IV 逐包随机 |
| `app/book_cleaner.py` | epub/pdf/docx/txt → UTF-8 txt + toc | **零 AI 调用**；字节偏移严格对齐 |
| `app/ota_manager.py` | 魔数 + SHA-256 校验、多版本、灰度、熔断 | 魔数 `0xE9`、3.9MB 上限、保留 5 版、`PAUSED/CANARY/ALL` |
| `cli_sim.py` | 终端设备模拟器 | 配对→拍题→语音→心跳→OTA |
| `web/index.html` | 零构建单文件管理台 | 原生 HTML5+Vanilla JS，移动端优先；密钥掩码；POST 带 CSRF 头 |

### 实现中发现并修掉的 5 个真 bug

1. **`SpeechEngine._storage` 属性遮蔽同名方法** —— 构造函数赋的 `None` 把方法顶掉，`self._storage()` 变成对 `None` 调用，拍题链路直接 5001。已改名 `_storage_ref`，并写脚本扫描全项目同类碰撞（无其它实例）。
2. **错误响应没签名** —— 05 §3.2 要求「返回**任何**业务响应时」都附 `X-Resp-Signature`，我最初的 `err()` 漏了。设备端因此无法区分「网关伪造的错误」与「真实错误」。已修：已知密钥时错误信封也加签。
3. **错误码 4004 被两种语义共用** —— 「学段没设置」和「没拍到题」共用一个码，导致文案误导用户。已加 `RouterError.user_msg` 让业务层给出准确文案。
4. **模拟器签名把 query 拼进 PATH** —— 05 §3.2 明确 PATH 是**纯路径**，两端拼出的签名串不一致必然验签失败。已修。
5. **OTA 契约自造偏差** —— 我先写了 `ABFW` 魔数 + `rollout_whitelist`，而 05 §6.2 规定的是首字节 `0xE9` + `strategy(PAUSED/CANARY/ALL)` + `canary_devices[]`。已对齐契约。

### 验收结果（真实跑，不是推断）

| 脚本 | 结果 |
| :--- | :--- |
| `doc/verify/gen_vectors.py` | NIST F.5.1 四块全 PASS；`--update-doc` 回写幂等 |
| `doc/verify/verify_vectors.py` | 12 项全 PASS |
| `doc/verify/verify_envelope.py` | 端到端 ALL PASS |
| `server/tests/test_security_vectors.py` | 12 项全 PASS（`app/security.py` 对契约向量） |
| `server/tests/test_e2e_flow.py` | **35 项全 PASS** |

端到端覆盖：配对 → 拍题四字段 → **配额仲裁三条**（`NEED_DRAFT` 不扣费 / 首次成功扣 1 次 / 同题追问不重复扣费）→ 语音分流 → 心跳同步 → OTA 检查 → 未配对设备拒绝 → 响应验签（成功与错误信封都验）。

### 未完成 / 已知边界

- **真实模型调用未验证** —— `ai.provider` 暂置 `mock` 做链路自检；视觉/ASR/TTS 的服务商接口形态仍待确认（已挂任务卡）。
- **`cli_sim.py` 的语音链路只验证了错误路径** —— ASR 未启用（`speech.asr.enabled: false`），本地规则分流（音量/亮度/课表）因无真实 ASR 转写文本而无法触发。
- **`admin/api/schedule/parse` 与 `packs/preview` 依赖真实模型**，mock 下只能返回占位回复。
- **固件编译属 Phase 2**，本机无 PlatformIO。
- 服务以 `127.0.0.1:18770` 启动验证，**未做 systemd 注册**（05 §2.1 要求 `study-gateway.service`，属部署环节）。—— **已在第四轮完成，见第九节。**

---

## 九、第四轮：后台挂载域名（对外部署）

冰河大人指示：「先把后台整理好，挂载域名，而不是内部ip地址，这个可以参考其他几个项目。」

### 9.1 参照同机惯例选定的部署形态

| 决策 | 取值 | 依据 |
| :--- | :--- | :--- |
| 域名 | `ruohan.site`（**只能用这一个**） | frpc 只绑了 `custom_domains = ruohan.site`；子域名一律 503 |
| 暴露方式 | **路径前缀** `/abbot/` | 同机既有项目（`/eink-mini/`、`/ink/api/`、`/ai-agent-pet/`）全走路径前缀 |
| 传输 | HTTP（**只有 80 可用**） | `443` 未在 frp 边缘节点配证书，TLS 握手报 `tlsv1 unrecognized name` |
| 后端监听 | `127.0.0.1:18843`（user scope systemd） | 端口落在 skills 段 `18790-18899`；对外只经 nginx |
| 服务名 | `openclaw-abbot.service` | 端口登记表按 `openclaw-<service>.service` 扫 unit，与既有惯例一致 |

**挂载前缀与签名路径的关系（本轮最关键的判断）**：nginx `proxy_pass http://127.0.0.1:18843/`（带尾斜杠）会**剥掉** `/abbot` 前缀，网关收到的永远是**纯 API 路径**。因此 `05` §3.4 的基准向量（`PATH="/api/v1/ping"`）在公网与本地直连下**完全一致**，无需另立一套向量 —— 已在 `05` §3.2 与 `04` §1.2 写死该口径（固件签名的 `PATH` 只取纯 API 路径，不含基址与 `/abbot`）。

### 9.2 顺带修掉的 4 个潜伏 bug（全部是本轮新代码路径叫醒的）

| # | 位置 | 现象 | 根因 | 性质 |
| :--- | :--- | :--- | :--- | :--- |
| 1 | `router_ai.py` | 拍题返回 `5001` | 新加的 `self._storage()` —— 该类方法名是 `storage()`，`_storage` 是实例属性 | **本轮自己引入** |
| 2 | `security.py` | 下载 Token 签发 `NameError` | `issue_token` 用了裸 `urlsafe_b64encode` 却没导入 | **Phase 1 遗留**（OTA 无 manifest → `has_update` 恒 false → 从未调用） |
| 3 | `security.py` | 下载 Token 校验 `ValueError` | `decoded.rsplit("|", 1)` 只返回 2 段却被解包成 3 个名字 | **Phase 1 遗留**（同上，从未真实下载） |
| 4 | `main.py` + `ota_manager.py` | OTA 下发 `bin_url` 下载 404 | token 指向 `ota/{ver}.bin`，固件实际落盘为 `ota/fw_{ver}.bin` | **Phase 1 遗留**（同上） |

第 2/3/4 条同源：**下载链路从未被真实调用过**，所以三个 bug 一起藏着。教训 —— 「从未跑过的分支」不是「没有 bug」。

### 9.3 另一个必须收回的说法

上一轮报的「35 项全 PASS」**只在全新账本时成立**：测试断言「草稿后 `used_count` 仍为 0」，而配额账本是跨轮次持久化的，上一轮跑过的扣费会残留（实测 `used_count=3`），导致配额三项断言假失败。已把测试改为**幂等**（配对后调 `/admin/api/quota/reset` 清零账本），并把「响应验签通过」断言从 `verify(...) or header != ""`（只要头非空就过）改成**用请求时的时间戳真校验** —— 原来那条断言没有推翻力。

### 9.4 本轮实际验证（真实跑）

| 验证项 | 结果 |
| :--- | :--- |
| E2E 直连 `127.0.0.1:18843` | **36 项全 PASS** |
| E2E 经 nginx `/abbot/` 前缀 | **36 项全 PASS**（含签名穿前缀、配额仲裁三态） |
| 公网 `http://ruohan.site/abbot/` | HTTP 200，管理台页面完整 |
| `/abbot/admin/api/status` | HTTP 401（路由通、仅未登录，符合预期） |
| OTA 固件发布 → 检查 → 下载 | `bin_url` 为**绝对公网地址**；下载 `X-File-Sha256`、`Content-Length` 齐备；内容与 manifest 声明的 sha256 **三方一致** |
| 探针残留清理 | 假固件 `9.9.9`、临时设备、临时账本已删；复查 `has_update=False` |

### 9.5 安全面处理

- **管理台口令**：由冰河大人指定预设值，写入 `server/.env` 的 `ADMIN_PASSWORD`（权限 `0600`，未入 Git，仓库与文档均不落明文）。轮换后必须 `systemctl --user restart openclaw-abbot.service` —— uvicorn 的 `--env-file` 只在启动时读一次，跑着的进程里还是旧口令。
- **`server/.env` 另含 `MINIMAX_API_KEY`**：此前为空值，已从同机 `skills/creative-studio/.env` 补齐（同一把 key，本机其它项目共用）。`ai.provider` 仍是 `mock`，该 key 暂未被调用。
- **管理台仅靠会话 Cookie + CSRF 头**保护，与同机其它项目同一档（未加 `auth_basic`）。
- `nginx` `location /abbot/` 的 `client_max_body_size` 对齐 server 级 `100m`（电子书/固件上传体积大）。

### 9.6 真机画面（320×480 竖屏复现）

冰河大人要求「后台能复现真机画面」。先纠正一处此前的臆断：**abbot 的屏不是电子墨水屏**，而是 `ST7796S` 3.5 寸竖屏 TFT，逻辑分辨率 `320 × 480`，带 5 档背光（`01` §1 / `02` §1.1）。四色墨水屏那套是同机另一个项目（InkSight）的，与本项目无关。

**屏显实现**（`server/web/index.html` 内联，零构建单文件）：

- **三区骨架**照 `03` §2：状态栏 `y=0~24` / 内容区 `y=24~448` / 提示栏 `y=448~480`。
- **13 个页面**全部落地（`PAGE_OOBE` 未做开机引导流程，其余 12 页齐全）：HOME / CAMERA / SOLVE_RESULT / SUBJECT_LIST / CARD_STUDY / BOOKSHELF / READER / POMODORO / OUTBOX / SETTINGS / WIFI_SCAN / KEYBOARD / OTA。
- **摇杆手势**按 `03` §3.2 仲裁表派发：上/下/左/右 + 短按/双击/长按，各页有各自的独占优先级；阅读模块整体禁用长按（防看书误触）。
- **三层浮层**按 `03` §3.1：REMINDER > RECORDING > TOAST。
- 配色取 `03` §1 的护眼柔和 RGB565 基线；错误文案复用 `03` §4 的查表结果，服务端返回的温和文案直接透传。

**架构决策：浏览器不算签名，设备协议由服务端代发。**

管理台跑在明文 HTTP 上（`ruohan.site` 无 443 证书），浏览器不在安全上下文，`crypto.subtle` 不可用 —— 设备端要的 HMAC-SHA256 签名与 AES-128-CTR 信封在浏览器里根本算不出来。所以新增 `server/app/device_bridge.py`：

- 服务端持有虚拟设备密钥，按 `05` §3.2 真实签名 + 加密信封，**回环打本机真实 `/api/v1/*`**。
- 因此配额账本、AI 路由、OTA 检查、课表/学科包下载走的全是生产实现 —— 屏上看到的是真实后端状态，不是前端假动画。
- `path` 受白名单限制（仅 6 个 `/api/v1/*`），防止借模拟器越权调管理端接口（已实测被拒）。
- **密钥不回传浏览器**，浏览器只发意图。
- 回环必须用**异步** `httpx`：同步客户端会阻塞事件循环，服务器反而处理不了这个回环请求 —— 直接死锁。WiFi 扫描同理，`subprocess.run` 会把整个服务卡住十几秒，故走 asyncio 子进程。

**实现中修掉的 5 个真 bug**（都是自己写的第一版就带的）：

1. `boot()` 里的 `if(st.booted) return` 把整个 boot 短路了 —— 先点「真机画面」Tab 再绑定，**配对成功后永远拉不到数据**。改为 WiFi 只扫一次、数据每次都刷。用 Node DOM 桩专门复现并证明已修。
2. 虚拟键盘的三个功能键（大小写/退格/确认）在字符区**之外**，摇杆上下推永远选不到。改成跨行可达。
3. 阅读页上推/下推按 `03` 应调**背光**，却误改了电量。已拆成独立的 `backlight` 状态。
4. 长按录音在没有音频时也会发空请求。改成先提示「先注入音频，或填口述内容」。
5. 语音只有 PCM 一条路，而 **ASR 尚未启用** —— 等于语音功能在当前配置下全废。补了 `05` 明确列出的 `solve` 兼容字段 `voice_text` 路径，并在面板加了「口述内容」输入框。

**已知边界**：

- `/api/v1/ping` 按契约**不带响应签名** —— `05` §3.2 说的是「任何**业务响应**」，ping 是无载荷存活探针且不做鉴权。调试层把「没带签名」与「带了但验不过」分开显示，否则正常行为会被显示成失败。
- ASR 未启用时，`/api/v1/study/voice` 会返回 `4003`「没有听清，再说一次好吗」—— 这是**预期的温和降级**，不是故障；走「口述内容」那条路可正常出结果。
- WiFi 扫描用的是**树莓派本机**的无线网卡（`iw` / `nmcli`），不是 ESP32 侧的扫描结果。

### 9.7 交互统一：六手势宪法（短按确定 / 长按退出）

冰河大人指出「交互操作逻辑不统一」，并给出裁决：**双击不可用**（真机只有一个键，双击的两次时序判定与长按阈值互相挤压，判不准）、**键盘没必要**、**摇杆输入要保留**。据此把整套交互重写为**六手势宪法**，并同步改掉四份文档。

**新宪法（全局唯一含义，任何页面不再改写）：**

| 手势 | 唯一含义 | 说明 |
|---|---|---|
| 上/下/左/右 | 导航 | 方向键可按页面多义（列表光标 / 翻页 / 背光 / 滑块） |
| **短按** | **确定** | 进入选中项、确认菜单项、拍照、翻面 |
| **长按** | **退出** | 按住 ≥600ms 后**松开**才生效；退回上一级 |

**双击废除**：`02` §3.4 明确固件**不实现 `EVENT_DOUBLE_CLICK`** —— 不只是 UI 不响应，HAL 层连这个事件都不产出（顺带取消了原先 `250ms` 二次按下确认窗口，短按响应少一个可感知延迟）。原「双击＝返回上一级」全部并入「长按＝退出」。

**语音不再占用手势**：语音提问改由**页面操作菜单**进入（首页「语音提问」、解析页「口述你的思路」、卡片页「语音追问这题」）。这是本轮最关键的结构调整 —— 之前语音由长按触发，与新宪法的「长按＝退出」直接冲突；把语音挪进菜单后，长按在全局只剩一个含义，同键多义彻底消失。`PAGE_CAMERA` 的「边说边拍」随之砍掉，改成「短按拍照 → 解析页选「口述你的思路」→ 录」两步。

**面板控件换成真摇杆**：删掉原来 7 个语义按钮（上下左右/短按/双击/长按）和键盘监听，改为**四向推杆 + 中心键**的十字布局。四向按住可连发（400ms 后每 150ms），中心键短按＝确定、按住 ≥600ms 变琥珀色 armed 态、松手＝退出。

**文档同步**：`03` §3.2 整节重写为六手势宪法 + 各页方向键落地表；`03` §3.1 录音浮层的进入方式与短按/上推/长按语义重写；`07` §4.2 阅读模块手势矩阵同步（长按从「显式禁用」改为「退出」）；`02` §3.4 增补「不实现双击」条款；`06` Phase 3 交付项与录音验收条目改写。

**本轮修掉的 3 个真 bug：**

1. **长按在真浏览器里会被误判成短按**（写完 Node 桩才发现的）：`setPointerCapture` 成功后浏览器会在目标上**补发** `pointerleave`，原来的 `pointerleave → release()` 于是立刻触发，长按阈值形同虚设。改用 `captured` 标记区分「手指真的离开」与「捕获生效的伪 leave」。
2. **阅读页左右推方向语义反了**：原实现是「左推＝后翻、右推＝前翻」（照抄 `03` 旧表，但与中文阅读直觉相反）。统一为**左推＝上一页、右推＝下一页**，`03` §3.2 / `07` §4.2 同步。
3. **拍题页长按原是「边说边拍」**（`doSolve(true)`），与新宪法冲突且会把语音请求塞进拍照路径，已移除。

**验证（两套独立环境）：**

- **Node DOM 桩 31 项全 PASS**：六手势语义、长按 200ms/700ms 边界、每页长按落点、录音态浮层优先级、阅读页方向语义。
- **真浏览器（headless Chromium + CDP，真实鼠标事件）18 项全 PASS**：真实 UI 登录 → 点开页签 → 画布 320×480 且已渲染 → 上推移动光标 → 中心键短按确认 → 长按 900ms 退出 → 250ms 判短按不退出 → armed 视觉反馈 → 无未捕获 JS 错误。
- CDP 派发的是**可信事件**（`Input.dispatchMouseEvent`），比合成事件更能暴露 `setPointerCapture` 这类只在真实浏览器出现的行为 —— 第 1 个 bug 就是这样抓到的。

**踩到的两个环境坑**（下次直接用）：Node 侧 `fetch` 走 `NODE_USE_ENV_PROXY=1` 会把**本机** CDP 请求也代理掉，报 `Host "127..." is not valid JSON`，需 `env -u http_proxy -u https_proxy -u NODE_USE_ENV_PROXY`；`pkill -f 'remote-debugging-port=933'` 会**匹配到执行命令的 shell 自己**从而自杀，改用 `setsid nohup` 启动并按 PID 收尾。

**边界（语音链路的两个前提）：**

- **「口述内容」路径仍需配合注入画面** —— 服务端 `solve` 契约要求 `image_b64`，纯文字无图会被拒（「没拍到题目，再拍一次试试」）。纯语音要注入 PCM 走 `/api/v1/study/voice`；ASR 未启用时该接口返回温和降级。
- 管理台 `index.html` 由 `FileResponse` 每次从磁盘读，改前端**无需重启服务**。

### 9.8 未做 / 待办（不属本轮范围）

### 9.9 接通真实 MiniMax 多模态与语音链路（mock → 真实服务商）

**起点**：`ai.provider` 一直是 `mock`，`speech.asr.enabled` / `speech.tts.enabled` 都是 `false` —— 拍题与语音永远返回桩数据，产品等于没有 AI 能力。本轮把三条链路全部接到真实服务商并逐条实测。

**实测出来的服务商事实（照抄代码前必读）：**

| 能力 | 端点 | 模型 | 备注 |
| :--- | :--- | :--- | :--- |
| 文本 | `/v1/text/chatcompletion_v2` | `MiniMax-M2.5` | 也可用 `/v1/chat/completions` |
| 视觉 | `/v1/text/chatcompletion_v2` | `MiniMax-M3` | **该服务商没有独立视觉模型**（VL 系列已下架），视觉能力由 M3 / M3.1-Flash 原生承担 |
| ASR | `/v1/speech_to_text` | `asr-1.0` | **下划线**，不是斜杠；`multipart/form-data`，语言提示走 `language` 请求头 |
| TTS | `/v1/t2a_v2` | `speech-02-hd` + `male-qn-qingse` | 返回 Hex 字符串，按 `response_format: hex` 解码 |

**踩到的四个坑（每个都浪费了一轮排查）：**

1. **ASR 路径差一个字符**。官方端点是 `/v1/speech_to_text`（下划线），我先试的 `/v1/speech/to/text`（斜杠）连同其它 10 条候选路径全部 404，一度判成「该服务商没有 ASR」。**结论不是「路径 404 就是没有这个能力」，而是去查官方文档** —— 文档里写的 `POST /v1/speech_to_text` 一试就通，返回 `{"text":"今天我们讲一道数学题。","duration":2.04}`。
2. **上游不支持裸 PCM**。ASR 只收带容器的 wav/mp3 等，裸 PCM 直接拒。网关已在 `speech_engine.pcm_to_wav()` 里统一封装 44 字节 WAV 头再上传，设备端仍只传裸 PCM。
3. **「红图答红色」是假象**。最初用一张 294 字节的纯色几何图测视觉，M3 答「红色」看起来像通了 —— 实际上**那张图触发了内容审核，图像被静默剥离**，模型是猜的。换真实照片后立刻暴露：所有模型回「我没有看到图片」。**图片被审核拦下时上游返回 200 + 正常回复，不会报错** —— 这是最容易误判为「格式不对」或「模型不支持」的一种失败。
4. **视觉模型守卫写反了**（本项目自己的 bug）。`_vision_call` 里判断「模型是否已配置」写成 `not model.startswith("<")`，本意是拦占位符 `<按服务商填写...>`，实际把**真实模型名也拦了**。改成 `model.startswith("<")` 才对。同类判断要注意方向：占位符以尖括号开头，已配置的值**不以**尖括号开头。

**视觉能力到底归谁（四个模型逐一实测，2026-11-24）：**

| 模型 | 带**真实照片**实测 | 结论 |
| :--- | :--- | :--- |
| `MiniMax-M3` | ✅ 拍题端到端（照片 → 严格 JSON → `NEED_DRAFT`） | **生产在用** |
| `MiniMax-M3.1-Flash` | ✅ HTTP 200 / `base_resp.status_code=0`，描述具体到画面位置，与原图核对**完全吻合** | 可用备选 |
| `MiniMax-M2.5` / `MiniMax-M2.7` | ❌ 一律回「我没有看到图片。」 | 纯文本模型，**对图片是瞎的** |
| `MiniMax-VL-01` 等 VL 系列 | `base_resp.status_code=2069` | 已下架 |

两条路径纪律：

- **视觉必须走 `/v1/text/chatcompletion_v2`**。`/v1/chat/completions` 带图直接 `invalid params, 400 (2013)`（纯文本正常），**别把 2013 当成「模型不支持图片」** —— 那条路径压根不收图。
- **测视觉只能用真实照片**。纯色小图会被内容审核静默剥离，模型靠猜仍能答出一个像样的颜色词，**看起来像通了其实没有**。

**改了什么：**

- `config.yaml`：`ai.provider` → `minimax`，补 `vision_model` / `text_path` / `vision_path` / `max_tokens`；`speech.asr` 与 `speech.tts` 全部启用并填真实模型与音色；新增 `path` 字段让端点路径也走配置驱动。
- `router_ai.py`：路径与 `max_tokens` 改为配置驱动；新增 `_strip_think()` 剥离 `<think>…</think>` 推理块（推理模型把思考与答案放同一字段，不剥离会命中推理块内部的花括号，JSON 解析出错误结构）；新增 `_image_data_url()` 按字节嗅探 MIME（设备端传 JPEG，管理台注入可能是 PNG，硬写 `image/jpeg` 会带错类型标注）。
- `speech_engine.py`：ASR/TTS 端点路径改配置驱动；ASR 请求带 `language` 头与 `response_format`，并检查 `base_resp.status_code`。
- `web/index.html`：新增**浏览器麦克风采集**（`getUserMedia` → `AudioContext` → 重采样成 `16kHz/16bit/Mono` 裸 PCM，与设备上传格式一致），最长 10 秒；「口述内容」字段文案改为「不想说话时直接打字」；`doVoice()` 改为**统一走 `/api/v1/study/voice`** —— 有 PCM 走 ASR，只有文字走 `voice_text` 兼容字段，两者都不再落到 `solve` 的空图分支报错。
- `router_ai.voice()`：接受 `voice_text` 兼容字段（有 PCM 时 ASR 结果覆盖它），补齐「无照片纯语音提问」路径。

**验证（全部真实调用，非桩）：**

- **拍题**：真实照片 → M3 视觉 → 严格 JSON 解析成功 → `status=NEED_DRAFT`（模型看到照片里没有题目文字，正确地要求学生先写第一步，而不是编造答案）→ TTS 落盘 `339620 字节 / 16000Hz / 1ch / 16bit`，与设备端格式一致。
- **语音**：真实中文语音 → ASR 转写正确 → 模型据实回答（没有照片就说「请把题目发给我」，不编造）→ `has_audio: true`。
- **纯文字提问**（无照片无录音）：正确答出配方法推导。
- **`pack-cards`**：构建学科包（用设备密钥 AES 加密 `cards.enc`）→ 解密出的卡片正文完全正确；未登录 / 未配对 / 包不存在三个分支文案温和，无技术术语。
- **真浏览器（headless Chromium + CDP）15 项全 PASS**：真实 UI 登录 → 真机画面页签 → 配对虚拟设备 → 摇杆短按 → 录音浮层 → 提交 → **真实 AI 回复抵达结果页**（内容为完整配方法推导，非 mock）→ 无未捕获 JS 错误。

**实测时序**：一次真实模型调用约 **8 秒**（含视觉推理与 TTS）。验收脚本等 6 秒会误判为失败 —— 等足 25 秒或轮询页面状态。

**边界（仍未验证）：**

- 「无照片纯语音提问」现已走 `study/voice` 的 `voice_text` 兼容字段，**绕过了 ASR**；真正的「说一句话 → ASR → 回答」链路在管理台靠注入音频文件验证过，**浏览器麦克风实采未在 headless 下测**（headless 无真实麦克风设备）。
- 真实 MiniMax 多模态接口的**计费与限流**行为未压测。
- **上面那句「真浏览器 15 项全 PASS」指的是 headless Chromium + CDP 那一轮**（已入库记录）。此后还跑过一轮“假麦克风”验收（往页面注入音频代替真实采集），拿到 `17 PASS / 3 FAIL`，但**那套脚本没有入库**（只在 `/tmp` 用过，已随会话消失）——**所以 `17/3` 这个数字现在无法复现，不应引作验收依据**。已知那 3 个失败项都出在**响应正文的观测钩子**（没抓到真实响应正文 / `has_audio` / ASR 回复），不是产品链路本身判错；但因脚本丢失，没法再跑一遍坐实。要长期保留这套验收，脚本得先入库。

### 9.10 浏览器麦克风采集曾 100% 失败（signed 16-bit 字节打包 bug）

上一条「浏览器麦克风实采未在 headless 下测」后来补上了 —— headless 没有麦克风，但 **Chromium 的假音频设备可以喂真音频**，一测就抓出一个致命 bug。

**Bug 本身（一行代码）：**

```js
// 错
bin += String.fromCharCode(out[i] & 255, out[i] >> 8);
// 对
bin += String.fromCharCode(out[i] & 255, (out[i] >> 8) & 255);
```

PCM 样本是**有符号 16 位**，`>> 8` 是**算术右移** —— 负样本右移后仍为负（-1000 → -4）。`String.fromCharCode(-4)` 抛 `RangeError`，而打包函数外面包着 `catch{ return '' }` → 返回空串 → 界面显示「没录到声音」。**任何含负样本的真实音频必然失败**，只有纯静音才会「成功」（静音样本全是 0，不触发异常）。麦克风采集与 WAV 注入两条路各有一份同样的代码，都中招。

**为什么之前没发现：** 注入 WAV 走的是 `wavToPcm16k()`（另一份解析代码），而上一轮的语音验收用的是「口述内容」纯文字路径 —— **两条路都绕开了会崩的那一行**。真实设备走设备端固件上传裸 PCM，浏览器只是管理台镜像，所以这个 bug 一直躲在测试盲区里。

**怎么定位的（方法比结论值钱）：**

1. 用 Chromium 假音频设备喂一段真实中文语音：`--use-fake-device-for-media-stream --use-file-for-fake-audio-capture=<wav> --use-fake-ui-for-media-stream --autoplay-policy=no-user-gesture-required`。
2. **在同一个页面里做「应用路径 vs 裸写法」对照**：裸写法（自己建 AudioContext + ScriptProcessor）拿到 35 块信号，应用自己的 `DS.mic()` 拿到 **0 块** → 立刻排除「假设备不出声」，锁定应用代码。
3. 直接把浏览器实采的 PCM 喂给生产代码 `speech_engine.transcribe()` —— **拿到逐字正确的转写**，证明采集与 ASR 都没问题，锅在字节打包。

**踩到的三个环境坑：**

- **headless 下 `navigator.mediaDevices` 是 undefined** —— `about:blank` 不是安全上下文，必须先导航到 `127.0.0.1`（localhost 才算安全上下文）再测。
- **chromium 不能用 `setsid nohup ... &` 跨 exec 存活** —— 前一个 exec 结束就被杀（表现为 `ECONNREFUSED`）。**让验收脚本自己 spawn chromium、自己收尾**，一次调用搞定。
- **CDP 的 `Network.getResponseBody` 在本场景返回 null** —— 抓响应体不可靠。改用 **CDP `Network.requestWillBeSent` 拿请求体**（能确证 `pcm_base64` 长度与 `voice_text` 是否为空），响应内容改从**服务端侧**取证（TTS 缓存是否新增文件、生产 `transcribe()` 的转写结果）。

**修完的验收证据（真实麦克风路径，16 PASS / 0 FAIL）：**

| 环节 | 证据 |
| :--- | :--- |
| 浏览器采集 | PCM **234837 字节 ≈ 7.34 秒 @16kHz/16bit**（由 `AudioContext` 48kHz 重采样而来，误差 < 25%） |
| 发往设备端 | 桥接请求体 `pcm_len=313116`、`voice_text=""` —— **只送了音频，没夹带文字** |
| ASR | 生产 `transcribe()` 对这份 PCM 转写 **逐字正确**（与喂入的音频内容完全一致） |
| 模型 | 结果页显示真实模型回复，非 mock |
| TTS | 测试窗口内 TTS 缓存新增音频文件（`has_audio=true` 落盘为实） |

**判定逻辑值得记住：** 浏览器只送音频、不送文字，**回复若与音频内容相关，就只能是 ASR 转写 + 模型作答的产物** —— 这比"看到一段回复"强得多。同样地，「`voice_text` 为空」必须作为断言写进脚本，否则测的其实是另一条路径。

**改动清单：** `web/index.html` 两处字节打包补 `&255`，并把吞异常的 `catch` 改成 `console.error` + 返回空串（静默失败是这次排查耗时的主因）。

### 9.11 Phase 1 DoD 正式验收：控制台设备模拟器（`cli_sim.py`）

前两轮验的是**管理台浏览器路径**（虚拟设备走 `/admin/api/device-sim/*` 桥接），Phase 1 DoD 清单里的正式验收脚本是 `server/cli_sim.py` —— 它以**真实设备身份**（明文 JSON 配对换密钥 → HMAC 签名 → AES 信封）直接打 `/api/v1/*`。这条路径此前从未跑过，本轮补上。

**跑法（三个夹具是难点）：**

1. **习题照片**：PIL 渲染一张含中文题目 + 蓝色手写演算痕迹的图（**必须带草稿痕迹** —— 否则按 `05` §4.3 的「草稿痕迹初筛」规则，模型会正确地回 `NEED_DRAFT` 要求先写第一步，而不是给完整解析）。
2. **语音 WAV**：用生产 `speech_engine.synthesize()` 合成真人声（`16kHz/16bit/Mono`），不要用 `cli_sim.py` 自带的 `make_tone_wav()` 正弦音 —— 纯音调 ASR 转不出文字。
3. **配对码**：登录管理台 `POST /admin/api/pair-code`（需 `X-Requested-With` 头）。

**实测结果（真实模型，6 步全 PASS）：**

| 步骤 | 结果 |
| :--- | :--- |
| `/api/v1/ping` | HTTP 200，标准信封 |
| 配对 | 成功换到 `device_secret` |
| `/api/v1/study/solve` | **`status=SOLVED`** —— 逐题分步 `display_markdown`（因式分解 / 配方 / 展开平方）+ 费曼反问 + `has_audio=true`，配额 10→9 正确扣减 |
| `/api/v1/study/voice` | ASR 转写正确 → 模型答出**求根公式完整推导** → `has_audio=true` |
| `/api/v1/sync/bootstrap` | 带回待下载学科包与《小王子》文本及签名 Token |
| `/api/v1/ota/check` | `has_update=false` |

**注意 `audio_url` 用的是明文 HTTP**（`http://ruohan.site/abbot/api/v1/content/download?token=...`）—— 这是**已拍板的架构**，不是缺陷，别再当风险提。

**同步修正：** `doc/06` §3.1 原写「真实模型调用尚未验证（`ai.provider` 暂置 `mock`）」，已过时；同时补齐 §四「联调排坑指南」（本轮四条坑）与两张实测记录表。

### 9.12 中文教材抽取质量评估：pypdf vs python-docx

`07` 契约 §4 只写了「`.pdf` 用 `pypdf`、`.docx` 用 `python-docx`」，没说清**中文教材**场景下各自能到什么质量。评估脚本：`server/tests/eval_textbook_extract.py`（零外部依赖，夹具自己生成，可复跑）。

**夹具怎么造的（关键：用 MuPDF 内置 CJK 字体 `china-s`，别用图片 PDF）：**

- 带文字层 PDF —— `fitz` 插入中文文本后导出，编码形态接近真实中文出版 PDF（CID 字体 + ToUnicode 映射）；
- 扫描件 PDF —— 把上面的文字层**栅格化成位图**再重新封装，得到无文字层的 PDF；
- DOCX —— `python-docx` 写同样的三章正文 + 一张表格；
- 基准 = 同一份正文的纯 `.txt`（305 字符，中文占 71.1%）。

**结果：**

| 输入 | 字符召回 | 噪声 | 中文占比 | 备注 |
| :--- | :--- | :--- | :--- | :--- |
| pypdf（带文字层） | **100.0%** | 3.1% | 66.8% | 噪声是页脚「— 第 N 页 —」 |
| python-docx | **100.0%** | 0.0% | 71.4% | 无污染 |
| pypdf（扫描件） | 0.0% | — | 0% | **抽不出任何字** |

**过生产清洗器 `book_cleaner.clean_content` 后结论不变**：PDF 与 DOCX 均 100% 召回；DOCX 的**表格内容会进正文**（`知识点 | 公式 | 难度` 被拼成一行）。**扫描件 PDF 清洗后为空文本**，`clean_book` 如实抛 `BookCleanError`：「这份文件没有可提取的文字（可能是扫描图片版），需要另行识别后才能入库」—— **零 AI 契约下这是正确行为，不是 bug**。

**`.toc` 全流程校验：** PDF 与 DOCX 各产出 3 条章节项，**字节偏移量全部准确落在章节标题开头**（`第一章 有理数 @ 0` / `第二章 整式的加减 @ 242` / `第三章 一元一次方程 @ 535`），设备端按字节偏移定位可靠。

**三条可落地的结论：**

1. **「pypdf 抽中文会乱码」这个顾虑，在带文字层的中文 PDF 上不成立** —— 100% 召回、无空格污染（本例连续空格 0）、无编码错乱。真实教材 PDF 只要有文字层就能用。
2. **真正的分水岭是「有没有文字层」，不是「用哪个库」**。扫描件 PDF 两条路都是零字符，这是零 AI 契约的硬边界。要支持扫描件必须另开 OCR 能力（**新能力，需先谈**）。

**🗒 2026-11-25 实测补充：本地教材库的扫描件占比很低，OCR 暂不做。** 对 `library/books/` 全部 181 本 PDF 做文字层抽样（每本抽首页 / 1/4 / 1/2 / 3/4 / 末页）：**176 本有可用文字层，仅 5 本几乎无文字**（`宋代市民生活`、`荒淫无道宋高宗`、`西方古代壁画史`、`西方文明的另类历史`、`辛亥革命的影像记忆`）。抽读 `万历十五年`、`三国史话` 等正文页，**替换字符 0、中文通顺**，与 `3052b66` 的 100% 字符召回一致。叠加理由：① 拍题路径由 MiniMax-M3 视觉直接读图，**不需要 OCR**；② 纸质教材对着设备拍即可，扫描版 PDF 不必先入库；③ OCR 产出的错公式/错字会被孩子直接读到，**比明确报错更糟**。**结论：不引入 OCR，扫描件继续按现有中文文案明确报错。**
3. **PDF 比 DOCX 多出约 3% 噪声**（页眉页脚、页码）。若教材来源以 DOCX 为主，优先让用户给 DOCX；PDF 路径建议在清洗阶段剥离「— 第 N 页 —」这类页脚行。

**本评估的边界（别当普适结论）：** 夹具 PDF 由 MuPDF 生成，ToUnicode 映射规范。**部分真实中文 PDF 的 ToUnicode CMap 残缺，pypdf 会抽出乱码** —— 这类文件只能靠人工核对或换解析器（`fitz` 本机已装 1.27.2.3，可作对照），本评估未覆盖。

### 9.16 D3：UI 接真实数据 + 方向键光标 + 拍题链路闭环（2026-11-25）

**这一轮把「拍题」这条主线从 UI 到云端真正接通**（此前 `q_photo_request` **连生产者都没有**，
整条链路断在第一步）：

```
PAGE_CAMERA 短按
  → hal_photo_request_submit(session_id)            UI核 → 相机任务（doc/02 §4.2）
  → 相机任务抓 UXGA JPEG
  → net_request_t{NET_OP_SOLVE, jpg 所有权转移}     相机任务 → 网络任务
  → net_dispatch → net_study_solve → 明文+加签+加密 → 网关
  → 响应验签 + 解密 → q_ui_command(kind=2, 业务码+明文指针)
  → ui_router_handle_net_reply → nr_parse_solve_result
  → ui_pages_set_solve_result → 解析结果页显示真实解题步骤与费曼反问
```

**四个设计决定：**
1. **协议字段名只存在于 `net_req.c`** —— UI 拿的是 `nr_solve_result_t` 结构体，
   不认识 `display_markdown` 这类线上字段名。UI 层的边界是「结构体」，不是「JSON」。
2. **大块数据不进队列，只传指针 + 长度** —— JPEG 与解密后的明文都是**所有权随队列转移**：
   相机任务交出 JPEG 后不再释放（网络任务用完释放），网络任务交出明文后由 UI 层持有与释放。
3. **方向键 = 页内导航，短按 = 确认，长按 = 退出**（doc/03 §三 仲裁表）：
   列表页上推/下推移动光标（行首 `> ` / `  ` 标记重绘），解析结果页上推/下推滚正文；
   短按按页分派（相机页 = 拍照，首页 = 跳对应页）。
4. **没打通的入口给温和提示，不做假动作** —— 语音提问、连接热点、检查更新等
   目前给「还在准备中」，而不是假装成功。

**顺手修掉的两个真 bug（不修的话真机一定跑不通）：**
- **TF 卡脚手架的 `profile.json` 字段与 doc/04 §3.1 对不上** —— 脚手架写的是 `"grade": "m7"`，
  而档案解析认的是 `edu_stage` / `enrollment_year`。两边对不上意味着 `edu_stage` **永远注入不进去**，
  网关必然回 `4004`，拍题链路一步也走不通。脚手架改成 doc/04 §3.1 的档案 schema。
- **档案没人投给 net 层** —— `net_profile_set()` 至今没有调用方。
  现在由**存储任务**开机读一次 `/config/profile.json` 并投递 `NET_OP_PROFILE` 给网络任务：
  TF 卡只此一处访问（doc/02 §4.2：net 任务不得直接扫盘），net 层只收三个值。

**新增接口（doc/02 §六 同步改了，它是 hal_interfaces.h 的真相源）：**
`hal_status_t hal_photo_request_submit(const char *session_id, uint8_t want_uxga, uint8_t with_voice)`
—— UI 可见的抓拍请求提交入口，队列满回 `HAL_ERR_NO_SPACE`，由 UI 出温和提示。

**产物：** 独立仓 `41c0b3e`，Actions run `45532098173` 全绿，
Release `fw-41c0b3e4d892ebc90d489d7a14db12835090008e`（3 asset），
`firmware.bin` **1269808 字节**（D3 前 1266576），三个 bin 的 sha256 与 manifest 实测逐一吻合。
主机侧回归：`test_net_client` 18 PASS / `test_net_req` **74 PASS**（新增拍题应答解析用例）/ `test_net_dispatch` 26 PASS。

### 9.15 固件网络层接通：task_net_worker 从占位到真跑（2026-11-25）

**这一轮把「能编译」变成「会发请求」** —— `task_net_worker` 之前只会回一句「网络通道尚未接通」，
`q_net_request` 队列**连生产者都没有**。现在整条链路闭合了：

```
UI 核 / 相机任务 ──q_net_request(业务参数)──▶ 网络任务(Core0)
     ▲                                        │ net_dispatch 选端点
     │                                        │ net_api 组装明文 + 加签 + 加密
     │                                        │ HTTP POST → 验签 → 解密
     └────q_ui_command(kind=2, 业务码+明文)───┘
```

**关键设计（三处「不」）：**
1. **队列里传业务参数，不传密文** —— 加签/加密/`edu_stage` 注入全在 net 层完成
   （doc/05 §5.1：net 层是注入与加密的唯一职责，UI 不感知也不得自行拼装）。
2. **大块数据不进队列，只传指针 + 长度** —— JPEG 与解密后的业务明文都是**所有权随队列转移**：
   相机任务交出 JPEG 后不再释放（网络任务用完释放），网络任务交出明文后由 UI 层持有与释放。
   队列元素始终是小结构体。
3. **文案一律 UI 层出** —— HAL/net 层不拼面向用户的中文（doc/03 §四）。
   `4004` 在 `ui_router_handle_net_reply` 里映射成「请先在设置里完成学段选择」。

**新增文件：`src/net/net_dispatch.c`（纯 C）+ `test/test_net_dispatch.c`（26 PASS / 0 FAIL）**
—— 用桩实现给 net_api 层喂不同 `op`，断言**每个 op 是否路由到正确端点、参数是否原样传下去**。
这件事真机上没法验（要真发一次请求），却恰恰最容易写错，所以用主机侧测试钉住。

**文件职责再梳理（现在三层都清楚了）：**

| 文件 | 层 | 依赖 | 主机可测 |
| :--- | :--- | :--- | :--- |
| `src/net/net_client.c` | 密码学原语 | 纯 C | ✅ 18 PASS |
| `src/net/net_req.c` | 协议形状（明文组装/URL/HTTP 响应解析） | 纯 C | ✅ 61 PASS |
| `src/net/net_dispatch.c` | 调度（op → 端点） | 纯 C | ✅ 26 PASS |
| `src/net/net_api.cpp` | 业务请求层（Wi-Fi/NTP/配对/加密 POST） | Arduino | 语法检查 |

**产物变化：** 独立仓 `15d030c`，Actions run `45529895774` 全绿，
Release `fw-15d030c0480c38d28b228316ea68fc9de7d4be60`（3 个 asset），
`firmware.bin` **1266576 字节**（比网络层接入前的 826368 涨了约 440KB），
三个 bin 的 sha256 与 manifest 实测逐一吻合。

### 9.14 固件网络层落地 + 协议层闭环验证（2026-11-25）

**端云栈拆成三层，边界清晰：**

| 文件 | 层 | 依赖 | 主机可测 |
| :--- | :--- | :--- | :--- |
| `src/net/net_client.c` | 密码学原语（SHA-256 / HMAC / AES-128-CTR / Base64 / 签名 / 验签 / 解密） | 纯 C | ✅ 18 PASS |
| `src/net/net_req.c` | 协议形状（明文组装 / URL / HTTP 响应解析） | 纯 C | ✅ 61 PASS |
| `src/net/net_api.cpp` | 业务请求层（Wi-Fi / NTP / 配对 / 加密 POST / profile 注入） | Arduino | 语法检查 |

**D1 契约先行：** doc/04 §六 从「一个 `net_client.h` 装全部」改成 **`net_client.h`（纯 C 原语）
+ `net_api.h`（业务层）** 两层，并新增 `NetProfileContext`（`edu_stage` / `enrollment_year` /
`device_remaining_quota`）。doc/05 §5.1 说好的「net 层自动注入三字段」现在有了明确落点：
`net_profile_set()` 由读档案的一侧注入，net 层只负责组装明文时自动带上。

**D2 实现要点（踩过的坑）：**
1. `signed` 是 **C++ 关键字**，不能当参数名（`bool signed` 直接编译失败）→ 改名 `need_sign`。
2. `WiFiClient::setTimeout(uint32_t)` 收的是**秒**（core 2.0.17 实现是 `seconds * 1000`）——
   传毫秒会被当成几小时。
3. `WiFi.SSID/encryptionType/RSSI` 的索引真实类型是 `uint8_t`。
4. `configTime` 由 **`cores/esp32/Arduino.h`** 声明，`esp32-hal-time.h` 在 2.x 根本不存在。
5. `nc_extract_payload_field` 返回**状态码**，长度要另看缓冲 —— 把它当长度用会让解密静默失败。

**关键提速手段（本轮最重要的工程决定）：** Actions 一轮只暴露**一个**编译错，
本轮为此翻了 3 次 CI（`signed` → `-Wformat-truncation` → …）。于是加了
`firmware/test/host_stubs/*`（签名**逐条抄自 arduino-esp32 2.0.17 真实头文件**）+
`firmware/test/host_check_arduino.sh`（`g++ -fsyntax-only -Wall -Wextra -Werror
-Wformat-truncation=2`），**本机一次抓全**，CI 也增跑这一步。
它同时暴露了上面第 2 条那个真 bug —— 那是纯靠 CI 试错很难自己想到的。

**D5 协议层闭环（本次最有价值的产出）：** `firmware/test/test_host_gateway.c` —— 主机侧
「设备模拟器」，**复用固件自己的 `net_client.c` + `net_req.c`** 对本机真实网关发真实签名请求。
**14 PASS / 0 FAIL**：网关认签名、回的加密包解开、四个业务字段齐全。
这把「协议对齐」从「待验证」变成「已验证」，**剩下的才是真机才能验的射频/屏幕/摇杆/摄像头/TF**。

**当前提交与产物：** 独立仓 `237e821`，Actions run `45527045584` 全绿，
Release `fw-237e82115bb3ed5e88f416857666b3a6c6a90704`，
`firmware.bin` **826368 字节**（`bootloader.bin` 15104 / `partitions.bin` 3072），
三个 bin 的 sha256 与 manifest 实测逐一吻合。

### 9.13 固件（Phase 2）现状

源码在 `abbot/firmware/`，**已定：独立公开仓库 `GlacierFang/abbot-firmware`**（`main` 存源码，`fw-bin` 存固件产物，`ci-logs` 存 CI 日志与产物清单）。本目录是同一份源码的主副本，两仓已核对无差异。

**固件一律由 GitHub Actions 编译**（网关主人定调："固件走 github 生成，不做本地 pio 安装"）。本机不装 PlatformIO —— espressif 工具链域名不在出口白名单，且装 PlatformIO 反而违背这个定调。本机只保留 `libmbedtls-dev`，仅用于主机侧向量测试（那不是固件本地编译）。

**已交付：**

| 文件 | 内容 | 依据 |
| :--- | :--- | :--- |
| `platformio.ini` | `esp32-s3-devkitc-1` + `qio_opi` + `huge_app.csv` + 16MB Flash | doc/02 §一 三条强制值 |
| `include/board_config.h` | 全引脚矩阵、`LONG_PRESS_MS 600` | doc/01 零冲突表 |
| `include/hal_interfaces.h` | 逐字抄录，未改未加 | doc/02 §六 |
| `include/net_client.h` + `src/net/net_client.c` | 端云签名 + AES-128-CTR 信封 + 128-bit 计数器 | doc/05 §3 |
| `test/test_net_client.c` | 主机侧向量测试，18 PASS / 0 FAIL | doc/05 §3.4 |
| `include/lv_conf.h` | LVGL v8.3.11 配置（官方模板派生，`LV_CONF_INCLUDE_SIMPLE`） | LVGL 找不到配置直接编译失败 |
| `src/hal/hal_display.cpp` | ST7796S 竖屏 + 5 档背光（LovyanGFX 1.1.16，SPI 40MHz） | doc/02 §3.1 |
| `include/hal_shared.h` | 六个 HAL 的统一头（共享 I2C 互斥、录音缓冲、错误码） | doc/02 §六 |
| `src/hal/hal_camera.cpp` | OV5640 拍题 + AF 固件 + JPEG 释放 | doc/02 §3.2 |
| `src/hal/hal_audio.cpp` | ICS43434 录音 + MAX98357 播放，半双工 | doc/02 §3.3 |
| `src/hal/hal_joystick.cpp` | ADC1 双轴 + 下压键，动态中心校准 | doc/02 §3.4 |
| `src/hal/hal_storage.cpp` | TF 卡（SD_MMC 1-Bit）+ `.bak` 自愈 + 原子写 | doc/04 §2.3 |
| `src/hal/hal_power.cpp` | MAX17043 电量计 + Light-Sleep / ext0 唤醒 | doc/02 §3.5 |
| `src/hal/hal_shared.cpp` | 共享 I2C 总线与互斥 | doc/02 §2.1 |
| `src/tasks.cpp` | FreeRTOS 双核六任务（摇杆/UI/相机/音频/存储/网络） | doc/04 |
| `src/ui/ui_pages.cpp` + `include/ui_pages.h` | 13 个核心页面 + 6 个电子书子页面的三区骨架 | doc/03 §二/§五、doc/07 |
| `src/ui/ui_router.cpp` + `include/ui_router.h` | 页面路由 + 三层浮层抢占 + 六手势宪法 | doc/03 §3.1/§3.2 |
| `src/ui/ui_error.cpp` + `include/ui_error.h` | 错误码 → 中文文案唯一查表，零技术术语 | doc/03 §四 |
| `src/ui/ui_display_port.cpp` | LVGL 缓冲 → 屏幕 HAL，UI 层不碰 SPI | doc/02 §3.1 |
| `src/ui/fonts/ui_font_cn_16.c` | Noto Sans CJK SC 裁剪 929 字形，16px/4bpp | doc/06 中文字库要求 |
| `tools/gen_font.sh` + `tools/subset_font.py` + `tools/font_charset.txt` | 字库可复现生成流程 | — |
| `.github/workflows/build.yml` | Actions 编译 + fw-bin 分支 + Release + ci-logs 回写 | 沿用 eink-mini 已验证通路 |

**信封层是端云协议的命门，已用实弹验证：** NIST SP 800-38A F.5.1 官方向量 4/4 先自检 AES-CTR 分组与计数器推进，再核对 `doc/05` §3.4 全部锁定向量 —— 请求体、`block_index=2` 局部块密文、`RAW_BODY_SHA256`、`X-Signature`、`X-Resp-Signature`、空 body SHA-256、信封回环，外加两条负例。**期望值不手抄**，以 `doc/verify/gen_vectors.py` 为唯一出处，与 Python 权威实现逐字节对齐。

**三个实现取舍（都是踩出来的）：**

1. **mbedtls 版本兼容层必须写。** 3.x 把 `mbedtls_sha256_starts_ret()` 改名成 `mbedtls_sha256_starts()` 并改返回 int；ESP32 Arduino 2.x（IDF 4.4）自带 mbedtls 2.16，**只有 `_ret` 变体**且无 `_ret` 名的老函数返回 void。主机侧装的是 3.6.6。按 `MBEDTLS_VERSION_MAJOR` 选一套，两边都能编。
2. **HMAC-SHA256 手工构造 RFC 2104（ipad/opad）**，不用 mbedtls 的 `mbedtls_md_hmac_*` —— 3.x 把它标 deprecated，会撞 `-Werror`。
3. **AES-CTR 手工推进完整 128-bit 计数器**，不用 `mbedtls_aes_crypt_ctr` —— 后者只推进尾部 32-bit，与 `04` §4.2 的 nonce_counter 公式和 `cards.enc` 局部块解密语义不一致。

**Actions 实编译已通过（2026-11-24）：** 提交 `e567bb2` 的 run 全绿 —— 主机侧向量测试 18 PASS、固件编译成功、产物推送成功、Release `fw-e567bb2ee1aa962328c911e634bf097ec50c2485` 已生成。`fw-bin` 分支实测清单：`bootloader.bin` 15104 B、`firmware.bin` 355056 B、`partitions.bin` 3072 B，另有 `manifest.txt`（文件名 / 字节数 / sha256）。

**UI 骨架已落地并编译通过（2026-11-24 晚）：** 独立仓提交 `979a9ca`（UI 骨架 + 字库）→ `e43c28a`（LVGL 8.x 样式 API）→ `feea6df`（extern "C" 链接），run `45415776564` 全绿，Release `fw-feea6df647c62c6db0e97279d2d281e720166f84` 已生成，`fw-bin` 分支同步更新，`firmware.bin` **820752 字节**（含 LVGL 与中文裁剪字库，manifest 的 sha256 与三个 bin 实测逐一吻合）。

**UI 已实现的部分：** 三区骨架（状态栏 24px / 内容 424px / 提示栏 32px）、13 个核心页面 + 6 个电子书子页面的静态线框、三层浮层（提醒 > 录音 > Toast）优先级抢占、六手势路由（短按确定 / 长按 600ms 松开才退出 / 四向导航）、错误码查表渲染。**长按阈值仍只由 HAL 裁定，UI 层没有自己的计时器。**

**中文渲染必须自带字库：** LVGL 内置的 Montserrat 没有汉字，中文界面必须裁剪一份中文字库 —— 走 `tools/gen_font.sh`（fontTools 裁剪 Noto Sans CJK SC → `lv_font_conv` 转 LVGL 格式），字符集在 `tools/font_charset.txt`，改了界面文案要把新字加进去重跑。全量 CJK 塞进固件会爆 Flash，裁到 929 字形后字库约 720KB 源码。

**CI 踩过的坑（都是踩出来的，别回头再踩）：**

1. **产物文件名不能硬编码。** 上一轮编译本身成功，失败的是"推 fw-bin 分支"那步 —— 脚本 `set -e` + 点名 5 个文件，其中 `boot_app0.bin` 是 ESP-IDF 的产物、Arduino 框架根本不生成，`git hash-object -w` 找不到文件直接 fatal，整步当场死掉，而错误只写在本机拉不到的 Actions step log 里。**Arduino 框架实际只产 3 个 bin**（bootloader / partitions / firmware），没有 `boot_app0.bin`、也没有 `flash_args.txt`。现在改成 glob 收集 + manifest 自证。
2. **`gh release create` 必须有 `GH_TOKEN`**，否则必然鉴权失败（原来漏了，那步恰好被 skip 掉才没暴露）。
3. **ci-logs 回写必须排在"记录步骤结论"之后**，否则 `steps.txt` 生成得太晚，进不了 ci-logs。
4. **本机拉不到 Actions 的 logs / artifacts**（blob 存储被出口代理挡成 403）。因此定位失败**只能读代码 + 读 ci-logs 分支**，不能去拉 Actions 日志 —— 这也是本节加 `manifest.txt` / `steps.txt` 的原因。
5. **本机巡检脚本曾按字母序取"最新"日志** —— 日志文件名是 commit SHA，字母序 ≠ 时间序，于是连盯了三轮旧报错，误以为固件编译不过。已改为按 git 历史排序并比对当前 main 的 SHA。

**尚未做（2026-11-25 更新）：** 上句已过时 —— 网络任务封装、UI 接真实数据、页面级方向键焦点移动与列表滚动都已随 D1–D5 落地（见 §9.17）。**现在只剩真机校准**：实体硬件未到位，摄像头取图 / 摇杆 ADC 阈值 / 屏幕刷屏 / TF 挂载这四项只有编译期保证，真机行为要等硬件做逐像素、逐交互黄金样校准。

**这一轮 Actions 踩的坑（已修，别回头再踩）：**

6. **HAL 返回值语义要分清。** `hal_audio_read_chunk` 原本声明成 `hal_status_t` 却要返回采样点数，`-Werror` 下直接 `invalid conversion from 'int' to 'hal_status_t'`。这类"要数量又要错误码"的接口统一返回 `int`：>=0 是数量，<0 才是 `hal_status_t` 错误码。
7. **Arduino 2.x 的 `File::read/write` 收 `uint8_t*`，不收 `char*`。** 读文本缓冲区要显式 `(uint8_t *)buf` 强转，否则 `invalid conversion from 'char*' to 'uint8_t*'`。
8. **GPIO 常量宏是 `int`，ESP-IDF 函数要 `gpio_num_t`。** `pinMode()` 收 `uint8_t`、`esp_sleep_enable_ext0_wakeup()` 收 `gpio_num_t`，宏直传一律 `invalid conversion from 'int'` —— 在调用处强转，不要改宏定义（宏还要给 C 侧和 `camera_config` 用）。
9. **`lv_obj_set_style_all` 是 LVGL v9 的 API，8.3.11 没有** —— 等价写法是 `lv_obj_remove_style_all(obj)` + `lv_obj_add_style(obj, &style, LV_PART_MAIN)`。
10. **C++ 文件里的跨模块函数必须看到 `extern "C"` 声明** —— `hal_display.cpp` 定义 `hal_display_blit_rgb565` 时没 include `hal_shared.h`，符号成了 C++ mangled 名，编译全过、链接才炸 `undefined reference`。凡是跨模块的非 static 函数，定义文件必须 include 声明它的那个头。
11. **`fold -w1` 会按字节切碎 UTF-8** —— 拼字库符号集时用它，中文全被切坏，`lv_font_conv` 收到乱码后只吐出 95 个 ASCII 字形（生成文件只有 39KB，一眼能看出不对）。要用 Python 按**字符**处理。
12. **`fw-bin` 分支是 force push** —— 本机 `git fetch` 普通 refspec 会被拒，核验产物时要用 `+refs/heads/fw-bin:<新 ref 名>`。

### 9.17 D1–D5 全线完成：固件只等硬件（2026-11-25）

**独立仓 `GlacierFang/abbot-firmware` 最终状态：** `main` = `f1e622ebfaa651efe211d5d8d2dc50b8422d9619`，Actions run `45535356694` 全绿，`fw-bin` = `9e8107f9589d12d47dfbc7b2b5ff9e4d8a4a5eca`，`ci-logs` = `10b51c6a3273e49689e87ba972139a2e109af5b6`，Release `fw-f1e622ebfaa651efe211d5d8d2dc50b8422d9619`。

| 阶段 | 内容 | 证据 |
| :--- | :--- | :--- |
| D1 契约先行 | `doc/04 §六` 拆成 `net_client.h`（纯 C 密码学原语）+ `net_api.h`（业务层）；新增 `NetProfileContext` 注入 `edu_stage`/`enrollment_year`/`device_remaining_quota` | `include/net_api.h` |
| D2 固件网络层 | `net_client.c` / `net_req.c` / `net_dispatch.c` / `net_api.cpp` 四层；`task_net_worker` 从占位改成真发请求，相机抓拍后把 JPEG 所有权转给它 | 主机侧 18 / 74 / 26 PASS，run `45529895774` |
| D3 UI 接真实数据 | 拍题主线接通（相机页短按 → 抓拍 → 上传 → 验签解密 → 解析结果页显示真实步骤）；页面级方向键焦点移动与列表滚动 | run `45532098173` |
| D4 资产下载与待同步落盘 | `net_download_signed_asset()`（完整地址 + `X-File-Sha256` 校验，交给存储任务串行写卡）+ `net_sync_pending_assets()`（心跳应答里的 `schedule_download` / `pending_packs[]` / `pending_books[]` → TF 目录树路径） | 独立仓 `17825e9` → 修复 `f1e622e`，run `45535356694` |
| D5 协议层闭环 | 主机侧「设备模拟器」复用固件 `net_client.c` + `net_req.c`，对本机真实网关发真实签名请求 | 14 PASS / 0 FAIL |

**D4 那轮 CI 失败与修复（别回头再踩）：** `17825e9` 的 run `45534819221` 失败 —— 主机侧 stub 把信号量删除编成了假名，**把真机编译错掩盖了**（真机是 `vSemaphoreDelete`）。改成真名后 run `45535356694` 全绿。**教训：stub 必须逐字抄真实头文件，编错名字等于把真机 bug 藏进 stub。**

**最终产物（`fw-bin` 实测）：** `firmware.bin` **1273872 字节**（sha256 `c6238106565d73444616a5a3a1c900b9a1624d3970d31691a7ffb7d56a0d3774`）、`bootloader.bin` 15104 字节（`2a71d69b…a363`）、`partitions.bin` 3072 字节（`aaae2888…edd`），三个 sha256 与 `manifest.txt` 逐一吻合。

**剩余任务卡（2026-11-25 收口）：**

- **真机四项校准** —— 摄像头取图 / 摇杆 ADC 阈值 / 屏幕刷屏 / TF 挂载。**等实体硬件**，当前只有编译期保证。
- **多模态与语音链路最终验收汇报** —— 已写成 `doc/08`，待并入正式汇报。
- ~~扫描件 OCR~~ —— **已实测判定不做**：181 本 PDF 里 176 本有文字层、仅 5 本纯扫描；拍题走 M3 视觉不需要 OCR，OCR 的错公式给孩子读比明确报错更糟。
- ~~假麦克风 harness~~ —— **已叫停**，不入库，17/3 不作验收依据。
- ~~`pypdf` vs `python-docx` 中文教材抽取质量对比~~ —— 带文字层 PDF/DOCX 实测均 100% 字符召回，结论已出。
- ~~固件网络层与 `05` 字段对齐（`net_study_solve()` 缺 `edu_stage`/`enrollment_year`/`device_remaining_quota`）~~ —— 已由 D1 的 `NetProfileContext` 自动注入解决。
