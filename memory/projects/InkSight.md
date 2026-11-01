# InkSight 项目文档

> 创建时间：2026-10-21 17:35
> 最后更新：2026-11-01 15:40
> 状态：🟢 后端（18834）+ WebApp（18835）已部署；**新墨水屏已到货、组装完毕、设备一切正常**
> 上游仓库：https://github.com/datascale-ai/inksight
> 许可：MIT（可自由二开，保留版权声明）

---

## 零、拍板记录（2026-10-21 对齐结论 · 优先于本文档其他章节）

这几条是冰河大人明确拍的板，**不要再重复讨论、不要再问**：

| # | 结论 | 说明 |
|---|---|---|
| 1 | **刷机用官方站** | inksight.site 的 flash 页直接用。我们不部署刷机页，不做 WebSerial 打包 |
| 2 | **本地做控制网页 + 开发新的功能 JSON** | 控制界面 = 部署上游 WebApp（见第 5 条）；新内容 = 新增模式 JSON 文件，不改代码 |
| 3 | **账号层留（上游全套）** | 注册/登录/设备绑定/邀请码/模式广场整套保留，我们自部署这套账号体系。**已多次表态，不再问** |
| 4 | 控制网页挂 ruohan.site/ink/（路径前缀，不用子域名） | frp 只绑了 ruohan.site 这一个 Host，其他 Host 直接 503；ink.ruohan.site 无 DNS 解析 |
| 5 | **部署上游 WebApp**（2026-10-22 拍板） | 成本最小：账号 / 设备配置 / 个人信息页 / 刷机页全部原生可用，不自己写页面 |

### ✅ 硬未知已解决（2026-10-22）：设备可以指向我们的后端，固件一行不改

**结论 = 情况 a（最理想）。**

证据（只读上游代码 + 文档）：
- `docs/button-controls.md`：开机长按 BOOT 键强制进 Captive Portal，适用场景明写「修改了后端服务器地址」
- `firmware/data/portal_html.h`：配网页第一屏就是「服务器地址」输入框
  ```js
  var OFFICIAL_SERVER='https://web.inksight.site/';
  var LOCAL_DEFAULT_SERVER='http://本地服务IP:8080';
  ```
  校验只要求以 http:// 或 https:// 开头 → 可填任意地址
- `firmware/src/portal.cpp` 的 `/info` 路由回显 `cfgServer` → 可改且可读
- 固件所有请求都拼 `cfgServer`（claim-token / heartbeat / config / render / alert-bmp），改一处即全切

**我们填 `http://ruohan.site/ink`** → 固件请求 `http://ruohan.site/ink/api/xxx` → nginx 已把 `/ink/api/` 转发到后端 18834。✅

---

## 一、项目定位

**一句话**：基于开源项目 InkSight（墨鱼），做一台放在书桌上的墨水屏桌面伴侣，内容来自我们已有的数据资产。

**分工**（2026-10-21 冰河大人拍板）：

| 部分 | 负责人 | 状态 |
|---|---|---|
| 硬件（采购、焊接、组装） | 冰河大人 | 🟢 新屏已到货、组装完毕，设备一切正常 |
| 软件（后端 + 控制网页 + 内容开发） | 麻辣小龙虾 | ✅ 后端 + WebApp 均已部署 |
| 内容做哪些 | — | 🟢 自研模式已上线并注册生效（见 `modes-ours/`） |

---

## 硬件现状（2026-11-01 更新）<!-- project: path:/home/open/.openclaw/workspace -->

- 设备：ESP32-C3 Pro mini + 4.2 寸墨水屏（大连佳显 GDEM042F86，400×300，黑白红黄四色，SSD2683ZA 驱动）
- 2026-10-27 已到手、已刷机、已配网，服务器地址 `http://ruohan.site/ink`，真实拉图跑通
- **2026-10-30：原墨水屏损坏**
- **2026-11-01：新墨水屏已到货、组装完毕，设备一切正常**（此前文档记的「屏幕已损坏，等新屏到货」是过期信息）
- 设备按键只有 RST / BOOT，**没有翻页键**，屏上内容靠定时唤醒轮播

---

## 二、这是什么项目（上游）

InkSight 创建于 2026-03，最后提交 **2026-07-16**（已停滞约 3 个月），775★ / 136 fork，MIT 许可。

| 层 | 技术栈 | 职责 | 对我们 |
|---|---|---|---|
| **固件** | PlatformIO / Arduino，ESP32-C3 | 墨水屏驱动、深睡唤醒、5 路 WiFi 顺序重连、Captive Portal 配网、按键交互 | ✅ **直接用**，官网刷 |
| **后端** | FastAPI + Pillow | 30 个 JSON 模式渲染成 1-bit BMP；设备协议；两个 SQLite | ✅ **核心，已本地部署** |
| **WebApp** | Next.js 16 + React 19 + Tailwind v4，9 页面 | 官网、WebSerial 刷机、账号、设备配置 | ✅ **已部署**（18835，basePath=/ink） |
| **Expo App** | expo-router | 手机端 | ❌ 不用（我们已有 5 个 uniapp App） |

### 关键资产：模式系统是纯 JSON 驱动的

backend/core/modes/builtin/ 下 **30 个 JSON 文件**（另有 en/ 目录 28 个英文版）定义全部内置模式：

- 天气 / 诗词 / 黄历 / AI 简报 / 习惯 / 倒计时 / 相框 / 词表复习 / 日程 / 菜谱 / 谜语 / 故事 / 禅 / 斯多葛 / 每日推荐 …
- **新增一个模式 = 写一个 .json**（content.prompt_template + output_schema + layout + layout_overrides），不写 Python
- layout 用 16 种 block type 拼装（text / section / list / two_column / conditional / key_value / big_number / progress_bar / image …），另有 16 种 layout preset（poetry_card / memo_card …）
- layout_overrides 支持按屏幕尺寸（296x128 / 648x480 / 800x480）各自排版

**content.type 六种**：llm / llm_json / static / computed / external_data / image_gen / composite

### 设备协议（我们能直接用的接口）

| 接口 | 用途 |
|---|---|
| GET /api/render | 设备拉取 BMP（固件调用） |
| GET /api/preview | 浏览器预览 PNG |
| GET /api/widget/{mac} | 只读 PNG，可塞 iOS Scriptable / Android KWGT 小组件 |
| POST /api/device/{mac}/alert → GET .../alert-bmp | **OpenClaw 主动推送告警到墨水屏**（上游自带 openclaw_skill/inksight_alert） |
| POST /api/device/{mac}/apply-preview | 塞一张 PNG，设备下次刷新直接显示 |
| POST /api/device/{mac}/switch / /refresh | 切模式 / 强制刷新 |
| GET /api/firmware/releases | 固件列表（上游 = GitHub Releases，我们 = 本地目录） |

**注意**：上游的 /config /preview /dashboard /editor 四个老页面**已从后端删除**，后端只做 307 重定向到 webapp（靠 INKSIGHT_PRIMARY_WEBAPP_URL），webapp 没起就 410。**上游文档滞后于代码**，读代码优先于读文档。

---

## 三、实测结论（2026-10-21 在本机跑过）

### 已验证 ✅

- **后端在本机跑通**：/api/health /api/modes /api/modes/catalog /api/firmware/releases/latest /api/preview /api/widget 均 200
- **后端已 systemd 常驻**：openclaw-inksight.service（user scope）→ 127.0.0.1:18834，/api/health 200，返回 {"status":"ok","version":"1.1.0"}
- **MiniMax 走 openai_compat 通道可用**：https://api.minimaxi.com/v1 + MiniMax-M3.1-Flash，出图成功（渲染出的诗词卡片正常）
- **告警链路端到端通**：POST /api/device/{MAC}/alert（X-Agent-Token）→ GET .../alert-bmp 返回 400×300 BMP（BM magic, 15662 bytes）
- **本地固件源可用**：skills/inksight/firmware/v0.6.0/ 下 25 个 bin / 29M，/api/firmware/releases 返回 source: "local"，下载接口实测 200
- **依赖装法**：上游 requirements.txt 锁 Pillow~=10.1，**本机 Python 3.13 + arm64 装不上**（无预编译轮子）→ 必须放宽版本锁
- **WebApp 构建通过**（2026-10-22）：Next.js 16.1.6 production build EXIT=0，30 个页面全出，routes-manifest 确认 `basePath: /ink`
- **外网全链路通**（2026-10-22）：`http://ruohan.site/ink/` 页面 200、`/ink/api/health` 200、固件下载 1.19MB 200；原有站点 dashboard/shanhe/gallery/nature/studio 全 200，未回归
- **设备真实拉图已通**（2026-10-27 起）：固件已刷、已配网（服务器地址 `http://ruohan.site/ink`），nginx 日志 65 次真实 `GET /api/render`（`bpp=2&colors=4`），固件零改动。原屏 2026-10-30 损坏，**2026-11-01 新屏到货组装完毕，设备一切正常**

### 未验证 ❌

- ~~**墨水屏实际观感与刷新速度**~~ ← 2026-11-01 新屏到货，组装完毕，设备一切正常
- **固件编译**（本机无 PlatformIO）未做，固件用官方站刷
- **账号链路端到端**（注册 → 配对码 → 绑定设备）未实测
- 语音链路（百炼 ASR/TTS，需 WROOM32E + 麦克风扬声器，C3 不支持）

### 上游已做的修复（改代码前必看）

core/content.py 两处（本机已改 + 已验证，备份 core/content.py.bak.20261021-1655）：

1. openai_compat 的 max_tokens 默认 120 → 2048
   **原因**：推理型模型（MiniMax M3 / Claude / GPT-5 系）把 token 预算花在 reasoning 上，120 截断后正文为空 → content=None → AttributeError
   **实测**：max_tokens=120 → finish_reason=length，正文被截断；max_tokens=1024 → finish_reason=stop，JSON 完整
2. response.choices[0].message.content.strip() → (… .content or "").strip()
   **原因**：部分 provider 截断时返回 content=None

---

## 四、坑清单（都是实测的，不是推测）

