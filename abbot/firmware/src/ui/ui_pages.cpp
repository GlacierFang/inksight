/*
 * ui_pages.cpp —— 13 个核心页面 + 电子书子页面 + 三层浮层的 LVGL 骨架
 *
 * 契约来源: abbot/doc/03 §二 三区骨架 / §三 浮层优先级 / §五 13 页线框,
 *           abbot/doc/07 电子书子页面。
 *
 * 设计约定:
 *   - 本文件只负责**建页面**与**画内容**, 不做路由决策、不计时。
 *   - 路由决策在 ui_router.cpp; 手势的短按/长按判定由 HAL 裁定后传入。
 *   - 严禁在本文件里出现任何阻塞式网络 / TF 卡 IO —— 那是 task_net_worker / task_storage 的活。
 *   - 严禁出现 AES / HTTP / signature / Token / Python / Traceback 等技术术语字样。
 */
#include "ui_pages.h"

#include <stdio.h>
#include <string.h>

#include "lvgl.h"
#include "ui_font.h"

#include "hal_interfaces.h"
#include "keypad_editor.h"
#include "net_api.h"
#include "ui_book_guard.h"
#include "ui_data.h"
#include "ui_ota_page.h"
#include "wifi_model.h"

/* ===================== 颜色（中性骨架，落地前按 doc/03 §一 核对） ===================== */
#define UI_C_BG        lv_color_hex(0xFFFFFF)
#define UI_C_CARD      lv_color_hex(0xF2F4F7)
#define UI_C_LINE      lv_color_hex(0xD7DCE3)
#define UI_C_TEXT      lv_color_hex(0x1F2430)
#define UI_C_SUBTEXT   lv_color_hex(0x6B7280)
#define UI_C_ACCENT    lv_color_hex(0x2F6FED)
#define UI_C_WARN      lv_color_hex(0xB42318)

/* ===================== 三区几何（doc/03 §二） ===================== */
#define UI_STATUS_H    24
#define UI_HINT_H      32
#define UI_CONTENT_Y   UI_STATUS_H
#define UI_CONTENT_H   (480 - UI_STATUS_H - UI_HINT_H)   /* 424 */
#define UI_PAD         8

static lv_style_t s_style_root;
static lv_style_t s_style_card;
static lv_style_t s_style_title;
static lv_style_t s_style_body;
static lv_style_t s_style_hint;
static lv_style_t s_style_status;
static bool s_styles_ready = false;

/* 页面内容对象句柄, 每个页面一个 */
typedef struct {
    lv_obj_t *root;
    lv_obj_t *status;
    lv_obj_t *status_label;
    lv_obj_t *content;
    lv_obj_t *hint;
} ui_page_widgets_t;

static ui_page_widgets_t s_pages[PAGE_COUNT];
static ui_page_widgets_t s_book_pages[PAGE_BOOK_COUNT - PAGE_BOOKSHELF];

/* ===================== 列表光标（doc/03 §三） ===================== */

#define UI_MAX_ITEMS 12          /* 每页最多可选行数 */
#define UI_ITEM_TEXT_MAX 72      /* 行文案（不含行首标记）上限 */
#define UI_SOLVE_TEXT_MAX 1024   /* 解析正文缓存上限 */
#define UI_SOLVE_LINES 13        /* 正文一屏显示的行数 */

typedef struct {
    lv_obj_t *row;
    lv_obj_t *label;
    char text[UI_ITEM_TEXT_MAX]; /* 去掉行首 "> " / "  " 标记后的原文 */
} ui_item_t;

static ui_item_t s_items[PAGE_COUNT][UI_MAX_ITEMS];
static uint8_t s_item_count[PAGE_COUNT];
static uint8_t s_cursor[PAGE_COUNT];
static int s_build_page = -1;     /* 正在构建的页面（-1 = 不登记条目） */

/* 光标渲染（定义在文件后面的「列表光标」段 —— 这里先前置声明） */
static void ui_cursor_render(ui_page_t page);

/* 解析结果页的动态内容 */
static lv_obj_t *s_solve_body = NULL;
static lv_obj_t *s_solve_question = NULL;
static char s_solve_text[UI_SOLVE_TEXT_MAX];
static int s_solve_scroll = 0;

/* ===================== 番茄钟（doc/03 §五 第 8 页） ===================== */

typedef enum {
    POMO_IDLE = 0,
    POMO_RUNNING,
    POMO_PAUSED,
    POMO_DONE
} pomo_state_t;

static pomo_state_t s_pomo_state = POMO_IDLE;
static uint32_t s_pomo_total_ms = 0;
static uint32_t s_pomo_remain_ms = 0;
static uint32_t s_pomo_last_ms = 0;
static bool s_pomo_tick_ready = false;
static lv_obj_t *s_pomo_time_label = NULL;
static lv_obj_t *s_pomo_sub_label = NULL;

/* 专注时长（分钟）：默认 25，可调，持久化在 TF 卡 /config/settings.json */
static int s_focus_minutes = UI_DATA_FOCUS_DEFAULT;
static bool s_focus_loaded = false;
static lv_obj_t *s_focus_value_label = NULL;

/* 首页「今日课程」卡片 */
static lv_obj_t *s_home_schedule_label = NULL;

/* ===================== 学科卡片（doc/04 §四） ===================== */

#define UI_MAX_PACKS   8   /* 学科包列表最多显示几个 */
#define UI_PACK_ID_MAX 32  /* 学科包 id 长度上限 */

static const card_pack_t *s_cards = NULL;   /* 已载入的卡片集（UI 持有指针） */
static int s_card_index = 0;                /* 当前这张卡（0 起） */
static bool s_card_show_answer = false;     /* 当前是不是背面 */
static lv_obj_t *s_card_label = NULL;       /* 卡片正文标签 */
static lv_obj_t *s_card_pos_label = NULL;   /* 「第 N / M 张」 */

/* 学科包列表（读卡上 /subjects 下的真实目录） */
static char s_pack_ids[UI_MAX_PACKS][UI_PACK_ID_MAX];
static int s_pack_count = 0;

/* OOBE 自动接入进度页（doc/04 §1.2 自动登记） */
static lv_obj_t *s_oobe_state_label = NULL;

/* ===================== 书架与阅读页（doc/07） ===================== */

#define UI_MAX_BOOKS       8    /* 书架最多列出的书 */
#define UI_MAX_CHAPTERS    16   /* 目录最多解析的章节 */
#define UI_READER_HISTORY  16   /* 往回翻记住的页起点数 */

static char s_book_names[UI_MAX_BOOKS][UI_DATA_MAX_NAME];
static uint32_t s_book_sizes[UI_MAX_BOOKS];
static int s_book_count = 0;
static int s_book_cursor = 0;
static lv_obj_t *s_book_labels[UI_MAX_BOOKS];

static char s_reader_path[UI_DATA_MAX_PATH];
static uint32_t s_reader_offset = 0;
static uint32_t s_reader_consumed = 0;
static uint32_t s_reader_size = 0;
static uint32_t s_reader_history[UI_READER_HISTORY];
static int s_reader_history_count = 0;
static lv_obj_t *s_reader_body = NULL;

static char s_chapter_titles[UI_MAX_CHAPTERS][32];
static uint32_t s_chapter_offsets[UI_MAX_CHAPTERS];
static int s_chapter_count = 0;

/* ===================== 阅读护栏（doc/07 §2.3 / §2.4） ===================== */

static ui_bookmarks_t s_bookmarks;           /* /records/bookmarks.json 的内存形态 */
static bool s_bookmarks_loaded = false;
static ui_book_guard_t s_book_guard;         /* /config/profile.json 的护栏配置 */
static bool s_book_guard_loaded = false;
static uint32_t s_reader_tick_last_ms = 0;   /* 上一次计时的毫秒计数 */
static uint32_t s_reader_session_ms = 0;     /* 本次会话的零头（不足 1 秒的部分） */
static bool s_reader_counting = false;       /* 是否正在计当日阅读时长 */
static lv_obj_t *s_locked_title = NULL;      /* 宵禁 / 上限提示页 */
static lv_obj_t *s_locked_body = NULL;

/* 进度表读写用的缓冲（静态：不占 UI 任务栈） */
static char s_bookmarks_buf[1024];
static char s_bookmarks_bak_buf[1024];
static char s_bookmarks_out[1400];

/* 浮层句柄 */
typedef struct {
    lv_obj_t *root;
    lv_obj_t *label;
} ui_overlay_widgets_t;

static ui_overlay_widgets_t s_overlays[OVERLAY_COUNT];

/* ===================== 样式 ===================== */
static void ui_styles_init(void)
{
    if (s_styles_ready) {
        return;
    }

    lv_style_init(&s_style_root);
    lv_style_set_bg_color(&s_style_root, UI_C_BG);
    lv_style_set_bg_opa(&s_style_root, LV_OPA_COVER);
    lv_style_set_pad_all(&s_style_root, 0);
    lv_style_set_border_width(&s_style_root, 0);

    lv_style_init(&s_style_card);
    lv_style_set_bg_color(&s_style_card, UI_C_CARD);
    lv_style_set_bg_opa(&s_style_card, LV_OPA_COVER);
    lv_style_set_radius(&s_style_card, 6);
    lv_style_set_border_width(&s_style_card, 1);
    lv_style_set_border_color(&s_style_card, UI_C_LINE);
    lv_style_set_pad_all(&s_style_card, UI_PAD);

    lv_style_init(&s_style_title);
    lv_style_set_text_color(&s_style_title, UI_C_TEXT);
    lv_style_set_text_font(&s_style_title, &ui_font_cn_16);

    lv_style_init(&s_style_body);
    lv_style_set_text_color(&s_style_body, UI_C_TEXT);
    lv_style_set_text_font(&s_style_body, &ui_font_cn_16);

    lv_style_init(&s_style_hint);
    lv_style_set_text_color(&s_style_hint, UI_C_SUBTEXT);
    lv_style_set_text_font(&s_style_hint, &ui_font_cn_16);

    lv_style_init(&s_style_status);
    lv_style_set_text_color(&s_style_status, UI_C_SUBTEXT);
    lv_style_set_text_font(&s_style_status, &ui_font_cn_16);

    s_styles_ready = true;
}

/* ===================== 通用构件 ===================== */

static lv_obj_t *ui_make_root(lv_obj_t *parent)
{
    lv_obj_t *root = lv_obj_create(parent);
    /* LVGL 8.x 没有 lv_obj_set_style_all (那是 v9 的 API) —— 等价写法是
     * 先清掉默认样式再挂自己的。 */
    lv_obj_remove_style_all(root);
    lv_obj_add_style(root, &s_style_root, LV_PART_MAIN);
    lv_obj_set_size(root, 320, 480);
    lv_obj_clear_flag(root, LV_OBJ_FLAG_SCROLLABLE);
    return root;
}

