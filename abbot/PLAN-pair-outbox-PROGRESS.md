# abbot 本块进度（16:40 自查）

子会话 `agent:main:subagent:aa9aeb27-44a4-4f8b-8a53-cc903f364de3` / run `811c9406` —— **running**。

## 已完成并落库

### 1. 配对简化（自动登记，无码）—— 完���
- 固件 `0f119db`「配对简化 —— 自动登记（无码）+ OOBE 接入进度页」
  - CI run `45572846664` ✅ success（`gh run list` 实测，headSha 匹配）
- 网关 workspace 本地提交 `cfede8a`「网关自动登记端点 + 文档同步（设备侧零输入）」
  - 新端点 `POST /api/v1/device/register`（`server/app/main.py:214`），
    注释写明「设备开机联网后用芯片 MAC 派生的 device_id（esp32s3_<mac8>）自报身份，
    网关按 device_id **幂等**签发 device_secret」
- OOBE 页改成进度态（`ui_pages.cpp:329` 起）：
  - 「正在接入学习助手…」
  - 「[ 重新尝试接入 ]」
  - 文案温和，不出现技术术语

### 2. 离线队列 outbox —— 写完，**还在接线**
- 新增 `include/outbox.h`（8.4KB）、`src/net/outbox.c`（22.5KB）、`test/test_outbox.c`
- 主机侧 **93 checks, 0 failures**（我复跑确认）
- 已覆盖：索引原子写 + `.bak` 自愈、索引损坏回退 `.bak`、关联文件删与读
- `src/net/net_dispatch.c` 已改：拍题成功后 `net_media_cleanup()` 删卡上媒体，
  失败则 `net_outbox_enqueue_solve()` 入队而不是丢掉用户的拍照
- **未完成**：`test/test_net_dispatch.c` 仍 **34 PASS / 1 FAIL**
  （失败项「拍题成功时删掉卡上的媒体文件」，`test_net_dispatch.c:292`）——
  子会话正在修，我复跑时仍是红的。

### 3. 学科卡 cards.enc 解密 —— **未开始**
`src/` 下无 card / study / crypt / aes 相关新文件。

## 本轮已推送提交

| 仓 | 提交 | 状态 |
|---|---|---|
| GlacierFang/abbot-firmware main | `0f119db` 配对简化 | CI ✅ 45572846664 |
| 同上 | `1a8b77b` 本地能力增量 | CI ✅ 45569228140（上轮） |
| workspace 本地 | `cfede8a` 网关自动登记端点 | 已提交 |

## 判读

本块三件事：一件完成并 CI 绿、一件代码写完卡在最后一个测试、一件没动。
剩余工作量约等于「修完 1 个测试 + 接线 + 学科卡解密 + 卡片学习页 + 一轮 CI」。
