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

- 新端点 `POST /api/v1/device/register`（明文 JSON，设备还没密钥时无法签名）；
  应答沿用 `/api/v1/pair` 信封形状，固件侧复用同一段解析，零新增解析分支。
- 幂等：网关 `storage.register_device()` 命中已有设备返**同一份**密钥。
- 密钥绝不回传浏览器；管理台只看状态与配额。
- 旧 6 位配对码路径保留为兜底，默认不暴露。
- 固件：OOBE 改「自动接入进度页」，接入成功自动跳首页（零输入）；
  `ui_command_t.kind=5` + `UI_PAIR_STATE_*`；自动登记失败每 15 秒重试。
- 网关：`app/storage.py` `register_device()`；`app/main.py` 新端点 + `/admin/api/status` 加状态；
  `web/index.html` 设备表加「状态」列。
- 文档：doc/04 §1.2 改写；doc/05 §3.2/§5/§6.2；doc/03 §五 OOBE + §十 设置页。

**验证**：`test_net_req` 114 / `test_net_dispatch` 29 / `test_ui_data` 69 checks /
`test_host_gateway`（真实网关）14 PASS / `host_check_arduino.sh` 通过 / charset 无缺字。
网关实测：连续三次登记 → 同一份密钥；经 nginx `/abbot/` 200；非法输入 → 4001 + 温和中文。

**提交**：固件 `0f119db`（已推）。**CI run 45572846664 = success**。
**产物自核**（fetch fw-bin 亲自验）：bootloader 15104 B / firmware 1287056 B / partitions 3072 B，
sha256 与 manifest.txt **逐一吻合**。

## 块 B：离线队列 outbox —— ✅ 已完成并推送

- 索引按 **doc/04 §3.5 既有契约**（`task_id`/`type`/`status`/`retry_count`/`created_at`/
  `hint_level`/`image_path`/`pcm_path`）+ 补传必需的 `session_id`/`context`。
  10 条满载约 2.2KB —— 放不进 `storage_req_t.text` 的 1KB，故新增
  `STORAGE_OP_WRITE_TEXT_BUF = 12`（数据取自调用方缓冲）。
- 新增纯 C 模块 `include/outbox.h` + `src/net/outbox.c`：走 `hal_storage_req_queue()`，
  不碰 SPI、不直接调 `hal_storage_*`。
- 策略（同步写进 doc/04 §3.5）：重试上限 **5 次**；过期 **72 小时**；
  时钟未校准**不做过期判定**；队列上限 **10 条**，满了淘汰最旧一条并删关联文件；
  只有**传输失败**才入队（网关业务错不入队）；补传**每次只补一条**且**只在业务请求队列空闲时**。
- 接线：相机任务把 `media_path` 带给网络任务；拍题成功 → 删媒体文件；传输失败 → 入队；
  网络队列满时也入队。

**验证**：`test_net_client` 18 / `test_net_req` 114 / `test_net_dispatch` **35** /
`test_ui_data` 69 checks / `test_outbox` **93 checks** —— 全部 0 failures。
`test_outbox` 是新建的主机侧单测（假存储任务）。

**提交**：固件 `0027c11`（已推）。**CI run 45577886523 = success**。
**产物自核**：bootloader 15104 B / firmware **1291632 B** / partitions 3072 B，
sha256 与 manifest.txt **逐一吻合**。

## 块 C：学科包 cards.enc 解密 + 卡片学习页 —— 代码完成并推送

**包格式**（与服务端 `pack_builder.py` 逐字核对）：
`cards.enc` = `IV(16B) || AES-128-CTR(明文 JSONL)`；密钥 `device_secret[0:16]`（`key_type=device`）
或固定演示密钥（`key_type=demo`）；`index.idx` = `"IDX1"` + 版本 + 张数 + CRC32 + 偏移表。

**新增模块**：`include/cards.h` + `src/net/cards.c`（纯 C，主机侧可单测）
- `cards_parse_index()`：校验魔数 / 版本 / CRC32 / 偏移下界（`< 16` 判损坏）。
- `cards_decrypt_range()`：按 doc/04 §4.2 公式做**局部块解密**
  （`block_index` / `byte_in_block` / `nonce_counter = BigEndian128(IV_base) + block_index`）。
