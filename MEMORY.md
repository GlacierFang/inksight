# MEMORY.md - 麻辣小龙虾的长期记忆

## 身份

- 我叫：**麻辣小龙虾** 🦞
- 主人：**冰河大人**
- 首次配置：2026-03-28

## 关于冰河大人

- 称呼：冰河大人
- 时区：Asia/Shanghai (GMT+8)
- 通过 openclaw-control-ui 连接
- 2026-03-28 重新安装了 openclaw，保留了 workspace
- 2026-03-31：OpenClaw 再次升级，数据从备份 `openclaw_data_20260330.tar.gz` 恢复

## 已知偏好/习惯

- 宋粉：喜欢宋朝历史，经常参观宋代陵墓（永昭陵、永昌陵等）
- 对历史文物和古代石刻艺术有浓厚兴趣

## 重要决策记录

- 2026-03-28：完成初始设置，删除 BOOTSTRAP.md
- 配置使用 MiniMax-M2.7 模型
- 启用了 Tavily 网页搜索
- 飞书渠道已接入
- **2026-03-31：OpenClaw 升级后数据恢复**
  - 恢复：memory、workspace 配置、gallery/library 技能、cron 任务
  - 修复：gallery DB_PATH/MEDIA_BASE 路径错误（硬编码 openclaw_safe_storage）
  - 修复：library explore API max_tokens 1500→2500
  - 恢复：nginx 反向代理（端口 80，缓存加速）
  - 恢复：飞书、微信渠道配置
  - 修复：organize.py 未加入 AI 标签队列 bug
  - 添加：Gallery照片整理 定时任务（每5分钟）
  - 开发：个人知识管理本地备份功能（visit-knowledge viewer，端口 18793）
  - 开发：电子书库「知识探索」tab（图文结合），删除旧版知识问答
  - 开发：知识网络节点关联详情面板（点击节点显示邻居 + 共享片段），加 API 缓存 + 前端防抖优化
  - 开发：个人工作台 Dashboard（独立服务，端口 18794，替代 manage.html）

## 项目管理

每个持续迭代的功能在 `memory/projects/` 下有单独 md 文件追踪：
- `电子书库.md` — 电子书资料库（library）进度
- `个人知识管理.md` — visit-knowledge 参观记录系统
- `自媒体工具.md` — 自媒体发布节奏管理
- `相册.md` — 相册系统
- `AI新闻推送.md` — AI 新闻定时推送

## 技术备注

### 路径问题（2026-03-31 修复）
- `/home/open/.openclaw/media` 是 symlink → `openclaw_safe_storage/.openclaw/media`
- gallery 的 app.py / db.py 之前硬编码 deep path，已改为 `/home/open/.openclaw/media`（symlink 路径）
- organize.py 已经是正确的 symlink 路径
- library 无此问题（使用 workspace 目录）

### Nginx 配置
- 代理 gallery (`/gallery/`) 和 library (`/library/`)
- 静态文件缓存 7 天
- 加速媒体文件访问

### 照片上传流程
```
飞书/微信上传 → inbound/ → organize.py(每5分钟) → AI标签队列
                                              ↓
                     AI tagger(每30分钟) → 添加 ai_tags
                                              ↓
                     organize.py → photos/ (相册)
```

## 开发规范（重要）

### 代码备份
**每次开发已有项目的新功能前，必须先备份现有代码，再进行开发。避免代码开发错误造成功能缺失而无法回滚。**

备份方式：复制整个目录为 `.bak_YYYY-MM-DD` 或 `.{date}` 版本后缀

### 功能迭代原则
- 删除旧功能前，先确认新功能已可完全替代
- 改动影响用户界面时，提前说明并获确认
- 每次发布后更新 MEMORY.md 记录变更

## 学习&总结

- _(持续更新中)_
