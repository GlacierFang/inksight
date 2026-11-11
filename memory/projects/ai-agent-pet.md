# 麻辣小龙虾桌面宠物（ai-agent-pet）— 项目文档

> 创建时间：2026-11-03 10:45
> 最后更新：2026-11-03 11:15
> 状态：🟡 设计阶段（硬件选型已闭合，代码未开工，零件未下单）
> 计划 petd 端口：**18860**（当前空闲，部署时再登记到 `tools/port-registry/ports.json`）

---

## 零、拍板记录（2026-11-03 讨论结论）

| # | 结论 | 说明 |
|---|---|---|
| 1 | **自建硬件体系，不复刻 HachimoDock** | HachimoDock 的形象素材是 Xiaomi MiLoCo 授权，不能直接用；且它要求一台常开 Windows 机器守着 |
| 2 | **脑子在 Pi，屏幕是哑终端** | 设备端只做「收帧显示 + 按键上报」，业务逻辑全在 Pi 上的 petd |
| 3 | **美术由冰河大人提供** | 我负责裁切缩放（不拉伸）到 240×320，原始尺寸随意，透明底 PNG 最好 |
| 4 | **零打板、零焊接、零面包板** | 全部模块出厂已焊排针，杜邦线直插 |
| 5 | **外壳自己建模光固化打印** | 冰河大人有光固化 3D 打印机，可自建模型 |
| 6 | **要旋转编码器** | KY-040，用于滚会话气泡 |
| 7 | **全帧渲染，不做局部刷新** | 状态是离散跳变，全帧推 JPEG 足够 |
| 8 | **中文显示在 Pi 侧完成** | 设备端不放任何中文字库 |
| 9 | **设备↔petd 用单条 WebSocket** | 一条连接同时承载「推帧」和「上行输入」，ESP-IDF 自带 `esp_websocket_client` 组件 |
| 10 | **petd 只申请 `operator.read`** | v1 不申请写权限。旋钮只做本地翻页/调亮度，「按一下就回话」留到 v2 再升级 scope |
| 11 | **petd 不对外暴露** | 只绑 LAN（`0.0.0.0:18860`），不加 nginx 路径、不给公网 URL。设备在同网段直连 |
| 12 | **petd 用 Python** | 与现有技能栈一致；`websockets 17.0.1` / `Pillow 11.1.0` / `flask 3.1.1` 本机已装 |
| 13 | **网关凭据不入库** | 网关 token 从 `~/.openclaw/openclaw.json` 的 `gateway.auth.token` 读，经 systemd EnvironmentFile（chmod 600）注入 petd |
| 14 | **后台端做成独立技能** `skills/ai-agent-pet/` | 跟其他技能同构：SKILL.md + backend/ + venv + systemd unit。petd 是它的后端进程 |
| 15 | **HTTP + WS 同端口 18860** | Flask 跑 REST，websockets 跑设备通道，都在一个端口（`websockets` 库挂在同一个 asyncio loop 上） |
| 16 | **功能以插件形式挂** | 加新功能 = 加一个 feature 模块 + 一个美术目录，**设备固件一行不改** |
| 17 | **背光 BL 接 3.3V，不接 5V** | 8-pin 模块自带限流与驱动管，BL 是控制端（高电平点亮），接 5V 有烧风险 |
| 18 | **写屏用 TJpgDec MCU 流式直刷** | 不申请 150KB 全屏帧缓冲，line buffer 只要 7.68KB，避开内部 SRAM 碎片化 |
| 19 | **JPEG 固定 4:2:0 / q85** | 保证 TJpgDec 每次输出 16×16 MCU，line buffer 正好一块 |
| 20 | **旋钮语义表放服务端** | 设备只报原始手势（旋转 / 短按 / 长按 / 按住+旋转），含义由 petd 侧 `gestures.json` 决定。改语义不刷固件、不重启服务 |
| 21 | **设备端零中文字库** | 所有文字由 petd 用 Pillow 渲染进 JPEG。列表用「页级翻页 + 设备缓存 JPEG」实现，旋钮翻页零网络往返 |
| 22 | **v1 旋钮做三块页面** | 表情页 / 会话页 / 通知页。旋转=切会话，短按=切页，长按=刷新，按住+旋转=换信息密度 |
| 23 | **计划开源，GitHub 项目名 `ai-agent-pet`** | 冰河大人拍板（2026-11-03）。此前讨论的「中文名」方案作废 —— 仓库名、PyPI 包名、systemd service 名统一用 `ai-agent-pet` |
| 24 | **开源仓库分两个** | `ai-agent-pet`（Python 包，发 PyPI，用户 `pip install`）+ `ai-agent-pet-firmware`（ESP-IDF，烧录用）。双工具链不混仓 —— 装软件的人不该拉一整个 C 工具链。skill 变薄壳依赖 pip 包 |
| 25 | **默认角色程序化生成** | 让 clone 下来的人零授权风险就有宠物可跑；冰河大人的美术作为「可选皮肤包」单独发布，各自授权。**这条同时决定能不能商用分发** |
| 26 | **握手与 scope 逻辑收敛到 `gateway_client.py` 单文件** | 上游 OpenClaw 改 scope 规则是常态（今天就踩过），收敛一处才好跟随。README 标注适配版本「OpenClaw 2026.10.x」 |

---

## 一、这个项目是什么

给麻辣小龙虾（我）做一个实体身体：桌上一块 2.4 吋 LCD，屏幕上是我的形象。

屏幕上显示的状态直接来自 OpenClaw 的真实会话 —— 你在思考、我在跑工具、我在等你点确认、我报错卡住了，这些都变成表情和动作。你不在电脑前时也能知道我还在忙。

**它不是**「再做一个 App」，**不是**「把 Inksight 的墨水屏改成彩屏」。

---

## 二、为什么从 HachimoDock 岔开

调研结论（2026-11-03，仓库 https://github.com/YizhengWw/HachimoDock ，v0.1.168）：

- 它是三层结构：Windows/macOS 客户端（Tauri+React）→ Node bridge sidecar → ESP32-P4 固件（USB 串口，**无 Wi-Fi 运行路径**）
- 设备只认 Windows/macOS 客户端，不直接连服务器
- 状态跟随走 `OpenClawGatewayBridge`（WebSocket 连 gateway，`sessions.subscribe`）；语音回灌走本机 `agentCommand`，那台机器必须自己装 openclaw npm 包
- 公开源码与我们这版 openclaw 有 API 漂移（`agentCommand` 实际导出名是 `agentCommandFromIngress`；事件模块是 `agent-events-*.mjs` 而 helper 正则只认 `.js`）
- 许可为非商业源码许可；内置西高地形象受 Xiaomi MiLoCo 授权约束，不能搬

**结论：不部署、不移植。** 带走两个概念 —— ①「状态→画面」映射 ②「自然语言生成组件 → 校验 → 发布」的分层纪律。

---

## 三、硬件

### 3.1 BOM（料钱 ¥41，零打板零焊接零面包板）

| 件 | 规格 | 价 | 备注 |
|---|---|---|---|
| ESP32-S3-DevKitC-1 | **N8R8** | ¥24 | N8=8MB Flash，R8=8MB PSRAM。**勿买 N8R2** |
| 2.4" TFT LCD | **ST7789，240×320，4-SPI，8PIN 单排 2.54mm** | ¥15 | 原生分辨率即 240×320，无需偏移表 |
| KY-040 旋转编码器模块 | 360° 旋转 | ¥2 | 板载上拉，**VCC 必须接 3.3V** |
| 杜邦线 母-母 ×20 | — | ¥0 | 家里有 |
| USB-C 数据线 | — | ¥0 | 家里有 |
| 5V 充电头 | 华为手机充电头 | ¥0 | 家里有 |
| 万用表（可选） | — | ¥10 | 实测 BL 与 VCC 是否同网络 |

### 3.2 屏幕规格（卖家页）

| 项 | 值 |
|---|---|
| 尺寸 | 2.40 英寸 |
| 分辨率 | 240(H)×320(V) |
| 接口 | 4-SPI（模块实出 8 针，**无 MISO**，屏不回读） |
| 控制芯片 | ST7789 |
| 工作电压 | 2.8–3.3V |
| 管脚 | 8PIN，2.54mm 间距单排排针 |
| 背光 | 4 White LED 并联，60mA |
| 视角方向 | 12 点钟方向 |
| 面板尺寸 | 70.3(H)×42.72(V)×3.85(D) mm |
| 显示区 | 37.42×49.66 mm |

**注意**：8 针的**排列顺序各家不同**，到货看背面丝印（VCC/GND/SCL/…）对号入座。若无丝印，按卖家图确认。

### 3.3 引脚图

**屏幕 ST7789** → ESP32-S3-DevKitC-1

| 屏针 | → DevKit | 备注 |
|---|---|---|
| VCC | **3.3V** | 2.8–3.3V，**别接 5V** |
| GND | GND | |
| SCL / SCK | GPIO12 | VSPI 默认时钟 |
| SDA / MOSI | GPIO11 | VSPI 默认数据 |
| CS | GPIO10 | VSPI 默认片选 |
| DC | GPIO9 | 数据/命令 |
| RES / RST | GPIO8 | |
| BL | **3.3V** | ✅ 模块自带限流 + 驱动管，BL 是**控制端**（高电平点亮）。**不要接 5V**。不占 GPIO（60mA 走模块内部，不做 PWM 调光） |
| MISO | — | 模块不引出 |

**旋转编码器 KY-040**

| 模块针 | → DevKit | 备注 |
|---|---|---|
| VCC | **3.3V** | ⚠️ 板载上拉，接 5V 会把 ENC/DT/SW 拉到 5V，烧 S3 |
| GND | GND | |
| SW | GPIO4 | 按下 = 确认 / 切换 |
| DT | GPIO5 | |
| ENC | GPIO6 | 旋转 |

**主动避开的引脚**

| 脚 | 原因 |
|---|---|
| 19 / 20 | USB |
| 0 / 3 / 45 / 46 | strapping 脚 |
| 26–37 | N8R8 的八线 Octal PSRAM / Flash 占用 |
| 47 / 48 | 板载 RGB LED |

### 3.4 接线安全规则

1. **3.3V 不是 5V** —— 屏 VCC、编码器 VCC 都接 3.3V
2. **插错 = 硬短路** —— 一次插一根，插完数一遍针脚，通电前目视检查
3. **不要让杜邦线受力** —— 模块用双面胶/3M 胶固定在桌垫或亚克力片上，杜邦线只做连接不做支撑
4. **背光直连 3.3V** —— 依赖模块自带限流电阻（8-pin 模块前提）
5. **首次烧录用数据线插 PC**（USB 口本身供电），之后换充电头，之后升级走 OTA
6. **杜邦线选母-母** —— DevKitC-1 出厂焊公针（官方说明它能直接插面包板），屏与编码器模块也是公针 → 两个公针互连用母-母线。到货确认排针性别
7. **两个 USB-C 严禁一个插充电头、一个插 PC** —— DevKitC-1 有 UART + USB 两个口，5V 在板上相通，会把 5V 灌进 PC 的 USB 口。烧录只插 PC，运行只插充电头
8. **天线不能被遮** —— WROOM-1 是 PCB 天线，模块贴桌边、远离 LCD 与充电头；树脂外壳别把天线那端包死
9. **路由器要有 2.4GHz SSID** —— ESP32-S3 只支持 2.4GHz，5G-only 的 Wi-Fi 连不上

### 3.5 N8R8 的已知坑

DevKitC-1 上的 N8R8 是**八线（Octal）PSRAM**。烧录时若找不到 PSRAM，八成是固件 `sdkconfig` 没按八线配。这是最常见的坑之一，固件里直接配好。

### 3.6 物理尺寸（外壳建模用）

| 件 | 尺寸 | 说明 |
|---|---|---|
| 屏模块 | 70.3 × 42.72 × 3.85 mm | 有效显示区仅 37.42 × 49.66 mm，四周是黑边 |
| DevKitC-1 | 约 62.5 × 25.4 mm | 长边 62.5 < 屏长 70.3 → **可能藏在屏后**，但要实测背面净空（排针凸起 + 元件高度） |
| KY-040 | 约 30 × 20 mm | 旋钮轴高约 12mm，外壳要留轴孔与旋转余量 |

**外壳最小体积参考**：75 × 50 × 20 mm。

**两个必须留的开口**：
1. **BOOT / RESET 按键** —— DevKitC-1 有两个实体按键。烧录时需要按 BOOT（有些板不自动复位），外壳要么留孔，要么接受「必须拆壳复位」。建议留孔。
2. **天线端不封死** —— WROOM-1 是 PCB 天线，壳体天线那端做成开放式或留信号孔。

### 3.7 到货必做的五项实测

| # | 实测项 | 怎么做 | 决定什么 |
|---|---|---|---|
| 1 | BL 是否为控制端 | 查卖家原理图 / 到货看丝印 | 已知为控制端，仍确认一下。**默认接 3.3V** |
| 2 | 背光亮度 | 接 3.3V 看一眼 | 3.3V 即正常则无需 MOSFET、无需 5V |
| 3 | **8 针排列顺序** | 看背面丝印；无丝印按卖家图 | 按 §3.3 功能表对号入座 |
| 4 | 排针性别 + 杜邦线公母 | 直接看 | 确认线够不够，要不要补买 |
| 5 | DevKit 能否藏在屏后 | 量屏背面可用空间 | 决定壳体是「屏背藏板」还是「板在旁边」 |