1. **依赖版本锁要放宽** — Pillow~=10.1 在 Python 3.13 arm64 编译失败。上游 target 是 3.9/3.10
2. ~~**默认 LLM 配置不可用**~~ — **2026-10-22 已解决**：默认 provider 改为 minimax/MiniMax-M3，凭证走后端 .env 的 MINIMAX_API_KEY（见第九章）。用户侧零配置
3. ~~**configs 表没有 base_url 字段**~~ — **不再需要**：个人端 BYOK 已整体下线，base_url 永远取服务端内置值
4. **告警是内存态 + TTL 只有 60 秒** — 设备深睡周期通常 60 分钟，醒来时告警已过期。**已修**（见第五章）
5. **上游已停滞 3 个月**，24 个 open issue 里有 "feat: add MiniMax TTS backend" 没人合 → 注定自己维护 fork，拿不到上游合并红利
6. **本机 nginx 只有 listen 80，无证书** — 设备局域网走 http 没问题，跨网要另想办法
7. **文档数量对不上** — docs 里写 22/24/27 个模式，实际 30 个 builtin json；读 core/modes/builtin/ 目录为准
8. **frp 只绑 ruohan.site 一个 Host** — 用 Host: www.ruohan.site 或 ink.ruohan.site 访问都返回 503；ink.ruohan.site 也没有 DNS 解析。**所以只能用路径前缀，不能用子域名**
9. **Dashboard 根路径有 22 个 /api/* 路由**（/api/stats /api/memory /api/publish …）— InkSight 的 /api/* 绝不能挂在站点根上，必须带 /ink/ 前缀
10. **npm ci 只装生产依赖** — 首轮 npm ci 后缺 typescript/tailwindcss（构建不了），需 npm install --include=dev 补齐
11. **nginx sites-enabled 里的 .bak 文件照样被加载** — `include /etc/nginx/sites-enabled/*` 会加载所有文件，包括 openclaw.bak.20261014，导致「conflicting server name ruohan.site」警告。已移到 /etc/nginx/backups/old-sites-enabled/
12. **上游 docs/deploy.md 就是私有部署入口，我第一轮没读** — 我只挑了 5 个技术文档读，跳过 deploy.md / button-controls.md / config.md / flash.md / website.md，结果把「设备能不能指向我们」当成未知项反复讨论。**读上游文档要全读，别挑。**
13. **nginx 与 Next.js 对尾斜杠的期望相反 → 跳转死循环（09:33 修）** — Next.js `basePath=/ink` 下 `/ink/` 会 308 回 `/ink`，而我写的 `location = /ink { return 301 /ink/; }` 又把 `/ink` 打回 `/ink/` → 无限互跳 → 浏览器 `ERR_TOO_MANY_REDIRECTS`。
    - 修法：`= /ink` 改成 `proxy_pass http://127.0.0.1:18835`（直转不跳），并加 `= /ink/` → `proxy_pass http://127.0.0.1:18835/ink`（剥尾斜杠再转）
    - 为什么两个都要：浏览器会**永久缓存**旧的 301(`/ink`→`/ink/`)，只修 `/ink` 的话老用户照样掉进循环
    - 备份：`/etc/nginx/backups/openclaw.bak.20261022-093316-inkredirect`
14. **验证不能只探子路径** — 我 09:03 探的是 `/ink/login`（200 通过），漏了 `/ink/` 这个**根路径自己会跳**。**每个部署必须探根路径 + 至少一条子路径，且用 `curl -L --max-redirs` 看真实跳转链**，不能只看单跳 http_code。**
15. **basePath 前缀：fetch 不会自动加，`Link`/`router.push` 会自动加**（2026-10-22 10:00 修）— 批量改 `fetch("/api/...")` → `fetch(apiUrl(...))` 时漏了 4 处，浏览器把请求打到站点根 `/api/auth/register`（无 `/ink`）→ nginx 返回 404 HTML → `res.json()` 抛错 → catch → 页面显示「网络错误」。
    - **判据**：`Link` / `router.push` / `router.replace` 走 Next 的 `dispatchNavigateAction` → `addBasePath()`，**自动加** `/ink`；`fetch` / `window.location.href` / `window.open` **不会**，必须自己拼。
    - 推论：**`next` 查询参数里绝不能带 `/ink`** —— 它最终会走 `router.push(next)` 再被加一次 → `/ink/ink/zh/flash`。这个坑我改的时候自己踩了一次。
    - 排查手法：`grep -rn 'fetch(' --include='*.tsx'` 逐个核对首参是否包了 `apiUrl()`；再扫 `window.location.href =` / `window.open(` 是否漏了 `siteUrl()`。

---

## 五、已完成的软件改造（2026-10-21 22:00-23:40）

### 后端 4 个文件（已改 + 已备份 + 已验证）

| 文件 | 改了什么 | 为什么 |
|---|---|---|
| api/shared.py | 新增 FIRMWARE_LOCAL_DIR / PUBLIC_API_BASE；新增 load_local_firmware_releases()（本地目录优先，GitHub 降级为 fallback） | 自部署不依赖外网 |
| api/routes/firmware.py | 新增 GET /api/firmware/local/{tag}/{asset_name} 流式下载，含路径穿越防护（resolve + relative_to 校验） | 本地 bin 直出，不绕 GitHub |
| core/config_store.py | 新增 device_alerts 表 + save_device_alert() / consume_device_alert() | 告警落库，服务重启不丢 |
| api/routes/device.py | 告警内存 + DB 双写；两个消费端（check_alert / alert-bmp）都加 DB 兜底；TTL 60s → 7200s（env INKSIGHT_ALERT_TTL_SECONDS） | 设备深睡 60 分钟，60 秒 TTL 必丢告警 |

备份：/tmp/pre-edit-backup-2026-10-21-214054-{shared,firmware,device,config_store}.py + backend/core/content.py.bak.20261021-1655

### 部署

- **systemd user scope**：~/.config/systemd/user/openclaw-inksight.service → 127.0.0.1:18834，Type=simple，无 gunicorn/--reload
- **systemd user scope**：~/.config/systemd/user/openclaw-inksight-web.service → 127.0.0.1:18835（npm run start）
- **端口登记**：tools/port-registry/ports.json + services.json → 18834 inksight / 18835 inksight-web（pr audit 0 错 1 警，警是 sanxia 老问题）
- **后端 .env**：skills/inksight/backend/.env（600），含 ADMIN_TOKEN / ENCRYPTION_KEY / INKSIGHT_ALLOWED_HOSTS / INKSIGHT_PUBLIC_API_BASE=/ink/api / INKSIGHT_FIRMWARE_DIR
- **nginx**：/etc/nginx/sites-enabled/openclaw 加 3 个 location（`= /ink` 301、`/ink/api/` → 18834、`/ink/` → 18835）
- **对外入口**：`http://ruohan.site/ink/`（frp `[Web]` type=http local_port=80 custom_domains=ruohan.site，整站透传）
- **nginx 备份**：/etc/nginx/backups/openclaw.bak.20261022-091335

---

## 六、软件部分接下来怎么做

### 控制网页：已用上游 WebApp（2026-10-22 拍板）

不自己写了。上游 WebApp 整份部署在 18835，basePath=/ink，账号 / 设备配置 / 个人信息页 / 刷机页全部原生可用。成本最小。

将来若要加「我们自己的功能」，是**在 WebApp 里加页**，不是重写。

### 内容扩展

新增模式 = 在 backend/core/modes/builtin/ 加一个 JSON 文件（自己的数据源做成 external_data provider）。**不改 Python**。

### 需要自己解决的技术点

1. **设备如何指向我们的后端**（第零章的硬未知）
2. **告警的"到达"语义** — 墨水屏深睡 + 无推送通道，"主动告警"实际只能靠设备频繁醒来拉取。是否为此把轮询压到 5 分钟（费电）待定
3. **HTTPS** — 控制链路走 http 没问题；但如果将来要在外网做敏感操作需要证书

---

## 七、本机现状

```
skills/inksight/
├── backend/          # 上游 backend/ 完整副本（207M，含 fonts/）
│   ├── api/shared.py           ← 已改（本地固件源 + PUBLIC_API_BASE）
│   ├── api/routes/firmware.py  ← 已改（本地固件下载接口）
│   ├── api/routes/device.py    ← 已改（告警落库 + TTL 7200）
│   ├── core/config_store.py    ← 已改（device_alerts 表）
│   ├── core/content.py         ← 已改（max_tokens + null 保护）
│   ├── .env                    ← 600，含本机密钥
│   ├── inksight.db / cache.db
│   └── runtime_uploads/
├── firmware/v0.6.0/  # 25 个 bin / 29M（本地固件源）
├── venv/             # Python 3.13 venv（382M）
└── webapp/           # 上游 webapp 完整副本（455M，其中 node_modules 442M）
    └── ✅ 9 个文件已修好，production build 通过，18835 常驻
```

上游仓库原始副本在 /tmp/inksight（含 webapp / firmware / inksight-mobile，可随时重新 clone）。

### webapp 改造清单（已全部生效）

| 改了什么 | 怎么改的 |
|---|---|
| 整站挂 /ink | next.config.ts 加 `basePath: "/ink"` |
| 客户端 API 路径 | 55 处 `fetch("/api/...")` → `fetch(apiUrl("/api/..."))`，apiUrl() 见 lib/api.ts |
| 服务端 API 路由 | app/api/* 走 INKSIGHT_BACKEND_API_BASE（Next rewrite 到 18834），不用改 |
| 导航类调用 | router.push / window.location / window.open 逐个加 /ink 前缀（用 siteUrl()） |
| 固件下载 | 后端本地固件路径直出（/ink/api/firmware/local/...），不再绕 Next 下载代理 |
| 固件列表 | INKSIGHT_PUBLIC_API_BASE=/ink/api，后端下发的 download_url 自带前缀 |

**验证链**：`tsc --noEmit` 0 错误 → `next build` EXIT=0 → 18835 起服务 → nginx reload → 外网 200。

---

## 八、行为纪律（2026-10-21 23:00-23:45 的教训）

**1. 沟通 ≠ 拍板。** 冰河大人说"用 ruohan.site/ink/ 不就好了"是**纠正方向**，不是拍板"去改"。我立刻开始改 next.config、ports.json、systemd unit —— 这是"急着改"。

**2. 机械改代码必须配括号感知 + 逐文件语法验证。** 这次 9 个文件全坏，根因是脚本只做字符串替换。批量改代码前必须：
- 从原始副本恢复，不要在坏文件上二次修补
- 用 AST / 括号配对逻辑改，不用裸 replace
- 改完立刻 tsc --noEmit 逐文件验语法，再谈构建

**3. 答案在别人脑子里时，别替别人定义目标。** 我一直在"跑起来"方向使劲（建 venv、装依赖、改代码、起服务），从没问过"自己开发 InkSight 到底要造什么"。这是"调研充分 ≠ 任务完成"的升级版。

**4. 已拍板的事不再问。** 账号层"留"这个决定，冰河大人说了好几次，我还在文档里标"待拍板"并反复追问 —— 相当于把决定推回去让对方重做。

**5. 上游文档要全读，别挑。** 只读了 5 个技术文档就下判断，把"部署"和"设备指向"当成未知项绕了半天。而 docs/deploy.md + button-controls.md 里白纸黑字写着自托管流程和"配网可改服务器地址"。**读文档 = 读全部，不是读我认为相关的部分。**

---

## 九、个人端 AI 配置下线 + 后端内置 MiniMax（2026-10-22 17:00-17:40）

**拍板**：冰河大人要求「个人端 AI 算力/模型配置能力删干净」，改为**用户零配置 + 后端内置**。不是隐藏入口，是删除能力本身。

### 内置配置（core/config.py）

| 项 | 值 |
|---|---|
| DEFAULT_LLM_PROVIDER | `minimax` |
| DEFAULT_LLM_MODEL | `MiniMax-M3` |
| DEFAULT_IMAGE_PROVIDER | `minimax` |
| DEFAULT_IMAGE_MODEL | `image-01` |
| 文字 base_url | `https://api.minimaxi.com/v1` |
| 图像接口 | `https://api.minimaxi.com/v1/image_generation`（payload 带 `aspect_ratio: "3:2"`；MiniMax 不支持独立 negative_prompt，黑白/彩色禁止项拼进 prompt 正文） |
| 凭证 | 后端 `.env` 的 `MINIMAX_API_KEY`（从 skills/gallery/ai_tagger.env 取值写入，600 权限） |

### 删干净的范围

- 后端：`PUT/DELETE /api/user/profile/llm`、`config_store` 的 `get/save/delete_user_llm_config` 与 `user_llm_config` 表、profile 响应里的 `llm_config`、admin_analytics 的表统计
- 消费接线：`shared.build_image` 的 `user_api_key` 参数与 BYOK 额度旁路、`pipeline` 的 `user_api_key` 分支、`voice` 的用户级配置加载、`modes` 的用户级 provider/model/key 优先、`user_provided_api_key` 恒假分支
- 存储：旧 `inksight.db` 的 `user_llm_config` 表已 DROP（备份 `/tmp/inksight.db.bak-2026-10-22-171930`）
- 前端：profile 页整张「AI 算力与模型配置」卡片（BYOK Tab / 保存 / 删除 / 切换模式确认）、preview 页的 `ink_user_llm_api_key` 与 `x-inksight-llm-api-key` 头、config 页的 provider/model 类型与 mode override 透传、`usage_source` 里已不可达的 `*_api_key` 分支
- 测试：删除 `tests/test_user_llm_config_delete.py`；改写 test_integration / test_billing_quota / test_custom_mode_api_key* / test_unit_config_store

**保留**：注册、登录、设备绑定（账号层，与 AI 配置无关）。
> 注：邀请码兑换与免费额度已于同日 17:40 后整体下线，见第十章。

### 两个真实 bug（切到推理模型后才暴露）

1. **MiniMax-M3 把 `<think>...</think>` 内联在 `message.content`**（没有独立 `reasoning_content` 字段）→ 用户可见文案被思考过程污染，JSON 模式直接解析失败。
   修法：core/content.py 加 `_strip_reasoning()`，接到全部 4 处取 content 的点（content.py×3 + mode_generator.py×1）。
2. **`generate_json_mode_content` 的 `api_key` 默认是 `""`** → `_get_client` 判定为「用户提供了无效 Key」，直接抛 `LLMKeyMissingError`，**不回退环境变量**。BYOK 下线后默认值必须改成 `None`。

### 验证结果（全部实测，非推断）

- 文字：`_call_llm('minimax','MiniMax-M3')` → 正常返回
- 图像：`generate_artwall_content()` → `image_url` 非空（hailuo-image-algeng-data.oss-cn-wulanchabu.aliyuncs...）
- JSON 模式（ZEN 真实 prompt_template）→ `{'word':'澄','source':'秋日晴澄，心自安然'}`，`_llm_ok=True`，无 think 残留
- `python3 -m compileall` 通过；`npm run build` EXIT=0；`systemctl --user restart` 后两服务 active，启动日志 0 错误

### 备份

- 后端：`/tmp/pre-edit-backup-2026-10-22-165631-content.py`、`/tmp/pre-edit-backup-2026-10-22-165639-{admin_analytics,config,config_store,mobile,modes,shared,user,voice}.py`
- 前端：`/tmp/profile-before.tsx`、`/tmp/preview-before.tsx`、`/tmp/config-before.tsx`
- env：`/tmp/backend.env.bak.2026-10-22-1730`；db：`/tmp/inksight.db.bak-2026-10-22-171930`

---

## 十、免费额度 / 邀请码体系下线（2026-10-22 17:40-18:00）

**背景**：冰河大人明确指示「没必要，拿掉。都说了是自用了」——项目按自用场景处理，
不再以公网开放的风险作为保留理由。

**下线前的事实**（决定依据）：
- 注册送 50 次（`auth.py` `initial_quota = 50`），每成功渲染一个 LLM 类模式扣 1
- 邀请码兑换 +50 次，但**邀请码没有任何生成路径**——表里 0 条，代码里只有建表/迁移/校验/标记已用
- 开关 `INKSIGHT_BILLING_ENABLED` 未设 → 默认启用
- 实际用量：`total_calls_made=0`，设备表空
→ 结论：计费层是空的，只剩限流作用；额度用完就是死路

**删掉的东西**：
- 后端：`api_quotas` / `invitation_codes` 建表与迁移、`init_user_api_quota`、
  `get_user_api_quota`、`consume_user_free_quota`、`get_quota_owner_for_mac`、
  `shared.py` 的额度检查/扣费/owner 解析、`modes.py` 的 `_billing_enabled()`、
  `render.py` 的 402 分支、`user.py` 的 `/user/redeem`、前端 `user_llm_config` 表
- `users.invite_code` 列（含 ALTER DROP COLUMN 迁移）
- 前端：profile 页额度卡 + 邀请码输入 + 兑换处理器 + `free_quota_remaining` 字段；
  config 页邀请码弹窗（105 行）+ 兑换处理器（35 行）+ `formatPreviewUsageText`（13 行）
  + `formatPreviewConfirmText`（12 行）+ 402/402-SSE 分支 + `usage_source` 类型；
  preview 页 402 邀请码弹窗流程
- 测试：`test_billing_quota.py` 从 705 行砍到 246 行（删掉邀请码兑换 + 4 个额度用例组，
  保留注册/密码重置）；`test_integration.py` 去掉邀请码种子

**build_image 返回值**：8 项 → 7 项（去掉 `quota_exhausted`、`usage_source`），
改用 `llm_required` 表达是否需要 LLM；`render.py` 三处解包同步调整

**数据库**：`inksight.db` 已 DROP `invitation_codes` + `api_quotas` 两张表，
`users.invite_code` 列已删；备份 `inksight.db.bak.20261022-175455`

**验证**：
- `python3 -m compileall -q api core tests` 通过；12 个关键文件 `ast.parse` 通过；
  9 个后端模块真实 `import` 通过
- `npx tsc --noEmit` 0 错误；`npm run build` EXIT=0
- 两服务 `systemctl --user restart` 后 active，18834/18835 均在 LISTEN
- think 清洗复验：ARTWALL 标题「霜染山河」无 `<think>` 残留；
  真实 llm_json 指令 → `{'question': 'What idea has been quietly occupying your mind lately?', 'category': 'Self-reflection'}` 解析成功
- ⚠️ pytest 未安装（venv 和系统 python 都没有），测试文件只做了语法/import 校验，**未实际跑通**

---

## 十一、待办

- [ ] 冰河大人：**等新墨水屏到货**（2026-10-30 原屏损坏）；到货装回后配网，**服务器地址仍填 `http://ruohan.site/ink`**，验证设备能拉到 BMP
- [ ] 麻辣小龙虾：建第一个账号并登录 ruohan.site/ink，验证账号链路（注册 → 配对码 → 绑定设备）
- [x] ~~麻辣小龙虾：接 MiniMax 持久配置~~ → **2026-10-22 改为「用户零配置 + 后端内置 MiniMax」，方案已变，见第九章**
- [ ] 麻辣小龙虾：写第一个自定义模式 JSON（验证"加内容 = 加 JSON，不改代码"）
- [ ] 麻辣小龙虾：决定告警的"到达"语义（是否把轮询压到 5 分钟）
- [ ] 待定：具体做哪些内容模式（后续沟通决定）

## 十二、四色插画归色改造（2026-10-28）

### 硬件事实（决定了方案边界）

- 面板：**大连佳显 GDEM042F86**（4.2 寸 400×300，黑白红黄四色，SSD2683ZA 驱动）
- 规格硬事实：**MAX GRAYSCALE 2** —— 硬件只有黑白两级灰度，黄红是独立色单元
- **背光：无**，反射式显示，色彩随环境光变；厂商公开规格里**没有色度坐标**
- 结论：**「拍色卡标定」这条路走不通**（测到的是「面板色 × 环境光 × 手机白平衡」三重偏色）
- 蓝/绿/紫永远出不来 —— 面板没有这些颜料，抖动也变不出来
- 设备实测：nginx 日志里 65 次 render 请求全是 `bpp=2&colors=4`，固件零改动

### 问题定位

四条图片路径，两种落屏法：

| 路径 | 生图 | 原落屏方式 | 中间调 |
|---|---|---|---|
| 相册 | 真实照片 | Atkinson 抖动 | 保留 |
| 躺平巴拉 | image-01 | RGB 最近邻硬分类 | 全丢 |
| 天工阁 | image-01 | HSV 语义分类硬分类 | 全丢 |
| artwall | image-01 | Atkinson 抖动 | 保留（但该模式不在轮转里）|

「生硬」的根因：**两套硬分类都把中间调一刀切掉**。天工阁法把中间灰判成白（`V<110→黑`，`V≥110→白`），躺平巴拉法把中间灰判成最近的黄。实测同源图：「墨韵」现状黄占 0.1%（几乎全白），换成灰落黄后黄占 20.1%。

### 改法：先分区，再在区内有序抖动

新建 `backend/core/eink_snap.py`（274 行，天工阁与躺平巴拉共用底层）：

- 墨线在**原分辨率**上判定，缩放时「有墨即墨」（线宁可粗不能消失）
- 每个彩色区按**自己区内的**亮度范围做 3 级有序抖动：黄区=红→黄→白，红区=黑→红→黄（色阶按感知亮度排序）
- 中性区按亮度做黑白两级有序抖动：纸面留白，中间调出灰阶网点
- 区与区之间**不混色**，所以不出现跨色相脏噪点
- 输出永远是精确四色（`EINK_4COLOR_PALETTE` 12 字节），与固件色码一一对应

关键性质：**对已经是精确四色的平涂输入 100% 幂等**（像素逐个相同）—— 所以存量的天工阁/躺平巴拉 PNG 不需要重生成。

### 提示词同步放开

两处 `PALETTE_SPEC` 从「平涂色块，无渐变，无阴影，无写实光影」改成「允许同一色系内的深浅变化，用明暗表现体积；但不要出现第五种色相」。层次不靠第五种颜色，靠同一色相的深浅。

### 改动文件

- 新增 `backend/core/eink_snap.py`
- `backend/core/tiangong.py`：删 `_classify` / `_shrink_any`，`snap_to_palette` 改调 `eink_snap.snap_illustration`
- `backend/core/tangping.py`：删 `_boost_ink`，`_snap_to_palette` 改调同一模块
- 死代码清理：`PALETTE_RGB`（两文件）+ `WHITE_INDEX`（tangping）已无引用，删除
- 备份：`/tmp/pre-edit-backup-2026-10-28-153001-*.py`
- **已提交**：`e8ab24c feat(inksight): 四色插画改用区域归色，保留中间调层次`（只含本次 3 个文件；工作树里 `json_content.py` / `json_renderer.py` / `mode_catalog.py` / `eworld.py` 等是本轮之前就有的无关改动，未混入）

### 验证

- 幂等性：4 张存量精确四色 PNG（天工阁×2 + 躺平巴拉×2）→ 一致率 100.00%
- 构造「同色系深浅」图 → 金黄块深→浅被映射成红→黄→白的有序网点，机制正确
- 端到端 `build_image`（colors=4）：躺平巴拉 白57.9%/黄22.0%/黑10.5%/红9.6%（颜色数=4）；天工阁 白92.5%/黑4.9%/黄1.7%/红0.9%（颜色数=4）
- 服务重启后 `/api/health` = ok，376 passed
- **二次抖动安全**：存好的四色图进 JSON 渲染（`convert_image_block` → Atkinson）后像素 100% 不变（4 张实测），不会被再抖一次打烂网点
- **HTTP 全链路**：`/api/preview?persona=X&colors=4` → 200、400×300、8-bit 索引色 PNG，四色构成与直接调 `build_image` 完全一致
- **真机 2bpp 下发**：`/api/render?bpp=2&colors=4` 带设备 token → 200、30000 字节 = 400×300×2bit/8；解码 2bpp 验证色码映射 0=黑 1=白 2=黄 3=红 全对（构造四色块各 64 像素，躺平巴拉真图 黄 28.2%/黑 11.5%/红 12.2%/白 48.2% 全部落对索引）
- **preview 鉴权**：带 `mac=` 需 `x_device_token` 头或登录 cookie（`ink_session`），裸 curl 401 属正常；不带 `mac=` 的 Web 预览免鉴权
- **`mode_override` ≠ persona**：preview 端点 `mode_override` 是 JSON 覆盖串，persona 要用 `persona=`，用错会渲染出别的模式（97% 白无黄）

### 遗留 / 下一步

- 天工阁今天的图（`obj_20261028_006.png`）本身 92.4% 白 —— 那张生图就是空白的，改造救不了空白图，要等下一张重新生图才看得出效果
- 躺平巴拉当前图（`dream_20261028.png`）是改造前的平涂四色，跑新 snap 像素完全相同 —— 也要等下一张新图才看得出层次
- **相册路径尚未优化**（仍是 Atkinson 抖动，未加去饱和 / 中性闸 / despeckle）
- `artwall` 的 fallback 池 5 张图是全彩（`yunjuanyunshu.png` 是蓝色），抖动后蓝色会死；该模式不在设备轮转里
- 图标（icon）走 1-bit 通道，四色下不参与混色
- Web 端推送预览 `api/shared.py:699` 直接 `convert("1")`，颜色全丢

### 中间态：两套算法解耦（2026-10-28 16:46-17:01，**已被下一节推翻，仅留档**）

冰河大人确认：**躺平巴拉新版视觉效果远高于旧版，不要再动**（除非新方案能更好）。但天工阁金边碎成虚线。两套共用 `snap_illustration`，所以必须解耦。

**决策依据（同源 A/B，同一张原图两套算法）**：

| 同一张原图 | 黄占比 | 黄最大连通块 |
|---|---|---|
| 旧硬分类（改造前） | 14.0% | 89.9% |
| snap_illustration（上版） | 8.1% | **11.0%** |
| snap_line_art（本版新增） | 14.0% | **89.6%** |

**根因**：用 Bayer 网点去抖**细结构**。大色块该抖（读作层次），细金边一抖就断。天工阁图幅只有 160×200（原图 832×1248，5.2 倍下采样），黄色 92.1% 是边缘像素，只有 7.9% 是色块内部。

**两套算法（互不影响）**：

- `snap_illustration` —— 躺平巴拉用，**一行未改**
- `snap_line_art` —— 天工阁用，新增。三处不同：
  1. 彩区色阶**不接白色**（金永远保持金，更暗才降红，红更暗降黑）
  2. 阈值用**绝对亮度**，不做百分位拉伸（拉伸会把均匀金色推到色阶中段再抖成网点）
  3. 过渡带先过**区内 3×3 中值滤波**去噪再抖

**扫参结果**（同一张原图测黄像素最大连通块）：p20/p35 → 35.0%，p12/p20 → 59.1%，p08/p12 → 79.5%，**p05/p08 → 85.9%**（含补墨后 89.6%）。取 p05/p08。

**验证**：

- 躺平巴拉生产图重新归色后与现文件**逐像素 100.00% 一致**（证明解耦干净、未受影响）
- 天工阁新图 160×200、恰好 4 色、幂等 100.00%，已写回 `static/tiangong/obj_20261028_006.png`
- 设备下发 `/api/render?bpp=2&colors=4` → 30000 字节，黄色 0% → **25.1%**、红 12.1%（旧空白图是 97.4% 白）
- 376 passed（排除 `test_poetry_day.py` 的 4 个既有失败 —— `fact_duration` 字段，与本次无关，stash 验证过）

**提交**：`0ed10ac fix(inksight): 天工阁改用线描专用归色，解耦躺平巴拉`

### 终态：弃预四色化，直接交给渲染器抖动（2026-10-28 17:00-19:20）

**提交**：`b82c343 feat(inksight): 天工阁弃预四色化，交给渲染器直接抖动`（只含 `eink_snap.py` + `tiangong.py`）

**根因改了 —— 不是「四色化」的问题，是「下采样」的问题。**

前一版（`snap_line_art` 预四色化）是在错的方向上打补丁：它假设「先把图变成精确四色再上屏」是对的。实际上：

- 原图 512×768 要压到 156×234（**3.3 倍**）
- 墨线只有 **1 像素宽**、亮度中位 168
- 直接 LANCZOS 缩放是**邻域平均**，压完灰度 <110 的像素**只剩 3 个**
- 也就是说：**线在下采样那一步就没了**
- 屏上调色板**没有灰**（白 255 / 黄 175 / 红 43 / 黑 0），被稀释成中灰的线亮度正好落在黄上
- Atkinson 误差扩散一累积 → **线被抖成黄色虚点**

**所以：金边碎不是归色算法的锅，是缩放把线糊没了，然后抖动把糊掉的线染黄。**

**改法**：`eink_snap.prepare_direct_art()` —— 不预四色化，只做「准备工作」，颜色归类交给渲染器标准的 Atkinson 四色抖动：

1. 裁白边（撑成目标比例）
2. V 通道明度归一 —— **只动无彩色区**，彩色区不动（动彩色区金黄会被洗成麻点）
3. **纸面置为精确白**（见下）
4. min-pool 下采样保线（「有墨即墨」，线宁可粗不能消失）

**纸面必须置精确白（不能只提亮）**：

模型有时把纸画成米白（实测 `(252,240,168)` S=84 V=252）。屏上四色没有米白这种中间色，它在四色里**没有归属**，Atkinson 处理不了 —— 误差会累积成**一片黄网纹**（实测米黄袍身 → 满屏黄色网纹）。
写成精确白 `(255,255,255)` 后误差为 0，纸面就是干净的白纸。
只提 V 不提 S 没用：米黄提亮后饱和度还在，仍然不是调色板色。

阈值（实测 512×768 生图原图）：`_PAPER_V = 200`、`_PAPER_S = 120`
- 纸面（米白） S=29..84 V=245..254
- 金黄区 S>=91、朱红区 S>=91 → 饱和度高于阈值，不会被误伤
- 墨线 S<40 V=38..170

**图幅**：160×200 → **156×234**（屏 400×300，body 可用高 300-36-30=234，2:3 精确；左栏 176 装得下，右栏 212 未动）

**生图参数**：改指定 `width/height = 512×768`，**不发 `aspect_ratio`** —— 两者同时给时 MiniMax 优先 `aspect_ratio`，会让 `width/height` 失效。提示词加「勾线一律纯黑」。

**躺平巴拉**：仍走 `snap_illustration`，**函数体一行未改**；生产图重跑 `snap_illustration` 逐像素一致率 **100.00%**。

**验证**：

- 407 passed（`test_poetry_day.py` 那 4 个 `fact_duration` 失败已由工作树里**未提交**的 poetry_day 相关改动修复，与本次两文件无关，未混入本次提交）
- 生产路径重生成图 → 156×234，黑 3.8% / 白 90.7% / 金黄 4.2% / 朱红 1.3%
- 整页渲染 400×300 → 四色（黑 6.1% / 白 91.7% / 黄 1.3% / 红 0.9%），2bpp 30000 字节
- HTTP `/api/preview?persona=CUSTOM_TIANGONG&colors=4` → 200、400×300、四色（黑 5.9% / 红 1.0% / 黄 1.4% / 白 91.7%）
- HTTP `/api/render?persona=CUSTOM_TIANGONG&colors=4` → 200、content-length **30000**、x-render-time 1986ms
- 服务 `openclaw-inksight.service` active，`/api/health` = `{"status":"ok"}`
- **真机肉眼验收：冰河大人 2026-10-28 20:34 确认「已经达到完美态了」** —— 本方案定稿

<!-- project: path:/home/open/.openclaw/workspace -->

**教训（重要）**：数据验证只能当**门槛**（揪出失败），不能当**结论**（宣布成功）。本次 "376 passed / 幂等 100% / 四色严格" 全在验管道，没碰到真问题「金边断了」；是冰河大人一句「你的算法告诉你旧版比新版好，对不对」把方向掰回来的。**最终判断权在人**——反射式四色屏的观感必须拿实物看。

---

## 十三、诗笺日课双城自选（2026-10-28 18:45-19:20）

**要解决的事**：`skills/bt-presence/bt_presence.py` 里 `CITIES = [郑州, 淄博]` 写死，诗笺日课的双城天气永远是这两座。冰河大人要求在 InkSight 里自选。

**方案（复用现有结构，不新建表）**：

| 层 | 做法 |
|---|---|
| 模式声明 | `modes-ours/poetry_day.json` 加 `settings_schema`，两个 `type:"location"` 项：`poetry_home` / `poetry_away`（源文件与部署副本 `core/modes/builtin/custom_poetry_day.json` 必须逐字一致，测试盯着） |
| 存哪 | 模式覆盖 `mode_overrides["CUSTOM_POETRY_DAY"]["mode_settings"]["poetry_home"/"poetry_away"]`，各存一份 `{city,latitude,longitude,timezone,admin1,country}`。不新建数据库表、不新建配置项 |
| 前端 | `webapp/app/config/page.tsx`：`modeLocationItems()` 取模式声明的地点槽位；**一个** location 槽位 → 沿用原来的天气那套单城弹窗（`CUSTOM_DAYOVERVIEW` 不受影响）；**多个** → 新 `paramModal.type="cities"` 弹窗，每个槽位一个 `LocationPicker`，存 `mode_settings` 下 |
| 前端（预览页） | `webapp/app/preview/page.tsx`：**另有一套**城市弹窗实现（`modal.type="weather"`），必须同步改，否则在 /ink/preview 里仍然只弹一个城市 |
| 后端 | `core/poetry_day.py` 新增 `build_poetry_day_content()`（async）：读有效配置里的两个槽位 → 用 InkSight 自己的 `context.get_weather`（Open-Meteo → 和风兜底）抓当前天气 → 缓存 30 分钟 → 格式化成 `杭州 17° / 南京 16°` |

**天气文本规则**：

- 自选了 → 用自选城市抓的天气，`到家`加前缀「明日」、`出门`加前缀「出门时」、无蓝牙事件时无前缀也照常上屏
- 没自选 → 完全沿用蓝牙事件里的旧文本（郑州/淄博），一条代码没动
- 自选城市抓不到 → 退回旧文本，不空屏

**坑**：

- **同一个「模式城市弹窗」在 Web 前端有两套实现**：`app/config/page.tsx`（配置页）和 `app/preview/page.tsx`（预览页）各写了一遍。改一处不改另一处 → 预览页仍然只弹一个城市。**教训：改模式弹窗先 grep `LocationPicker` 找出所有调用点**（`refresh-strategy-editor.tsx` 那个是全局设备城市，不算）
- `mode_overrides` 是**按模式 ID** 存的（不是按 persona），`get_effective_mode_config(cfg, mode_id)` 才会把 `mode_settings` 提升到有效配置顶层
- `schemas.py` 的 `validate_mode_overrides` 会保留任意嵌套 dict，所以 `mode_settings` 里的双城能原样存进 DB（已实测 `ConfigRequest` 往返不丢）
- `json_content.py` 的 override 合并会把 `mode_settings` 一起塞进 content —— 渲染器只读版式引用到的字段，多余键无害（实测渲染正常）
- 「模式设置」弹窗（`settingsMode`）目前**没有入口**（全仓只有 `setSettingsMode(null)`），别往那里加东西

**验证**：407 passed（新增 8 条：schema 声明两个地点 / 有效配置两城不互相覆盖 / 自选城市替换旧天气 / 没自选保持旧文本 / 抓取失败退回旧文本 / 无蓝牙事件也上屏 / 抓天气走 InkSight 自己的栈）；真实链路实测 `出门时 杭州 17° / 南京 16°`（杭州=poetry_home，南京=poetry_away）；后端与 Web 服务已重启（active）。

**注意**：自选城市走的是「当前实况」，前缀「明日」沿用旧文案（旧的 bt-presence 用 wttr.in format=3 拿的也是实况，语义一致）。

---

## 十四、日课三则：毒舌 + 猜谜 + 微故事合并（2026-10-28 21:00-21:15）

**要解决的事**：ROAST（毒舌）/ RIDDLE（猜谜）/ STORY（微故事）三个模式各自独占一屏、各调一次模型，一轮轮转要等三个回合才能凑齐三样。

**做法**（只新增，不动原三个模式）：

| 项 | 决定 |
|---|---|
| 模式 ID / 名称 | `CUSTOM_DAILY_TRIO` / 「日课三则」 |
| 源文件 | `modes-ours/daily_trio.json` + 部署副本 `backend/core/modes/builtin/custom_daily_trio.json`（两处必须逐字一致，测试盯着） |
| 内容 | **单个 `llm_json` 步骤**（不是 composite）——一次模型调用出全部三块 |
| 字段 | 扁平：`roast` / `riddle_category` / `riddle_question` / `riddle_hint` / `riddle_answer` / `story_title` / `story_opening` / `story_twist` / `story_ending` / `story_genre`（渲染器只认扁平字段，嵌套对象取不到） |
| 目录 | `core/mode_catalog.py` 加 `CatalogItem`（zh/en 名称 + tip） |
| 缓存 | `cacheable: true`（与三个原模式一致） |

**排版（冰河大人授权「合理排版就OK」，由我定）**：

- **竖排三块**，不用两列 —— 这三样是有先后顺序的内容（钩子→谜面→故事），横排会把故事挤成窄条
- 正文：`毒舌(15px粗/居中/≤2行)` → 短分隔线 → `类型(9px) + 谜面(13px粗/≤2行) + 提示·谜底(10px)` → 短分隔线 → `类型·标题(12px粗) + 开头/转折/结尾(11px)`
- 字数上限按行宽反推：毒舌 ≤28、谜面 ≤34、提示 ≤10、谜底 ≤12、故事 6/22/28/18 字
- 296×128 小屏只留 毒舌 + 谜面 + 谜底，**故事整块省掉**；648×480 放大到 22/18/15px
- 页脚：label「日课三则」，attribution = 故事类型

**关键坑**：

- **别自己写 `max_tokens`**：默认模型是 `minimax/MiniMax-M3`（推理模型），推理 token 吃掉预算会返回空内容 → 解析失败 → 整屏掉兜底。我先写了 1500，实测直接空；**不写就跟随 provider 默认（minimax 4096）**，跟 ROAST/RIDDLE/STORY 一样
- **模式注册表是启动时加载进内存的**：改了 `backend/core/modes/builtin/*.json` 必须重启 `openclaw-inksight.service`，否则跑的还是旧定义（我踩过一次：预览还掉兜底）
- 模型偶尔不按字数上限写（谜底 12 字写成 20 字）→ 版式给 `max_lines` 兜住

**验证**：15 条新测试（源/副本一致、目录注册、output_schema 与兜底覆盖全部字段、**一次模型调用出三块**、模型只回一部分时用 schema 默认值补齐、版式引用了全部字段、三种尺寸不压页脚线）；全量 422 passed；真机预览渲染确认（毒舌/冷知识/悬疑三块各一行，故事三段、页脚「日课三则 悬疑」）；后端服务已重启。

**保留**：ROAST / RIDDLE / STORY 三个原模式原封不动，设备轮转列表要不要换、怎么换由冰河大人自己动。




---

<!-- project: path:/home/open/.openclaw/workspace -->

### 终态补充：躺平巴拉也统一（2026-10-28 20:35-21:10）

冰河大人问「卡皮巴拉你试试看也走这个逻辑呢」。**试了，一致更好，已落地。**

**提交**：`1ed7262 feat(inksight): 躺平巴拉也走渲染器直接抖动，与天工阁统一`

**躺平巴拉之前的问题**（和天工阁同一个病，表现不同）：
走 `snap_illustration` 预四色化（分区 + 区内有序抖动），把大色块打成有序网点，
原图里毛发的细节反而被抹平了 —— 毛发变成一片噪点，眼睛和爪子糊掉。

**三张场景同源 A/B**（星河谷 / 桂花巷 / 南瓜坡）：

| 场景 | 像素一致率 | 黑占比 A → B |
|---|---|---|
| 星河谷 | 56.1% | 2.0% → 13.6% |
| 桂花巷 | 71.4% | 6.2% → 12.4% |
| 南瓜坡 | 66.7% | 2.0% → 6.9% |

一致率只有 56%~72%，说明两条路输出差别很大，不是微调。
肉眼对比：毛发更实、轮廓更清、眼睛和爪子更锐利。

**改法**：`tangping.py` 的 `_snap_to_palette` 改名 `_prepare_card`，改调 `eink_snap.prepare_direct_art`，
与天工阁同一条路。删除 `BLACK_MIN`（补墨闸门随预四色化一起失效了）、`_crop_to_ratio`、
四色占比改用 `native_dither.atkinson_palette` 实测脉冲（和天工阁 `_counts` 同口径）。

**`snap_illustration` 保留**（当前无调用者）备查，方便回退。

**尺寸校验（关键）**：模式 JSON 里三个 image 块是 400×234 / 322×99 / 400×232。
400×300 屏命中 base layout 的 400×234，与 `CARD_DISPLAY` 一致 → **1:1 落屏**，渲染器不二次缩放。
三种屏尺寸（400×300 / 296×128 / 648×480）实测都是四色。

**验证**：

- 407 passed
- 生产图重生成 400×234（黑11.5% 白58.9% 金黄22.6% 朱红7.0%）
- 整页 400×300 四色（黑10.6% 白66.3% 黄17.6% 红5.5%）；296×128 也四色
- HTTP `/api/preview?persona=CUSTOM_TANGPING&colors=4` → 200、400×300、四色（黑10.4% 白66.3% 黄17.7% 红5.6%）
- HTTP `/api/render?persona=CUSTOM_TANGPING&colors=4` → 200、content-length **30000**、x-render-time 1318ms
- **2bpp 解码验证**：色码 0=黑 10.4% / 1=白 66.3% / 2=黄 17.7% / 3=红 5.6%，四个码全在，与预览一致
- 服务 `openclaw-inksight.service` active，`/api/health` = `{"status":"ok"}`
- **真机肉眼验收：2026-10-28 22:25 通过 ✅**

**踩坑**：重生成生产图时第一版画面描述写「星河谷的**夜色**里」，黑占比直接飙到 **50.6%**（画面被压暗），
换成明亮场景（白天/溪边）才正常。说明场景描述会显著影响明暗分布 —— 生图描述要避免暗色调词。

<!-- project: path:/home/open/.openclaw/workspace -->

### 电子世界：州郡名垫白底（2026-10-28 21:36-21:56）

冰河大人问「电子世界这个功能，是不是也有类似的提升空间」，随后给了关键思路：
**「其实我们只需要给文字加上白色底，是不是就不用担心点阵的影响了？」**（原话「电针」是「点阵」笔误）

**先说清电子世界和天工阁/躺平巴拉不是一类东西**：

- 电子世界是纯代码矢量绘制，不走生图
- 输出已是精确四色（非四色 0.0000%），渲染器二次抖动零损伤（三种屏一致率都 100%）
- 所以它没有「预四色化丢信息」的 bug，病是**死板**：地图面积 61.4% 是平涂死色块（红 22.7% + 黄 22.2% + 白 16.5%）

**病根对比**：天工阁/躺平巴拉是「预四色化丢信息」，电子世界是「纯色平涂没肌理」。两者不同源。

**已落地（提交 f17c118）**：

- 州郡名垫白底：黑字压红底只有 **3.46:1**（AA-large），垫白底后 **21:1**
- 文字区从此只有纯白+纯黑两色，不受任何底图网点混色干扰（为后续套印留前提）

**实现要点**：

- 新增 `_text_plate()`：按字形 bbox 外扩 1px 生成白底板掩膜
- 州界改为存掩膜 `border_mask`，白底板盖住穿过的界后用 `ImageChops.multiply(border_mask, union)` 把板内那段界补回来
- 文字挪到州界之后画，与界重叠时以字为准

**踩过的坑**：

1. **白底板必然切断州界线**，跟绘制顺序无关。实测黑→白 554 个像素，
   其中属于州界的是 **0** —— 因为补界逻辑已生效。先试「先画界后画字」只把 575 降到 554，
   治标不治本。正解是州界存掩膜、在底板上局部重画。
2. **我一度误判补界没生效**（白→黑 = 0），实际是判据错了 —— 那些界在基准版里本来就是黑的。
   正确判据是拿州界掩膜当真值量：2818/2818 = 100%。
3. **验证对比度时采样取偏**（采到底板外背景），正确做法是遍历底板区域做色表统计：
   32 个标签底板内色表全部只有 {黑, 白}，非黑白残留 0。

**验证**：

- 州界完整性 2818/2818 = 100%
- 32 个标签底板只有纯白+纯黑
- 三种屏（400x300 / 296x128 / 648x480）严格四色，非四色 0.0000%
- HTTP preview 200、四色；render 200、30000 字节；2bpp 解码 0=黑/1=白/2=黄/3=红 全对
- 422 passed

**git 情况**：电子世界此前完全未跟踪。本次纳入版本管理：
`core/eworld.py`、`core/data/eworld.json`、`tests/test_eworld.py`、`modes-ours/eworld.json`（源文件），
并在 `json_content.py` 接 provider hook、`mode_catalog.py` 注册 CUSTOM_EWORLD。
`builtin/custom_eworld.json` 是部署副本，按 .gitignore 惯例（`custom_*.json`）不提交。

**部分暂存技巧**：工作树的 `json_content.py` / `mode_catalog.py` 混了 eworld + dayoverview + poetry_day + daily_trio
四个功能的改动。做法是取 `git show HEAD:<file>` 的原版，只打 eworld 补丁，`git hash-object -w` + `git update-index --cacheinfo`
写进暂存区，**不动工作树**。提交后两个文件仍是 MM 状态，其它功能改动完整保留。

**待办（等冰河大人真机验证）**：

- 套印网点混色（红地叠 45° 黄点 / 黄地叠 15° 红点）原型已做但**未落地**。白底板前置已通过真机肉眼验收（2026-10-28 22:25），但**套印本身要不要做由冰河大人定**，未获明确指令前不推进
- 关键坑：网点步长**不能跟 u 缩放**，否则 296×128 覆盖率 64.5%（网点连片糊掉）、648×480 只有 9.8%（几乎看不见）。
  必须用固定设计稿像素步长，三屏才能一致落在 25%
- 底板范围 +1px 还是 +2px、线在底板上要不要重画（我倾向重画，字和线会挤）

<!-- project: path:/home/open/.openclaw/workspace -->

## 十五、丹青谱：每天画一幅不存在的宋代绘画（2026-10-28 21:53-22:15）

**冰河大人的定调**：「与其用现在已有的内容，不如我们自己创造新的内容」→ 选题方向定为
**以图片为主、且内容全部现生成**（不翻库存、不用现成素材）。

**做法**：从"翻库存"（名画局部 / 碑帖拓片 / 参观照片卡）里挑了天工阁那条路的同构版 ——
既然天工阁每天造一件不存在的宋代器物，那就每天画一幅不存在的宋代绘画。

| 项 | 决定 |
|---|---|
| 模式 ID / 名称 | `CUSTOM_DANQING` / 「丹青谱」 |
| provider | `backend/core/danqing.py`（新文件） |
| 源文件 | `modes-ours/danqing.json` + 部署副本 `backend/core/modes/builtin/custom_danqing.json`（逐字一致，测试盯着） |
| 题签 | 画名 / 作者 / 朝代 / 题材 / 题跋 / 款识 / 印文 —— LLM 当日现写，**全部虚构** |
| 画心 | MiniMax image-01，768×864，按屏上四色盘（白/黑/金黄/朱红）画册页 |
| 私印 | 作者四字印文本地画朱文方印（像素精确 (200,0,0)，抖动零误差） |
| 题材轮转 | 山水/花鸟/人物/楼阁/墨竹/蔬果/荷石/渔隐，一轮不重样 |

**关键设计**：

- **不做预四色化**：直接交给渲染器的 Atkinson 抖动（与天工阁、躺平巴拉统一，见十二节终态）
- **画心显示尺寸 208×234 与生图 768×864 同比例**：渲染器 1:1 贴图，不二次缩放丢墨线
  （这是 `prepare_direct_art` 的硬要求，尺寸必须与模式 JSON 的 image 块一致）
- **生图失败退回纯题签版式**：有图/无图两套 layout 分支，不空屏
- **防撞真实画家**：提示词里明写「作者、画名、印文必须全部虚构，不得使用真实宋代画家名或真实画作名」
  —— 实测那次给了虚构的「郗蘅，号寄云」；小屏 VL 读图把「郗蘅」误认成「郭熙」，那是识图误读不是模型输出，
  但这条只能靠提示词压、没法硬保证（若模型某天真的写了郭熙就得人工兜）
- **同一天只调一次模型**：结果存 `runtime_cache/danqing/state.json`，跨天接着编号

**版式（初版，已于 22:30 后重做，见下）**（400×300 主版）：左图 208×234，右栏题签
（编号/画名/朝代·作者/题材/题跋/款识/印）。296×128 小屏左图缩到 88×92 + 三行字；
648×480 放大到 322×372。

**踩过的坑**：

1. `json_content.py` / `mode_catalog.py` 的工作树里混着 poetry_day / dayoverview / eworld 等功能的改动，
   直接 `git add` 会把别人的东西一起提交。做法照抄电子世界那次的「只写索引」技巧：
   取 `git show HEAD:<file>` 的原版，只打丹青谱的补丁，`git hash-object -w` + `git update-index --cacheinfo`
   写进暂存区，**不动工作树**。提交后两个文件仍是 MM 状态，其它功能改动完整保留。
2. `backend/core/modes/builtin/custom_*.json` 与 `backend/static/` 都被 .gitignore 排除
   （部署副本与生图产物不纳管），源文件在 `modes-ours/` 才纳管。

**验证**：20 条新测试（源/副本一致、catalog 注册、provider 接线、题签逐行解析、私印复用与去重、
同日不重复调模型、题材轮转不重样、版式引用全部字段、有图/无图双分支、图尺寸与 ART_DISPLAY 一致）；
全量 **442 passed**；真机预览渲染确认三种尺寸均有画心与题签（VL 读图确认画的是断桥山水、
题签齐全、私印在右下）。

**提交**：`6bebb92 feat(inksight): 丹青谱——每天画一幅不存在的宋代绘画`

**保留**：设备轮转列表未改动，是否加入由冰河大人自己决定。

<!-- project: path:/home/open/.openclaw/workspace -->

### 2026-10-28 22:25-22:45 版式重做：横幅大画心，题签压到两行

冰河大人看完初版后反馈：**「图片太小，文字太多。比重不合理。」**

初版是左图右签：208×234 的小画挤在左半边，右半边七八行题签，画只占屏面约四成。

**改法**：

| 项 | 初版 | 新版 |
|---|---|---|
| 画心 | 208×234 竖幅（册页式） | 392×176 横幅（手卷式），满屏宽 |
| 生图 | 768×864 | 1152×512，提示词改成要求横向横幅构图 |
| 题签 | 7 块（编号/画名/朝代·作者/题材/题跋/款识/印） | 2 行（画名 + 「第 N 幅 · 题材 · 朝代 · 作者」） |
| 款识 | 上屏 | **从屏上移除**（仍写进 state.json） |
| 题跋 | 正文一块 | 移到页脚 attribution |

三屏画心占位（实测几何）：400×300 → 392×176（占宽 98%、占屏高 52%）；
296×128 → 184×82；648×480 → 620×278。三屏统一 2.23:1 比例，满宽不裁切不留白边。

**关键坑：渲染器给 image 块的 width/height 乘 `ctx.scale`，但 `x` 是绝对像素不缩放。**

`ctx.scale` = `max(0.92, screen_w/400)`（296→0.92、648→1.62）。第一版重排时给 648 写了
`width: 620`，实际被放大成 1004px 直接出屏 —— 光看 JSON 看不出来，必须按真实缩放算右边界。
x 也要注意是绝对像素：648 屏的 `x: 14` 就是真的 14px，不会跟着缩放。

**验证**：新增 5 条版式回归测试（横幅比例 1.6<w/h<3.0、画心占屏宽 ≥55% 且不出屏、
题签块数上限、主版式只两行、无图分支不空屏）；丹青谱 25 条全过；后端全量 **447 passed**；
HTTP 预览 200 且四色齐全（白/黑/金黄/朱红）。

**提交**：`6c96e5f fix(inksight): 丹青谱改为横幅大画心，题签压到两行`

<!-- project: path:/home/open/.openclaw/workspace -->

### 2026-10-28 22:25 真机肉眼验收：全部通过 ✅

冰河大人原话：**「肉眼审核全部通过，笑。」**

本轮验收覆盖三件事，全部通过：

1. **天工阁**（`prepare_direct_art` + 渲染器 Atkinson 抖动）—— 真机肉眼通过
2. **躺平巴拉**（同路径，真彩图直存）—— 真机肉眼通过
3. **电子世界**（州郡名垫白底，文字区只留纯白+纯黑）—— 真机肉眼通过

**教训固化（2026-10-28 已记入 USER.md）**：

- 算法指标（颜色占比、连通块、幂等率、四色越界率）**只能用于技术诊断**，
  不等于人类视网膜观感，也不能替代验收
- **最终画质判断权在人**。以后不得以数据代替用户/真机视觉结论
- 报告时要把「技术通过」和「肉眼通过」分开说，别混为一谈

**本轮技术侧结论（供追溯）**：

- 电子世界：州界完整性 2818/2818 = 100%；32 个标签底板内只有 {黑,白}，残留 0
- 三种屏（400x300 / 296x128 / 648x480）严格四色，非四色 0.0000%
- 2bpp 下发：电子世界 / 躺平巴拉 / 天工阁 render 均 200、30000 字节，
  色码 0=黑 1=白 2=黄 3=红 全对
- 422 passed

<!-- project: path:/home/open/.openclaw/workspace -->

## 十六、双城诗笺 WebApp 收尾验证（2026-10-28 22:40-22:50）

**要解决的事**：上一节做完双城自选后，WebApp 侧还差最后的界面级确认——预览页是否真的同时给出两个城市选择器、分别选城市后参数是否都正确提交。

**验证结论（全部实机通过）**：

| 项 | 结果 |
|---|---|
| 模式目录声明 | `/ink/api/modes/catalog` 返回 `CUSTOM_POETRY_DAY` 的 `settings_schema` 有两条 `type:"location"`：`poetry_home`（城市一（家））/ `poetry_away`（城市二（在外）） |
| 预览页弹窗逻辑 | `app/preview/page.tsx` 的 `modeLocationItems()` 遍历模式声明的 location 项；`length > 1` → 走 `modal.type="cities"`，每个槽位各渲染一个 `LocationPicker` |
| 提交结构 | 弹窗确认按钮组装 `{mode_settings: {poetry_home: {...}, poetry_away: {...}}}` → `mode_override` 查询参数 |
| 后端还原 | `api/shared.py` 把 `preview_mode_override` 合并进 `config["mode_overrides"]["CUSTOM_POETRY_DAY"]`；`core/poetry_day.py` 的 `_mode_settings()` 兜底从 `mode_overrides[MODE_ID]["mode_settings"]` 读回两槽 |
| provider 直调 | `_configured_cities()` 返回 `{'poetry_home':'杭州','poetry_away':'成都'}`；`build_poetry_day_content()` 产出 `fact_weather = "明日 杭州 15° / 成都 15°"` |
| 端到端 HTTP | `GET /api/preview?persona=CUSTOM_POETRY_DAY&mode_override={"mode_settings":{...杭州/成都...}}` → 200，400x300 PNG |
| 屏上文字 | tesseract OCR 该 PNG 读出「明日杭州15*/成都…」，双城天气确实上屏 |

