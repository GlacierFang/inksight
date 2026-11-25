# abbot 第四块：WiFi 真扫描 / 固件升级 / 天气链路 / 阅读进度与防沉迷

> 2026-11-25 17:40 立。冰河大人拍板四条全做；UI 视觉**不预先做**，等他的要求。

## 硬约束（全部沿用，不新增）

- 固件只由 GitHub Actions 编译；本机禁 PlatformIO，只允许 gcc 主机侧测试。
- 交互宪法：四向导航 + 短按确定 + 长按 600ms 松开退出；长按阈值只由 HAL 裁定；
  保留摇杆输入；语音只能从菜单进入。
- UI 只经 `hal_storage_*` 读卡，走存储任务请求/应答；UI 任务不碰 SPI；网络任务不扫 TF 卡。
- 错误文案不得出现 AES/HTTP/signature/Token/Python/Traceback 等技术术语。
- abbot 外网入口保持明文 HTTP，不做 HTTPS。
- 只改 abbot 范围内文件；`.env`、密钥、缓存不入库。
- **小步提交**：每完成一块就同步 + 提交 + 推，别攒到最后。
- **不做 UI 美化**：不抽主题层、不改配色字体、不重排布局。UI 只做「接真实数据」这一件事。
  视觉风格由冰河大人另行定稿后单独做。

## A. Wi-Fi 真扫描（不能写死）

现状：`net_api.cpp:269` 已有 `net_wifi_start_async_scan()` / `net_wifi_get_scan_results()`
（异步扫描不阻塞），但 `ui_pages.cpp:554` 的 `ui_build_wifi_scan` 写死三条
`Home-2.4G 强 / Guest 中 / Neighbor 弱`，完全没接。

要做：
1. Wi-Fi 扫描页进页 → 启动扫描 → 轮询结果 → 列表显示**真实 SSID + 信号强度 + 是否加密**。
2. 选中一个 AP → 用**虚拟键盘页输密码** → 调 `net_wifi_connect_and_save()` → 显示连接结果。
3. 虚拟键盘页（`ui_pages.cpp:544`）当前也是写死的，**要真做**：可移动光标、输入、删除、
   空格、大小写切换，能把字符送到调用方。
4. 已保存网络列表、连接状态、失败重试。
5. 扫不到 / 未连上 → 温和中文提示。

## B. 固件升级（OTA 真做）

服务端已有：`app/ota_manager.py`（publish / verify_firmware / check_update / report_result）
+ `GET /api/v1/ota/check` 路由（`main.py:411`）+ doc/05 §三 的应答形状。
但 `server/storage/ota/` 是**空的**（无 manifest.json、无 .bin），固件侧**一行 OTA 代码都没有**。

固件侧要做（doc/04 §OTA 章节是真相源）：
1. **分区表**：现 `platformio.ini` 用 `board_build.partitions = huge_app.csv`，**只有 app0**。
   doc/04 要求经 `esp_ota_get_next_update_partition()` 写入未激活的备用分区 →
   **必须先设计满足 16MB Flash 的双应用分区表**（项目内自定义 csv，`board_build.partitions` 指过去），
   app0/app1 各能装下当前 ~1.3MB 固件并留余量，nvs / otadata / phy_init 保留。
   ⚠️ 改分区表会影响整个 CI 构建产物大小，改完必须核三个 bin 仍能产出。
2. **下载**：`/api/v1/ota/check` 拿 `has_update/latest_ver/sha256/size/bin_url/changelog` →
   用带签名 token 的 `bin_url` 下载。
3. **三重校验**（doc/04）：首包首字节必须 `0xE9`；总字节数与 `size` 一致且不超分区容量；
   流式 `mbedtls_sha256` 与网关声明一致。每 4KB 喂一次看门狗。
4. **写入备用分区** → 重启 → 新固件自检通过后 `esp_ota_mark_app_valid_cancel_rollback()`；
   自检不通过由 bootloader 回滚。**OTA 失败绝不能变砖**（这是硬要求）。
5. **前置采样**：静置 3 次 × 50ms 取电池电压均值，`avg_mv >= 3600mV` 且 `RSSI >= -75dBm` 才启动。
6. **UI**：OTA 页（`ui_pages.cpp:566` 现在写死「当前已是最新版本 / 上次检查：刚刚」）接真实数据：
   当前版本、最新版本、changelog、下载进度百分比、结果提示。按钮要真能触发检查。
7. 上报结果到服务端 `report_result`。