static lv_obj_t *ui_make_status(lv_obj_t *root, const char *text, lv_obj_t **out_label)
{
    lv_obj_t *bar = lv_obj_create(root);
    lv_obj_set_size(bar, 320, UI_STATUS_H);
    lv_obj_set_pos(bar, 0, 0);
    lv_obj_set_style_bg_color(bar, UI_C_BG, 0);
    lv_obj_set_style_bg_opa(bar, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(bar, 0, 0);
    lv_obj_set_style_pad_all(bar, 4, 0);
    lv_obj_clear_flag(bar, LV_OBJ_FLAG_SCROLLABLE);

    lv_obj_t *label = lv_label_create(bar);
    lv_label_set_text(label, text);
    lv_obj_add_style(label, &s_style_status, 0);
    lv_obj_align(label, LV_ALIGN_LEFT_MID, 0, 0);
    if (out_label != NULL) {
        *out_label = label;
    }
    return bar;
}

static lv_obj_t *ui_make_content(lv_obj_t *root)
{
    lv_obj_t *content = lv_obj_create(root);
    lv_obj_set_size(content, 320, UI_CONTENT_H);
    lv_obj_set_pos(content, 0, UI_CONTENT_Y);
    lv_obj_set_style_bg_color(content, UI_C_BG, 0);
    lv_obj_set_style_bg_opa(content, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(content, 0, 0);
    lv_obj_set_style_pad_all(content, UI_PAD, 0);
    lv_obj_clear_flag(content, LV_OBJ_FLAG_SCROLLABLE);
    return content;
}

static lv_obj_t *ui_make_hint(lv_obj_t *root, const char *text)
{
    lv_obj_t *bar = lv_obj_create(root);
    lv_obj_set_size(bar, 320, UI_HINT_H);
    lv_obj_set_pos(bar, 0, 480 - UI_HINT_H);
    lv_obj_set_style_bg_color(bar, UI_C_BG, 0);
    lv_obj_set_style_bg_opa(bar, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(bar, 0, 0);
    lv_obj_set_style_pad_all(bar, 4, 0);
    lv_obj_clear_flag(bar, LV_OBJ_FLAG_SCROLLABLE);

    lv_obj_t *label = lv_label_create(bar);
    lv_label_set_text(label, text);
    lv_obj_add_style(label, &s_style_hint, 0);
    lv_obj_align(label, LV_ALIGN_LEFT_MID, 0, 0);
    return bar;
}

static lv_obj_t *ui_make_label(lv_obj_t *parent, const char *text, bool title)
{
    lv_obj_t *label = lv_label_create(parent);
    lv_label_set_text(label, text);
    lv_obj_add_style(label, title ? &s_style_title : &s_style_body, 0);
    lv_obj_set_width(label, 280);
    lv_label_set_long_mode(label, LV_LABEL_LONG_WRAP);
    return label;
}

static lv_obj_t *ui_make_card(lv_obj_t *parent)
{
    lv_obj_t *card = lv_obj_create(parent);
    lv_obj_set_size(card, 280, 96);
    lv_obj_remove_style_all(card);
    lv_obj_add_style(card, &s_style_card, LV_PART_MAIN);
    lv_obj_clear_flag(card, LV_OBJ_FLAG_SCROLLABLE);
    return card;
}

/* ===================== 公开接口：给别的 UI 模块建标签 ===================== */

/**
 * 建一个正文 / 标题标签（样式与页面内一致）。
 *
 * 供 src/ui/ui_ota_page.cpp 用 —— 它要自己持有这个标签并定时刷新它的文字。
 */
lv_obj_t *ui_pages_make_label(lv_obj_t *parent, const char *text, bool title)
{
    return ui_make_label(parent, text, title);
}

/* 列表项: 文案 + 右侧副文案, 返回可点击的整行对象 */
static lv_obj_t *ui_make_list_item_ex(lv_obj_t *parent, const char *text, const char *sub,
                                      lv_obj_t **out_sub)
{
    lv_obj_t *row = lv_obj_create(parent);
    lv_obj_set_size(row, 280, 34);
    lv_obj_set_style_bg_opa(row, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(row, 0, 0);
    lv_obj_set_style_pad_all(row, 2, 0);
    lv_obj_clear_flag(row, LV_OBJ_FLAG_SCROLLABLE);

    lv_obj_t *label = lv_label_create(row);
    lv_label_set_text(label, text);
    lv_obj_add_style(label, &s_style_body, 0);
    lv_obj_align(label, LV_ALIGN_LEFT_MID, 0, 0);

    /* 登记为可选行（光标用）：行首的 "> " / "  " 标记在渲染时按光标位置重写 */
    if (s_build_page >= 0 && s_build_page < (int)PAGE_COUNT) {
        uint8_t n = s_item_count[s_build_page];
        if (n < UI_MAX_ITEMS) {
            const char *t = text;
            while (*t == ' ') {
                t++;
            }
            if (*t == '>') {
                t++;
                while (*t == ' ') {
                    t++;
                }
            }
            snprintf(s_items[s_build_page][n].text, UI_ITEM_TEXT_MAX, "%s", t);
            s_items[s_build_page][n].row = row;
            s_items[s_build_page][n].label = label;
            s_item_count[s_build_page] = n + 1;
        }
    }

    if (sub != NULL) {
        lv_obj_t *right = lv_label_create(row);
        lv_label_set_text(right, sub);
        lv_obj_add_style(right, &s_style_hint, 0);
        lv_obj_align(right, LV_ALIGN_RIGHT_MID, 0, 0);
        if (out_sub != NULL) {
            *out_sub = right;
        }
    }
    return row;
}

static lv_obj_t *ui_make_list_item(lv_obj_t *parent, const char *text, const char *sub)
{
    return ui_make_list_item_ex(parent, text, sub, NULL);
}

/* ===================== 各页内容 ===================== */

/* 0. 首次开机引导 (PAGE_OOBE)
 *
 * 改版后的 OOBE 不再要求家长敲配对码：设备联网后自己向学习助手报到，
 * 这一页只展示**接入进度**（doc/04 §1.2）。
 * 页面上的文字由 `ui_pages_set_pair_state()` 刷新，UI 不自己计时、不发网络请求。
 */
static void ui_build_oobe(lv_obj_t *content)
{
    lv_obj_t *title = ui_make_label(content, "欢迎使用 · 随身学习终端", true);
    lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 0);

    lv_obj_t *card = ui_make_card(content);
    lv_obj_align(card, LV_ALIGN_TOP_MID, 0, 0);
    lv_obj_t *state = ui_make_label(card, "正在准备中…", false);
    lv_obj_align(state, LV_ALIGN_TOP_LEFT, 0, 0);
    s_oobe_state_label = state;

    lv_obj_t *body = ui_make_label(content,
        "正在为你连接学习助手，\n不用输入任何东西，等一会儿就好。", false);
    lv_obj_align(body, LV_ALIGN_TOP_LEFT, 0, 112);

    lv_obj_t *item1 = ui_make_list_item(content, "> [ 连接 Wi-Fi 网络 ]", NULL);
    lv_obj_align(item1, LV_ALIGN_TOP_LEFT, 0, 200);

    lv_obj_t *item2 = ui_make_list_item(content, "  [ 重新尝试接入 ]", NULL);
    lv_obj_align(item2, LV_ALIGN_TOP_LEFT, 0, 240);

    lv_obj_t *item3 = ui_make_list_item(content, "  [ 先离线体验 ]", NULL);
    lv_obj_align(item3, LV_ALIGN_TOP_LEFT, 0, 280);
}

/** 按接入状态刷新 OOBE 进度文案（文案温和，不出现任何技术术语）。 */
void ui_pages_set_pair_state(int state)
{
    if (s_oobe_state_label == NULL) {
        return;
    }
    const char *text;
    switch (state) {
    case UI_PAIR_STATE_PENDING:
        text = "正在接入学习助手…";
        break;
    case UI_PAIR_STATE_READY:
        text = "已经接上啦，可以开始学习了";
        break;
    case UI_PAIR_STATE_FAILED:
        text = "暂时没接上，会自动再试一次";
        break;
    default:
        text = "正在准备中…";
        break;
    }
    lv_label_set_text(s_oobe_state_label, text);
}

/* 1. 主仪表盘 (PAGE_HOME) */
static void ui_build_home(lv_obj_t *content)
{
    lv_obj_t *card = ui_make_card(content);
    lv_obj_align(card, LV_ALIGN_TOP_MID, 0, 0);
    lv_obj_t *label = ui_make_label(card, "今日课程\n暂时读不到课表", false);
    lv_obj_align(label, LV_ALIGN_TOP_LEFT, 0, 0);
    s_home_schedule_label = label;

    static const char *items[] = {
        "> [ 拍题导学 ]      剩余 8 / 共 10",
        "  [ 语音提问 ]      说出你的想法",
        "  [ 学科卡片 ]      已收录 4 个包",
        "  [ 电子书架 ]      今日剩 45 分钟",
        "  [ 专注番茄 ]      25分钟倒计时",
        "  [ 待问队列 ]      0 条待同步",
        "  [ 系统设置 ]      WiFi/音量/OTA",
    };
    int i;
    for (i = 0; i < 7; i++) {
        lv_obj_t *row = ui_make_list_item(content, items[i], NULL);
        lv_obj_align(row, LV_ALIGN_TOP_LEFT, 0, 110 + i * 36);
    }
}

/* 2. 拍题取景 (PAGE_CAMERA) */
static void ui_build_camera(lv_obj_t *content)
{
    lv_obj_t *frame = lv_obj_create(content);
    lv_obj_set_size(frame, 280, 300);
    lv_obj_set_style_bg_opa(frame, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(frame, 2, 0);
    lv_obj_set_style_border_color(frame, UI_C_ACCENT, 0);
    lv_obj_clear_flag(frame, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_align(frame, LV_ALIGN_TOP_MID, 0, 0);

    lv_obj_t *tip = ui_make_label(content, "把题目放进取景框，保持平稳", false);
    lv_obj_align(tip, LV_ALIGN_BOTTOM_MID, 0, -8);
}

/* 3. 解析结果 (PAGE_SOLVE_RESULT) */
static void ui_build_solve_result(lv_obj_t *content)
{
    lv_obj_t *title = ui_make_label(content, "解题步骤", true);
    lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 0);

    lv_obj_t *body = ui_make_label(content,
        "1. 先圈出已知条件\n2. 找出它们之间的联系\n3. 一步一步往下推\n4. 回头检查每一步", false);
    lv_obj_align(body, LV_ALIGN_TOP_LEFT, 0, 36);
    s_solve_body = body;

    lv_obj_t *q = ui_make_label(content, "还有哪里没想明白？", false);
    lv_obj_align(q, LV_ALIGN_BOTTOM_LEFT, 0, -34);
    s_solve_question = q;

    lv_obj_t *menu = ui_make_list_item(content, "> [ 口述你的思路 ]", NULL);
    lv_obj_align(menu, LV_ALIGN_BOTTOM_LEFT, 0, 0);
}

/* 4. 学科卡片包列表 (PAGE_SUBJECT_LIST)
 *
 * 内容由 `ui_pages_refresh_subject_list()` 从 TF 卡的 /subjects 重建
 * （这里只放标题占位）—— 列表显示的是**真实已下载**的学科包，不是写死的假数据。
 */
static void ui_build_subject_list(lv_obj_t *content)
{
    lv_obj_t *title = ui_make_label(content, "学科包", true);
    lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 0);
}

/* 5. 翻卡学习 (PAGE_CARD_STUDY)
 *
 * 正面是题干，短按翻面看答案，长按退出（doc/03 §四 手势表）。
 * 左右键在卡片之间翻。
 */
static void ui_build_card_study(lv_obj_t *content)
{
    lv_obj_t *card = ui_make_card(content);
    lv_obj_set_size(card, 280, 260);
    lv_obj_align(card, LV_ALIGN_TOP_MID, 0, 0);
    lv_obj_t *label = ui_make_label(card, "还没有打开学科包", false);
    lv_obj_align(label, LV_ALIGN_TOP_LEFT, 0, 0);
    s_card_label = label;

    lv_obj_t *pos = ui_make_label(content, "", false);
    lv_obj_align(pos, LV_ALIGN_TOP_RIGHT, 0, 6);
    s_card_pos_label = pos;

    /* 菜单只有两项是**可确认**的（doc/03 §四）：翻面 / 语音追问。
     * 第三行是操作提示（左右键翻卡），用普通标签 —— 不是可选行。 */
    static const char *items[] = {
        "> [ 翻面 ]",
        "  [ 语音追问这题 ]",
    };
    int i;
    for (i = 0; i < 2; i++) {
        lv_obj_t *row = ui_make_list_item(content, items[i], NULL);
        lv_obj_align(row, LV_ALIGN_BOTTOM_LEFT, 0, -70 + i * 26);
    }
    lv_obj_t *hint = ui_make_label(content, "  左右键换一张", false);
    lv_obj_align(hint, LV_ALIGN_BOTTOM_LEFT, 0, -18);
}

/* 6. 番茄专注时钟 (PAGE_POMODORO) */
static void ui_build_pomodoro(lv_obj_t *content)
{
    lv_obj_t *time = ui_make_label(content, "25:00", true);
    lv_obj_align(time, LV_ALIGN_CENTER, 0, -20);
    s_pomo_time_label = time;

    lv_obj_t *sub = ui_make_label(content, "准备开始", false);
    lv_obj_align(sub, LV_ALIGN_CENTER, 0, 20);
    s_pomo_sub_label = sub;

    lv_obj_t *note = ui_make_label(content, "到点会响一声提示音", false);
    lv_obj_align(note, LV_ALIGN_BOTTOM_MID, 0, 0);
}

/* 7. 待问收件箱 (PAGE_OUTBOX) */
static void ui_build_outbox(lv_obj_t *content)
{
    lv_obj_t *title = ui_make_label(content, "待问队列 (0 / 10)", true);
    lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 0);

    lv_obj_t *empty = ui_make_label(content, "暂时没有待发送的问题", false);
    lv_obj_align(empty, LV_ALIGN_CENTER, 0, 0);
}

/* 8. 系统设置 (PAGE_SETTINGS) */
static void ui_build_settings(lv_obj_t *content)
{
    static const char *items[] = {
        "> 无线网络",
        "  音量",
        "  屏幕亮度",
        "  固件升级",
        "  电池与省电",
        "  关于设备",
    };
    int i;
    for (i = 0; i < 6; i++) {
        lv_obj_t *row = ui_make_list_item(content, items[i], NULL);
        lv_obj_align(row, LV_ALIGN_TOP_LEFT, 0, i * 40);
    }
    /* 第 7 项：专注时长（右侧显示当前分钟数） */
    lv_obj_t *focus_row = ui_make_list_item_ex(content, "  专注时长", "", &s_focus_value_label);
    lv_obj_align(focus_row, LV_ALIGN_TOP_LEFT, 0, 6 * 40);
    if (s_focus_value_label != NULL) {
        char buf[16];
        snprintf(buf, sizeof(buf), "%d 分钟", s_focus_minutes);
        lv_label_set_text(s_focus_value_label, buf);
    }
}

/* ===================== 9. Wi-Fi 扫描列表 (PAGE_WIFI_SCAN)
 *
 * 页面上显示的是**真扫描结果**：进页时向网络任务提交扫描请求，
 * 心跳里轮询缓存回来的结果，按信号强弱排序、同名合并，标出加密与已保存。
 * 底层扫描与连接都在 Core 0 的网络任务里做（doc/02 §4.1），UI 不阻塞。
 * ===================================================== */

#define WIFI_VISIBLE_ROWS 8   /* 屏幕里能同时显示的热点行数 */
#define WIFI_ROW_H        34  /* 行高（与别页的行高一致） */
#define WIFI_RAW_MAX      16  /* 一次扫描最多缓存的热点数 */

/* 静态缓冲：UI 核栈只有 4KB，列表构建路径不开大缓冲 */
static struct WifiApInfo s_wifi_raw[WIFI_RAW_MAX];
static struct WifiApInfo s_wifi_aps[WIFI_MODEL_MAX_APS];
static lv_obj_t *s_wifi_status_label = NULL;
static lv_obj_t *s_wifi_row_label[WIFI_VISIBLE_ROWS];
static lv_obj_t *s_wifi_rescan_label = NULL;
static int s_wifi_ap_count = 0;
static int s_wifi_cursor = 0;     /* 0..ap_count-1 = 热点；= ap_count 时是「重新扫描」 */
static int s_wifi_scroll = 0;      /* 列表滚动偏移 */
static wifi_conn_state_t s_wifi_conn;
static uint32_t s_wifi_result_seq = 0;     /* 已看过的连接结果序号 */
static uint32_t s_wifi_seen_scan_seq = 0;   /* 已看过的扫描结果序号 */
static char s_wifi_line[UI_ITEM_TEXT_MAX + 4];

/** 建一行「不登记进通用光标」的可选行（Wi-Fi 页与键盘页各自管光标）。 */
static lv_obj_t *ui_make_row(lv_obj_t *parent, lv_obj_t **out_label)
{
    lv_obj_t *row = lv_obj_create(parent);
    lv_obj_set_size(row, 300, WIFI_ROW_H);
    lv_obj_set_style_bg_opa(row, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(row, 0, 0);
    lv_obj_set_style_pad_all(row, 2, 0);
    lv_obj_clear_flag(row, LV_OBJ_FLAG_SCROLLABLE);

    lv_obj_t *label = lv_label_create(row);
    lv_label_set_text(label, "");
    lv_obj_add_style(label, &s_style_body, 0);
    lv_obj_align(label, LV_ALIGN_LEFT_MID, 0, 0);
    if (out_label != NULL) {
        *out_label = label;
    }
    return row;
}

static void ui_build_wifi_scan(lv_obj_t *content)
{
    lv_obj_t *title = ui_make_label(content, "附近的无线网络", true);
    lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 0);

    s_wifi_status_label = ui_make_label(content, "正在搜索附近的无线网络…", false);
    lv_obj_align(s_wifi_status_label, LV_ALIGN_TOP_LEFT, 0, 26);

    int i;
    for (i = 0; i < WIFI_VISIBLE_ROWS; i++) {
        lv_obj_t *row = ui_make_row(content, &s_wifi_row_label[i]);
        lv_obj_align(row, LV_ALIGN_TOP_LEFT, 0, 58 + i * WIFI_ROW_H);
    }

    /* 最后一行固定是「重新扫描」 */
    lv_obj_t *rescan = ui_make_row(content, &s_wifi_rescan_label);
    lv_obj_align(rescan, LV_ALIGN_TOP_LEFT, 0, 58 + WIFI_VISIBLE_ROWS * WIFI_ROW_H);
    lv_label_set_text(s_wifi_rescan_label, "  [ 重新扫描周边热点 ]");
}

/** 重画 Wi-Fi 列表（真数据 + 光标标记）。 */
static void ui_wifi_render_rows(void)
{
    char buf[UI_ITEM_TEXT_MAX];
    int i;
    for (i = 0; i < WIFI_VISIBLE_ROWS; i++) {
        int idx = s_wifi_scroll + i;
        if (idx >= s_wifi_ap_count) {
            lv_label_set_text(s_wifi_row_label[i], "");
            continue;
        }
        wifi_model_format_ap(buf, sizeof(buf), &s_wifi_aps[idx]);
        snprintf(s_wifi_line, sizeof(s_wifi_line), "%s%s",
                 (idx == s_wifi_cursor) ? "> " : "  ", buf);
        lv_label_set_text(s_wifi_row_label[i], s_wifi_line);
    }
    snprintf(s_wifi_line, sizeof(s_wifi_line), "%s[ 重新扫描周边热点 ]",
             (s_wifi_cursor == s_wifi_ap_count) ? "> " : "  ");
    lv_label_set_text(s_wifi_rescan_label, s_wifi_line);
}

/** 重画顶部状态行（连接中 / 成功 / 失败 / 没扫到）。 */
static void ui_wifi_render_status(void)
{
    if (s_wifi_status_label == NULL) {
        return;
    }
    const char *text = wifi_conn_hint(&s_wifi_conn);
    if (s_wifi_conn.phase == WIFI_CONN_SCANNING) {
        text = "正在搜索附近的无线网络…";
    } else if (s_wifi_conn.phase == WIFI_CONN_IDLE && s_wifi_ap_count == 0) {
        text = "附近没搜到无线网络，靠近路由器再试一次";
    } else if (s_wifi_conn.phase == WIFI_CONN_FAILED) {
        text = "无线网络连接失败，请核对密码后重试";
    }
    lv_label_set_text(s_wifi_status_label, text);
}

/** 把网络任务缓存回来的扫描结果整理成列表。 */
static void ui_wifi_take_scan_results(void)
{
    int n = net_wifi_get_cached_scan(s_wifi_raw, WIFI_RAW_MAX);
    if (n <= 0) {
        s_wifi_ap_count = 0;
        wifi_conn_scan_finish(&s_wifi_conn, 0);
        ui_wifi_render_rows();
        ui_wifi_render_status();
        return;
    }
    /* 排序 + 同名合并 + 标出已保存的网络 */
    n = wifi_model_sort_aps(s_wifi_raw, n);
    char saved[33];
    if (!net_wifi_get_saved_ssid(saved, sizeof(saved))) {
        saved[0] = '\0';
    }
    wifi_model_mark_saved(s_wifi_raw, n, saved);
    if (n > WIFI_MODEL_MAX_APS) {
        n = WIFI_MODEL_MAX_APS;
    }
    memcpy(s_wifi_aps, s_wifi_raw, (size_t)n * sizeof(struct WifiApInfo));
    s_wifi_ap_count = n;
    s_wifi_cursor = 0;
    s_wifi_scroll = 0;
    wifi_conn_scan_finish(&s_wifi_conn, n);
    ui_wifi_render_rows();
    ui_wifi_render_status();
}

void ui_pages_wifi_scan_enter(uint32_t now_ms)
{
    /* 从键盘输完密码刚回来：连接已经在进行中，别把状态冲掉（否则会白扫一次） */
    if (s_wifi_conn.phase == WIFI_CONN_CONNECTING) {
        ui_wifi_render_rows();
        ui_wifi_render_status();
        return;
    }
    wifi_conn_state_init(&s_wifi_conn);
    s_wifi_ap_count = 0;
    s_wifi_cursor = 0;
    s_wifi_scroll = 0;
    s_wifi_result_seq = net_wifi_connect_result_seq();
    net_wifi_request_scan();          /* UI 只提交请求 */
    wifi_conn_scan_start(&s_wifi_conn, now_ms);
    ui_wifi_render_rows();
    ui_wifi_render_status();
}

void ui_pages_wifi_scan_tick(uint32_t now_ms)
{
    /* 1) 扫描结果到了没 */
    if (s_wifi_conn.phase == WIFI_CONN_SCANNING) {
        uint32_t seq = net_wifi_scan_seq();
        if (seq != s_wifi_seen_scan_seq && net_wifi_scan_cache_ready()) {
            s_wifi_seen_scan_seq = seq;
            ui_wifi_take_scan_results();
        } else if (wifi_conn_scan_expired(&s_wifi_conn, now_ms)) {
            /* 扫太久没结果：给温和提示，不空屏 */
            wifi_conn_scan_finish(&s_wifi_conn, 0);
            ui_wifi_render_status();
        }
    }

    /* 2) 连接结果 */
    if (s_wifi_conn.phase == WIFI_CONN_CONNECTING) {
        if (net_wifi_connect_result_seq() != s_wifi_result_seq) {
            s_wifi_result_seq = net_wifi_connect_result_seq();
            bool ok = (net_wifi_connect_result() == 1);
            wifi_conn_connect_finish(&s_wifi_conn, ok);
            if (ok) {
                /* 连上了：刷新列表把刚连的网络标成「已保存」 */
                char saved[33];
                if (net_wifi_get_saved_ssid(saved, sizeof(saved))) {
                    wifi_model_mark_saved(s_wifi_aps, s_wifi_ap_count, saved);
                    ui_wifi_render_rows();
                }
            }
            ui_wifi_render_status();
        } else if (wifi_conn_connect_expired(&s_wifi_conn, now_ms)) {
            wifi_conn_connect_finish(&s_wifi_conn, false);
            ui_wifi_render_status();
        }
    }
}

int ui_pages_wifi_scan_move(int delta)
{
    int last = s_wifi_ap_count;   /* 最后一项是「重新扫描」 */
    int c = s_wifi_cursor + delta;
    if (c < 0) {
        c = 0;
    }
    if (c > last) {
        c = last;
    }
    s_wifi_cursor = c;
    /* 滚动：让光标始终在可见窗口里 */
    if (c < s_wifi_scroll) {
        s_wifi_scroll = c;
    } else if (c >= s_wifi_scroll + WIFI_VISIBLE_ROWS) {
        s_wifi_scroll = c - WIFI_VISIBLE_ROWS + 1;
    }
    ui_wifi_render_rows();
    return c;
}

void ui_pages_wifi_scan_connect(const char *ssid, const char *password, uint32_t now_ms)
{
    if (ssid == NULL || ssid[0] == '\0') {
        return;
    }
    wifi_conn_connect_start(&s_wifi_conn, ssid, now_ms);
    s_wifi_result_seq = net_wifi_connect_result_seq();
    net_wifi_submit_connect(ssid, password ? password : "");  /* 密码只进内存，不进日志 */
    ui_wifi_render_status();
}

void ui_pages_wifi_scan_action(uint32_t now_ms)
{
    if (s_wifi_conn.phase == WIFI_CONN_CONNECTING) {
        return;   /* 正在连，不重复发起 */
    }
    if (s_wifi_cursor >= s_wifi_ap_count) {
        /* 重新扫描 */
        s_wifi_ap_count = 0;
        s_wifi_cursor = 0;
        s_wifi_scroll = 0;
        net_wifi_request_scan();
        wifi_conn_scan_start(&s_wifi_conn, now_ms);
        ui_wifi_render_rows();
        ui_wifi_render_status();
        return;
    }
    const struct WifiApInfo *ap = &s_wifi_aps[s_wifi_cursor];
    if (ap->is_encrypted) {
        /* 加密网络：先进键盘输密码 */
        ui_pages_keyboard_enter(ap->ssid, true, now_ms);
        ui_router_go(PAGE_KEYBOARD);
        return;
    }
    ui_pages_wifi_scan_connect(ap->ssid, "", now_ms);
}

/* ===================== 10. 虚拟键盘 (PAGE_KEYBOARD)
 *
 * 真键盘：能移动光标、输入、退格、空格、大小写切换、显/隐、清空、确认。
 * 编辑逻辑全在 keypad_editor.c（纯逻辑，主机侧有测试），
 * 这一层只负责画键位与把摇杆动作翻译成编辑动作。
 * ===================================================== */

#define KEY_ROWS      6
#define KEY_COLS      10
#define KEY_ROW_H     38
#define KEY_TOP_Y     74
#define KEY_IDLE_MS   60000UL   /* 60 秒无操作退出（doc/03 §五 第 12 页） */

typedef enum {
    K_CHAR = 0,  /* 普通字符 */
    K_SHIFT,     /* 大小写切换 */
    K_SPACE,     /* 空格 */
    K_BACK,      /* 退格 */
    K_LEFT,      /* 光标左移 */
    K_RIGHT,     /* 光标右移 */
    K_MASK,      /* 显 / 隐 */
    K_CLEAR,     /* 清除 */
    K_OK,        /* 确认 */
} keypad_key_act_t;

typedef struct {
    const char *label;        /* 功能键的显示文字（普通字符键为 NULL） */
    char ch;                  /* 普通字符键的字符（小写） */
    keypad_key_act_t act;
} keypad_key_t;

static const keypad_key_t s_key_rows[KEY_ROWS][KEY_COLS] = {
    { { NULL, '1', K_CHAR }, { NULL, '2', K_CHAR }, { NULL, '3', K_CHAR },
      { NULL, '4', K_CHAR }, { NULL, '5', K_CHAR }, { NULL, '6', K_CHAR },
      { NULL, '7', K_CHAR }, { NULL, '8', K_CHAR }, { NULL, '9', K_CHAR },
      { NULL, '0', K_CHAR } },
    { { NULL, 'q', K_CHAR }, { NULL, 'w', K_CHAR }, { NULL, 'e', K_CHAR },
      { NULL, 'r', K_CHAR }, { NULL, 't', K_CHAR }, { NULL, 'y', K_CHAR },
      { NULL, 'u', K_CHAR }, { NULL, 'i', K_CHAR }, { NULL, 'o', K_CHAR },
      { NULL, 'p', K_CHAR } },
    { { NULL, 'a', K_CHAR }, { NULL, 's', K_CHAR }, { NULL, 'd', K_CHAR },
      { NULL, 'f', K_CHAR }, { NULL, 'g', K_CHAR }, { NULL, 'h', K_CHAR },
      { NULL, 'j', K_CHAR }, { NULL, 'k', K_CHAR }, { NULL, 'l', K_CHAR } },
    { { NULL, 'z', K_CHAR }, { NULL, 'x', K_CHAR }, { NULL, 'c', K_CHAR },
      { NULL, 'v', K_CHAR }, { NULL, 'b', K_CHAR }, { NULL, 'n', K_CHAR },
      { NULL, 'm', K_CHAR }, { NULL, ',', K_CHAR }, { NULL, '.', K_CHAR },
      { NULL, '-', K_CHAR } },
    { { "大小写", 0, K_SHIFT }, { "空格", 0, K_SPACE }, { "退格", 0, K_BACK },
      { "左移", 0, K_LEFT }, { "右移", 0, K_RIGHT } },
    { { "显/隐", 0, K_MASK }, { "清除", 0, K_CLEAR }, { "确认并连接", 0, K_OK } },
};

/* 每行的键数与格宽（合计不超过 300px 内容宽度） */
static const uint8_t s_key_count[KEY_ROWS] = { 10, 10, 9, 10, 5, 3 };
static const uint16_t s_key_w[KEY_ROWS] = { 30, 30, 33, 30, 60, 100 };

static lv_obj_t *s_key_obj[KEY_ROWS][KEY_COLS];
static lv_obj_t *s_key_label[KEY_ROWS][KEY_COLS];
static lv_obj_t *s_key_title_label = NULL;
static lv_obj_t *s_key_input_label = NULL;
static keypad_editor_t s_keypad;
static int s_key_row = 0;
static int s_key_col = 0;
static bool s_key_confirmed = false;
static uint32_t s_key_last_ms = 0;
static char s_key_input_line[KEYPAD_MAX_TEXT + 16];
static char s_key_title[64];
static char s_key_target[33];

static void ui_keyboard_render_input(void)
{
    char vis[KEYPAD_MAX_TEXT + 2];
    int n = keypad_editor_render(&s_keypad, vis, sizeof(vis));
    int c = s_keypad.cursor;
    if (c > n) {
        c = n;
    }
    /* 光标处插一个下划线 */
    memcpy(s_key_input_line, vis, (size_t)c);
    s_key_input_line[c] = '_';
    memcpy(s_key_input_line + c + 1, vis + c, (size_t)(n - c));
    s_key_input_line[n + 1] = '\0';
    if (s_key_input_label != NULL) {
        /* buf 要比「密码：」+ 输入行更长，否则 gcc 的 -Wformat-truncation 会报错 */
        char buf[KEYPAD_MAX_TEXT + 48];
        snprintf(buf, sizeof(buf), "密码：%s", s_key_input_line);
        lv_label_set_text(s_key_input_label, buf);
    }
}

static void ui_keyboard_render_keys(void)
{
    int r, c;
    for (r = 0; r < KEY_ROWS; r++) {
        for (c = 0; c < s_key_count[r]; c++) {
            if (s_key_label[r][c] == NULL) {
                continue;
            }
            const keypad_key_t *k = &s_key_rows[r][c];
            char buf[32];
            if (k->act == K_CHAR) {
                buf[0] = keypad_editor_key_char(&s_keypad, k->ch);
                buf[1] = '\0';
            } else {
                snprintf(buf, sizeof(buf), "%s", k->label);
            }
            lv_label_set_text(s_key_label[r][c], buf);
            bool sel = (r == s_key_row && c == s_key_col);
            lv_obj_set_style_text_color(s_key_label[r][c], sel ? UI_C_ACCENT : UI_C_TEXT, 0);
            lv_obj_set_style_bg_color(s_key_obj[r][c], sel ? UI_C_CARD : UI_C_BG, 0);
            lv_obj_set_style_bg_opa(s_key_obj[r][c], sel ? LV_OPA_COVER : LV_OPA_TRANSP, 0);
        }
    }
}

static void ui_build_keyboard(lv_obj_t *content)
{
    s_key_title_label = ui_make_label(content, "输入密码", true);
    lv_obj_align(s_key_title_label, LV_ALIGN_TOP_MID, 0, 0);

    s_key_input_label = ui_make_label(content, "密码：", false);
    lv_obj_align(s_key_input_label, LV_ALIGN_TOP_LEFT, 0, 28);

    int r, c;
    for (r = 0; r < KEY_ROWS; r++) {
        for (c = 0; c < s_key_count[r]; c++) {
            lv_obj_t *obj = lv_obj_create(content);
            lv_obj_set_size(obj, s_key_w[r] - 2, 32);
            lv_obj_set_pos(obj, c * s_key_w[r], KEY_TOP_Y + r * KEY_ROW_H);
            lv_obj_set_style_bg_opa(obj, LV_OPA_TRANSP, 0);
            lv_obj_set_style_border_width(obj, 1, 0);
            lv_obj_set_style_border_color(obj, UI_C_LINE, 0);
            lv_obj_set_style_radius(obj, 4, 0);
            lv_obj_set_style_pad_all(obj, 0, 0);
            lv_obj_clear_flag(obj, LV_OBJ_FLAG_SCROLLABLE);

            lv_obj_t *label = lv_label_create(obj);
            lv_label_set_text(label, "");
            lv_obj_add_style(label, &s_style_body, 0);
            lv_obj_align(label, LV_ALIGN_CENTER, 0, 0);
            s_key_obj[r][c] = obj;
            s_key_label[r][c] = label;
        }
    }
}

void ui_pages_keyboard_enter(const char *title, bool masked, uint32_t now_ms)
{
    keypad_editor_init(&s_keypad);
    keypad_editor_set_mask(&s_keypad, masked);
    s_key_row = 0;
    s_key_col = 0;
    s_key_confirmed = false;
    s_key_last_ms = now_ms;
    snprintf(s_key_target, sizeof(s_key_target), "%s",
             (title != NULL) ? title : "");
    snprintf(s_key_title, sizeof(s_key_title), "输入密码：%s",
             (s_key_target[0] != '\0') ? s_key_target : "网络");
    if (s_key_title_label != NULL) {
        lv_label_set_text(s_key_title_label, s_key_title);
    }
    ui_keyboard_render_input();
    ui_keyboard_render_keys();
}

void ui_pages_keyboard_move(int dx, int dy, uint32_t now_ms)
{
    s_key_last_ms = now_ms;
    if (dy != 0) {
        int r = s_key_row + dy;
        if (r < 0) {
            r = 0;
        }
        if (r >= KEY_ROWS) {
            r = KEY_ROWS - 1;
        }
        s_key_row = r;
        if (s_key_col >= s_key_count[s_key_row]) {
            s_key_col = s_key_count[s_key_row] - 1;
        }
    }
    if (dx != 0) {
        int c = s_key_col + dx;
        if (c < 0) {
            c = 0;
        }
        if (c >= s_key_count[s_key_row]) {
            c = s_key_count[s_key_row] - 1;
        }
        s_key_col = c;
    }
    ui_keyboard_render_keys();
}

void ui_pages_keyboard_action(uint32_t now_ms)
{
    s_key_last_ms = now_ms;
    const keypad_key_t *k = &s_key_rows[s_key_row][s_key_col];
    switch (k->act) {
    case K_CHAR:
        keypad_editor_type_key(&s_keypad, k->ch);
        break;
    case K_SHIFT:
        keypad_editor_toggle_shift(&s_keypad);
        break;
    case K_SPACE:
        keypad_editor_space(&s_keypad);
        break;
    case K_BACK:
        keypad_editor_backspace(&s_keypad);
        break;
    case K_LEFT:
        keypad_editor_move_left(&s_keypad);
        break;
    case K_RIGHT:
        keypad_editor_move_right(&s_keypad);
        break;
    case K_MASK:
        keypad_editor_toggle_mask(&s_keypad);
        break;
    case K_CLEAR:
        keypad_editor_clear(&s_keypad);
        break;
    case K_OK:
        s_key_confirmed = true;
        break;
    default:
        break;
    }
    ui_keyboard_render_input();
    ui_keyboard_render_keys();
}

bool ui_pages_keyboard_confirmed(void)
{
    return s_key_confirmed;
}

const char *ui_pages_keyboard_text(void)
{
    return keypad_editor_text(&s_keypad);
}

const char *ui_pages_keyboard_target(void)
{
    return s_key_target;
}

bool ui_pages_keyboard_tick(uint32_t now_ms)
{
    if (s_key_last_ms == 0) {
        s_key_last_ms = now_ms;
        return false;
    }
    return (uint32_t)(now_ms - s_key_last_ms) > KEY_IDLE_MS;
}
void ui_pages_keyboard_leave(void)
{
    /* 清掉内存里的明文，别留在 buffer 里 */
    keypad_editor_clear(&s_keypad);
    s_key_confirmed = false;
}

/* 11. 固件升级 (PAGE_OTA)
 *
 * 正文（当前版本 / 最新版本 / 更新说明 / 下载进度 / 结果提示）是**真实数据**：
 * 由 src/ui/ui_ota_page.cpp 持有并定时刷新，数据源是网络任务写的 OTA 状态。
 */
static void ui_build_ota(lv_obj_t *content)
{
    lv_obj_t *title = ui_make_label(content, "固件升级", true);
    lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 0);

    ui_ota_page_build(content);

    lv_obj_t *item = ui_make_list_item(content, "> [ 检查更新 ]", NULL);
    lv_obj_align(item, LV_ALIGN_BOTTOM_MID, 0, 0);
}

/* ===================== 学科卡片：公开接口 ===================== */

/** 刷新学科包列表（读卡上 /subjects 下的真实学科包目录）。 */
void ui_pages_refresh_subject_list(void)
{
    lv_obj_t *content = ui_page_content(PAGE_SUBJECT_LIST);
    if (content == NULL) {
        return;
    }
    lv_obj_clean(content);
    s_pack_count = 0;
    s_item_count[PAGE_SUBJECT_LIST] = 0;
    s_cursor[PAGE_SUBJECT_LIST] = 0;

    lv_obj_t *title = ui_make_label(content, "学科包", true);
    lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 0);

    ui_data_file_t files[UI_DATA_MAX_FILES];
    uint32_t count = 0;
    int rc = ui_data_list_files(UI_DATA_DIR_SUBJECTS, files, UI_DATA_MAX_FILES, &count);
    if (rc != UI_DATA_OK) {
        lv_obj_t *empty = ui_make_label(content, "暂时读不到学科包", false);
        lv_obj_align(empty, LV_ALIGN_CENTER, 0, 0);
        return;
    }

    uint32_t i;
    /* 构建期登记可选行 —— 这一页是**运行时重建**的，
       所以要自己把 s_build_page 置上（否则光标找不到行）。 */
    s_build_page = PAGE_SUBJECT_LIST;
    for (i = 0; i < count && s_pack_count < UI_MAX_PACKS; i++) {
        const char *name = files[i].name;
        if (name[0] == '\0' || strcmp(name, ".") == 0 || strcmp(name, "..") == 0) {
            continue;
        }
        size_t n = strlen(name);
        if (n >= UI_PACK_ID_MAX) {
            continue;
        }
        snprintf(s_pack_ids[s_pack_count], UI_PACK_ID_MAX, "%s", name);
        char row_text[UI_PACK_ID_MAX + 4];
        snprintf(row_text, sizeof(row_text), "  %s", name);
        lv_obj_t *row = ui_make_list_item(content, row_text, "已下载");
        lv_obj_align(row, LV_ALIGN_TOP_LEFT, 0, 44 + s_pack_count * 40);
        s_pack_count++;
    }
    s_build_page = -1;
    if (s_pack_count == 0) {
        lv_obj_t *empty = ui_make_label(content, "还没有下载学科包", false);
        lv_obj_align(empty, LV_ALIGN_CENTER, 0, 0);
        return;
    }
    ui_cursor_render(PAGE_SUBJECT_LIST);
}

