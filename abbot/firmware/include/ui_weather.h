/**
 * ui_weather.h —— 天气与城市的**显示策略**（纯 C，**不依赖 Arduino.h**，主机侧可单测）
 *
 * 分层（照 doc/05 §5.1 的既有约定）：
 *   - **协议形状**在 `net_req.h`：一次天气读数是 `nr_weather_t`，解析是 `nr_parse_weather()`；
 *   - **显示策略**在本文件：怎么显示成一句话、什么时候算空态、城市名能不能用；
 *   - **TF 卡读写**在 `ui_data.h`：城市落盘与离线天气快照。
 *
 * 铁律（doc/05 §5.2 + 服务端天气链路的约定）：
 *   1. 「没有天气」只有一种表达：空态文案「暂时拿不到天气」。
 *      服务端回 `weather:{}`、缺字段、缺 temp —— 一律空态，
 *      **不显示 0℃、不显示破折号、不弹错误框**。
 *   2. 服务端上报城市后 ≤30 秒才生效，这期间也是空态 —— **不是故障，不重试**。
 *   3. 离线（心跳失败）时才用 TF 卡上的本地快照兜底，并且要标明是缓存。
 *   4. 城市名**直接给服务端用**（不是经纬度，服务端自己解析地名）。
 */
#ifndef ABBOT_UI_WEATHER_H
#define ABBOT_UI_WEATHER_H

#include <stdbool.h>
#include <stddef.h>

#include "net_req.h"

#ifdef __cplusplus
extern "C" {
#endif

/* 天气描述（中文短语）上限 */
#define UI_WEATHER_TEXT_MAX 32
/* 城市名上限（字节；服务端按地名解析，中文 UTF-8 一字 3 字节） */
#define UI_WEATHER_CITY_MAX 32

/* ===================== 天气显示状态 ===================== */
typedef enum {
    UI_WX_NONE = 0,  /* 什么都没有 */
    UI_WX_LIVE,      /* 服务端刚给了数据 */
    UI_WX_EMPTY,     /* 服务端回了，但暂时没有天气 */
    UI_WX_CACHE      /* 离线：TF 卡本地快照兜底 */
} ui_weather_state_t;

/* ===================== 候选城市 ===================== */
/**
 * 一个候选城市。
 *
 * `zh` 给用户看（设备是中文界面）；`en` 是**上报给服务端的值** ——
 * 服务端拿它做地名解析（Open-Meteo / wttr.in），英文地名解析最稳，
 * 所以两者分开存，不靠猜。
 */
typedef struct {
    const char *zh;   /* 显示用，如「北京」 */
    const char *en;   /* 上报用，如「Beijing」 */
} ui_city_candidate_t;

/** 候选城市个数。 */
int ui_weather_city_candidate_count(void);
/** 第 index 个候选城市（越界返回 NULL）。 */
const ui_city_candidate_t *ui_weather_city_candidate(int index);
/** 按上报用的英文名找候选（找不到返回 NULL）。 */
const ui_city_candidate_t *ui_weather_city_find_by_wire(const char *wire);

/* ===================== 显示 ===================== */
/**
 * 生成天气显示文案。
 *
 *   - `UI_WX_LIVE`  → 「晴 25℃」（温度来自服务端，描述来自服务端）
 *   - `UI_WX_CACHE` → 「晴 25℃（缓存）」（离线兜底，标明是缓存不是实时）
 *   - 其余（NONE / EMPTY） → 「暂时拿不到天气」
 *
 * `out_cap` 至少 32 字节；返回值始终是 `out`（便于链式调用）。
 */
char *ui_weather_display_text(ui_weather_state_t state, const nr_weather_t *live,
                               const nr_weather_t *cached, char *out, size_t out_cap);

/**
 * 城市名校验（能不能拿去上报）。
 *
 * 通过返回 0；不通过返回负值：
 *   - 空串 / 全空白；
 *   - 超过 UI_WEATHER_CITY_MAX 字节；
 *   - 含控制字符（换行、制表符等）；
 *   - 含 JSON 结构字符（引号、反斜杠、花括号）—— 会让上报明文坏掉。
 */
int ui_weather_validate_city(const char *city);

#ifdef __cplusplus
}
#endif

#endif /* ABBOT_UI_WEATHER_H */