**坑**：

- **WebApp 有 basePath**：`webapp` 的 `basePath: "/ink"`，所以 `/preview`、`/config` 直接访问是 404，**不是故障**。正确地址：`http://127.0.0.1:18835/ink/preview`、`/ink/config`、`/ink/login`（均 200）。端口 18834 是后端 API（无前缀），18835 是 Web 前端。
- **浏览器自动化访问本地 URL 被策略拦**（`browser navigation blocked by policy`，127.0.0.1 和 localhost 都拦）。要验本地页面只能走 HTTP/OCR/服务端直调，别指望 browser 工具。
- **模式目录接口路径**是 `/ink/api/modes/catalog`（前端 basePath 也要带上）。

**服务状态**：`openclaw-inksight-web.service` 已 build + restart，`active`；`openclaw-inksight.service` 亦 `active`。

<!-- project: path:/home/open/.openclaw/workspace -->

### 丹青谱：专属四色盘 + 色相硬映射（2026-10-28 22:49-23:20）

冰河大人让我「看，在判断有没有提高画质空间」。**提交 e27866e**。

**先说清时间线**：22:25 那轮「肉眼审核全部通过」不含丹青谱 ——
丹青谱 22:38 才出图，`6c96e5f`（22:42）又把它改成横幅大画心。所以它没进那轮验收。

