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

---

# 第二轮评审（2026-11-18 下午）

又一份 Gemini 审校意见。核实结果：**3 条里 2 条成立、1 条是它第三次重复的误判**。
另外它完全漏掉了三个真 bug，其中一个是致命的。

## 成立并已修

### 1. 没配网/断网时卡在自检画面（Gemini 提到）

原 loop 里 `isClock && g_timeOk` 才进时钟分支。`g_timeOk` 为 false 时
三条分支全不进，屏幕永远停在 SELF TEST。

改法：不看 `g_timeOk`，看 epoch 本身。`currentEpoch()` 返回 0 表示没校时，
渲染器画成 `--:--` + `NO TIME - CHECK WIFI`。SNTP 在 syncTime() 的 12 秒
窗口之后成功也能自己活过来，不用重启。

### 2. 开机前 30 秒内切页不刷新（Gemini 提到）

`gotoPage()` 靠 `g_lastPageFetchMs = 0` 凑「毫秒差 > 30 秒」，那个条件在
开机前 30 秒内恒为假。非 bitmap 页更是完全不走那条分支。

改法：加 `g_dirtyPage` 布尔标志。切页 / 配网完成 / 首次上电 / 计时器状态
变化都置位，loop 里统一判断。

### 3. fillRect 循环轴反了 —— 致命（Gemini 漏了）

    for (int j = 0; j < h; j++) vLine(x, y + j, w, black);

`vLine(x, y, h)` 是「第 x 列画 h 像素高」，所以画出来是 1 像素宽的竖条，
不是 w×h 的矩形。`drawRect` 用 `hLine`+`vLine`，语义是对的 —— 两个函数
不一致，`fillRect` 这边是错的。

后果：数码管 7 根段全成竖线 → 时钟页数字、冒号、页脚方块、计时器档位
方块、进度条、自检 8×8 棋格，全是竖条。**时钟页是第一页，真机首次点亮
屏幕上就是一堆竖线。**

**CI 编译查不出来** —— 语法没错，逻辑错。是靠「主机上编译固件渲染器
（ink_canvas.cpp + ink_pages.cpp）渲染出图」发现的。

### 4. 配网完成后没有重新校时（Gemini 漏了）

原来只在 setup 里 `syncTime()` 一次。配完网 `g_timeOk` 还是 false，
时钟页一直停在 `--:--`。改：配网完重新 `syncTime()`。

### 5. 计时器秒数与时钟页共用一个变量（Gemini 漏了）

`g_lastClockSec` 被时钟页（epoch）和计时器页（剩余秒）共用，语义不同，
切页容易漏画。拆成 `g_lastClockSec` + `g_lastTimerSec`。

## 误判（第三次重复）

### platformio.ini 要补 lib_deps / partitions.csv 要补全

- GxEPD2 是 vendored 在 `firmware/lib/GxEPD2/`，PlatformIO 自动收录。
  CI 实测编译通过。照它改反而会拉进第二份 GxEPD2 与 vendored 符号冲突，
  还破坏刻意保留的上游 GPL-3.0 许可证安排。
- `partitions.csv` 实际 255 字节标准分区表，不是空文件。

这是 Gemini **连续两轮**提的同一条。第一轮已驳回并写进本文档，它没读。

## 交互设计：待冰河大人定

Gemini 指出「计时器操作全绑在板载 BOOT 键上，要抠背面的小硬键，很别扭」——
**这条意见成立**。

事实（`memory/2026-11-17.md`）：只焊了一颗外接 12×12×12 微动开关（A 键，
GPIO5），其余微动开关**只买不焊留储备**。B 键 = 板载 BOOT 键（GPIO9，零接线）。

所以现在的计时器操作（启停/换预设/重置）全在 BOOT 键上，手感确实差。

建议方案（尚未实施）：

    计时器页  A 短 = 启停/暂停/继续/DONE 后重开
              A 双 = 换预设（RUNNING 时忽略；顺带把状态清回 READY）
              A 长 = 下一页（离开计时器）
              B 长 = 进配网（全页通用，低频）
              B 短/双 = 无操作

这样「重置」不需要独立手势 —— `cyclePreset()` 在非 RUNNING 时本来就会
把状态清回 READY 并装满。

代价：计时器页上「上一页」不可直接达（靠 B 双回第一页兜底），且 A 键在
计时器页的语义与其他页不同（600ms 长按才能离开）。

## 仍未验证

所有固件改动只经静态核实 + CI 编译 + 主机渲染出图，**没有一处真机烧录**。
硬件行为（BUSY 极性、旋转方向、局刷残影、B 键实际脚位）仍未验收。

相关：[[eink-mini]] <!-- project: path:/home/open/.openclaw/workspace -->
