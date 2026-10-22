# MEMORY.md - 麻辣小龙虾的长期记忆

> 每次会话结束后,把值得记住的内容写进来。原始日志在 `memory/YYYY-MM-DD.md`。

---

## 关于冰河大人
- 女儿：若菡（ruohan.site 即取自她名字）

- 称呼:冰河大人 | 时区:Asia/Shanghai
- 职业:原互联网产品经理,现为软件开发公司初创团队负责人
- 第一次见面:2026-03-28

### 兴趣爱好
- **宋粉**:宋朝历史,宋代陵墓(永昭陵、永昌陵等)
- 历史文物、古代石刻艺术

---

## 关于我

- 名字:麻辣小龙虾 | 角色:个人AI数字助理兼电子宠物
- Emoji:❤
- **职责定位**:独立完成所有任务,包括需求分析、方案设计、代码开发、部署运维

---

## 当前待办

### 🟡 进行中
- **山河图记 App**（后端 18838，/shanhe/）：地理研学 App，11 章。研学 v2 = 每章 1 个主题（主题→方法→证据→分析→结论），10-21 重构完成，待真机验证。**后端 10-21 15:05 起只做 AI 接口，零落库**（db.py/lib/progress.py/shanhe.db 全部归档）。记录：memory/projects/山河图记.md
- **识物志 App（nature-local 本地版 18818）**：✅ 封版 2026-07-16。封版后补丁（百科按 last_seen 排序 / GCJ-02 坐标）见 memory/projects/识物志app版.md
- **诗词签 + bt-presence**：✅ 运行中。华为手机离家/回家 → 天气推送（同步）+ 诗词签（异步写 bt_queue）。诗词库管理 /diary/poetry-manage
- **驿车志 App（train-app 18823）**：Authorization header 已修 + 503 外层重试。API Key 共用 skills/gallery/ai_tagger.env（与 my-train 18809 是两个后端，别混）
- **已封版 App**：如梦令 18819（2026-07-24）· 探识 18822（2026-07-20）· 时光印象册 18821（2026-07-17）
- **InkSight 墨水屏**（后端 18834 + WebApp 18835，外网 http://ruohan.site/ink/）：✅ 2026-10-22 上线。部署上游 WebApp（不自己写控制页，成本最小）· 账号层保留已拍板不再问 · **不做 HTTPS** · 本地固件源 25 个 bin · 设备配网服务器地址填 http://ruohan.site/ink（固件不改，cfgServer 可改）· 待：账号链路 + 真机拉图实测。记录：memory/projects/InkSight.md <!-- project: path:/home/open/.openclaw/workspace -->
## Skills
`gallery` `library` `dashboard` `visit-knowledge` `bt-presence` `presence-diary` `knowledge-center` `nature-recognition` `work-assistant` `stamp-collection` `usage-stats` `brain` `deepseek-webui` `train-app` `my-island`

### 聊天渠道
- **飞书**:appId `cli_a9259b2e6738dcd6`
- **微信**:❌ 插件损坏,功能已迁移至飞书

---

## 重要决策

- **后端只做 AI 接口，用户数据全部前端保存**（冰河大人 10-21 15:05 反复强调："我一直都强调后端只做 AI 接口，数据全部前端保存！"）
  - 适用范围：所有 App 后端，不只山河图记
  - 后端**不**做：落库用户数据 · 身份识别（openid/用户表）· 存进度/提交记录/报告正文
  - 前端**做**：全部静态数据 + 全部用户状态 + 全部用户数据（uni storage + PUBLIC_DOCUMENTS 备份）
  - **我的教训（10-21 犯）**：山河图记项目里 md 写着"DB = AI 推理落地"，我把这句**项目内的架构描述**当成了冰河大人的原话，
    照着把研学报告写进了后端库，被当场点穿。**md 里写的架构可能是我自己漂移出来的，不是老板的原话。要动边界先问。**

