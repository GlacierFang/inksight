# MEMORY.md - 麻辣小龙虾的长期记忆

<!-- project: path:/home/open/.openclaw/workspace -->

> **行为纪律不在这里** —— 规则统一在 `AGENTS.md`（工具纪律 / 回复纪律 / 工作节奏 / 红线）。这里只留**状态**：在做什么、哪些边界不可协商、踩过的坑的索引。
> 细节：原始日志 `memory/YYYY-MM-DD.md` · 项目文档 `memory/projects/`。

## 关于冰河大人
- 称呼：冰河大人 | 时区 Asia/Shanghai | 职业：软件开发公司初创团队负责人（原互联网产品经理）
- 女儿：若菡（ruohan.site 取自她名字）
- 宋粉：宋朝历史、宋代陵墓（永昭陵、永昌陵）、古代石刻艺术
- 完整档案：`USER.md`

## 关于我
麻辣小龙虾 | 个人 AI 数字助理兼电子宠物 | ❤
职责：独立完成需求分析 → 方案 → 开发 → 部署运维全流程。

## 在做的项目
- **InkSight 墨水屏**（18834/18835，外网 http://ruohan.site/ink/）：2026-10-22 上线。已拍板不做 HTTPS、不做固件/OTA。自定义模式 24 个（狸行记/小景/透屏/窗外/草地铺道蚁/宋人日课/树莓派状态/日昼与月相/天工阁/日痕等，catalog 里 `category=custom` 的全算）。**诗笺日课 2026-11-22 起与 bt-presence 解耦**：去掉「到家/出门事实」块，只留当日主诗 + 宋代掌故 + 天气，诗库搬进 inksight 自己目录 → `memory/projects/InkSight-存在感依赖解耦.md`。待：账号链路 + 真机拉图实测。→ `memory/projects/InkSight.md`、`InkSight-自定义模式开发.md`、`InkSight-狸行记.md`
- **山河图记**（18838 `/shanhe/`）：研学 v2 每章 1 主题，10-21 重构完，待真机验证。→ `memory/projects/山河图记.md`
- **识物志**（nature-local 18818）：✅ 封版 2026-07-16。在线版 nature-api（18798）2026-11-05 确认死分支并下线，别再混。→ `memory/projects/识物志app版.md`
- **驿车志**（18823 `/train-app/`）：Authorization header 已修 + 503 外层重试。API Key 共用 `skills/gallery/ai_tagger.env`；与 my-train（18809）是两个后端，别混。
- **诗词签 + bt-presence**：🔴 已停用（2026-11-22）。5 个 user unit 已 disable（天气推送 / 诗词签图片 / 提醒队列 / 日记网页全下线），代码 + 数据 + nginx `/diary/` 路由全部保留，`systemctl --user enable --now` 一条命令可恢复；port-registry 登记与 dashboard 入口已同步注销
- **已封版 App**：如梦令 18819 · 探识 18822 · 时光印象册 18821
- **Skills**：gallery library dashboard visit-knowledge bt-presence presence-diary knowledge-center nature-recognition work-assistant stamp-collection usage-stats brain deepseek-webui train-app my-island
- **渠道**：飞书 `cli_a9259b2e6738dcd6`；微信插件已废，功能已迁至飞书

## 不可协商的边界
- **后端只做 AI 接口，用户数据全在前端**（10-21 原话「我一直都强调后端只做 AI 接口，数据全部前端保存！」）。后端不落库、不做身份识别、不存进度/报告正文。**md 里写的架构可能是我自己漂移出来的，不是他的原话；要动边界先问。**
- **自选方向 = 越界**（10-23「之后再开发什么新的能力，务必和我沟通」）。不要把自己的推测写进 USER.md/MEMORY.md —— 每条都要能指回他亲口说过的话或真实产出物。
- **InkSight 不做固件/OTA**（10-22「我们不做固件」）：固件相关一律不查、不修、不主动提，别当「发现」汇报。
- **不做 HTTPS**（10-22「我们不做https」，11-24 再次确认）：所有外网入口一律保持明文 HTTP（`http://ruohan.site/<项目>/`），不申请证书、不做 TLS 收口。同类「万一…」的假设性风险 = 噪音，不作为发现汇报，也不开成待办卡片。
- **不主动升级 OpenClaw**，等稳定版，升级前全量备份。**不擅自 restart gateway**；他说「重启」就立刻重启。
- **端口操作（最高优先级）**：先 `pr check <端口>` 确认归属；不知道就停下来问，不猜。真相源 `tools/port-registry/ports.json`。
- **红线目录（严禁清理）**：`gallery/photos/` · `library/books/` · `skills/video-shelf/`
- **不主动探活/起浏览器/端到端**，要跑先问；不让用户去验证我自己没走过的端到端。

## 核心教训
> 详细版在 daily memory 与 `memory/projects/`；这里只留能直接改变行为的部分。