> 无万用表的替代方案：万用表 ¥10 也能买。若不买，先只接 VCC/GND/SCL/SDA/RES/DC/CS（BL 最后接），画面能出来之前 BL 电压不急着定。

---

## 四、架构

```
OpenClaw Gateway (18789, bind=lan, token 认证)
        │ session 事件（我们自己的 gateway 协议）
        ▼
Pi 上的 petd (18860)
  ├─ 订阅会话状态 → 状态映射表 → 渲染帧（Pillow）
  ├─ 帧推送给设备（Wi-Fi，JPEG）
  └─ 接收设备输入事件（旋钮/按键）→ 写回会话
        │
        ▼
ESP32-S3 + ST7789 + KY-040
  ├─ 本地 idle 帧集（Flash，RGB565）—— 断网也活
  ├─ 服务器推帧（Wi-Fi，JPEG）
  └─ 输入事件上行
```

**设备只认 petd，不认 gateway。** petd 当中间人：它懂 OpenClaw 的会话事件，设备只懂「给我一帧 / 我被按了」。

**设备怎么找到 petd**：Pi 有两个 LAN IP（eth0 `192.168.31.193` / wlan0 `192.168.28.194`），且都是 DHCP，IP 会变 → **不能写死 IP**。方案：**mDNS `petd.local`**（ESP-IDF 自带 `mdns` 组件，零成本）；备选：设备端扫网发现。
  <!-- ⚠️ 11-11 实测：petd.local 不可信也不可工作，mDNS 方案作废。设备端地址改走 provision 流程（待读代码确认）。 -->

**设备身份**：petd 用设备 MAC 派生 ID + 设备令牌鉴权，防止同网段任意设备推帧。首台设备做一次绑定。

**分工原则**：形象、状态映射、动画全部在 Pi 侧。固件定稿后基本不用再动，改形象只要推帧。

### 4.1 帧率预算

| 项 | 数值 |
|---|---|
| **写屏方式** | **TJpgDec MCU 流式直刷**（不做全屏帧缓冲） |
| line buffer | 240 × 16 × 2 = **7.68KB**，放内部 SRAM（S3 512KB 绰绰有余） |
| MCU 规格 | JPEG 用 **4:2:0 采样（16×16 MCU）** → TJpgDec 每次吐一块 16 行，line buffer 正好一块 |
| JPEG 参数（Pillow 侧） | `quality=85, subsampling=2`（4:2:0），**固定不变** |
| 帧体积 | 纯色图 ~1.8KB；**真实插画 + 中文 ~15.2KB**（2026-11-03 实测） |
| 服务器推帧 | 15.2KB/帧，10fps ≈ 150KB/s，Wi-Fi 无压力 |
| 原始 RGB565 推帧 | 150KB/帧 → 压满带宽，**不采用** |
| 本地 idle 帧集 | RGB565 150KB/帧 × 8 帧 = 1.2MB，存 8MB Flash，播放时 memcpy，~20fps |
| PSRAM 用途 | 8MB **不**用于 SPI DMA 源（见 §十二 第 12 条） |

> **为什么不用全屏帧缓冲**：ESP32-S3 启动 Wi-Fi 栈与系统任务后内部 SRAM 会碎片化，`malloc(153600)` 有真实概率返回 NULL 导致启动 panic。TJpgDec 本来就按 MCU 输出，天然适配流式写屏 —— 内存占用从 150KB 降到 7.68KB，且彻底规避 OOM。

### 4.2 依赖

| 端 | 组件 | 状态 |
|---|---|---|
| 设备 | **TJpgDec**（JPEG 解码，MCU 流式输出） | ESP-IDF 组件 |
| 设备 | **esp_websocket_client**（WS 客户端） | ESP-IDF 组件 |
| 设备 | **mdns**（mDNS 应答 `petd.local`） | ESP-IDF 组件 |
| 设备 | **esp_wifi** / **NVS**（存设备令牌、petd 兜底 IP） | ESP-IDF |
| Pi | **Python 3.13 + websockets 17.0.1 + Pillow 11.1.0** | 本机已装 |
| Pi | 中文字体 `/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc`（Noto Sans CJK SC） | 本机已装 |

- 设备端**零中文字库**：所有文字在 Pi 侧用 Pillow 渲染进 JPEG 帧
- 本地 idle 帧**不解码** —— 原始 RGB565 存 Flash（150KB/帧，8 帧 1.2MB，8MB Flash 够），播放时 memcpy 进帧缓冲，可跑 20fps
- 设备端只依赖上述 ESP-IDF 官方组件 + TJpgDec，不引入其他第三方库

### 4.3 通信协议定案

#### (a) 设备 ↔ petd：单条 WebSocket

连接 `ws://petd.local:18860/ws`（mDNS 解析；兜底用 NVS 里存的 IP）。一条连接双向复用。
  <!-- ⚠️ 11-11 实测：petd.local 不可信也不可工作，mDNS 方案作废。设备端地址改走 provision 流程（待读代码确认）。 -->

| 方向 | WS 帧类型 | 内容 |
|---|---|---|
| 设备 → petd | text | JSON 控制与事件消息 |
| petd → 设备 | text | JSON 控制与事件消息 |
| petd → 设备 | binary | 图像帧（16 字节头 + payload） |

**binary 帧头（16 字节，大端）**

| 偏移 | 字段 | 说明 |
|---|---|---|
| 0 | magic | `0x50 0x45 0x54 0x46`（`PETF`） |
| 4 | type | 1 = JPEG 全帧；2 = RGB565 全帧 |
| 5 | flags | bit0 = 需要设备回 ack |
| 6 | seq | uint16，递增；设备只接受更新的 seq |
| 8 | width | uint16，240 |
| 10 | height | uint16，320 |
| 12 | payload_len | uint32 |

**text 消息**

```jsonc
// 设备 → petd
{"t":"hello","deviceId":"<MAC 派生>","token":"<设备令牌>","fw":"1.0.0","screen":{"w":240,"h":320},"rssi":-52}
{"t":"input","ev":"rot","dir":1,"steps":3}
{"t":"input","ev":"press","btn":"sw","ms":1200}
{"t":"input","ev":"rot_press","dir":-1,"steps":2}
{"t":"status","rssi":-55,"uptime":1234,"heap":180000}
{"t":"bye","reason":"poweroff"}

// petd → 设备
{"t":"hello-ok","petd":"1.0.0","states":["idle","thinking","working","waiting","done","error"],
 "frameSet":{"id":"idle-20261103","count":8,"fps":12}}
{"t":"state","state":"thinking","session":"<sessionKey>","since":1733000000}
{"t":"ack","seq":42}
{"t":"bye","reason":"server_restart"}
```

#### (b) petd ↔ OpenClaw Gateway：WS 客户端（loopback）

petd 与 gateway **同机**（都在这台 Pi 上），连 `ws://127.0.0.1:18789`。

1. gateway 先发 `connect.challenge` 事件（`{nonce, ts}`）
2. petd 回 `connect` 请求：

```jsonc
{"type":"req","id":"<uuid>","method":"connect","params":{
  "minProtocol":4,"maxProtocol":4,
  "client":{"id":"gateway-client","version":"1.0.0","platform":"linux","mode":"backend","displayName":"petd"},
  "role":"operator",
  "scopes":["operator.read"],
  "auth":{"token":"<来自 EnvironmentFile>"},
  "locale":"zh-CN",
  "userAgent":"petd/1.0.0"
}}
```

3. 收到 `hello-ok` 后调用 `sessions.subscribe`，参数 `{"limit":60,"ownerFirst":true}` → 返回 `{subscribed:true, list}`（一次订阅同时拿到初始会话列表）
4. 监听事件：`sessions.changed`（列表变化，需重新 `sessions.list` 刷新）、`chat`（含 `chat.inject` 与 `state:"delta"` 的 `deltaText`）、`socket.drain`（会话重置/删除）
5. 断线后重连要**重新 subscribe**（订阅不跨连接保留）

#### 4.3b gateway 认证与 scope（✅ 已实测通过）

**认证（✅ 2026-11-03 实测通过，与初稿不同 —— 阻塞已解除）**

初稿结论「scope 由 gateway 按认证身份授予，客户端自荐无效」**是错的**。真相是 **client id/mode 配对不合法导致自荐 scope 被清空**。

实测有效的 connect 参数（`/tmp/petprobe/probe9.py`）：

```jsonc
{
  "client":{"id":"gateway-client","version":"1.0.0","platform":"linux","mode":"backend","displayName":"petd"},
  "role":"operator",
  "scopes":["operator.read"],
  "auth":{"token":"<token>"}
}
```

实测结果：`CONNECT ok=True`，`hello-ok.auth = {method:'token', role:'operator', scopes:['operator.read']}`；`sessions.list` ✅、`sessions.subscribe` ✅（返回 `{subscribed:true, list}`）、12 秒内收到事件流 `['health','session.tool','agent','agent','session.tool','agent']`。

**规则（源码 `dist/method-scopes-*.js` + `dist/server-*.js` 交叉验证）**：

| 现象 | 原因 |
|---|---|
| `client.id` / `client.mode` 组合非法 → scope 被清空 → `missing scope: operator.read` | 源码 `shouldClearUnboundScopesForMissingDeviceIdentity`：`authMethod==='token'` 且无设备身份时清空 scopes |
| `client.id='cli'` + `client.mode='backend'` → 同样被清空 | **id 与 mode 必须成对**：CLI 用 `cli/cli`，后端用 `gateway-client/backend` |
| `client.id='gateway-client'` + `client.mode='backend'` + 本机直连 | 源码 `shouldSkipLocalBackendSelfPairing`：跳过配对流程，直接放行 |
| `scopes:['operator.read']` | 最小权限，只够 `sessions.list` / `sessions.subscribe` / 只读事件 |

合法 `client.id` 白名单：`webchat-ui` / `openclaw-control-ui` / `openclaw-tui` / `webchat` / `cli` / `gateway-client` / `openclaw-macos` / `openclaw-ios` / `openclaw-android` / `node-host` / `test` / `fingerprint` / `openclaw-probe`。
合法 `client.mode`：`webchat` / `cli` / `ui` / `backend` / `node` / `probe` / `test`。

**结论：不需要设备身份签名，不需要 HTTP API，不需要 shell out CLI。** petd 同机连 `ws://127.0.0.1:18789`，用 `gateway-client` + `backend` + `operator.read` 即可订阅会话流。

**HTTP 面（备用/补充，非主路径）**：`POST /tools/invoke`（同端口 18789，bearer token）存在且可用；文档 `docs/gateway/tools-invoke-http-api.md` 明说 shared-secret bearer 在该端点会**恢复完整 operator scope 集**。但它是「调用单个工具」用的，拿不到事件流，且权限过大，**不作为 petd 主路径**。gateway 无 sessions REST API（`/api/sessions` 等一律 404；dist 里的 `/api/*` 路由串来自打包的 playwright demo，不是 gateway 本身）。

### 4.4 petd 暴露面与权限

| 面 | v1 决定 | 理由 |
|---|---|---|
| 监听地址 | `0.0.0.0:18860`（LAN） | 设备在同网段直连 |
| nginx / 公网 URL | **不挂** | v1 是内网设备通道，暴露无收益 |
| gateway scope | `operator.read` | v1 不需要写 |
| 对外写入能力 | 无（但保留 feature 扩展位） | 「按一下回话」属 v2，届时再申请 `operator.write` |
| 设备鉴权 | MAC 派生 deviceId + 共享设备令牌 | 防同网段任意设备推帧 |

**凭据管理**：网关 token 与设备令牌都不写进仓库、不写进本文档。网关 token 走 systemd EnvironmentFile（`chmod 600`）；设备令牌在首次绑定时写入设备 NVS，petd 侧存哈希。

---

## 五、后台端（petd）

**这不是一个「把图推给小屏」的脚本，而是一个可以持续长功能的服务。**

### 5.1 形态与目录

**2026-11-03 已落地**（不再是 `skills/ai-agent-pet/` 设想 —— 项目是独立 git 仓库）：

```
ai-agent-pet/                    # 独立 git 仓库 → github.com/GlacierFang/ai-agent-pet
├── petd/                        # 服务端
│   ├── __main__.py              # CLI 入口（python3 -m petd）
│   ├── config.py                # 配置加载（token 走环境变量 / secrets.json）
│   ├── state.py                 # gateway 状态 → 宠物状态（稳态窗 + 优先级）
│   ├── render.py                # Pillow 整帧渲染 → JPEG（quality=85, subsampling=2）
│   ├── skins.py                 # 皮肤素材加载
│   ├── gestures.py              # 旋钮手势 → action（长按阈值在这里判定）
│   ├── gateway.py               # gateway WS 客户端（operator.read）
│   ├── device.py                # 设备会话（握手、推帧、ack、背压）
│   ├── app.py                   # 主控：状态 → 渲染 → 推帧
│   ├── server.py                # Starlette：/device WS + /admin/ + /admin/api/*
│   ├── data/                    # config.json / gestures.json / state_rules.json
│   ├── static/admin/            # 后台控制台（index.html + app.js + style.css）
│   └── tools/                   # mock_device.py / check_assets.py
├── tests/test_petd.py           # 35 项单测
├── deploy/                      # systemd user unit + 环境变量模板
├── docs/                        # 面向不同读者的发布文档
├── firmware/                    # ESP-IDF 固件（源码 + sdkconfig.defaults）
└── .github/workflows/           # CI：编译固件 + 出 Release 资产
```