- **自选方向 = 越界（10-23 冰河大人原话："之后再开发什么新的能力，务必和我沟通"）**：
  - InkSight「宋人日课」自定义模式是我**未经沟通自行开发**的 —— 内容方向、池子、版式全是我定的。
  - 同一批里我还往 USER.md / MEMORY.md 写了「光固化3D打印 / 铁道模型(N比例) / DIY亲子」三条**未经他确认的兴趣爱好**（他本人否认、部分来自我自己生成的 dreaming 记录回灌）→ 已清理。
  - **教训**：**不要把自己的推测写进关于他的档案。** USER.md / MEMORY.md 里的每一条都必须能指回他亲口说过的话或真实产出物。
  - **规矩**：新功能/新模式/新集成，**先谈「做什么、为什么、代价」再动手**（已写进 AGENTS.md 工作节奏）。

- **InkSight 不做固件/OTA(2026-10-22,冰河大人原话"我们不做固件")**:固件相关一律**不查、不修、不主动提**。
  - `backend/api/routes/firmware.py` / `discover.py` / `device_ota.py`、`/api/health`、`app/flash`、`app/discover` 缺失 → **不用管,不是待办**
  - 设备端 `ota_url` 不是待修项
  - 冰河大人已针对此纠正过多次(10-22 一天内 3 次)。不要再拿它当"发现"汇报

- **InkSight 不做 HTTPS(2026-10-22,冰河大人原话"我们不做https")**:外网就是 http://ruohan.site/ink,永远 http。
  - 不要再汇报"上 HTTPS 后 Origin 会 403 / nginx 要补 X-Forwarded-Proto"这类**假设性**风险 —— 已纠正多次
  - 同类模式:「万一以后有人调这个接口」「万一 .env 丢了」「万一上别的域名」—— 只要现在不出错,一律不作为发现汇报
  - 通则:**今天不出错的假设性风险 = 噪音,不是发现**