/** 当前选中的学科包 id（没有则空串）。 */
const char *ui_pages_selected_pack_id(void)
{
    int idx = ui_pages_cursor_index(PAGE_SUBJECT_LIST);
    if (idx < 0 || idx >= s_pack_count) {
        return "";
    }
    return s_pack_ids[idx];
}

/** 载入卡片集（UI 持有指针，不拷贝 —— 上层持有所有权）。 */
void ui_pages_set_cards(const card_pack_t *pack)
{
    s_cards = pack;
    s_card_index = 0;
    s_card_show_answer = false;
    ui_pages_show_card(0, false);
}

int ui_pages_card_count(void)
{
    return (s_cards != NULL) ? s_cards->count : 0;
}

int ui_pages_card_index(void)
{
    return s_card_index;
}

bool ui_pages_card_answer_shown(void)
{
    return s_card_show_answer;
}

/** 显示第 index 张卡（show_answer = true 显示背面）。 */
void ui_pages_show_card(int index, bool show_answer)
{
    if (s_cards == NULL || index < 0 || index >= s_cards->count) {
        return;
    }
    s_card_index = index;
    s_card_show_answer = show_answer;
    const card_item_t *item = &s_cards->items[index];
    if (s_card_label != NULL) {
        lv_label_set_text(s_card_label, show_answer ? item->answer : item->question);
    }
    if (s_card_pos_label != NULL) {
        char buf[48];
        snprintf(buf, sizeof(buf), "第 %d / %d 张", index + 1, s_cards->count);
        lv_label_set_text(s_card_pos_label, buf);
    }
}

