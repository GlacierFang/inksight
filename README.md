# InkSight 后端（自部署分支）

基于上游 [datascale-ai/inksight](https://github.com/datascale-ai/inksight) 的墨水屏内容渲染与下发后端。
本仓库是自部署分支：保留上游渲染框架与 API 结构，新增自研内容 provider 与自研模式。

上游许可证：MIT（本仓库遵循同一许可）

## 目录结构

| 路径 | 说明 |
| --- | --- |
| `backend/core/` | 渲染核心、抖动与调色板、内容 provider、模式注册表 |
| `backend/api/` | FastAPI 路由（render / modes / device / pages 等） |
| `backend/tests/` | 单元测试 |
| `backend/scripts/` | 定时任务入口脚本 |
| `modes-ours/` | **自研模式源文件**（JSON），唯一真源 |
| `backend/core/modes/builtin/custom_*.json` | 由 `modes-ours/` 拷贝出的部署副本，不纳管 |
| `docs/` | 上游文档 |

## 模式机制

模式源文件只声明布局与字段，具体出图由对应 provider 生成整屏 PNG，再由通用 JSON 渲染器落到墨水屏调色板上。

新增自研模式的流程：

1. 在 `modes-ours/` 写模式 JSON（布局 + 字段 + 设置项）。
2. 在 `backend/core/` 实现对应 provider，注册进 `json_content.py` 与 `mode_catalog.py`。
3. 拷贝源文件到 `backend/core/modes/builtin/custom_*.json`。
4. 在设备配置里把模式加入设备模式列表。

## 自研模式

| 模式 ID | 名称 | 说明 |
| --- | --- | --- |
| `CUSTOM_WEATHER` | 天气看板 | 整屏天气图：左上大字温度与五行实况，右上三天预报，下半屏逐小时气温曲线与降水概率柱；逐小时数据，每小时换一次 |
| `CUSTOM_SUN_MOON` | 日昼与月相 | 白天显示日弧与日照，夜间显示月相；缓存按昼夜分桶 |

## 运行

```bash
pip install -r backend/requirements.txt
uvicorn main:app --host 0.0.0.0 --port 18834 --app-dir backend
```

设备侧通过 `/api/render` 拉取当前模式整屏图，`/api/modes` 返回可用模式列表。