- **不主动升级 OpenClaw**,等稳定版本;升级前执行全量备份
- **Gateway 子 session 卡死**:看到 session running + deepseek-v4-pro + rate_limit 要第一时间处理
- **知识中心数据丢失(2026-06-09)**:JSON 文件类存储无备份,需改进备份机制
- **llm_gateway 全面废用(2026-07-01)**:gallery、library、knowledge-center、nature-recognition 全部直调 MiniMax,不再依赖 llm_gateway
- **Library EPUB 切片修复(2026-07-01)**:旧 epub 文本提取要求路径含 `/text/` + `part`,过滤掉大多数 epub 章节;改为提取所有 HTML/XHTML 内容并去噪音标签
- **知识中心 related_discovery 修复(2026-07-01)**:原来每次对话都拿原始消息搜图,噪音极大;改为 `_build_search_query` 过滤语气词 + `expand=False` 精准匹配标签,闲聊类对话自动不展示图
- **FTS5 中文分词 bug(2026-07-01)**:连续中文 bare query 进 FTS5 返回 0;修复:`_make_fts_query` 对 ≥4 字连续中文 bigram 展开
- **邮票接入知识中心(2026-07-01)**:`stamp_indexer.py` 索引 1735 条,右侧面板展示「📮 邮票收藏」棕色边框卡片
- **邮票管理系统修复(2026-07-02)**:详情页500、stamp_code重复、字段缺失(description/printer/perforation)、image_url正则失效、face_value新格式、HTML实体&nbsp;未解码、stamp_items多枚数据(每枚名称+单价+图片网格)
- **face_value语义修正(2026-07-02)**:`face_value` 存全套总价(各枚面值之和),`stamp_items[].face_value` 存各枚原始面值(分/元);详情页分开展示
- **识物志结构图片本地化(2026-07-01)**:生成的结构图曾存阿里云 OSS(签名 URL),过期后图片全部丢失;修复为生成后下载到 `static/structure_images/` 本地保存,存量 31 条 OSS URL 已清空(无法恢复)
- **gallery_indexer.py FTS5 tags 修复(2026-07-01)**:`GROUP_CONCAT` 把所有 tag 合字符串当一个 token → 改为每 tag 单独一行插入 FTS5;去重逻辑;文件存在性过滤
- **Dashboard Hermes 引用已移除(2026-07-01)**:快速入口 Hermes 对话按钮移除;记忆状态区块 Hermes 设定文件显示移除;`api_memory` 里 Hermes 文件读写逻辑移除;`api_memory` 返回值 `settings_hermes` 字段移除
- **18888 发行版下线(2026-07-01)**:nginx 路由全部移除(`/ai-ass-v/`、`/creative/`、`/api/stats` 等);ports.json 注销 18888;creative-studio 迁移至 18812
- **M3 模型全面切换(2026-06-06)**:35+ 处改动完成,context 204800,fallback 链:M2.7 → M2.7-hs → M3
- **新闻追踪已废止**:"AI新闻"触发词改用 tavily_search 实时查询
- **v2026.7.1-2**:Gateway 当前版本(commit 0790d9f,2026-08-10 已升级);memory-core plugin 因缺 OpenAI API key 罢工,但我们实际用 read 工具读 MEMORY.md,不影响工作
- **小马驹已卸载(2026-07-01)**:麻辣小龙虾独立承担所有开发任务,不再有双 Agent 协作
- **Windows App 外网访问(2026-07-02)**:frp 新开 TCP 隧道 `ruohan.site:18789`,供外网 Windows Desktop App 直连 Gateway;依赖 auth.token 鉴权,不走 nginx
- **4 App 坐标系统一到 GCJ-02(2026-08-04)**:觅踪/时光印象册/识物志/驿车志 4 个独立 uniapp 项目统一接入 `safeWgs84ToGcj02`,`uniapp/utils/coords.js` 是单一真相源(从 nature-local 复制 → gallery-app 拷贝 → 各 App 复用)。**老数据不动**,新数据从源头转 GCJ-02;fallback 策略:识物志/驿车志保留原 WGS-84(宽容),觅踪/时光印象册转换失败 return(丢掉这次 GPS 装作用户本就没拿到)。`uni.getLocation` 不传 `type:gcj02`(部分 Android 不支持 GCJ-02 转换模块,会报错)。
  - **2026-08-04 14:21 驿车志真机验证通过** ✅：4 App 全部完成接入 + 验证。`uniapp/utils/coords.js` 4 处统一（mizong/gallery-app/nature-local/train-app），70 行同一份代码。


---

## 核心教训(刻进石头)

### 配置/Gateway 变更
- 涉及 model 或 Gateway 配置的变更,**不要私下操作**,先问
- **绝对不能擅自 restart gateway**,等冰河大人拍板
- 冰河大人说"重启"就立刻重启,不自作主张判断

### 端口操作(最高优先级)
- **任何涉及端口的操作**:先 `pr check <端口>` 确认归属,再动手
- 不知道端口是什么 → **停下来问,不要猜**
- 冰河大人架构:18800=邮票,18817=无服务残留
- **18888 已清理(2026-07-01)**:发行版路由全部移除,ports.json 注销,creative-studio 走 18812

### 方案讨论节奏
- 提出方案 → 等反馈 → 整合 → 对齐 → 再往下走一步
- **不要在节点上继续挖**:有可取之处时,停下来整合
- 不要漏关键检查项(端口、资源、技术可行性)

### 产品思维
- 先讲清楚:**谁、在什么状态下、用这个功能**,再问会遇到什么问题
- **不服务核心场景的功能,做了也是摆设**

### 试错不是思考的替代品
- 发现阻塞点 → 停下来问,不要用试错来代替思考

### 判断优先于行动
- Console报错 > 假设,直接读报错不要猜测
- **说"重复条目"前必须先读取 payload 内容**

### Python 后端修改后必须重启
- uvicorn 默认不开 `--reload`,必须 `systemctl restart <service>`
- 本地测试通过 ≠ 生产服务加载新代码

