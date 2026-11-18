# eink-mini

2.9 寸黑白墨水屏桌面终端（ESP32-C3 Pro mini + GDEH029A1 / SSD1680 / 296x128）。
仓库：`git@github.com:GlacierFang/eink-mini.git`（GlacierFang/eink-mini），本地 `/home/open/.openclaw/workspace/eink-mini`。
<!-- project: path:/home/open/.openclaw/workspace/eink-mini -->

## 状态（2026-11-17）

- 固件：全部写完，CI 首次全绿（run 44103056290）。产物在 `fw-bin` 分支。
- 服务端：已上线 `http://ruohan.site/eink-mini/`（Flask + gunicorn，端口 18842，user scope systemd
  `openclaw-eink-mini.service`，nginx 路由 `/eink-mini/`）。<!-- project: path:/home/open/.openclaw/workspace -->
- 位序：服务端发 1=黑（4736 字节，MSB first），固件按 INK_SERVER_BIT1_IS_BLACK 翻成面板 1=白。
  自检工具 `server/tools/check_frame.py` 把 .bin 解回图验证。<!-- project: path:/home/open/.openclaw/workspace -->
- **留言功能整套已删**（提交 `660cf01`）：服务端 `GET/POST /api/message(s)`、`store.py`、
  `pages.render_message`、固件 `fetchMessage` / `collectStrings` / `inkRenderMessagePage` /
  `INK_PAGE_MESSAGE`、manifest 的 message 页、`write_token` 与写入鉴权，全部移除。
  设备回到三页：clock（native）、weather、calendar。<!-- project: path:/home/open/.openclaw/workspace -->
- **控制台改成功能分区式**（参考 ai-agent-pet 的 petd 后台范式）：设备状态（在线/最后活动/来源 IP/UA/
  拉取计数）、各功能页面预览、功能配置（天气城市 + 时钟屏标题）。<!-- project: path:/home/open/.openclaw/workspace -->
- **设备状态**：`server/device_state.py` 落盘记录（gunicorn 2 worker，内存态跨 worker 不可见），
  `GET /api/device` 暴露，3 分钟内有拉取算在线。<!-- project: path:/home/open/.openclaw/workspace -->
- RAM 30.2%、Flash 51.3%。提交 `660cf01` 的 CI run 44118223158 全绿。<!-- project: path:/home/open/.openclaw/workspace -->
- **未上真机**：B 键 GPIO（当前 16，候选 0/16/17）、旋转方向、BUSY 极性要实物核对。<!-- project: path:/home/open/.openclaw/workspace -->
- **天气上游 open-meteo 当前直连和代理都超时**（2026-11-17 23:40 观测，geocode 通、forecast 不通），
  天气页会退化成占位内容。<!-- project: path:/home/open/.openclaw/workspace -->

## CI 在国内网络的三个坑（都踩过）

1. **Actions 日志/artifact 下载主机被挡** → 真实错误走 `::error::` annotation（走 api.github.com，通的）；
   整份日志用 git plumbing 推到 `ci-logs` 分支读，别用 Contents API（有 1MB 限制）。
2. **缓存整个 `~/.platformio` 会带进 penv** → penv 的 shebang 写死 Python 路径，补丁号一变就 126 bad interpreter。
   只缓存 `packages` 和 `platforms`。
3. **PlatformIO Core 必须钉 6.1.18**：6.2.0 带 SCons 4.11+，SCons 4.11 起 `${func(args)}` 要求被调函数接受
   `target/source/env/for_signature` 关键字参数，espressif32 的 `__get_board_flash_mode(env)` 不匹配 →
   替换返回 SCons `_Null` 哨兵 → elf2image 那步 `print_cmd_line` 里 `_Null + str` 抛 TypeError。

## 环境坑

- Debian 的 gunicorn 只有 python 模块，没有 `/usr/bin/gunicorn` 脚本 → systemd 要写 `python3 -m gunicorn`。
- nginx 配置在 `/etc/nginx/sites-enabled/openclaw`，改动需要 sudo；改前先备份。
- frpc 隧道偶发 503，重试即恢复 —— 遇到 503 先重试几次再判断。

## 接线

SCK=6, MOSI=7, MISO=8, CS=1（CS2 与 CS1 短接）, DC=4, RST=10, BUSY=3, KEY_A=5, KEY_B=16(待确认)。
全部 3.3V。BUSY 极性上电探测 + 超时兜底，不写死。

## 许可

仓库 MIT；`firmware/lib/GxEPD2/` 是 vendored 的 GxEPD2 最小子集，沿用上游 GPL-3.0。


## 踩过的坑

- **manifest version 原本用时间戳**（`int(datetime.now().timestamp())`），每请求一次就变，设备会认为所有页面全变、
  反复重下全部位图。已改为按页面内容指纹算（配置或日期变了才变），见 `server/app.py::_page_version`。
  <!-- project: path:/home/open/.openclaw/workspace -->
- **算 version 时绝不能渲染页面**：渲染天气页会触发实时天气请求，把清单接口阻塞住（设备 HTTP 超时 15 秒，
  服务端一卡设备就拉不到）。指纹只用便宜的输入算（城市/坐标/标题/日期）。<!-- project: path:/home/open/.openclaw/workspace -->
- **模板里必须用相对路径**（`preview/x.png`、`api/config`）。写绝对路径 `/preview/x.png` 在公网会掉到站点根 404。
  <!-- project: path:/home/open/.openclaw/workspace -->
- **服务端不留写接口时，令牌那套是纯负担**：写操作去掉后 `write_token` 和鉴权一并删掉。
  <!-- project: path:/home/open/.openclaw/workspace -->
- **控制台配置项必须真能上屏**：屏标题要写进 manifest 的 native 页标题，否则配置存了也不生效。
  标题限 ASCII —— 设备屏没有中文字库。<!-- project: path:/home/open/.openclaw/workspace -->


## 渲染与量化（2026-11-18）

面板 GDEH029A1（2.9 寸黑白 296x128），**单色，无灰阶**。
灰度是**渲染手段**：源图留灰度，再量化成 1bpp。

**渲染：3 倍超采样。** 页面在 888x384 上画，BOX 降采样回 296x128。
1x -> 3x，PSNR 一致 +3 ~ +7.8 dB 且不增噪点（参考取 6x 渲染）。

**量化：硬阈值 165，不用 dither。** 三个指标一起看
（tools/eval_quant.py）：PSNR / 噪点% / 文字 1px 笔画%。

| 策略 | PSNR | 噪点% | 文字1px% |
|---|---|---|---|
| floyd 140 | 35.4 | 5.61 | 65 |
| bayer4 | 31.2 | 13.70 | 73 |
| hard 128 | 31.2 | 2.64 | 65 |
| **hard 165** | **28.1** | **0.22** | **38** |

dither 的 PSNR 最高但噪点打断 12px 小字笔画；页面没有连续色调区域，
灰阶收益换不来可读性。阈值高 = 笔画粗，165 是拐点。

**教训**：只用一个指标（PSNR）选量化策略会选错 —— PSNR 量的是色调
保真，屏上字读不出来就没意义。以后多指标一起看，别靠肉眼猜。

相关：[[eink-mini]] <!-- project: path:/home/open/.openclaw/workspace -->
