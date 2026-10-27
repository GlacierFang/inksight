# InkSight 自定义模式开发指南 <!-- project: path:/home/open/.openclaw/workspace -->

> 本文是 InkSight **"做自己的内容"**这一层的开发手册。目标是:不改固件、不改后端代码,只写 JSON,就能让 4.2 寸墨水屏显示我们自己的东西。
>
> 结论先行:**固件根本不知道"模式"是什么,它只负责把 BMP 刷到屏上。屏上显示什么,100% 由后端按该 MAC 的配置决定。** 所以这一层是纯数据层。
>
> 全部结论来自代码与运行态验证,不是推测。文中每条"坑"都是实际踩到或实测出来的。

---

## 一、心智模型:为什么不用改固件

### 1.1 固件只发这几个参数

`firmware/network.cpp:623` 构造的请求:

```
GET {cfgServer}/api/render?v={电压}&mac={MAC}&rssi={信号}&refresh_min={间隔}
    &w=400&h=300&bpp=1&colors=2[&next=1]
```

**注意:没有 `persona` 参数。** 固件不知道也不关心"模式"是什么,它只说"给我一张图"。

**⚠️ 2026-10-27 实测更正:这台设备没有"翻页/换下一张"按键。** 固件只发 `GET /api/render?v=&mac=&rssi=&refresh_min=&w=&h=&bpp=&colors=`,**实测真机请求从不带 `next=1`**(nginx 日志 0 次)。设备上只有 RST + BOOT 两个键:BOOT 短按=醒着时无动作/深睡中唤醒 10 分钟窗口,BOOT 长按 2 秒=进配网 Portal。`next_mode` 只是后端 `RenderQuery` 支持的可选参数,**别再拿它当设备交互能力**。

### 1.2 模式是后端按 MAC 配置决定的

```
GET /api/render
  → api/routes/render.py
  → api/shared.py:build_image()
  → api/shared.py:resolve_mode(mac, config, persona_override, force_next)
      ① pending_mode(网页端"立刻切到这个模式")→ 优先
      ② persona_override(仅预览/Widget 用)
      ③ config["modes"] + refreshStrategy → 选出 persona
  → core/mode_registry.py:is_supported(persona, mac) 校验
  → core/json_content.py:generate_json_mode_content()  出内容
  → core/json_renderer.py:render_json_mode()            排版出图
```

`refreshStrategy` 四种(`core/schemas.py:27`):

| 策略 | 行为 |
|---|---|
| `random` | 从 `config.modes` 里随机挑一个(默认) |
| `cycle` | 按 `device_state.cycle_index` 顺序轮播 |
| `time_slot` | 按 `timeSlotRules` 的时段规则挑(如早上天气、晚上诗词) |
| `smart` | 按后端内置的时段偏好挑 |

`next=1` 走 `advance_to_next_mode()`:在 `config.modes` 列表里往后挪一位。

### 1.3 推论

**要加一个我们自己的模式,固件一行都不用改。** 只要:
1. 这个模式 id 进入该 MAC 的 `config.modes`
2. 后端能按这个 id 找到定义并渲染出来

---

## 二、模式定义 JSON 的完整规格

权威 schema 在 `backend/core/modes/schema/mode_schema.json`。以下是实战要点。

### 2.1 顶层字段

```jsonc
{
  "mode_id": "CUSTOM_SONG_LORE",   // 必填, ^[A-Z][A-Z0-9_]{1,31}$
                                 // ⚠️ 必须 CUSTOM_ 或 MY_ 开头,见 §四
  "display_name": "宋人日课",      // 必填, UI 显示名, ≤32 字符
  "icon": "star",                 // 图标名,见 §2.6
  "cacheable": true,              // 是否可缓存复用
  "description": "……",            // ≤200 字符
  "settings_schema": [ … ],       // 可选:该模式专属的 UI 设置项
  "content": { … },               // 必填:内容怎么来
  "layout": { … },                // 必填:怎么排版
  "layout_overrides": {           // 可选:按分辨率覆盖
    "296x128": { … }, "648x480": { … }, "800x480": { … }
  }
}
```

### 2.2 content:内容从哪来

`content.type` 七选一(`core/mode_registry.py:406`):

| type | 说明 | 必填 |
|---|---|---|
| `static` | 写死的内容,零成本零依赖 | `static_data` |
| `llm` | LLM 生成纯文本,`{field}` 分隔 | `prompt_template` + `fallback` |
| `llm_json` | LLM 生成结构化 JSON | `prompt_template` + `output_schema` + `fallback` |
| `external_data` | 外部 API 取数 | `provider` |
| `computed` | 后端内置算法(日期/农历/天气/倒计时等) | `provider` |
| `image_gen` | 图像模型生成 | `provider` |
| `composite` | 多步组合 | `steps` |

**`computed` 内置 provider**(`core/json_content.py`):
`countdown` / `daily_meta`(含农历,依赖 `zhdate`)/ `almanac_api` / `lifebar` / `memo` / `vocab_review` / `habit` / `calendar_grid` / `timetable` / `briefing` / `weather_forecast` / `text2image`

**`external_data` 内置 provider**:
`briefing` / `weather_forecast` / `text2image`

**`fallback` / `fallback_pool` 的真实作用范围(2026-10-23 更正)**:

`core/json_content.py:573` 的 `_get_fallback()`:
```python
pool = content_cfg.get("fallback_pool")
if pool and isinstance(pool, list) and len(pool) > 0:
    return dict(random.choice(pool))
return dict(content_cfg.get("fallback", {}))
```

⚠️ **这个函数只在 LLM 调用失败时才被用到**。LLM 成功时,模型返回什么就渲染什么,**`fallback_pool` 不会参与约束**。

所以:
- ❌ "手写 `fallback_pool`,屏上内容就永远不会出错" —— **错**。LLM 成功时池子形同虚设。
- ✅ **想让内容 100% 可控,别用 LLM**。用 `content.type = "static"` + `fallback_pool`,**且不写 `static_data`**:
  - `static` 分支取 `content_cfg.get("static_data", fallback)`,没有 `static_data` 就落到 `fallback`;
  - `fallback` 又来自 `fallback_pool` 的随机一条;
  - 结果:**每次请求从池中随机取一条,全程不过 LLM**。史实 100% 手写可控,零模型成本,零幻觉,离线也能跑。

这就是「宋人日课」现在的实现(见 §五)。

**另一种用法**:`llm_json` + `fallback_pool` = "模型正常时自由发挥,模型挂了才用兜底"。适合工具型内容,不适合史实型内容。

**服务端自带凭证**:`api/shared.py:395` 附近强制把 `llm_provider` / `llm_model` / `image_provider` / `image_model` 覆盖为后端 `.env` 里的内置值(MiniMax M3 / image-01),用户级 BYOK 已下线。**所以自定义模式走 LLM 不需要我们自己配 Key。**

### 2.3 layout:两种排版引擎

**A. 传统 block 列表**(直接给 `body` 数组):

```jsonc
"layout": {
  "status_bar": { "line_width": 1, "dashed": true },
  "body": [
    { "type": "centered_text", "field": "title", "font": "noto_serif_bold",
      "font_size": 16, "vertical_center": true },
    { "type": "separator", "style": "short", "width": 60 },
    { "type": "text", "field": "note", "font_size": 12, "align": "center",
      "max_lines": 3 }
  ],
  "footer": { "label": "XXX", "attribution_template": "— {author}", "dashed": true }
}
```

可用 block 类型(`core/mode_registry.py` schema `definitions.block`):
`text` / `centered_text` / `separator` / `section` / `list` / `vertical_stack` / `conditional` / `spacer` / `icon_text` / `two_column` / `image` / `progress_bar` / `big_number` / `icon_list` / `key_value` / `group`

**B. 组件树**(`layout_engine: "component_tree"`):

```jsonc
"layout": {
  "layout_engine": "component_tree",
  "status_bar": { "line_width": 1, "dashed": true },
  "body_preset": "poetry_card",
  "preset_props": { "title_field": "title", "title_font_size": 16, … },
  "footer": { "label": "XXX" }
}
```

`body_preset` 可选值(公开的 17 个,`core/layout_presets.py:2378+`):

`plain_text` / `title_with_rule` / `inset_body_text` / `footer_note` / `metric_hero` / `quote_block` / `book_block` / `quote_focus_card` / `zen_focus_card` / `prompt_card` / `word_card` / `letter_card` / `poetry_card` / `riddle_card` / `bias_card` / `story_card` / `countdown_card`

也可混用 `fragments` + `fragment_stack` 组合多个片段。

**实战建议**:优先用现成 preset,改 `preset_props` 调字号/行距/内缩,比自己拼 block 列表快得多,排版也更稳。

### 2.4 layout_overrides:不同分辨率微调

我们的屏是 **400×300**。其他尺寸(296×128 / 648×480 / 800×480)用 `layout_overrides` 单独覆盖:

```jsonc
"layout_overrides": {
  "296x128": { "preset_props": { "lines_font_size": 10, "lines_limit": 8 } }
}
```

### 2.5 settings_schema:给模式加专属设置项

```jsonc
"settings_schema": [
  { "key": "style", "label": "风格", "type": "select",
    "options": [{ "value": "a", "label": "甲" }, { "value": "b", "label": "乙" }],
    "default": "a" },
  { "key": "count", "label": "条数", "type": "number", "min": 1, "max": 5, "default": 3 }
]
```

`type` 可选:`text` / `textarea` / `number` / `select` / `boolean`(还有 `as_json` 让值以 JSON 解析)。

用户填的值最终落到 `config.mode_overrides.<MODE_ID>.mode_settings`,内容生成时通过 `config["mode_settings"]` 读到(`core/json_content.py:923`)。

### 2.6 字体与图标

**字体 key**(`core/layout_presets.py:2487`):
`noto_serif_light` / `noto_serif_regular` / `noto_serif_bold` / `lora_regular` / `lora_bold` / `inter_medium`

**CJK 自动处理**:文本含中日韩字符时,渲染器自动切到 NotoSerifSC 字体(`core/json_renderer.py:419-423` 的 `_pick_cjk_font`)。可用位图字体在 `backend/fonts/bitmap/NotoSerifSC-*.pcf`(含 Light/Regular/Bold/ExtraBold/ExtraLight)。

**图标**(`backend/fonts/icons/*.png`,填不带 `.png` 的文件名):
`app` `art` `body` `book` `breakfast` `calendar` `cloud` `cookie` `dinner` `electric_bolt` `exercise` `flag` `foggy` `food` `global` `habit` `lunch` `meat` `memo` `memo_img` `mic` `mobile` `my_adaptive` `my_quote` `partly_cloudy` `power` `question` `rainy` `rice` `robot` `robot2` `rst` `school` `snow` `soup` `star` `sunny` `swap` `thisday` `thunderstorm` `tips` `vegetable` `vital` `word` `yes` `zen` `zen_img`

### 2.7 校验规则

`core/mode_registry.py:397` `_validate_mode_def_with_error()`:

- `mode_id` 必填非空
- `content` 必须是对象,`type` 必须在七种之内
- `llm` / `llm_json` 必须同时有 `prompt_template` 和 `fallback`
- `layout` 必须是对象
- `component_tree` 引擎编译后 `body` 必须是有 `type` 的对象
- 非 component_tree 引擎,`body` 必须是非空数组