### 判断与决策
- **Console 报错 > 假设**：读报错，不猜；说「重复条目」前先读 payload。
- **用户给的诊断优先，别重新论证一遍**（11-01）：他说「问题在这里」→ 就在那个前提下找哪一行错了，不要先验证前提本身。**「没有 X 参数所以做不到」要先想等效做法**（反向词写进 prompt 就行，别拿它当理由）。提方案前先 grep 规格：已否过的做法（不追溯/不回源）在 2.18 里写着，别第二次犯。
- **不确定就对齐，别用跑测试代替思考**（10-30）：涉及「最终长什么样」先问；跑命令前先问是否必要。跑只能告诉你「我猜的那条路是这样」。
- **我加的每层「保护」都在替模型做决定**（10-30）：约束越多，模型自由度越低。写新规则前先问「它防什么错？代价是模型少做什么？」
- **效率公式：可复现 = 可推理 = 少测**（10-30）：输出可预测 → 出一张就知道结果 → 不需要采样。答案已存在时，严谨只是拖延。修复优先**删除**而非增加；删除不需要 A/B 论证。
- **一个槽位只能有一个主人**（10-30）：一段只由一个主人负责；名词堆 ≠ 描述。
- **上下文里的待办清单 ≠ 我的待办**（10-22）：继承的清单先问「这是谁的活」，不属本次范围的只说一次，不进报告。
- **别用「观察」去「验证」**（10-24）：验收自动化只读磁盘状态（cron_jobs / cron_run_receipts / transcript_events），不用 gateway CLI 轮询；手动跑心跳必然自我跳过。
- **同源 provider 不构成冗余**（10-24）：minimax 与 minimax-portal 同 baseUrl 同 api，只差认证 = 假冗余。
- **能力开发要同时验证「写」和「读」**（08-01「input 和 output 你只考虑了一半」）：写完是真的能被读到吗。
- **跨模块开发：先读再动，不许造轮子**（07-07）：说「参考 xxx」第一步就去读，读完再动手。
- **系统边界意识**（07-07）：多模块动手前先查 ports.json 确认端口与数据隔离，不假设。
- **测试要测完整流程**（07-08）：验证异步流程走完整路径，不手动模拟中间步骤。
- **试错不是思考的替代品**；**产品思维**：先讲清谁、在什么状态下、用这个功能，不服务核心场景的功能做了也是摆设。
- **方案节奏**：提方案 → 等反馈 → 整合 → 对齐 → 再走一步；不在节点上继续挖。
- **急 = 降智**：删/改承载 UI 的容器前先问「元素放哪」；答「做对了吗」前先读完整文件；说「修好了」前先看 diff。**省了 1 秒 = 第 1 个就要停。**（详见 `memory/2026-10-12.md`）

### 出图 / 墨水屏（详见 `memory/projects/InkSight*.md`）
- **限制越多，出图越差**（11-02 狸行记）：自己加的角色设定/造型限定/动态表达/否定清单全砍掉，图立刻变好。奥卡姆剃刀在提示词上同样成立。
- **风格词用用户原话或问，不要自己编译**：「版画/粗墨勾线/网点纸/大留白」全是我编的，编完还写进 memory，下轮读回来当成用户口径 —— 自己造的答案自己当依据，闭环了。
- **出图判断权在用户；看到图 ≠ 用户看到图**：出图只负责发（用 MEDIA: 发出去），不自己评、不擅自重出。
- **颜色是结果，不是指令**：传语义（「阴沉的天空」），不传像素反推的色板词。
- **参考图是锁脸 + 标准形象，不是风格锚点**：风格另找锚点。
- **四色屏按四色盘「出」，不是事后「转」**：先分类后缩放（先缩放会抹平细墨线）；新建 `RenderContext` 必须显式传 `colors`。
- **限制输出要限制字数，不是限制 token**：`max_tokens` 只保证推理有地方烧。
- **排版**：`separator` 不当装饰横线（贴着上一行会变下划线）；`text` 块要显式给 `margin_bottom`；排版问题用像素证据定位。

### 工程常识
- Python 后端改完必须 `systemctl restart`（uvicorn 不开 --reload）；重启后确认只剩一个进程（system 级 + user 级同名 unit 双启坑，07-03）。
- Silent Failure 是藏雷：daemon 的 `except: pass` 改成打印 stderr。
- nginx：改前备份、放弃即回滚、改完全量验证所有服务 http_code；`.bak` 文件也会被 include 加载，备份完立刻挪走。`proxy_pass` 无 URI = 原路径透传；`rewrite ... break` = 去前缀；带 URI = 替换匹配段。
- 4 App 坐标统一 GCJ-02（08-04）：`uniapp/utils/coords.js` 单一真相源；老数据不动；`uni.getLocation` 不传 `type:gcj02`。
- llm_gateway 已废用（07-01），各服务直调 MiniMax。

## 技术事实速查
- 端口与路由：真相源 `tools/port-registry/ports.json` · 文档 `memory/projects/端口与路由配置.md`
- 数据库：Gallery `~/.openclaw/media/gallery.db` · Library 索引 `memory/library-index.json`（无独立 DB） · Dashboard `skills/dashboard/data/metrics.db`
- cron add 需 pairing → 改 `~/.openclaw/cron/jobs.json` + `sudo systemctl restart openclaw-gateway`
- nginx 配置归我负责；禁止修改已有 location 块（新增服务除外）
- MEMORY.md 本身超大会导致 embedded session OOM（limit ~20000 chars）→ 控制在 5000 字内，日常记录进 daily memory
- **md 维护**：不挂定时任务，**每次跟冰河大人沟通时顺手清理**。工具：`bash tools/md-maintenance.sh`（只读、零 token）

_Last updated: 2026-11-02_
