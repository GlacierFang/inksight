# abbot 本块工作日志（配对简化 / outbox / 学科卡解密）

> 2026-11-25 收工。三件事各一次提交，每块单独同步 + 提交 + 推。

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

---

## 块 A：配对简化（自动登记，无码）—— ✅

**设计**：新端点 `POST /api/v1/device/register`（明文 JSON，设备还没密钥时无法签名）；
应答沿用 `/api/v1/pair` 信封形状 → `payload` 明文 JSON 串含 `device_secret`（64 hex），
固件侧与 `net_pair_with_code()` 复用同一段解析，零新增解析分支。
幂等：网关 `storage.register_device()` 命中已有设备返**同一份**密钥（不重签、不覆盖）。
密钥绝不回传浏览器；管理台只看状态与配额。旧 6 位配对码路径保留为兜底，默认不暴露。

**固件**：`net_req.h/.c`（`NR_PATH_REGISTER` + `nr_build_register_plain`）、
`net_api.h/.cpp`（`net_auto_register` / `net_is_paired` / `net_post_plain` /
`net_store_secret_from_plain_reply`）、`net_dispatch.c`（`NET_OP_REGISTER`）、
`hal_shared.h`（`ui_command_t.kind=5` + `UI_PAIR_STATE_*`）、
`tasks.cpp`（上电未配对即自动登记；失败每 15 秒重试，放主循环里不占请求队列）、
`ui_pages.cpp/.h`（OOBE 改「自动接入进度页」）、`ui_router.cpp/.h`
（`ui_router_set_pair_state`，READY → 自动跳首页，设备侧零输入）、
`tools/font_charset.txt` + `ui_font_cn_16.c`（新增「儿」「尝」，重生成 936 字形）。
顺带修掉 CI 里 `set +e` 静默吞掉链接失败的老问题（`test_net_dispatch` 补 `net_sync_pending_assets` 桩）。

**网关**：`app/storage.py` `register_device()`；`app/main.py` 新端点（校验 device_id 格式）+
`/admin/api/status` 的 `devices[]` 加 `status`/`registered_at`；`web/index.html` 设备表加「状态」列。

**文档**：doc/04 §1.2 改写为「设备自动登记握手鉴权（无码；6 位码降级为兜底）」+ 写明
「已接入」的真相源是 NVS 而非 TF 卡 `profile.json`；doc/05 §3.2 / §5 / §6.2；doc/03 §五 OOBE、§十 设置页。

**验证**：`test_net_req` 114 / `test_net_dispatch` 29 / `test_ui_data` 69 checks /
`test_host_gateway`（对真实网关）14 PASS / `host_check_arduino.sh` 通过 / charset 无缺字。
网关实测：重启后连续三次登记 → 同一份密钥（`registered` 首轮 true、后两轮 false）；
经本机 nginx `/abbot/` 前缀 200；非法 device_id / 缺字段 / 非法 JSON → 4001 + 温和中文；
用固件自己的密码学代码 + 登记到的密钥打真实网关，签名/信封/验签/解密全通。测试设备已清掉。

**提交**：固件 `0f119db`（已推 `1a8b77b..0f119db`）。**CI run 45572846664 = success**。
**产物自核**：bootloader 15104 B / firmware 1287056 B / partitions 3072 B，与 manifest 逐一吻合。

---

## 块 B：离线队列 outbox —— ✅

**索引格式**：按 **doc/04 §3.5 既有契约**（`task_id`/`type`/`status`/`retry_count`/`created_at`/
`hint_level`/`image_path`/`pcm_path`）+ 补传必需的 `session_id`/`context`。
10 条满载约 2.2KB —— 放不进 `storage_req_t.text` 的 1KB，故新增
`STORAGE_OP_WRITE_TEXT_BUF = 12`（数据取自调用方缓冲，队列项本身不放大）。

**模块**：`include/outbox.h` + `src/net/outbox.c`（纯 C）—— 走 `hal_storage_req_queue()`，
不碰 SPI、不直接调 `hal_storage_*`。

**策略（同步写进 doc/04 §3.5）**：重试上限 **5 次**；过期 **72 小时**；
时钟未校准**不做过期判定**（否则一开机就把队列全清掉）；队列上限 **10 条**，
满了淘汰最旧一条并删它的关联文件；只有**传输失败**才入队（网关业务错不入队）；
补传**每次只补一条**且**只在业务请求队列空闲时**（一次拍题往返含 AI 推理可能十几秒）。

**接线**：相机任务把 `media_path` 带给网络任务；拍题成功 → 删媒体文件；传输失败 → 入队；
网络队列满时也入队。

**验证**：`test_net_client` 18 / `test_net_req` 114 / `test_net_dispatch` **35** /
`test_ui_data` 69 checks / `test_outbox` **93 checks** —— 全部 0 failures。

