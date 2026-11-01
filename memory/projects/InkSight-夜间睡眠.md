# InkSight 夜间睡眠设置（2026-11-01）

## 为什么做

墨水屏不是耗电，是耗**刷新次数**。反射式 EPD 没有背光、双稳态，
屏上的字不刷新就一直显着也不耗电；真正磨损面板的是每次全刷闪屏。

设备是 USB 常供电，所以省电没意义 —— 目的是**省面板寿命**。
刷新间隔 20 分钟 → 一晚 6 小时 = 18 次刷新，其中 18 次全是浪费。
一年下来夜间这一项就占 6570 次刷新。

## 拍板结论（冰河大人 2026-11-01）

- 在现有刷新间隔之外，**加一个夜间睡眠设置**，不动原有刷新间隔
- **夜间间隔 = 用户填的分钟数**（不是写死 360）
- **夜间起止时间用户自己定**（默认 0-6 点）
- **不做「立即切换模式」的夜间补偿** —— 告警从来没用过，夜间延迟无所谓

## 实现

### 数据（configs 表，迁移 24/25/26）

```
night_refresh_interval INTEGER DEFAULT 0   -- 夜间刷新间隔(分钟)，0 = 关闭
night_start_hour      INTEGER DEFAULT 0   -- 夜间开始小时(0-23)
night_end_hour        INTEGER DEFAULT 6   -- 夜间结束小时(0-24)
```

`night_refresh_interval = 0` 表示**完全关闭**，不改变任何现有行为。

### 判定（api/shared.py）

`night_refresh_minutes_for_config(config, now=None)`：

- 按配置里的 `timezone` 取本地小时（设备配的是 Asia/Shanghai）
- 支持跨午夜：`start > end` 表示如 23:00 → 06:00
- `start == end` 视为不启用
- 时区非法 → 回落到服务器时间并打 warning，不崩

### 优先级（关键）

`resolve_refresh_minutes_for_device_state()` 原本的优先级是：

```
device_state.expected_refresh_min  (设备自己上报的)  >  config.refresh_interval
```

设备每次 `/render` 都会把自己的 `refresh_min` 上报回来（`render.py:127`），
所以**服务器说的间隔会被设备盖掉**。夜间值必须**压过一切**，否则服务器说了不算。

### 两条下发通道

| 通道 | 位置 | 设备多久拿一次 |
|---|---|---|
| `GET /device/{mac}/state` 响应体 `refresh_minutes` | `api/routes/device.py:114` | **每 5 秒** |
| `GET /render` 响应头 `X-Refresh-Minutes` | `api/routes/render.py:238` | 每次拉图 |

设备是常在线的（nginx 日志：每 5 秒轮询 state），不是深睡轮播。
所以夜间 00:00 一到，**5 秒内**就拿到新间隔。

## 踩坑

**夜间检查必须放在越界钳位之前。**

`_configured_refresh_minutes()` 原本长这样：

```
if refresh_minutes < 10: return 10      # ← 这里就 return 了
if refresh_minutes > 1440: return 1440
night = night_refresh_minutes_for_config(config)
if night > 0: return night              # ← 永远到不了
```

`refresh_interval` 一旦越界（<10 或 >1440），夜间值就永远不生效。
已修（把夜间检查提到钳位之前），有测试覆盖。

## 前端

`webapp/components/config/refresh-strategy-editor.tsx` 的「刷新间隔」下方：

- checkbox「启用夜间睡眠」（勾选默认 360，取消置 0）
- 夜间刷新间隔 (分钟) — 10~1440
- 夜间开始 — 0~23
- 夜间结束 — 0~24

`webapp/app/config/page.tsx` 负责读写与提交。
加载时 camelCase / snake_case 双读（用 `!== undefined` 而非 `||`，
因为起始小时 0 是合法值，`||` 会当成「没返回」跳过）。

## 入库说明

`skills/inksight/webapp/` 和 `skills/inksight/backend/migrations/` 都在
`.gitignore` 忽略列表里（部署副本目录）。本次改动用 `git add -f` 强制纳管，
与之前 `page.tsx` / `gallery/page.tsx` 的处理方式一致。

## 验证

- `tests/test_night_sleep.py` — 7 passed
- 定向回归 36 passed
- 迁移实跑、端到端存配置读回、后端重启 `/ink/api/health` 200

## 当前状态

功能已上线但**默认关闭**（`night_refresh_interval = 0`）。
冰河大人在设备配置页「个性化」里自己勾选并填夜间起止与间隔即可生效。