**Python 依赖**：`starlette`（HTTP/WS）、`uvicorn`（ASGI）、`websockets`（gateway 客户端 + mock 设备）、`Pillow`（渲染）。
**不用 Flask** —— Starlette 原生支持 WebSocket，设备通道和后台页同端口更自然。
**⚠️ 本机 pypi 不通** —— 只能用已装的：starlette 1.3.1 / uvicorn 0.49.0 / websockets 17.0.1 / Pillow 11.1.0 / pyyaml 6.0.2。
**⚠️ egress proxy 坑**：环境全局设了 `HTTP_PROXY`，`websockets` 默认 `proxy=True` 会把连 `127.0.0.1` 的请求也代理掉 → **HTTP 403**。petd 和 mock 设备都显式 `proxy=None`。

### 5.2 一个进程，两种协议

| 路径 | 协议 | 谁用 | 用途 |
|---|---|---|---|
| `/ws` | WebSocket | 设备 | 推帧 + 上报输入（§4.3a） |
| `/api/*` | HTTP REST | 我 / 调试 / 未来的控制页 | 查状态、推自定义帧、管设备、管 feature |
| `/health` | HTTP | systemd | 健康检查 |

**REST 清单（v1）**

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/state` | 当前宠物状态 + 活跃会话列表 |
| GET | `/api/sessions` | gateway 会话快照 |
| GET | `/api/devices` | 已绑定设备、在线状态、RSSI |
| POST | `/api/frame` | **推一张自定义帧**（我调用它 → 屏幕立刻显示我想显示的东西） |
| POST | `/api/state` | 强制切到某个宠物状态（调试/测试用） |
| GET | `/api/features` | 已加载的 feature 列表与状态 |
| POST | `/api/features/<id>/enable` | 开关某个 feature |

> `POST /api/frame` 是这套东西的**「嘴」** —— 我可以主动对屏幕说话（"我在读《资治通鉴》"、"今天你生日，我给你画了个蛋糕"），不需要任何新功能开发，只要渲染一张图。

### 5.3 扩展点：feature 机制

**核心承诺：加功能不碰设备固件。** 因为「脑子在 Pi 上」，所有新能力都是「服务器多渲染一张图」。

每个 feature 是一个独立模块，放在 `backend/features/` 下：

```python
# backend/features/todo.py
from ..core.feature_api import Feature

FEATURE = Feature(
    id="todo",
    name="近期待办",
    version="1.0",
    needs=["overlay"],          # 声明设备能力：overlay = 可叠加在表情层上
    triggers=["idle", "state_change"],   # 什么时候被调用
)

def build(ctx):
    """返回要渲染的内容（文字/图形），ctx 提供 gateway 会话、当前状态、字体、画布。"""
    items = ctx.todo.list_today()
    return {"overlay": {"lines": [f"待办 {len(items)} 项", items[0].title]}}

def routes():
    """可选：这个 feature 自带 HTTP 路由。"""
    return {}