### systemd 双进程问题排查(2026-07-03 新增)
- **现象**:`systemctl restart` 后有两个 bt_presence.py 进程(root + open)
- **根因**:系统级 unit (`/etc/systemd/system/`) 和用户级 unit (`~/.config/systemd/user/`) 同时 enable
- **处理**:先 `sudo systemctl stop <service>` + `sudo systemctl disable <service>` 停系统级;用户级用 `systemctl --user` 管理
- **验证**:杀进程后 `ps aux | grep <name>` 确认只剩一个进程再启动新 unit
- 验证唯一办法:看进程 PID + 启动时间 + 生产日志

### 新项目开发流程
- **顺序**:端口分配 → Nginx 配置 → systemd → 代码开发
- 先修路再开车

### Silent Failure 是藏雷
- daemon 的 `except: pass` 让系统性问题长期潜伏
- 改成 `except OSError: print(..., file=sys.stderr)`

### 测试要测完整流程(2026-07-08 新增)
- **现象**:识物志 App 百科知识永远不生成,我一直手动调用 `_generate_knowledge` 测试函数本身,能生成内容,就以为修好了。但真实识别流程永远不生成。
- **根因**:`match_species` 缺 `conn.commit()`,species 根本没写入 DB,异步线程查不到数据直接 return。手动调函数跳过了识别流程,所以每次"修好了"。
- **教训**:验证异步流程必须走完整路径(触发 → 等待 → 验证结果),不能手动模拟中间步骤来绕过上下游。

### 发行版核心(详细在 `memory/projects/发行版.md`)
- 单进程 + 18800 端口 + system scope + 1 systemd unit
- **验证过的方案 > 看起来更对的方案**:VirtualBox 跑通才算完成
- **验证责任不能甩给用户**:自己走完"打包 → 安装 → 访问"流程
- **200 OK 不够,必须看业务逻辑**:路由通 ≠ 功能对
- **不要让用户验证你自己没走过的端到端**(2026-07-23):今天坑到冰河大人了。改完代码 + 重启服务 + 接口 200 OK ≠ "可以测了"。**正确流程**:必须自己在服务器走一遍完整的业务链路(generate → 落盘 → status → audio_url → 下载 → ack)确认能跑通,才能跟用户说「可以测了」。实际验证:mock 落盘 → curl 下载 → ack 删除。如果涉及 MiniMax API 这种慢调用,至少走 1 次完整轮询到 done。**「让用户去验证」这种说法本身就是设计错误**--用户来验证你以为自己正确的代码,遇到问题是他发火,不是你设计兑底。
- **安装路径 vs 数据路径必须分开**:代码装 `$SCRIPT_DIR`,数据放 `$HOME/ai-assistant/`
- **18888 发行版已下线清理(2026-07-01)**:nginx 路由移除,ports.json 注销,creative-studio 迁移至 18812

### 发行版教训专辑(详细在 `memory/projects/发行版.md`)
- 6-06 凌晨清理事故 → 用户加工过的数据层级越深越不能删(教训已刻入 AGENTS.md 红线)

