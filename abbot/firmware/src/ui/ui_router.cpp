/*
 * ui_router.cpp —— 页面路由 + 浮层优先级 + 六手势宪法落地
 *
 * 契约来源: abbot/doc/03 §三 浮层优先级 / §3.2 六手势宪法 / §五 13 页状态。
 *
 * 铁律:
 *   - 长按 600ms 的判定只由 HAL 做, 本文件只消费 HAL 交来的 GESTURE_LONG。
 *   - 语音一律从页面操作菜单进入, 长按全局唯一含义是"退出"。
 *   - 阅读模块内全局屏蔽录音浮层 (doc/07)。
 *   - 提醒弹窗短按关闭并恢复原页, 长按忽略。
 */
#include "ui_router.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "lvgl.h"
#include "net_api.h"
#include "net_req.h"
#include "ui_data.h"
#include "ui_pages.h"
#include "ui_ota_page.h"
#include "ui_weather.h"

#include "hal_interfaces.h"   /* 背光、音频（Arduino.h 也从这里进来） */

static ui_page_t s_current = PAGE_OOBE;
static ui_book_page_t s_current_book = PAGE_BOOKSHELF;
static bool s_in_book = false;

/* 浮层可见状态, 索引即优先级(高->低) */
static bool s_overlay_visible[OVERLAY_COUNT] = {false, false, false};

/* 本题会话号计数器（doc/05 §5.1：每题唯一，同题追问沿用同一个） */
static uint32_t s_session_seq = 0;

/* 最近一次云端应答（解密后的业务明文）—— 由网络任务经队列投递过来 */
static char *s_last_reply = NULL;
static uint32_t s_last_reply_len = 0;
static int32_t s_last_biz = -1;

/* 提醒弹窗打断前的页面, 供短按关闭后精准恢复 */
static ui_page_t s_reminder_restore_page = PAGE_HOME;
static bool s_reminder_had_page = false;

/* 是否已配对（读 /config/profile.json）：未配对时端云功能给温和提示，
 * 本地页（番茄钟/设置/书架/课表）照常可用。 */
static int s_paired = 0;

/* 设备接入状态（自动登记，doc/04 §1.2）：由网络任务经队列投来。 */
static int s_pair_state = UI_PAIR_STATE_NONE;

/* 已载入的学科卡片集（网络任务解好的明文，堆内存 —— 所有权在本层）。 */
static card_pack_t *s_cards = NULL;

/* ===================== 天气与城市（doc/05 §5.2） ===================== */

/* 心跳节奏：开机先来一次（拿到天气），之后每 10 分钟一次。 */
#define UI_HB_FIRST_DELAY_MS   15000u
#define UI_HB_INTERVAL_MS      600000u
/* 网络队列满时的重试间隔 */
#define UI_HB_RETRY_MS           5000u
/* 设完城市后的追一次心跳：服务端 ≤30 秒才重新抓天气，这里 60 秒后再问一次。 */
#define UI_HB_FOLLOWUP_MS       60000u

static uint32_t s_hb_due_ms = 0;      /* 下一次心跳的到期时刻 */
static uint32_t s_hb_gap_ms = UI_HB_INTERVAL_MS;
static int  s_hb_followups = 0;       /* 还剩几次「短间隔」心跳 */
static bool s_hb_started = false;

/* 天气状态：在线读数 / 本地快照 / 状态 */
static nr_weather_t s_weather_live;
static nr_weather_t s_weather_cached;
static ui_weather_state_t s_weather_state = UI_WX_NONE;
static bool s_weather_cache_valid = false;
static bool s_weather_cache_written = false;
static char s_weather_cache_text[UI_WEATHER_TEXT_MAX];  /* 已写进卡里的内容 */
static int  s_weather_cache_temp = 0;

/* 当前城市（上报用的英文地名）与同步标志 */
static char s_city_current[UI_DATA_CITY_MAX];
static bool s_city_synced = false;

/* 读配对标志（定义在“页面动作”段） */
static void ui_paired_refresh(void);

/* ===================== 天气与城市（doc/05 §5.2） ===================== */

/**
 * 心跳（bootstrap）应答的识别与处理。
 *
 * **为什么靠 `weather` 字段识别**：net 层只有一条回包通路（kind=2），
 * 拍题应答与心跳应答都走它。心跳应答顶层必有 `weather` 字段
 * （doc/05 §5.2），拍题应答没有 —— 这是唯一可靠的区分方式。
 *
 * 心跳失败时 net 层会造一份带 `offline` 标记的空应答（见 net_dispatch.c），
 * 固件据此切到 TF 卡上的本地快照兜底。
 */