---

## 三、开发流程:从写 JSON 到屏上出图

### 3.1 本地预览(不用硬件、不用认证、秒级)

```bash
curl -s -X POST http://127.0.0.1:18834/api/modes/custom/preview \
  -H 'Content-Type: application/json' \
  -d "$(python3 -c "
import json
md=json.load(open('skills/inksight/modes-ours/song_lore.json'))
print(json.dumps({'mode_def':md,'w':400,'h':300,'colors':2,'responseType':'image'}))
")" -o /tmp/preview.png
```

请求体字段:

| 字段 | 说明 |
|---|---|
| `mode_def` | 完整模式定义(或直接把定义放顶层) |
| `w` / `h` | 画布尺寸,默认 400×300 |
| `colors` | 2=黑白,4=灰度 |
| `responseType` | `image`(默认,返回 PNG)/ `json`(返回内容文本,便于调试) |
| `layout_only` | `true` = 只验排版用占位内容,不调 LLM |

**强烈建议的调试姿势**:先 `responseType=json` 看内容对不对,再 `responseType=image` 看排版。`layout_only=true` 专门用来查溢出/截断。

出图应为 `400×300 / 1-bit grayscale`;转成 BMP 后应为 `PC bitmap, Windows 3.x, 400×300 x 1, 15662 bytes` —— 和真机 `/api/render` 返回的格式一致。

### 3.2 落地到真机:两条路,各有代价

| 路径 | 做法 | 活过重启? | 适用 |
|---|---|---|---|
| **A. builtin 目录** | JSON 放进 `backend/core/modes/builtin/` | ✅ 启动时被扫描 | 全局可见、给所有设备用、我们自己的"官方内容" |
| **B. 数据库** | `POST /api/modes/custom` 带 `mac` | ✅ 存 `custom_modes` 表 | 绑定到具体设备、按用户隔离(WebApp 编辑器走这条) |

**⚠️ 路径 C 是陷阱**:`backend/core/modes/custom/*.json` 这个目录**在服务启动时不会被扫描**(`core/mode_registry.py:462` 的 `_init_registry` 只加载 `BUILTIN_JSON_DIR` 和 `BUILTIN_EN_DIR`)。实测:该目录里的 `mobile_note.json` 冷启动后不在 registry 里。

只有 WebApp "无 mac 的 legacy 路径"会往这个目录写文件 + 注册进内存,**但那只是运行期内存态,重启即失**。别把长期内容放这儿。

路径 A 的启动加载是实测过的:往 `builtin/` 放一个探针文件,冷启动 `get_supported_ids()` 里出现了它。

**路径 A 的副作用**:会出现在所有用户的模式列表里,且 `mode_id` 不能与 builtin 撞名(`load_json_mode` 遇到同名 builtin 会跳过)。

**路径 A 的分类落点**:不在 `core/mode_catalog.py` 的 `BUILTIN_CATALOG` 里 → `catalog.get(mid)` 返回 None → `category` 判定为 `"more"`(`api/routes/modes.py:196`)。所以 WebApp 预览页会把它列在「更多」分组里(`app/preview/page.tsx:242` 按 category 过滤)。

**⚠️ 路径 A 有两份文件,改错不生效**:

| 角色 | 路径 | 作用 |
|---|---|---|
| **源文件**(改这个) | `skills/inksight/modes-ours/<名字>.json` | 我们写和版本管理的地方 |
| **部署副本**(同步过去) | `backend/core/modes/builtin/<名字>.json` | 后端启动时扫描的目录 |

改完源文件**必须**重新复制 + 重启后端,否则线上还是旧的:

```bash
cp skills/inksight/modes-ours/song_lore.json \
   skills/inksight/backend/core/modes/builtin/custom_song_lore.json
systemctl --user restart openclaw-inksight.service
```

(`builtin/` 目录里的文件名不影响 `mode_id`,但约定用小写下划线,如 `custom_song_lore.json`。)

### 3.3 路径 B(数据库)的完整步骤

```bash
# 1) 登录拿会话 token
TOKEN=$(curl -s -X POST http://ruohan.site/ink/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"glacier11@sohu.com","password":"<密码>"}' | jq -r .token)
# 也可以用 Bearer: Authorization: Bearer $TOKEN

# 2) 创建(必须带 mac,且该设备必须已绑定到该用户)
curl -s -X POST http://ruohan.site/ink/api/modes/custom \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "$(python3 -c "
import json
md=json.load(open('skills/inksight/modes-ours/song_lore.json'))
print(json.dumps({**md,'mac':'AA:BB:CC:DD:EE:FF'}))
")"
```

前置条件:`has_active_membership(mac, user_id)` 必须为真(`core/config_store.py:850`)。**设备没绑定 → 403。**

存表结构(`custom_modes`):`mode_id` / `user_id` / `mac` / `definition_json`,唯一键 `(mode_id, user_id, mac)`。

渲染时按设备归属自动加载:`build_image` → `get_device_owner(mac)` → `load_user_custom_modes(owner_user_id, mac)`(`api/shared.py:346-364`)。**同一 mode_id 在不同设备下互不干扰。**

### 3.4 把模式挂到设备的轮播列表

> ⚠️ **不要擅自改设备的轮转列表**（2026-10-26 冰河大人明确）。模式写完、注册完、验证完就交付，
> `configs.modes` 他自己动。下面的命令是给他看的操作说明，不是让我自己去执行。<!-- project: path:/home/open/.openclaw/workspace -->

```bash
curl -s -X POST http://ruohan.site/ink/api/config \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{
    "mac": "AA:BB:CC:DD:EE:FF",
    "modes": ["CUSTOM_SONG_LORE", "CUSTOM_RAILWAY", "POETRY"],
    "refreshStrategy": "cycle",
    "refreshInterval": 60
  }'
```

`modes` 最多 50 个;`refreshInterval` 取值 **10–1440 分钟**,越界会被 `core/schemas.py:55` 拒绝(422)。

保存后端端会自动 `set_pending_refresh(mac, True)`,设备下一次轮询就会拿到新内容。

**`refreshStrategy` 建议用 `cycle`**:我们的内容是"每天一条/每天几屏",随机挑会导致重复感和不可预期。

---

## 四、⚠️ 最大的坑:mode_id 必须以 `CUSTOM_` 或 `MY_` 开头

**这是本项目最贵的一个坑,踩了直接 422。**

`core/schemas.py:109` 的 `validate_modes`:

```python
supported = get_supported_modes()
for mode in v:
    m = mode.upper().strip()
    # 允许 CUSTOM_* / MY_* 透传,避免误判导致 422/500
    if not (m.startswith("CUSTOM_") or m.startswith("MY_") or m in supported):
        raise ValueError(f"不支持的模式: {mode},可选: {supported}")
```

实测:

| mode_id | 存 config 时 |
|---|---|
| `SONG_LORE` | ❌ **422** 不支持的模式 |
| `CUSTOM_SONG_LORE` | ✅ |
| `MY_SONG_LORE` | ✅ |
| `POETRY` | ✅ |

**为什么 `SONG_LORE` 会挂**:`get_supported_modes()` 返回的是 registry 里的 id,而 registry 只在启动时加载 builtin 目录。数据库里的自定义模式是**渲染时才懒加载**的(`load_user_custom_modes`)。

所以时序是:

```
服务重启 → registry 里没有数据库自定义模式
         → 你 POST /api/config 带 modes=["SONG_LORE"]
         → validate_modes 查不到 → 422
```

而 `CUSTOM_` / `MY_` 前缀是后端留的**逃生舱**,无条件放行。

**结论:自定义模式的 `mode_id` 一律用 `CUSTOM_<名字>` 命名。** 这是硬规矩,不是风格偏好。

(注:如果刚通过 API 创建完模式、还没重启就去存 config,可能"碰巧"成功——因为创建时 `load_custom_mode_from_dict` 已经把它塞进 registry 了。别赌这个。)

---

## 五、我们已做的东西

### `CUSTOM_SONG_LORE`「宋人日课」

文件:`skills/inksight/modes-ours/song_lore.json`

每日一条宋代掌故:陵墓石刻、五大名窑、活字印刷、《营造法式》、交子、《清明上河图》、苏轼李清照…

设计要点:

1. **`mode_id` = `CUSTOM_SONG_LORE`**(符合 §四 硬规矩)
2. **史实准确性不交给模型现编**:手写 **27 条**有出处的掌故放进 `content.fallback_pool`,`content.type = "static"` 且**不写 `static_data`** → 每次请求随机取池中一条,**全程不过 LLM**。
   - 屏上内容 **100% 是我们手写的字**,零幻觉、零模型成本、离线可跑。
   - (第一版是 `llm_json`,靠 prompt 引导模型"从池子里挑一条改写"。实测在无设备预览里连刷都是毕昇 —— 见下面「§六 预览缓存与内容轮转」。现已改成 static。)
3. **排版**:`component_tree` + `poetry_card` preset,标题 16 号粗宋体、正文 15 号、行距 9、注释 10 号,页脚虚线 + `attribution_template: "{era}"`。
4. **实测**(2026-10-23 改为 static 后):registry 命中 `source=builtin_json`;进程内连续生成 12 次 → **12 个不同标题,12/12 全部落在手写池内**;公网 preview 连续 4 次 `no_cache=1` → 4 张不同 PNG,`X-Cache-Hit: 0`。

渲染图存 `skills/inksight/modes-ours/preview/`。

**部署状态(2026-10-23)**:已走**路径 A** 部署上线。

- 源文件 `skills/inksight/modes-ours/song_lore.json`
- 部署副本 `backend/core/modes/builtin/custom_song_lore.json`
- 后端已重启,冷启动 registry **31 个模式**(原 30),`CUSTOM_SONG_LORE` 的 `source=builtin_json`
- 公网 `http://ruohan.site/ink/api/modes/catalog` → 命中,`category="more"`,排在「更多」分组第 19 位
- 公网 `http://ruohan.site/ink/api/preview?persona=CUSTOM_SONG_LORE` → **200 / 400×300 PNG**
- **WebApp「无设备体验」页(`/ink/preview`)已可选到它**,在「更多」分组里

> 注:本地 webapp 端口 18835 带 `basePath=/ink`,所以本地要访问 `http://127.0.0.1:18835/ink/preview`,直接打 `/preview` 是 404。

### `CUSTOM_HOME_SIGN`「回家签」

**做什么**:手机到家那一刻,屏上给一张「到家签」——到家时刻、在外时长、当日一句诗,页脚挂次日双城天气。