**根因：模型画了墨绿的松树。**

色相分布（技术诊断）：

| | 绿/青/蓝/紫相占比 |
|---|---|
| 丹青谱 | **17.0%** |
| 躺平巴拉 | 2.9% |
| 天工阁 | 0.3% |

模型违反了 PALETTE_SPEC 里「不要出现第五种色相（不要蓝、绿、紫、青）」。
这不是模型偷懒 —— 宋代山水本来就用花青石绿，模型艺术上是对的。
但四色屏没有绿，那部分在 Atkinson 里没有归属，只能被抖成黑黄椒盐噪点：
黄色最大连通块 **11%**、孤立率 **61%**（躺平巴拉是 28% / 53%）。

**算法路径没问题**：丹青谱本来就走 prepare_direct_art，跟天工阁、躺平巴拉同一条路。

**方案（冰河大人选「1」= A+B 都做）**：

A. **专属四色盘**（不复用天工阁的 PALETTE_SPEC）——
   天工阁画器物可以用「同色系深浅变化」做体积；丹青谱画山水改成
   「树石一律纯墨皴染，绝不要绿蓝青紫，花青石绿一概不用」，远山金黄、点睛朱红。

B. **色相硬映射 _snap_hue_to_ink**（后端保险）——
   纸白->纯白；纯红相->朱红；橙金相->金黄（暗部归墨黑，远山留皴法）；
   其余色相->墨黑。绿树变墨树是「墨分五色」的正统画法，不是降级。

