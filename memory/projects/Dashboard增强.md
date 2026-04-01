# Dashboard 增强计划

## 项目背景

参考两个源码项目：
- **openclaw-control-center**（TypeScript）：https://github.com/TianyiDataScience/openclaw-control-center
- **oa-cli**（Python + React）：https://github.com/Amyssjj/Agent_Exploration/tree/main/CLIs/oa-cli

源码存放位置：`workspace/references/`

---

## 一、现状分析

现有 Dashboard（Flask，端口 18794）功能：

**✅ 已完成功能：**
- ✅ Cron 任务执行状态（当日）
- ✅ API 调用统计（分 source）
- ✅ 系统状态（Gallery、cron、磁盘空间）
- ✅ 自媒体发布日历
- ✅ P0/Cron 可靠性 + Agent 活跃度
- ✅ P1/SQLite 历史积累 + Cron 趋势图
- ✅ P2/用量拆分（主会话/定时任务/飞书/微信）
- ✅ **Session Context Pressure（上下文占用）**
- ✅ **Session 类型分类（Cron/Main/飞书/微信）**
- ✅ **Cron nextRunAt + due/late 健康状态**
- ✅ **Connector Status（数据源连接状态）**
- ✅ **Healthz 系统健康状态**
- ✅ **Burn Rate 基于 Token Plan 真实配额**
- ✅ **磁盘空间细分（Gallery/Library/工作空间）**
- ✅ **pubCount null 错误修复**

**⚠️ 待处理：**
- ❌ Usage 页柱状图和饼图从未渲染（历史遗留，前端图表库问题）
- ❌ 发布日历标记后端持久化未验证
- ❌ 深入分析模块逻辑问题（列出所有任务，无意义）
- ❌ 记忆状态模块 999天前 bug

**💡 新发现：**
- Token Plan API：`GET https://www.minimaxi.com/v1/api/openplatform/coding_plan/remains`
  - M2.7 配额数据完全可信（5h窗口 1500次，本周 15000次）
  - speech-hd/image-01 的 interval 与后台「自然日」口径不一致，仅供参考
- OpenClaw 版本：2026.3.28
- API Key 存储：`~/.openclaw/agents/main/agent/auth-profiles.json`

---

## 二、源码详细分析

### 2.1 openclaw-control-center（TypeScript）

**源码路径：** `references/openclaw-control-center/openclaw-control-center-main/src/`

#### 核心文件清单
```
src/
├── runtime/
│   ├── usage-cost.ts         # 用量核心，含 Context Pressure + Subscription Status
│   ├── cron-overview.ts     # Cron 概览，含 nextRunAt + 健康状态
│   ├── healthz.ts           # 系统健康状态（snapshot 新鲜度 + monitor lag）
│   ├── monitor-health.ts     # 运行时监控 lag 状态
│   ├── budget-governance.ts  # 预算超限判断
│   ├── budget-policy.ts     # 预算策略配置
│   ├── session-status-parser.ts  # Session 状态文本解析
│   ├── agent-roster.ts      # Agent 名册（合并 config + runtime）
│   ├── openclaw-cli-insights.ts  # CLI insights 读取
│   └── monitor.ts           # 监控主循环
├── clients/
│   ├── openclaw-live-client.ts  # Gateway WebSocket 实时客户端
│   └── factory.ts           # 客户端工厂
├── adapters/
│   └── openclaw-readonly.ts # 只读适配器
└── types.ts                # 共享类型定义
```

#### 核心设计原则
- **只读优先**：`READONLY_MODE=true` 为默认，所有写操作仅限 `runtime/*` 本地文件
- **数据源透明**：每个数据点注明来源（snapshot / runtime / provider）
- **多路径兜底**：订阅信息来源尝试多个路径，connected > partial > not_connected

#### 关键模块详解

