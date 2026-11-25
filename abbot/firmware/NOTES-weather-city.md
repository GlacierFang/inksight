# 天气显示 + 设置城市入口 —— 实施笔记（工作文件，非交付文档）

## 服务端契约（已定稿，不改）

- bootstrap 应答：`weather{temp,text}`；**空对象 `{}` = 暂时没有数据**。
- 城市随**心跳**（= 周期性 bootstrap POST）的**加密请求明文**上报：`"city":"Shanghai"`。
- 服务端**不回传城市** → 固件自己把城市落一份到 TF 卡。
- 上报后 ≤30 秒才生效：这期间 `weather` 是 `{}` → 固件显示**空态**。
- 服务端缓存一次只存一个城市：设备城市 ≠ 缓存城市时返回 `{}` → 固件照空态显示。

## 已完成

| 文件 | 改动 |
|---|---|
| `include/net_req.h` / `src/net/net_req.c` | `nr_weather_t`；`nr_parse_weather()`；`nr_json_get_number()`；`nr_build_bootstrap_plain()` 加 `city` 参数 |
| `include/net_api.h` / `src/net/net_api.cpp` | `net_request_t.city[32]`；`NET_OP_CITY`；`net_set_city/net_get_city`；心跳带上城市 |
| `src/net/net_dispatch.c` | `NET_OP_CITY` → `net_set_city(req->city)`；心跳失败造 `{"weather":{},"offline":true}` 空应答 |
| `include/ui_weather.h` / `src/ui/ui_weather.c`（新） | 显示策略、城市名校验、12 个候选城市 |
| `include/ui_data.h` / `src/ui/ui_data.c` | `ui_data_city_get/set`（读-改-写）、`ui_data_weather_cache_get/set`；`ui_data_focus_minutes_set` 改成读-改-写 |
| `include/ui_router.h` | `PAGE_CITY` 枚举 |
| `include/ui_pages.h` / `src/ui/ui_pages.cpp` | 首页天气标签、设置页「所在城市」行、`PAGE_CITY` 候选列表页、`ui_pages_set_weather_text()` / `ui_pages_set_city_label()` / `ui_pages_city_*()` |
| `src/ui/ui_router.cpp` | 周期心跳、bootstrap 应答识别、天气状态机（在线/空态/离线缓存）、城市保存流程 |
| `test/test_ui_weather.c`（新） | 230 项断言 → **ALL PASS** |
| `test/test_net_req.c` | bootstrap 明文带 city 用例 → **118 PASS / 0 FAIL** |
| `test/test_net_dispatch.c` | NET_OP_CITY 路由用例 → **39 PASS / 0 FAIL** |

主机侧测试命令（都已跑通）：
```
gcc -Wall -Wextra -Werror -I include -I src test/test_ui_weather.c \
    src/ui/ui_weather.c src/net/net_req.c src/net/net_client.c \
    -o /tmp/test_ui_weather -lmbedtls -lmbedcrypto        → ALL PASS (230 checks)
gcc -Wall -Wextra -Werror -I include -I src test/test_net_req.c \
    src/net/net_req.c src/net/net_client.c -o /tmp/test_net_req -lmbedtls -lmbedcrypto
    → 118 PASS / 0 FAIL
gcc -Wall -Wextra -Werror -I include -I src test/test_net_dispatch.c \
    src/net/net_dispatch.c -o /tmp/test_net_dispatch       → 39 PASS / 0 FAIL
bash test/host_check_arduino.sh                             → 三段语法检查全过
```

## 待做

1. 检查 ui_router.cpp 里函数定义顺序（前置声明是否够）。
2. **字库扩充（必须）**：现有字库缺「气」「市」「℃」与大部分天气词/城市名
   → 服务端下发的中文天气短语会显示成空白 → 跑 `tools/gen_font.sh` 重生成
   （工具链本机齐备：fonttools 4.62 + NotoSansCJK-Regular.ttc + /tmp/lvtest/lv_font_conv）。
3. `test/test_ui_data.c` 加城市读写 + 天气缓存读写用例（假存储后端已在）。
4. `.github/workflows/build.yml` 加 `test_ui_weather` 步骤。
5. `doc/03*.md` / `doc/05*.md` 如实描述。
6. 提交 → 同步 /tmp/abbot-firmware → 推 → CI → 核 fw-bin sha256。

## 交互宪法对照（自查用）

- 四向导航 + 短按确定 + 长按 600ms 松开退出 —— 只用现成的 `ui_router_handle_gesture`；
- 不加双击、不加后台电脑键盘手势；
- 摇杆输入保留（列表光标 + 短按）；
- UI 不碰 SPI；读卡只经 `hal_storage_*`（ui_data.c 已封装请求/应答）；
- 网络任务不扫 TF 卡（城市靠 UI 侧写卡 + net 层内存缓存经队列投递）；
- 用户可见文案无 HTTP/AES/signature/Token/Python/Traceback 等技术术语。
