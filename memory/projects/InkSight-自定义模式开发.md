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

短按按键时固件追加 `&next=1`,语义是"换下一张"。

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
   - (第一版是 `llm_json`,靠 prompt 引导模型"从池子里挑一条改写"。实测在无设备预览里连刷都是毕昇 —— 见下面「§七 预览缓存与轮转」。现已改成 static。)
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

---

## 七、预览缓存与内容轮转(踩过的坑)

### 7.1 预览有 5 分钟缓存,键里没有日期

`core/cache.py:194` 附近的预览缓存 **TTL = 5 分钟**,缓存键 = `persona + 尺寸 + 语言 + 城市` 等,**不含日期**。

后果:无设备体验页连着刷,5 分钟内拿到的是**同一张图**(响应头 `X-Cache-Hit: 1`,字节完全相同)。

绕过:请求加 `&no_cache=1`
```
http://ruohan.site/ink/api/preview?persona=CUSTOM_SONG_LORE&ui_language=zh&w=400&h=300&no_cache=1
```
WebApp 的预览页目前**没有**传这个参数 —— 所以在页面上反复点刷新,短时间内看不到变化。这是**缓存行为,不是内容出错**。

### 7.2 无设备预览不会做 LLM 去重

`core/json_content.py:804` 附近,LLM 去重历史只在 `if mac and ctype in ("llm","llm_json") and not DISABLE_DEDUP:` 时才加载。
→ **无设备预览没有 `mac`,模型看不到自己近期输出过什么**,所以会连续选同一个题材(当时表现为连续三屏毕昇/活字印刷)。

### 7.3 结论:想要"每次刷新都不一样"的三种手段

| 手段 | 效果 | 代价 |
|---|---|---|
| `type=static` + `fallback_pool`(当前做法) | 每次随机取池中一条,内容全可控 | 池子小了会重复,池子大了不好维护 |
| 设备端 `refreshInterval` 调到 1440 | 每天只换一次,一屏一整天 | 换得不勤 |
| 预览页加"换一条"按钮(带 `no_cache=1`) | 手动立刻换 | 要改 webapp 并重新构建 |

> ⚠️ `static` + `fallback_pool` 是**随机**轮转,不是"按日期确定性选取"。
> 想做到「同一天永远同一条,零点自动换」,需要一个按日期取索引的机制 —— 目前后端没有现成 provider,要么加一个小 computed provider,要么用第 2 行的 `refreshInterval=1440` 近似。

---

## 六、真机链路速查(供日后核对)

```
设备定时唤醒
  → GET {cfgServer}/api/render?v=&mac=&rssi=&refresh_min=&w=&h=&bpp=&colors=
  → nginx /ink/api/  →  后端 /api/  (重写,实测行为一致)
  → build_image → resolve_mode → 生成内容 → 渲染 → 返回 BMP
  → 设备刷屏
```

- 设备 token:固件开机自签,`POST /api/device/{mac}/token`,后端免鉴权签发,存 `device_state.auth_token`,后续带 `X-Device-Token`。
- 配对:固件本地生成 6 位 `pair_code` → 换 claim token → 网页 `POST /api/claim/consume` 认领。
- 短按 = 换下一张(`next=1`);长按 2 秒 = 进配网 Portal。
- Portal 里服务器地址填 `http://ruohan.site/ink`(本项目固定 HTTP,不做 HTTPS)。
- 离线:缓存最后一次图,屏显 OFFLINE;Wi-Fi 重连后恢复。

---

## 七、改动纪律

- 改后端代码前:`bash scripts/pre-edit-backup.sh <文件>`
- 改完:`systemctl --user restart openclaw-inksight.service` 和 `systemctl --user restart openclaw-inksight-web.service`
- 后端是 `Type=simple` 跑的 uvicorn,**改文件不会热重载**,必须重启
- 改完只做针对性回归,别跑全量
- 预览限流:`/api/preview` 是 20 次/分钟(IP 维度),并发探测容易被自己打爆;**自定义模式的 `/api/modes/custom/preview` 没有这个限流**,用它做开发迭代

---

## 八、下一步(待硬件到位)

1. 硬件未到 → 固件编译、真机刷写、墨水屏实际观感、真实拉图、账号绑定,**全链路均未端到端验证**
2. 当前库里 `configs` 0 行、`user_devices` 0 行,`device_state` 只有测试 MAC
3. 设备到手后的第一步:**先在网页存一份配置**,否则 `GET /config/{mac}` 返回 404,固件不重试,专注/常亮/轮播全是默认值
4. 再把 `CUSTOM_*` 模式灌进 builtin 目录或数据库,并设 `refreshStrategy=cycle`
5. 本文档提到的"我们自己的内容"目前只做了「宋人日课」一个;亲子手工、铁路模型等**尚未开工**