**提交**：固件 `0027c11`（已推 `0f119db..0027c11`）。**CI run 45577886523 = success**。
**产物自核**：bootloader 15104 B / firmware **1291632 B** / partitions 3072 B，与 manifest 逐一吻合。

---

## 块 C：学科包 cards.enc 解密 + 卡片学习页 —— ✅

**包格式**（与服务端 `pack_builder.py` 逐字核对）：
`cards.enc` = `IV(16B) || AES-128-CTR(明文 JSONL)`；密钥 `device_secret[0:16]`（`key_type=device`）
或固定演示密钥（`key_type=demo`）；`index.idx` = `"IDX1"` + 版本 + 张数 + CRC32 + 偏移表。

**模块**：`include/cards.h` + `src/net/cards.c`（纯 C）
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

**验证**：`test_cards` **66 checks, 0 failures** —— 含 **doc/05 §3.4 官方锁定向量**
（`block_index=2` 处明文 `{"ping":1}` ↔ 密文 `41da37cc48e5aa3f8d06`）、`cards_load` 端到端，
以及**交叉核对**：用服务端 `pack_builder.build_pack()` 真实造出来的包喂固件自己的解密代码，
逐字核对题干与答案（Python 服务端 ↔ C 固件这一对必须逐字节一致）。

**顺带修掉的服务端真 bug**：`pack_builder.py` 的 `DEMO_KEY = b"STUDY_DEMO_KEY_16"` 是 **17 字节**，
AES-128 要恰好 16 字节 —— `key_type="demo"` 的打包会直接抛
`ValueError: Invalid key size (136) for AES`。仓库里唯一的包是 `key_type=device`（长度天然正确），
所以这条路一直没被走过。已改为 `b"STUDY_DEMO_KEY16"`，固件常量与 doc/04、doc/05 同步。
修完实测：demo 包构建成功（224 字节 cards.enc）。

**文档同步**：doc/04 §2.2（删掉「空白卡预置 demo_pack」的不实说法 + 写明学科包由网关心跳下发）、
§3.3（密钥名）、§四（补设备端实现落点与层级说明）；doc/05 §6.2（密钥名）；
doc/03 §五 第 4/5 节（学科包列表与翻卡页写实，标明掌握进度与语音追问未实现）。

**提交**：固件 `0a848ed`（卡片包）+ `f21a8e9`（交叉核对测试）+ `cd49fe7`（修常量名）。
**CI run 45580001210 = failure**（唯一一处：`ui_pages.cpp:623` 的 `UI_DATA_SUBJECTS_DIR`
应为 `UI_DATA_DIR_SUBJECTS` —— 我写常量名时前后不一致）。
**CI run 45580497302 = success**（修完）。
**产物自核**（最终）：bootloader 15104 B / firmware **1295712 B** / partitions 3072 B，
sha256 与 manifest.txt **逐一吻合**。

---

## 实现过程中修掉的自己的 bug（累计 7 个）
1. `outbox_parse` 找不到 `tasks` 数组 —— 通用取值函数碰到 `[` 会当标量值停住 → 补 `json_find_array()`。
2. 序列化时 `pcm_path`/`session_id` **漏写键名**（只写值）→ 重构成 `json_put_str_field()`。
3. `status` 字段长度比较写成 7（"retry" 是 5）→ retry 态解析不出来。
4. 测试桩 `g_last_code[8]` 装不下媒体路径 → 另开 `g_last_media[72]`。
5. **`cards_decrypt_range` 密文摆错位置** —— 摆在 `block[0]` 会让块尾用错密钥流 → 改摆 `block[byte_in_block]`。
6. `s_paired` 真相源搞错 —— 读的是 TF 卡 `profile.json` 的 `paired` 字段，
   而固件从不写那个字段 → 改为取「profile.json 标志」与「NVS 密钥已在手」的**或**。
7. `UI_DATA_SUBJECTS_DIR` / `UI_DATA_DIR_SUBJECTS` 常量名前后不一致（CI 唯一一处编译错）。
   顺手加了一道本机自查：把 include/ 下所有头文件里定义的宏/函数/类型/枚举成员收集起来，
   扫 src/ 与 test/ 里「用了但没定义」的标识符 —— 不用等 CI 就能发现同类错。

## 明确的没做
- 多网关地址可配；OTA apply 与 app1 双分区；真机校准。
- **真实签名联调**：没有真机，端到端签名验证只能靠主机侧测试覆盖
  （`test_host_gateway` 用固件自己的密码学代码打真实网关，14 PASS）。
- 学科包**掌握进度百分比**未实现（`/records/progress.json` 没接线）。
- **语音追问这题**仍未接通（UI 只给温和提示）；**语音问答链路整体**也未接通。
- 离线队列只支持 `SOLVE` 类型的补传；`VOICE` 类型的条目字段齐备但补传路径未实现。
- 设备端 manifest（`/subjects/manifest.json`）的写入未接线 —— 学科包列表靠列目录发现，不依赖它。
- 卡片页的「语音追问」菜单项按 doc 保留但未接线。