- `cards_parse_item()`：解析单卡 JSON（`\n` `\"` `\\` 与 `\uXXXX` 转义）。

**分层**：解密只在**网络任务**做（`net_cards_load()` → `cards_load()`）——
设备密钥只存 NVS，UI 层不碰密钥、不碰加密；解出的卡片经 `q_ui_command`（kind=6）投回 Core 1。
读卡仍走存储任务队列。

**交互**：学科包列表读卡上 `/subjects` 下的**真实**已下载包（不再是写死的假数据）；
翻卡页 = 上下移菜单 / 左右换一张（回到正面）/ 短按翻面 / 长按退出返回列表；
解密失败给温和中文（不出现加密、密钥等词）。

**验证**：`test_cards` **60 checks, 0 failures** —— 含 **doc/05 §3.4 官方锁定向量**
（`block_index=2` 处明文 `{"ping":1}` ↔ 密文 `41da37cc48e5aa3f8d06`）与
`cards_load` 端到端（假存储任务喂真包进去）。其余五组测试仍全绿。

**顺带修掉的服务端真 bug**：`pack_builder.py` 的 `DEMO_KEY = b"STUDY_DEMO_KEY_16"` 是 **17 字节**，
而 AES-128 要恰好 16 字节 —— `key_type="demo"` 的打包会直接抛
`ValueError: Invalid key size (136) for AES`。仓库里唯一的包是 `key_type=device`
（长度天然正确），所以这条路一直没被走过。已改为 `b"STUDY_DEMO_KEY16"`（恰好 16 字节），
固件侧常量同步，doc/04 与 doc/05 的密钥名同步。

**文档同步**：doc/04 §2.2（删掉「空白卡预置 demo_pack」的不实说法 + 写明学科包由网关心跳下发）、
§3.3（密钥名）、§四（补设备端实现落点与层级说明）；doc/05 §6.2（密钥名）；
doc/03 §五 第 4/5 节（学科包列表与翻卡页写实，标明掌握进度与语音追问未实现）。

**提交**：固件 `0a848ed`（已推 `0027c11..0a848ed`），CI run **45580001210**（运行中）。
网关 + 文档：workspace 本地 commit `f33f46b`（不推 inksight 远端）。

## 实现过程中修掉的自己的 bug（累计 6 个）
1. `outbox_parse` 找不到 `tasks` 数组 —— 通用取值函数碰到 `[` 会当标量值停住 → 补 `json_find_array()`。
2. 序列化时 `pcm_path`/`session_id` **漏写键名**（只写值）→ 重构成 `json_put_str_field()`。
3. `status` 字段长度比较写成 7（"retry" 是 5）→ retry 态解析不出来。
4. 测试桩 `g_last_code[8]` 装不下媒体路径 → 另开 `g_last_media[72]`。
5. **`cards_decrypt_range` 密文摆错位置** —— 摆在 `block[0]` 会让块尾用错密钥流
   （本块最后一个字节解成垃圾）→ 改摆 `block[byte_in_block]`。
6. `s_paired` 真相源搞错 —— 读的是 TF 卡 `profile.json` 的 `paired` 字段，
   而固件从不写那个字段 → 改为取「profile.json 标志」与「NVS 密钥已在手」的**或**。

## 明确的没做
- 多网关地址可配；OTA apply 与 app1 双分区；真机校准；真实签名联调（无真机时端到端签名验证靠主机侧测试覆盖）。
- 学科包**掌握进度百分比**未实现（`/records/progress.json` 没接线）。
- **语音追问这题**仍未接通（UI 只给温和提示）。
- 学科包**列表**已改成读真实目录，但设备端 manifest（`/subjects/manifest.json`）的写入
  仍未接线 —— 包是网关心跳下发时落盘的，列表靠列目录发现，不依赖 manifest。
- 离线队列只支持 `SOLVE` 类型的补传；`VOICE` 类型的条目字段齐备但补传路径未实现
  （语音链路整体尚未接通）。
- 卡片页的「语音追问」菜单项、掌握进度卡片等按 doc 保留但未接线。