1. **内容**:`content.type = "computed"`,`provider = "home_sign"`。**不走 LLM**,诗句一字不改地来自已落盘的诗签。
   - 新增文件 `backend/core/bt_presence.py`(我们自己写的,不是上游代码),`json_content.py` 里只加了 3 行派发:
     ```python
     if provider == "home_sign":
         from .bt_presence import build_home_sign_content
         return build_home_sign_content(fallback)
     ```
   - 数据源(全在本地,路径由 `backend/core/bt_presence.py` 往上数 4 级定位 `skills/bt-presence/`,可用环境变量 `INKSIGHT_BT_PRESENCE_DIR` 覆盖):
     - `skills/bt-presence/poetry_signs.json` —— 诗签元数据(诗库选句)
     - `skills/bt-presence/presence_log.json` —— 到家/离家事件(`absent_minutes`、`weather_home`)
   - **配对规则**:诗签 ts 与到家事件 ts 都是「事件发生时刻」,容差 120s 内即视为同一次到家;只认 `event=HOME` 的签。多个候选按到家时间倒序取最近一条。
     - 诗签 ts 语义见 `poetry_sign.py:402`(`'ts': event_dt.timestamp()`),事件 ts 见 `bt_presence.py` 的 `append_presence_log`。
   - **诗句分行**:按中文句号切分并保留句号,与 `poetry_sign.py` 画飞书诗签一致。
   - **天气**:`weather_home` 形如 `郑州: ☁️  +14°C / 淄博: ☁️  +12°C`,解析成 `10-21 郑州 22° / 淄博 13°`。
     - ⚠️ `bt_presence.py` 的 `build_home_message()` 取的是 **day=1(次日)** 天气,所以标签用**日期**而不是「明日」——签可能过几天才被看到,写「明日」会错。
   - **配不到真实到家事件就返回空内容**,不编造、不兜底。
2. **排版**:`fragments` 栈自定义顺序(不用 `poetry_card` preset,因为它固定是「标题→作者→诗句→注释」,放不下「到家时间在前」):
   `metric_hero`(到家日期 + 大字时刻 + 短线)→ `plain_text`(在外时长)→ `book_block`(《诗题》/〔作者〕/ 短线)→ `briefing_repeat_text`(诗句行,`text_field="_value"`)→ 页脚(`label=回家签`,`attribution_template="{note}"` 放天气)。
3. **`cacheable: false`**:内容随到家事件变化,不能进图片缓存(缓存键不含外部内容哈希)。实测 registry `cacheable=False`,不在 `get_cacheable_ids()` 里。

**部署状态(2026-10-23)**:已走**路径 A** 部署上线。

- 源文件 `skills/inksight/modes-ours/home_sign.json`
- 部署副本 `backend/core/modes/builtin/custom_home_sign.json`
- 后端已重启,registry **32 个模式**,`CUSTOM_HOME_SIGN` 的 `source=builtin_json`、catalog 命中 `category="more"`
- 本地 `POST /api/modes/custom/preview`:`responseType=json` 返回真实内容;`responseType=image` 出 400×300 PNG
- 公网 `http://ruohan.site/ink/api/preview?persona=CUSTOM_HOME_SIGN&no_cache=1` → **200 / 400×300 PNG**
- 渲染图存 `skills/inksight/modes-ours/preview/home_sign_400x300.png`

**已清理的脏数据**:`poetry_signs.json` 里那条 `2026-10-23 02:46 HOME《峨眉山月歌》` 是修 `poetry_sign.py` 时手动跑 `generate()` 留下的测试记录,不是真实到家事件 → 已从 JSON 删除(备份 `poetry_signs.json.bak.20261023-104941`),图片移到 `skills/bt-presence/removed/`。删除后 81 条 HOME 签全部能配到真实 home 事件。

**未验证**:真机墨水屏观感、`configs`/`device_memberships` 仍为 0 行,设备端到端未跑。

### `CUSTOM_PI_STATUS`「树莓派状态」

**做什么**:屏上一格报家里这台树莓派的实时状态。五项:CPU 温度、CPU 占用率、内存占用率、SSD 剩余空间、开机时长。

1. **内容**:`content.type = "computed"`,`provider = "pi_status"`。**渲染时直接读本机**,不装 smartctl、不加定时器、不写状态文件、不推飞书。
   - 新增 `backend/core/pi_status.py`,`json_content.py` 里同样只加 3 行派发。
   - 取数:`/sys/class/thermal/thermal_zone0/temp`(毫度→`45°C`)、`os.getloadavg()[0] / os.cpu_count()`(占用率,htop 同算法)、`/proc/meminfo` 的 `MemTotal`/`MemAvailable` 折算、`os.statvfs('/')`、`/proc/uptime`。
   - **读不到的指标直接不出现那一行**,绝不填假值。
   - ⚠️ `/proc/uptime` 是 `"241098.48 704836.00"`,`int()` 会抛 ValueError——要 `float(...split()[0])`。
2. **排版**:`title_with_rule`(标题 + 短线)+ `briefing_repeat_row`(左标签 / 右数值两列),页脚 `label=树莓派状态`、`attribution_template="更新 {updated_at}"`。
3. **`cacheable: false`**:内容随时间变,不能进图片缓存。

**部署状态(2026-10-23)**:已走**路径 A** 部署上线。

- 源文件 `skills/inksight/modes-ours/pi_status.json`
- 部署副本 `backend/core/modes/builtin/custom_pi_status.json`
- 后端已重启,registry **33 个模式**,catalog 命中 `category="more"`
- 本地 preview json 返回五项;image 出 400×300 PNG,OCR 逐行核对通过
- 公网 `http://ruohan.site/ink/api/preview?persona=CUSTOM_PI_STATUS&no_cache=1` → **200 / 400×300 PNG**
- 渲染图存 `skills/inksight/modes-ours/preview/pi_status_400x300.png`

**这台机器的存储实况(2026-10-23 查)**:

| 项 | 实际 |
|---|---|
| 根目录 `/` | **就是外接的 128G USB SSD**(`/dev/sda2`,ext4 117G,已用约 29%) |
| SD 卡 | `mmcblk0` 119G **在位但未挂载**,不承载系统 |
| 内存 | 7.6G,swap 2.0G 曾被占到 1.9G(内存压力长期偏高,未追查) |
| SMART | `smartctl` 未安装,USB 桥不透传 SMART,取不到寿命数据 |

> 推论:SSD 掉线 = 根盘消失 = 机器当场不可用;SD 卡不承载系统,监控它没意义。

**未验证**:真机墨水屏观感(硬件未到)。

---

### `CUSTOM_SUN_MOON`「日昼与月相」

**做什么**:以当日为准,屏上显示郑州的日出、日落与**当日白昼时长**;入夜后换成当晚的月相与**月相图**。配一句当天现写的小文案。

1. **分工**(关键:数值不交给 LLM):
   - 日出/日落/白昼时长 → Open-Meteo daily 接口(`sunrise,sunset,daylight_duration`),`past_days=1` 顺带拿到昨天,好算「比昨天短 N 分钟」。
   - 月相 → 本地天文算法:新月基准 `2000-01-06 18:14 UTC`,朔望月 `29.530588853` 天。
   - 一句话 → LLM(默认 `minimax` / `MiniMax-M3`,凭证读后端 `.env` 的 `MINIMAX_API_KEY`)。
   - **LLM 只写句子,不许写数字**:prompt 里明确要求「不要出现任何数字」,返回后再用正则把数字剔掉;剔完不足 4 字就退回写死的备选句。
2. **新增 `backend/core/sun_moon.py`**,`json_content.py` 里加 3 行派发(`provider == "sun_moon"`),provider 是 `async` 的(要 await LLM)。
3. **月相图**:`component_tree` 不支持 image 块,但 **legacy `layout.body` 支持** → 本模式用 legacy body。
   - 几何:把月球当球体,光源方向 `s = (sin(2πf), 0, -cos(2πf))`,法向 `n` 与 `s` 点积为正即受光,投影后就是真实月相形状(f=0 新月、0.25 上弦、0.5 满月)。
   - 画法:PIL 纯 Python 逐像素,**受光部分留白、阴影部分填黑**(天文图惯例),外圈加 2px 黑轮廓标出月面边缘。满月 = 白盘带黑圈,新月 = 实心黑盘。
   - ⚠️ 别把黑白搞反:第一版图省事把受光面填了黑,结果满月是一颗实心黑盘,和天文图完全相反,被冰河大人一眼看穿。**受亮=白**。
   - 落地:`backend/static/moon/moon_<YYYYMMDD>_<相位序号>_<美术版本>.png`,image 块用 `/static/moon/...` 本地 URL。
     - `MOON_ART_VERSION` 是美术版本号:改画法就加 1,旧文件自动失效重画(否则同名旧图会被直接复用)。
     - ⚠️ **必须用 `/static/` 路径,不能用 `/api/uploads/<id>`**:上传引用会被 `_prefetch_images` 塞进 `_prefetched_<field>` 字节流,`responseType=json` 预览会因 bytes 不可序列化直接 500。`/static/` 路径会被 prefetch 跳过。
   - **画布必须 = 显示尺寸(104×104),不要画大再缩**:渲染器 `fit=contain` 会做 LANCZOS 缩放,缩放产生的灰边在 4 色下会被 `atkinson_palette` 抖成黄/红杂色(月相边缘实测冒出 96 个黄点 + 5 个红点)。画布 104、显示 104 时 PIL 的 `resize` 同尺寸直接 copy,不过滤,边缘无灰 → 4 色下干净。
   - 2 色下 `quantize_image_for_eink` 走 `atkinson_bw`,输出 `"1"` 模式且 `dither=NONE`,`paste_icon_onto` 是直接像素拷贝,极性可靠(0=黑)。
4. **昼夜切换**:legacy 的 `conditional` 块按 content 里的 `period` 字段分支(`day` / `night`)。判定用**郑州本地时间与当天日出日落比较**,不靠小时数硬猜。
5. **缓存**:
   - Open-Meteo 结果缓存 30 分钟(`backend/runtime_cache/sun_moon.json`)。
   - LLM 文案按「日期 + 昼夜」缓存,一天最多调两次(0 次/1 次交替)。
   - 月相图按天+相位命名复用,同文件名不重画;`_prune_moon_images` 顺手删 5 天前的旧图。
6. **`cacheable: false`**:日落后不能继续显示缓存的日间内容。

**踩过的坑**:

- **legacy body 是整体垂直居中**的(`body_align` 默认 `center`):渲染器先在废图上量一遍高度,再把正文整体下移居中。所以正文第一行不在 36px 状态栏下面,而是在中段——这不是 bug,别去调 `y`。
- **`separator` 会压到上一行字的墨迹上**:`text` 块推进 `ctx.y` 时只按墨迹底部算,紧跟其后的 separator 正好画在上一行最后一个墨迹行里,看起来像没画。**在 separator 前插一个 `{"type":"spacer","height_px":6}`** 就干净了。
- **位图字体只覆盖 12–18px**:`_load_bitmap_font` 按 PCF 后缀映射加载字号,请求 20/21px 时找不到合适后缀 → 退回抗锯齿 TTF,墨水屏上发糊。**正文别超过 18px**,主行用 18。
  - 顺带:`_load_bitmap_font` 对无后缀的 `NotoSerifSC-Bold.pcf` 总会报一次 `invalid pixel size` 警告,属正常(排在候选最后),不影响结果。
- 内存里的 `"1"` 模式图 `convert("1")` 会把白(字节 1)压成黑,**做像素分析一律先存 PNG 再读文件**,否则整图全黑。

**部署状态(2026-10-23)**:已走**路径 A** 部署上线。