**1. usage-cost.ts（用量模块）**
- 读取 `agents/*/sessions/*.jsonl` 的 assistant message usage 统计 token
- 读取 `agents/*/sessions/sessions.json` 的 `contextTokens` 做上下文压力计算
- 按 `today / 7d / 30d` 三个窗口计算用量
- **Context Pressure**：按 `contextLimitTokens` 的 70%（warn）/ 90%（critical）设阈值
- **Session Context Window**：每个 session 显示使用量百分比 + `ok/warn/critical` 状态 + "快满了要烧钱" 警示
- **Usage Budget Burn Rate**：根据日均消耗预测「还剩多少天会超预算」
- **Subscription Status**：读取多个路径 `~/.openclaw/subscription.json`、`billing/subscription.json` 等，显示「额度已用/剩余/重置时间」
- **Connector Status**：每个数据源标注 `connected/partial/not_connected`，并给出「如何连接」的 hint
- **Session 类型分类**：从 sessionKey 解析出 `Cron/Discord/Telegram/Main` 四类
- 并发扫描：`RUNTIME_USAGE_SCAN_CONCURRENCY = 8`
- 用量来源拆分：`byAgent / byProject / byTask / byModel / byProvider / bySessionType / byCronJob / byCronAgent`

**2. cron-overview.ts（Cron 概览）**
- 显示每个 job 的 `nextRunAt` + `dueInSeconds`
- 健康状态：`scheduled / due / late / unknown / disabled`
- `scheduled`：正常运行，下一次执行时间在将来
- `due`：应该在过去执行的，但还没执行（lag ≤ 2x interval）
- `late`：严重延迟（lag > 6x interval）
- `unknown`：无法判断下次执行时间
- `disabled`：已禁用

**3. healthz.ts（系统健康状态）**
- Build info：`name / version / node / readonlyMode`
- Snapshot 新鲜度：`ageMs` + 阈值判断 `ok < 12xinterval / warn < 48xinterval / stale`
- Monitor lag 状态：`ok / warn / missing / stale`
- 整体状态：`ok / warn / stale` 三档

**4. session-status-parser.ts**
```typescript
// 从 session 状态文本解析 model/tokens/cost
const model = matchOne(raw, /Model:\s*([^·\n]+)/)?.trim();
const tokensIn = toInt(matchOne(raw, /Tokens:\s*(\d+)\s*in/i));
const tokensOut = toInt(matchOne(raw, /in\s*\/\s*(\d+)\s*out/i));
const cost = toFloat(matchOne(raw, /Cost:\s*\$?\s*([0-9]+(?:\.[0-9]+)?)/i));
```

**5. budget-governance.ts（预算治理）**
- Scope：`agent / project / task`
- 输入：session usage snapshots（tokensIn, tokensOut, cost）
- 输出状态：`ok / warn / over`
- 阈值计算：`warn ratio = 0.8`，超限则 alert

---

### 2.2 oa-cli（Python + React）

**源码路径：** `references/oa-cli/Agent_Exploration-main/Agent_Exploration-main/CLIs/oa-cli/`

#### 核心文件清单
```
src/oa/
├── core/
│   ├── scanner.py       # 自动扫描 ~/.openclaw/ 发现 agents/cron jobs
│   ├── config.py        # 项目配置（goals + agents）
│   ├── schema.py        # 数据模型
│   └── tracing.py       # OTel 兼容 tracing
├── pipelines/
│   ├── base.py          # Pipeline 基类
│   ├── cron_reliability.py  # G1 Cron 可靠性
│   └── team_health.py    # G2 Team 健康
└── server.py            # HTTP Server（路由 + SQLite 查询）

dashboard-src/src/
├── components/
│   ├── GoalCard.tsx         # 目标卡片（含 Sparkline + Trend Badge）
│   ├── HealthSummaryStrip.tsx  # 全局健康态势一行
│   ├── SystemHealth.tsx     # 系统健康详情
│   ├── MechanismView.tsx    # 数据流可视化（SVG）
│   ├── GoalDetailSection.tsx # 指标详情（定义/数据源/计算逻辑）
│   └── TracesView.tsx       # Trace 详情
├── hooks/
│   └── useOAData.ts         # 数据获取 hook
└── types.ts                 # 类型定义
```

