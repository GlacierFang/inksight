# eink-mini

2.9 寸黑白墨水屏桌面终端（ESP32-C3 Pro mini + GDEH029A1 / SSD1680 / 296x128）。
仓库：`git@github.com:GlacierFang/eink-mini.git`（GlacierFang/eink-mini），本地 `/home/open/.openclaw/workspace/eink-mini`。
<!-- project: path:/home/open/.openclaw/workspace/eink-mini -->

## 状态（2026-11-18）

- 固件：全部写完，CI 全绿。产物在 `fw-bin` 分支。<!-- project: path:/home/open/.openclaw/workspace -->
- 服务端：已上线 `http://ruohan.site/eink-mini/`（Flask + gunicorn，端口 18842，user scope systemd
  `openclaw-eink-mini.service`，nginx 路由 `/eink-mini/`）。<!-- project: path:/home/open/.openclaw/workspace -->
- 位序：服务端发 1=黑（4736 字节，MSB first），固件按 INK_SERVER_BIT1_IS_BLACK 翻成面板 1=白。
  自检工具 `server/tools/check_frame.py` 把 .bin 解回图验证。<!-- project: path:/home/open/.openclaw/workspace -->
- **留言功能整套已删**（提交 `660cf01`）：服务端 `GET/POST /api/message(s)`、`store.py`、
  `pages.render_message`、固件 `fetchMessage` / `collectStrings` / `inkRenderMessagePage` /
  `INK_PAGE_MESSAGE`、manifest 的 message 页、`write_token` 与写入鉴权，全部移除。
  <!-- project: path:/home/open/.openclaw/workspace -->
- **设备现在四页**：clock（native）、timer（native 子类型）、weather、calendar。<!-- project: path:/home/open/.openclaw/workspace -->
- **控制台改成功能分区式**（参考 ai-agent-pet 的 petd 后台范式）：设备状态（在线/最后活动/来源 IP/UA/
  拉取计数）、各功能页面预览、功能配置（天气城市 + 时钟屏标题）。<!-- project: path:/home/open/.openclaw/workspace -->
- **设备状态**：`server/device_state.py` 落盘记录（gunicorn 2 worker，内存态跨 worker 不可见），
  `GET /api/device` 暴露，3 分钟内有拉取算在线。<!-- project: path:/home/open/.openclaw/workspace -->
- RAM 30.2%、Flash 51.3%。<!-- project: path:/home/open/.openclaw/workspace -->
- **未上真机**：旋转方向、BUSY 极性、局刷残影、B 键实际脚位要实物核对。<!-- project: path:/home/open/.openclaw/workspace -->
- **天气上游 open-meteo 已通**（2026-11-18 实测：geocode + forecast 都能取到）。
  取数走后台线程 + 文件锁 + 落盘缓存，改城市后立即返回、后台强制刷新。<!-- project: path:/home/open/.openclaw/workspace -->

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

SCK=6, MOSI=7, MISO=8, CS=1（CS2 与 CS1 短接）, DC=4, RST=10, BUSY=3, KEY_A=5, KEY_B=9。
全部 3.3V。BUSY 极性上电探测 + 超时兜底，不写死。

**KEY_B 改成 GPIO9（板载 BOOT 键，零接线）**：GPIO11~17 在 ESP32-C3 上内部接 Flash，
模块根本没引出来，配成 16 固件连烧都烧不进去。只焊了一颗外接微动开关（A 键 GPIO5），
其余只买不焊留储备。想接第二颗外接开关用 GPIO2。<!-- project: path:/home/open/.openclaw/workspace -->

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
1x -> 3x，PSNR 一致 +3.2 ~ +7.9 dB 且不增噪点（参考取 6x 渲染）。

**量化：硬阈值 165，不用 dither。** 三个指标一起看
（tools/eval_quant.py）：PSNR / 噪点% / 文字 1px 笔画%。

| 策略 | PSNR | 噪点% | 文字1px% |
|---|---|---|---|
| floyd 140 | 34.97 | 5.67 | 64 |
| bayer4 | 30.94 | 13.73 | 72 |
| hard 128 | 31.26 | 2.56 | 64 |
| **hard 165** | **28.06** | **0.22** | **38** |

dither 的 PSNR 最高但噪点打断 12px 小字笔画；页面没有连续色调区域，
灰阶收益换不来可读性。阈值高 = 笔画粗，165 是拐点。

## 天气图标（2026-11-18）

**每个图标在自己的 28x28 局部画布上画**，量化成掩码后按位置叠进页面。
不在页面画布上直接画 —— 要带偏移，算错一次图标就飞（太阳飞到左上角
压住大温度）。局部画布把 bug 消灭在结构里。

**细致化靠负空间和构图，不靠灰度抖动。** 变体实测（1x 实际尺寸）：
实心剪影 0.10% / 掏白高光 0.11% / 纯灰阶抖动 14.71% / 轮廓+灰阶 23.67%。
**28x28 上抖动出来的是纹理不是灰度**，是散点不是层次。