/* ===================== 电子书子页面 (doc/07) ===================== */

static void ui_build_bookshelf(lv_obj_t *content)
{
    /* 书架内容由 ui_pages_refresh_bookshelf() 从 TF 卡重建（这里只放标题占位） */
    lv_obj_t *title = ui_make_label(content, "我的书架", true);
    lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 0);
}

static void ui_build_reader(lv_obj_t *content)
{
    lv_obj_t *body = ui_make_label(content, "还没有打开书籍", false);
    lv_obj_align(body, LV_ALIGN_TOP_LEFT, 0, 0);
    s_reader_body = body;
}

static void ui_build_book_menu(lv_obj_t *content)
{
    static const char *items[] = {
        "> 目录",
        "  跳转",
        "  亮度",
        "  退出阅读",
    };
    int i;
    for (i = 0; i < 4; i++) {
        lv_obj_t *row = ui_make_list_item(content, items[i], NULL);
        lv_obj_align(row, LV_ALIGN_TOP_LEFT, 0, i * 44);
    }
}

static void ui_build_book_toc(lv_obj_t *content)
{
    static const char *items[] = {
        "> 第一章  帽子",
        "  第二章  玫瑰",
        "  第三章  星球",
    };
    int i;
    for (i = 0; i < 3; i++) {
        lv_obj_t *row = ui_make_list_item(content, items[i], NULL);
        lv_obj_align(row, LV_ALIGN_TOP_LEFT, 0, i * 44);
    }
}

