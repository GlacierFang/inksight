/**
 * ui_weather.c —— 天气与城市的**显示策略**（纯 C，不依赖 Arduino.h，主机侧可单测）
 *
 * 契约来源: abbot/doc/05 §5.2（天气链路）+ 服务端天气子会话定稿的三条口径：
 *   1. `weather` 空对象 / 缺字段 / 缺 temp —— 全是**同一个空态**，不是故障；
 *   2. 上报城市后 ≤30 秒才生效，这期间也是空态，**不要反复重试**；
 *   3. 服务端不回传城市 → 固件自己落一份到 TF 卡做离线兜底。
 *
 * 铁律:
 *   - 「没有天气」只有一句文案：暂时拿不到天气。不显示 0℃、不显示破折号。
 *   - 离线兜底用 TF 卡快照时**要标明是缓存**，不冒充实时。
 *   - 城市名是**地名**，不是经纬度 —— 上报用英文地名最稳（服务端自己解析）。
 */
#include "ui_weather.h"

#include <stdio.h>
#include <string.h>

/* ===================== 候选城市 ===================== */

/**
 * 候选城市表。
 *
 * `zh` 给用户看（设备是中文界面），`en` 是上报给服务端的值 ——
 * 服务端拿 `en` 做地名解析（Open-Meteo / wttr.in），英文地名解析最稳。
 * 没有这一层就得让用户打拼音，还得自己判对错。
 */
static const ui_city_candidate_t kCities[] = {
    { "北京", "Beijing" },
    { "上海", "Shanghai" },
    { "广州", "Guangzhou" },
    { "深圳", "Shenzhen" },
    { "杭州", "Hangzhou" },
    { "成都", "Chengdu" },
    { "南京", "Nanjing" },
    { "武汉", "Wuhan" },
    { "西安", "Xi'an" },
    { "重庆", "Chongqing" },
    { "天津", "Tianjin" },
    { "苏州", "Suzhou" },
};

#define CITY_COUNT ((int)(sizeof kCities / sizeof kCities[0]))

int ui_weather_city_candidate_count(void)
{
    return CITY_COUNT;
}

const ui_city_candidate_t *ui_weather_city_candidate(int index)
{
    if (index < 0 || index >= CITY_COUNT) {
        return NULL;
    }
    return &kCities[index];
}

const ui_city_candidate_t *ui_weather_city_find_by_wire(const char *wire)
{
    if (wire == NULL || wire[0] == '\0') {
        return NULL;
    }
    int i;
    for (i = 0; i < CITY_COUNT; i++) {
        if (strcmp(kCities[i].en, wire) == 0) {
            return &kCities[i];
        }
    }
    return NULL;
}

/* ===================== 显示 ===================== */

char *ui_weather_display_text(ui_weather_state_t state, const nr_weather_t *live,
                               const nr_weather_t *cached, char *out, size_t out_cap)
{
    if (out == NULL || out_cap == 0) {
        return out;
    }
    out[0] = '\0';

    const nr_weather_t *use = NULL;
    bool mark_cache = false;
    switch (state) {
    case UI_WX_LIVE:
        use = live;
        break;
    case UI_WX_CACHE:
        use = cached;
        mark_cache = true;
        break;
    default:
        /* NONE / EMPTY：没有天气 —— 一句温和提示，不显示 0℃、不显示破折号 */
        snprintf(out, out_cap, "暂时拿不到天气");
        return out;
    }

    if (use == NULL || !use->valid || use->text[0] == '\0') {
        snprintf(out, out_cap, "暂时拿不到天气");
        return out;
    }
    if (mark_cache) {
        snprintf(out, out_cap, "%s %d℃（缓存）", use->text, use->temp);
    } else {
        snprintf(out, out_cap, "%s %d℃", use->text, use->temp);
    }
    return out;
}

/* ===================== 城市名校验 ===================== */

int ui_weather_validate_city(const char *city)
{
    if (city == NULL) {
        return -1;
    }
    size_t n = strlen(city);
    if (n == 0 || n >= UI_WEATHER_CITY_MAX) {
        return -1;
    }
    size_t i;
    for (i = 0; i < n; i++) {
        unsigned char c = (unsigned char)city[i];
        if (c < 0x20 || c == 0x7F) {
            return -1;   /* 控制字符 */
        }
        if (c == '"' || c == '\\' || c == '{' || c == '}' || c == ':' || c == ',') {
            return -1;   /* JSON 结构字符 */
        }
    }
    /* 首尾不能是空格（trim 交给调用方，这里只判定「能不能用」） */
    if (city[0] == ' ' || city[n - 1] == ' ') {
        return -1;
    }
    return 0;
}
