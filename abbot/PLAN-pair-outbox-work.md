# abbot 本块工作日志（配对简化 / outbox / 学科卡解密）

> 2026-11-25 起。三件事分三次提交，每块单独同步+提交+推。

## 约束速记

- 固件只由 GitHub Actions 编译；本机禁 PlatformIO，只允许 gcc 主机侧测试。
- 交互宪法：四向导航 + 短按确定 + 长按 600ms 松开退出；长按阈值只由 HAL 裁定；保留摇杆；语音只能从菜单进入。
- UI 只经 `hal_storage_*` 读卡；UI 不碰 SPI、不碰网络、不碰加密。
- 错误文案不得出现 AES/HTTP/signature/Token/Python/Traceback。
- abbot 外网保持明文 HTTP。
- 签名格式：`METHOD\nPATH\nX-Timestamp\nRAW_BODY_SHA256`（PATH 不含域名、query、`/abbot` 前缀）。
- 只改 abbot 范围文件；`.env`/密钥/缓存/测试数据不入库。
- 新增中文文案必须同步加进 `tools/font_charset.txt` 并重生成字库（`tools/gen_font.sh`）。
- 连续只读调用 ≤3 次，读写比 ≤2:1。

## 设计决策（块 A：自动登记）

**新端点**：`POST /api/v1/device/register` —— **明文 JSON**（此时设备还没有密钥，无法签名，与
`/api/v1/pair` 同性质）。请求：`{"device_id":"esp32s3_xxxxxxxx","fw_version":"1.0.0"}`。
应答：沿用 `/api/v1/pair` 的信封形状 → `payload` 是明文 JSON 串，内含 `device_secret`（64 hex）。
固件侧解析逻辑与 `net_pair_with_code()` 完全复用，**零新增解析分支**。

**幂等**：网关 `storage.register_device()` —— 命中已有设备就返**同一份**密钥（不重签），
未命中才签发新密钥落盘。

**密钥不出浏览器**：`/admin/api/status` 的 `devices[]` 只给 device_id / status / 配额，无 secret 字段；
管理台加「状态」列显示「已接入」。

**旧路径兜底**：`/api/v1/pair`（6 位码）与网页端「生成配对码」保留，默认不暴露。

## 块 A 已完成的代码改动

**固件**（`abbot/firmware/`）
- `include/net_req.h`：`NR_PATH_REGISTER` + `nr_build_register_plain()` 声明。
- `src/net/net_req.c`：`nr_build_register_plain()` 实现（有 fw_version / 无 fw_version / 空 id 三态）。
- `include/net_api.h`：`net_auto_register()` / `net_is_paired()` 声明 + `NET_OP_REGISTER=6`。
- `src/net/net_api.cpp`：抽出 `net_store_secret_from_plain_reply()` + `net_post_plain()`，
  `net_pair_with_code()` 改用它们，新增 `net_auto_register()` / `net_is_paired()`。
- `src/net/net_dispatch.c`：`NET_OP_REGISTER` → `net_auto_register()`。
- `include/hal_shared.h`：`ui_command_t.kind=5` + `UI_PAIR_STATE_*`（NONE/PENDING/READY/FAILED）。
- `src/tasks.cpp`：网络任务上电后未配对 → 自动登记；投 kind=5 状态；失败每 15 秒重试（不占住请求队列）；
  UI 任务收 kind=5。
- `src/ui/ui_pages.cpp` + `include/ui_pages.h`：OOBE 改「自动接入进度页」，`ui_pages_set_pair_state()`。
- `src/ui/ui_router.cpp` + `include/ui_router.h`：`ui_router_set_pair_state()`，READY → 自动跳首页；
  OOBE 三个入口（连 Wi-Fi / 重新尝试接入 / 先离线体验）；首页未接入提示改为「请检查一下网络」。