- 源文件 `skills/inksight/modes-ours/sun_moon.json`
- 部署副本 `backend/core/modes/builtin/custom_sun_moon.json`
- 后端已重启,registry **34 个模式**,catalog 命中 `CUSTOM_SUN_MOON`「日昼与月相」
- 本地 preview json 返回全部字段;日间/夜间两版都出 400×300 PNG,逐行墨迹带核对无重叠无截断
- 公网 `http://ruohan.site/ink/api/preview?persona=CUSTOM_SUN_MOON&no_cache=1` → **200 / 400×300 PNG**
- 渲染图存 `skills/inksight/modes-ours/preview/sun_moon_day_400x300.png`、`sun_moon_night_400x300.png`
- 月相校验图:`preview/moon_30days.png`(连续 30 天)、`preview/moon_phases8.png`(八大相位)。实测受光面积对理论值误差 ≤2%(像素离散所致)
- 当日实测:日出 `06:37`、日落 `17:41`、白昼 `11小时04分`、比昨天短 2 分钟;月龄 11.8 天、照亮 90%、盈凸月、距满月 3.0 天

**未验证**:真机墨水屏观感(硬件未到);夜间分支只做过离线渲染,没在真实夜间时段跑过。

---

### 四色屏(4 色)怎么用(2026-10-23 实测)

冰河大人告知设备是四色屏,于是把颜色这套摸了一遍,结论如下(全部实测,非推测)。

**调色板**(`core/config.py:18`):

| 索引 | 名称 | RGB |
|---|---|---|
| 0 | black | `(0,0,0)` |
| 1 | white | `(255,255,255)` |
| 2 | yellow | `(232,176,0)` 金黄 |
| 3 | red | `(200,0,0)` 正红 |

**颜色数由设备决定,不由模式决定**:render 请求带 `colors=2/3/4`(`api/routes/render.py`),`EINK_COLOR_AVAILABILITY` 里 2 色无命名色、3 色只有 red、4 色才有 red+yellow。所以**模式里写命名颜色是安全的**——黑白屏自动降级成黑字。

**legacy 任意块都能加 `"color": "red"|"yellow"|"black"|"white"`**(`ctx.resolve_color(block)`,`json_renderer.py:215`):text / centered_text / separator / section / key_value / big_number / image 都吃;component_tree 的 node.props 同理。

**对比度实测(决定能写字还是只能做色块)**:

| 组合 | 对比度 | 结论 |
|---|---|---|
| 黑字白底 | 21.0:1 | 正文 |
| **红字白底** | **6.08:1** | **可以写字** |
| **黄字白底** | **1.97:1** | **不能写字** |
| **黑字黄底** | **10.64:1** | **四色屏的杀手锏** |
| 黑字红底 | 3.46:1 | 勉强 |

→ **三条硬规则:① 黄只能做底/线/面,不能写字;② 红可以写字但不当正文(留给"人的痕迹":编号、藏印、评点、警示);③ 黄底黑字最醒目,做标签条。**

**图的色要用"精确调色板"自绘,不能画灰度**:4 色下 `quantize_image_for_eink` 走 `atkinson_palette`,灰度边缘会被抖成黄/红杂色。自绘时直接用调色板里的纯色值即可保住。

**给 LLM 用的颜色机制(天工阁方案)**:让 LLM 在 ASCII 图里用 `*` 标鎏金、`@` 标朱漆彩绘,渲染器映射成黄和红——**颜色由内容决定**,不是贴上去的装饰。

**已修**:月相图原为 200×200 缩到 104×104,LANCZOS 灰边在 4 色下抖出 96 黄点 + 5 红点;改为**画布 = 显示尺寸(104×104)、硬边不插值**后噪点清零(月相区纯白 9269 / 纯黑 1135)。

**设备侧待验**:公网预览默认 `colors=2`,真机要看到四色得设备请求带 `colors=4`,得等硬件。

---

### ⚠️ 渲染器 bug:`two_column` 漏传 `colors`,四色图在两栏里变黑白(2026-10-23 已修)

**症状**:天工阁的器物图存盘是精确四色(白 86.3% / 金黄 10.9% / 朱红 1.6% / 黑 1.3%),
但渲染出的 400×300 里**金黄归零、朱红只剩藏印**;红色「第 1 号」编号也变黑。

**根因**(`core/json_renderer.py` `_render_two_column`):创建左右子 `RenderContext` 时
**没有传 `colors`**,于是落到 dataclass 默认值 `colors: int = 2`。后果有两处:
- `_render_image` 里 `if ctx.colors >= 3` 判断落空 → 走 `ctx.paste_icon()`(1-bit 路径) → 四色图被压成黑白;
- `ctx.resolve_color()` → `EINK_COLOR_AVAILABILITY.get(self.colors)` 在 2 色下是空集 → `color: "red"` 一律回落成黑色。

**同类构造点有 6 个,只有 two_column 这两个漏传**(已全部核对):`json_renderer.py` 的
1096 / 1338 / 1360 / 1371 都传了 `colors=colors`,2265 的 measure ctx 传了 `colors=ctx.colors`,
**2342 / 2349(two_column 的 left_ctx / right_ctx)漏了**。

**修复**:两处各补一行 `colors=ctx.colors`。

**影响面与回归结论**:
- 用 two_column 的模式只有 3 个:`custom_sun_moon`、`custom_tiangong`、`weather`
- `custom_sun_moon`:月相图是 1-bit,**修复前后像素完全一致**(黑 3.05% → 3.05%),无变化
- `weather`:两栏内无彩色文字、无 image 块,**修复前后像素完全一致**
- `custom_tiangong`:金黄 0% → 2.90%,朱红 0.04% → 0.98%,修复生效

**教训**:凡新建 `RenderContext` 的地方,`colors` 必须显式传。`colors` 不只管图片,
还决定**文字能不能用命名色**——漏传等于把四色屏降级成黑白屏。

---

### `CUSTOM_PRINCE`「小王子」(2026-10-26 完成)

一张角色图 + 右侧一句气泡台词。角色图按情绪切换，台词由 LLM 写，跟当天时间/天气/节日节气对齐。

**关键文件**

| 文件 | 作用 |
|---|---|
| `core/prince.py` | provider：挑情绪 → 选图 → 写文案 → 撞句重试 → 兜底句库 |
| `core/modes/builtin/custom_prince.json` | 模式定义（**只有中文**，不做 en/ 版本） |
| `core/json_renderer.py` | 新增 `speech_bubble` 块；`status_bar`/`footer` 支持 `hidden` |
| `core/pipeline.py` / `core/json_content.py` | 把 `weather_code` 透传到 provider |

**角色图**：`backend/runtime_characters/<账号桶>/`，沿用 `album_bucket`（U<user_id>），跟相册同口径。
文件名随意也认，认不出就退回目录里第一张。图片字节走 `_prefetched_image_url`，**不开公开文件路由**。

**情绪表**（优先级从高到低，命中即止）：特别的日子→excited；恶劣天气→grumpy；雨雪→sad；
深夜/凌晨/午后→sleepy；下午 15-17 点→curious；傍晚或晴天→happy；其余→calm。

**防重复**：唯一的历史用途是别把上一句又说一遍。读现成的 `content_history` 上一条，
相似就重试一次，再不行用本地句库兜底。**不新增长期状态、养成、亲密度、连续情绪状态机。**

**版式**（400×300）：角色图 260×260 靠左（y=36，贴状态栏下沿），右侧 266-395 竖条放气泡
（白底黑边圆角框 + 向左尖角指向角色），页脚隐藏。

**两条踩过的坑**

- `MiniMax-M3` 会烧 reasoning token，`max_tokens` 给 200 会返回空正文。要给到 800。
- 设备配置里 `llm_provider`/`image_provider` 存的是历史遗留的 deepseek/aliyun，后端根本没接这两个 key；
  `get_active_config` 读取时会主动清空它们，实际走服务端默认 minimax。2026-10-26 已把 5 行台账改成
  `minimax/MiniMax-M3` + `minimax/image-01`。**别再把 `llmProvider` 字段当成生效配置。**<!-- project: path:/home/open/.openclaw/workspace -->

### `CUSTOM_TIANGONG`「天工阁」(2026-10-23 完成)

每天造一件**并不存在的**宋代器物,配虚构展签 + 四色图录插图。纯 LLM 创意,不查真实名窑名器。

**核心设计:颜色是资源,不是要躲的负担**

冰河大人指出:之前"剥掉颜色词"是自废武功——四色屏本来就有金黄和正红,
正确做法是**告诉模型要什么、基于什么颜色画**。改完实测:LLM 的「图」字段天然就在指定配色
(如「内壁云肩纹填金黄,外壁折枝茶花用朱红」),根本不需要约束。

**文字侧:限制输出字数,而不是限制 token**

之前用 `max_tokens` 去堵 reasoning 燃烧(出现 `tokens=6454, finish=length`、正文为空),
是跟模型的脾气较劲。实测三个模型各跑 2 轮(真实提示词):

| 模型 | 耗时 | tokens | 字段完整 | 展签质量 |
|---|---|---|---|---|
| MiniMax-M3 | 16.9s | 烧穿 | ✗ | 思考内联进 content,正文被挤没 |
| **MiniMax-M3.1-Flash** | 24–44s | 4k–6k | 2/2 | 好(「霁青茶盏 / 盏心刻缠枝菊」) |
| **MiniMax-M2.7** | 58–80s | ~3k | 2/2 | 最好(「流云茶盏 / 淳熙初随商舶传入日本」) |
| MiniMax-Text-01 | 5.8s | ~960 | 2/2 | 弱:「紫砂茶盏」时代错了(紫砂是明清的) |

**结论**:① 提示词里把每个字段字数写死(全文 ≤150 字),模型自己就收尾;
② `max_tokens` 只保证推理有地方烧(给 12000),**不靠它限制正文长度**;
③ `M3` 不适合当默认——它把思考内联在 content 里;
④ 模型链 `MiniMax-M3.1-Flash` → `MiniMax-M2.7`,前者空输出/解析失败就换下一个。

**图像侧:四色盘出图 + 模式侧 snap(不动共享量化代码)**

1. 生图提示词直接写死四色盘:纯白底 / 墨黑 #000000 勾线 / 金黄 #E8B000 填色 / 朱红 #C80000 点缀,平涂色块无渐变。
2. `snap_to_palette()` 在**原分辨率**分类(HSV 规则:低饱和按明暗落黑白,金黄色相落金黄,红色相落朱红,其它色相落黑白),
   再对每张掩膜做「有墨即墨」缩放到 160×200——**先缩放再分类会把细墨线抹平**(实测黑线从 1.3% 掉到 0.0%)。
3. 优先级合并:黑 > 金黄 > 朱红 > 白;墨线不足则膨胀。
4. 存成**精确调色板值**的 PNG → 渲染器 Atkinson 抖动误差为 0 → **无损直通**(与藏印、月相图同一招)。
5. **质量闸门已于 2026-10-26 全部删除**（冰河大人:「我不要门槛！」）。原来那套本地阈值(黑 1.0%–16%、金黄 ≥2.0%、朱红 ≥0.4%)是我自说自话加的,记忆与 git 里都没有用户授权记录。现在 `snap_to_palette()` 只做四色归色 + 补墨,生成了就上屏,只认真出错(读不到图/写不出文件)。常量 `BLACK_MAX`/`GOLD_MIN`/`RED_MIN` 已删;`BLACK_MIN`(0.010)保留,但语义改成**补墨目标**（墨线太淡在墨水屏上看不见,故加粗到该比例),不是否决线。<!-- project: path:/home/open/.openclaw/workspace -->

