# 下一步：固件版本抬号 + OTA 清单重发（Wi-Fi 与天气固件落地后执行）

> 触发条件：`Wi-Fi 真扫描` 与 `天气显示` 两个固件子会话都完成并合入 `main` 之后。
> 顺序不能颠倒 —— 抬号太早会把不含新功能的产物发出去。

## 现状（2026-11-25 18:50 实测）

- 固件 `include/net_api.h:40` → `#define NET_FW_VERSION "1.0.0"`
- 服务端 `storage/ota/manifest.json` → `latest = "1.0.0"`，指向 `fw_1.0.0.bin`
  - 该 bin 的 sha256 = `124c33a8…`，大小 1295712 —— 是 **CI run 45580497302** 那版产物
  - **不含** OTA 客户端、阅读护栏、Wi-Fi 扫描、天气显示
- `fw-bin` 分支最新产物（OTA 客户端落地后）：
  - commit `b43146c`，tag `fw-be17983e6d3f4a1e8c820f8341c37d7ae7428145`
  - `bootloader.bin` 15104 / `2a71d69b471e20c2bac7fb469f3c6a807b3ebee780e348e5889db0da849ca363`
  - `firmware.bin` 1311504 / `bdc888eccb8f533b1a836c4feac45a6904dbe22dc0ab4149b59c1308e93d58dc`
  - `partitions.bin` 3072 / `71939c82bec98f427052a1811aaa2a9bebfcf8cc107128341674de72f1f06ec6`
- 清单里的 `changelog` 写了「Wi-Fi 扫描、OTA 客户端」——**OTA 客户端现在有了，Wi-Fi 扫描还没落地**。
  下一版必须按**实际内容**重写，不能沿用。

## 下一个提交要一起做的两件事（别拆开）

### A. 修一处**会误导后来人**的注释（重要）

`firmware/src/net/ota_client_esp.cpp` 约 528 行，NVS「待验证」标记写入处的注释写着：

> 写在 `esp_ota_end()` **之前**：`esp_ota_end()` 会把启动分区切到新分区

**这句是错的**。ESP‑IDF 4.4 的 `esp_ota_end()` 只做「校验镜像 + 结束写入」，**不切启动分区**；
切分区的是 `esp_ota_set_boot_partition()`（同文件 544 行，代码已经正确调用了）。

不改的后果：后来人照注释以为 `esp_ota_set_boot_partition()` 是多余的，删掉它 → **升级后永远启动老固件**，
而且现象是「OTA 显示成功但版本不变」，极难查。

改成如实描述：

> 写在 `esp_ota_end()` 之前：`esp_ota_end()` 只校验镜像并结束写入，**不切启动分区**；
> 真正切分区的是后面的 `esp_ota_set_boot_partition()`。
> 标记先写，是为了「已写入备用分区但还没切换」时中断也能在下次启动识别出来。

### B. 抬固件版本

`include/net_api.h` 的 `NET_FW_VERSION` 从 `"1.0.0"` 改成 `"1.0.1"`。
   - 版本串**不带 `v` 前缀**（`check_update()` 判字符串相等，`v1.0.1` ≠ `1.0.1`，设备永远看不到更新）。
   - 改完提交到 `main`，**跑一轮 CI**。
2. **取新产物**：CI 绿后
   ```bash
   cd /tmp/abbot-firmware
   GIT_SSH_COMMAND="ssh -i ~/.ssh/id_ed25519 -o IdentitiesOnly=yes -o BatchMode=yes" \
     git fetch '+refs/heads/fw-bin:refs/remotes/origin/fw-bin'
   ```
   取出三个 bin，**自己算 sha256 与 `manifest.txt` 逐一比对**（`gh api` 不受影响，可交叉验证）。
3. **重发 OTA 仓库**（参数已核实，命令就这一条）：
   ```bash
   cd /home/open/.openclaw/workspace/abbot/server
   ../.venv/bin/python scripts/publish_ota.py \
     --bin /tmp/_fw_1.0.1.bin \
     --version 1.0.1 \
     --changelog "<按实际内容写>"
   ```
   产物落到 `storage/ota/fw_1.0.1.bin`，`manifest.json` 的 `latest` 变 `"1.0.1"`。
   - ⚠️ `--version` **不要带 `v` 前缀**。`publish_ota.py` 的 `--help` 原文写的是「版本号，如 v1.0.0」
     —— 那是**错的引导**（`ota_manager.publish()` 直接拿它当落盘名 `fw_{version}.bin` 与清单 key，
     不会剥 `v`）。已把 help 文案改成「如 1.0.0（**不要带 v 前缀**）」。
   - `changelog` **只写这一版真正包含的东西**。
   - 旧的 `fw_1.0.0.bin` 可以留着（设备可回退），但别让它当 latest。
   - ⚠️ **顺手修正 1.0.0 自己的 changelog**（现在写的是「Wi-Fi 扫描、OTA 客户端、学科包与离线队列」，
     但 `124c33a8…` 那份产物是 CI run `45580497302` 的，**OTA 客户端与 Wi-Fi 扫描都是之后才有的**，
     它俩不在那个二进制里）→ 改成如实描述，避免历史产物被说成已包含这些功能。
