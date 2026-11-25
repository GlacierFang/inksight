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
#include "ui_data.h"

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

/* 0. 首次开机引导 (PAGE_OOBE) */
static void ui_build_oobe(lv_obj_t *content)
{
    lv_obj_t *title = ui_make_label(content, "欢迎使用 · 随身学习终端 (1/4)", true);
    lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 0);

    lv_obj_t *body = ui_make_label(content,
        "你好！初次见面，只需 1 分钟\n完成网络与学习档案配置即可启程。", false);
    lv_obj_align(body, LV_ALIGN_TOP_LEFT, 0, 60);

    lv_obj_t *item1 = ui_make_list_item(content, "> [ 开始连接 Wi-Fi 网络 ]", NULL);
    lv_obj_align(item1, LV_ALIGN_TOP_LEFT, 0, 130);

    lv_obj_t *item2 = ui_make_list_item(content, "  [ 跳过引导，先离线体验 ]", NULL);
    lv_obj_align(item2, LV_ALIGN_TOP_LEFT, 0, 170);
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

/* 4. 学科卡片包列表 (PAGE_SUBJECT_LIST) */
static void ui_build_subject_list(lv_obj_t *content)
{
    static const char *items[] = {
        "> 语文 · 基础积累",
        "  数学 · 几何直观",
        "  英语 · 日常表达",
        "  历史 · 时间线",
    };
    int i;
    for (i = 0; i < 4; i++) {
        lv_obj_t *row = ui_make_list_item(content, items[i], "已下载");
        lv_obj_align(row, LV_ALIGN_TOP_LEFT, 0, i * 40);
    }
}

/* 5. 翻卡学习 (PAGE_CARD_STUDY) */
static void ui_build_card_study(lv_obj_t *content)
{
    lv_obj_t *card = ui_make_card(content);
    lv_obj_set_size(card, 280, 260);
    lv_obj_align(card, LV_ALIGN_TOP_MID, 0, 0);
    lv_obj_t *label = ui_make_label(card, "正面：这句话的意思是……\n\n[ 翻面 ]", false);
    lv_obj_align(label, LV_ALIGN_TOP_LEFT, 0, 0);

    static const char *items[] = {
        "> [ 翻面 ]",
        "  [ 语音追问这题 ]",
        "  [ 上一张 / 下一张 ]",
    };
    int i;
    for (i = 0; i < 3; i++) {
        lv_obj_t *row = ui_make_list_item(content, items[i], NULL);
        lv_obj_align(row, LV_ALIGN_BOTTOM_LEFT, 0, -70 + i * 26);
    }
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

/* 9. WiFi 扫描列表 (PAGE_WIFI_SCAN) */
static void ui_build_wifi_scan(lv_obj_t *content)
{
    lv_obj_t *title = ui_make_label(content, "附近的无线网络", true);
    lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 0);

    static const char *items[] = {
        "> Home-2.4G      强",
        "  Guest          中",
        "  Neighbor       弱",
    };
    int i;
    for (i = 0; i < 3; i++) {
        lv_obj_t *row = ui_make_list_item(content, items[i], NULL);
        lv_obj_align(row, LV_ALIGN_TOP_LEFT, 0, 44 + i * 40);
    }
}

/* 10. 虚拟键盘 (PAGE_KEYBOARD) */
static void ui_build_keyboard(lv_obj_t *content)
{
    lv_obj_t *input = ui_make_label(content, "输入：", false);
    lv_obj_align(input, LV_ALIGN_TOP_LEFT, 0, 0);

    static const char *keys[] = {
        "1 2 3 4 5 6 7 8 9 0",
        "Q W E R T Y U I O P",
        "A S D F G H J K L",
        "Z X C V B N M",
        "空格  删除  大小写",
    };
    int i;
    for (i = 0; i < 5; i++) {
        lv_obj_t *row = ui_make_label(content, keys[i], false);
        lv_obj_align(row, LV_ALIGN_TOP_LEFT, 0, 44 + i * 44);
    }
}

/* 11. 固件升级 (PAGE_OTA) */
static void ui_build_ota(lv_obj_t *content)
{
    lv_obj_t *title = ui_make_label(content, "固件升级", true);
    lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 0);

    lv_obj_t *body = ui_make_label(content, "当前已是最新版本\n\n上次检查：刚刚", false);
    lv_obj_align(body, LV_ALIGN_TOP_LEFT, 0, 40);

    lv_obj_t *item = ui_make_list_item(content, "> [ 检查更新 ]", NULL);
    lv_obj_align(item, LV_ALIGN_BOTTOM_MID, 0, 0);
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

    lv_obj_t *body = ui_make_label(content, "现在是阅读休息时段\n明早再来读书吧", false);
    lv_obj_align(body, LV_ALIGN_CENTER, 0, 12);
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
        snprintf(sub, sizeof(sub), "%u KB", (unsigned)(files[i].size / 1024u));
        lv_obj_t *row = ui_make_list_item_ex(content, "  ", sub,
                                             &s_book_labels[s_book_count]);
        lv_obj_align(row, LV_ALIGN_TOP_LEFT, 0, 44 + s_book_count * 40);
        s_book_count++;
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
        int pct = 0;
        if (s_reader_size > 0) {
            pct = (int)((uint64_t)s_reader_offset * 100u / (uint64_t)s_reader_size);
            if (pct > 100) {
                pct = 100;
            }
        }
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
