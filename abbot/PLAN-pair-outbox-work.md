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

## 块 B 设计（离线队列 outbox）

**位置**：`/outbox/queue.json`（索引）+ `/outbox/task_<ts>.<jpg|pcm>`（关联媒体）。
hal_storage 的空白卡自愈已建 `/outbox/` 目录并写好 `{"tasks": []}` 初始索引。

**索引格式**：按 **doc/04 §3.5 的既有契约**（字段名 `task_id`/`type`/`status`/`retry_count`/
`created_at`/`hint_level`/`image_path`/`pcm_path`），另加 `session_id`/`context` 两个补传必需字段。
10 条满载约 2.2KB —— **放不进 `storage_req_t.text` 的 1KB**，所以新增
`STORAGE_OP_WRITE_TEXT_BUF = 12`（数据取自调用方缓冲），队列项本身不放大。

**谁持有队列**：新增纯 C 模块 `src/net/outbox.c` + `include/outbox.h`（主机侧可单测）。
**不直接碰 TF 卡** —— 与 `ui_data.c` 一样走 `hal_storage_req_queue()` 请求/应答，
守住「网络任务不得扫 TF 卡」这条硬约束。

**上限 10 条**：超出时淘汰最旧的一条（并删掉它的关联媒体文件），保证队列不撑爆卡。

**原子写 + .bak 自愈**：写走 op=12（存储任务做 `.tmp → .bak → 重命名`）；
读时正式文件缺失由 `hal_storage_read_text` 自动回退 `.bak`；
正式文件**内容损坏**时本模块自己回退 `.bak` 并删掉坏文件。

**重试上限与过期策略**（必须写进 doc，不能只写在代码里）：
- 重试上限 **5 次**（`OUTBOX_MAX_RETRY`）；超过则丢弃条目并删关联文件。
- 过期 **72 小时**（`OUTBOX_MAX_AGE_SEC`）；超期条目在补传时丢弃并删关联文件。
- 时钟不可信（未校时）时**不做过期判定**，否则一开机就会把队列全清掉。

**入队时机**：拍照/语音这类需要云端的请求，断网或未配对时**入队**而不是失败。
未配对时也允许入队（配对完成后一并补传）。

**补传**：联网 + 已配对后逐条补传，成功则移除索引条目并删除关联 JPG/PCM。

**已完成的代码改动（块 B）**
- [x] `include/outbox.h` + `src/net/outbox.c`（纯 C，主机侧可单测）
- [x] 队列索引读写（走存储队列，≤10 条，原子写 + .bak 自愈）
- [x] 入队 / 出队 / 重试计数 / 过期清理 / 媒体路径生成
- [x] `include/storage_io.h` + `src/tasks.cpp`：新增 `STORAGE_OP_WRITE_TEXT_BUF = 12`
- [x] `include/net_api.h` + `src/net/net_api.cpp`：`net_outbox_enqueue_solve()` / `net_outbox_flush()`
      / `net_outbox_pending()` / `net_media_cleanup()`
- [x] `include/net_api.h`：`net_request_t.media_path[72]`（相机任务把媒体路径带过来）
- [x] `src/net/net_dispatch.c`：拍题**成功 -> 删媒体文件**；**传输失败 -> 入队**（业务错不入队）
- [x] `src/tasks.cpp`：相机任务带上 `media_path`；网络队列满时也入队；
      网络任务在**业务请求队列空时**才补传（避免饿死业务请求）
- [x] `test/test_outbox.c`：**93 checks, 0 failures**
- [x] `test/test_net_dispatch.c`：加 `net_outbox_enqueue_solve` / `net_media_cleanup` 桩 +
      「拍题失败->入队 / 拍题成功->删文件」两条路由断言
- [ ] 编译 + 全量主机测试通过
- [ ] doc/04 §3.5 补齐重试上限与过期策略
- [ ] CI 里加跑 outbox 测试
- [ ] 提交 + 推

**实现过程中修掉的 3 个自己的 bug**
1. `outbox_parse` 找不到 `tasks` 数组 —— 通用取值函数碰到 `[` 会当标量值停住，
   补了 `json_find_array()` 专门定位配对的 `]`。
2. 序列化时 `pcm_path`/`session_id` **漏写键名**（只写了值）→ 解析回来是空的。
   重构成 `json_put_str_field()`（键 + 值成对写），根治这类漏。
3. `status` 字段长度比较写成 7（"retry" 是 5）→ retry 态解析不出来。

## 任务清单
### A. 配对简化 ✅（已推、CI 绿、产物自核通过）
### B. 离线队列 outbox（进行中，代码已写完，待编译验证 + 文档 + 提交）
### C. 学科包 cards.enc 解密 + 卡片学习页
- [ ] 固件解 `cards.enc`（AES-128-CTR，key = `device_secret[0:16]`）
- [ ] 卡片学习页：正面题干 → 短按翻面 → 长按退出
- [ ] 解密失败给温和中文提示
- [ ] 提交 + 推