- `tools/font_charset.txt`：新增「儿」「尝」；`src/ui/fonts/ui_font_cn_16.c` 重生成（936 字形 / 739620 字节）。
- `test/test_net_req.c`：新增 `test_register_plain()`。
- `test/test_net_dispatch.c`：新增 `net_auto_register` 桩 + `CALL_REGISTER` + 路由断言；
  **顺带补了漏掉的 `net_sync_pending_assets` 桩**（原 CI 里 `set +e` 把链接失败吞掉了，
  等于这个测试从来没真跑过 —— 这次修好并让 CI 真的把关）。
- `.github/workflows/build.yml`：主机侧测试步骤 `set +e` → `set -e`（之前链接失败被静默吞掉）。

**网关**（`abbot/server/`）
- `app/storage.py`：`register_device()` → `(secret_hex, created)`，幂等；补 `secrets` / `Tuple` import。
- `app/main.py`：`POST /api/v1/device/register`（校验 device_id 格式）；`/admin/api/status` 的
  devices[] 加 `status` / `registered_at`。
- `web/index.html`：设备表加「状态」列。

**文档**（`abbot/doc/`）
- `04`：§1.2 改写为「设备自动登记握手鉴权（无码；6 位码降级为兜底）」+ 真相源是 NVS 的说明 + 密钥不回传浏览器。
- `05`：§3.2 签名例外加 `/api/v1/device/register`；§5 端云 API 表加该行；§6.2 status/pair-code 说明更新。
- `03`：§五 第 0 页 OOBE 改为接入进度页线框 + 流程说明；§十 设置页第 2 项标注为兜底路径。

## 主机侧验证结果（本机 gcc，非固件编译）
- `test_net_req`：**114 PASS / 0 FAIL**
- `test_net_dispatch`：**29 PASS / 0 FAIL**
- `test_ui_data`：**69 checks, 0 failures**
- `test_host_gateway`（对真实网关）：**14 PASS / 0 FAIL**
- `host_check_arduino.sh`：`net_api.cpp` + `ui_data.c` 均通过 `-Wall -Wextra -Werror`。
- `tools/check_charset.py`：5 个文件、上屏文案无缺字。

## 网关实测
- 服务重启后 `POST /api/v1/device/register` 连续三次返回**同一份**密钥（幂等 OK），
  `registered` 字段首轮 true、后两轮 false。
- 经本机 nginx `/abbot/` 前缀同样 200（生产真实路径）。
- 非法 device_id / 缺 device_id / 非法 JSON → 一律 `code 4001` + 温和中文「连接有点不稳，请再试一次」。
- 用固件自己的密码学代码（`nc_build_request` / `nc_verify_response` / `nc_decrypt_envelope`）
  拿登记得到的密钥打真实网关：签名、加密信封、响应验签、解密全通，**14 PASS / 0 FAIL**。
- 测试设备已从 `storage/devices.json` 清掉（不留测试数据）。

## 进展日志

- 15:45 建仓本日志，读 PLAN / ALIGN §9.20。
- 15:55 摸清固件配对链路与服务端端点清单。
- 16:05 定下块 A 设计。
- 16:35 固件块 A 代码写完。
- 16:40 网关块 A 代码写完。
- 16:45 charset 补字 + 字库重生成（936 字形）。
- 17:05 主机侧测试全绿；修掉 `test_net_dispatch` 漏桩 + CI `set +e` 吞失败。
- 17:20 网关重启实测，幂等验证通过。
- 17:25 `test_host_gateway` 对真实网关 14 PASS。
- 17:35 修两个自己写出来的真 bug（s_paired 真相源、自动登记只试一次）。
- 17:50 文档同步完成；准备同步固件 + 提交 + 推。

## 待办
- [ ] 同步固件到 /tmp/abbot-firmware → 提交 → 推（触发 CI）
- [ ] workspace 本地提交（网关后端 + doc + 工作日志）
- [ ] CI 绿 + fetch fw-bin 核验产物
- [ ] 块 B（outbox）
- [ ] 块 C（cards.enc 解密 + 卡片学习页）