static void ui_build_book_jump(lv_obj_t *content)
{
    lv_obj_t *title = ui_make_label(content, "跳转到", true);
    lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 0);

    lv_obj_t *bar = ui_make_label(content, "████████░░░░░░░░  50%", false);
    lv_obj_align(bar, LV_ALIGN_CENTER, 0, 0);
}

static void ui_build_book_locked(lv_obj_t *content)
{
    lv_obj_t *title = ui_make_label(content, "该休息啦", true);
    lv_obj_align(title, LV_ALIGN_CENTER, 0, -20);
    s_locked_title = title;

    lv_obj_t *body = ui_make_label(content, "现在是阅读休息时段\n明早再来读书吧", false);
    lv_obj_align(body, LV_ALIGN_CENTER, 0, 12);
    s_locked_body = body;
}

/* ===================== 页面表 ===================== */

typedef void (*ui_page_builder_t)(lv_obj_t *content);

static const ui_page_builder_t s_page_builders[PAGE_COUNT] = {
    [PAGE_HOME]         = ui_build_home,
    [PAGE_OOBE]         = ui_build_oobe,
    [PAGE_WIFI_SCAN]    = ui_build_wifi_scan,
    [PAGE_SETTINGS]     = ui_build_settings,
    [PAGE_SUBJECT_LIST] = ui_build_subject_list,
    [PAGE_CARD_STUDY]   = ui_build_card_study,
    [PAGE_CAMERA]       = ui_build_camera,
    [PAGE_SOLVE_RESULT] = ui_build_solve_result,
    [PAGE_OUTBOX]       = ui_build_outbox,
    [PAGE_KEYBOARD]     = ui_build_keyboard,
    [PAGE_OTA]          = ui_build_ota,
    [PAGE_POMODORO]     = ui_build_pomodoro,
};

static const ui_page_builder_t s_book_builders[PAGE_BOOK_COUNT - PAGE_BOOKSHELF] = {
    [PAGE_BOOKSHELF - PAGE_BOOKSHELF] = ui_build_bookshelf,
    [PAGE_READER - PAGE_BOOKSHELF]    = ui_build_reader,
    [PAGE_BOOK_MENU - PAGE_BOOKSHELF] = ui_build_book_menu,
    [PAGE_BOOK_TOC - PAGE_BOOKSHELF]  = ui_build_book_toc,
    [PAGE_BOOK_JUMP - PAGE_BOOKSHELF] = ui_build_book_jump,
    [PAGE_BOOK_LOCKED - PAGE_BOOKSHELF] = ui_build_book_locked,
};

/* 各页底部提示文案 (doc/03 §五) */
static const char *s_page_hints[PAGE_COUNT] = {
    [PAGE_HOME]         = "上下移动 | 短按确定 | 长按退出",
    [PAGE_OOBE]         = "上下选择 | 短按确定",
    [PAGE_WIFI_SCAN]    = "上下选择 | 短按连接 | 长按退出",
    [PAGE_SETTINGS]     = "上下选择 | 左右调时长 | 短按确定 | 长按退出",
    [PAGE_SUBJECT_LIST] = "上下选择 | 短按进入 | 长按退出",
    [PAGE_CARD_STUDY]   = "左右翻卡 | 短按确定 | 长按退出",
    [PAGE_CAMERA]       = "短按拍照 | 长按退出",
    [PAGE_SOLVE_RESULT] = "上下滚动 | 短按确定 | 长按退出",
    [PAGE_OUTBOX]       = "上下选择 | 短按确定 | 长按退出",
    [PAGE_KEYBOARD]     = "上下选择 | 短按输入 | 长按退出",
    [PAGE_OTA]          = "上下选择 | 短按确定 | 长按退出",
    [PAGE_POMODORO]     = "短按暂停/继续 | 长按结束退出",
};

static const char *s_book_hints[PAGE_BOOK_COUNT - PAGE_BOOKSHELF] = {
    [PAGE_BOOKSHELF - PAGE_BOOKSHELF] = "上下选择 | 短按打开 | 长按退出",
    [PAGE_READER - PAGE_BOOKSHELF]    = "左右翻页 | 上下调亮度 | 长按退出",
    [PAGE_BOOK_MENU - PAGE_BOOKSHELF] = "上下选择 | 短按确定 | 长按退出",
    [PAGE_BOOK_TOC - PAGE_BOOKSHELF]  = "左右翻目录 | 短按跳转 | 长按退出",
    [PAGE_BOOK_JUMP - PAGE_BOOKSHELF] = "左右微调 | 长按退出",
    [PAGE_BOOK_LOCKED - PAGE_BOOKSHELF] = "长按退出",
};

/* ===================== 浮层 ===================== */