**顺序铁律**：映射必须在 prepare_direct_art **之后**。
先归色相再备尺寸，会被 _stretch_value 把已归好的色相重新拉伸而劣化 ——
实测黄色孤立率 52%（顺序1）vs 43%（顺序2）。

**严格隔离**：映射函数只写在 danqing.py，**绝不进 eink_snap 共享层** ——
躺平巴拉已冻结，共享层一改就会动它。试过：色相映射会把躺平巴拉
黑 0.1%->13.8%、红 15.6%->6.9%，不能共享。有测试兜这条线。

**验证**：

- 冷色相 17.0% -> 0.0%
- 存盘画心严格四色（非四色 0.0000%）；抖动后黄色最大连通块 11% -> 51%，孤立率 61% -> 43%
- HTTP preview 200、严格四色；render 200、30000 字节；2bpp 色码 0=黑/1=白/2=黄/3=红 全对
- 躺平巴拉与天工阁代码路径未变，输出不变
- 新增 7 个回归测试（含「映射不得进共享层」）；454 passed

<!-- project: path:/home/open/.openclaw/workspace -->

### 丹青谱终态：色相映射已回退，改用 box 下采样 + 只压暗墨色（2026-10-28 23:20-2026-10-29 00:05）

**上一节的 `e27866e`（专属四色盘 + 色相硬映射）已被回退**，提交 `a59a709`（23:11）。
本节是当前生效状态，上一节仅留档。

