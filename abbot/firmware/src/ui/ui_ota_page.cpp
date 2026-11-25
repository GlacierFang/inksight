/*
 * ui_ota_page.cpp —— 固件升级页的正文与刷新（真实数据）
 *
 * 数据源：ota_get_status() —— 网络任务（Core 0）写，UI 任务（Core 1）只读。
 * 刷新方式：LVGL 定时器（500ms）。状态序号没变就不重画，避免闪烁。
 *
 * 契约：
 *   - doc/04 固件升级章节：这一页要显示当前版本 / 最新版本 / 更新说明 /
 *     下载进度 / 结果提示；
 *   - 错误文案铁律：不出现 AES / HTTP / signature / Token / Python /
 *     Traceback 等技术术语（文案全部由 ota_result_text() 给，本文件只做排版）。
 *   - UI 只读状态、只发请求，不做校验、不碰密钥。
 */

#include "ui_ota_page.h"

#include <stdio.h>
#include <string.h>

#include "ota_client.h"
#include "ui_pages.h"

#define OTA_TEXT_MAX 512
#define OTA_CHANGELOG_MAX 40 /* 更新说明最多 40 字（屏幕 280px 宽，约 17 字一行） */

static lv_obj_t *s_ota_body = NULL;
static lv_timer_t *s_ota_timer = NULL;
static uint32_t s_ota_seq = 0;
static char s_ota_text[OTA_TEXT_MAX];

static void ota_page_timer_cb(lv_timer_t *timer);

/** 把更新说明压成一行（换行折成空格），最多 OTA_CHANGELOG_MAX 个字。 */
static void ota_changelog_flat(const char *src, char *out, size_t cap)
{
    size_t o = 0;
    if (out == NULL || cap == 0) {
        return;
    }
    out[0] = '\0';
    if (src == NULL) {
        return;
    }
    for (const char *p = src; *p != '\0' && o + 1 < cap && o < OTA_CHANGELOG_MAX; p++) {
        if (*p == '\r') {
            continue;
        }
        if (*p == '\n') {
            out[o++] = ' ';
            continue;
        }
        out[o++] = *p;
    }
    out[o] = '\0';
}

/** 往正文缓冲里追加一段（自动截断，不会越界）。 */
static void ota_append(char *out, size_t cap, size_t *off, const char *text)
{
    if (out == NULL || text == NULL || off == NULL || cap == 0) {
        return;
    }
    if (*off + 1 >= cap) {
        return;
    }
    size_t n = strlen(text);
    if (*off + n + 1 > cap) {
        n = cap - *off - 1;
    }
    memcpy(out + *off, text, n);
    *off += n;
    out[*off] = '\0';
}

/** 按当前状态拼出这一页要显示的正文。 */
static void ota_page_render(char *out, size_t cap)
{
    ota_status_t st;
    char line[160];
    size_t off = 0;

    if (out == NULL || cap == 0) {
        return;
    }
    out[0] = '\0';
    ota_get_status(&st);

    snprintf(line, sizeof(line), "当前版本：%s\n",
             (st.current_ver[0] != '\0') ? st.current_ver : "-");
    ota_append(out, cap, &off, line);

    if (st.has_update && st.latest_ver[0] != '\0') {
        snprintf(line, sizeof(line), "最新版本：%s\n", st.latest_ver);
    } else {
        snprintf(line, sizeof(line), "最新版本：暂无新版本\n");
    }
    ota_append(out, cap, &off, line);

    char cl[OTA_CHANGELOG_MAX + 2];
    ota_changelog_flat(st.changelog, cl, sizeof(cl));
    if (cl[0] != '\0') {
        snprintf(line, sizeof(line), "更新说明：%s\n", cl);
        ota_append(out, cap, &off, line);
    }

    if (st.state == OTA_STATE_PRECHECK) {
        ota_append(out, cap, &off, "正在检查升级条件…\n");
    } else if (st.state == OTA_STATE_CHECKING) {
        ota_append(out, cap, &off, "正在检查更新…\n");
    } else if (st.state == OTA_STATE_DOWNLOADING) {
        snprintf(line, sizeof(line), "下载进度：%d%%\n", st.progress_pct);
        ota_append(out, cap, &off, line);
    } else if (st.state == OTA_STATE_VERIFYING) {
        ota_append(out, cap, &off, "正在校验固件…\n");
    } else if (st.state == OTA_STATE_REBOOTING) {
        ota_append(out, cap, &off, "正在重启，请稍候…\n");
    }

    if (st.message[0] != '\0') {
        ota_append(out, cap, &off, st.message);
        ota_append(out, cap, &off, "\n");
    }
}

void ui_ota_page_refresh(void)
{
    ota_status_t st;

    ota_get_status(&st);
    s_ota_seq = st.seq;
    /* 渲染到**静态**缓冲：界面核的栈只有 4KB，不能在这里放大块局部变量 */
    ota_page_render(s_ota_text, sizeof(s_ota_text));
    if (s_ota_body != NULL) {
        lv_label_set_text(s_ota_body, s_ota_text);
    }
}

static void ota_page_timer_cb(lv_timer_t *timer)
{
    ota_status_t st;
    (void)timer;

    ota_get_status(&st);
    if (st.seq == s_ota_seq) {
        return; /* 状态没变 -> 不重画 */
    }
    ui_ota_page_refresh();
}

void ui_ota_page_build(lv_obj_t *content)
{
    if (content == NULL) {
        return;
    }
    ota_page_render(s_ota_text, sizeof(s_ota_text));
    s_ota_body = ui_pages_make_label(content, s_ota_text, false);
    lv_obj_align(s_ota_body, LV_ALIGN_TOP_LEFT, 0, 40);

    /* 刷新定时器只建一个（页面可能被重建，标签由本模块持有） */
    if (s_ota_timer == NULL) {
        s_ota_timer = lv_timer_create(ota_page_timer_cb, 500, NULL);
    }
    ui_ota_page_refresh();
}