static void ui_build_overlays(lv_obj_t *parent)
{
    /* 提醒弹窗: 最高优先级, 居中卡片 */
    {
        lv_obj_t *root = lv_obj_create(parent);
        lv_obj_set_size(root, 320, 480);
        lv_obj_set_style_bg_color(root, lv_color_hex(0x000000), 0);
        lv_obj_set_style_bg_opa(root, LV_OPA_50, 0);
        lv_obj_clear_flag(root, LV_OBJ_FLAG_SCROLLABLE);
        lv_obj_add_flag(root, LV_OBJ_FLAG_HIDDEN);

        lv_obj_t *card = lv_obj_create(root);
        lv_obj_set_size(card, 280, 160);
        lv_obj_remove_style_all(card);
        lv_obj_add_style(card, &s_style_card, LV_PART_MAIN);
        lv_obj_align(card, LV_ALIGN_CENTER, 0, 0);

        lv_obj_t *label = lv_label_create(card);
        lv_label_set_text(label, "课程提醒");
        lv_obj_add_style(label, &s_style_title, 0);
        lv_obj_align(label, LV_ALIGN_TOP_LEFT, 0, 0);

        s_overlays[OVERLAY_REMINDER].root = root;
        s_overlays[OVERLAY_REMINDER].label = label;
    }

    /* 录音浮层: 中优先级, 顶部波形 + 倒计时 */
    {
        lv_obj_t *root = lv_obj_create(parent);
        lv_obj_set_size(root, 320, 120);
        lv_obj_set_pos(root, 0, 0);
        lv_obj_set_style_bg_color(root, lv_color_hex(0x1F2430), 0);
        lv_obj_set_style_bg_opa(root, LV_OPA_COVER, 0);
        lv_obj_set_style_border_width(root, 0, 0);
        lv_obj_clear_flag(root, LV_OBJ_FLAG_SCROLLABLE);
        lv_obj_add_flag(root, LV_OBJ_FLAG_HIDDEN);

        lv_obj_t *label = lv_label_create(root);
        lv_label_set_text(label, "正在录音…  短按结束  上推取消");
        lv_obj_add_style(label, &s_style_body, 0);
        lv_obj_align(label, LV_ALIGN_LEFT_MID, 0, 0);

        s_overlays[OVERLAY_RECORDING].root = root;
        s_overlays[OVERLAY_RECORDING].label = label;
    }

    /* 提示条: 最低优先级, 底部 2 秒 */
    {
        lv_obj_t *root = lv_obj_create(parent);
        lv_obj_set_size(root, 320, 40);
        lv_obj_set_pos(root, 0, 440);
        lv_obj_set_style_bg_color(root, lv_color_hex(0x1F2430), 0);
        lv_obj_set_style_bg_opa(root, LV_OPA_COVER, 0);
        lv_obj_set_style_border_width(root, 0, 0);
        lv_obj_clear_flag(root, LV_OBJ_FLAG_SCROLLABLE);
        lv_obj_add_flag(root, LV_OBJ_FLAG_HIDDEN);

        lv_obj_t *label = lv_label_create(root);
        lv_label_set_text(label, "");
        lv_obj_add_style(label, &s_style_body, 0);
        lv_obj_align(label, LV_ALIGN_LEFT_MID, 0, 0);

        s_overlays[OVERLAY_TOAST].root = root;
        s_overlays[OVERLAY_TOAST].label = label;
    }
}

/* ===================== 对外接口 ===================== */

void ui_pages_init(lv_obj_t *parent)
{
    ui_styles_init();
    ui_build_overlays(parent);

    int i;
    for (i = 0; i < PAGE_COUNT; i++) {
        lv_obj_t *root = ui_make_root(parent);
        s_pages[i].root = root;
        s_pages[i].status = ui_make_status(root, "                  ", &s_pages[i].status_label);
        s_pages[i].content = ui_make_content(root);
        s_pages[i].hint = ui_make_hint(root,
            (s_page_hints[i] != NULL) ? s_page_hints[i] : "");
        s_build_page = i;   /* 构建期间登记可选行 */
        s_item_count[i] = 0;
        s_cursor[i] = 0;
        if (s_page_builders[i] != NULL) {
            s_page_builders[i](s_pages[i].content);
        }
        s_build_page = -1;
        lv_obj_add_flag(root, LV_OBJ_FLAG_HIDDEN);
    }

    for (i = 0; i < PAGE_BOOK_COUNT - PAGE_BOOKSHELF; i++) {
        lv_obj_t *root = ui_make_root(parent);
        s_book_pages[i].root = root;
        s_book_pages[i].status = ui_make_status(root, "                  ",
                                               &s_book_pages[i].status_label);
        s_book_pages[i].content = ui_make_content(root);
        s_book_pages[i].hint = ui_make_hint(root, s_book_hints[i]);
        if (s_book_builders[i] != NULL) {
            s_book_builders[i](s_book_pages[i].content);
        }
        lv_obj_add_flag(root, LV_OBJ_FLAG_HIDDEN);
    }
}

/* ===================== 列表光标 ===================== */

static void ui_cursor_render(ui_page_t page)
{
    if (page < 0 || page >= (int)PAGE_COUNT) {
        return;
    }
    for (uint8_t i = 0; i < s_item_count[page]; i++) {
        if (s_items[page][i].label == NULL) {
            continue;
        }
        char buf[UI_ITEM_TEXT_MAX + 4];
        snprintf(buf, sizeof(buf), "%s%s", (i == s_cursor[page]) ? "> " : "  ",
                 s_items[page][i].text);
        lv_label_set_text(s_items[page][i].label, buf);
    }
}

int ui_pages_cursor_move(ui_page_t page, int delta)
{
    if (page < 0 || page >= (int)PAGE_COUNT || s_item_count[page] == 0) {
        return -1;
    }
    int c = (int)s_cursor[page] + delta;
    if (c < 0) {
        c = 0;
    }
    if (c > (int)s_item_count[page] - 1) {
        c = (int)s_item_count[page] - 1;
    }
    s_cursor[page] = (uint8_t)c;
    ui_cursor_render(page);
    return c;
}

int ui_pages_cursor_index(ui_page_t page)
{
    if (page < 0 || page >= (int)PAGE_COUNT || s_item_count[page] == 0) {
        return -1;
    }
    return (int)s_cursor[page];
}

void ui_pages_cursor_reset(ui_page_t page)
{
    if (page < 0 || page >= (int)PAGE_COUNT) {
        return;
    }
    s_cursor[page] = 0;
    ui_cursor_render(page);
}

/* ===================== 番茄钟（doc/03 §五 第 8 页） ===================== */

/* 首次用到时才去读卡（定义在“专注时长”段） */
static void ui_focus_load(void);

/** 刷新番茄钟页面上的大字与状态行。 */
static void ui_pomodoro_render(void)
{
    if (s_pomo_time_label == NULL) {
        return;
    }
    uint32_t sec = (s_pomo_remain_ms + 999u) / 1000u;
    char buf[24];
    snprintf(buf, sizeof(buf), "%02lu:%02lu", (unsigned long)(sec / 60u),
             (unsigned long)(sec % 60u));
    lv_label_set_text(s_pomo_time_label, buf);

    if (s_pomo_sub_label == NULL) {
        return;
    }
    const char *sub = "专注中";
    if (s_pomo_state == POMO_PAUSED) {
        sub = "已暂停";
    } else if (s_pomo_state == POMO_DONE) {
        sub = "已完成，休息一下吧";
    } else if (s_pomo_state == POMO_IDLE) {
        sub = "准备开始";
    }
    lv_label_set_text(s_pomo_sub_label, sub);
}

void ui_pages_pomodoro_enter(void)
{
    /* 时长取本地设置（默认 25 分钟；已调过就直接用内存里的值） */
    ui_focus_load();
    int minutes = s_focus_minutes;
    if (minutes < UI_DATA_FOCUS_MIN) {
        minutes = UI_DATA_FOCUS_MIN;
    }
    if (minutes > UI_DATA_FOCUS_MAX) {
        minutes = UI_DATA_FOCUS_MAX;
    }
    s_pomo_total_ms = (uint32_t)minutes * 60000u;
    s_pomo_remain_ms = s_pomo_total_ms;
    s_pomo_state = POMO_RUNNING;
    s_pomo_tick_ready = false;
    ui_pomodoro_render();
}

void ui_pages_pomodoro_tick(uint32_t now_ms)
{
    if (s_pomo_state != POMO_RUNNING) {
        return;
    }
    if (!s_pomo_tick_ready) {
        /* 首帧只对齐基准，不计时间（暂停/继续后也一样） */
        s_pomo_last_ms = now_ms;
        s_pomo_tick_ready = true;
        return;
    }
    /* 毫秒差值（uint32 回绕安全），不做秒级累加 —— 免得长跑漂移 */
    uint32_t delta = now_ms - s_pomo_last_ms;
    s_pomo_last_ms = now_ms;
    if (delta == 0) {
        return;
    }
    if (delta >= s_pomo_remain_ms) {
        s_pomo_remain_ms = 0;
    } else {
        s_pomo_remain_ms -= delta;
    }
    if (s_pomo_remain_ms == 0) {
        s_pomo_state = POMO_DONE;
        s_pomo_tick_ready = false;
        /* 到点出声：只投递请求给音频任务，UI 不阻塞 */
        hal_audio_request_tone(880, 800);
    }
    ui_pomodoro_render();
}

void ui_pages_pomodoro_toggle(void)
{
    switch (s_pomo_state) {
    case POMO_RUNNING:
        s_pomo_state = POMO_PAUSED;
        s_pomo_tick_ready = false;
        break;
    case POMO_PAUSED:
        s_pomo_state = POMO_RUNNING;
        s_pomo_tick_ready = false;
        break;
    case POMO_DONE:
        /* 完成后再来一次短按 = 重新开始一轮 */
        s_pomo_remain_ms = s_pomo_total_ms;
        s_pomo_state = POMO_RUNNING;
        s_pomo_tick_ready = false;
        break;
    case POMO_IDLE:
    default:
        break;
    }
    ui_pomodoro_render();
}

void ui_pages_pomodoro_stop(void)
{
    s_pomo_state = POMO_IDLE;
    s_pomo_remain_ms = 0;
    s_pomo_tick_ready = false;
    ui_pomodoro_render();
}

/* ===================== 专注时长（PAGE_SETTINGS） ===================== */

/** 首次用到时才去读卡（读卡要走存储任务，不能在开机路径上干等）。 */
static void ui_focus_load(void)
{
    if (s_focus_loaded) {
        return;
    }
    s_focus_loaded = true;
    int minutes = UI_DATA_FOCUS_DEFAULT;
    if (ui_data_focus_minutes_get(&minutes) == UI_DATA_OK) {
        s_focus_minutes = minutes;
    }
    if (s_focus_minutes < UI_DATA_FOCUS_MIN) {
        s_focus_minutes = UI_DATA_FOCUS_MIN;
    }
    if (s_focus_minutes > UI_DATA_FOCUS_MAX) {
        s_focus_minutes = UI_DATA_FOCUS_MAX;
    }
    if (s_focus_value_label != NULL) {
        char buf[16];
        snprintf(buf, sizeof(buf), "%d 分钟", s_focus_minutes);
        lv_label_set_text(s_focus_value_label, buf);
    }
}

int ui_pages_focus_minutes(void)
{
    ui_focus_load();
    return s_focus_minutes;
}

void ui_pages_focus_adjust(int delta)
{
    int minutes = s_focus_minutes + (delta * UI_DATA_FOCUS_STEP);
    if (minutes < UI_DATA_FOCUS_MIN) {
        minutes = UI_DATA_FOCUS_MIN;
    }
    if (minutes > UI_DATA_FOCUS_MAX) {
        minutes = UI_DATA_FOCUS_MAX;
    }
    if (minutes == s_focus_minutes) {
        return;
    }
    s_focus_minutes = minutes;
    /* 落盘：原子写 + .bak 自愈（doc/04 §2.3），不放 NVS */
    ui_data_focus_minutes_set(minutes);
    if (s_focus_value_label != NULL) {
        char buf[16];
        snprintf(buf, sizeof(buf), "%d 分钟", s_focus_minutes);
        lv_label_set_text(s_focus_value_label, buf);
    }
}