**云上掏白试过三种，全是坑**：掏洞读作破洞、白块压顶断成两座山峰、
底部横槽读作横线。所以云不掏白。

实际采用：太阳掏空成环、太阳画在云后（云盖下半 = 从云后探出，
画反了读作太阳在云前面，天气含义就错）、云的弧 3 段加 4 段、
雨丝长短不一、雾中间虚线、雪花粗十字（六角星的 60 度斜线碎成乱线）。

**教训**：只用一个指标（PSNR）选量化策略会选错 —— PSNR 量的是色调
保真，屏上字读不出来就没意义。以后多指标一起看，别靠肉眼猜。
另一个教训：**"更细致"不等于"更多灰"**。小尺寸上 dither 加的是纹理，
负空间加的才是信息。

相关：[[eink-mini]] <!-- project: path:/home/open/.openclaw/workspace -->

## 计时器页（2026-11-18）

设备本地倒计时，native 子类型。服务端只加一条 manifest 条目，**不渲染、不产生位图**。

- **计时用 `millis()` 不用 epoch**：millis 单调递增，不依赖 SNTP 也不依赖 HTTP 校时，
  所以没校过时计时器照样能跑，比时钟页还独立。<!-- project: path:/home/open/.openclaw/workspace -->
- 状态只在 RAM，**不做持久化**（冰河大人 2026-11-18 定）。<!-- project: path:/home/open/.openclaw/workspace -->
- 复用已有的 `drawSevenDigit` / `drawSevenColon`，没写新渲染器。<!-- project: path:/home/open/.openclaw/workspace -->
- 预设固定在固件：1/3/5/10/15/25/30/45 分钟，默认 15。不接控制台 —— manifest 塞预设数组
  要改固件解析，改完还得等设备重拉 manifest 才生效。<!-- project: path:/home/open/.openclaw/workspace -->
- 按键：计时器页 B 短=启停/继续（DONE 后重开）、B 双=换预设（RUNNING 时忽略）、B 长=重置。
  其它页 B 键行为不变。<!-- project: path:/home/open/.openclaw/workspace -->
- 归零时一次全刷反色提醒（设备无蜂鸣器，视觉是唯一本地提醒）。<!-- project: path:/home/open/.openclaw/workspace -->

**踩到的坑**：`type == "native"` 的判断会漏掉 timer → 被当成 bitmap 页，每 30 秒去拉
`/api/page/timer.bin`（404）。控制台模板同样按 `f.type == 'native'` 分支，漏了 timer 会去请求
`preview/timer.png`。manifest 里 native 页标题取 `device_title` 是时钟页专属，timer 页不能套。
<!-- project: path:/home/open/.openclaw/workspace -->

## 主机端渲染器（2026-11-18）

`firmware/tools/render_host.sh` —— 在 PC / Pi 上直接编译固件的 `ink_canvas.cpp` +
`ink_pages.cpp` 并出图。<!-- project: path:/home/open/.openclaw/workspace -->

**这两份是纯逻辑，不依赖 Arduino API**（`ink_pages.cpp` 去掉了 `<Arduino.h>`，
它只用到 snprintf / gmtime_r / memset / uint32_t，全在标准头里），所以能在主机上编译运行，
画出来的就是设备上会显示的像素 —— **逐像素一致**，不是「长得像」。<!-- project: path:/home/open/.openclaw/workspace -->

三个用途：

1. **抓逻辑错** —— `fillRect` 那种语法没错、CI 编译通过、但画出来是竖线的 bug，
   只有出图才看得见。<!-- project: path:/home/open/.openclaw/workspace -->
2. **版式验证** —— 改完版式立刻出图，不用等硬件到货。<!-- project: path:/home/open/.openclaw/workspace -->
3. **控制台预览的素材来源** —— 同一份渲染代码，不是另画一遍。<!-- project: path:/home/open/.openclaw/workspace -->

输出 6 张：时钟（已校时 / 没校时）、计时器（READY / RUN / PAUSE / DONE）。
自检画面在 `main.cpp` 里（依赖 `g_display` / `g_net` 全局状态），这个工具不渲染它。
<!-- project: path:/home/open/.openclaw/workspace -->

## 固件逻辑错：fillRect 循环轴反了（2026-11-18 修）

```cpp
for (int j = 0; j < h; j++) vLine(x, y + j, w, black);   // 错
for (int j = 0; j < w; j++) vLine(x + j, y, h, black);   // 对
```

`vLine(x, y, h)` 是「第 x 列画 h 像素高」，所以原实现画出来是 1 像素宽的竖条，
不是 w×h 的矩形。`drawRect` 用的是 `hLine`+`vLine`，语义是对的 —— 两个函数不一致，
`fillRect` 这边是错的。<!-- project: path:/home/open/.openclaw/workspace -->