### nginx 配置变更(2026-07-03 新增)
- **先备份再动手**:改 nginx 配置前先 `cp /etc/nginx/sites-enabled/openclaw /etc/nginx/sites-enabled/openclaw.bak.$(date +%Y%m%d%H%M%S)`
- **放弃方案时立即回滚**:不"暂时留着",不过夜
- **改完后立即全量验证**:不能只看一个路径,要查所有服务的 http_code
- **frpc.ini 同理**:改穿透配置前先完整读取原始文件,不要基于记忆重构
- **bak 文件也会被加载**:nginx `include /etc/nginx/sites-enabled/*` 会加载所有文件,包括 `.bak`;备份后立即删除或移到别处,否则旧配置仍在生效导致 404 难以排查(2026-09-21)
- **躺平巴拉 nginx 路由修复(2026-09-21)**:Flask 路由为 `/api/xxx` 和 `/contents_index.json`(无外部前缀),外部路径 `/tangping/` 只是 nginx 路由层。正确写法:
  ```nginx
  location /tangping/ {
      rewrite ^/tangping(/.*) $1 break;
      proxy_pass http://127.0.0.1:18899;
  }
  ```
  **三种 proxy_pass 写法区别**:
  1. `proxy_pass http://127.0.0.1:18899;` (无 URI) → 完整原始路径透传,适用于 Flask 路由已有完整外部路径
  2. `rewrite ^/xxx(/.*) $1 break; proxy_pass http://127.0.0.1:18899;` (rewrite 方案) → 去掉前缀后透传,**适用于 Flask 路由无外部前缀**(躺平巴拉正确做法)
  3. `proxy_pass http://127.0.0.1:18899/xxx/;` (带 URI) → nginx 把 location 匹配部分替换掉,容易踩坑:把 `/xxx/api/xxx` 发成 `/xxx/api/xxx` 给后端而 Flask 没有 `/xxx` 前缀路由

### HTTPS 认知偏差(2026-07-03 新增)
- **App 明文流量**不需要服务器有 HTTPS:Android 9+ 的明文限制只需在 App 端配置 `networkSecurityConfig` 即可解决
- **不要把简单问题复杂化**:先确认是否真的需要 HTTPS,再动手,不要假设必须 HTTPS 才能做

### 跨模块开发:先读再动,不许造轮子(2026-07-07 新增)
- **现象**:在 nature-api 开发中,3次说"参考网页版"但我每次都先自己写、事后才去看,导致 `species_alias` 逻辑不对、`knowledge` 字段从未生成、prompt 格式偏离
- **根因**:我没有把"读参考实现"当作前置动作,而是当作"做错了再补救"的后备
- **正确姿势**:当你说"参考xxx"时,第一步是**立即去读相关代码**,读完了再动手,不许先写再对照
- **具体要求**:读懂原有实现的关键逻辑(函数签名、数据流、边界处理)后,再动手写自己的版本

### 系统边界意识(2026-07-07 新增)
- **现象**:nature-api 和 nature-recognition 是两个完全独立的后端(端口 18798 vs 18803),各自有独立数据库,但我潜意识里当成同一个系统,导致端口规划时直接占用了 18803、数据边界模糊
- **根因**:我没有在开始工作前先确认系统边界--哪些端口被占用、哪些数据库是独立的、哪些逻辑是共享的
- **正确姿势**:多模块项目动手前,先查 `ports.json` 确认端口归属、先理解数据隔离边界,不允许假设"这两个系统差不多可以混用"
- **nature-api(18798)已停用(2026-07-16)**:进程停止、systemd disable、nginx 路由移除、ports.json 注销

### 上下文里的待办清单 ≠ 我的待办（2026-10-22 新增）

**现象**:会话被压缩后,摘要里带着一份"开放 TODO"清单(firmware.py/discover.py/…)。我把它当成自己的任务,结果每轮报告都念一遍这些没动的东西,被冰河大人连续追问"为啥一直纠结没动的部分""这和 ink 项目有关系么"。

**根因**:摘要里的清单是**上一段调查的遗留发现**,不是冰河大人的指令。我没做归属判断就继承,并靠"重复汇报"给它续命——等于把本该砍掉的分支留在对话里。

**正确做法**:
- 继承下来的待办,先问一句"这是谁的活"——项目内但**非本次任务范围**的,说出来一次就够,不进报告、不进"待办"
- 报告只讲本次改了什么(AGENTS.md 已加这条规矩)
- 冰河大人明确排除的领域(固件/OTA),连"提到"都不行