/* ===================== 首页今日课程（读 /config/schedule.json） ===================== */

/** 星期几（中文）。入参是公历日期。 */
static const char *ui_weekday_cn(const ui_data_date_t *d)
{
    static const char *names[] = { "周日", "周一", "周二", "周三", "周四", "周五", "周六" };
    static const int t[] = { 0, 3, 2, 5, 0, 3, 5, 1, 4, 6, 2, 4 };
    int y = d->year;
    int m = d->month;
    if (m < 1 || m > 12) {
        return "";
    }
    if (m < 3) {
        y -= 1;
    }
    int w = (y + y / 4 - y / 100 + y / 400 + t[m - 1] + d->day) % 7;
    if (w < 0) {
        w += 7;
    }
    return names[w];
}

void ui_pages_refresh_home(void)
{
    if (s_home_schedule_label == NULL) {
        return;
    }
    static char buf[512];
    ui_data_date_t today;
    ui_data_lesson_t lessons[UI_DATA_MAX_LESSONS];
    uint32_t count = 0;

    int rc = UI_DATA_ERR_NOT_FOUND;
    if (ui_data_today(&today) == UI_DATA_OK) {
        rc = ui_data_read_schedule(&today, lessons, UI_DATA_MAX_LESSONS, &count);
    }
    if (rc != UI_DATA_OK) {
        lv_label_set_text(s_home_schedule_label, "今日课程\n暂时读不到课表");
        return;
    }
    if (count == 0) {
        snprintf(buf, sizeof(buf), "今日课程 (%s)\n今天没有课程安排", ui_weekday_cn(&today));
        lv_label_set_text(s_home_schedule_label, buf);
        return;
    }

    size_t pos = 0;
    int n = snprintf(buf + pos, sizeof(buf) - pos, "今日课程 (%s)\n", ui_weekday_cn(&today));
    pos += (n > 0) ? (size_t)n : 0;
    uint32_t i;
    for (i = 0; i < count && i < 4 && pos < sizeof(buf); i++) {
        n = snprintf(buf + pos, sizeof(buf) - pos, "%u.%s %s-%s\n",
                     (unsigned)(i + 1), lessons[i].subject, lessons[i].start,
                     lessons[i].end);
        if (n <= 0) {
            break;
        }
        pos += (size_t)n;
    }
    if (count > 4 && pos < sizeof(buf)) {
        snprintf(buf + pos, sizeof(buf) - pos, "……");
    }
    lv_label_set_text(s_home_schedule_label, buf);
}

/* ===================== 书架与阅读页（doc/07） ===================== */

/* ===================== 阅读护栏：进度表与配置读写 ===================== */

/**
 * 读 /records/bookmarks.json；主文件坏了就退回 .bak（doc/07 §2.3 的自愈路径）。
 *
 * 只走 ui_data（存储任务请求/应答）—— UI 任务不碰 SPI（doc/02 §4.2）。
 */
static void ui_book_load_marks(void)
{
    uint32_t n1 = 0;
    uint32_t n2 = 0;

    s_bookmarks_buf[0] = '\0';
    s_bookmarks_bak_buf[0] = '\0';
    int r1 = ui_data_read_text(UI_DATA_PATH_BOOKMARKS, s_bookmarks_buf,
                               (uint32_t)sizeof(s_bookmarks_buf), &n1);
    int r2 = ui_data_read_text(UI_DATA_PATH_BOOKMARKS_BAK, s_bookmarks_bak_buf,
                               (uint32_t)sizeof(s_bookmarks_bak_buf), &n2);
    if (r1 < 0) {
        n1 = 0;
    }
    if (r2 < 0) {
        n2 = 0;
    }
    const char *json = s_bookmarks_buf;
    size_t len = (size_t)n1;
    if (ui_book_pick_source(s_bookmarks_buf, (size_t)n1, s_bookmarks_bak_buf, (size_t)n2,
                             &json, &len) < 0) {
        /* 两份都不可用：当作没有进度（不编造） */
        json = "";
        len = 0;
    }
    ui_bookmarks_parse(json, len, &s_bookmarks);
    if (s_bookmarks.schema_version == 0) {
        s_bookmarks.schema_version = 1;
    }
    s_bookmarks_loaded = true;
}

/** 读护栏配置（/config/profile.json）；读不到就用默认值（22:00 / 06:30 / 45 分钟）。 */
static void ui_book_load_guard(void)
{
    static char buf[1024];
    uint32_t n = 0;

    ui_book_guard_defaults(&s_book_guard);
    int rc = ui_data_read_text(UI_DATA_PATH_PROFILE, buf, (uint32_t)sizeof(buf), &n);
    if (rc >= 0 && n > 0) {
        ui_book_guard_parse(buf, (size_t)n, &s_book_guard);
    }
    s_book_guard_loaded = true;
}

/** 把进度表写回 /records/bookmarks.json（原子写：.tmp → .bak → 改名，doc/04 §2.3）。 */
static void ui_book_save_marks(void)
{
    int n = ui_bookmarks_format(&s_bookmarks, s_bookmarks_out, sizeof(s_bookmarks_out));
    if (n <= 0) {
        return;
    }
    ui_data_write_text(UI_DATA_PATH_BOOKMARKS, s_bookmarks_out, (uint32_t)n);
}

/** 记下当前这本书的进度并落盘（翻页停顿即自动保存 → 下次打开秒回原位）。 */
static void ui_book_save_progress(void)
{
    if (!s_bookmarks_loaded) {
        ui_book_load_marks();
    }
    if (s_reader_path[0] != '\0' && s_reader_size > 0) {
        const char *name = strrchr(s_reader_path, '/');
        name = (name != NULL) ? name + 1 : s_reader_path;
        ui_bookmarks_set(&s_bookmarks, name, s_reader_offset, s_reader_size);
    }
    ui_book_save_marks();
}

/** 跨日清零（doc/07 §2.4）：日期变了就把当日已读秒数清零。 */
static void ui_book_daily_rollover_check(void)
{
    char today[UI_DATA_MAX_DATE_STR];

    if (ui_data_today_str(today, sizeof(today)) != UI_DATA_OK) {
        return;   /* 没校时：不动计数器，也不编日期 */
    }
    if (ui_book_daily_rollover(&s_bookmarks, today)) {
        ui_book_save_marks();
    }
}

/** 把偏移对齐到 UTF-8 字符首字节（doc/07 §3.2：绝不切坏多字节字符）。 */
static uint32_t ui_reader_align_offset(const char *path, uint32_t offset)
{
    uint8_t buf[8];
    uint32_t got = 0;
    uint32_t start;

    if (path == NULL || offset < 4u) {
        return offset;
    }
    start = offset - 4u;
    if (ui_data_read_range(path, start, buf, (uint32_t)sizeof(buf), &got) != UI_DATA_OK ||
        got == 0) {
        return offset;
    }
    uint32_t rel = ui_data_utf8_align_forward(buf, got, 4u);
    if (rel >= got) {
        return offset;
    }
    return start + rel;
}

void ui_pages_set_book_locked_text(const char *title, const char *body)
{
    if (s_locked_title != NULL) {
        lv_label_set_text(s_locked_title, (title != NULL) ? title : "");
    }
    if (s_locked_body != NULL) {
        lv_label_set_text(s_locked_body, (body != NULL) ? body : "");
    }
}

bool ui_pages_reader_guard_check(void)
{
    int now_min = 0;

    if (!s_bookmarks_loaded) {
        ui_book_load_marks();
    }
    if (!s_book_guard_loaded) {
        ui_book_load_guard();
    }
    ui_book_daily_rollover_check();

    /* 宵禁：跨午夜区间按 now >= start || now < end 判（doc/07 §2.4） */
    if (ui_data_local_minutes(&now_min) == UI_DATA_OK &&
        ui_book_curfew_active(&s_book_guard.curfew, now_min)) {
        ui_pages_set_book_locked_text("该休息啦", "现在是阅读休息时段\n明早再来读书吧");
        return false;
    }

    /* 每日课外阅读时长上限 */
    if (ui_book_daily_limit_reached(&s_book_guard, s_bookmarks.daily_used_sec)) {
        ui_pages_set_book_locked_text("今天读得够多啦",
                                      "今天的课外阅读时间用完了\n明天再来读书吧");
        return false;
    }
    return true;
}

void ui_pages_reader_tick(uint32_t now_ms, bool active)
{
    if (!active || !s_reader_counting) {
        s_reader_tick_last_ms = now_ms;   /* 暂停：不累计 */
        return;
    }
    uint32_t delta = now_ms - s_reader_tick_last_ms;
    s_reader_tick_last_ms = now_ms;
    if (delta > 2000u) {
        return;   /* 停顿或休眠后醒来：不补计 */
    }
    s_reader_session_ms += delta;
    while (s_reader_session_ms >= 1000u) {
        s_reader_session_ms -= 1000u;
        s_bookmarks.daily_used_sec += 1u;
    }
}

void ui_pages_reader_leave(void)
{
    s_reader_counting = false;
    s_reader_session_ms = 0;
    ui_book_save_progress();   /* 退出阅读页也保存进度与当日时长 */
}

/** 重画书架光标标记。 */
static void ui_bookshelf_render_cursor(void)
{
    int i;
    for (i = 0; i < s_book_count; i++) {
        if (s_book_labels[i] == NULL) {
            continue;
        }
        char buf[UI_DATA_MAX_NAME + 4];
        snprintf(buf, sizeof(buf), "%s%s", (i == s_book_cursor) ? "> " : "  ",
                 s_book_names[i]);
        lv_label_set_text(s_book_labels[i], buf);
    }
}

void ui_pages_refresh_bookshelf(void)
{
    lv_obj_t *content = ui_page_content_book(PAGE_BOOKSHELF);
    if (content == NULL) {
        return;
    }
    lv_obj_clean(content);
    s_book_count = 0;
    s_book_cursor = 0;
    memset(s_book_labels, 0, sizeof(s_book_labels));

    lv_obj_t *title = ui_make_label(content, "我的书架", true);
    lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 0);

    /* 进度表与护栏配置：书架要显示每本书的实时进度、今天还能读多久 */
    ui_book_load_marks();
    if (!s_book_guard_loaded) {
        ui_book_load_guard();
    }

    ui_data_file_t files[UI_DATA_MAX_FILES];
    uint32_t count = 0;
    int rc = ui_data_list_files(UI_DATA_DIR_BOOKS, files, UI_DATA_MAX_FILES, &count);
    if (rc != UI_DATA_OK) {
        lv_obj_t *empty = ui_make_label(content, "暂时读不到内容", false);
        lv_obj_align(empty, LV_ALIGN_CENTER, 0, 0);
        return;
    }

    uint32_t i;
    for (i = 0; i < count && s_book_count < UI_MAX_BOOKS; i++) {
        const char *name = files[i].name;
        size_t n = strlen(name);
        if (n < 5 || strcmp(name + n - 4, ".txt") != 0) {
            continue;   /* 只列正文，.toc 是配套目录 */
        }
        snprintf(s_book_names[s_book_count], UI_DATA_MAX_NAME, "%s", name);
        s_book_sizes[s_book_count] = files[i].size;
        char sub[24];
        int pct = -1;
        int idx = ui_bookmarks_find(&s_bookmarks, name);
        if (idx >= 0) {
            uint32_t total = files[i].size;
            if (total == 0) {
                total = s_bookmarks.items[idx].total_bytes;
            }
            /* 实时重算进度，绝不信 JSON 里的 percent（doc/07 §2.3） */
            pct = ui_book_progress_percent(s_bookmarks.items[idx].offset, total);
        }
        if (pct > 0) {
            snprintf(sub, sizeof(sub), "%d%%", pct);
        } else {
            snprintf(sub, sizeof(sub), "未读");
        }
        lv_obj_t *row = ui_make_list_item_ex(content, "  ", sub,
                                             &s_book_labels[s_book_count]);
        lv_obj_align(row, LV_ALIGN_TOP_LEFT, 0, 44 + s_book_count * 40);
        s_book_count++;
    }

    /* 状态栏：今天还能读多久（doc/07 §4.1） */
    lv_obj_t *status = s_book_pages[PAGE_BOOKSHELF - PAGE_BOOKSHELF].status_label;
    if (status != NULL) {
        char sbuf[32];
        if (s_book_guard.daily_limit_min > 0) {
            int used_min = (int)(s_bookmarks.daily_used_sec / 60u);
            int left = s_book_guard.daily_limit_min - used_min;
            if (left < 0) {
                left = 0;
            }
            snprintf(sbuf, sizeof(sbuf), "今日剩 %d 分", left);
        } else {
            snprintf(sbuf, sizeof(sbuf), "今日不限");
        }
        lv_label_set_text(status, sbuf);
    }
    if (s_book_count == 0) {
        lv_obj_t *empty = ui_make_label(content, "还没有下载内容", false);
        lv_obj_align(empty, LV_ALIGN_CENTER, 0, 0);
        return;
    }
    ui_bookshelf_render_cursor();
}

