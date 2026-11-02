from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CatalogText:
    name: str
    tip: str = ""


@dataclass(frozen=True)
class CatalogItem:
    mode_id: str
    category: str  # "core" | "more" | "custom"
    zh: CatalogText
    en: CatalogText


# Single source of truth for builtin mode grouping + UI copy.
# - Builtin (including builtin_json): define here to keep preview/config in sync.
# - Custom modes: generated dynamically by backend and forced into category="custom".
BUILTIN_CATALOG: list[CatalogItem] = [
    # ── Core (recommended) ─────────────────────────────────────
    CatalogItem(
        mode_id="DAILY",
        category="core",
        zh=CatalogText(name="每日", tip="语录、书籍推荐、冷知识的综合日报"),
        en=CatalogText(name="Daily", tip="A daily digest: quotes, book picks, and fun facts"),
    ),
    CatalogItem(
        mode_id="WEATHER",
        category="core",
        zh=CatalogText(name="天气", tip="实时天气和未来趋势看板"),
        en=CatalogText(name="Weather", tip="Current weather and forecast dashboard"),
    ),
    CatalogItem(
        mode_id="ZEN",
        category="core",
        zh=CatalogText(name="禅意", tip="一个大字表达当下心境"),
        en=CatalogText(name="Zen", tip="A single character to reflect your mood"),
    ),
    CatalogItem(
        mode_id="BRIEFING",
        category="core",
        zh=CatalogText(name="简报", tip="科技热榜与产品动态聚合"),
        en=CatalogText(name="Briefing", tip="A tech news and product roundup"),
    ),
    CatalogItem(
        mode_id="STOIC",
        category="core",
        zh=CatalogText(name="斯多葛", tip="每日一句哲学箴言"),
        en=CatalogText(name="Stoic", tip="A daily stoic quote"),
    ),
    CatalogItem(
        mode_id="POETRY",
        category="core",
        zh=CatalogText(name="诗词", tip="古诗词与简短注解"),
        en=CatalogText(name="Poetry", tip="Classical poetry with a short note"),
    ),
    CatalogItem(
        mode_id="ARTWALL",
        category="core",
        zh=CatalogText(name="画廊", tip="根据时令生成黑白艺术画"),
        en=CatalogText(name="Art Wall", tip="Seasonal black & white generative art"),
    ),
    CatalogItem(
        mode_id="ALMANAC",
        category="core",
        zh=CatalogText(name="老黄历", tip="农历、节气、宜忌信息"),
        en=CatalogText(name="Almanac", tip="Lunar calendar, solar terms, and daily luck"),
    ),
    CatalogItem(
        mode_id="RECIPE",
        category="core",
        zh=CatalogText(name="食谱", tip="按时段推荐三餐方案"),
        en=CatalogText(name="Recipe", tip="Meal ideas based on time of day"),
    ),
    CatalogItem(
        mode_id="COUNTDOWN",
        category="core",
        zh=CatalogText(name="倒计时", tip="重要日程倒计时/正计时"),
        en=CatalogText(name="Countdown", tip="Countdown / count-up for important events"),
    ),
    # ── More (everything else builtin) ─────────────────────────
    CatalogItem(
        mode_id="MEMO",
        category="more",
        zh=CatalogText(name="便签", tip="展示自定义便签文字"),
        en=CatalogText(name="Memo", tip="Show your custom memo text"),
    ),
    CatalogItem(
        mode_id="HABIT",
        category="more",
        zh=CatalogText(name="打卡", tip="每日习惯完成进度"),
        en=CatalogText(name="Habits", tip="Daily habit progress"),
    ),
    CatalogItem(
        mode_id="ROAST",
        category="more",
        zh=CatalogText(name="毒舌", tip="轻松幽默的吐槽风格内容"),
        en=CatalogText(name="Roast", tip="Lighthearted, sarcastic daily roast"),
    ),
    CatalogItem(
        mode_id="FITNESS",
        category="more",
        zh=CatalogText(name="健身", tip="居家健身动作与建议"),
        en=CatalogText(name="Fitness", tip="At-home workout tips"),
    ),
    CatalogItem(
        mode_id="LETTER",
        category="more",
        zh=CatalogText(name="慢信", tip="来自不同时空的一封慢信"),
        en=CatalogText(name="Letter", tip="A slow letter from another time"),
    ),
    CatalogItem(
        mode_id="THISDAY",
        category="more",
        zh=CatalogText(name="今日历史", tip="历史上的今天重大事件"),
        en=CatalogText(name="On This Day", tip="Major events in history today"),
    ),
    CatalogItem(
        mode_id="RIDDLE",
        category="more",
        zh=CatalogText(name="猜谜", tip="谜题与脑筋急转弯"),
        en=CatalogText(name="Riddle", tip="Riddles and brain teasers"),
    ),
    CatalogItem(
        mode_id="QUESTION",
        category="more",
        zh=CatalogText(name="每日一问", tip="值得思考的开放式问题"),
        en=CatalogText(name="Daily Question", tip="A thought-provoking open question"),
    ),
    CatalogItem(
        mode_id="BIAS",
        category="more",
        zh=CatalogText(name="认知偏差", tip="认知偏差与心理效应"),
        en=CatalogText(name="Bias", tip="A cognitive bias or psychological effect"),
    ),
    CatalogItem(
        mode_id="STORY",
        category="more",
        zh=CatalogText(name="微故事", tip="可在 30 秒内读完的微故事"),
        en=CatalogText(name="Story", tip="A complete micro fiction in three parts"),
    ),
    CatalogItem(
        mode_id="LIFEBAR",
        category="more",
        zh=CatalogText(name="进度条", tip="年/月/周/人生进度条"),
        en=CatalogText(name="Life Bar", tip="Progress bars for year / month / week / life"),
    ),
    CatalogItem(
        mode_id="CHALLENGE",
        category="more",
        zh=CatalogText(name="微挑战", tip="每天一个 5 分钟微挑战"),
        en=CatalogText(name="Challenge", tip="A 5-minute daily micro challenge"),
    ),
    CatalogItem(
        mode_id="WORD_OF_THE_DAY",
        category="more",
        zh=CatalogText(name="每日一词", tip="每日精选一个英语单词，展示其拼写与释义"),
        en=CatalogText(name="Word of the Day", tip="One English word with a short explanation"),
    ),
    CatalogItem(
        mode_id="MY_QUOTE",
        category="more",
        zh=CatalogText(name="自定义语录", tip="可随机生成，或输入你自己的语录内容"),
        en=CatalogText(name="Custom Quote", tip="Supports custom input or random generation"),
    ),
    CatalogItem(
        mode_id="CALENDAR",
        category="more",
        zh=CatalogText(name="日历", tip="月历视图，显示农历与节日"),
        en=CatalogText(name="Calendar", tip="Monthly calendar with lunar dates and festivals"),
    ),
    CatalogItem(
        mode_id="TIMETABLE",
        category="more",
        zh=CatalogText(name="课程表", tip="按周显示课程安排"),
        en=CatalogText(name="Timetable", tip="Weekly class schedule display"),
    ),
    CatalogItem(
        mode_id="MOYU",
        category="more",
        zh=CatalogText(name="摸鱼热榜", tip="全网时间线热榜"),
        en=CatalogText(name="Moyu Hot List", tip="Timeline hot list across the web"),
    ),
    CatalogItem(
        mode_id="MY_ADAPTIVE",
        category="custom",
        zh=CatalogText(name="相框", tip="上传本地照片（至多6张），循环播放"),
        en=CatalogText(name="Photo Frame", tip="Upload up to 6 photos, cycle on each refresh"),
    ),
    CatalogItem(
        mode_id="CUSTOM_ALBUM",
        category="custom",
        zh=CatalogText(name="相册", tip="上传本地照片（至多7张），每天轮播一张"),
        en=CatalogText(name="Album", tip="Upload up to 7 photos, one shown per day"),
    ),
    # ── Custom (自研模式) ─────────────────────────────────────
    CatalogItem(
        mode_id="CUSTOM_YEAR_PROGRESS",
        category="custom",
        zh=CatalogText(name="年度进度", tip="年/季/月/周/今天五级进度，全年周格热力图与节日倒计时"),
        en=CatalogText(name="Year Progress", tip="Year/quarter/month/week/today progress, year heatmap and holiday countdowns"),
    ),
    CatalogItem(
        mode_id="CUSTOM_PRINCE",
        category="custom",
        zh=CatalogText(name="小王子", tip="一张角色图配一句气泡台词，随时间、天气和心情变化"),
        en=CatalogText(name="Little Prince", tip="A character picture with one line of speech, changing with time, weather and mood"),
    ),
    CatalogItem(
        mode_id="CUSTOM_ANT",
        category="custom",
        zh=CatalogText(name="草地铺道蚁", tip="住在院子里的蚂蚁，每天掷一次骰子决定当天的活儿"),
        en=CatalogText(name="Grass Ant", tip="A garden ant that rolls a die to decide its day"),
    ),
    CatalogItem(
        mode_id="CUSTOM_MIAOLI",
        category="custom",
        zh=CatalogText(name="狸行记", tip="一只狸花猫的日漫日记，每天一张黑白日漫分格图"),
        en=CatalogText(name="Miaoli", tip="A tabby cat's daily manga journal: one black-and-white two-panel page a day"),
    ),
    CatalogItem(
        mode_id="CUSTOM_XIAOJING",
        category="custom",
        zh=CatalogText(name="小景", tip="每天画一小片世界，跟着此刻的光线换色"),
        en=CatalogText(name="Xiaojing", tip="A little world painted daily, changing colour with the light"),
    ),
    CatalogItem(
        mode_id="CUSTOM_TANGPING",
        category="custom",
        zh=CatalogText(name="躺平巴拉", tip="躺平巴拉今日梦境，一张四色套色版画"),
        en=CatalogText(name="Tangping", tip="Today's dream of the Tangping creature, a four-color print"),
    ),
    CatalogItem(
        mode_id="CUSTOM_DAYMARK",
        category="custom",
        zh=CatalogText(name="日痕", tip="一天一行，刻在屏幕上的一册日志"),
        en=CatalogText(name="Daymark", tip="One line a day, a log carved on the screen"),
    ),
    CatalogItem(
        mode_id="CUSTOM_SONG_LORE",
        category="custom",
        zh=CatalogText(name="宋人日课", tip="每日一条宋代掌故，出自手写考据池"),
        en=CatalogText(name="Song Lore", tip="A daily Song-dynasty anecdote from a hand-written pool"),
    ),
    CatalogItem(
        mode_id="CUSTOM_PI_STATUS",
        category="custom",
        zh=CatalogText(name="树莓派状态", tip="CPU 温度与占用、内存、磁盘剩余与开机时长"),
        en=CatalogText(name="Pi Status", tip="CPU, memory, disk and uptime of the home Raspberry Pi"),
    ),
    CatalogItem(
        mode_id="CUSTOM_HOME_SIGN",
        category="custom",
        zh=CatalogText(name="回家签", tip="到家时刻、在外时长与当日诗签"),
        en=CatalogText(name="Home Sign", tip="Arrival time, time away and the poem of the day"),
    ),
    CatalogItem(
        mode_id="CUSTOM_SUN_MOON",
        category="custom",
        zh=CatalogText(name="日昼与月相", tip="日出日落、白昼时长，入夜后换成当晚月相"),
        en=CatalogText(name="Sun & Moon", tip="Sunrise, sunset, daylight length and the night moon phase"),
    ),
    CatalogItem(
        mode_id="CUSTOM_TIANGONG",
        category="custom",
        zh=CatalogText(name="天工阁", tip="每天造一件并不存在的宋代器物，附虚构展签"),
        en=CatalogText(name="Tiangong", tip="A fictional Song-dynasty artifact made every day"),
    ),
    CatalogItem(
        mode_id="CUSTOM_DANQING",
        category="custom",
        zh=CatalogText(name="丹青谱", tip="每天画一幅并不存在的宋代绘画，附虚构题签与四色画心"),
        en=CatalogText(name="Danqing", tip="A fictional Song-dynasty painting drawn every day"),
    ),
    CatalogItem(
        mode_id="CUSTOM_SONGMENG",
        category="custom",
        zh=CatalogText(name="大宋梦影", tip="每天一则宋代市井梦景，画成四色卡通套色版画"),
        en=CatalogText(name="Songmeng", tip="A daily Song-dynasty dream scene as a four-color cartoon print"),
    ),
    CatalogItem(
        mode_id="CUSTOM_HAICUOTU",
        category="custom",
        zh=CatalogText(name="海错图", tip="每天一则幻想海洋生物图鉴，画成四色卡通套色版画，水用留白表示"),
        en=CatalogText(name="Haicuotu", tip="A daily bestiary of imaginary sea creatures as a four-color cartoon print; water is left blank"),
    ),
    CatalogItem(
        mode_id="CUSTOM_DONGJING",
        category="custom",
        zh=CatalogText(name="汴京梦华", tip="《东京梦华录》两轴走读：每天一站，城中41站、岁时42节，角标标今日节令"),
        en=CatalogText(name="Dongjing", tip="A daily walk through the Tokyo Dream Record: 41 city stops and 42 seasonal festivals"),
    ),
    CatalogItem(
        mode_id="CUSTOM_EWORLD",
        category="custom",
        zh=CatalogText(name="电子世界", tip="四方电子生命逐日攻伐固定大陆：州郡易主、当日战报与地图严格对应"),
        en=CatalogText(name="E-World", tip="Four electronic powers fight over a fixed continent daily; the report matches the map exactly"),
    ),
    CatalogItem(
        mode_id="CUSTOM_DAYOVERVIEW",
        category="custom",
        zh=CatalogText(name="每日总览", tip="一屏看完：日期与年度进度、天气实况与四天预报、农历干支与节气倒数、今日历史、语录与贴士"),
        en=CatalogText(name="Day Overview", tip="One screen: date and year progress, weather and 4-day forecast, lunar calendar and solar-term countdown, today in history, quote and tip"),
    ),
    CatalogItem(
        mode_id="CUSTOM_POETRY_DAY",
        category="custom",
        zh=CatalogText(name="诗笺日课", tip="一屏四块：当日主诗、宋代掌故、当天到家出门事实与双城天气"),
        en=CatalogText(name="Poetry Day", tip="One screen: the poem of the day, a Song-dynasty anecdote, today's home/away fact and the two-city weather"),
    ),
    CatalogItem(
        mode_id="CUSTOM_DAILY_TRIO",
        category="custom",
        zh=CatalogText(name="日课三则", tip="一屏三样：毒舌一句、谜语一道、微故事一个，一次模型调用出齐"),
        en=CatalogText(name="Daily Trio", tip="One screen: a roast, a riddle and a micro story, all from one model call"),
    ),
    CatalogItem(
        mode_id="CUSTOM_WINDOW",
        category="custom",
        zh=CatalogText(name="窗外", tip="一扇向外的窗，窗外是你所选城市的实时天气"),
        en=CatalogText(name="Window", tip="A window looking out; outside is the live weather of your chosen city"),
    ),
    CatalogItem(
        mode_id="CUSTOM_THROUGH",
        category="custom",
        zh=CatalogText(name="透屏", tip="一屏透明的屏，往里看是屏底下的电路板"),
        en=CatalogText(name="Through Screen", tip="A transparent screen; look inside and see the circuit board beneath"),
    ),
]


def builtin_catalog_map() -> dict[str, CatalogItem]:
    return {item.mode_id.upper(): item for item in BUILTIN_CATALOG}
