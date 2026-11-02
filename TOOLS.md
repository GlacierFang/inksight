# TOOLS.md - 环境事实速查

<!-- project: path:/home/open/.openclaw/workspace -->

> 硬件 / 网络 / 端口 / 路径 / 服务清单。**按需读，不每轮注入**。

> 📌 2026-08-07 16:25 测试：助理 edit 工具可用性验证

## 硬件环境

- **设备：** Raspberry Pi 4B 8G
- **存储：** 128GB SSD（ext4，挂载点 `/`，已用约 12%）
- **启动介质：** SD卡（mmcblk0,bootfs + rootfs 分区）

### 电子墨水屏（InkSight 墨水屏设备）

- **色数：四色** —— 黑 / 白 / 金黄 `#E8B000` / 朱红 `#C80000`
- **屏尺寸：** 400 × 300
- **色数定义处：** `skills/inksight/backend/core/config.py` 的 `EINK_4COLOR_PALETTE`（扁平 int 列表，每色 3 通道）
- **屏上没有灰。** 浅阴影要用金黄网点表现，灰网点会被 Atkinson 抖成脏黄网纹
- 生图提示词要把四个色值直接写死给模型，别让它自由发挥 —— 参考 `core/xiaojing.py` 的 `_PALETTE_SPEC`（同类：同样每天一张画）
- ⚠️ 不要凭印象以为它是 2 色屏 —— 这个错误在狸行记里犯过，还被当成了禁用上色的理由

## 网络与地址

- **主机名：** openclaw
- **操作系统：** Debian Linux 6.12.62+rpt-rpi-v8 (aarch64)
- **Gateway 端口：** 18789（局域网可访问）
- **Tailscale：** 关闭

## 聊天渠道

- **飞书：** appId `cli_a9259b2e6738dcd6`，domain `feishu`，webhook `/feishu/events`
- **微信：** ❌ 插件损坏（v2.4.1 dist/index.js 为 TypeScript 源码，无法运行）；功能已迁移至飞书

## 客户端

- **OpenClaw Windows Tray (cli)：** OpenClaw Windows 桌面客户端，外网通过 frp 隧道连接 `ruohan.site:18789`

## 已安装插件

- tavily（网页搜索）
- minimax
- feishu ✅
- openclaw-weixin ❌（已停用，功能迁移至飞书）

## Skills

- **dashboard** — 个人工作台（Dashboard）
- **gallery** — 相册（照片、视频、缩略图）
- **library** — 知识库（books/covers/index/pdfs）
- **visit-knowledge** — 个人知识管理
- **my-island** — 梦见岛（Life Island 汉化版,后端 18825,手机号+密码鉴权,内网使用）
- **躺平巴拉** — tangping（后端 18899,/tangping/,沉浸式水豚养成游戏）

## 服务端口与路由

> **真相源：`tools/port-registry/ports.json`** — 端口分配以此为准，TOOLS.md 是导出版
> 详细文档：`memory/projects/端口与路由配置.md`
> ⚠️ 修改前必读：新项目开发流程为 **pr find-free → pr register → Nginx 配置 → systemd → 代码 → pr verify**
> systemd scope 原则：**Web 页面 → user scope | daemon/push/timer → system scope**

### 端口分配（user scope / Web 服务）