**背景**：冰河大人指出「躺平巴拉好了但天工阁没好，可能是生图 prompt 的问题，不是你处理的逻辑问题」。
同源 A/B 验证（同一张原图换算法）：

| 同一张原图 | 黄占比 | 黄连通块 |
|---|---|---|
| 旧算法 | 14.0% | **89.9%** |
| 共享算法 | 8.1% | **11.0%** |

原图换算法就碎 → 原图是好的，是算法没处理好它。
提示词的贡献在别处：旧提示词的色是平涂的（区内亮度塌缩 → 归一化不触发 → 不抖 → 连通）；
新提示词要求「深浅变化」，模型画了带明暗的金边，我的归一化把细金边拉到色阶中间 → Bayer 抖成黄白虚线。
**提示词制造了前提条件，算法没有正确处理这个条件。**

**躺平巴拉冻结**：冰河大人明确「新版视觉效果和精细度远高于旧版，不要再动它」。
所以 `prepare_direct_art`（天工阁 + 躺平巴拉 + 丹青谱三家共用）的新行为必须做成**可选参数**，
默认值 = 现行行为，保证躺平巴拉与天工阁逐像素不变。

**方案 D（冰河大人肉眼选定）**：box 平均下采样 + 整体压暗 0.88。
我指出了一个自己造的缺陷：整体乘 0.88 会把白纸 255 压成 224，纸面在四色里不再算白，
Atkinson 把整片天空抖成一片黄点。改成**只压暗低饱和像素（墨/灰，S<=70）**，
白纸与金黄朱红原样保留 —— 树的黑白比与原方案一致，天空干净。冰河大人选 D。