int ui_pages_bookshelf_move(int delta)
{
    if (s_book_count <= 0) {
        return -1;
    }
    int c = s_book_cursor + delta;
    if (c < 0) {
        c = 0;
    }
    if (c > s_book_count - 1) {
        c = s_book_count - 1;
    }
    s_book_cursor = c;
    ui_bookshelf_render_cursor();
    return c;
}

/** 解析同名 .toc：每行「章节标题|起始字节偏移量」（doc/07 §2.2）。 */
static void ui_reader_load_toc(void)
{
    s_chapter_count = 0;
    char toc_path[UI_DATA_MAX_PATH];
    snprintf(toc_path, sizeof(toc_path), "%s", s_reader_path);
    size_t n = strlen(toc_path);
    if (n < 4) {
        return;
    }
    memcpy(toc_path + n - 4, ".toc", 5);

    static char buf[1024];
    uint32_t len = 0;
    if (ui_data_read_text(toc_path, buf, (uint32_t)sizeof(buf), &len) != UI_DATA_OK) {
        return;
    }
    uint32_t i = 0;
    while (i < len && s_chapter_count < UI_MAX_CHAPTERS) {
        uint32_t j = i;
        while (j < len && buf[j] != '\n') {
            j++;
        }
        uint32_t bar = i;
        while (bar < j && buf[bar] != '|') {
            bar++;
        }
        if (bar < j && bar > i) {
            uint32_t title_len = bar - i;
            if (title_len < (uint32_t)sizeof(s_chapter_titles[0])) {
                uint32_t off = 0;
                uint32_t k = bar + 1;
                bool ok = (k < j);
                while (k < j) {
                    char c = buf[k];
                    if (c < '0' || c > '9') {
                        ok = false;
                        break;
                    }
                    off = off * 10u + (uint32_t)(c - '0');
                    k++;
                }
                if (ok && (s_reader_size == 0 || off < s_reader_size)) {
                    memcpy(s_chapter_titles[s_chapter_count], buf + i, title_len);
                    s_chapter_titles[s_chapter_count][title_len] = '\0';
                    s_chapter_offsets[s_chapter_count] = off;
                    s_chapter_count++;
                }
            }
        }
        i = (j < len) ? (j + 1) : len;
    }
}

/** 当前所在章节标题（没有目录则返回空串）。 */
static const char *ui_current_chapter_title(void)
{
    const char *title = NULL;
    int i;
    for (i = 0; i < s_chapter_count; i++) {
        if (s_chapter_offsets[i] <= s_reader_offset) {
            title = s_chapter_titles[i];
        } else {
            break;
        }
    }
    return (title != NULL) ? title : "";
}

/** 渲染当前页 + 状态栏进度。 */
static int ui_reader_render(void)
{
    if (s_reader_body == NULL) {
        return -1;
    }
    static char buf[UI_DATA_PAGE_BYTES + 64];
    uint32_t consumed = 0;
    int lines = 0;
    int rc = ui_data_load_page(s_reader_path, s_reader_offset, &consumed, &lines, buf,
                                (uint32_t)sizeof(buf));
    if (rc != UI_DATA_OK) {
        s_reader_consumed = 0;
        lv_label_set_text(s_reader_body, "暂时读不到内容");
        return -1;
    }
    if (consumed == 0) {
        s_reader_consumed = 0;
        lv_label_set_text(s_reader_body, "已经读到最后一页");
        return 0;
    }
    s_reader_consumed = consumed;
    lv_label_set_text(s_reader_body, buf);

    lv_obj_t *status = s_book_pages[PAGE_READER - PAGE_BOOKSHELF].status_label;
    if (status != NULL) {
        /* 实时重算进度，绝不信 JSON 里的 percent（doc/07 §2.3） */
        int pct = ui_book_progress_percent(s_reader_offset, s_reader_size);
        char sbuf[64];
        const char *chapter = ui_current_chapter_title();
        if (chapter[0] != '\0') {
            snprintf(sbuf, sizeof(sbuf), "%s · %d%%", chapter, pct);
        } else {
            snprintf(sbuf, sizeof(sbuf), "已读 %d%%", pct);
        }
        lv_label_set_text(status, sbuf);
    }
    return 0;
}

int ui_pages_bookshelf_open_selected(void)
{
    if (s_book_cursor < 0 || s_book_cursor >= s_book_count) {
        return -1;
    }
    snprintf(s_reader_path, sizeof(s_reader_path), "%s/%s", UI_DATA_DIR_BOOKS,
             s_book_names[s_book_cursor]);
    s_reader_offset = 0;
    s_reader_consumed = 0;
    s_reader_history_count = 0;
    s_reader_size = 0;
    ui_data_file_size(s_reader_path, &s_reader_size);
    ui_reader_load_toc();

    /* 恢复上次进度：每本书只维护**唯一最新进度**（翻页停顿即自动保存） */
    if (!s_bookmarks_loaded) {
        ui_book_load_marks();
    }
    int idx = ui_bookmarks_find(&s_bookmarks, s_book_names[s_book_cursor]);
    if (idx >= 0 && s_bookmarks.items[idx].offset > 0) {
        uint32_t off = s_bookmarks.items[idx].offset;
        if (s_reader_size == 0 || off < s_reader_size) {
            s_reader_offset = ui_reader_align_offset(s_reader_path, off);
        }
    }

    s_reader_counting = true;
    s_reader_session_ms = 0;
    return ui_reader_render();
}

void ui_pages_reader_next_page(void)
{
    if (s_reader_consumed == 0) {
        ui_toast("已经读到最后一页");
        return;
    }
    if (s_reader_history_count < UI_READER_HISTORY) {
        s_reader_history[s_reader_history_count++] = s_reader_offset;
    } else {
        memmove(&s_reader_history[0], &s_reader_history[1],
                sizeof(uint32_t) * (size_t)(UI_READER_HISTORY - 1));
        s_reader_history[UI_READER_HISTORY - 1] = s_reader_offset;
    }
    s_reader_offset += s_reader_consumed;
    ui_reader_render();
    ui_book_save_progress();   /* 翻页后自动保存进度 */
}

void ui_pages_reader_prev_page(void)
{
    uint32_t target = 0;
    if (s_reader_history_count > 0) {
        target = s_reader_history[--s_reader_history_count];
    } else if (ui_data_prev_page_offset(s_reader_path, s_reader_offset, &target) != UI_DATA_OK) {
        return;
    }
    if (target >= s_reader_offset) {
        return;
    }
    s_reader_offset = target;
    ui_reader_render();
    ui_book_save_progress();   /* 翻页后自动保存进度 */
}

/* ===================== 状态栏 ===================== */

void ui_pages_set_status(ui_page_t page, const char *text)
{
    if (page < 0 || page >= (int)PAGE_COUNT) {
        return;
    }
    if (s_pages[page].status_label == NULL) {
        return;
    }
    lv_label_set_text(s_pages[page].status_label, (text != NULL) ? text : "");
}

/* ===================== 解析结果页 ===================== */

/** 按行切出正文的一屏（从 s_solve_scroll 行起，最多 UI_SOLVE_LINES 行）。 */
static void ui_solve_render(void)
{
    if (s_solve_body == NULL) {
        return;
    }
    static char buf[UI_SOLVE_TEXT_MAX];   /* static：别占 FreeRTOS 任务栈 */
    size_t pos = 0;
    const char *p = s_solve_text;
    int line = 0;
    while (*p != '\0' && line < s_solve_scroll) {
        while (*p != '\0' && *p != '\n') {
            p++;
        }
        if (*p == '\n') {
            p++;
        }
        line++;
    }
    int shown = 0;
    while (*p != '\0' && shown < UI_SOLVE_LINES) {
        const char *e = p;
        while (*e != '\0' && *e != '\n') {
            e++;
        }
        size_t n = (size_t)(e - p);
        if (pos + n + 2 >= sizeof(buf)) {
            break;
        }
        memcpy(buf + pos, p, n);
        pos += n;
        buf[pos++] = '\n';
        shown++;
        p = (*e == '\n') ? e + 1 : e;
    }
    buf[pos] = '\0';
    lv_label_set_text(s_solve_body, buf);
}

void ui_pages_set_solve_result(const char *markdown, const char *question)
{
    snprintf(s_solve_text, sizeof(s_solve_text), "%s", (markdown != NULL) ? markdown : "");
    s_solve_scroll = 0;
    ui_solve_render();
    if (s_solve_question != NULL) {
        lv_label_set_text(s_solve_question, (question != NULL) ? question : "还有哪里没想明白？");
    }
}

bool ui_pages_solve_scroll(int delta)
{
    int total = 1;
    for (const char *p = s_solve_text; *p != '\0'; p++) {
        if (*p == '\n') {
            total++;
        }
    }
    int max_scroll = total - UI_SOLVE_LINES;
    if (max_scroll < 0) {
        max_scroll = 0;
    }
    int next = s_solve_scroll + delta;
    if (next < 0) {
        next = 0;
    }
    if (next > max_scroll) {
        next = max_scroll;
    }
    if (next == s_solve_scroll) {
        return false;
    }
    s_solve_scroll = next;
    ui_solve_render();
    return true;
}

lv_obj_t *ui_page_root(ui_page_t page)
{
    if (page >= PAGE_COUNT) {
        return NULL;
    }
    return s_pages[page].root;
}

lv_obj_t *ui_page_content(ui_page_t page)
{
    if (page >= PAGE_COUNT) {
        return NULL;
    }
    return s_pages[page].content;
}

lv_obj_t *ui_page_root_book(ui_book_page_t page)
{
    int idx = (int)page - (int)PAGE_BOOKSHELF;
    if (idx < 0 || idx >= PAGE_BOOK_COUNT - PAGE_BOOKSHELF) {
        return NULL;
    }
    return s_book_pages[idx].root;
}

lv_obj_t *ui_page_content_book(ui_book_page_t page)
{
    int idx = (int)page - (int)PAGE_BOOKSHELF;
    if (idx < 0 || idx >= PAGE_BOOK_COUNT - PAGE_BOOKSHELF) {
        return NULL;
    }
    return s_book_pages[idx].content;
}

lv_obj_t *ui_overlay_root(ui_overlay_t overlay)
{
    if (overlay >= OVERLAY_COUNT) {
        return NULL;
    }
    return s_overlays[overlay].root;
}

void ui_overlay_set_text(ui_overlay_t overlay, const char *text)
{
    if (overlay >= OVERLAY_COUNT || s_overlays[overlay].label == NULL) {
        return;
    }
    lv_label_set_text(s_overlays[overlay].label, text);
}

void ui_overlay_set_visible(ui_overlay_t overlay, bool visible)
{
    if (overlay >= OVERLAY_COUNT || s_overlays[overlay].root == NULL) {
        return;
    }
    if (visible) {
        lv_obj_clear_flag(s_overlays[overlay].root, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(s_overlays[overlay].root, LV_OBJ_FLAG_HIDDEN);
    }
}
