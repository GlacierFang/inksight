# abbot 本块工作日志（配对简化 / outbox / 学科卡解密）

> 2026-11-25 起。三件事分三次提交，每块单独同步+提交+推。

## 硬约束速记

- 固件只由 GitHub Actions 编译；本机禁 PlatformIO，只允许 gcc 主机侧测试。
- 交互宪法：四向导航 + 短按确定 + 长按 600ms 松开退出；长按阈值只由 HAL 裁定；保留摇杆；语音只能从菜单进入。
- UI 只经 `hal_storage_*` 读卡；UI 不碰 SPI、不碰网络、不碰加密。
- **网络任务不得扫 TF 卡**（doc/02 §4.2：TF 卡由 task_storage 独占串行化）。
- 错误文案不得出现 AES/HTTP/signature/Token/Python/Traceback。
- abbot 外网保持明文 HTTP。
- 签名格式：`METHOD\nPATH\nX-Timestamp\nRAW_BODY_SHA256`（PATH 不含域名、query、`/abbot` 前缀）。
- 只改 abbot 范围文件；`.env`/密钥/缓存/测试数据不入库。
- 新增中文文案必须同步加进 `tools/font_charset.txt` 并重生成字库（`tools/gen_font.sh`）。
- 连续只读调用 ≤3 次，读写比 ≤2:1。

## 块 A：配对简化（自动登记，无码）—— ✅ 已完成并推送

**设计**
- 新端点 `POST /api/v1/device/register`：明文 JSON（此时设备无密钥、无法签名）。
  应答沿用 `/api/v1/pair` 信封形状 → `payload` 明文 JSON 串含 `device_secret`（64 hex），
  固件侧与 `net_pair_with_code()` 复用同一段解析，零新增解析分支。
- 幂等：网关 `storage.register_device()` —— 命中已有设备返**同一份**密钥（不重签），未命中才新签。
- 密钥绝不回传浏览器；管理台只看状态与配额。
- 旧 6 位配对码路径保留为兜底，默认不暴露。

**固件改动**
- `include/net_req.h`：`NR_PATH_REGISTER` + `nr_build_register_plain()` 声明。
- `src/net/net_req.c`：`nr_build_register_plain()`（有/无 fw_version、空 id 三态）。
- `include/net_api.h`：`net_auto_register()` / `net_is_paired()` + `NET_OP_REGISTER=6`。
- `src/net/net_api.cpp`：抽出 `net_store_secret_from_plain_reply()` / `net_post_plain()`；
  新增 `net_auto_register()` / `net_is_paired()`。
- `src/net/net_dispatch.c`：`NET_OP_REGISTER`。
- `include/hal_shared.h`：`ui_command_t.kind=5` + `UI_PAIR_STATE_*`（NONE/PENDING/READY/FAILED）。
- `src/tasks.cpp`：上电未配对即自动登记；失败每 15 秒重试（放主循环里，不占住请求队列）。
- `src/ui/ui_pages.cpp` + `include/ui_pages.h`：OOBE 改「自动接入进度页」+ `ui_pages_set_pair_state()`。
- `src/ui/ui_router.cpp` + `include/ui_router.h`：`ui_router_set_pair_state()`，READY → 自动跳首页。
- `tools/font_charset.txt` + `src/ui/fonts/ui_font_cn_16.c`：新增「儿」「尝」，重生成（936 字形 / 739620 字节）。
- `test/test_net_req.c` / `test/test_net_dispatch.c`：新增断言；补上漏掉的 `net_sync_pending_assets` 桩
  （CI 的 `set +e` 把链接失败吞掉了，那测试从没真跑过）。
- `.github/workflows/build.yml`：主机侧测试 `set +e` → `set -e`。

**网关改动**
- `app/storage.py`：`register_device()` 幂等签发。
- `app/main.py`：`POST /api/v1/device/register`（校验 device_id 格式）；`/admin/api/status` 加 `status`/`registered_at`。
- `web/index.html`：设备表加「状态」列。

**文档同步**
- `doc/04` §1.2 改写为「设备自动登记握手鉴权（无码；6 位码降级为兜底）」+ 真相源是 NVS。
- `doc/05` §3.2 签名例外、§5 端云 API 表、§6.2 status/pair-code。
- `doc/03` §五 OOBE 进度页线框与流程、§十 设置页第 2 项标注兜底。

**验证**
- 主机侧：`test_net_req` 114 PASS / `test_net_dispatch` 29 PASS / `test_ui_data` 69 checks /
  `test_host_gateway`（对真实网关）14 PASS / `host_check_arduino.sh` 通过 / `check_charset.py` 无缺字。
- 网关实测：重启后连续三次登记 → 同一份密钥（registered 首轮 true、后两轮 false）；
  经本机 nginx `/abbot/` 前缀 200；非法 device_id / 缺字段 / 非法 JSON → 4001 + 温和中文。
- 用固件自己的密码学代码 + 登记到的密钥打真实网关：签名/信封/验签/解密全通。
- 测试设备已从 `devices.json` 清掉。

**提交与推送**
- 固件 commit `0f119db`，已推 `1a8b77b..0f119db`。
- **CI run 45572846664 = success**（2m42s）。
- **产物自核**（fetch `fw-bin` 亲自验，不只信 CI 日志）：
  - bootloader.bin 15104 B / sha256 `2a71d69b…ca363`
  - firmware.bin 1287056 B / sha256 `053f90ef…577d`
  - partitions.bin 3072 B / sha256 `aaae2888…3edd`
  - 三者字节数与 sha256 与 `manifest.txt` **逐一吻合**。
- 网关 + 文档：workspace 本地 commit `cfede8a`（不推 inksight 远端，按要求只做本地提交）。