**实现（提交 `73f11b4`）**：

- `eink_snap.prepare_direct_art` 新增 `downsample` / `ink_gamma` 两个**关键字参数**，
  默认值 = 现行行为（minpool + gamma 1.0）。
- `eink_snap._darken_ink`：只压暗 S<=70 的像素，gamma>1 才生效。
- `danqing.prepare_art_file` 显式传 `downsample="box", ink_gamma=1.35`。

**验证**：

- **默认路径逐像素不变**（HEAD 版 A/B 对比，全部 True）：天工阁原图、躺平巴拉生产图、
  天工阁生产图、丹青谱生产图，三家存量图重跑 100% 一致。
- 实现保真：代码输出 == 给冰河大人看的候选 D，逐像素一致。
- 严格四色：整页 400x300 颜色数 4；2bpp 30000 字节。
- HTTP preview 200，四色（`persona=CUSTOM_DANQING`）。
- 全量 **447 passed**；服务 active，`/api/health` {"status":"ok"}。
- 10-28 那幅已用 D 重出（`art_20261028_001.png`，state 的 art_note 改为 "392x176 box+只压墨"）。
  10-29 那幅由 D 管线自动生成（服务重启后生效）。

**验收**：2026-10-29 00:10 冰河大人确认「达到预期」—— 丹青谱 D 版通过肉眼验收。
（这是本项目视觉产出的最终判断依据，数值只作技术诊断。）

<!-- project: path:/home/open/.openclaw/workspace -->


---

## 十七、四色屏画质通则（2026-10-29 09:00-09:15）

> 独立成节，因为它跨模式通用 —— 天工阁、躺平巴拉、丹青谱共用同一套物理约束。
> 代码里每个函数的 docstring 记的是「这个函数为什么这么写」；本节记的是**通则**。

### 核心认知

**四色屏的画质不是生成出来的，是「归属干净」出来的。**

屏 = 4 个精确颜色 + 最近邻归属 + Atkinson 误差扩散。
每个像素要么落在色盘上（误差 0），要么误差被推到邻域。
所以噪点的本质是**误差无处可去** —— 画面里只要有四色之外的中间调，误差就在那里堆成椒盐噪点。

### 三条具体规则

1. **中间调是敌人。**
   灰度 110~150 落在黄的区间（屏上黄是 `(232,176,0)`，明度约 175，和纸白一样亮），
   一条中灰的线会被抖成黄色虚点。
   解法**不是**整体拉对比度 —— 整体拉会把金色内部的深浅笔画一起拉开，深金被判成黑、金块变麻点。
   是**只拉无彩色区**（`_stretch_value`：S 越低越用拉伸后的明度，彩色区保持原样）。

2. **纸面要「置白」不是「提亮」。**
   模型画米白 `(252,240,168)`，它在四色里没有归属，Atkinson 误差扩散处理不了 ——
   误差会累积成一片黄网纹。写成精确白后误差 = 0，纸面就是干净的白纸。
   判据：明度 >= 200 且饱和度 <= 120（`_whiten_paper`）。
   彩色区饱和度高于 120，不会被误伤。

3. **压暗必须分层，不能整体乘。**
   整体乘 0.88 把白纸 255 压成 224，纸面在四色里就不再算白 → Atkinson 把整片天空抖成一片黄点。
   只压低饱和像素（墨/灰，S <= 70），白纸与金黄朱红原样保留（`_darken_ink`）。

### 提示词与算法是配套，不是二选一

**提示词制造前提条件，算法负责正确处理这个条件。**

实测（同一张原图换算法）：

| 同一张原图 | 黄占比 | 黄最大连通块 |
|---|---|---|
| 旧算法 | 14.0% | **89.9%** |
| 共享算法 | 8.1% | **11.0%** |

原图换算法就碎 → 原图是好的，是算法没处理好它。

提示词的贡献在别处：旧提示词画**平涂**的色 → 区内亮度范围塌缩 → 归一化不触发 → 不抖 → 连通；
新提示词要求「深浅变化」→ 区内有亮度范围 → 细金边被拉到色阶中间 → Bayer 抖成黄白虚线。

**反向的教训**：后端硬改色相（`_snap_hue_to_ink`，提交 `e27866e`）被回退（`a59a709`）。
模型画花青石绿是艺术事实（宋代山水本来就用花青石绿），屏上没绿只能靠提示词引导，
硬映射把绿树变墨树，代价大于收益 —— 提示词那层更划算。

### 图幅比调色更值钱（我的判断，未验证）

天工阁 160x200 = 32000 格，屏是 400x300 = 120000 格，画只占 1/4。
四色屏每一格都是硬资源 —— 同样构图，画幅放大一倍的收益远大于任何调色技巧。

### 暖色旁的抖动灰会看成冷色（冰河大人观察，2026-10-29 10:17；用法由冰河大人 10:37 明确）

**原话**：当人的视网膜接收到极度温暖、高饱和的暖色（尤其是黄色）时，
旁边那一片由密集黑点和白点混合出来的"灰色"，就会在视觉上自动偏向它的补色 —— 冷色（蓝/青色）。

**为什么这条对四色屏特别要紧**：屏上只有四色，**根本没有蓝/青**
（`EINK_4COLOR_PALETTE` = 黑 / 白 / `#E8B000` / `#C80000`）。
所以这不是"看起来有点脏"，而是**眼睛造出了一个硬件不存在的颜色**。
金黄旁边的抖动灰会被看成偏蓝的灰 —— 屏给不出蓝，眼睛自己补上了。

**实际用法（冰河大人 2026-10-29 10:37 明确）**：这条是**要蓝的时候的造法**，
不是“屏上没有蓝所以调不出来”。
既然眼睛会把“极度温暖的暖色（尤其黄）旁的抖动灰”补成冷色，
那要蓝就往那个方向布置：**一片极度温暖高饱和的黄做底/做邻，
中间（或紧邻）留一片密集黑白点混出的抖动灰** —— 眼睛会把那片灰看成蓝/青。
硬件不需要有蓝，眼睛自己造。

**注意**：这是**造蓝**的用法，不是“避开蓝”。我一度把它记反了。

**（我的推论，非冰河大人要求）** 这个造法不违反四色约束 ——
输出仍然只有黑/白/`#E8B000`/`#C80000` 四色，固件零改动；
变的只是**像素分布**：在想要蓝的区域布一片高频黑白点，
并让极度饱和的黄紧邻（做底或做邻）。要试得先找一块要蓝的画面，
由冰河大人肉眼验收。

**它推翻了我今天早上说过的一个说法**：我曾说"卡通 = 少中间调，天然避开坑"。
少中间调确实避开了**色相溢出**（绿青蓝相在四色里没有归属），
但**没有避开抖动灰** —— 卡通照样靠 Atkinson 把中间调抖成网点，照样在黄边产生灰。
卡通只是把灰的总量压小了，不是消灭了。

> 附注（我自己顺手量的，非冰河大人要求）：
> 大宋梦影整页的抖动灰占 11.25%，其中 80.4% 紧挨着黄色；
> 天工阁 2.77% / 62.1%。
> 这只说明效应**可能出现在哪里**，不构成对上面观察的"证明" ——
> 「那片灰看起来偏蓝」是冰河大人的肉眼判断，数据证明不了。

### 诚实边界

- 本项目**实测验证有效**的只有方案 D（box 下采样 + 只压暗墨色），且已通过冰河大人肉眼验收。
- 其余各条是我的理解，不是结论。
- **好不好看，永远是冰河大人的视网膜说了算** —— 算法指标（颜色占比、连通块、幂等率、
  detail 分数、高频比例）只作技术诊断，不能用于候选排序，更不能代替肉眼判断。
  诊断的产出是「这是什么、为什么」；排序的产出是「选这个」，那是他的活。

<!-- project: path:/home/open/.openclaw/workspace -->
---

## 十八、大宋梦影：卡通视觉系新模式（2026-10-29 09:16-10:05）

**提交 `3872275`**。方向由冰河大人定：**卡通、每天不同场景、模型现编**。
名字「大宋梦影」——「梦影」即虚构，史实顾虑消掉。

**为什么卡通是有技术依据的，不是风格偏好**：

第十七章那三条修复（拉无彩色区明度 / 纸面置白 / 压暗分层）**全是为救写实图而存在的**。
卡通 = 大色块 + 粗墨线，天然少中间调，一个坑都不踩。
丹青谱栽的正是写实（冷色相 17%，在四色里没有归属）。

**管线**（逐字沿用躺平巴拉 —— 唯一通过肉眼验收的卡通样板）：