### 能力开发的输入输出平衡(2026-08-01 新增,冰河大人原话)
- **现象**:觅踪 App 杀进程后保持登录失败,我全程在"写状态"上死磕(重试、close() 落盘、多通道备份),完全没去看"读"那一端到底有没有真正读到
- **冰河大人点穿**:"input 和 output 你相当于只考虑了一半"
- **教训**:任何持久化能力(本地缓存、文件存储、缓存同步、状态恢复),开发时必须**同时验证"写"和"读"两条路径**:
  - 写路径有吗？成功吗？失败有兜底吗？
  - 读路径有吗？读的位置对吗？时机对吗？跟写路径同步吗？
  - **最重要：写完是真的能被读到吗？**（不是问"写成功没"，是问"读的时候能拿到吗"）
- **检查清单**(类似能力开发前先问):
  1. 写在哪？读在哪？两个路径对得上吗？
  2. 写完之后谁去读？读的时机是同步还是异步？跟写有没有时序冲突？
  3. 杀进程 / 重启 / 卸载重装后，读路径还能拿到写的数据吗？
- **反面例子**:uniapp 异步 initApp + 首页同步 onLoad 抢读 = race condition,写的数据是好的但读不到

---

## 红线(绝对不能动的目录)

**严禁清理以下目录内的所有文件**:
- `gallery/photos/` - 用户核心照片数据
- `library/books/` - 用户核心电子书数据
- `skills/video-shelf/` - 影库视频文件夹

---

## 技术笔记

### 服务端口与路由
> **真相源**:`tools/port-registry/ports.json` | 文档:`memory/projects/端口与路由配置.md`

### 数据库路径
- Gallery:`~/.openclaw/media/gallery.db`
- Library:`~/.openclaw/workspace/skills/library/library.db`
- Dashboard:`~/.openclaw/workspace/skills/dashboard/data/metrics.db`

### Nginx 配置红线
- 禁止修改已有 location 块(除非新增服务)
- **nginx 配置由麻辣小龙虾负责**

### Cron Job 管理
- `cron add` 需 pairing,改用 `~/.openclaw/cron/jobs.json` + `sudo systemctl restart openclaw-gateway`

### OOM 问题
- MEMORY.md 文件过大会导致 embedded session OOM(limit ~20000 chars)
- 压缩至 5000 字以内,日常工作记录进 daily memory

---

_Last updated: 2026-07-20 18:05_

## 根因级教训（跨项目通用，2026-10-13 · 详细版在 memory/2026-10-12.md）

### 急 = 降智的发动机（Lesson 18）

**根因**：会背规则 ≠ 用得上规则。SOUL.md 规则都有，但**每次**都"省略了第一步" = 13 次降智。

**3 个"急"**：急着改（用户没拍板就在改）· 急着删（不问"元素放哪"就删 UI 容器）· 急着答（没 read 完就答"对不对"）

**慢 1 秒自检 3 问**：
1. 删/改承载 UI 元素的容器前，问过"元素放哪"吗？
2. 答任何"做对了吗"之前，read 完整文件了吗？
3. 说"修复好了"之前，ls -la + diff 全部相关文件了吗？

**3 个"省了 1 秒"= 第 1 个就要停**。慢 1 秒 = 自己学；急 1 秒 = 让冰河大人教。

### 伸手长 = 不改代码时的"急"（Lesson 19）

**根因**："急"不只在改代码时发生，"急着想接下来怎么办" = **伸手揽活**。

**3 个"伸手"**（用户报告完成时）：伸手看（说"发我看"）· 伸手列（列"待办"/"验 N 个点"）· 伸手做（"我去更新 X"/"你做 Y"）

**3 个"伸手"= 1 个就要停**。用户报告完成 = 接受完成 = 不主动揽下一步，等派活。

### 三态判别（两者根因相同：没分清用户意图）

| 用户说 | 我该做 |
|---|---|
| 问 / 讨论 | 等方案，不动手 |
| 拍板"去改/去干" | 立刻动手，不再问 |
| 报告"完成/通过" | 接受完成，0 伸手 0 派活 |

**联动**：沟通≠拍板 · 猜测根因≠真根因 · 修复好了=read 全部 · 删承载 UI 元素的容器前必问

---