服务端侧（另一路）要做：把 `fw-bin` 的产物按版本发布进 `storage/ota/`，
生成 `manifest.json`（含 sha256/size/bin_url），让 `/api/v1/ota/check` 真能返回 `has_update`。

## C. 天气链路（两端都要）

现状：doc/03 把「准点课表/天气提醒弹窗」定为最高优先级浮层，doc/05 规定 bootstrap 应答含
`weather{temp,text}`；但**服务端全项目没有代码写 `storage/cache/weather.json`**，
`config.yaml` 无城市配置项；固件只有 `hal_storage.cpp:53` 的空文件骨架，没人读。

服务端侧：
1. `config.yaml` 加天气配置（城市、可选经纬度）。
2. 后台定时抓取（TTL 2 小时，与现有 `_weather_snapshot` 的 TTL 对齐）→ 原子写
   `storage/cache/weather.json`（结构：`{"weather": {"temp": ..., "text": ...}, "updated_at": ...}`）。
3. 抓取源用已放行的 `wttr.in` / `api.open-meteo.com`（`web_fetch` 或服务端直连，别用代理被挡的域名）。
4. `/api/v1/device/profile`（或既有档案端点）接受设备上报的城市 → 写回配置，供固件「设置城市」用。
5. bootstrap 继续带 `weather{temp,text}`；抓到失败时温和降级（不报错、不阻塞）。

固件侧：
1. 读 `/cache/weather.json` 显示在首页 + 准点提醒浮层（doc/03 的 OVERLAY_REMINDER）。
2. **「设置城市」入口**（这是现在完全没有的东西）：设置页加一项 → 用虚拟键盘输入城市名 →
   上报服务端 → 落一份到 TF 卡（`/config/settings.json`）供离线兜底显示。
3. 没数据 / 抓不到 → 温和中文空态（如「暂时拿不到天气」），不显示假数据。

## D. 电子书阅读进度 + 防沉迷（doc/07 §2.3 / §2.4 是真相源）

1. **进度**：`/records/bookmarks.json` 每本书只维护**唯一最新进度**（翻页停顿即自动保存，
   下次打开秒回原位）。原子写 + `.bak` 自愈。
2. **percent 必须实时重算**：`current_byte_offset * 100 / total_bytes`，**严禁直接信 JSON 里的 percent**，
   钳位 0~100%，`total_bytes == 0` 时显示 0.0%。书架列表与阅读页页脚都显示。
3. **防沉迷护栏**（配置复用 `/config/profile.json`）：
   - `curfew_window`：默认 `["22:00", "06:30"]` 宵禁时段锁定。
     同日区间（`start <= end`）：`now >= start && now < end` 触发；
     **跨午夜区间（`start > end`）：`now >= start || now < end` 触发**。
   - `daily_book_limit_min`：默认 `45`，每日课外阅读时长上限 + 精确计时暂停。
   - 锁定时给温和中文提示（不解释成技术错误），不出现英文技术词。
4. 翻页时保存进度；退出阅读页也保存。

## 分工（两路并行，文件不重叠）

| 路 | 范围 | 负责文件 |
|---|---|---|
| **A 服务端** | OTA 仓库发布 + manifest + `/api/v1/ota/check` 打通；天气定时抓取 + 城市配置 + 设备上报城市 | `abbot/server/**`、`abbot/doc/05*.md` |
| **B 固件** | Wi-Fi 真扫描 + 虚拟键盘；OTA 分区表 + 下载校验 + 激活回滚 + OTA 页；天气显示 + 设置城市；阅读进度 + 防沉迷 | `abbot/firmware/**`、`abbot/doc/02*.md`、`doc/03*.md`、`doc/04*.md`、`doc/07*.md` |

- B 推 `/tmp/abbot-firmware` main 触发 CI；A 不碰固件仓。
- A 的服务端改动**本地重启服务实测**（`systemctl --user restart openclaw-abbot.service`）。
- 文档冲突风险：A 只改 doc/05，B 不改 doc/05；B 要改 doc/05 的需求回报给主会话。

## 验收

- CI 在 main 上全绿（含主机侧测试步骤），三个 bin 与 `manifest.txt` 逐一吻合（自己 fetch `fw-bin` 核）。
- 每一项要有**可推翻**的证据：Wi-Fi 扫描页的 host 侧测试证明它读的是扫描结果不是硬编码；
  OTA 的分区表与校验逻辑要有主机侧测试；天气的抓取与解析要有测试；
  进度与防沉迷要有跨午夜边界与 percent 重算的测试。
- 明确写出哪些没做。