#### 核心设计原则
- **纯只读扫描** `~/.openclaw/` 文件，无任何 agent 侵入
- **Pipeline 机制**：新增指标只需写一个 Python 类继承 `Pipeline`
- **零额外依赖**：纯 Python core，无 Node/npm/venv

#### 关键模块详解

**1. scanner.py（Agent 自动发现）**
```python
# 自动发现 agent 逻辑（从 session 文件名解析）
for path in sessions_dir.iterdir():
    if "agent:" in name:
        parts = name.split(":")
        agent_id = parts[1]  # 提取 agent ID
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
```

**2. cron_reliability.py（G1 Cron 可靠性）**
- 读取 `~/.openclaw/cron/jobs.json` 获取 job 列表
- 读取 `~/.openclaw/cron/runs/*.jsonl` 过滤当日数据
- 成功：`status == "completed"`；失败：`status in ("failed", "error")`
- 写入 SQLite `cron_runs` 表
- OTel tracing：每个步骤带 span，支持点击查看完整链路

**3. team_health.py（G2 Team 健康）**
- 按日期统计每个 agent 的 session 数量
- 检查 memory 文件是否存在于 `agents/<id>/memory/YYYY-MM-DD.md`
- 写入 SQLite `daily_agent_activity` 表

**4. server.py（API 路由）**
```python
api_routes = {
    "/api/goals": self._api_goals,           # 所有 goal + 最新指标
    "/api/goals/metrics": self._api_goal_metrics,  # 时间序列
    "/api/cron-chart": self._api_cron_chart, # Cron 图表数据
    "/api/team-health": self._api_team_health,   # Team 健康趋势
    "/api/traces": self._api_traces,          # 执行 trace
    "/api/health": self._api_health_summary,  # 全局健康汇总
    "/api/config": self._api_config,          # 项目配置
}
```

**5. GoalCard.tsx（目标卡片 UI）**
```typescript
// Sparkline：左上角 30 天迷你趋势线
// Trend Badge：指标旁显示 ▲ +2.3 或 ▼ -1.1
// Health Color：healthy=绿 / warning=黄 / critical=红
```

**6. HealthSummaryStrip.tsx**
```
🟢 3 healthy  🟡 1 warning  🔴 0 critical  全局态势一行
```

**7. GoalDetailSection.tsx（指标定义面板）**
- 点击任意指标可查看：
  - **数据源**：哪个文件/API
  - **计算逻辑**：怎么算的
  - **用途**：这个指标用来干什么

**8. MechanismView.tsx（数据流可视化）**
- SVG 流程图：「Cron JSONL → Pipeline → SQLite → Dashboard」
- 直观展示数据如何流转

---

## 三、可借鉴功能完整清单

### 3.1 按实现难度分级

#### 🔴 立即可做（数据可得，实现简单）

| # | 功能 | 源码出处 | 说明 |
|---|------|----------|------|
| 1 | **Session Context Pressure** | usage-cost.ts | 读取 `sessions.json` 的 `contextTokens`，显示使用百分比 + ok/warn/critical |
| 2 | **Sparkline + Trend Badge** | GoalCard.tsx | 前端改动，SQLite 已有历史数据，直接加字段 |
| 3 | **Cron nextRunAt + due/late 健康状态** | cron-overview.ts | 显示下次执行时间 + 延迟状态 |
| 4 | **Connector Status** | usage-cost.ts | 给每个数据源标注 connected/partial/not_connected + hint |
| 5 | **自动发现 Agent** | scanner.py | 从 session 文件名自动解析 agent ID，无需手动配置 |

#### 🟡 中期（需要多数据源融合）