**验收**:
- 后端 `core/tiangong.py`;模式源 `modes-ours/tiangong.json`,部署副本 `core/modes/builtin/custom_tiangong.json`
- catalog **35 个模式**,命中 `CUSTOM_TIANGONG`「天工阁」
- 当日实测:第 1 号「葵口茶盏」/ 六瓣葵口浅腹圈足 / 底心阴刻绍兴壬午 / 绍兴三十二年绍兴府,淳熙间移置湖州
- 存图四色:白 86.3% 金黄 10.9% 朱红 1.6% 黑 1.3%(零杂色)
- 本地 preview json + PNG 均通过;**公网 `http://ruohan.site/ink/api/modes/custom/preview` → 200 / 400×300 / 金黄 2.90% 朱红 0.98%**
- 渲染图存 `preview/tiangong_4color_fixed.png`、`preview/tiangong_public_400x300.png`
- 回归:`test_json_renderer.py` 等 4 个测试文件修复前后**同为 23 failed / 71 passed**(23 个是预先存在的失败,与本次改动无关)

**踩坑记录**:
- `view_image` 工具一直可用——之前声称"没法看图只能靠像素统计"是错的,白白多走了弯路。**看图就调用它**。
- PIL 的 `img.load()` 返回 `PixelAccess`,`r,g,b = img.load()` 会炸(对象不可迭代);
  `px[x,y]` 在 RGB 图上返回三元组,别再多解一层。
- 公网入口是 **HTTP 80**(`ruohan.site`,natfrp 只代理 80 和 22,没配 HTTPS);用 https 连会 HTTP 000。
  `date.appworlds.cn` 是 OpenClaw 网关域名,所有路径都过网关鉴权,返回「请求安全验证失败(002)」,与后端无关。

**未验证**:真机墨水屏观感(硬件未到)。四色屏下红字编号、藏印、器物图的实际观感待硬件确认。

**排版返工(2026-10-23,冰河大人指出「名称后面多了一溜下划线、行距过小、莫名下划线」)**:

三个现象同一个根因——`separator` 画在文字基线上,且相邻 `text` 块默认 `margin_bottom=0`。

| 现象 | 像素证据 | 根因 |
|---|---|---|
| 名称后一溜下划线 | y=81,x=270-318 一条 48px 实线 | `separator` 紧贴标题基线(间距 1px),从标题末字下方起笔 → 读起来像名字拖着一串下划线 |
| 正文行距过小 | 器形/用途/纹样/款识 墨迹 y=114-146 连成一片 | 相邻 text 块 `margin_bottom=0`,单行块只推进 11px(=字号 12)→ 行间零缝隙 |
| 莫名下划线 | 正文中间另一条 48px 短横 | 同一 `separator` 出现在「款识」和「来历」之间,无语义 |

**改法(只改模式 JSON,不动共享渲染器)**:
- 删掉右栏两条 `separator`,层级靠字号(17 粗体 vs 12)+ `margin_bottom` 建立
- `index_label` +2 / `name` +11 / 器形·用途·纹样 +5 / 款识 +9 / origin +0

**验证**:右栏长横线检测(段长>=25)归零;行间距从 11-13px 变为 5-13px,墨迹不再相接;VL 完整读出全部字段;公网 200 / 400×300 同结果;无图降级分支同样干净。

**教训**:
- **别用 `separator` 当装饰横线**。它画在 `ctx.y` 基线上,紧贴上一行文字底部 → 视觉上变成上一行的下划线。要留白就留白,不要画线。
- **`text` 块默认 `margin_bottom=0`**,连续多个 text 块排版会「零缝隙相接」。每个语义段之间必须显式给 `margin_bottom`。
- 排版问题**用像素证据定位**:按行统计墨迹带、行间距、检测段长>=25 的长横线,比肉眼看图快且准。

---

## 六、预览缓存与内容轮转(踩过的坑)

### 6.1 预览有 5 分钟缓存,键里没有日期

`core/cache.py:194` 附近的预览缓存 **TTL = 5 分钟**,缓存键 = `persona + 尺寸 + 语言 + 城市` 等,**不含日期**。

后果:无设备体验页连着刷,5 分钟内拿到的是**同一张图**(响应头 `X-Cache-Hit: 1`,字节完全相同)。

绕过:请求加 `&no_cache=1`
```
http://ruohan.site/ink/api/preview?persona=CUSTOM_SONG_LORE&ui_language=zh&w=400&h=300&no_cache=1
```
WebApp 的预览页目前**没有**传这个参数 —— 所以在页面上反复点刷新,短时间内看不到变化。这是**缓存行为,不是内容出错**。

### 6.2 无设备预览不会做 LLM 去重

`core/json_content.py:804` 附近,LLM 去重历史只在 `if mac and ctype in ("llm","llm_json") and not DISABLE_DEDUP:` 时才加载。
→ **无设备预览没有 `mac`,模型看不到自己近期输出过什么**,所以会连续选同一个题材(当时表现为连续三屏毕昇/活字印刷)。

### 6.3 结论:想要"每次刷新都不一样"的三种手段

| 手段 | 效果 | 代价 |
|---|---|---|
| `type=static` + `fallback_pool`(当前做法) | 每次随机取池中一条,内容全可控 | 池子小了会重复,池子大了不好维护 |
| 设备端 `refreshInterval` 调到 1440 | 每天只换一次,一屏一整天 | 换得不勤 |
| 预览页加"换一条"按钮(带 `no_cache=1`) | 手动立刻换 | 要改 webapp 并重新构建 |

> ⚠️ `static` + `fallback_pool` 是**随机**轮转,不是"按日期确定性选取"。
> 想做到「同一天永远同一条,零点自动换」,需要一个按日期取索引的机制 —— 目前后端没有现成 provider,要么加一个小 computed provider,要么用第 2 行的 `refreshInterval=1440` 近似。

**2026-10-23 用户拍板**:当前形态(static + 随机池)就挺好。
→ **不加「换一条」按钮**,**不做按日期确定性选取**。预览 5 分钟缓存、随机轮转都按现状留着,以后别再重复提这两个方案。

---

## 七、真机链路速查(供日后核对)

```
设备定时唤醒
  → GET {cfgServer}/api/render?v=&mac=&rssi=&refresh_min=&w=&h=&bpp=&colors=
  → nginx /ink/api/  →  后端 /api/  (重写,实测行为一致)
  → build_image → resolve_mode → 生成内容 → 渲染 → 返回 BMP
  → 设备刷屏
```

- 设备 token:固件开机自签,`POST /api/device/{mac}/token`,后端免鉴权签发,存 `device_state.auth_token`,后续带 `X-Device-Token`。
- 配对:固件本地生成 6 位 `pair_code` → 换 claim token → 网页 `POST /api/claim/consume` 认领。
- 设备按键只有 RST(硬复位)和 BOOT:短按=醒着时无动作/深睡唤醒(给 10 分钟在线窗口),长按 2 秒=进配网 Portal。**没有"换下一张"键**——屏幕翻页只能靠设备定时唤醒(默认 `refresh_min=10`)。
- Portal 里服务器地址填 `http://ruohan.site/ink`(本项目固定 HTTP,不做 HTTPS)。
- 离线:缓存最后一次图,屏显 OFFLINE;Wi-Fi 重连后恢复。

---

## 八、改动纪律

- 改后端代码前:`bash scripts/pre-edit-backup.sh <文件>`
- 改完:`systemctl --user restart openclaw-inksight.service` 和 `systemctl --user restart openclaw-inksight-web.service`
- 后端是 `Type=simple` 跑的 uvicorn,**改文件不会热重载**,必须重启
- 改完只做针对性回归,别跑全量
- 预览限流:`/api/preview` 是 20 次/分钟(IP 维度),并发探测容易被自己打爆;**自定义模式的 `/api/modes/custom/preview` 没有这个限流**,用它做开发迭代

---

## 九、下一步(待硬件到位)

1. 硬件未到 → 固件编译、真机刷写、墨水屏实际观感、真实拉图、账号绑定,**全链路均未端到端验证**
2. 当前库里 `configs` 0 行、`user_devices` 0 行,`device_state` 只有测试 MAC
3. 设备到手后的第一步:**先在网页存一份配置**,否则 `GET /config/{mac}` 返回 404,固件不重试,专注/常亮/轮播全是默认值
4. 再把 `CUSTOM_*` 模式灌进 builtin 目录或数据库,并设 `refreshStrategy=cycle`
5. 本文档提到的"我们自己的内容"目前只做了「宋人日课」一个。**后续要做的新内容,先跟冰河大人沟通再开工**

---

## 十、CUSTOM_TANGPING 躺平巴拉梦境卡(2026-10-23 已上线)

冰河大人拍板的形态:**顶栏 + 全屏图片 + 底栏**(底栏结构照抄画廊 `artwall.json` 的 `footer.label` + `attribution_template`)。

| 项 | 值 |
|---|---|
| mode_id | `CUSTOM_TANGPING`(必须 `CUSTOM_`/`MY_` 开头) |
| 源模式 | `skills/inksight/modes-ours/tangping.json` |
| 部署副本 | `skills/inksight/backend/core/modes/builtin/custom_tangping.json` |
| provider | `backend/core/tangping.py`,在 `core/json_content.py` 的 computed 分支派发 |
| 派发键 | `content.provider = "tangping"` |
| 状态 | `backend/runtime_cache/tangping/state.json` |
| 图目录 | `backend/static/tangping/dream_YYYYMMDD.png`(只留今明两天) |
| 定时 | systemd user timer `openclaw-inksight-tangping.timer`,每天 04:00(CST) |
| 脚本 | `backend/scripts/tangping_daily.py` |

**版式几何(400×300 基准)**:状态栏底线 `int(300*0.12)=36`;footer 高 30 → `footer_top=270`;正文带 **y 36–269,高 234**;图块 `x=0, width=400, height=234, fit=cover`。
`layout_overrides` 已给 `296x128`(图块 322×99)与 `648x480`(图块 400×232)算好尺寸。