## 块 B：离线队列 outbox —— ✅ 代码完成并推送

**位置**：`/outbox/queue.json`（索引）+ `/outbox/task_<ts>.<jpg|pcm>`（关联媒体）。

**索引格式**：按 **doc/04 §3.5 的既有契约**（`task_id`/`type`/`status`/`retry_count`/
`created_at`/`hint_level`/`image_path`/`pcm_path`），另加 `session_id`/`context` 两个补传必需字段。
10 条满载约 2.2KB —— **放不进 `storage_req_t.text` 的 1KB**，所以新增
`STORAGE_OP_WRITE_TEXT_BUF = 12`（数据取自调用方缓冲），队列项本身不放大。

**谁持有队列**：新增纯 C 模块 `src/net/outbox.c` + `include/outbox.h`（主机侧可单测）。
**不直接碰 TF 卡** —— 与 `ui_data.c` 一样走 `hal_storage_req_queue()` 请求/应答。

**策略（同步写进 doc/04 §3.5）**
- 重试上限 **5 次**；过期 **72 小时**；时钟未校准时**不做过期判定**。
- 队列上限 **10 条**，满了淘汰最旧一条并删它的关联文件。
- 只有**传输失败**才入队；网关业务错（如 4004）不入队。
- 补传**每次只补一条**，且**只在业务请求队列空闲时**才补（一次拍题往返含 AI 推理可能十几秒，
  不能把业务请求饿死）。

**代码改动**
- `include/outbox.h` + `src/net/outbox.c`：解析 / 序列化 / 入队淘汰 / 出队 / 过期判定 /
  走存储队列的读写 / 媒体文件删读。
- `include/storage_io.h` + `src/tasks.cpp`：新增 `STORAGE_OP_WRITE_TEXT_BUF = 12`。
- `include/net_api.h` + `src/net/net_api.cpp`：`net_outbox_enqueue_solve()` / `net_outbox_flush()` /
  `net_outbox_pending()` / `net_media_cleanup()`；`net_request_t.media_path[72]`。
- `src/net/net_dispatch.c`：拍题成功 → 删媒体文件；传输失败 → 入队。
- `src/tasks.cpp`：相机任务带上 `media_path`；网络队列满时也入队；空闲时补传。
- `test/test_outbox.c`（新建）、`test/test_net_dispatch.c`（桩 + 路由断言）。
- `.github/workflows/build.yml`：加跑 outbox 测试。

**主机侧验证（全绿）**
- `test_net_client` 18 PASS / `test_net_req` 114 PASS / `test_net_dispatch` **35 PASS** /
  `test_ui_data` 69 checks / `test_outbox` **93 checks** / `host_check_arduino.sh` 通过。

**提交**
- 固件 commit `0027c11`，已推 `0f119db..0027c11`；CI run **45577886523**（待确认）。
- 文档：workspace 本地 commit `6c900c4`（doc/04 §3.5 补齐重试上限与过期策略）。

**实现过程中修掉的 4 个自己的 bug**
1. `outbox_parse` 找不到 `tasks` 数组 —— 通用取值函数碰到 `[` 会当标量值停住，
   补了 `json_find_array()` 专门定位配对的 `]`。
2. 序列化时 `pcm_path`/`session_id` **漏写键名**（只写了值）→ 解析回来是空的。
   重构成 `json_put_str_field()`（键 + 值成对写），根治这类漏。
3. `status` 字段长度比较写成 7（"retry" 是 5）→ retry 态解析不出来。
4. 测试桩里 `g_last_code[8]` 装不下媒体路径（被截断）→ 另开 `g_last_media[72]`。

## 块 C 设计（学科包 cards.enc 解密 + 卡片学习页）—— 待写

**包格式（已核对服务端实现）**
- `cards.enc` = `IV(16B) || AES-128-CTR(明文 JSONL 流)`；
  密钥 = `device_secret[0:16]`（`key_type=device`）或固定 `STUDY_DEMO_KEY_16`（`key_type=demo`）。
- 明文 JSONL 每行一张卡：`{"id","question","answer","hint1","hint2","analysis"}`。
- `index.idx` = `"IDX1"` + `uint16 version(1)` + `uint16 N` + `uint32 crc32` + N × `uint32 偏移`。
- doc/04 §4.2 的局部解密公式：`payload_offset = start_offset - 16`；
  `block_index = payload_offset / 16`；`byte_in_block = payload_offset % 16`；
  `nonce_counter = BigEndian128(IV_base) + block_index`（固件已有 `nc_add_counter`）。

**分层**
- 解密归**网络任务**（密钥只在 net 层，UI 不碰加密）；卡片内容经 `q_ui_command`
  投回 Core 1 渲染。
- 读卡仍走存储任务队列（网络任务不得直接扫 TF 卡）。

**卡片学习页**
- 正面题干 → 短按翻面看答案 → 长按退出（长按阈值只由 HAL 裁定）。

## 任务清单
### A. 配对简化 ✅（已推、CI 绿、产物自核通过）
### B. 离线队列 outbox ✅（已推，CI 待确认）
### C. 学科包 cards.enc 解密 + 卡片学习页（下一步）
- [ ] `include/cards.h` + `src/net/cards.c`：读 index.idx / cards.enc（走存储队列）
- [ ] AES-128-CTR 局部解密 + JSONL 解析
- [ ] net 层：`net_cards_load(pack_id, out)`（用 NVS 里的密钥）
- [ ] UI：卡片学习页渲染题干/答案，短按翻面、长按退出
- [ ] 解密失败给温和中文提示
- [ ] doc/04 §四 / doc/03 卡片页同步
- [ ] 提交 + 推