| # | 功能 | 源码出处 | 说明 |
|---|------|----------|------|
| 6 | **Usage Subscription Status** | usage-cost.ts | 读多路径 subscription.json，显示已用/剩余/重置时间 |
| 7 | **Burn Rate 预算预测** | budget-governance.ts | 基于日均消耗预测还剩多少天超预算 |
| 8 | **Healthz 系统状态** | healthz.ts | 量化 snapshot 新鲜度 + monitor lag |
| 9 | **Session 类型分类** | usage-cost.ts | 解析 sessionKey 得 Cron/Discord/Telegram/Main 四类 |
| 10 | **Usage 来源完整拆分** | usage-cost.ts | byAgent/byProject/byTask/byCronJob 多维度 |

#### 🟢 长期（架构升级）

| # | 功能 | 源码出处 | 说明 |
|---|------|----------|------|
| 11 | **Pipeline 扩展机制** | pipelines/base.py | 重构指标采集为插件式，新增只需写 Python 类 |
| 12 | **Mechanism View** | MechanismView.tsx | SVG 数据流可视化 |
| 13 | **Metrics 定义面板** | GoalDetailSection.tsx | 点击指标看数据源/计算逻辑/用途 |
| 14 | **OTel Trace 链路** | tracing.py | 每个数据点可追溯完整链路 |

---

### 3.2 详细功能说明

#### F1: Session Context Pressure（上下文压力）⭐⭐⭐
**现状：** Sessions 页无 context 使用统计
**目标：** 每个 session 显示 `使用tokens / 上下文上限` 的百分比

数据来源：`~/.openclaw/sessions/*.json`（session 文件里的 tokensIn/tokensOut 字段）

**UI 状态：**
- `ok`（绿色）：使用率 < 70%
- `warn`（黄色）：使用率 70%-90%
- `critical`（红色）：使用率 > 90%（显示"快满了要烧钱"）

**参考代码（usage-cost.ts）：**
```typescript
const CONTEXT_WARN_RATIO = 0.7;
const CONTEXT_CRITICAL_RATIO = 0.9;
const usagePercent = contextLimitTokens > 0
  ? (usedTokens / contextLimitTokens) * 100
  : undefined;
```

#### F2: Sparkline + Trend Badge（迷你趋势线 + 变化标签）⭐⭐⭐
**现状：** 无任何趋势可视化
**目标：** Goal Card 左上角显示 30 天迷你折线图 + 变化箭头

数据来源：SQLite `goal_metrics` 表已有历史数据

**UI 效果：**
```
        ╭──╮
   ╭────╯  ╰────╮        ─── ─ ─
▂▃▅▇▅▃▂▁▁▂▃▅▆▅▃▂▁▁   ▲ +2.3（相比上期）
```

#### F3: Cron nextRunAt + due/late 健康状态 ⭐⭐⭐
**现状：** 只显示"今日执行次数"，无未来状态
**目标：** 每个 job 显示"下次执行时间" + 健康状态

**健康状态定义：**
- `scheduled`：正常运行，下一次执行时间在将来
- `due`：应该在过去执行的，但还没执行（lag ≤ 2x interval）
- `late`：严重延迟（lag > 6x interval）
- `unknown`：无法判断下次执行时间
- `disabled`：已禁用

#### F4: Connector Status（数据源连接状态）⭐⭐
**现状：** 无此概念
**目标：** 每个数据源模块标注状态 + 连接 hint

**示例：**
```
订阅额度：🟢 connected（来源：~/.openclaw/subscription.json）
用量数据：🟡 partial（来源：runtime events，部分字段缺失）
历史趋势：🟢 connected
上下文上限：🔴 not_connected（缺少 model-context-catalog.json）
```

#### F5: 自动发现 Agent ⭐⭐
**现状：** 手动配置 agent 列表
**目标：** 启动时自动扫描 `~/.openclaw/sessions/` 目录

**解析规则：** 文件名格式 `agent:<id>:<sessionId>.json`

```python
for path in sessions_dir.iterdir():
    if "agent:" in path.name:
        parts = path.name.split(":")
        agent_id = parts[1]  # 提取 agent ID
```

#### F6: Usage Subscription Status（订阅额度状态）⭐⭐
**现状：** 无此概念
**目标：** 显示当前窗口已用/剩余/重置时间