static void ui_weather_handle_reply(const char *payload, uint32_t len)
{
    bool offline = (nr_json_get_bool(payload, len, "offline", false) != 0);
    nr_weather_t w;
    memset(&w, 0, sizeof w);
    nr_parse_weather(payload, len, &w);

    if (offline) {
        /* 没连上服务端：本地有快照就显示快照（标明是缓存），没有就空态 */
        s_weather_state = s_weather_cache_valid ? UI_WX_CACHE : UI_WX_NONE;
    } else if (w.valid) {
        s_weather_live = w;
        s_weather_state = UI_WX_LIVE;
        ui_weather_save_cache(&w);
    } else {
        /* 服务端回了但暂时没数据（空对象 / 缺字段）—— 同一个空态，不当故障 */
        s_weather_state = UI_WX_EMPTY;
    }
    ui_weather_refresh_display();
}

/** 心跳应答（含 `weather` 字段）？ */
static bool ui_reply_is_weather(const char *payload, uint32_t len)
{
    if (payload == NULL || len == 0) {
        return false;
    }
    const char *obj = NULL;
    size_t obj_len = 0;
    return nr_json_get_object(payload, len, "weather", &obj, &obj_len) == 0;
}

/* ---- 天气状态的读写 ---- */

/** 把最近一次在线读数落盘（TF 卡 /cache/weather.json）：离线时的兜底快照。 */
static void ui_weather_save_cache(const nr_weather_t *w)
{
    if (w == NULL || !w->valid) {
        return;
    }
    /* 内容没变就不写（避免反复磨卡） */
    char text[UI_WEATHER_TEXT_MAX];
    snprintf(text, sizeof(text), "%s", w->text);
    if (s_weather_cache_written && strcmp(s_weather_cache_text, text) == 0 &&
        s_weather_cache_temp == w->temp) {
        return;
    }
    /* 天气描述来自服务端（中文短语），不含引号与控制字符 */
    static char buf[160];
    int n = snprintf(buf, sizeof(buf),
                     "{\"weather\":{\"temp\":%d,\"text\":\"%s\"}}",
                     w->temp, w->text);
    if (n <= 0 || (size_t)n >= sizeof(buf)) {
        return;
    }
    if (ui_data_weather_cache_set(buf, (uint32_t)n) == UI_DATA_OK) {
        snprintf(s_weather_cache_text, sizeof(s_weather_cache_text), "%s", w->text);
        s_weather_cache_temp = w->temp;
        s_weather_cache_written = true;
        s_weather_cache_valid = true;
    }
}

/** 读本地天气快照（离线兜底）。 */
static void ui_weather_load_cache(void)
{
    static char buf[512];
    uint32_t n = 0;
    if (ui_data_weather_cache_get(buf, sizeof(buf), &n) != UI_DATA_OK || n == 0) {
        return;
    }
    nr_weather_t w;
    memset(&w, 0, sizeof w);
    if (nr_parse_weather(buf, (size_t)n, &w) == 0 && w.valid) {
        s_weather_cached = w;
        s_weather_cache_valid = true;
        snprintf(s_weather_cache_text, sizeof(s_weather_cache_text), "%s", w.text);
        s_weather_cache_temp = w.temp;
        s_weather_cache_written = true;
    }
}

/** 刷新首页上的天气文案。 */
static void ui_weather_refresh_display(void)
{
    char buf[64];
    ui_weather_display_text(s_weather_state, &s_weather_live, &s_weather_cached,
                            buf, sizeof(buf));
    ui_pages_set_weather_text(buf);
}

/* ---- 城市 ---- */

/** 把城市写进 net 层缓存（经网络队列，跨核不乱写共享内存）。 */
static void ui_city_post_to_net(const char *wire)
{
    if (wire == NULL || wire[0] == '\0') {
        return;
    }
    net_request_t req = {};
    req.op = NET_OP_CITY;
    snprintf(req.city, sizeof(req.city), "%s", wire);
    if (hal_net_request_queue() == NULL ||
        xQueueSend(hal_net_request_queue(), &req, 0) != pdTRUE) {
        /* 网络队列满：下次心跳前再试一次 */
        s_city_synced = false;
    }
}

/** 把 TF 卡上保存的城市灌进 net 层缓存（开机 / 重启后恢复）。 */
static void ui_city_sync_from_card(void)
{
    if (s_city_synced) {
        return;
    }
    char city[UI_DATA_CITY_MAX];
    city[0] = '\0';
    if (ui_data_city_get(city, sizeof(city)) != UI_DATA_OK || city[0] == '\0') {
        return;   /* 还没设置过城市 */
    }
    snprintf(s_city_current, sizeof(s_city_current), "%s", city);
    s_city_synced = true;
    ui_city_post_to_net(city);
    /* 设置页右侧显示中文名（候选表里查得到就显示中文） */
    const ui_city_candidate_t *c = ui_weather_city_find_by_wire(city);
    ui_pages_set_city_label((c != NULL) ? c->zh : city);
}

/* ---- 心跳 ---- */