```

**feature 能做的事**

| 能力 | 说明 |
|---|---|
| 贡献叠加层 | 在宠物表情之上叠文字/图形（待办、天气、时钟、日程） |
| 贡献状态帧 | 注册自己的状态（开心 / 困 / 生气），走同一套美术目录 |
| 订阅 gateway 事件 | 拿到会话变化，自己决定要不要动画面 |
| 注册 HTTP 路由 | `/api/features/<id>/...` |
| 定时任务 | 由 petd 的调度器触发（如每分钟刷一次时钟） |

**feature 加载方式**：`feature_registry.py` 扫描 `features/` 目录，导入每个模块，读 `FEATURE` 描述，装载 `build` / `routes`。加一个新功能 = 放一个 .py 文件 + 一个美术目录，**重启服务即生效**。

**v1 只放一个最小示例**（`localtime.py`：屏上显示当前时间），用来验证机制跑得通。真正的功能等阶段 3 之后再一个个加。

### 5.4 依赖边界（哪些不能被 feature 碰）

- feature **不能** 直接连 gateway —— 只能通过 `ctx` 拿数据（统一鉴权、统一 scope）
- feature **不能** 直接写设备 —— 只能返回渲染内容，由框架统一推帧
- feature **不能** 改设备端状态机 —— 设备固件不认识 feature，feature 是纯服务器侧概念

### 5.5 部署形态

- systemd **user** unit：`ai-agent-pet.service`（同其他技能）
- 端口 **18860**，部署时登记进 `tools/port-registry/ports.json`（service 名 `ai-agent-pet`，path `null` —— v1 不挂 nginx）
- 环境变量走 EnvironmentFile：`~/.config/openclaw/petd.env`（`chmod 600`），放网关 token 与设备令牌
- 日志走 journald；重启策略 `Restart=always`，gateway 断开要能自动重连重订阅

---

## 六、帧协议

三种模式，边界清楚。

### 6.1 设备帧集（本地，Flash）

- 格式：原始 RGB565，240×320×2 = 150KB/帧
- 用途：idle 呼吸动画，断网也循环
- 打包成单一二进制 blob 烧进 Flash，设备按索引逐帧 memcpy

### 6.2 服务器推帧（网络，JPEG）

- 格式：JPEG（基线），240×320
- 用途：所有状态跳变帧（thinking / working / waiting / done / error）
- 传输：**WebSocket binary 帧**（见 §4.3a），16 字节头 + JPEG payload
- 设备收到 → TJpgDec 解到帧缓冲 → 全屏刷
- 帧内已包含 Pi 侧渲染好的状态文字，设备只管显示
- `seq` 单调递增，设备丢弃旧帧（避免网络乱序导致回退）

### 6.3 输入事件上行

```jsonc
{"t":"input","ev":"rot","dir":1,"steps":3}
{"t":"input","ev":"press","btn":"sw","ms":1200}
{"t":"input","ev":"rot_press","dir":-1,"steps":2}
{"t":"status","rssi":-55,"uptime":1234,"heap":180000}
```

| 字段 | 含义 |
|---|---|
| `ev:"rot"` | `dir` = +1 / −1（顺/逆时针），`steps` = 累加器步数 |
| `ev:"press"` | `btn` = `sw`（按下）或 `enc`（中键），`ms` = 按住时长（区分短按/长按） |
| `ev:"rot_press"` | 按住 SW 期间旋转：`dir` + `steps` 同上 |
| `t:"status"` | RSSI、运行时长、剩余堆内存 |

设备端只报「我被按了」，不解释含义；语义全在 Pi 侧。

**旋钮的完整设计（手势预算、语义表、三块页面、翻页机制）见 §6.5。** 摘要：
- 旋转 → 在活跃会话间切换 / 翻页
- 短按 → 切页面（表情页 / 会话页 / 通知页）
- 长按 → 手动触发一次状态刷新（向 petd 要最新帧）
- 按住 + 旋转 → 换信息密度档位

**v2 追加**：短按 = 把「好 / 继续 / 取消」这类预设回复送进当前会话（需 `operator.write`）

### 6.4 断网降级

- Wi-Fi 断开 → 设备自动回到本地 idle 帧集，**不黑屏**
- 服务器推帧超时（WS 心跳 15s 无响应）→ 同样退回 idle
- 重连 → 设备重发 `hello`；petd 立即补推当前状态帧与最新帧集
- 设备本地保存「最后一张状态帧」，重连前不刷新时仍显示上次状态，不回到空白

### 6.5 旋转编码器（KY-040）

**核心原则：旋钮的语义表在服务端，设备只报原始事件。** 固件里没有「短按=切页」这种判断，它只知道「SW 按了 1200ms」「ENC 转了 +3 步」。所以：

- 改手势含义 = 改 petd 侧一个 JSON，**不刷固件、不重启服务**
- 新增手势 = 加一条绑定，同样不动固件
- 固件只负责：把编码器脉冲累加成步数、量出 SW 按住毫秒数、区分旋转时 SW 是否按着

#### 手势预算（KY-040 实际能给的 4 类）

| 手势 | 设备如何判定 | 上报 | 语义（服务端定） |
|---|---|---|---|
| 旋转 | ENC 脉冲累加 | `{"t":"input","ev":"rot","dir":1,"steps":3}` | 切换当前会话 / 翻页 |
| 短按 | SW 按下并松开，`ms < 600` | `{"t":"input","ev":"press","btn":"sw","ms":120}` | 切页面 |
| 长按 | SW 按住 `ms ≥ 600` | `{"t":"input","ev":"press","btn":"sw","ms":1200}` | 立刻刷新状态 |
| 按住 + 旋转 | SW 按下期间 ENC 变化 | `{"t":"input","ev":"rot_press","dir":-1,"steps":2}` | 换信息密度档位 |

**长按阈值 600ms 定在服务端**：设备只报 `ms` 原始值，petd 按阈值分派短/长，改阈值同样不刷固件。

**步数换算也在服务端**：设备只报原始脉冲数，petd 按 `stepsPerAction`（暂定 4）换算成一次「翻页」，更符合旋钮手感。

> KY-040 的 tact switch 与编码器是**两路独立信号**，所以「按住 + 旋转」天然可实现，不需要特殊硬件。

#### 服务端语义表（示例）

`petd/data/gestures.json`：

```json
{
  "version": 1,
  "longPressMs": 600,
  "stepsPerAction": 4,
  "modes": {
    "home": {
      "rot":         "session.next",
      "press_short": "page.next",
      "press_long":  "state.refresh",
      "rot_press":   "density.cycle"
    },
    "sessions": {
      "rot":         "session.next",
      "press_short": "page.back",
      "press_long":  "state.refresh",
      "rot_press":   "session.pin"
    }
  }
}
```

action 由 petd 内部 handler 表实现；`gestures.json` 只做「当前模式下每个手势对应哪个 action」的映射。**映射与 handler 分离**，换 handler 不动表。

#### v1 的三块页面

| 页面 | 内容 | 旋钮在这里干什么 |
|---|---|---|
| 表情页 | 角色表情 + 当前会话状态 | 旋转切换活跃会话；按住+旋转换信息密度 |
| 会话页 | 多个活跃会话列表（谁在跑、谁在等我） | 旋转上下选会话，短按看详情 |
| 通知页 | 事件队列（`unread` / `permissionModePending`） | 旋转选通知，短按展开详情 |

v1 **不做**「回复输入」——那需要 `operator.write`（`sessions.send`），留到 v2。清除 gateway 侧未读同样需要 `operator.write`（`sessions.patch`），v1 的通知页只读。

#### 关键实现：文字全在 Pi 侧渲染，设备零字体

设备端不放任何中文字库（Flash 装不下、渲染也慢）。所有文字由 petd 用 Pillow 渲染进 JPEG。

**长列表怎么办？v1 用页级翻页，不用像素级滚动：**

- petd 把长列表切成若干页，每页一张 240×320 的完整 JPEG
- 进入该页时**一次性预推**这些帧；设备把 JPEG 字节存 PSRAM（每页约 15KB）
- 旋钮翻页 = 设备换一张已缓存的 JPEG 解码上屏（TJpgDec 流式，量级 15–30ms，实测见 §十二 第 24 条），**零网络往返**
- 页数太多时（>10 页）服务端只推「当前页 ±2 页」，滚到边缘再补 —— 边界处有网络延迟，可接受

**为什么不用「一张 240×1600 长图 + 设备像素级滚动」**（最初的想法，已否）：TJpgDec 是**顺序解码**，要显示第 900 行就得把前面 56 个 MCU 行全解一遍，滚一下等几百毫秒。真做像素级滚动，设备得在 PSRAM 常驻一张解码后的帧缓冲（240×1600×2 = 768KB，N8R8 的 8MB 装得下），每次上屏还要把 16 行窗口从 PSRAM 拷进 SRAM 再走 SPI DMA（PSRAM 不能直接当 DMA 源）—— 多一套搬运机制。v1 不值得，v2 再议。

#### 帧缓存与内存

设备只缓存**压缩后的 JPEG 字节**，不缓存解码后的像素：

- PSRAM 8MB 足够放几十页 JPEG
- 解码后的 240×320×2 = 150KB 像素缓冲**不常驻** → 沿用 TJpgDec 的 7.68KB line buffer 流式直刷
- PSRAM 只当「存储」不当「DMA 源」，不违反 §六 的定案

#### 旋钮能玩出的更多花样（v2+ 候选）

| 玩法 | 实现 | 依赖 |
|---|---|---|
| 长列表像素级滚动 | PSRAM 常驻帧缓冲 + 16 行窗口搬运 | v1 不做，见上 |
| 一键「好 / 继续 / 取消」 | 短按映射到 `sessions.send` 预设回复 | 需 `operator.write` |
| 心情值 / 养成数值 | petd 侧持久化小状态机，转旋钮 = 互动 | 纯软件，成本最低 |
| 专注计时 / 番茄钟 | petd 计时，长按暂停 | 纯软件 |
| 天气、待办、日程 | 走 feature 机制新增模块 | 纯软件 |
| 旋钮当滚轮调音量 | — | ❌ 本板无音频硬件 |
| 旋钮调背光亮度 | — | ❌ BL 不接 GPIO，做不了 PWM 调光（§3.3） |

---

## 七、状态映射表 + 美术规格

### 7.1 状态定义

**字段名已实测确认（2026-11-03）**：`sessions.list` 每行有 `status` 字段，实测取值有 `running` / `done`，**无 `status` 键**（等价于 idle）。

| 宠物状态 | gateway 判定条件（实测字段） | 帧数 | 循环 | 备注 |
|---|---|---|---|---|
| `idle` | 无会话 `status=running` 且 `hasActiveRun=false` | 6–10 | 是 | 呼吸/眨眼，帧集要够长 |
| `working` | `status=running` 且 `hasActiveRun=true` | 4–6 | 否 | 我在跑工具 |
| `waiting` | `permissionModePending=true` 或 `unread=true` | 3–4 | 否 | **最醒目** |
| `done` | `status=done` | 4–6 | 否 | 任务完成 |
| `error` | `status=error` | 3–4 | 否 | 出错 |
| `sleeping` | 无活跃会话持续 N 分钟 | 4–6 | 是 | 可选 |

> 📌 **原候选词表已作废（保留原文以留痕）**：上面这些状态值来自 HachimoDock 侧的 `idle/working/speaking/done/error/waiting_user` + 旧 `thinking/tool_running`，**不是本机 gateway 的真实取值**。真实字段已于 2026-11-03 实连确认，见上表与 §十二 第 1 条。

### 7.2 美术交付规格

- 目录：`assets/<state>/<nn>.png`
- 透明底 PNG，原始尺寸随意（**我负责裁切缩放到 240×320，保持宽高比，不拉伸**）
- 所有状态帧保持同一只角色的比例与调性
- 命名：两位数字，`01`–`10`

### 7.3 界面布局（待细化）

- 上部：表情区（约 240×200）
- 下部：状态文字区（Pi 侧渲染，约 240×120）
- 会话气泡：旋钮在多个活跃会话间切换时显示
- 三块页面（表情 / 会话 / 通知）的具体版式待美术稿确定；页面与手势绑定见 §6.5

---

## 八、阶段计划与止损点

| 阶段 | 内容 | 止损线 | 状态 |
|---|---|---|---|
| 0 | 冻结协议 / 引脚图 / BOM / 状态映射 / 美术规格 / 后台端设计 | 不写一行固件就能定 | 🟡 **本文件** |
| 1 | DevKit + 屏 + 编码器裸线搭原型，跑通「收帧显示 + 按键上报」 | ¥41，不打板 | ⬜ |
| 2 | **petd 后台端**：连 gateway 订阅会话 → 状态映射 → 渲染帧 → REST/WS 骨架 | **不碰硬件也能软件调**（curl 推一张图看效果） | ⬜ |
| 3 | 形象与状态映射（美术由冰河大人出，我裁切） + feature 机制跑通 | 这阶段最容易「像图标堆」，不合格就重做 | ⬜ |
| 4 | OTA + 3D 打印外壳 | 只有前三步成立才走到这 | ⬜ |
| 5 | 功能扩展期：按需加 feature（待办 / 天气 / 对话回话 / 提醒） | 每个 feature 独立可停可删 | ⬜ |

**阶段 2 的第一件事**：写个 20 行的脚本连 gateway `sessions.subscribe`，把 `sessions.list` 的原始 JSON 打出来 —— 状态字段名、事件名、状态取值，一次看全。这是后面所有映射的地基，也是 §七 那张表的验证点。

前三步全部可用杜邦线完成，不碰焊接。

---

## 九、风险

| 风险 | 说明 | 缓解 |
|---|---|---|
| 美术一致性 | 小尺寸、多帧要保持同一只角色的比例与调性 | 美术由冰河大人出，我做尺寸/对比度校验 |
| 电源纹波 | 廉价充电头纹波大，Wi-Fi 电流尖峰可能拉出欠压复位 | 观察是否莫名重启；必要时并 100µF 电解（华为头余量充足，大概率不需要） |
| 刷新率 | 全帧刷写受 SPI 限制 | 状态跳变帧播一次即可，idle 走本地 Flash，需求离上限差一个数量级 |
| 断连黑屏 | 若「活着」的每帧都依赖网络 | idle 帧集烧进设备本地，天然不黑屏 |
| PSRAM 八线配置 | N8R8 与常见四线配置不同 | 固件 sdkconfig 直接按八线配 |
| ~~gateway scope 被拒~~ | ✅ **已解除**（2026-11-03） | 真因是 `client.id`/`client.mode` 配对非法导致自荐 scope 被清空；改用 `gateway-client`/`backend` 即通。见 §4.3b |
| **gateway 连接稳定性** | petd 断了宠物就瞎 | systemd 托管 + 指数退避重连；重连后必须重新 `sessions.subscribe`（订阅不跨连接保留） |
| **mDNS 在双网卡下可能广告错接口** | Pi 有 eth0 / wlan0 两个网段 | petd 在两张网卡上都应答 `petd.local`；设备端以实际连通的那个为准 |
| **petd 常驻** | 要 systemd user unit 托管 | 崩溃自动重启；gateway 断了要能自动重连重订阅 |
| **feature 越写越乱** | 插件机制容易长成屎山 | 强制三条边界（§5.4）：feature 不连 gateway、不直接写设备、不改设备状态机 |
| **venv 与系统依赖不一致** | PEP 668 禁装系统 pip | 建独立 venv，依赖清单写进 requirements.txt |
| **背光电压** | 白光 Vf≈3.0V，3.3V 供电可能极暗 | **已定案 BL 接 3.3V**（模块自带限流+驱动管，BL 是控制端，接 5V 有烧风险）。到货实测亮度，若过暗再议 MOSFET 调光 |
| **双 USB 5V 回灌** | DevKitC-1 两个口 5V 相通 | 烧录只插 PC，运行只插充电头，**不同时插** |
| **Pi IP 会变** | DHCP，两个网段 | mDNS `petd.local`，不写死 IP |
  <!-- ⚠️ 11-11 实测：petd.local 不可信也不可工作，mDNS 方案作废。设备端地址改走 provision 流程（待读代码确认）。 -->
| **廉价编码器抖动** | KY-040 脉冲有回差/抖动，一次转动可能报 2 步 | 设备端最小间隔去抖（≥5ms）+ 服务端按 `stepsPerAction` 换算；到货实测后再调（§十二 第 22–23 条） |
| **旋钮误触** | 屏与旋钮相邻，转动时手可能碰到 | 长按阈值 600ms、短按需 <600ms；v1 的短按动作都是无害切页 |
| **预推帧占带宽** | 长列表预推可能几百 KB | 预推窗口 ±2 页；4:2:0 q85 单页约 15KB |

---

## 十、待办

- [ ] 冰河大人下单 3 件料（¥41）+ 确认家里杜邦线是**母-母**
- [ ] 到货实测五项（§3.7）：BL/VCC 是否同网络、背光亮度、8 针顺序、排针性别、屏背净空
- [x] ~~阶段 2 第一件事：连 gateway 打印 `sessions.list` 原始 JSON~~ ✅ 2026-11-03 已完成，见 §七
- [x] ~~**解决 gateway scope 阻塞**~~ ✅ 2026-11-03 已解决：`client.id=gateway-client` + `client.mode=backend` + `scopes=['operator.read']`，无需设备身份。见 §4.3b
- [x] ~~决定 petd 的 scope~~ ✅ v1 = `operator.read`（见 §零 第 10 条）。`operator.write` 留到 v2 做「按一下回话」时再加 —— 实测 `scopes:['operator.read','operator.write']` 在 token 认证下同样生效，届时加 scope 不用重做接入
- [x] ~~渲染器里固定 JPEG 参数为 `quality=85, subsampling=2`（4:2:0）~~ ✅ 2026-11-03 已实现并实测（6.8–9.9KB/帧）
- [x] ~~建骨架~~ ✅ 2026-11-03 落地为独立仓库 `ai-agent-pet/`（petd/ + tests/ + deploy/ + docs/ + firmware/），不再是 skill
- [ ] 写 feature 机制（`feature_registry.py` + 最小示例）—— **v1 暂不做**，先把主线跑通
- [x] ~~写 systemd unit~~ ✅ 2026-11-03 完成：`deploy/petd.service`（user 级）+ `deploy/petd.env.example`（token 走 EnvironmentFile，不进 ps 输出）
- [ ] 部署时把 18860 登记进 `tools/port-registry/ports.json`（service: ai-agent-pet, path: null）
- [x] ~~token 注入方式定案~~ ✅ `PETD_GATEWAY_TOKEN` 环境变量 → `petd/data/secrets.json`（gitignore）→ systemd EnvironmentFile，三级回落
- [ ] 设备端 Wi-Fi 凭据用本地 `wifi_config.h` 注入（进 .gitignore，不进仓库）
- [ ] 确认 petd 的 systemd unit 归属（user 级 vs 系统级）
- [x] ~~定稿 gestures.json~~ ✅ 2026-11-03 完成（`petd/data/gestures.json` + `petd/gestures.py` handler 表）
- [ ] petd 侧实现「页级翻页 + 预推帧 + 设备缓存 JPEG」的推送逻辑 —— **v1 只推当前帧**，预推是优化项
- [ ] v1 三块页面版式定稿：表情页 / 会话页 / 通知页 —— 表情页已实现（程序化默认角色），会话页/通知页只做了页签切换
- [x] ~~信息密度三档~~ ✅ 2026-11-03 完成（表情区高度 140/200/280，文字区跟着变）
- [ ] 美术规格确认后出图（目录/命名见 §6.2）
- [ ] 固件：Wi-Fi + ST7789 + TJpgDec + 帧缓冲 + 旋钮 —— **进行中**（源码 + CI 由子代理写，编译验证靠 GitHub Actions）
- [x] ~~Pi 端 petd：gateway 订阅 → 状态映射 → 渲染 → 推送~~ ✅ 2026-11-03 完成并实测：真实 gateway 接入、6 个会话、状态映射正确、JPEG 渲染正常、后台接口全通
- [x] ~~gateway 连接方式定案~~ ✅ 设备连 petd，petd 连 gateway（见 §4.3b）
- [ ] 部署时登记端口 18860 到 `tools/port-registry/ports.json`
- [ ] 外壳建模（阶段 4，树脂收缩 +0.3mm，壁厚 1.5–2mm，卡扣改「插入式」）

---

## 十一、相关文件

- 调研原始记录：`memory/2026-11-03.md`（HachimoDock 调研段）
- 端口登记表：`tools/port-registry/ports.json`
- 环境事实：`TOOLS.md`
- Gateway 协议文档（本机）：`/home/open/.nvm/versions/node/v26.12.1/lib/node_modules/openclaw/docs/gateway/protocol/{handshake,rpc-bootstrap-and-events,rpc-methods}.md`
- Gateway 操作员 scope：`.../docs/gateway/operator-scopes.md`
- 参考同构技能：`skills/inksight/`（设备 + 后端 + venv + systemd unit 的现成样板）
- **开源文档集**：`ai-agent-pet/`（README + docs/ 八篇 + CONTRIBUTING + SECURITY，共 1007 行，2026-11-03 写）
  - `ai-agent-pet/README.md` — 项目门面、架构图、状态语义速查
  - `ai-agent-pet/docs/硬件指南.md` / `状态语义.md` / `旋钮.md` / `feature开发.md` / `美术规范.md` / `固件构建.md` / `故障排查.md` / `开源路线.md`
  - `ai-agent-pet/CONTRIBUTING.md` — **代码与美术分开授权**
  - `ai-agent-pet/SECURITY.md` — 权限、数据流向、凭据管理
  - ⚠️ 这个目录在 workspace 仓库里（该仓库有 513 个未提交改动、含私人内容），**不能直接当开源仓库** —— 发布时要另建独立仓库

---

## 十二、待交叉验证的事实清单

下面每条都是待验证的判断。给 Gemini 做交叉验证时按此清单逐条查；查完把「结论 / 证伪」回填，冲突的地方**以一手实测为准**改本文档。

已实测的条目已标 ✅ / ❌ / ⚠️，未测的保持原样。

| # | 待验证断言 | 怎么验 | 依据 / 风险 |
|---|---|---|---|
| 1 | ~~`sessions.list` 行里承载「会话状态」的字段名与取值枚举~~ | ✅ **已实测** | 字段 = `status`（`running`/`done`/`error`/无键）；辅助字段 `hasActiveRun` / `unread` / `permissionModePending` / `isBackground`。见 §七 |
| 2 | ~~`operator.read` 覆盖 `sessions.subscribe`~~ | ✅ **已证实** | 实测 `sessions.subscribe` 返回 `{subscribed:true,list}`，事件流正常（`session.tool` / `agent` / `health`） |
| 3 | ~~只发 `connect.challenge` 不回签名、仅带 token 能否完成握手~~ | ✅ **已证实** | 不需要签名。`auth.token` + `client.id=gateway-client` + `client.mode=backend` + `scopes=['operator.read']` → `hello-ok` 带 `scopes:['operator.read']` |
| 4 | ~~网关 token 认证用 `auth.token` 字段即可~~ | ✅ **已证实** | token 认证 + 合法 client id/mode 组合即可，无需设备身份 |
| 5 | ~~ESP32-S3 DevKitC-1 的 N8R8 是**八线 Octal PSRAM**，需专用 sdkconfig~~ | ✅ **已核实** | 🟢 一手核 ESP-IDF Kconfig 源码：根因是 `choice SPIRAM_MODE` 默认 `QUAD`，Octal 芯片必须显式 `CONFIG_SPIRAM_MODE_OCT=y`。完整 `sdkconfig.defaults` 已写进 `ai-agent-pet/docs/固件构建.md` |
| 6 | ESP32-S3 **不支持 5GHz Wi-Fi**，路由器必须有 2.4G SSID | 查芯片 datasheet + 实连 | 5G-only 路由器直接连不上 |
| 7 | ST7789 8-pin 模块的 BL 与 VCC 是否同一网络 | 万用表量通断 | 决定 BL 能不能接 5V（见 §3.7 第 1 项） |
| 8 | 白光 LED Vf≈3.0V，3.3V 供电背光偏暗 | 实测亮度对比 3.3V / 5V | 决定要不要加 ¥3-5 的 MOSFET 调光 |
| 9 | DevKitC-1 两个 USB 口的 5V 在板上相通 | 查原理图 + 实测 | 同插 PC 与充电头会 5V 回灌 |
| 10 | DevKitC-1 尺寸约 62.5×25.4mm，屏 70.3×42.72mm，板可藏屏后 | 量实物 | 决定外壳是「屏背藏板」还是「板在旁边」 |
| 11 | ~~240×320×2=150KB 帧缓冲放 S3 内部 SRAM 够用~~ | 🔄 **已改为流式直刷** | 150KB 单块 malloc 在 Wi-Fi 栈起来后有 OOM 风险 → 改 TJpgDec MCU 流式，line buffer 7.68KB |
| 12 | **PSRAM 不能作为 SPI DMA 源** | 查 ESP-IDF 文档 / 实测 | ⚠️ Gemini 指出：OPI PSRAM 理论上可作 SPI DMA 源（需 16 字节对齐 + Cache 同步），但**效率低、易撕裂**。结论不变：仍不用 PSRAM 做 DMA 源 |
| 13 | `esp_websocket_client` 组件在 ESP-IDF 5.x 可用且支持 binary 帧 | 查 IDF 组件列表 | 推帧要靠它 |
| 14 | ESP-IDF `mdns` 组件可应答 `petd.local` | 查 IDF 组件 + 实测解析 | 备选是静态 IP 或 UDP 广播发现 |
| 15 | Pi 侧 `websockets 17.0.1` / `Pillow 11.1.0` / Noto Sans CJK SC 均已就绪 | ✅ 本机已验证 | —— |
| 16 | 本机 gateway `bind=lan`、`auth.mode=token`、协议 v4 | ✅ 已读 `openclaw.json` + 实连确认（server `2026.10.8`） | —— |
| 17 | **`client.id` 是白名单**，不能自取 | ✅ 实测：`petd` 被拒 `INVALID_REQUEST`，`cli` 可用 | 若走设备身份方案，需申请合法 client id |
| 18 | **`client.mode` 合法值含 `backend`** | ✅ 实测：`operator` 非法，`backend` 可用 | —— |
| 19 | **`openclaw gateway call sessions.list` 可用** | ✅ 实测成功（CLI 走本地 owner 路径） | petd 的**兜底方案**：shell out CLI |
| 20 | Pillow `subsampling=2`（4:2:0）时 TJpgDec 输出 16×16 MCU | 按 JPEG 标准推导，未实机验证 | 决定 line buffer 取 16 行；实机跑通后确认 |
| 21 | **「scope 客户端自荐无效」是错的** | ✅ 已证伪 | 真因是 `client.id`/`client.mode` 配对非法 → 源码清空 scopes。配对正确后自荐 `operator.read` 生效 |
| 22 | ~~KY-040 每圈格数~~ | 🟡 **工作假设 20 PPR** | Gemini 称常见 20 PPR；未实测。配 `stepsPerAction=4` → 拧一圈 5 次动作。到货复核 |
| 23 | 廉价 KY-040 的脉冲抖动与回差 | 到货慢转 / 快转各 10 圈，比对上报步数 | 决定设备端去抖参数 |
| 24 | TJpgDec 解一帧 240×320 4:2:0 的耗时 | 实机计时（`esp_timer_get_time` 包住 `JpegDec_GetImage`） | 决定翻页动画能否逐帧；量级估计 15–30ms |
| 25 | ~~一页 240×320 列表页 JPEG 的实际体积~~ | ✅ **已实测** | 🟢 Pillow q85/4:2:0 + Noto CJK：2 会话 8.4KB / 5 会话 16.4KB / 8 会话 22.7KB。预推 ±2 页约 82KB，无压力 |
| 26 | **ESP-IDF 版本选 v5.2/v5.3 LTS** | ❌ **Gemini 的建议已过时** | 🟢 GitHub 一手核：ESP-IDF 最新已是 **v6.1**（另有 v6.0.x / v5.5.6 / v5.4.4 / v5.3.6）。PSRAM 选项名在 5.3/5.5/6.1 三版一致。选 5.5.x 求稳或 6.1 求新 |
| 27 | **状态抖动（flapping）风险** | ✅ Gemini 指出，已修 | 瞬态脏状态会让宠物在 idle/working 间抽搐。已加稳态窗（退回 idle 需连续 3s）+ 明确空集判定 + 事件去抖 200ms。详见 `ai-agent-pet/docs/状态语义.md` |
| 28 | **列表页加滚动条指示** | ✅ Gemini 建议，已采纳 | 页级翻页没有位置感，右缘加胶囊条标「第几页/共几页」，成本近零 |
| 29 | **`draw_bitmap` 是异步的，单缓冲会撕裂** | 🟢 **已核源码** | `esp_lcd_panel_io_spi.c` 用 `spi_device_queue_trans` 排队发送，返回时 DMA 可能仍在读 buffer。必须**双缓冲**。这是 Gemini 代码骨架里漏掉的竞态 |
| 30 | **TJpgDec 工作内存需 ~3100 字节连续内存** | 🟢 已采纳 | 复杂哈夫曼表会溢出崩溃。`heap_caps_malloc(3100, MALLOC_CAP_INTERNAL)`。🟢 该 fork 的 `JD_FORMAT` 由 `TJPGD_USING_FORMAT_RGB565` 控制；`JD_SZBUF` 需自定义 `TJPGD_INPUT_BUFFER_SIZE` |
| 31 | **ROM 版 TJpgDec 不可用** | 🟢 已采纳 | S3 **没有 ROM 版 TJpgDec** → 源码引入是唯一选择（ROM 版只有原始 ESP32 才有）。🟢 已核该 fork **无 `JD_FASTDECODE`**，加速开关是 `JD_USE_SCALE` / `JD_TBLCLIP` |
| 32 | **SPI 频率起步 26MHz** | 🟡 已采纳 | 40MHz 走杜邦线易花屏。240/9=26.67MHz、240/6=40MHz，**避开非整数分频点**。26MHz 下整屏 47ms，够 12fps idle |
| 33 | **`buffer_size` 默认值** | 🔴 **未核到** | Gemini 称 1024。🟡 `esp_websocket_client` **不在 IDF v5.5 核心树**（在 esp-iot-solution），本轮 raw 路径全 404。收 23KB 帧必然分片，靠 `payload_offset + data_len >= payload_len` 拼帧 |
| 34 | **TJpgDec 解码速度 18–25ms** | ❌ **Gemini 的推导不成立** | 它用 IDF `esp_driver_jpeg` 的 320×180 基准按像素线性外推，但那是**另一个解码器**（IDF 自带组件，与 TJpgDec 的纯软件实现无关）。跨解码器外推无效。**我们的 15–30ms 仍是待实测** |
| 35 | **「6.1 会把 ST7789 驱动剥离到 component registry」** | ❌ **已证伪** | 🟢 一手核：v6.1 的 `components/esp_lcd/src/esp_lcd_panel_st7789.c` **仍在**。驱动没剥离，选 5.5.x 的理由改为「避开未知的 6.x 行为变化」 |
## 2026-11-03 22:00 更新：后台口令 + 尾斜杠修复

- **后台口令（可选）**：`PETD_ADMIN_PASSWORD` 或 `petd/data/secrets.json` 的 `adminPassword`。
  没设 = 无鉴权（行为不变）。设了 = 同一个页面里显示口令框，验证后原地展开。
  cookie 是 HMAC 签名 + HttpOnly + SameSite=lax，7 天。
  `/device` 不鉴权（要改固件，违反原则）。
- **反代前缀必须带尾斜杠**：`= /前缀` 要 301 跳到 `/前缀/`，否则相对资源解析到站点根。
- 提交 `b7ec0db`。

## 2026-11-03 22:20 更新：后台页文案 + CI 修好 + restart 卡死 <!-- project: path:/home/open/.openclaw/workspace -->

- **后台页文案要是产品语言，不是开发者自述**（冰河大人当面点名）。原来第一屏是红色横幅骂「任何能打开这个链接的人都能看状态、改配置」，页脚在解释「OTA 按钮在 v1 不可用，所以这里不放一个假的 OTA 按钮」—— 这是写给开发者的备注，不是功能页给使用者看的东西。改成中性页头 + 鉴权状态徽章，安全提示收到页脚。<!-- project: path:/home/open/.openclaw/workspace -->
- **`starlette.testclient` 依赖 `httpx`，而 `requirements.txt` 是运行时依赖不含它** —— CI 三个 Python 版本在收集阶段就 error，本地 53 项全绿也掩盖不了。CI 要显式 `pip install httpx`。<!-- project: path:/home/open/.openclaw/workspace -->
- **SSE 长连接会让 `systemctl restart petd` 永久卡在 `stop-sigterm`** —— 后台页开着 `/admin/api/events`，生成器不查断连，uvicorn 优雅关闭会一直等它。修法：SSE 每轮 `await request.is_disconnected()`，unit 另加 `TimeoutStopSec=15` 兜底。复现验证：restart 从 124（超时）变 0。<!-- project: path:/home/open/.openclaw/workspace -->
- **手势语义表是启动时加载的** —— `gestures.json` 改完要重启 petd 才生效，页脚文案原来写「改这个不刷固件」被误读成热生效，已改成「重启 petd 后生效」。<!-- project: path:/home/open/.openclaw/workspace -->
- 提交 `5a96ca3`，python-ci 全绿（lint / 3.10 / 3.11 / 3.12）。<!-- project: path:/home/open/.openclaw/workspace -->
- **后台页主体在免密路径下从未显示**（11-03 22:45）：`index.html` 的 `<main id="app">` 带 `class="hidden"`，解锁它的 `hideGate()` 只在登录成功后调用；没设口令走的 `startDashboard()` 完全不碰 `#app` → 页面只剩页头页脚。修法：`<main>` 默认可见（gate 路径由 `showGate()` 隐藏），`startDashboard()` 兜底解锁一次 + 两条回归测试。提交 `a5b3cc4`。<!-- project: path:/home/open/.openclaw/workspace -->
- **验证纪律（11-03 冰河大人当面点名）**：改后台页只 curl 源码不算验证过；无头 chromium 在这台机上也不可靠（截图 4 张 sha256 相同、PNG 仅 1 色、dump-dom 仅 40 字节空 HTML）。**要拿渲染截图当证据，先确认截图本身有效**（唯一颜色数、DOM 非空），再下结论。<!-- project: path:/home/open/.openclaw/workspace -->
- **先对齐再动手**（冰河大人 11-03 22:41 强调，AGENTS.md「先聊聊看看」=暂停）：功能/改动类任务第一步只给目标+验收+方案，等回应后再动代码。越俎代庖先改代码，事后问「要不要改」等于没对齐。<!-- project: path:/home/open/.openclaw/workspace -->
- **预览图静态 src 导致未登录 401**（11-03 22:50）：`<img src="api/preview.jpg">` 是静态引用，浏览器一解析 HTML 就发请求，那时还没登录、没有 cookie。改用透明占位图，真实 URL 由 app.js 在登录后设置。提交 `c832e8b`。<!-- project: path:/home/open/.openclaw/workspace -->
- **无头 chromium 在这台机必须加 `--no-proxy-server`**（11-03 22:47 定位）：全局 `HTTP_PROXY` 会把浏览器导航劫持 —— 症状是截图只有 1 种颜色、dump-dom 只有 40 字节空 HTML、4 张图 sha256 相同。之前那批「已上线」截图全是废的。python 侧也要 `env -u HTTP_PROXY ...`。**正确姿势：chromium --remote-debugging-port + CDP 脚本驱动**，能真实填表提交、能截图、能读 DOM。<!-- project: path:/home/open/.openclaw/workspace -->
- **CDP 验证后台页的标准姿势**（11-03 22:50）：`form.requestSubmit()` 触发真实提交链路，不要直接 `fetch('api/login')` —— 后者绕过 `onGateSubmit`，`hideGate()` 不跑，界面不切换，看不出真实行为。<!-- project: path:/home/open/.openclaw/workspace -->
- **后台重构为四块**（11-03 23:30）：`我的宠物`（默认）/ `素材仓库` / `能力清单` / `安装与诊断`。默认视角改成「宠物在做什么」—— 大字状态 + 顾着谁 + 要不要你动手；26 项原始遥测收进诊断区。<!-- project: path:/home/open/.openclaw/workspace -->
- **宠物反应全部会话**（11-03 23:30）：各自判状态 → 宠物取最急的 → 带归因（`pet.attribution`）和计数（`pet.counts`）。**焦点不再改变宠物状态** —— 旋钮选中只决定 session 页看哪条。优先级 `waiting > error > working > done > idle`（等你批准最急）。<!-- project: path:/home/open/.openclaw/workspace -->
- **`lastActivityAt` 是毫秒**（11-03 23:30 踩坑）：之前按秒算 → 前端「几分钟前」全显示「刚刚」，且 done 衰减永不触发（秒减毫秒是负数），几天前的会话一直占着宠物。修法：`state.py` 的 `epoch_s()` 在边界统一（>1e11 视为毫秒）。<!-- project: path:/home/open/.openclaw/workspace -->
- **能力清单 = 数据不是代码**（11-03 23:30）：`petd/data/capabilities.json` 随仓库发布，用户在 `capabilities.local.json` 加自己的。只允许白名单 `ALLOWED_ACTIONS` 里的声明式动作 —— 不开放任意命令执行。<!-- project: path:/home/open/.openclaw/workspace -->
- **CDP 脚本别硬编码口令**（11-03 23:30 踩坑）：口令写进脚本会被脱敏成 `***`，登录永远失败。改成运行时从 `~/.config/petd/petd.env` 读。<!-- project: path:/home/open/.openclaw/workspace -->