**四色 snap 的关键点(踩过的坑)**:
- 四色物理色 = 白 `#FFFFFF` / 黑 `#000000` / 金黄 `#E8B000` / 朱红 `#C80000`,与 `core/config.py` 的 `EINK_4COLOR_PALETTE` 顺序一致(0 黑 1 白 2 金 3 红)。**顺序必须一致**,渲染器是把 P 模式图**原样贴索引**,不重映射。
- snap 出来的 PNG 每个像素都是精确调色板值 → 渲染器 Atkinson 抖动误差为 0,**实测 0 像素差异**,等于无损直通。同尺寸 resize 是 PIL 的 copy(已验)。
- 棕色(170,120,60)最近邻会 snap 成**金黄**而不是黑 → 整幅可能一条墨线都没有 → 被「墨线太少」否掉。已加 `_boost_ink`:先 MaxFilter 加粗已有墨线,仍不够就**只从白底像素**里按亮度取最暗的补墨(**不能从金黄/朱红里补**,否则四色被吃成三色)。
- **质量闸门已于 2026-10-26 全部删除**（冰河大人:「我不要门槛！」）。原来那套本地阈值（覆盖率 6%–92%、黑 2%–62%、金 ≥0.6%、红 ≥0.2%、碎点 ≤4%）是我自说自话加的,记忆与 git 里都没有用户授权的记录。现在 `_snap_to_palette` 只做四色归色 + `_boost_ink` 补墨,生成了就上屏,只认真出错(读不到图/写不出文件)。常量 `COVER_*`/`BLACK_MAX`/`GOLD_MIN`/`RED_MIN`/`NOISE_MAX` 与 `_noise_ratio()` 一并删除,`BLACK_MIN` 保留(它是补墨目标,不是门槛)。
- 当天卡已经生成但被门槛丢掉的情况:图已落盘(判定在存盘**之后**做的),把 state 里 `art_url` 指回 `/static/tangping/dream_YYYYMMDD.png`、`has_image="yes"` 即可找回,不用重新扣费生图。<!-- project: path:/home/open/.openclaw/workspace -->

**降级路径(已逐条实测)**:
| 情况 | 结果 |
|---|---|
| LLM 整体失败 + 无昨日图 | 纯文字卡,用兜底「云上 / 梦里有条不赶时间的河」 |
| LLM 整体失败 + 有昨日图 | 复用昨日图,地点短句也沿用昨日;日期由顶栏体现 |
| 生图失败 | 纯文字卡,保留当天梦题地点短句 |
| ~~质量不过关~~ | 门槛已删,不再有这一条 |

**一天只生成一次**:文件锁 `runtime_cache/tangping/generate.lock` + state 里的 `date` 判定;锁被占用时等对方出结果(最多 180s),不重复扣费。timer 手动跑一次已验证:复用已有卡、`generated_at` 不变、锁不留残留。

**当天卡里没有的东西**:不留历史、不提供回看、不显示「第 N 夜」。`recent` 只留最近 7 条地点短句用于**防重复**,不是可回看的历史。

**真机未验**:墨水屏实际观感(全屏图的对比度、底栏中文在屏上的可读性)仍需真机确认;WebApp PNG 预览与真机显示是两回事。

**未做**:设备端 `configs` 表 0 行,`CUSTOM_TANGPING` 还没进任何设备的模式列表 —— 需要在网页里选进设备模式列表(或设 `refreshStrategy=cycle`)。

---

## 十一、局域网访问的规矩(2026-10-23)

**背景**:冰河大人在家用局域网 IP 访问(`http://192.168.31.193/ink/...`),域名 ruohan.site 在家里绕公网一圈反而不合理 —— 域名是给外面用的。域名访问本身一直正常。

**根因**:两道 Host 白名单都不含内网地址,外部 Host 一律拒:
- webapp `proxy.ts` → `INKSIGHT_ALLOWED_WEB_HOSTS`(webapp/.env.local),不匹配回 **404 "Not Found"**
- 后端 `TrustedHostMiddleware` → `INKSIGHT_ALLOWED_HOSTS`(backend/.env),不匹配回 **400 "Invalid host header"**

**教训(以后照这个来)**:这两张机器的 IP 都是 **DHCP 动态**的(eth0 .193 / wlan0 .197),**绝不能把具体 IP 写进白名单** —— 租约一换就复发。要**按私网段放行**,和 CORS 那边已有的 `INKSIGHT_CORS_ALLOW_LAN=1` 同一个路子。

**现在的做法**:
| 位置 | 开关 | 作用 |
|---|---|---|
| `backend/.env` | `INKSIGHT_ALLOW_LAN_HOSTS=1` | Host 白名单额外放行 RFC1918 + 回环 + 链路本地 |
| `backend/.env` | `INKSIGHT_CORS_ALLOW_LAN=1` | Origin 额外放行内网来源(手机 App 在家直连后端用) |
| `webapp/.env.local` | `INKSIGHT_ALLOWED_WEB_LAN_HOSTS=1` | proxy 额外放行内网 Host |

**后端为什么换掉 TrustedHostMiddleware**:Starlette 那个只支持精确匹配与 `*.` 后缀,写不出 `192.168.x.x` 这种段。已换成自己的 `HostAllowMiddleware`(api/index.py),语义与 Starlette 对齐(不在名单→400;`www.<host>` 命中→307 跳带 www 的形式)。

**webapp 改 proxy.ts 的坑**:`next start` 跑的是**构建产物**,改 `proxy.ts` 必须重新 `npm run build`,光重启服务不生效(env 改动才是运行时生效)。所以改完 proxy.ts 要 build + restart 两步。

**验证过的行为**(curl 带 Host 头):
- 私网段任意 IP(含本机没有的 192.168.31.50 / 10.8.0.7 / 172.20.1.9)→ preview/login/api 全 200
- `ruohan.site` / `127.0.0.1` → 200
- 公网陌生 Host(`zhigu.chat`/`evil.com`)→ webapp 404、后端直连 400、陌生 Origin 403 —— 防护没松
- 注意:`/ink/api/*` 经 nginx 转发时 **Host 被改写**成上游(nginx 默认 `proxy_set_header Host $proxy_host`,该段没设 `$host`),所以后端那道 Host 校验对浏览器路径实际不生效,只对**直连 18834** 生效。

---

## 十二、CUSTOM_ANT 草地铺道蚁(2026-10-23 已上线)

冰河大人拍板:**赛博生命体，纯文字，每天早上掷骰子**。物种锁定**草地铺道蚁 *Tetramorium caespitum***(铺道蚁属模式种、中国分布最广、河南城乡优势种)。

**核心设计原则**:骰子只决定**方向和幅度**,不决定内容;内容由 LLM 自由发挥。骰子是骨架,LLM 才是主角 —— 让骰子抽事件、LLM 去扩写,两头都浪费。

| 项 | 值 |
|---|---|
| mode_id | `CUSTOM_ANT`(图标 `fonts/icons/ant.png`,我画的小蚂蚁,页脚渲染成朱红) |
| 源模式 | `skills/inksight/modes-ours/ant.json` |
| 部署副本 | `backend/core/modes/builtin/custom_ant.json` |
| provider | `backend/core/ant.py`,`content.provider = "ant"` |
| 状态 | `backend/runtime_cache/ant/state.json` |
| 定时 | systemd user timer `openclaw-inksight-ant.timer`,每天 **06:00** CST(躺平巴拉是 04:00) |
| 脚本 | `backend/scripts/ant_daily.py` |

**骰子层(本地、确定性、免费)**:种子 = `sha256("ant-dice|" + 日期)` → 同一天重算结果一致。
- 方向骰 1-6 = 六种活儿:搬运 / 探路 / 放牧 / 搬家 / 蹭食 / 蛰伏
- 幅度骰 1-6 = 投入程度,同时也决定这张卡读起来有多满(1-2 半句碎片,5-6 才铺开)
- **10% 反常骰**:方向 +3 取模 = 今天偏不跟信息素走,改干别的

**状态轴(跨天累积)**:体力 / 饱满 / 熟路 / 同伴。今天的活儿按幅度缩放改变它们,明天的 LLM 拿到的是今天的结果 → 状态有因果。
**行程**由代码算(不交给 LLM):动作基线 × 幅度 × 体力系数;底部显示 `今日行程 X 米 ≈ 你走 Y 米`(蚂蚁走 3 米≈人走 300 米)。

**它不能像人说话**:提示词里给了 26 个禁用词(心情/决定/计划/梦想/孤独/工作/生活/自由/回忆…),**写完再扫一遍**,命中就重写一次,仍不合格退到手写兜底短句(FALLBACK_CARDS,按日期哈希轮换)。这个"禁词表 + 复检"是让它不像 AI 的关键。

**它自己认领身份**:第一天 LLM 写一份"自我认知"(代号/住所/签名/语气/三条怪癖),**永久固定**,之后只改行为不改身份。首日它给自己认领了「四脊蚁」——取自 *Tetramorium* 的字义"四脊",住老槐树根东侧砖缝。

**不生图**:纯文字,一天一次 LLM 调用,不花钱不费墨。

**版式**:顶栏(日期天气电量)+ 标题 + 3-4 句纪事 + 心声(「」右对齐)+ 状态行 + 行程行;底栏 label `ANT` + `{name}·{signature}`。400×300 正文墨迹最低到 223 行(底栏线 270),底栏标签 13-48px、署名 207-382px 不碰;296×128 与 648×480 都实测不溢出。

**只留状态和最近 3 天摘要**给 LLM 当记忆,**没有回看界面**,卡片永远只有今天。

**已知小瑕疵**:LLM 的纪事会从当天动作漂到别的动作(首日"蹭食"那天的第三句写成了探路)。想收紧可在提示词里把当天动作再点一次。

### 2026-10-23 晚：CUSTOM_ANT 第一轮完善

冰河大人说"继续完善"，这轮修的是**真会露馅的地方**，不是加功能：

1. **纪事漂移**（原已知的瑕疵，已修）
   LLM 常把当天动作写成别的动作——"蹭食"那天第三句去写探路。
   现在加**漂移检测**：每个动作有一套强标志词（搬回/举家/蚜虫/探路/甜食/装死…），
   命中别的动作 = 触发重写一次，提示词里点明"你写到 X 上去了，重写，只干 Y"。
   **分强/弱两级**：弱标志（面包屑、糖块、碎屑这类对象词）单个出现不算跑题——
   蚂蚁蹭食路上顺路衔块面包屑是正常的一天；但同一弱词出现两次以上就是重心偏了。
   判例：强=搬运/搬家 一抓就中；顺路带面包屑不误伤。

2. **禁词与漂移分级处置**
   禁词（像人说话）是硬底线，命中就整张作废；
   漂移只是尾巴沾了别的动作，**重写后仍轻微漂移就保留**——不因为一句话把整张好卡扔了。
   只有解析失败才落到手写兜底。

3. **幅度骰真的控制详略**（原设计没落地，已修）
   `_line_spec()` 按幅度给行数和字数：1-2 幅度写 2 行短句，3-4 写 3 行，5-6 写 4 行长句。
   `_style_hint()` 给语气："碰一下就走" / "完全陷进去，几乎忘了时间"。

4. **体力会反过来压住骰子**（跨天因果）
   体力 <25 → 幅度骰压到 2（干不动重活）；体力 >85 → 幅度 +1（睡饱了有劲）。
   方向骰不变，只改投入。骰子原始值记在 `base_magnitude`，能看出改没改。

5. **兜底卡按动作分**（原来 5 张通用卡，可能跟当天活儿无关）
   现在每个动作各 2 张手写卡，模型挂了也保证内容不跑题。

6. **状态语义修正：基准/结果分离**
   原来 `states` 存的是当天结束值，当天重跑一次就把效果**叠加两遍**。
   现在 `states` = 当天起点，`states_end` = 当天结果，跨天时把 end 接到下一天起点。
   实测连续强制生成两次，`states_end` 与 `delta` 完全一致 → 幂等。
   旧状态文件已迁移（起点用当天效果反推，54/73 与首轮日志吻合）。

7. **状态行加箭头**：体力 51↓ · 饱满 82↑ · 熟路 20· · 同伴 5·，看得出身子在变好还是变差。