/** 投递一次心跳（bootstrap）：天气、课表、待同步清单都靠它。 */
static void ui_heartbeat_post(void)
{
    net_request_t req = {};
    req.op = NET_OP_BOOTSTRAP;
    if (hal_net_request_queue() == NULL ||
        xQueueSend(hal_net_request_queue(), &req, 0) != pdTRUE) {
        /* 网络忙：下个周期再试（不弹提示，后台同步不打扰用户） */
        s_hb_due_ms = (uint32_t)millis() + UI_HB_RETRY_MS;
    }
}

/* 前置声明（定义在“天气与城市”段） */
static void ui_weather_refresh_display(void);
static void ui_heartbeat_post(void);

/* ===================== 浮层 ===================== */

static void ui_overlay_sync_visibility(void)
{
    /* 高优先级浮层出现时, 低优先级必须让位 */
    int i;
    for (i = OVERLAY_COUNT - 1; i >= 0; i--) {
        if (s_overlay_visible[i]) {
            break;
        }
    }
    /* i 是当前最高优先级的可见浮层; 比它低的全部隐藏 */
    for (int j = OVERLAY_COUNT - 1; j > i; j--) {
        s_overlay_visible[j] = false;
    }
    for (int j = 0; j < OVERLAY_COUNT; j++) {
        ui_overlay_set_visible((ui_overlay_t)j, s_overlay_visible[j]);
    }
}

ui_overlay_t ui_overlay_push(ui_overlay_t overlay)
{
    if (overlay >= OVERLAY_COUNT) {
        return ui_overlay_top();
    }
    /* 录音浮层在阅读模块里被屏蔽 */
    if (overlay == OVERLAY_RECORDING && s_in_book) {
        return ui_overlay_top();
    }
    s_overlay_visible[overlay] = true;
    ui_overlay_sync_visibility();
    return ui_overlay_top();
}

void ui_overlay_pop(ui_overlay_t overlay)
{
    if (overlay >= OVERLAY_COUNT) {
        return;
    }
    s_overlay_visible[overlay] = false;
    ui_overlay_sync_visibility();
}

ui_overlay_t ui_overlay_top(void)
{
    int i;
    for (i = 0; i < OVERLAY_COUNT; i++) {
        if (s_overlay_visible[i]) {
            return (ui_overlay_t)i;
        }
    }
    return OVERLAY_NONE;
}

/* ===================== 页面 ===================== */

static void ui_show_page(ui_page_t page)
{
    if (page >= PAGE_COUNT) {
        return;
    }
    if (s_in_book) {
        lv_obj_add_flag(ui_page_root_book(s_current_book), LV_OBJ_FLAG_HIDDEN);
        s_in_book = false;
    }
    lv_obj_t *root = ui_page_root(page);
    if (root == NULL) {
        return;
    }
    lv_obj_clear_flag(root, LV_OBJ_FLAG_HIDDEN);
    /* 切页时关掉浮层, 提醒弹窗除外 —— 它要保留到用户短按关闭 */
    s_overlay_visible[OVERLAY_RECORDING] = false;
    s_overlay_visible[OVERLAY_TOAST] = false;
    ui_overlay_sync_visibility();
    s_current = page;
}

void ui_router_go(ui_page_t page)
{
    ui_show_page(page);
    ui_pages_cursor_reset(page);
    switch (page) {
    case PAGE_POMODORO:
        /* 进入就开始倒计时（时长取本地设置） */
        ui_pages_pomodoro_enter();
        break;
    case PAGE_HOME:
        ui_paired_refresh();
        ui_pages_refresh_home();
        break;
    case PAGE_SUBJECT_LIST:
        /* 学科包列表是**真实**已下载的包 —— 进页时重新读一遍卡 */
        ui_pages_refresh_subject_list();
        break;
    case PAGE_OTA:
        /* 固件升级页正文是真实状态 —— 进页时立刻刷新一次 */
        ui_ota_page_refresh();
        break;
    case PAGE_WIFI_SCAN:
        /* 进页就请求一次真扫描（真正的扫描在网络任务里做，UI 不阻塞） */
        ui_pages_wifi_scan_enter((uint32_t)millis());
        break;
    case PAGE_CITY:
        /* 候选城市列表：光标复位（从第一项开始） */
        ui_pages_city_enter();
        break;
    default:
        break;
    }
}