## 2026-11-11 OTA（后台决策式固件自更新）+ 屏保 + 贪吃蛇转向修复 <!-- project: path:/home/open/.openclaw/workspace -->

### OTA 设计底线
- **升级只能由后台的人点** —— 设备连上来不会自己升级，petd 只显示「当前版本 / 可用版本」。<!-- project: path:/home/open/.openclaw/workspace -->
- 支持两种固件来源：上传 bin（私有化部署连不上 GitHub Release）、填 URL（内网 NAS）。petd 自动算 sha256。<!-- project: path:/home/open/.openclaw/workspace -->
- 协议新增 otaStart / otaChunk / otaFinish / otaStatus；分片 16KB，**文本预告 + WebSocket binary 本体**。<!-- project: path:/home/open/.openclaw/workspace -->
- 写入非当前运行的 ota_0 / ota_1 分区，校验成功后切启动分区重启；CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y，新固件连上 petd 才确认有效，否则 bootloader 自动回滚。<!-- project: path:/home/open/.openclaw/workspace -->
- 最低支持版本 0.5.4 —— 更低固件连 otaStart 都不认识。<!-- project: path:/home/open/.openclaw/workspace -->
- **0.5.4 改了 bootloader，所以第一次仍要整片刷 merged-binary.bin；之后才能纯 OTA petd.bin。全量镜像绝不能作为 OTA app 镜像登记。**<!-- project: path:/home/open/.openclaw/workspace -->
- Release：https://github.com/GlacierFang/ai-agent-pet/releases/tag/fw-v0.5.4（merged-binary.bin / petd.bin / uf2.bin / flash_args / flasher_args.json）。<!-- project: path:/home/open/.openclaw/workspace -->

