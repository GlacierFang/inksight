# 05-OTA 发布与自检（服务端侧）

> 2026-11-25 新增。配套 `server/app/ota_manager.py` 与 `server/scripts/`。
> 本文只讲**服务端仓库怎么被填满**、**怎么自检**；设备端的应答形状仍以 05 主文档 §五 为准，不改。

## 一、产物从哪来

固件**一律由 GitHub Actions 编译**，本机不装 PlatformIO、不本地编译。

CI 把产物推到固件仓的 `fw-bin` 分支（force push）。取产物：

```bash
cd /tmp/abbot-firmware
git fetch origin '+refs/heads/fw-bin:refs/remotes/origin/fw-bin'
git ls-tree -l origin/fw-bin
```

`fw-bin` 分支上固定四个文件：

| 文件 | 作用 | 设备端下载 |
|---|---|---|
| `firmware.bin` | 应用固件本体（app 分区） | ✅ 是 |
| `bootloader.bin` | 引导程序 | ❌ 否（归档备查） |
| `partitions.bin` | 分区表 | ❌ 否（归档备查） |
| `manifest.txt` | CI 里的构建目录清单与 sha256 | ❌ 否（归档备查） |

## 二、发布（复用既有 `OtaManager.publish()`）

发布走既有接口，**不另造一套**：

```bash
cd abbot/server
python3 scripts/publish_ota.py \
  --bin /tmp/abbot-fw/firmware.bin \
  --version 1.0.1 \
  --changelog "首个可发布版本：……" \
  --extra /tmp/abbot-fw/bootloader.bin,/tmp/abbot-fw/partitions.bin,/tmp/abbot-fw/manifest.txt
```

> `--version` 的值**不要带 `v` 前缀**。`publish()` 直接拿它当落盘名 `fw_{version}.bin` 与清单 key，
> **不会剥 `v`** → 写成 `v1.0.1` 会落成 `fw_v1.0.1.bin`，且与设备自报的 `NET_FW_VERSION` 对不上。

`publish()` 内部做的事：

1. `verify_firmware()` —— 魔数（首字节 `0xE9`）+ SHA-256 + 体积校验，任一不过直接抛错，不落半个文件；
2. 固件复制为 `storage/ota/fw_{version}.bin`；
3. 写 `storage/ota/manifest.json`。

`--extra` 的文件只归档进 `storage/ota/`，**不登记进清单**（设备端不需要下载它们）。

版本号必须与固件里自报的一致：固件上报的是 `include/net_api.h` 的 `NET_FW_VERSION`，
清单版本写成同一个字符串（**当前 `"1.0.0"`**）。写成 `"v1.0.0"` 会导致设备永远收不到更新
（`check_update()` 判的是**字符串相等**）。

> ⚠️ **发版顺序**：先改 `NET_FW_VERSION` 并跑一轮 CI，**拿到那一轮产物再发**。
> 先发后改号 → 清单里的 `latest` 与固件自报版本对不上，设备会一直认为有新版本、反复下载同一个包。

### changelog 必须与产物内容一致

`changelog` 会直接显示在设备的升级页上。**写这一版真正包含的东西**，不要写"计划包含"或"即将支持"。
历史版本的 changelog 若与那份产物不符，也要在下一版发布时顺手改正
（例：`1.0.0` 的产物里没有 OTA 客户端与 Wi-Fi 真扫描，就不能那样写）。

## 三、清单结构 `storage/ota/manifest.json`

```json
{
  "versions": {
    "1.0.0": {
      "version": "1.0.0",
      "file": "fw_1.0.0.bin",
      "sha256": "124c33a8580a1bc3f6fa1ba92e26f2a48544c5a19d873cd1097d5bf53b7b5e62",
      "size": 1295712,
      "changelog": "……",
      "force_update": false,
      "published_at": 1795602194
    }
  },
  "latest": "1.0.0"
}
```

字段含义：

| 字段 | 含义 |
|---|---|
| `file` | 仓库内文件名。**下载路径由它拼**（`ota/{file}`），不能自行拼 `{version}.bin` |
| `sha256` | 固件本体摘要，设备端流式校验用；服务端下载时也在响应头回 `X-File-Sha256` |
| `size` | 固件字节数，设备端校验总长度用 |
| `changelog` | 更新说明，设备端 OTA 页展示 |
| `force_update` | 为真时即使设备版本号相同也下发（当前为 `false`） |
| `published_at` | 发布时间戳（秒） |
| `latest` | 多个版本里选出的最新版（按数字段比较） |

灰度白名单**不在清单里**：策略 `PAUSED` / `CANARY` / `ALL` 与白名单 `canary_devices`
在 `config.yaml` 的 `ota:` 段（`canary_devices` 为空 = 全量）。

## 四、`.gitignore` 约定

仓库根 `.gitignore` 第 39 行 `abbot/server/storage/` —— **整个 storage 目录不入库**，
所以 `manifest.json` 与 `fw_*.bin` 都不进 git。本机 `storage/ota/` 真实存在并生效；
换机器部署时用 `scripts/publish_ota.py` 重新发布一次即可。

## 五、自检（真调接口 + 真下载）

```bash
cd abbot/server
python3 scripts/ota_selfcheck.py --device esp32s3_test01 --current-ver 0.9.0
```

脚本做的是**设备端同一条路**，不削弱鉴权：

1. 从 `storage/devices.json` 取设备密钥；
2. `app.security.sign_request` 给 `GET /api/v1/ota/check` 签名，带
   `X-Device-Id` / `X-Timestamp` / `X-Signature` 三个头，响应签名也验；
3. `app.security.decrypt_json` 解出业务字段，逐项与 `manifest.json` 对照；
4. 用应答里的 `bin_url`（换本机 base、token 不变）真下载一次，
   比对字节数、SHA-256、响应头 `X-File-Sha256` 三者与清单一致。

`--current-ver` 要给**比清单旧的版本**才会命中更新；给成与清单相同版本时
`has_update=false` 是正确行为（设备已经是最新）。

## 六、分发策略（`config.yaml` → `ota:`）

| 策略 | 行为 |
|---|---|
| `ALL` | 全量下发（当前默认） |
| `CANARY` | 只有 `canary_devices` 里的设备能拿到 |
| `PAUSED` | 全停，只回版本信息不给 `bin_url` |

另有熔断：同一版本连续上报失败达 `circuit_breaker_threshold`（默认 3）次即停止下发，
避免批量变砖。设备端升级结果要回报到 `report_result`。