**多路径兜底：**
```python
SUBSCRIPTION_SNAPSHOT_PATHS = [
    "~/.openclaw/subscription.json",
    "~/.openclaw/billing/subscription.json",
    "~/.openclaw/usage/subscription.json",
    "~/.openclaw/billing/usage.json",
]
```

#### F7: Burn Rate 预算预测 ⭐⭐
**现状：** 无此概念
**目标：** 基于日均消耗预测「还剩多少天会超预算」

```typescript
burnRatePerDay = usedCost30d / daysCovered;
projectedDaysToLimit = (limitCost30d - usedCost30d) / burnRatePerDay;
```

#### F8: Healthz 系统状态 ⭐⭐
**现状：** 系统状态数据编造
**目标：** 量化 snapshot 新鲜度 + monitor lag

**Snapshot 新鲜度：**
- `ok`：age < 12x polling interval
- `warn`：age < 48x polling interval
- `stale`：age >= 48x polling interval

#### F9: Session 类型分类 ⭐
**现状：** 无此概念
**目标：** 从 sessionKey 解析类型，分别统计用量

```typescript
function classifySessionType(sessionKey: string): "Cron" | "Discord" | "Telegram" | "Main" {
  if (key.includes(":cron:")) return "Cron";
  if (key.includes(":discord:")) return "Discord";
  if (key.includes(":telegram:")) return "Telegram";
  return "Main";
}
```

#### F10: Health Summary Strip（全局健康态势一行）⭐⭐
**现状：** 无此概念
**目标：** 顶部一行显示：`🟢 3 healthy  🟡 1 warning  🔴 0 critical`

---

## 四、当前待验证/修复事项（P3）

来自 `个人工作台.md`：

| 事项 | 状态 | 备注 |
|------|------|------|
| Usage 页柱状图渲染 | ❓ | 前端图表库问题，需排查 D3.js |
| 发布日历标记持久化 | ❓ 待验证 | publish-log.json 已实现但未验证 |
| 记忆状态 999天前 bug | ❌ 待处理 | 日期计算明显错误 |
| 系统状态数据编造 | ✅ 已重构 | 替换为真实 healthz API（2026-04-01）|

---

## 四.2 2026-04-01 今日完成

**API 新增：**
- `GET /api/token-plan` — MiniMax Token Plan 真实配额（从 MiniMax API 实时读取）
- `GET /api/connector-status` — 5大数据源连接状态（Cron/Sessions/TokenPlan/历史趋势）
- `GET /api/healthz` — 系统健康状态（Gateway/Cron/Dashboard/Gallery/Library）
- `GET /api/disk-space` — 磁盘空间细分（Gallery/Library/工作空间）

**API 增强：**
- `/api/session-context` — 新增 `session_type` 分类（main/cron/feishu/wechat）
- `/api/cron-reliability` — 新增 `next_run_at` + `due_in_seconds` + `health` 字段
- `/api/burn-rate` — 完全重写，基于 Token Plan 真实配额计算

**前端增强：**
- Overview 页新增：健康状态条（⚫️数据过期 vs 🔴系统异常区分）
- Overview 页新增：💾 磁盘空间细分面板
- Sessions Tab 新增：🧠 上下文占用面板（含类型分布统计）
- Sessions Tab 新增：会话类型彩色标签（主/定时/飞书/微信）
- Cron 面板增强：健康状态标签 + 下次执行时间
- Usage Tab 新增：🔥 Burn Rate Token Plan 配额面板
- 修复：pubCount null 错误导致 loadPublish 崩溃

---

## 四.3 2026-04-02 清理重构完成

**已移除功能：**
- ❌ Burn Rate 模块：与 Overview stat cards 数据重复，已删除
- ❌ 来源分布饼图：从开发以来从未渲染（函数存在但未被调用），已删除
- ❌ 用量页 5 个 stat cards：本期API/日均/窗口配额/Token剩余/会话数，与其他模块重复
- ❌ 预估费用列：改为调用次数口径后无意义
- ❌ 总请求数卡片：与调用次数重复
- ❌ 深入分析模块：功能价值低，已从 Overview 删除
- ❌ Sessions 页重复发布日历：与 Overview 重复，已删除
- ❌ Overview 刷新按钮：无实际意义

