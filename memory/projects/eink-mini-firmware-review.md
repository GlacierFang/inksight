# eink-mini 固件外部评审：Gemini 反馈（2026-11-18）

来源：冰河大人转来的一份 Gemini 审校意见（逐行审 firmware/src/）。
本文档存的是**核实后的结论**，不是原文照抄 —— 原文在会话记录里。

结论一句话：**4 条成立（2 条致命），3 条是误判（Gemini 没看到仓库实际结构）。**

## 成立并已修

### 1. ink_keys.cpp: poll() 没调 update() —— 致命

poll() 只读 pendingEvent / pendingClickAt，而这两个字段**只由 update() 写**。
update() 写好了、声明了、但全仓库没有任何地方调用它。

结果：按键永远不产生事件，整机零交互。

已修：poll() 开头加引脚采样循环。

### 2. main.cpp: renderCurrentPage() 缓存优先 —— 致命

原逻辑「有缓存就直接用缓存」，缓存一旦有了就再也不拉新帧。
而 loop() 里 bitmap 页每 30 秒调一次 renderCurrentPage() —— 每次都命中缓存。

结果：天气页永远停在开机那一帧。30 秒刷新形同虚设。

已修：先拉新帧，成功则更新缓存；拉取失败才回退缓存。

（附带：gotoPage() 补 g_lastClockSec = 0，切回时钟页立刻重画。）

### 3. ink_net.cpp: syncTime() 没收 Date 头

ESP32 的 HTTPClient 默认不收集响应头，不声明要哪个头，
http.header("Date") 永远返回空串。

结果：HTTP Date 兜底校时从来没生效过（SNTP 成功时看不出来）。

已修：http.GET() 之前 collectHeaders({"Date"}, 1)。

### 4. main.cpp: 自检画面 GRAY CHECK 越界

y=126，屏高 128，字高 7 —— 下半截被切掉。已改为 y=118。

### 5. ink_config.h: B 键默认脚（半条成立）

Gemini 说「GPIO16 在 ESP32-C3 上不存在」——**这条对**。
ESP32-C3 的 GPIO11~17 内部接 Flash，模块上没引出来，配成 16 固件起不来。

但 Gemini 给的整套引脚表是**错的**（它把 CS/DC/RST/BUSY 整个重排了），
照它改会把 README 里已确认的接线表作废。

已修：只改 B 键默认脚 —— GPIO16 -> **GPIO9（板载 BOOT 键，零接线）**，
并加编译期引脚冲突检查（KEY 与屏的引脚撞车直接 #error）。
想接第二颗外接开关就改成 GPIO2。

## 误判（不采纳）

### platformio.ini 缺 lib_deps —— 不成立

GxEPD2 是 **vendored** 在 firmware/lib/GxEPD2/，PlatformIO 自动收录 lib/，
不需要 lib_deps。而且 CI run 44118223158 已实测编译通过 —— 真缺依赖根本编不过。

照 Gemini 的加 lib_deps 反而会引入**第二份 GxEPD2**（联网拉的），
和 vendored 那份符号冲突，还破坏了刻意保留的上游 GPL-3.0 许可证安排。

### partitions.csv 是空文件 —— 不成立

实际 255 字节，标准 4MB 分区表。Gemini 读到的是 0 字节版本。

### 引脚表要整体重排 —— 不成立

Gemini 给的 CS=10 / DC=3 / RST=1 / BUSY=0 与 README 已确认的接线表
（CS=1 / DC=4 / RST=10 / BUSY=3）完全不同。照它改，硬件接线和固件就对不上。

## 还没验证的（真机）

以上全部是静态核实和 CI 编译，**没有一个经过真机烧录**。
硬件行为（BUSY 极性、旋转方向、局刷残影、B 键实际脚位）仍未验收。

相关：[[eink-mini]] <!-- project: path:/home/open/.openclaw/workspace -->