8. **反常标记并进标题**（不另起一行）
   试过单独加一行，10% 的日子整张卡会整体下移、可能顶到底栏；改成标题前缀
   `今天反常 · 浅尝甜屑`，像素级对比：400x300 只在标题行变化，648x480 位移 1px。

9. **顺手修**：提示词里的英文 "surveys" 混进中文（"在河南一带的城乡 surveys 里"）→ 改成
   "河南一带的城乡蚁类调查"；蹭食 hint 里的"蚜虫的蜜露"会和放牧撞车 → 改成"糖块、甜食残渣"。

**验证**：三档预览全部 200；正文无溢出、状态行未被截断（右边界余量 400 档 13px / 296 档 14px / 648 档 33px）；
幂等已实测；漂移检测用 5 个判例全部符合预期。

**仍未做**：真机墨水屏观感；设备轮播（`configs` 表为空，需在 WebApp 给设备选模式）。

---

## 日痕（CUSTOM_DAYMARK）· 2026-10-24 上线 <!-- project: path:/home/open/.openclaw/workspace -->

一天一行、屏幕是一册慢慢写满的日志。**服务端累积合成**：设备每次收整图，服务端按屏幕尺寸
取最近 N 行重新合成，旧字永远留在服务端（`scripts/rihen_history.py` 可读回），屏幕只留最近几行。

### 结构

| 件 | 位置 |
|---|---|
| provider | `backend/core/rihen.py`（状态 + 生成 + 版式计算） |
| 模式源 | `skills/inksight/modes-ours/rihen.json` → cp 到 `backend/core/modes/builtin/custom_rihen.json` |
| 图标 | `backend/fonts/icons/daymark.png`（64×64 RGBA） |
| 每日脚本 | `backend/scripts/rihen_daily.py`（timer 05:00，早于蚂蚁的 06:00） |
| 历史读取 | `backend/scripts/rihen_history.py`（`--all` 连归档册一起看） |
| 状态 | `backend/runtime_cache/rihen/state.json`：`days[]` + `volume` + `archived[]` |
| timer | `openclaw-inksight-rihen.timer` / `.service`（user unit） |

### 规则

- **一册 = 30 天**（`VOLUME_DAYS`），册满即归档（`archived` 列表）并册号 +1；剩 6 天以内时页面末尾落一方朱红「第 N 册」印。
- **幂等**：按日期写锁 + 状态查重，同一天重复刷新不生成、不重复扣费（实测第二次跑 0.37s，无模型调用）。
- **兜底**：模型失灵 → 30 条手写短句按日期哈希取，当天不空（已实测）。
- 一行最多 **28 字**（不是 30，见下条字体坑）。

### 坑：bitmap 字体引擎会把字号量化

`INKSIGHT_FONT_ENGINE=bitmap`（本机默认），`load_font()` 不是给什么尺寸就给什么尺寸：

| 请求字号 | 实际字号 | 说明 |
|---|---|---|
| ≤ 8 | 原样 | 没有更小的 PCF，走 TTF（1bit 无抗锯齿） |
| 9 / 10 / 11 / 12 | **12** | 全部落到 12px PCF 位图 |
| ≥ 13 | 原样 | 13→13、14→14、16→16、18→16、20→20 |

所以 **CJK 前进宽度 = 实际字号**：12px 时一字 12px，30 字 = 360px。400px 屏留不出
「日期 + 朱红点 + 30 字」，因此取 28 字（336px）。

### 坑：component_tree 的行数必须自己算

`render_json_mode()` 的 component tree 分支**不裁剪**：行高总和超过正文区就直接压到页脚上。
provider 必须复刻渲染器几何算行数（`core/rihen.py` 的 `_geometry()` / `_capacity()`），
且三档 profile 的数字要与模式 JSON 的 `layout` / `layout_overrides` **逐项对齐**。

几何公式：`scale = max(0.92, w/400)`（w≥648 封顶 1.35）；`min_scale = max(0.65, min(scale, h/300))`；
状态栏 `h≤128 → int(h*0.11)+2`，否则 `int(h*0.10 或 0.12)`；页脚 `int(30*min_scale)`（`h≤128` 至少 24，
且顶线 +3）。

三档实测行数：400×300 → 10 行 · 296×128 → 6 行 · 648×480 → 10 行。

### 验证

三档 preview API 与 device pipeline（`generate_and_render`）均 200 出图；28 字整行不折行、
行距均匀（400 档 23px / 296 档 13px / 648 档 36px）；册满归档实测：30 天归档 → 册号 第二册 → 当前册 1 天，
总数 31 = 30 + 1 不丢字。

**仍未做**：真机墨水屏观感；设备轮播（`configs` 表为空，需在 WebApp 给设备选模式）。

---

## 新相册模式 CUSTOM_ALBUM（2026-10-25 上线）

独立于 `MY_ADAPTIVE`（相框）的新模式，**没有**改动相框任何行为。

| 项 | 值 |
|---|---|
| 模式 ID | `CUSTOM_ALBUM`（中英文目录项：相册 / Album） |
| 存储 | `backend/runtime_album/U<user_id>/`（`manifest.json` + 原图）——**按账号一份**，不是按设备 |
| 上限 | 7 张（`core/album.py: MAX_ALBUM_PHOTOS`） |
| 轮播 | `(today - anchor_date).days % len(photos)`，anchor = 第一张上传日 |
| 文案 | 无（只出图） |
| 渲染 | 沿用相框四色 Atkinson 抖动链路，`photo_enhance=True` |

**与相框的语义区别（容易混）**：相框是「刷新一次换一张」，靠设备端游标 `photo_frame_index` 推进；
相册是「每天换一张」，同一天刷新多少次都是同一张，靠日期取模，不占游标。

### 文件清单

- `backend/core/album.py` — 清单/增删/选图/`build_album_content`
- `backend/api/routes/album.py` — 上传/列表/删除/原图（手写 multipart 解析，venv 无 `python-multipart`）
- `backend/core/modes/builtin/custom_album.json` + `builtin/en/custom_album.json` — 模式定义
- `backend/core/mode_catalog.py` — 目录项（category=`custom`）
- `backend/core/json_content.py` — `provider == "album"` 派发
- `webapp/app/config/page.tsx` — 相册弹窗（上传/缩略图/删除/加入轮播）
- `webapp/components/config/mode-selector.tsx` — `deletableCustomModes`：内置目录项不显示删除按钮
- `webapp/app/preview/page.tsx` — 无设备体验页的相册弹窗 + 预览（用登录用户自己的账号相册）

### 账号化收敛（2026-10-25 定稿，遵循「如无必要无增实体」）

最初实现过「设备桶 + 用户桶」双轨（`runtime_album/<MAC>/` 和 `runtime_album/U<id>/`），
后来收敛为**只有账号桶**一种——设备渲染读「这台设备 owner 的账号相册」，
无设备体验页读「当前登录用户的账号相册」，同一个账号就是同一份照片，设备之间也不各存一份。

- 存储目录只剩 `runtime_album/U<user_id>/`，旧的 `<MAC>/` 桶已迁移并删除。
- 设备作用域接口 `/api/album/{mac}/photos` 服务端解析 owner 账号（`api/routes/album.py: _device_account`），
  账号作用域接口 `/api/album/user/photos` 解析当前登录用户（`_self_account`），落到同一份存储。
- **路由顺序有坑**：`/album/user/*` 必须注册在 `/album/{mac}/*` 之前，否则 `"user"` 会被当 MAC 匹配掉直接 400。
- 已删掉 `album_user` 查询参数（双桶时代的遗留）：现在规则统一为
  「有 mac 用设备 owner，没 mac 用登录用户」，在 `api/routes/render.py` 里判断。
- 未登录调 `/api/album/user/photos` → 401；设备没绑账号 → 409 `device_unbound`。

### 备忘

- 4 色预览必须显式 `colors=4`（默认 2 是黑白）。
- 设备 4 色返回的是 raw 2bpp（`0=黑 1=白 2=黄 3=红`），不是 BMP。
- WebApp PNG 预览与设备 2bpp 实测 99.82% 逐像素一致；
  账号化后复测**照片区 100% 逐像素一致**（全图 99.98%，差异全在文字抗锯齿处）。
- WebApp 缩略图走 `<img src>`，带不了 `Authorization` 头——真实登录会同时下发
  `ink_session` cookie，所以缩略图能正常加载；只注入 localStorage token 会 401。
<!-- project: path:/home/open/.openclaw/workspace -->

## 全量代码审计（2026-10-27）

### 头号发现：测试基线一直是假的
- `backend/requirements.txt` 第 30 行**声明了** `pytest-asyncio>=1.2,<2`，但 `skills/inksight/venv` 里**根本没装**。
- 后果：`pytest.ini` 里的 `asyncio_mode = auto` 成了未知配置项，所有 `@pytest.mark.asyncio` 测试全部报
  "async def functions are not natively supported" → **67 failed + 60 errors**，其中只有 7 条是真正的断言失败。
- 之前每次报告"失败数与基线一致"都是拿这堆假失败在比，**基线本身是坏的**。
- 修复：`venv/bin/python -m pip install "pytest-asyncio>=1.2,<2"` → 真实结果 **320 passed / 4 failed**。
- 教训：报测试结果前先确认依赖装齐了。67 failed 这个数字从此作废。

### 4 个真实失败（均非本次改动引入，涉及文件时间戳都早于 10-26）
| 测试 | 症状 | 根因 |
|---|---|---|
| `test_health_endpoint` | `/api/health` 返回 404 | 该路由全仓不存在，测试与实现脱节（上游问题） |
| `test_artwall_title_failure_keeps_image_prompt_fallback` | 期望 `image_url == ""`，实际有真实 OSS URL | `core/content.py:1013` 用 `os.getenv("MINIMAX_API_KEY")` 兜底，环境里有 key 就会真生图。测试假设无 key |
| `test_artwall_color_device_uses_color_prompt` | 同上 | 同上 |
| `test_focus_alert_bmp_renders_and_consumes_alert` | 第二次取 alert-bmp 期望 204，实际 200 | `consume_device_alert()` 只 UPDATE `consumed_at`，SELECT 不过滤已消费行 → 同一告警会重复投递 |

### 自查发现的真问题
- **prince.py `int(kwargs.get("weather_code", -1))`**：`weather_code=None` 时 TypeError。当前调用链
  `weather.get("weather_code", -1)` 恒为 int 所以没炸，但防御写成 `int(...)` 之后才判 None，顺序错了。
- **`/runtime_characters/U5/*.png` 返回 404**：`prince.py` 写的 `image_url` 指向 `/runtime_characters/...`，
  但后端没有这个公开路由（渲染靠 `_prefetched_image_url` 字节所以屏上是好的，只是这个 URL 字段是死的）。
- **`AI_CHAT` / `VOCAB_REVIEW` 不在 `BUILTIN_CATALOG`**：有 builtin json 但没登记。`/api/modes` 仍能列出
  （走 fallback 分支），只是分组落到 "more" 且没有中文文案。
- **`year_progress._resolve_today` 月份反查恒失败**：`date_ctx["month_cn"]` 是「十月」，
  `_ZH_MONTHS` 是「10月」，格式不一致 → 永远退回 `now.month`。跨月/缓存日期下会算错月份。
