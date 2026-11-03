---
name: InkSight-测试基线
description: InkSight 后端 pytest 的既有失败基线 —— 哪些测试本来就红，与新改动无关
updated: 2026-11-03
---
# InkSight · 测试基线（2026-11-03 实测）
<!-- project: path:/home/open/.openclaw/workspace -->

## 结论

全量 `pytest tests/` 在 HEAD 上是 **9 failed, 589 passed, 32 errors**。**这些失败全是既有问题**，
不是任何新改动引入的。新改动只需看「基线之外的增量」。

## 三类既有失败

### 1. `sqlite3.IntegrityError: UNIQUE constraint failed: schema_versions.version`（32 errors）

- 范围：`test_integration.py` 全量、`test_unit_config_store.py`、`test_vocab_review.py`、`test_unit_db.py`、`test_stats_store.py`、`test_habit.py`。
- 根因：这些测试各自建临时库并跑 `init_db()`，迁移往 `schema_versions` 插同一 version 号，UNIQUE 冲突。
- 单独跑也复现，**不是并发问题**。
- 涉及文件：`core/config_store.py:init_db` → `core/migrations/__init__.py:run_main_db_migrations`。

### 2. `test_billing_quota.py`（7 failed）

- 注册与密码重置相关，与渲染链路无关。

### 3. 画廊/狸行记的模式集合断言（2 failed）

- `test_gallery.py::test_gallery_covers_the_seven_kept_modes`：期望的七个模式集合与实际不符
  （实际多了 `CUSTOM_XIAOJING`/`CUSTOM_WINDOW`/`CUSTOM_THROUGH`/`CUSTOM_MIAOLI`）—— 画廊收录范围变过，测试没跟着改。
- `test_miaoli.py::test_build_content_today_uses_record`：狸行记当天记录断言。

## 验证新改动的方法

不要看全量数字，看**增量**：

```
git worktree add /tmp/ink_base HEAD~N   # 切到改动之前的提交
cd /tmp/ink_base/skills/inksight/backend
/home/open/.openclaw/workspace/skills/inksight/venv/bin/python -m pytest tests/ -q --tb=line > /tmp/base.log 2>&1
```

**坑**：worktree 里没有 `venv/`，要指到主仓的 venv。
**更坑**：worktree 里 `backend/core/modes/builtin/custom_*.json` 是 gitignore 的部署副本，
worktree 里缺失 → 大量 `test_mode_source_and_deploy_match` 假失败。
基线数字必须用主仓目录跑，或先把部署副本拷进 worktree。

## 跑全量的姿势

- 后台跑 + 写日志文件 + 轮询文件，**不要靠 exec 返回值**（长跑会被 session 重建打断，输出丢失）。
- 清理残留进程用 `pgrep` 逐个看 cmdline 再 kill，**别用 `pkill -f pytest`** —— 会连自己一起杀。