| 端口 | 服务 | 路径 | 说明 |
|------|------|------|------|
| 18789 | Gateway | - | 主服务（外网：ruohan.site:18789，Windows App 直连） |
| 18790 | Gallery | /gallery/ | 相册 |
| 18792 | Library | /library/ | 电子书库 |
| 18794 | Dashboard | /dashboard/ | 个人工作台，含 Token Plan 百分比配额显示 |
| 18796 | visit-knowledge | /visit/ | 个人知识管理 |
| 18797 | knowledge-center | /knowledge/ | 知识中心 |
| 18800 | stamp-collection | /stamp/ | 邮票收藏 |
| 18801 | food-recognition | /food/ | 膳品志（食材识别+知识生成） |
| 18803 | nature-recognition | /nature/ | 识物志 |
| 18804 | work-assistant | /work/ | 新闻订阅 |
| 18806 | scraps | /scraps/ | 灵感碎片 |
| 18807 | interactive-novel | /novel/ | 互动小说生成 |
| 18808 | todo-list | /todo/ | 待办事项 + 提醒 |
| 18888 | ai-assistant | /ai-ass-v/ | AI Assistant 发行版（Dashboard + Gallery + Library + Nature + Knowledge + Creative） |
| 18809 | my-train | /train/ | 驿车志（N比例列车识别） |
| 18810 | flow-work | /flow/ | AI-Agent联合工作流 ⚠️ 开发中禁区 |
| 18815 | brain | /brain/ | AI 个人副脑 |
| **18819** | **creative-app** | **/rumengling/** | **如梦令 App 后端**（创意工坊移动端，图像+音乐生成） |
| **18840** | **sanxia** | **/sanxia/** | **伞下 App 后端**（情绪庇护 LLM 代理，2026-10-12 新增 · 后端桩已起） |
| **5173** | **my-island-vite** | **/my-island-web/** | **my-island Vite dev 前端**（systemd user unit `openclaw-my-island-vite.service`，2026-08-10 加，nginx `/my-island-web/` 转这里） |
| **18825** | **my-island** | **/my-island/** | **my-island App - Life Island 汉化版**（2026-08-07 由梦华里端口复用） |
| **18899** | **tangping** | **/tangping/** | **躺平巴拉 App 后端**（沉浸式水豚养成游戏） |
| **18838** | **shanhe** | **/shanhe/** | **山河图记 App 后端**（地理研学，11 章；研学 v2 每章 1 主题） |
| **18841** | **little-prince** | **/little-prince/** | **小王子の日历 App 后端**（2026-10-27 新增；每日生图 + 台词，systemd `openclaw-little-prince.service`） |

### 端口分配（system scope / daemon & 内部）

| 端口 | 服务 | 路径 | 说明 |
|------|------|------|------|
| 80 | Nginx | - | 反向代理入口 |
| 18791 | Gateway Internal | - | 内部 RPC（loopback） |
| 18795 | organize | - | 队列处理 |
| 18802 | deepseek-webui | - | DeepSeek WebUI 后端（无对外路径） |

### daemon & push（无端口）

| 服务 | 触发 | 说明 |
|------|------|------|
| bt-presence | 常驻 daemon | 蓝牙感知（回家/离家推送，**user scope**） |
| scraps-push | 常驻 daemon | 灵感碎片推送（system scope） |
| little-prince-pregen | timer 18:00 | 小王子日历次日预生成（system scope `openclaw-little-prince-pregen.timer`） |
| unit-watch | timer 每小时 | systemd failed unit 新增监控（system scope，零 token，飞书推送） |
| certbot.timer | 已 mask | 证书续期空转（证书 10-01 已过期，ruohan.site HTTPS 不通），已 mask 不再报 failed |
| natfrp | 常驻 daemon | 内网穿透 frpc（system scope `sakura-frp.service`，承载 ruohan.site 隧道；配置 `~/.natfrp/frpc.ini`） |

### 外网访问 ruohan.site（2026-10-24 实测）

- **只有 HTTP 可用**：`http://ruohan.site/` 与 `http://ruohan.site/ink/` 均 200
- **HTTPS 不可用**：`https://ruohan.site/` 报 `tlsv1 unrecognized name`（TLS alert 112）。原因是 frpc 只配了 `type = http` 隧道，frp-cup 边缘节点没有该域名的 SNI/证书，443 直接拒绝握手；无 SNI 时 edge 也不返回证书
- **子域名一律 503**：frpc 只绑 `custom_domains = ruohan.site`，`www.ruohan.site` / `xia.ruohan.site` 返回 503（后者连 DNS 都没有）；只能走路径前缀
- 边缘节点 IP `45.95.212.18`，80/443 均开放但 443 无可用证书

### Nginx 配置

- 文件：`/etc/nginx/sites-enabled/openclaw`
- 修改后：`sudo nginx -t && sudo nginx -s reload`
- **新增服务流程**：选端口 → 配 Nginx → 验证 200 → 启动后端 → 更新文档

### 端口中央登记表（2026-06-06 建）
- **位置**：`tools/port-registry/ports.json` + `port-registry.py`
- **职责**：端口分配的唯一真相源（Single Source of Truth）
- **必走流程**：新增服务前先 `register`，改 nginx 后必 `verify`
- **TOOLS.md 端口表是导出品**，以 `ports.json` 为准
- 详细：`tools/port-registry/README.md`
- 范围：skills 用 18790-18899，utilities 用 18900-18999

### systemd unit 范围约定（2026-06-06 修）
- **system 范围（/etc/systemd/system/）**：核心服务，systemd 启动后接管
- **user 范围（/home/open/.config/systemd/user/）**：开发者工作区服务
- **不允许同名 unit 在两个范围同时 enable**（双启动会冲突）

### 已知进程 PID

```
1001 Gallery(18790) | 1003 knowledge-center(18797) | 1004 Library(18792)
1007 organize(18795) | 1008 scraps(18806) | 1012 stamp(18800)
1075 nature(18803)
1079 work-assistant(18804) | 220057 visit(18796) | 256587 usage-stats(18900)
262692 Dashboard(18794)
```

## 数据库路径

| 服务 | 路径 |
|------|------|
| Gallery | `/home/open/.openclaw/media/gallery.db`（软链 → safe_storage）|
| Library | `/home/open/.openclaw/workspace/skills/library/library.db` |
| Library 数据 | `/home/open/.openclaw/workspace/library/`（index/books/covers/pdfs）|
| bt-presence | `/home/open/.openclaw/workspace/skills/bt-presence/bt_queue.json`（事件队列）|
| Dashboard | `/home/open/.openclaw/workspace/skills/dashboard/data/metrics.db` |

## Memory

- `memory/YYYY-MM-DD.md` — 每日工作记录
- `memory/projects/` — 各项目详细记录
- `MEMORY.md` — 长期记忆精华版

---

Add whatever helps you do your job. This is your cheat sheet.

---

## Cron Job 管理（2026-04-12 补充）

### 创建 cron job
- **cron add** 在 LAN session 报 pairing required
- **替代方案**：手动写入 `~/.openclaw/cron/jobs.json` + `sudo systemctl restart openclaw-gateway`
- **验证**：`cron list`（需等待几秒让 gateway 加载）

### systemctl --user 必加环境变量（2026-06-05 补）
- 各种 exec 上下文里直接 `systemctl --user` 会报：
  `Failed to connect to user scope bus via local transport`
- **修复**：在命令前加两行
  ```bash
  export XDG_RUNTIME_DIR=/run/user/$(id -u)
  export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u)/bus
  ```
- **根因**：gateway 重启后 user systemd scope 上下文丢环境，每次新 exec 都要重设
- **受影响命令**：`systemctl --user *`、`journalctl --user *`
- **快捷 alias**（建议加到 `~/.bashrc`）：
  ```bash
  alias osc='export XDG_RUNTIME_DIR=/run/user/$(id -u) && export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u)/bus && systemctl --user'
  ```

### 新增 systemd --user 服务标准流程（2026-06-05 补）
1. 写 `/home/open/.config/systemd/user/openclaw-<name>.service`（参考 openclaw-gallery.service 模板）
2. `osc daemon-reload` + `osc enable` + `osc start`
3. **Type=simple + Restart=always + RestartSec=5** 是默认配置
4. **WorkingDirectory** 必须设，否则 Flask 找不到相对路径的 templates/static
5. service 文件不需要 `chmod +x`

---

> 2026-10-21 从 AGENTS.md 迁出（为省 bootstrap context 体积）。按需读。