### OTA 踩过的三个坑
- **IDF 组件名**：esp_ota_ops.h 属于 app_update 组件，不是 bootloader_support。还需要加 esp_partition。<!-- project: path:/home/open/.openclaw/workspace -->
- **esp_ota_begin(..., OTA_SIZE_UNKNOWN, ...) 会整片擦 2MB 分区**（IDF 源码：image_size == OTA_SIZE_UNKNOWN 时 erase_size = partition->size），二十几秒且跑在 WS 回调里 → 回调阻塞、设备心跳都发不出去。改 OTA_WITH_SEQUENTIAL_WRITES，擦除交给 esp_ota_write 按扇区做。<!-- project: path:/home/open/.openclaw/workspace -->
- **hello 的 char buf[768] 会截断 JSON**：加 ota 字段后 + 最长 512 字节 packs 列表超限 → 截断的 JSON 非法 → **握手直接失败**。改 1280。<!-- project: path:/home/open/.openclaw/workspace -->

### 测试污染仓库（11-11 修）
- ota_dir() 原来绑死 petd/data/ota，测试上传的 13 字节假固件直接落在仓库里还被 git 追踪。改成**调用时取 config.DATA_DIR**（生产路径不变，测试换临时目录）；petd/data/ota/ 进 .gitignore。提交 6d34d3e。<!-- project: path:/home/open/.openclaw/workspace -->
- 教训跟 tests/conftest.py 里那条一致：**路径必须在调用时取 DATA_DIR，不能在 import 时绑死**。<!-- project: path:/home/open/.openclaw/workspace -->

### 屏保
- 忙状态超过后台阈值（默认 60 秒）切相册轮播：右下角宠物小窗 + 底部渐变黑带显示时间与照片计数 + 旋钮换图（间隔 15 秒）。阈值 0 = 关闭；相册为空或设备在游戏中则不上屏保。<!-- project: path:/home/open/.openclaw/workspace -->
- 纯服务端实现，无固件改动。<!-- project: path:/home/open/.openclaw/workspace -->

### 贪吃蛇（fw-v0.5.3）
- 转向改为按方向序号 delta = a * steps * sign（旧固件算不出 90° 转向）；速度 3 格/秒；绘制与碰撞几何统一到 grid_cell_size()；修判负后继续移动、吃食物后食物消失。<!-- project: path:/home/open/.openclaw/workspace -->
- **fw-v0.5.4 顺带带上了这个修复** —— 一次刷机解决两件事。<!-- project: path:/home/open/.openclaw/workspace -->

### 守望进程已移除（11-11）
- openclaw-unit-watch.service / .timer（system scope，每小时检查 failed unit 推飞书）+ scripts/unit-watch.sh **全部删除**，timer 已 disable --now，状态目录与日志清空，TOOLS.md 服务表那行删掉。<!-- project: path:/home/open/.openclaw/workspace -->
### 0.5.5 —— 纯 OTA 的验证目标（11-11）
- `fw-v0.5.5` **只改了版本号**（`PETD_FW_VERSION` 0.5.4 → 0.5.5），bootloader / 分区表 / app 逻辑一字未动。Release：https://github.com/GlacierFang/ai-agent-pet/releases/tag/fw-v0.5.5（提交 `0073c04`）。<!-- project: path:/home/open/.openclaw/workspace -->
- app 镜像 `petd.bin` = 1060208 字节，sha256 `ea663f4613a7c844bf18f7cb2ca3f752e51310899b7627319eb9ff0790438631`。<!-- project: path:/home/open/.openclaw/workspace -->
- **两种登记方式都实测通过**：URL 登记（petd 自己取数，sha256 与直连下载一致）+ 上传登记（base64 走文件 body 提交；**base64 后 1.4MB 超过 argv 限制，必须 `--data-binary @file`，不能塞进命令行参数**）。<!-- project: path:/home/open/.openclaw/workspace -->
- 设备当前仍是 `fw=0.5.2`，所以后台 `available` 为空 —— 版本闸门正常工作（要 ≥ 0.5.4 才给升）。<!-- project: path:/home/open/.openclaw/workspace -->
- **网络坑**：这台机的出口代理会拦 `release-assets.githubusercontent.com`（Forbidden），但 `api.github.com` / `raw.githubusercontent.com` 放行。直连（不设代理变量）能拿到 release 资产。petd 自身进程环境里**没有代理变量**，所以它自己取 URL 是通的。<!-- project: path:/home/open/.openclaw/workspace -->
- **curl 能登录后台但 urllib 403**：admin 登录对 python urllib 的请求会 403，curl 正常 —— 后台 API 的自动化验证统一用 curl。<!-- project: path:/home/open/.openclaw/workspace -->

### 确认卡（11-11 已实现）
- 一张卡 = 一个问题 + 2~4 个选项；旋钮语义：转选择 / 短按确认 / 长按取消。答案自动回到**发起方会话**（gateway `chat.send`），AI 不用再问一遍。<!-- project: path:/home/open/.openclaw/workspace -->
- 多会话并发规则：队列上限 3 张，屏上一次只答一张；同会话连问 → 新问题顶掉自己的旧卡（`superseded`）；显示时优先焦点会话的卡，焦点没卡才回退队首（防饿死）；超时 5 分钟 → `expired`，回程告知未决不阻塞开发。<!-- project: path:/home/open/.openclaw/workspace -->
- `petd` 的 gateway scope 已从 `operator.read` 改为 `operator.read + operator.write` —— `chat.send` 是 write-scoped，只读 scope 会被 gateway 拒。`chat.send` 必须带 `idempotencyKey`。<!-- project: path:/home/open/.openclaw/workspace -->
- 兜底：答案落盘 `petd/data/confirm_answers.jsonl`，网关断了不丢。纯服务端实现，不刷固件、不改设备协议。<!-- project: path:/home/open/.openclaw/workspace -->
- 接口：`/admin/api/confirm` (GET/POST)、`/admin/api/confirm/answer`、`/admin/api/confirm/cancel`。发起端 skill：`skills/pet-confirm/SKILL.md`。<!-- project: path:/home/open/.openclaw/workspace -->
- 提交 `7571df0`；测试 `tests/test_confirm.py` 28 项；全量 456 passed。<!-- project: path:/home/open/.openclaw/workspace -->

### 发布说明规范（11-11 定，主人改过一遍定下来的）
- **标题只留 tag**（`fw-v0.5.4`），不挂副标题 —— 标题不重复正文已经说过的事。<!-- project: path:/home/open/.openclaw/workspace -->
- **正文 H1 只写版本号**（`## 0.5.4`），说明性内容全在下面写清楚。<!-- project: path:/home/open/.openclaw/workspace -->
- 发布说明存在仓库里 `docs/releases/<tag>.md`，跟着代码一起 review 一起改；CI 发 tag 时自动取该文件作正文，没有才退回 GitHub 自动生成的变更日志（并打 warning）。提交 `bb28fad`。<!-- project: path:/home/open/.openclaw/workspace -->
- 教训：**发布说明不该临场写在 GitHub 上** —— 发完就没了，下次要改只能重新抓一遍。<!-- project: path:/home/open/.openclaw/workspace -->

### mDNS / petd.local 不可信（11-11 主人实测）
- **`petd.local` 不可信也不可工作** —— 之前"mDNS 可用"的说法作废。文档里一律不要出现 `petd.local` 作为可用地址。<!-- project: path:/home/open/.openclaw/workspace -->
- **端口不能写死** —— 每台机器不一样，文档一律用占位符 + 让用户查自己的实际端口。<!-- project: path:/home/open/.openclaw/workspace -->
- **首次访问后台应跳转去设置口令页** —— 主人明确要求的行为。<!-- project: path:/home/open/.openclaw/workspace -->

### 部署改造（11-11 三批提交）
- `a4c668c` 设备地址自动获取（只列本机真实网卡 IP + 端口，不再推荐 petd.local/mDNS）+ OTA 镜像校验（app 分区上限 2MB、ESP32 magic 0xE9、拒绝 merged-binary.bin）+ 文档去写死；setup API 新增 `servers` 字段。<!-- project: path:/home/open/.openclaw/workspace -->
- `50c2ef5` 后台填的网关令牌真存下来（落 `petd/data/secrets.json` 600，优先级：手写 config.json > 后台填的 > 环境变量）+ 改完自动重连（`pet.cfg` 原地更新 + `GatewayClient.reconnect()`）；新增 `docs/网关接入.md`。<!-- project: path:/home/open/.openclaw/workspace -->
- `848920f` 固件配网页填的 `IP:端口` 真生效：新增 `firmware/main/addr.c`（只依赖 libc，主机 gcc 可验证），地址和端口分开存 NVS，连接时用拆出来的端口；`PETD_DEFAULT_PORT` 降级为「没填端口时的回落」。<!-- project: path:/home/open/.openclaw/workspace -->
- 测试：`tests/test_firmware_addr.py`（13 个地址解析用例，gcc -Wall -Wextra -Werror 真编译）+ `tests/test_config.py` 5 项 + `tests/test_admin_auth.py` 3 项；全量 **475 passed**。<!-- project: path:/home/open/.openclaw/workspace -->
- CI：`python-ci` 与 `firmware-build` 均 success（固件在真实 ESP-IDF v5.5 构建里编过，含新增 addr.c）。<!-- project: path:/home/open/.openclaw/workspace -->
- **首次访问流程已实测走通**：未设口令 → `setup/public` 返回 `servers` → 设口令 → 登录 → 虚拟屏 preview.jpg 200（10KB 且帧在变）→ 后台填 token 落盘。不造虚拟设备，`device.online=false`。<!-- project: path:/home/open/.openclaw/workspace -->
- **Release 已核对**：`fw-v0.5.4` / `fw-v0.5.5` 的标题都是干净的 tag，正文完整。<!-- project: path:/home/open/.openclaw/workspace -->
- 仍待真机：USB 整片刷 `fw-v0.5.4` → 登记 `fw-v0.5.5` 的 `petd.bin` → 纯 OTA → 失败回滚；确认卡整屏/旋钮/短按长按；贪吃蛇真机手感；固件新端口解析的真机验证。<!-- project: path:/home/open/.openclaw/workspace -->

