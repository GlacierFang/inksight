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
#
# 2026-11-17 起：系统默认能力（核心模式 / 更多模式）整体下线，只保留自研自定义模式。
# 相框（MY_ADAPTIVE）与相册（CUSTOM_ALBUM）同日下线。
BUILTIN_CATALOG: list[CatalogItem] = [
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
        mode_id="CUSTOM_TANGPING",
        category="custom",
        zh=CatalogText(name="躺平巴拉", tip="躺平巴拉今日梦境，一张四色套色版画"),
        en=CatalogText(name="Tangping", tip="Today's dream of the Tangping creature, a four-color print"),
    ),
    CatalogItem(
        mode_id="CUSTOM_PI_STATUS",
        category="custom",
        zh=CatalogText(name="树莓派状态", tip="CPU 温度与占用、内存、磁盘剩余与开机时长"),
        en=CatalogText(name="Pi Status", tip="CPU, memory, disk and uptime of the home Raspberry Pi"),
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
        zh=CatalogText(name="每日总览", tip="一屏看完：日期与年度进度、天气实况与四天预报、农历干支与节气倒数、语录与贴士"),
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
        mode_id="CUSTOM_SOLAR_TERMS",
        category="custom",
        zh=CatalogText(name="节气倒计时", tip="当年二十四节气逐个倒数：过去的算已过几天，将来的算还有几天"),
        en=CatalogText(name="Solar Terms", tip="Count down to all 24 solar terms of the year, past and upcoming"),
    ),
    CatalogItem(
        mode_id="CUSTOM_WEATHER",
        category="custom",
        zh=CatalogText(name="天气看板", tip="整屏天气图：七段大字温度、实况五行、三天预报、逐小时气温曲线与降水概率柱"),
        en=CatalogText(name="Weather Board", tip="A full-screen weather board: big temperature, live conditions, 3-day forecast, hourly temperature curve and precipitation bars"),
    ),
]


def builtin_catalog_map() -> dict[str, CatalogItem]:
    return {item.mode_id.upper(): item for item in BUILTIN_CATALOG}