4. **自检**：
   ```bash
   python3 abbot/server/scripts/ota_selfcheck.py
   ```
   要看到：`current_ver=1.0.1` → `has_update=false`；`current_ver=1.0.0` → `has_update=true` + 带 token 的 `bin_url`；
   实下 `fw_1.0.1.bin`，**下载内容 sha256 == 清单 sha256 == 响应头 `X-File-Sha256`** 三者一致。
5. **重启服务端**（改了 `storage/ota/` 不需要重启，但改了 `config.yaml` 需要）：
   ```bash
   export XDG_RUNTIME_DIR=/run/user/$(id -u)
   systemctl --user restart openclaw-abbot.service
   ```

## 已核实：OTA check 的签名路径口径一致 ✅

- 固件 `src/net/net_api.cpp:1263` 签的是 **纯路径**：`GET\n/api/v1/ota/check\n<ts>\n<sha256(空 body)>`，
  常量 `NR_PATH_OTA_CHECK = "/api/v1/ota/check"`（`include/net_req.h:34`）——
  请求 URL 带 query，**签名用纯路径**，注释里已写明这条铁律。
- 服务端 `app/main.py:454` 用 `req.url.path` 验签（**不含 query**）→ 与设备一致 ✅。
- `scripts/ota_selfcheck.py` 也是用纯路径签（`path = "/api/v1/ota/check"`，query 单独拼在 url 上）→ 三方同口径。
- 结论：设备查更新不会因为路径口径不一致而 4001。真机联调时若仍失败，优先查**时间戳偏差**（窗口 60 秒，
  设备 RTC 未校准就会飘）与 `device_secret` 是否已登记。

## 真机联调前还要逐条核对的（发版后）

1. ~~**下载端点必须回 `Content-Length`**~~ → **已核，服务端满足**：`app/main.py` 的
   `/api/v1/content/download` 用 `StreamingResponse(stream(), headers={"Content-Length": str(size), "X-File-Sha256": sha})`，
   `Content-Length` 显式给了（虽然 body 是分块 yield 的），固件 `ota_download_write_and_activate()`
   取得到这个头、且会与 check 应答的 `size` 逐一比对。`X-File-Sha256` 也带。
2. **下载请求必须带 `X-Device-Id`** → **已确认**：固件 `net_http_get()`（`net_api.cpp:1081`）与
   OTA 直连下载路径（`ota_client_esp.cpp` 内）都带这个头，服务端下载端点用它定位设备密钥。
3. **版本比较口径** → **已对齐**：固件 `ota_version_compare()` 按数字段比（`1.0.10 > 1.0.9`）；
   服务端 `check_update()` 取清单 `latest` 与设备 `current_ver` 比，固件侧再用 `ota_should_update()`
   二次判定，两边都以「无 `v` 前缀的数字段版本串」为口径。
4. **时间戳窗口 60 秒**：设备 RTC 没校准就会飘 → 表现为“连接有点不稳”（业务码 4001）。
5. **回滚前提**：设备跑在 app1 时 `esp_ota_set_boot_partition(app0)` 才能退回去。
6. **OTA check 的下发开关**：服务端 `check_update()` 支持 `ALL / CANARY / PAUSED` 三档策略 +
   连续失败熔断（`ota.circuit_breaker_threshold`，默认 3）→ 下一版出问题时可只放行白名单设备。

## 坑位备忘

- **设备侧上报的版本串必须与清单 `latest` 完全一致**（含无 `v` 前缀）。
- `manifest.json` 的 `file` 字段必须记**落盘后的真实文件名** `fw_{version}.bin`，
  `ota_manager.publish` 就是按这个名落的盘；自己拼 `{version}.bin` 会 404。
- 拉 `fw-bin` 默认 SSH key 会报 `correct access rights`，必须带 `GIT_SSH_COMMAND`。
- 外网入口是明文 HTTP（`http://ruohan.site/abbot/`），不做 HTTPS。