### 确认卡真机验收（11-11 已通过）
设备 `esp32s3-02349c` fw=0.5.2 在线，petd 侧三种手势全链路跑通：
- **整屏渲染**：240×320 帧里「待确认」标题 + 问题 + 2~3 个选项 + 底部「转选择 · 短按确认 · 长按取消」，无截断/重叠。<!-- project: path:/home/open/.openclaw/workspace -->
- **旋转选择**：`/admin/api/knob {"gesture":"rot"}` → `action=confirm.select`，`selected` 0→1，选中项高亮（截图确认）。<!-- project: path:/home/open/.openclaw/workspace -->
- **短按确认**：`{"gesture":"press_short"}` → `action=confirm.answer`，回程文本 `[确认卡] <问题>\n用户选择了：**<选项>**（第 N 项）` 已用 gateway `chat.send` 注回原会话（日志「的结果已注回 …」）。<!-- project: path:/home/open/.openclaw/workspace -->
- **长按取消**：`{"gesture":"press_long"}` → `action=confirm.cancel`，回程一句取消说明。<!-- project: path:/home/open/.openclaw/workspace -->
- 答案同时落盘 `petd/data/confirm_answers.jsonl`（answered/cancelled 都记）。<!-- project: path:/home/open/.openclaw/workspace -->
- 注：模拟手势走的是 `/admin/api/knob`，与真设备同一条 `on_knob` 路径；**实体旋钮的触感与多会话焦点切换仍未在真机上人工确认**（无人在设备旁操作）。<!-- project: path:/home/open/.openclaw/workspace -->

### 确认卡队列规则真机验证（11-11 已通过）
用后台 API 实跑（curl 走，后台挡 urllib —— 见「curl 能登录后台但 urllib 403」那条）：
- **同会话连问 → 顶掉旧卡**：A1 发完后 A2 发出，队列里只剩 A2，A1 状态 `superseded`，并回了一句「这个问题已被同一会话的新问题取代，未作答」。<!-- project: path:/home/open/.openclaw/workspace -->
- **队列上限 3**：发到第 4 张时最旧的被顶掉（`expired`），`pending` 始终 ≤3。<!-- project: path:/home/open/.openclaw/workspace -->
- **焦点会话优先**：焦点会话的卡即使排在**队尾**，仍被 `current()` 选中显示；焦点会话没卡时才回退队首（防其他会话饿死）。<!-- project: path:/home/open/.openclaw/workspace -->
- **游戏态不弹卡**：`_confirm_active()` 判 `devices.session.mode != "idle"` 就返回 None，卡留在队列里；已有测试 `tests/test_confirm.py::test_app_suppresses_confirm_when_device_playing` 覆盖。<!-- project: path:/home/open/.openclaw/workspace -->
- 实测脚本：`/tmp/queue_check.sh`、`/tmp/focus_check.sh`（临时文件，未入库）。<!-- project: path:/home/open/.openclaw/workspace -->

### 确认卡多会话回程验证（11-11 已通过）
- 三张测试卡同时挂在三个会话上（B1/B2/B3），长按取消后**每张卡的取消说明都回到了自己的发起会话**，没有串台。<!-- project: path:/home/open/.openclaw/workspace -->
- 结论：`chat.send` 按卡上记的 `sessionKey` 精确投递，`focusSession` 只影响**屏上显示哪张**，不影响答案回哪儿。<!-- project: path:/home/open/.openclaw/workspace -->
- `sessionKey` 指向不存在的会话（如 `session-other-x`）时：状态正常落盘、说明无处可投 —— 静默丢弃即可，不算故障。<!-- project: path:/home/open/.openclaw/workspace -->
- 验证完队列干净（`pending: 0`），答案全在 `petd/data/confirm_answers.jsonl`（answered/cancelled/superseded 都记）。<!-- project: path:/home/open/.openclaw/workspace -->

### 长按取消：模拟已过，真机未验（11-11）
- 服务端长按取消链路**完整跑通**：卡 `3eda491b3976`（长按取消能生效吗？）16:09:16 状态转 `cancelled`，16:09:17 答案回注原会话，文本「用户长按取消了这个问题，没有作答」。<!-- project: path:/home/open/.openclaw/workspace -->
- **但这次取消是后台模拟按钮按的**，不是实体旋钮 —— 访问日志 `POST /admin/api/knob` @16:09:16（127.0.0.1）；前两卡的旋转/短按同理（16:08:41 rot、16:09:02 press_short）。16:07 那张卡走的是 `POST /admin/api/confirm/answer`。<!-- project: path:/home/open/.openclaw/workspace -->
- 自 petd 启动（15:40）至今，内存日志 59 条里**实体旋钮事件 0 条** —— 物理旋钮一次都没按过，别把模拟当手感。<!-- project: path:/home/open/.openclaw/workspace -->
- 真机长按不需先刷机：fw-v0.5.2 的 `knob.c` 已上报 `held` 按压时长（`emit(PETD_EV_PRESS,0,0,held)`），`ws_client.c` 明确「设备只报原始 ms，不做判断」，阈值 600ms 在服务端 `gestures.json` 的 `longPressMs`。<!-- project: path:/home/open/.openclaw/workspace -->
- 查证要点：`/admin/api/logs` 返回 `{items:[{seq,ts,level,msg}],seq}`（不是裸数组）；user journal 无历史，用 `systemctl --user status petd.service --lines=N` 才看得到访问记录。<!-- project: path:/home/open/.openclaw/workspace -->
- 真机长按无需先刷机：fw-v0.5.2 的 `ws_client.c` 已带 `\"ms\":%d` 上报按压时长，服务端 `gestures.classify` 按 `ms >= longPressMs(600)` 分派长按/短按。<!-- project: path:/home/open/.openclaw/workspace -->

### 教训：没有硬件时别让用户按键（11-11）
- 16:21~16:34 我连续推卡让用户按实体旋钮，用户**不在设备旁边** —— 回执全是「超时未答」，那不是故障，是没人在旁边。以后推卡给人按键前先确认人在旁边。<!-- project: path:/home/open/.openclaw/workspace -->
- 正确做法：起隔离 petd 实例（`--no-gateway`，独立 data 目录）+ 仓库自带 `petd/tools/mock_device.py`，走**真实 WebSocket** 发 input 事件，不需要人手。<!-- project: path:/home/open/.openclaw/workspace -->
- WS 实测结论（16:37，隔离实例 18877）：`rot` → 选中「第一项」；`press ms=120` → 已答「第一项」；`press ms=900` → 已取消。三种手势的 WS 链路全通。<!-- project: path:/home/open/.openclaw/workspace -->
- 两个坑：隔离实例不加 `--no-gateway` 会连真实网关抢 session；清理别用 `pkill -f 'port 18877'`（会匹配到执行清理的 shell 本身），按 PID 精确 kill。<!-- project: path:/home/open/.openclaw/workspace -->
- 已把这些写进 `skills/pet-confirm/SKILL.md` 的「验证：手上没硬件时怎么验」一节。<!-- project: path:/home/open/.openclaw/workspace -->
- 服务端盲区已补（提交 `ce4a5e1`）：`on_device_message` 收到 `input` 先落一行「设备旋钮事件」日志 —— `rot_press` 在确认卡在场时被静默忽略，没这行就查不出事件到底来过没有。<!-- project: path:/home/open/.openclaw/workspace -->
- 实体设备旋钮事件至今仍是 **0 条**（补丁后也是 0）—— 物理旋钮一次都没按过，真机手感仍未验。<!-- project: path:/home/open/.openclaw/workspace -->

### gateway 订阅会推飞书会话（11-11 实测，推翻旧假设）
- `sessions.subscribe` 推来的原始行里**有**飞书：`agent:main:feishu:direct:ou_d1b4e7815a063128f882df928effe864`，字段 `channel = feishu`。**不需要**为飞书单独造本地 registry —— 数据早就流过来了。<!-- project: path:/home/open/.openclaw/workspace -->
- petd 后台 `/admin/api/sessions` 已经列出了这条飞书会话（4 条里就有它）。WebUI 不显示飞书后，**petd 后台反而是唯一能看见飞书会话的地方** —— 参照系不再是 WebUI。<!-- project: path:/home/open/.openclaw/workspace -->
- 真正的缺口是**展示层**：`public_sessions()` 没输出 `channel` 字段（`petd/app.py:877`），后台分不出通道；飞书会话的 `name` 退回 key 末段 → 显示成裸 open_id `ou_d1b4e781...`。<!-- project: path:/home/open/.openclaw/workspace -->
- 过滤规则不挡飞书：`state_rules.json` 的 exclude 只有 `{"isBackground": true}`，飞书行不是 background。<!-- project: path:/home/open/.openclaw/workspace -->
- 探针教训：手搓 gateway 协议（`connect` + `sessions.list`）会被 1008 policy violation 拒；**用 petd 自己的 `GatewayClient`** 才通。`sessions.list` 报「gateway 连接断开」是因为探针自己 cancel 了 connect 任务，不是真故障。<!-- project: path:/home/open/.openclaw/workspace -->

### 对外文章事实核对（11-11）
- 文章《从网页到墨水屏…》原写「428 项测试」—— 实际 481（加文档检查器后 486）。已改。<!-- project: path:/home/open/.openclaw/workspace -->
- **文章说「设备靠 mDNS 找 petd」是错的** —— 固件里已无 mdns 代码（`firmware/main/` 搜不到），现在配网手填 `IP:端口`，地址端口分别存 NVS。已改。<!-- project: path:/home/open/.openclaw/workspace -->
- 「20 齿旋钮帽不在仓库」是我看漏了 —— `enclosure/stl/bottom.stl` 就是它，`enclosure/README.md` 明确写了「KY-040 模块旋钮那一件」。<!-- project: path:/home/open/.openclaw/workspace -->
- OTA 的 URL 来源**真的支持**（`petd/ota.py:register_url`），不是只支持上传。<!-- project: path:/home/open/.openclaw/workspace -->
- 仓库里 `docs/故障排查.md`、`docs/游戏引擎.md` 一直写着「不做 OTA」—— 已在 `ed8336a` 改成「OTA 只由人点」。<!-- project: path:/home/open/.openclaw/workspace -->
- `docs/游戏引擎.md` 提到的「钓鱼」游戏不存在（实际是消砖块/贪吃蛇/骰子），但那几处是 `gauge` 原语的规划说明，保留。<!-- project: path:/home/open/.openclaw/workspace -->
- 新增 `tools/check_docs.py`：查文档里已确认过期的说法（不做 OTA / 不支持 OTA / 不能 OTA / 只能 USB 烧录），真拦 CI（不是 `|| true`）。需要引用原文的行加 `# check-docs: allow` 跳过。<!-- project: path:/home/open/.openclaw/workspace -->
- 文章说「加了个 CI 检查」原本是空头支票 —— 现在是真的。<!-- project: path:/home/open/.openclaw/workspace -->

### 对外文章事实核对（11-11）
- 文章《从网页到墨水屏…》原写「428 项测试」—— 实际 481（加文档检查器后 486）。已改。<!-- project: path:/home/open/.openclaw/workspace -->
- **文章说「设备靠 mDNS 找 petd」是错的** —— 固件里已无 mdns 代码（`firmware/main/` 搜不到），现在配网手填 `IP:端口`，地址端口分别存 NVS。已改。<!-- project: path:/home/open/.openclaw/workspace -->
- 「20 齿旋钮帽不在仓库」是我看漏了 —— `enclosure/stl/bottom.stl` 就是它，`enclosure/README.md` 明确写了「KY-040 模块旋钮那一件」。<!-- project: path:/home/open/.openclaw/workspace -->
- OTA 的 URL 来源**真的支持**（`petd/ota.py:register_url`），不是只支持上传。<!-- project: path:/home/open/.openclaw/workspace -->
- 仓库里 `docs/故障排查.md`、`docs/游戏引擎.md` 一直写着「不做 OTA」—— 已在 `ed8336a` 改成「OTA 只由人点」。<!-- project: path:/home/open/.openclaw/workspace -->
- `docs/游戏引擎.md` 提到的「钓鱼」游戏不存在（实际是消砖块/贪吃蛇/骰子），但那几处是 `gauge` 原语的规划说明，保留。<!-- project: path:/home/open/.openclaw/workspace -->
- 新增 `tools/check_docs.py`：查文档里已确认过期的说法（不做 OTA / 不支持 OTA / 不能 OTA / 只能 USB 烧录），真拦 CI（不是 `|| true`）。需要引用原文的行加 `# check-docs: allow` 跳过。<!-- project: path:/home/open/.openclaw/workspace -->
- 文章说「加了个 CI 检查」原本是空头支票 —— 现在是真的。<!-- project: path:/home/open/.openclaw/workspace -->