- **config 表同 MAC 有 5 行**：`save_config` 每次 INSERT 新行再把旧的置 `is_active=0`，
  `get_active_config` 取 `is_active=1 ORDER BY id DESC LIMIT 1`。功能正确，属"保留历史"设计，不是 bug。
- **天工阁图目录无清理**：躺平巴拉有 `_cleanup_art` 只留今明两天，天工阁没有，图会无限积累。
- **`ant.py` 还有一类"质量门"**：禁词表命中 → 重写一次 → 仍不合格就整张作废退回手写兜底。
  性质与躺平巴拉那套不同（是内容风格约束不是画质阈值），但**同属"否决已生成内容"**，需冰河大人定夺。
- **SOLAR_TERMS 只覆盖 2024–2030**：小王子 2031 年起取不到节气，静默退化成"普普通通的一天"。
- **`skills/inksight/backend/` 不在 git 版本控制内**（`.gitignore` 第 4 行，按上游 vendored 树约定）。
  我们的 provider 代码（prince/tangping/tiangong 等）因此没有版本历史，无法 diff、回滚、追责。
  `modes-ours/` 下的模式源文件是纳管的，部署副本不是。

### 未改动的部分（记录在案）
- 躺平巴拉、天工阁质量门已于 2026-10-26 全删，规则改为"生成了就上屏"。
- 2026-10-25 天工阁无图是 Token Plan 用量上限导致的真失败，与门槛无关。
<!-- project: path:/home/open/.openclaw/workspace -->

## 审计修复落地（2026-10-27 20:20）

按审计清单执行，P0 六条全修，P1 按表执行。提交 `8585a77`。

### P0 修复明细

| 文件 | 改了什么 | 修的是什么 |
|---|---|---|
| `core/prince.py` | `raw_weather_code = kwargs.get("weather_code", -1)` → 先判 None 再 int，try 兜住非数字 | 原来 `int(kwargs.get(...))` 在传入 None 时 TypeError；防御写在 int() 之后，顺序错了 |
| `core/year_progress.py` | `_resolve_today()` 月份反查兼容「十月」与「10月」两种格式，同时认 `config.MONTH_CN` | `date_ctx["month_cn"]` 是「十月」而 `_ZH_MONTHS` 是「10月」，反查恒失败 → 永远退回本机当前月份 |
| `core/json_renderer.py` | `_resolve_local_asset()` 增加 `/runtime_characters/` 分支，解析到 `backend/runtime_characters/` | 小王子 `image_url` 写的 `/runtime_characters/U5/*.png` 后端无此路由 → 历史重放取不到角色图 |
| `core/config_store.py` | `consume_device_alert()` SELECT 加 `AND consumed_at = ''`；新增 `mark_device_alert_consumed(mac, sender, message)` | 只 UPDATE 不过滤 → 已消费告警仍会被再次取出 |
| `api/routes/device.py` | `alert-bmp` 走内存路径时同步调 `mark_device_alert_consumed()` | **真根因**：推送时内存+DB 双写，走内存取走只 pop 内存不销 DB 那份副本，第二次请求穿透到 DB 又取到同一条 → 同一条告警反复上屏。用 sender+message 匹配，避免误销后来新推的 |
| `api/routes/health.py`（新） | `/api/health` 返回 `{"status":"ok"}`，不挂鉴权 | 全仓无 health 路由（上游测试期望 200）。探活端点挂鉴权会让监控失效，故不挂 |
| `tests/test_unit_content.py` | 两个 artwall 测试加 `monkeypatch.delenv("MINIMAX_API_KEY")` | `core/content.py:1013` 用 `os.getenv("MINIMAX_API_KEY")` 兜底（服务端内置 key 是设计意图），环境有 key 导致测试真去生图。改测试隔离，不动生产语义 |
| `core/tiangong.py` | 新增 `_cleanup_art(date_key, keep_days=7)`，按文件名日期戳保留最近 7 天，`obj_*.png` 之外的素材不动 | 天工阁图无限堆积。躺平巴拉只留 2 天，天工阁留 7 天更宽松，两者行为同类 |

### P1 决策

- `core/ant.py` 禁词门：**不改**。这是角色一致性约束不是画质阈值，且作废后走 `_fallback_card` 手写兜底，屏上不会空白。
- `AI_CHAT` / `VOCAB_REVIEW` 不在 `BUILTIN_CATALOG`：**不改**。`/api/modes` 已能列出（走 fallback 分支），只是分组文案差一点，属「能跑就别动」。
- `SOLAR_TERMS` 只到 2030：**不补**。2031 年才有影响，现在补要么编数据要么引新表。
- WebApp `INKSIGHT_BACKEND_API_BASE` 默认 8080：**不改**。nginx `location /ink/api/` 已全量接管到 18834，Next 的 `/api` 层在生产根本不会被命中；只有本地 dev 才用到。

### #12 backend 纳入版本控制（已提交 8585a77）

- `.gitignore` 用 `skills/inksight/backend/*` 而非 `backend/` —— 后者会让 git 不进目录，里面的文件无法被 `!` 规则捞回
- 纳管 `backend/core/` + `backend/api/` + `backend/tests/`：156 文件 / 约 2.2M，无大文件
- 仍排除：`.env`（backend 自带 .gitignore 已排除，且文件是 600 权限）、`fonts/`（190M）、`*.db`、`__pycache__/`、`static/`、`runtime_*`、`webapp/`、`venv/`
- `core/modes/builtin/custom_*.json` 不重复纳管：源文件在 `modes-ours/`，部署副本是 cp 产物
- 已扫过纳管树：无 `.env`、无硬编码 key（`sk-*` 模式扫描为空）

### 验证

- 全量测试 **324 passed / 0 failed**（基线 320 passed / 4 failed，4 条全绿）
- 服务 `active`，`/api/health` 返回 `{"status":"ok"}`，`/api/modes` 200，nginx `/ink/api/health` 200
- 月份解析 6 组用例全对（十月/三月/10月/一月/十二月/无 month_cn 兜底）
- grid 一致性：today 格所在列 == 真实日期所在周列，三组全对
- 小王子：`weather_code=None` 不崩（emotion=happy, today_code=-1），`image_url` 能解析成本地文件
- 天工阁清理实测：10-23~10-27 五张图全留，`seal_v1.png` 未被误删
- 告警：`test_focus_alert_bmp_renders_and_consumes_alert` 转绿

### 遗留（未做，等冰河大人定）

- `mode_catalog` 缺 `AI_CHAT` / `VOCAB_REVIEW` 中文文案（功能不缺）
- `SOLAR_TERMS` 2031 年起失效
- `config_store` 仍有 `content.py.bak.20261021-1655` 这类备份文件在树里（已 gitignore）
<!-- project: path:/home/open/.openclaw/workspace -->

### 补充：modes-ours 全部纳入版本控制（提交 c2d9ef4）

审计时顺带发现：`modes-ours/` 是我们自定义模式的源文件真相源，但此前**只有 `song_lore.json` 被跟踪**，
另外 8 个模式源文件（ant / home_sign / pi_status / rihen / sun_moon / tangping / tiangong + preview 图）
全是 untracked——与 #12 同类：我们的原创代码零版本历史。已一并纳管（19 文件 / 约 390K）。

现 InkSight 自有代码的版本控制覆盖：
- `skills/inksight/backend/core/` `api/` `tests/`（原创 provider、路由、测试）
- `skills/inksight/modes-ours/`（9 个模式源文件 + preview 图）

部署副本 `backend/core/modes/builtin/custom_*.json` 仍不跟踪（cp 产物，源在 modes-ours）。
<!-- project: path:/home/open/.openclaw/workspace -->

### 补充：CUSTOM_DONGJING《东京梦华录》模式（2026-10-27 落地）

**形态**：把《东京梦华录》做成「每天走一点点的城」——两轴走读。

- **空间轴**（卷一至卷五）41 站：东都外城 → 旧京城 → 河道 → 大内 → 外诸司 → 御街 → 州桥夜市 → … → 京瓦伎艺 → 娶妇 → 育子
- **时间轴**（卷六至卷十）42 节：正月 → 元旦朝会 → 立春 → 元宵 → … → 十月一日 → 天宁节 → 冬至 → 十二月 → 除夕
- **节奏**：一天一站，按当天日期推进（`index = (today - epoch).days % 83`，epoch=2026-10-27）。
  **零持久化状态**：不用 `device_state.cycle_index`、不建新表、不依赖设备按键（设备只有 RST/BOOT）。
- **一屏**：站名 + 卷·轴·第 N/83 站 + 两轴路线条 + 正文（叙述型=原文摘录，名录型=条目列表）+ 角标「今日 · X」或「距 X 还有 N 天」。

**数据与来源纪律**

- 原文：`library/books/东京梦华录.txt`（本地纯文本，十卷齐全）
- 引文全部由**原文逐字片段**拼成，片段之间用「……」表示省略；**不联网、不调 LLM、不补写原文没有的字句**
- `backend/core/data/build_dongjing_dataset.py` = 构建器（内含人工精选 SPAN 表）；
  `backend/core/data/dongjing.json` = 生成物，入 git，可随时重跑复现
- 构建器对每条引文/条目做**逐字子串校验**，不通过直接报错退出（首轮 8 处不匹配已修）
- 解析小节标题的坑：标题长度 >10 或含数字时（如「三月一日开金明池琼林苑」「十四日车驾幸五岳观」）不能用
  「≤10 字纯汉字」规则，否则会并进上一节；现用「短行且不以句读收尾」，得 83 节

**节令角标**

- 表在 `dongjing.json` 的 `festivals`：农历月日 20 条 + 节气（`config.SOLAR_TERMS`）立春/清明/立秋/立冬/冬至
- 农历换算踩坑：`zhdate.from_datetime` 在**除夕当天直接抛 TypeError**（当天算作下一年的腊月初一 0 日）；
  改为自算农历年（`CNY(L) <= today < CNY(L+1)`），除夕取腊月最后一天（`ZhDate(L,12,29)` 或 `12,30`，谁合法用谁）
- `SOLAR_TERMS` 只到 2030：2031 年起节令角标会自动缺节气条目（与既有认知一致，不补）

**代码落点**

- `backend/core/dongjing.py` = provider（内容 + 路线条 PNG，走 `_prefetched_route_url` 通道）
- `backend/core/json_content.py` 增加 `provider == "dongjing"` 分支
- `backend/core/mode_catalog.py` 增加 `CUSTOM_DONGJING` 条目（名称「汴京梦华」）
- 源文件 `modes-ours/dongjing.json`；部署副本 `backend/core/modes/builtin/custom_dongjing.json`（不跟踪）
- 测试 `backend/tests/test_dongjing.py`：逐字校验、走读推进与回绕、节令今日/倒计时/除夕全年不崩、
  路线条 PNG、兜底、模式 JSON 注册表校验 → **全量 341 passed**（基线 324）

**未做（等冰河大人定）**

- **设备 `configs.modes` 轮转列表没动**（2026-10-26 定的规矩：轮转他自己操作）。模式已在 `/api/modes` 里可选用。
<!-- project: path:/home/open/.openclaw/workspace -->