**Bug 修复：**
- `/api/sessions` 读取 sessions.json 字段映射错误：`kind` 字段不存在，应从 session key 解析（`agent:main:main` → kind=main，`agent:main:cron:UUID` → kind=cron）
- `/api/sessions` gateway 18789 接口 404：改为直接读取 sessions.json
- Token Plan 字段语义反转：interval_used（API）= 剩余量，interval_remain（API）= 已消耗量（已在所有前端引用处修正）
- Library 磁盘统计路径：`workspace/skills/library` → `workspace/library`（books 在此）

**UI 改进：**
- 用量来源拆分：统计口径从 Token 改为调用次数
- Cron 任务排行：从 Usage 迁移至 Sessions 页，命名从"Token排行"改为"调用次数排行"
- 数据源连接状态：移除 Token Plan 条目（与 Overview 重复）
- 工作数据模块：从 Overview 迁至 Usage 页
- 记忆状态：Overview 从双栏布局改为单栏

**API 变更：**
- `/api/burn-rate` 路由已删除（前端不再使用）

---

## 五、技术方案补充

### 5.1 数据来源（完整版）

```
~/.openclaw/
├── cron/
│   ├── jobs.json                 → job 列表 + schedule
│   └── runs/*.jsonl              → 执行记录（status/delivered/startedAt）
├── sessions/*.json               → session 基本信息 + tokens
├── agents/*/sessions/
│   ├── sessions.json              → session 上下文（contextTokens/totalTokens）
│   └── *.jsonl                   → assistant message usage（token 统计）
├── subscription.json             → 订阅额度（已用/剩余）
└── gateway (18789)                → sessions_list API（实时状态）
```

### 5.2 关键算法参考

**Context Pressure 计算（usage-cost.ts）：**
```typescript
const usedTokens = (status.tokensIn ?? 0) + (status.tokensOut ?? 0);
const contextLimitTokens = runtimeSession?.contextWindowTokens;
const usagePercent = contextLimitTokens > 0
  ? (usedTokens / contextLimitTokens) * 100
  : undefined;
const thresholdState = usagePercent >= 90 ? "critical"
  : usagePercent >= 70 ? "warn" : "ok";
```

**Cron due/late 判断（cron-overview.ts）：**
```typescript
const lagMs = nextRunMs - now.getTime();
let health: CronJobHealth = "scheduled";
if (lagMs <= 0) health = "due";           // 应该在过去
if (lagMs < -5 * 60 * 1000) health = "late";  // 延迟 > 5分钟
```

**Burn Rate 预测（budget-governance.ts）：**
```typescript
const burnRatePerDay = usedCost30d / Math.max(1, daysCovered);
const projectedDaysToLimit = burnRatePerDay > 0
  ? (limitCost30d - usedCost30d) / burnRatePerDay
  : undefined;
```

---

## 六、备份记录

| 日期 | 备份目录 | 说明 |
|------|----------|------|
| 2026-04-01 00:09 | `skills/dashboard.bak_2026-04-01_00-09/` | Dashboard 重构前 |
| 2026-04-01 | `skills/dashboard.bak_2026-04-01_api_logger/` | MiniMax API 日志系统 |
| 2026-04-01 12:43 | `skills/library.bak_2026-03-31_12-43_chunk_queue/` | Chunk 队列系统 |
| 2026-04-01 | `skills/library.bak_2026-03-31_pause_resume/` | 暂停/恢复功能 |
| 2026-04-01 | `skills/library.bak_2026-03-31_explore_history/` | 专题探究历史 |
| 2026-04-02 | `skills/dashboard.bak_2026-04-02_cleanup/` | 清理重构前备份 |

---

_Last updated: 2026-04-02_