### 屏型钉死（11-11，防止我把话说到墨水屏上）
- AI Agent Pet 的屏是 **ST7789 2.4 寸 240×320 RGB565 彩色 LCD**（`esp_lcd_new_panel_st7789`），**不是墨水屏**，固件里搜不到任何 e-ink/EPD 驱动。<!-- project: path:/home/open/.openclaw/workspace -->
- 文章标题里的「墨水屏」指**前作 InkSight**（ESP32-C3 + 4.2 寸 GDEM042F86 e-ink，SSD2683ZA），那是叙事里的上一站，不是这台 Pet 的屏。<!-- project: path:/home/open/.openclaw/workspace -->
- **别把 e-ink 的刷新限制套到 Pet 上**：Pet 走 TJpgDec 流式直刷 + 逐行 diff，能做实时动作类游戏；我 11-11 因为文章词汇把「墨水屏刷新慢」当成了 Pet 的约束，结论下窄了。<!-- project: path:/home/open/.openclaw/workspace -->
- 自查口径：谈这台设备的显示特性前，先问「ST7789 还是 e-ink」。<!-- project: path:/home/open/.openclaw/workspace -->
### 游戏引擎真实约束（11-11 逐条核对代码，替代我之前口述版）
- 画布 240×320，逻辑 60Hz（`GAME_TICK_HZ 60`），逐行 diff 刷屏 —— **不是 e-ink 的速度限制**；但全屏重绘约 60ms（SPI 26MHz，153600 字节），只有小范围移动才够 60fps。<!-- project: path:/home/open/.openclaw/workspace -->
- **旋钮是单轴但有幅度**：`rot` 事件带带符号步数，`move` 效果按 `value × 步数` 移动 —— 相当于半模拟摇杆。<!-- project: path:/home/open/.openclaw/workspace -->
- **长按在游戏内只能退出**：PLAYING 里 `ms >= 600` → 触发 `press_long` intent 并 `quit_to_menu()`，蓄力/长按动作全部不可用。<!-- project: path:/home/open/.openclaw/workspace -->
- `turn` 一次手势最多转 90°（steps 夹到 1），快甩不会转一整圈。<!-- project: path:/home/open/.openclaw/workspace -->
- 规则**没有条件分支**：`on` 只匹配事件（可选 intent/target/other/timer），effects 无条件执行 —— 写不出 if/else/数值比较，胜负只能事件驱动。<!-- project: path:/home/open/.openclaw/workspace -->
- 固件**没有声音**（无 tone/ledc/buzzer）。<!-- project: path:/home/open/.openclaw/workspace -->
- 上限：实体 12 / 规则 24 / 意图 8 / 定时器 2 / 精灵 4×8帧×16×16×4色；网格图案 32×16、8 色。<!-- project: path:/home/open/.openclaw/workspace -->
- **没有 spawn fx**：实体只能预置；`randomize` 让 value 实体取随机值、让格子实体落到随机空格；波次靠 hide/show 复用实体池 + timer。<!-- project: path:/home/open/.openclaw/workspace -->
- pack 通道已通：固件从 ws_client 收，petd `_push_packs` 推；动态 pack 落 `petd/data/packs/`（当前空目录）。<!-- project: path:/home/open/.openclaw/workspace -->
- 现有 3 个 pack：`brickout` / `snake` / `dice`；`tools/lint_packs.py` 在 CI 校验枚举与能力表；pack 可自带中文字形（glyphs）。<!-- project: path:/home/open/.openclaw/workspace -->
- 砍俄罗斯方块的理由是「行满检测 + 双轴」缺，**不是**屏幕慢；保龄球/弹球砍在「二维瞄准」+「蓄力要用长按但长按被退出占用」。<!-- project: path:/home/open/.openclaw/workspace -->

- **游戏输入预算（PLAYING 内）**：`rot`=带符号步数（连续一轴）、`press`=单次按钮（长按=退出）、`rot_press`=按住时旋转（±1，可重复）→ 实际能凑出「一轴连续 + 一轴二值 + 一个修饰键」。<!-- project: path:/home/open/.openclaw/workspace -->
- **胜负判定可用的纯事件**：`overlap`（两实体相交）、`self`（蛇撞自己）、`oob`/`bounds`（出界/出画布）、`grid_hit`（撞网格实心格）、`hit`（实体撞实体）、`empty`（网格全清）。<!-- project: path:/home/open/.openclaw/workspace -->
- 结论：**没有比较原语** → 一切「比大小/三连/相同/分数达标」类玩法都要新增 cond 原语；纯事件判定只够做「对齐/命中/时机」类游戏。<!-- project: path:/home/open/.openclaw/workspace -->

### 打地鼠 + 两个通用原语（11-11 落地）
- 提交 `997ac88 feat(游戏): 加打地鼠 —— 引擎补 move_cell / intent_overlap 两个通用原语`，已推送 main。<!-- project: path:/home/open/.openclaw/workspace -->
- **新原语 `move_cell`**（fx 19）：网格实体按格移动（value=每步 dx 格，value2=每步 dy 格），旋钮转几步挪几格，越界夹在场地内。<!-- project: path:/home/open/.openclaw/workspace -->
- **新事件 `intent_overlap`**（ev 10）：意图触发时先看目标实体碰到了没有，碰到了才补发 —— 「敲下去打中了」的基础。判定在**意图规则执行之前**（先判定再改状态），否则判定会落在改完之后的位置上。<!-- project: path:/home/open/.openclaw/workspace -->
- **踩过的坑**：`ev_from_name`（`game_pack.c`）不认新事件名会**静默落回 `GAME_EV_START`** —— 主机测试用的是 `gen_host_scene.py` 自己的事件表、没走真实 cJSON 解析器，所以测试绿了但真机会把「敲击命中」规则当「开局」规则执行。**新增事件/效果名必须同时改 `game_pack.c` 的 ev_from_name 和 `gen_host_scene.py` 的 EV 表**。<!-- project: path:/home/open/.openclaw/workspace -->
- 另一个坑：`match_rule` 只对 `GAME_EV_INTENT` 按 intent 过滤，新事件也得加进去，否则规则匹配不到。<!-- project: path:/home/open/.openclaw/workspace -->
- 打地鼠输入：**旋钮**移列、**按住旋转**移行、**短按**敲击 —— 旋钮单轴，但「按住」补出第二个轴。<!-- project: path:/home/open/.openclaw/workspace -->
- 固件版本 `0.5.5` → `0.5.6`，release doc `docs/releases/fw-v0.5.6.md`。老固件会在解析阶段拒收打地鼠（不写盘、回 `packAck ok=false`、菜单里不出现），老三个游戏不受影响。<!-- project: path:/home/open/.openclaw/workspace -->
- 验证：主机侧引擎测试打地鼠 8 项 0 失败；pack lint 4 个包全过；全量 `486 passed, 2 warnings in 85.23s`。<!-- project: path:/home/open/.openclaw/workspace -->
- **真机未验**：旋钮手感、敲击节奏、等人都到设备旁边才能确认。<!-- project: path:/home/open/.openclaw/workspace -->

### 老固件拒收新 pack 的真实行为（11-11 核实）
- 固件里**没有**「该游戏需要更新固件」这句屏上文案 —— 老固件的真实行为是：`parse_pack` 阶段 `ESP_ERR_NOT_SUPPORTED` 拒收 → **不写 SPIFFS** → 设备回 `packAck ok=false` → 后台日志记「游戏包安装失败」→ **菜单里根本不出现这个游戏**。安全但不「可读」，玩家只会发现菜单少了一项。<!-- project: path:/home/open/.openclaw/workspace -->
- 这句错话在 `docs/游戏引擎.md` 能力协商段、`firmware/main/game/game_comp.c` 注释里早就存在，已由提交 `986eff3 fix(文档): 能力不足时没有屏上提示 —— 改成真实行为` 改成真实行为，并在「尚未实现」表里补了「能力不足的屏上提示」这一项。<!-- project: path:/home/open/.openclaw/workspace -->
- **教训**：写 release doc 说「失败要可读」之前必须 grep 固件源码确认那句文案真的存在。文档里的「屏上提示」这类描述最容易变成想象出来的功能。<!-- project: path:/home/open/.openclaw/workspace -->
- 后台已能列出打地鼠（`/admin/api/games` 里 whack enabled=false，builtin=true），设备在线 `fw=0.5.2`、`192.168.31.105`、`esp32s3-02349c`。`petd/data/config.json` 的 `ui.games` 当前是 `["brickout","dice","snake"]`。<!-- project: path:/home/open/.openclaw/workspace -->
- **打地鼠尚未在真机验**：设备固件 0.5.2 不含 `move_cell`/`intent_overlap`，勾选后会被拒收（这是预期行为）。要真玩必须先刷到 0.5.6。<!-- project: path:/home/open/.openclaw/workspace -->

### 真机实测：0.5.2 拒收打地鼠（11-11 现场跑过）
- 勾选打地鼠 -> 后台日志依次出现「游戏包已下发: whack」->「**游戏包安装失败: whack**」；同时 brickout/dice/snake 三个照常「已安装」。<!-- project: path:/home/open/.openclaw/workspace -->
- **设备没崩**：撤回后 `online=True fw=0.5.2 mode=idle`，frames 持续增长（32536），没有任何重启迹象。拒收确实只发生在解析阶段。<!-- project: path:/home/open/.openclaw/workspace -->
- 撤回后 `ui.games` 恢复 `["brickout","dice","snake"]`，后台接口确认。<!-- project: path:/home/open/.openclaw/workspace -->
- 判定依据（先查源码再动手）：`git show fw-v0.5.2:firmware/main/game/game_pack.c` 里 `game_cap_supported` 存在（命中 1 处），且 0.5.2 的 `s_caps[]` 里 `move_cell`/`intent_overlap` 命中 0 次 —— 确认会被 `ESP_ERR_NOT_SUPPORTED` 拒，才敢在真机上试。<!-- project: path:/home/open/.openclaw/workspace -->
- **方法论**：拿真机试之前先用 `git show <tag>:<file>` 确认那一版固件**自己会怎么拒**，否则风险不可控。<!-- project: path:/home/open/.openclaw/workspace -->

### 根因：0.5.4 起的 CONNECTING 死循环 = WS 回调任务栈溢出（11-11 定位并修复）
- **症状**：刷上 0.5.4 后设备永远 CONNECTING。抓包（方向分离，`/tmp/ws_dir.py`）：设备->服务端 TEXT 帧 **0** 个，服务端->设备 CLOSE **1002**，服务端「设备接入」记录 **0** 次。设备完成了 TCP + HTTP 升级（`101 Switching Protocols`、`Sec-WebSocket-Protocol: petd.v1`），但**一个 hello 帧都发不出**。<!-- project: path:/home/open/.openclaw/workspace -->
- **根因**：`esp_websocket_client` 1.8.0 的事件回调跑在**它自己的任务**上，那个任务栈默认只有 4KB（组件源码 `#define WEBSOCKET_TASK_STACK (4*1024)`，可用 `.task_stack` 覆盖）。0.5.4 的 `627d989` 把 hello 缓冲 768 -> 1280，`send_hello()` 局部变量从约 1.6KB 涨到约 2.2KB，回调栈溢了 —— **溢在发帧之前**，所以表现为「握手成功但一句话都不说」。<!-- project: path:/home/open/.openclaw/workspace -->
- **这是同一类问题的第三次**：`a8278f5`（装游戏包挪出 WS 回调栈）、`5e45ec2`（游戏 pack 改走堆）。凡是 WS 回调里的局部变量，栈预算只有 4KB。<!-- project: path:/home/open/.openclaw/workspace -->
- **修法**（提交 `e87d548`，tag `fw-v0.5.7`）：`send_hello()` -> 独立 `hello_task`（CONNECTED 回调只入队）；hello 的 buf/packs/ids 全走 malloc；`.task_stack` 显式设 8192；`ota_mark_valid()` 延后到收到 **welcome** 之后（服务端认了 hello 才算新固件跑得起来，收不到就留待验证让 bootloader 回滚）；welcome 里 games 中转数组改静态区。<!-- project: path:/home/open/.openclaw/workspace -->
- **教训**：固件里凡是「跑在 esp_websocket_client 回调栈上」的代码，栈预算 = 4KB。本地大数组/大缓冲一律走堆或静态区；复杂逻辑一律挪到自己的任务。<!-- project: path:/home/open/.openclaw/workspace -->
- **排查方法论**：早期「设备只发空帧」的判断是错的 —— 混合方向 + 未解掩码的解析产物。必须**方向分离**抓包并解掩码后再下结论。<!-- project: path:/home/open/.openclaw/workspace -->
- **状态**：主机侧 `491 passed`；真机未验（刷机、握手、旋钮输入都要人在设备旁边）。0.5.4/0.5.5/0.5.6 都带同一缺陷，**别直接刷**。<!-- project: path:/home/open/.openclaw/workspace -->
