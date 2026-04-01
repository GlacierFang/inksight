---
name: visit-knowledge
description: 个人知识管理系统。当用户上传照片并确认标签后，询问是否加入个人知识管理；积累素材后用户说「开始生成」时，按标签分组整理并写入飞书文档，同时生成本地HTML备份。触发词：「开始生成」「查看待生成列表」「删除待生成照片」「查看本地备份」。
---

# 个人知识管理技能

## 核心流程

1. 用户上传照片 → 进入相册，AI 输出标签
2. 询问：「标签是否正确？需要调整吗？」
3. 用户确认后询问：「要加入个人知识管理吗？」
4. 用户同意 → 记录到待生成队列（pending_queue.json）
5. 用户可继续上传更多照片，都暂存
6. 用户说「开始生成」→「确认要生成哪些标签」→ 搜索背景 → 生成飞书文档 + 本地HTML备份

## 本地备份

生成飞书文档时，会同时保存一份 HTML 格式的本地备份到：
- `/home/open/.openclaw/workspace/memory/visit-docs/`

访问地址：`http://192.168.31.194/visit/`

本地备份包含：
- 标题、标签、参观时间
- 照片缩略图（点击可查看大图）
- 背景资料
- 飞书文档链接（如果有）

## 数据存储

队列文件：`/home/open/.openclaw/workspace/memory/visit-pending.json`

```json
{
  "pending": [
    {
      "id": "uuid",
      "photo_path": "/home/open/.openclaw/media/photos/2026-03-29/xxx.jpg",
      "photo_name": "xxx.jpg",
      "tags": ["龙门石窟", "佛像"],
      "ai_desc": "描述",
      "source": "飞书",
      "added_at": "2026-03-29T12:00:00"
    }
  ],
  "generated": [
    {
      "tag": "龙门石窟",
      "feishu_doc_token": "xxx",
      "feishu_doc_url": "https://feishu.cn/docx/xxx",
      "generated_at": "2026-03-29T12:00:00",
      "photos_data": [
        {
          "id": "uuid",
          "path": "/home/open/.openclaw/media/photos/2026-03-29/xxx.jpg",
          "rel_path": "2026-03-29/xxx.jpg",
          "desc": "照片AI描述内容",
          "source": "飞书",
          "date": "2026-03-29"
        }
      ],
      "background_info": "<p>背景资料完整HTML内容...</p>",
      "visit_time": "2026-03-29"
    }
  ]
}
```

**关键**：每次生成后，必须把完整的 `photos_data` 和 `background_info` 存入 `generated` 记录，不能只存 photo_ids。重建 HTML 时直接使用这些数据。

## 触发词

- **「开始生成」**：触发文档生成流程
- **「查看待生成列表」**：列出所有 pending 照片
- **「删除待生成照片」**：从队列移除指定照片
- **「查看本地备份」**：查看已生成的本地文档列表

## 标签确认流程

用户上传照片后：
1. 读取照片的 ai_tags（从 .meta 文件）
2. 回复：「标签：XXX，是否正确？需要调整吗？」
3. 用户调整 → 更新标签
4. 回复：「要加入个人知识管理吗？」
5. 用户同意 → 读取照片 meta 的 ai_desc，生成 pending 条目

## 生成流程（「开始生成」触发）

1. 读取 pending 队列
2. 询问用户：「请确认要生成的标签」，显示所有涉及标签列表
3. 用户确认后，按标签分组
4. 对每个标签：
   - 从 pending 队列收集该组所有照片的完整信息（id, path, rel_path, desc, source, date）
   - 用 Tavily 搜索该标签的背景资料
   - 生成飞书文档
   - 同时生成 HTML 本地备份（调用 `doc_generator.generate_html_doc()`）
   - **将完整的 `photos_data` 和 `background_info` 存入 `generated` 记录**
5. 更新 generated 记录，标记哪些 pending 已生成
6. 回复用户文档链接（飞书 + 本地）

## 飞书文档生成格式

每个标签生成一篇飞书文档，结构：

```
# {标签} 参观记录

## 基本信息
- 参观时间：{最早照片时间} ~ {最晚照片时间}
- 涉及照片：{N} 张
- 标签：{标签列表}

## 照片记录
{每张照片一段：
- 照片：{文件名}
- 内容：{ai_desc}
- 来源：{飞书/微信}
}

## 背景资料
{根据标签搜索的历史/文化背景}

## 相关知识点
{关联的其他标签或历史人物}
```

## 标签分组逻辑

同一张照片可以有多个标签，生成时：
- 每张照片归入其每个标签对应的组
- 同一标签的所有照片生成一篇文档
- 照片可能出现在多篇文档中（按标签共享）

## 技术文件

| 文件 | 用途 |
|------|------|
| `viewer/app.py` | 本地文档查看 Flask 服务（端口 18793） |
| `viewer/doc_generator.py` | HTML 备份生成器（飞书文档风格） |
| `viewer/rebuild_docs.py` | 从 `generated` 记录重建 HTML 文档的工具 |
| `viewer/templates/index.html` | 文档列表页模板 |

## 注意事项

- 本技能不管理相册，相册功能独立运行
- 每次生成前必须明确用户要生成哪些标签
- **generated 记录必须保存完整 photos_data 和 background_info**，用于后续本地备份重建
- 已生成过的标签再次生成 = 追加新照片到同一文档（读取原文档追加内容）
- 用户说「删除待生成照片」时，从 pending 队列移除指定照片
- 「查看待生成列表」→ 列出所有 pending 照片及标签
- 本地备份不依赖飞书，即使飞书出问题也能查看