后果：数码管 7 根段全成竖线 → 时钟页数字、冒号、页脚方块、计时器档位方块、进度条、
自检 8×8 棋格全是竖条。**时钟页是第一页，真机首次点亮屏幕就是一堆竖线。**
<!-- project: path:/home/open/.openclaw/workspace -->

**CI 编译查不出来** —— 语法没错，逻辑错。只有出图（主机渲染器）才看得见。
<!-- project: path:/home/open/.openclaw/workspace -->

## 天气页屏上用专用简称（2026-11-18）

`WMO_CN` 是完整气象术语（给控制台和接口用），`WMO_SHORT` 是屏上用的短名。
**不是把长描述截断**（那会切出「强毛毛」这种怪词），是每个码单独设计：
55 强雨 / 56 冻雨 / 57 强冻雨 / 96 雷雹 / 99 雷暴。<!-- project: path:/home/open/.openclaw/workspace -->

**右栏必须防撞大温度**：城市名用户可改、长度不设上限，「乌鲁木齐 + 雷暴」加上
大温度后只剩 2 像素间隙（像素级实测）。现在先算温度块（含单位）右缘，
右栏左缘必须留 8px；放不下让城市名先让位，再逐档缩字号 16→12，还不行才截断。
<!-- project: path:/home/open/.openclaw/workspace -->

## 外部评审的处理方式

Gemini 两轮评审里，**两轮都重复提同一条误判**（platformio.ini 要补 lib_deps、
partitions.csv 要补全）—— GxEPD2 是 vendored 在 `firmware/lib/`，CI 实测编译通过；
照改反而会拉进第二份 GxEPD2 与 vendored 符号冲突，还破坏保留的上游 GPL-3.0 安排。
<!-- project: path:/home/open/.openclaw/workspace -->

核实结论存 `memory/projects/eink-mini-firmware-review.md`。**外部评审意见要逐条核实再采纳**，
不能直接照抄 —— 尤其它没读仓库实际结构时。<!-- project: path:/home/open/.openclaw/workspace -->


## native 页后台同步渲染（2026-11-18，用户授权后落地）

控制台原来对 clock / timer 只写「设备本地渲染，这里看不到画面」的占位。现在有真画面了。

技术底座是已有的主机端渲染器（编译固件那份 ink_canvas.cpp + ink_pages.cpp），
所以像素和设备上的一致，不是「长得像」。<!-- project: path:/home/open/.openclaw/workspace -->

新增 server/native_render.py：后台线程编译 + 渲染 + 落盘 PNG 缓存，请求只读缓存。
编译靠 flock 去重（gunicorn 2 个 worker 都会来抢）。单次渲染 4.2ms，不构成负担。
<!-- project: path:/home/open/.openclaw/workspace -->

**预览代表什么状态，必须说清楚：**
- 时钟页 = 服务端当前时间（时区同样 +8），与设备校时后一致。
- 计时器页 = **代表性画面**（READY / 15 分钟档）。真实倒计时在设备 RAM 里，
  服务端看不到也不假装看得到。/preview/timer.png?state=READY|RUN|PAUSE|DONE 按需渲染其它态。
<!-- project: path:/home/open/.openclaw/workspace -->

要显示真实剩余时间得扩展设备状态上报协议 —— 这是新功能，先问再动。

## 按键全部映射到外接微动开关（2026-11-18）

**两颗都是外接**，一脚接引脚一脚接 GND，固件用内部上拉：

| 键 | 引脚 | 短按 | 双击 | 长按 |
|---|---|---|---|---|
| A | GPIO5 | 下一页 | 上一页 | 立即全刷清残影 |
| B | GPIO2 | 刷新当前页 | 回第一页 | 进入配网 |

计时器页上 B 键是计时器专用：短按 启停 · 双击 换预设 · 长按 重置。
<!-- project: path:/home/open/.openclaw/workspace -->

**B 键从板载 BOOT（GPIO9）挪到 GPIO2。** GPIO9 是启动采样脚，复位时按住进下载模式，
留给下载，不当交互键。GPIO2 同样是启动采样脚 —— 固件开内部上拉，复位时保持高电平
正常启动，但上电/复位那一瞬别按住 B。<!-- project: path:/home/open/.openclaw/workspace -->

GPIO16 是确认错误的方向：C3 的 GPIO11~17 内部接 Flash，模块上根本没引出来。
<!-- project: path:/home/open/.openclaw/workspace -->

## 主机渲染器 getStr 的 static 缓冲区 bug（2026-11-18 修）

getStr 用单个 static char buf[128]，多个字符串参数会互相覆盖 ——
label= 解析后紧接着 state= 解析，label 会被 state 的值顶掉。
<!-- project: path:/home/open/.openclaw/workspace -->

改成 4 个轮换缓冲区。**这类「多个参数同时要活着」的坑只在多参数调用时才暴露**，
单参数测试永远测不出来。<!-- project: path:/home/open/.openclaw/workspace -->