void ui_router_go_book(ui_book_page_t page)
{
    lv_obj_t *root = ui_page_root_book(page);
    if (root == NULL) {
        return;
    }
    if (!s_in_book) {
        lv_obj_add_flag(ui_page_root(s_current), LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(ui_page_root_book(s_current_book), LV_OBJ_FLAG_HIDDEN);
    }
    lv_obj_clear_flag(root, LV_OBJ_FLAG_HIDDEN);
    s_overlay_visible[OVERLAY_RECORDING] = false;
    s_overlay_visible[OVERLAY_TOAST] = false;
    ui_overlay_sync_visibility();
    s_current_book = page;
    s_in_book = true;
    if (page == PAGE_BOOKSHELF) {
        /* 书架内容来自 TF 卡，进页时重新读一遍 */
        ui_pages_refresh_bookshelf();
    }
}

ui_page_t ui_router_current(void)
{
    return s_current;
}

bool ui_router_in_book_module(void)
{
    return s_in_book;
}

/* ===================== 页面动作 ===================== */

/** 读配对标志（/config/profile.json）。读不到就按未配对处理。 */
static void ui_paired_refresh(void)
{
    int paired = 0;
    if (ui_data_read_paired(&paired) == UI_DATA_OK) {
        s_paired = paired;
    } else {
        s_paired = 0;
    }
    /*
     * 「已接入」的真相源是 **NVS 里有没有 device_secret**（自动登记写入，
     * doc/04 §1.2），不是 TF 卡上 profile.json 的 paired 字段 ——
     * 那张卡可能压根没有这个字段（空白卡自愈生成的那份就没有）。
     * 所以取两者的「或」：只要网关注册成功过，就算已接入，
     * 不然首页的「拍题导学」会一直误报「还没连上学习助手」。
     */
    if (s_pair_state == UI_PAIR_STATE_READY) {
        s_paired = 1;
    }
}

/**
 * 页面级「确定」动作（doc/03 §三 仲裁表的短按列）。
 *
 * 只有**已有真实实现**的入口才跳转；尚未打通的入口给一句温和提示，
 * 不用假动作糊弄过去。
 */
static void ui_page_action(ui_page_t page)
{
    int idx = ui_pages_cursor_index(page);
    switch (page) {
    case PAGE_CAMERA: {
        /* 短按 = 拍照：投递抓拍请求（UI 核 → 相机任务，doc/02 §4.2） */
        char sid[32];
        s_session_seq++;
        snprintf(sid, sizeof(sid), "sess_%u", (unsigned)s_session_seq);
        if (hal_photo_request_submit(sid, 1, 0) == HAL_OK) {
            ui_toast("已拍照，正在解题");
        } else {
            ui_toast("相机忙，请稍后再试");
        }
        break;
    }
    case PAGE_HOME:
        switch (idx) {
        case 0:
            /* 拍题导学要靠云端解题：没配对时先给一句温和提示 */
            if (!s_paired) {
                ui_toast("还没连上学习助手，请检查一下网络");
                break;
            }
            ui_router_go(PAGE_CAMERA);
            break;
        case 2: ui_router_go(PAGE_SUBJECT_LIST); break;
        case 3: ui_router_go_book(PAGE_BOOKSHELF); break;
        case 4: ui_router_go(PAGE_POMODORO); break;
        case 5: ui_router_go(PAGE_OUTBOX); break;
        case 6: ui_router_go(PAGE_SETTINGS); break;
        case 1: ui_toast("语音提问还在准备中"); break;
        default: break;
        }
        break;
    case PAGE_SETTINGS:
        switch (idx) {
        case 0: ui_router_go(PAGE_WIFI_SCAN); break;
        case 3: ui_router_go(PAGE_OTA); break;
        case 6: ui_toast("用左右键调整专注时长"); break;
        /* 「所在城市」：进候选城市列表（doc/05 §5.2） */
        case 7: ui_router_go(PAGE_CITY); break;
        default: ui_toast("这个设置还在准备中"); break;
        }
        break;
    case PAGE_POMODORO:
        /* 短按 = 暂停/继续（已完成后再按 = 重新开始） */
        ui_pages_pomodoro_toggle();
        break;
    case PAGE_CITY:
        /* 短按 = 确认选中的城市：落盘 TF 卡 + 写进 net 层缓存（随心跳上报） */
        {
            const ui_city_candidate_t *c = ui_pages_city_selected();
            if (c == NULL) {
                ui_toast("这里还没有可选的城市");
                break;
            }
            /* 校验能不能直接给服务端用（不是经纬度，是地名） */
            if (ui_weather_validate_city(c->en) != 0) {
                ui_toast("这个城市名暂时用不了");
                break;
            }
            /* 先落盘（离线兜底 + 重启后还在），再上报 */
            if (ui_data_city_set(c->en) != UI_DATA_OK) {
                ui_toast("没能保存，稍后再试");
                break;
            }
            snprintf(s_city_current, sizeof(s_city_current), "%s", c->en);
            s_city_synced = true;
            ui_city_post_to_net(c->en);
            ui_pages_set_city_label(c->zh);
            /* 立刻发一次心跳：服务端 ≤30 秒后重新抓取该城市天气 */
            s_hb_followups = 1;
            s_hb_due_ms = (uint32_t)millis() + 1000;
            ui_heartbeat_post();
            ui_toast("已保存，稍等就显示当地天气");
            ui_router_go(PAGE_SETTINGS);
        }
        break;
    case PAGE_OOBE:
        switch (idx) {
        case 0:
            ui_router_go(PAGE_WIFI_SCAN);
            break;
        case 1:
            /* 重新尝试接入：UI 只发请求，实际网络活儿由网络任务做 */
            {
                net_request_t req = {};
                req.op = NET_OP_REGISTER;
                if (hal_net_request_queue() == NULL ||
                    xQueueSend(hal_net_request_queue(), &req, 0) != pdTRUE) {
                    ui_toast("网络还在准备中，等一下就好");
                } else {
                    ui_toast("正在重新接入");
                }
            }
            break;
        case 2:
            /* 先离线体验：本地页（课表/书架/番茄钟/设置）照常可用 */
            ui_router_go(PAGE_HOME);
            break;
        default:
            break;
        }
        break;
    case PAGE_SUBJECT_LIST:
        if (idx >= 0) {
            /*
             * 打开学科包：卡片解密在**网络任务**里做（设备密钥只在 NVS），
             * 这里只发请求，解完经 kind=6 投回来再跳翻卡页。
             */
            const char *pack_id = ui_pages_selected_pack_id();
            if (pack_id[0] == '\0') {
                ui_toast("还没有下载学科包");
                break;
            }
            net_request_t req = {};
            req.op = NET_OP_CARDS;
            snprintf(req.pack_id, sizeof(req.pack_id), "%s", pack_id);
            if (hal_net_request_queue() == NULL ||
                xQueueSend(hal_net_request_queue(), &req, 0) != pdTRUE) {
                ui_toast("网络还在准备中，等一下就好");
            } else {
                ui_toast("正在打开学科包");
            }
        }
        break;
    case PAGE_CARD_STUDY:
        switch (idx) {
        case 0:
            /* 短按 = 翻面（正面题干 ↔ 背面答案） */
            ui_pages_show_card(ui_pages_card_index(), !ui_pages_card_answer_shown());
            break;
        case 1:
            /* 语音追问这题 —— 语音链路尚未接通 */
            ui_toast("语音追问还在准备中");
            break;
        default:
            break;
        }
        break;
    case PAGE_SOLVE_RESULT:
        /* 菜单项「口述你的思路」—— 语音链路尚未打通 */
        ui_toast("口述思路还在准备中");
        break;
    case PAGE_WIFI_SCAN:
        /* 短按：选中热点 →（加密的）进键盘输密码；（开放的）直接连 */
        ui_pages_wifi_scan_action((uint32_t)millis());
        break;
    case PAGE_KEYBOARD:
        /* 短按：输入字符 / 触发功能键；「确认并连接」→ 回去发起连接 */
        ui_pages_keyboard_action((uint32_t)millis());
        if (ui_pages_keyboard_confirmed()) {
            const char *pwd = ui_pages_keyboard_text();
            ui_pages_wifi_scan_connect(ui_pages_keyboard_target(), pwd, (uint32_t)millis());
            ui_pages_keyboard_leave();
            ui_router_go(PAGE_WIFI_SCAN);
        }
        break;
    case PAGE_OTA:
        /* 短按 = 检查更新：UI 只发请求，实际网络活儿由网络任务做（doc/04） */
        {
            net_request_t req = {};
            req.op = NET_OP_OTA_CHECK;
            if (hal_net_request_queue() == NULL ||
                xQueueSend(hal_net_request_queue(), &req, 0) != pdTRUE) {
                ui_toast("网络还在准备中，等一下就好");
            } else {
                ui_toast("正在检查更新");
            }
        }
        break;
    default:
        break;
    }
}

/** 方向键：列表页移动光标；解析结果页滚动正文（doc/03 §三）。 */
static void ui_page_nav(ui_page_t page, int delta)
{
    if (page == PAGE_SOLVE_RESULT) {
        ui_pages_solve_scroll(delta);
        return;
    }
    if (page == PAGE_WIFI_SCAN) {
        /* Wi-Fi 列表页的光标自己管（热点 + 重新扫描） */
        ui_pages_wifi_scan_move(delta);
        return;
    }
    if (page == PAGE_KEYBOARD) {
        /* 键盘：上下换行 */
        ui_pages_keyboard_move(0, delta, (uint32_t)millis());
        return;
    }
    if (page == PAGE_CITY) {
        /* 城市列表：上下移动光标（列表自己管滚动） */
        ui_pages_city_move(delta);
        return;
    }
    ui_pages_cursor_move(page, delta);
}

/**
 * 左右键：设置页调专注时长；其它页面的左右暂不作用途。
 * 不新增手势 —— 四向导航里左右本来就是「减/加」。
 */
static void ui_page_side(ui_page_t page, int delta)
{
    if (page == PAGE_KEYBOARD) {
        /* 键盘：左右换列 */
        ui_pages_keyboard_move(delta, 0, (uint32_t)millis());
        return;
    }
    if (page == PAGE_SETTINGS) {
        if (ui_pages_cursor_index(page) == 6) {
            ui_pages_focus_adjust(delta);
            return;
        }
        return;
    }
    if (page == PAGE_CARD_STUDY) {
        /* 左右键：上一张 / 下一张卡片（doc/03 §四 手势表）。
           翻到下一张时回到正面 —— 上一张的答案没必要留着。 */
        int n = ui_pages_card_count();
        if (n <= 0) {
            return;
        }
        int next = ui_pages_card_index() + delta;
        if (next < 0) {
            next = n - 1;
        }
        if (next >= n) {
            next = 0;
        }
        ui_pages_show_card(next, false);
    }
}

/* ===================== 电子书模块手势（doc/07） ===================== */

/** 调背光（阅读页上下键用）。 */
static void ui_brightness(int delta)
{
    uint8_t level = hal_display_get_backlight();
    int next = (int)level + delta;
    if (next < 0) {
        next = 0;
    }
    if (next > 5) {
        next = 5;
    }
    if ((uint8_t)next == level) {
        return;
    }
    hal_display_set_backlight((uint8_t)next);
}

static void ui_book_gesture(ui_gesture_t gesture)
{
    switch (gesture) {
    case GESTURE_UP:
        if (s_current_book == PAGE_BOOKSHELF) {
            ui_pages_bookshelf_move(-1);
        } else if (s_current_book == PAGE_READER) {
            ui_brightness(-1);
        }
        break;
    case GESTURE_DOWN:
        if (s_current_book == PAGE_BOOKSHELF) {
            ui_pages_bookshelf_move(1);
        } else if (s_current_book == PAGE_READER) {
            ui_brightness(1);
        }
        break;
    case GESTURE_LEFT:
        if (s_current_book == PAGE_READER) {
            ui_pages_reader_prev_page();
        }
        break;
    case GESTURE_RIGHT:
        if (s_current_book == PAGE_READER) {
            ui_pages_reader_next_page();
        }
        break;
    case GESTURE_CLICK:
        if (s_current_book == PAGE_BOOKSHELF) {
            /* 宵禁 / 每日上限：被锁就不进入阅读（doc/07 §2.4） */
            if (!ui_pages_reader_guard_check()) {
                ui_router_go_book(PAGE_BOOK_LOCKED);
                break;
            }
            if (ui_pages_bookshelf_open_selected() == 0) {
                ui_router_go_book(PAGE_READER);
            } else {
                ui_toast("暂时读不到内容");
            }
        } else if (s_current_book == PAGE_READER) {
            ui_router_go_book(PAGE_BOOK_MENU);
        }
        break;
    case GESTURE_LONG:
    default:
        /* 长按退出在主分支里统一处理 */
        break;
    }
}

/* ===================== 手势 ===================== */

static void ui_handle_recording_gesture(ui_gesture_t gesture)
{
    switch (gesture) {
    case GESTURE_CLICK:
        /* 短按结束并提交: 具体提交动作由 task_audio 监听浮层状态完成 */
        ui_overlay_pop(OVERLAY_RECORDING);
        break;
    case GESTURE_UP:
        /* 上推取消 */
        ui_overlay_pop(OVERLAY_RECORDING);
        break;
    case GESTURE_LONG:
        /* 长按等同于取消 */
        ui_overlay_pop(OVERLAY_RECORDING);
        break;
    default:
        break;
    }
}

void ui_router_handle_gesture(ui_gesture_t gesture)
{
    if (gesture == GESTURE_NONE) {
        return;
    }

    /* 浮层优先: 有浮层时手势先交给浮层, 不落到页面 */
    ui_overlay_t top = ui_overlay_top();
    if (top == OVERLAY_REMINDER) {
        if (gesture == GESTURE_CLICK) {
            /* 短按关闭提醒并恢复打断前的页面 */
            ui_overlay_pop(OVERLAY_REMINDER);
            if (s_reminder_had_page) {
                s_reminder_had_page = false;
                if (s_in_book) {
                    ui_router_go_book(s_current_book);
                } else {
                    ui_router_go(s_reminder_restore_page);
                }
            }
        }
        /* 长按忽略 */
        return;
    }
    if (top == OVERLAY_RECORDING) {
        ui_handle_recording_gesture(gesture);
        return;
    }
    if (top == OVERLAY_TOAST) {
        /* 提示条不响应手势 */
        return;
    }

    /* 页面级: 短按 = 确定, 长按 = 退出, 方向键 = 页内导航 */
    switch (gesture) {
    case GESTURE_CLICK:
        if (s_in_book) {
            ui_book_gesture(gesture);
            break;
        }
        ui_page_action(s_current);
        break;
    case GESTURE_UP:
        if (s_in_book) {
            ui_book_gesture(gesture);
            break;
        }
        if (s_current != PAGE_POMODORO) {
            ui_page_nav(s_current, -1);
        }
        break;
    case GESTURE_DOWN:
        if (s_in_book) {
            ui_book_gesture(gesture);
            break;
        }
        if (s_current != PAGE_POMODORO) {
            ui_page_nav(s_current, 1);
        }
        break;
    case GESTURE_LEFT:
    case GESTURE_RIGHT:
        if (s_in_book) {
            ui_book_gesture(gesture);
            break;
        }
        ui_page_side(s_current, (gesture == GESTURE_LEFT) ? -1 : 1);
        break;
    case GESTURE_LONG:
        /* 退出: 退回上一级 */
        if (s_in_book) {
            switch (s_current_book) {
            case PAGE_READER:
            case PAGE_BOOK_MENU:
            case PAGE_BOOK_TOC:
            case PAGE_BOOK_JUMP:
                ui_pages_reader_leave();   /* 退出阅读：保存进度与当日时长 */
                ui_router_go_book(PAGE_BOOKSHELF);
                break;
            case PAGE_BOOKSHELF:
            case PAGE_BOOK_LOCKED:
            default:
                ui_router_go(PAGE_HOME);
                break;
            }
        } else if (s_current == PAGE_POMODORO) {
            /* 番茄钟: 长按 = 结束并退出 */
            ui_pages_pomodoro_stop();
            ui_router_go(PAGE_HOME);
        } else if (s_current == PAGE_CARD_STUDY) {
            /* 翻卡页: 长按 = 退出返回学科包列表（doc/03 §四） */
            ui_router_go(PAGE_SUBJECT_LIST);
        } else if (s_current == PAGE_KEYBOARD) {
            /* 键盘页: 长按 = 退出回 Wi-Fi 列表（不打断正在进行的连接） */
            ui_pages_keyboard_leave();
            ui_router_go(PAGE_WIFI_SCAN);
        } else if (s_current != PAGE_HOME && s_current != PAGE_OOBE) {
            ui_router_go(PAGE_HOME);
        }
        break;
    default:
        /* 四向导航由页面自己的光标处理 */
        break;
    }
}

void ui_router_handle_joystick(const joystick_event_t *ev)
{
    if (ev == NULL) {
        return;
    }
    ui_gesture_t gesture = GESTURE_NONE;
    switch (ev->type) {
    case JOY_EVENT_UP:
        gesture = GESTURE_UP;
        break;
    case JOY_EVENT_DOWN:
        gesture = GESTURE_DOWN;
        break;
    case JOY_EVENT_LEFT:
        gesture = GESTURE_LEFT;
        break;
    case JOY_EVENT_RIGHT:
        gesture = GESTURE_RIGHT;
        break;
    case JOY_EVENT_CLICK:
        gesture = GESTURE_CLICK;
        break;
    case JOY_EVENT_LONG_PRESS_END:
        /* 长按在**松开瞬间**才生效 (doc/03 §3.2) */
        gesture = GESTURE_LONG;
        break;
    case JOY_EVENT_LONG_PRESS_START:
    case JOY_EVENT_REPEAT:
    case JOY_EVENT_NONE:
    default:
        /* 长按开始不触发动作; 连发滚动由页面自己处理 */
        break;
    }
    ui_router_handle_gesture(gesture);
}

void ui_toast(const char *text)
{
    if (text == NULL || text[0] == '\0') {
        return;
    }
    ui_overlay_set_text(OVERLAY_TOAST, text);
    ui_overlay_push(OVERLAY_TOAST);
}

/* ===================== 初始化与刷新 ===================== */

ui_book_page_t ui_router_current_book(void)
{
    return s_current_book;
}

void ui_router_init(void)
{
    lv_obj_t *scr = lv_scr_act();
    ui_pages_init(scr);
    s_current = PAGE_OOBE;
    s_in_book = false;
    ui_show_page(PAGE_OOBE);
}

void ui_router_tick(void)
{
    /* LVGL 5ms 心跳由 task_ui_fsm 的 lv_timer_handler 驱动;
     * 番茄钟的倒计时挂在这里 —— 用毫秒计数，不做秒级累加。 */
    uint32_t now = (uint32_t)millis();
    ui_pages_pomodoro_tick(now);

    /* Wi-Fi 扫描页：轮询真扫描结果与连接状态（doc/03 §五 第 11 页） */
    if (s_current == PAGE_WIFI_SCAN) {
        ui_pages_wifi_scan_tick(now);
    }
    /* 键盘页：60 秒无操作自动退出（doc/03 §五 第 12 页） */
    if (s_current == PAGE_KEYBOARD && ui_pages_keyboard_tick(now)) {
        ui_pages_keyboard_leave();
        ui_router_go(PAGE_WIFI_SCAN);
    }

    /* 心跳（bootstrap）：天气、课表、待同步清单都靠它（doc/05 §5.2）。
     * 开机先来一次（拿到天气），之后每 10 分钟一次；
     * 刚设完城市时追一次短间隔的（服务端 ≤30 秒才重新抓取天气）。 */
    if (!s_hb_started) {
        s_hb_started = true;
        s_hb_due_ms = now + UI_HB_FIRST_DELAY_MS;
        /* 开机就把 TF 卡上保存的城市灌进 net 层缓存：
         * 断网时用的就是它，恢复联网后随下一次心跳上报 */
        ui_city_sync_from_card();
        /* 本地天气快照（离线兜底） */
        ui_weather_load_cache();
        ui_weather_refresh_display();
    } else if ((int32_t)(now - s_hb_due_ms) >= 0) {
        ui_city_sync_from_card();
        ui_heartbeat_post();
        if (s_hb_followups > 0) {
            /* 追一次：服务端重抓完天气后再问一次 */
            s_hb_followups--;
            s_hb_gap_ms = UI_HB_FOLLOWUP_MS;
        } else {
            s_hb_gap_ms = UI_HB_INTERVAL_MS;
        }
        s_hb_due_ms = now + s_hb_gap_ms;
    }

    /* 阅读计时（doc/07 §2.4）：只有阅读页在前台、无浮层遮挡、屏幕点亮时
     * 才累计当日阅读时长 —— 菜单页 / 目录页 / 提醒弹窗 / 息屏一律暂停。 */
    bool reading_active = s_in_book && (s_current_book == PAGE_READER) &&
                          (ui_overlay_top() == OVERLAY_NONE) &&
                          (hal_display_get_backlight() > 0);
    ui_pages_reader_tick(now, reading_active);
}

/* ===================== 云端应答 ===================== */

void ui_router_handle_net_reply(int32_t biz_code, char *payload, uint32_t len)
{
    /* 旧缓冲先释放 —— 所有权始终在 UI 层 */
    if (s_last_reply != NULL) {
        free(s_last_reply);
        s_last_reply = NULL;
        s_last_reply_len = 0;
    }
    s_last_biz = biz_code;
    if (payload != NULL) {
        s_last_reply = payload;
        s_last_reply_len = len;
    }

    /* 心跳（bootstrap）应答：顶层有 weather 字段 —— 走天气分支，不弹提示。
     * 心跳失败时 net 层会造一份带 offline 标记的空应答（见 net_dispatch.c），
     * 固件据此切到 TF 卡上的本地快照兜底。 */
    if (ui_reply_is_weather(s_last_reply, s_last_reply_len)) {
        ui_weather_handle_reply(s_last_reply, s_last_reply_len);
        return;
    }

    /* 文案一律由 UI 层出（doc/03 §四）：HAL / net 层不拼面向用户的中文 */
    if (s_last_biz == 4004) {
        ui_toast("请先在设置里完成学段选择");
        return;
    }
    if (s_last_biz != 0) {
        ui_toast("网络请求未成功");
        return;
    }

    /* 成功：把解题步骤与费曼反问渲染到解析结果页 */
    nr_solve_result_t res;
    if (nr_parse_solve_result(s_last_reply, s_last_reply_len, &res) == 0 &&
        res.status[0] != '\0') {
        ui_pages_set_solve_result(res.display_markdown, res.feynman_question);
        ui_router_go(PAGE_SOLVE_RESULT);
    } else {
        ui_toast("已收到云端应答");
    }
}

const char *ui_router_last_reply(uint32_t *out_len)
{
    if (out_len != NULL) {
        *out_len = s_last_reply_len;
    }
    return s_last_reply;
}

/* ===================== 学科卡片包（doc/04 §四） ===================== */

void ui_router_handle_cards(card_pack_t *pack, int rc)
{
    /* 旧的卡片集先释放（所有权一直在 UI 层） */
    if (s_cards != NULL) {
        free(s_cards);
        s_cards = NULL;
    }
    if (pack == NULL) {
        ui_toast("暂时打不开这个学科包");
        return;
    }
    if (rc < 0 || pack->count <= 0) {
        free(pack);
        /* 文案温和，且**不出现加密、密钥这类技术词**（doc/03 §四 铁律） */
        if (rc == CARDS_ERR_KEY) {
            ui_toast("还没接入学习助手，先连上网再试");
        } else if (rc == CARDS_ERR_NOTFOUND) {
            ui_toast("这个学科包还没下载好");
        } else {
            ui_toast("暂时打不开这个学科包");
        }
        return;
    }
    s_cards = pack;
    ui_pages_set_cards(s_cards);
    ui_router_go(PAGE_CARD_STUDY);
}

/* ===================== 设备接入状态（自动登记，doc/04 §1.2） ===================== */

void ui_router_set_pair_state(int state)
{
    s_pair_state = state;
    ui_pages_set_pair_state(state);

    if (state == UI_PAIR_STATE_READY) {
        /* 接入成功：顺手刷新「已配对」标志，让首页的端云入口可用。
         * 读卡失败也不阻断跳转 —— 本地页照常。 */
        ui_paired_refresh();
        if (s_current == PAGE_OOBE) {
            /* 设备侧零输入：接入好了直接进首页，不需要家长按任何键 */
            ui_router_go(PAGE_HOME);
        }
    }
}