- `core/songmeng.py`：LLM 出「场景/题记/画面」→ MiniMax image-01 生图 →
  `prepare_direct_art` **默认参数**（min-pool 保细墨线）→ 上屏。不做预四色化。
  并发锁 / 跨日轮换 / 失败复用昨日图 / 文字保底，全部照抄躺平巴拉。
- `modes-ours/songmeng.json`：400x234 全幅画心，400/296/648 三档版式。
- `json_content.py` provider 分发，`mode_catalog.py` catalog 注册。
- `tests/test_songmeng.py`：20 项回归测试（含隔离红线）。

**与躺平巴拉的关键差别**：`CARTOON_SPEC` 把造型语言写死
（圆润 Q 版、大色块填充、墨线连续不断、不要写实材质与渐变）。
主体每天不同，造型不写死，哪天就会画回写实 —— 直接踩回中间调的坑。

**隔离手法（值得复用）**：`json_content.py` / `mode_catalog.py` 工作树有他人未提交改动，
用 `git hash-object -w` + `git update-index --cacheinfo` 把
「HEAD + 我的改动」的版本单独暂存，提交里零他人改动（grep 验证过）。

**验证**：全量 467 passed；服务 active；`/api/modes` 注册 CUSTOM_SONGMENG；
HTTP preview 200；整页严格四色（白 56.0% 黑 18.4% 黄 16.7% 红 8.9%）；
画心 1:1 落屏；躺平巴拉/天工阁/丹青谱路径一行未动。

**验收**：2026-10-29 10:09 冰河大人确认「验证通过」—— 大宋梦影通过肉眼验收。
（视觉产出的最终判断依据；数值只作技术诊断。）

<!-- project: path:/home/open/.openclaw/workspace -->


## 十九、海错图：幻想海洋生物图鉴（2026-10-29 11:14-11:38）

**提交 `f8827f6`**。名字由冰河大人定（清人聂璜《海错图》），方向：每天换一只新生灵。

**画法（冰河大人定，不是我加的）**：**留白画水**。
屏上没有蓝/青，真蓝在四色里没有归属会被 Atkinson 抖成脏噪点
（丹青谱那张 17% 像素是绿青蓝相），所以水面不填色 —— 白纸面即是水，
只用稀疏墨线勾波纹与涟漪；主体墨黑粗线勾轮廓、金黄填身体、朱红点睛。

**一处弯路（值得记住）**：我先把这条经验记成了「屏上没有蓝，调不出来」，
实际冰河大人的意思是**反过来** —— 正因为没有蓝，才可以用「暖色旁的抖动灰会被看成冷色」
来造蓝。见本章末的附注。

**管线**（沿用大宋梦影 —— 已通过肉眼验收的卡通样板）：

- `core/haicuotu.py`：LLM 出「名/异名/尺寸/产地/特征/题识/画」→
  MiniMax image-01 生图 → `prepare_direct_art` **默认参数** → 上屏。
  不做预四色化。并发锁 / 跨日轮换 / 文字保底，照抄大宋梦影。
  带最近 7 则目录喂给 LLM 防重复（每天换新，不复现）。
- `modes-ours/haicuotu.json`：400x234 全幅画心，400/296/648 三档版式。
  仿博物图鉴排版：图 + 名 + 「第 N 则 · 异名 · 尺寸 · 产地」+ 特征 + 题识。
- `json_content.py` provider 分发，`mode_catalog.py` catalog 注册。
- `tests/test_haicuotu.py`：18 项回归测试（含隔离红线）。

**隔离手法**：`json_content.py` / `mode_catalog.py` 工作树有他人未提交改动，
用 `git diff -U0` 切出含 `HAICUOTU` 的 hunk，`git apply --cached --unidiff-zero`
只把这部分入索引，提交里零他人改动（grep 验证过）。

**验证**：海错图测试 18 passed；全量 485 passed；服务 active；
`/api/v1/render?persona=CUSTOM_HAICUOTU` HTTP 200、30000 字节、`x-mode-id` 回显正确、
渲染 38s（含生图）；设备拿到的 2bpp 严格四色。

**验收**：2026-10-29 11:38 冰河大人「已经很好了，别乱搞了」—— 通过肉眼验收。

**我犯的毛病（第二次，已记进 `.learnings/`）**：出图后我先量了「26.8% 像素是蓝/青相」，
据此判定「这图有问题」并准备加冷色相兜底层。冰河大人：「你总是去看什么百分比啊巴拉巴拉的，
为啥就不让它生成之后给我看呢？你在用数值来限制或者说替代我的眼睛么？」
**数值可以是诊断，不能当门槛** —— 同一只病的第一版是「叫你记录，我跑去验证」。

> 附注：「暖色旁的抖动灰会看成冷色」这条经验的方向已修正（`dcc4f0c`）——
> 它是**要蓝时的造法**，不是「调不出蓝」。海错图**没有**用它造蓝（留白画水即可）。

<!-- project: path:/home/open/.openclaw/workspace -->

## 二十、海错图：名称上屏（2026-10-29 14:57-15:19）

**提交 `48c37a8`**。冰河大人验收时发现「忘记把生物名称弄上去」—— 名称确实不在屏上。

**根因（不是忘了画，是版面溢出）**：
名称一直在内容里（`墨尾鲛姥`），模式 JSON 也有 `field: "name"` 块。
但图设 `height: 234`，正好占满整个正文区（y=36..270），
名称那几行被推到页脚底下裁掉了 —— 屏上根本没有名称。
画面右下那行「墨尾蛟姥轻旋」是**页脚 credit**（题识），不是正文，很容易看岔。

**改法**：名称**烙进图心上缘的题签带**（白底 + 实黑字 + 细分隔线），
画心压到 196，题签 38，合计仍是 234；版面与构图一行未动 —— 还是验收过的满屏大图版。

**为什么烙图里而不是用渲染器画**：
渲染器的中文小字走 **9pt 位图字体（PCF）**，笔画只有 1px，屏上糊成一团
（实测副题「云鲛婆」被啃掉）。自己用 TrueType 画黑字更实 ——
实测纯黑在四色归色下 **0 杂点**（黑像素旁的中间调像素为 0）。
位图字体档位对照（`load_font` 实测）：请求 10~12 → `NotoSerifSC-*-9.pcf`（size=12），
13 → 10pt，14 → 13pt，15 → 11pt，16+ → 12pt。

**关键手法**：`_stamp_name()` 返回**新图**（题签带 + 原画心），
不是往原图上 paste —— paste 会把底部裁掉。

**弯路的教训**：我一开始把「第一版」理解成候选版面里的 E（腾出 48px 给文字），
就把 `ART_DISPLAY` 改成 `(400, 186)` 改了版面。冰河大人指的其实是**设备上那版满屏图**。
我在发现自己先前那批候选图 `view_image` 只进了自己的上下文、**用户根本没看到**之后
才回退。教训：**给用户看的图必须真的发出去**，`view_image` 是给我自己看的。

**验证**：海错图 21 passed（新增 3 条测题签）；全量 531 passed；服务 active；
`/api/v1/render?persona=CUSTOM_HAICUOTU` HTTP 200 / 30000 字节 / 2bpp 严格四色；
躺平巴拉与大宋梦影复渲正常，共享归色层 `eink_snap.py` 一行未改。

**验收**：2026-10-29 15:19 冰河大人「可」—— 通过肉眼验收。

<!-- project: path:/home/open/.openclaw/workspace -->
## 十六、回顾功能根治：去设备绑定 + 自动更新（2026-10-30 18:00-18:30）

<!-- project: path:/home/open/.openclaw/workspace -->

冰河大人指出回顾页两个问题：**和设备强绑定**、**不能自动更新**。查证后两个都成立，
根因是同一个：**内容生成的触发时机 = 设备拉取的时机**。

### 事实核对（根治前）

| 问题 | 证据 |
|---|---|
| 强绑定 | 前端第一屏强制选设备，所有请求走 `/api/device/{mac}/gallery`；但 `core/gallery.py` 读 `runtime_cache/<state>/state.json`、`core/screen_archive.py` 写 `runtime_cache/<state>/screens/<day>.png`，**两处都不接受 MAC**。换台设备请求，字节完全一样 —— MAC 是纯门面 |
| 不能自动更新 | 整屏图只在 `api/routes/render.py:202-208`（设备真实拉图时）落盘。补图唯一手段 `scripts/history_backfill.py` 是手动跑一次就结束，**systemd 和 crontab 都没有它的定时任务**。缺图是静默的：`gallery.entries()` 里 `if not _has_screen(...): continue` |

### 根治做法（A+B 组合）

**A. 内容生成脱离设备拉取（服务器定时）**

新增 `backend/scripts/daily_content.py` + systemd `openclaw-inksight-daily.timer`（每天 07:00）：

- 走 `core/pipeline.generate_and_render`（和设备下发同一套出图管线），`mac=""`、`archive_screen=True`；
- 模板配置从 `configs` 表取当前生效那份，**只借位置/语言，不传 MAC**；
- 模式清单从 `core/gallery.py` 派生（唯一事实来源），wide 形态（电子世界）排除；
- 设备退化成纯显示器：它醒不醒、内容都在。屏幕坏了内容也不断档。

**踩到的坑（都修了）**：

1. **脚本 hang 不退出** —— aiosqlite 的工作线程不是 daemon，进程干完活卡住不退（systemd 会判超时）。修法：`finally` 里 `await close_all()`。`history_backfill.py` 有同样的毛病，一并修了。
2. **天气取错城市** —— 原本用 `api.shared.build_image`，它在 `mac=None` 时拿不到设备配置，天气退回默认城市（杭州）而不是郑州。改用 `core/pipeline.generate_and_render` 显式传 date_ctx/weather。
3. **凭证** —— 不传 llm/image provider，让 pipeline 走默认 MiniMax（服务端托管凭证）。

**B. 回顾接口脱离 MAC（账号作用域）**

新增 `backend/api/routes/gallery.py`：`GET /api/gallery`、`GET /api/gallery/image`，鉴权 `require_user`（Bearer 或 `ink_session` Cookie —— 后者是为了 `<img>` 标签带不了 Authorization 头）。

旧路由 `/device/{mac}/gallery` **保留**（兼容旧链接），内部同一套 `core/gallery.py`，不产生第二份数据。

前端 `webapp/app/gallery/page.tsx` 去掉「选设备」第一屏。

### 验证

- 后端接口四条全通（带真实 token）：模式列表 200 / 条目 200 / PNG 200 (image/png, 9893 bytes) / wide 现算 200
- 未登录一律 401
- 外网 `ruohan.site/ink/gallery` 200、`/ink/api/health` 200
- **systemd 实跑**：`Result=success ExecMainStatus=0`，当天 6 个模式的整屏图全部生成
- 历史回填 29/29 完成（海错图 2 + 丹青谱 3 + 大宋梦影 2 + 躺平巴拉 7 + 天工阁 8 + 铺道蚁 4 + 电子世界 3）
- pytest **534 passed**（含新增 `tests/test_gallery_api.py` 5 条）

### 已知缺口（本次没动）

- **日痕（rihen）不在回顾收录范围**：设备轮转列表里叫 `CUSTOM_DAYMARK`，gallery 收录的是 `CUSTOM_RIHEN`，state 有 7 天但 0 张整屏图。既有设计（test_gallery 锁定了 7 个模式），要收进来的话得改 `core/gallery.py` + 补测试。
- 回顾数据仍不按账号隔离（所有人看到同一份）。当前单机单账号够用；将来多账号要隔离，是「加隔离」而非现在的「假装隔离」。
- 旧设备路由 `/device/{mac}/gallery` 仍在，行为与新路由一致（同一份数据）。

