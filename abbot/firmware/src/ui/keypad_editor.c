/*
 * keypad_editor.c —— 摇杆矩阵虚拟键盘的纯编辑逻辑
 *
 * 不依赖 LVGL / Arduino / ESP-IDF：主机侧可用 gcc 直接编译并跑测试。
 */

#include "keypad_editor.h"

#include <string.h>

static uint16_t clamp_u16(uint16_t v, uint16_t lo, uint16_t hi)
{
    if (v < lo) {
        return lo;
    }
    if (v > hi) {
        return hi;
    }
    return v;
}

void keypad_editor_init(keypad_editor_t *e)
{
    if (e == NULL) {
        return;
    }
    memset(e, 0, sizeof(*e));
    e->max_len = KEYPAD_MAX_TEXT;
    e->masked = true;
    e->shift = false;
    e->len = 0;
    e->cursor = 0;
}

void keypad_editor_set_max_len(keypad_editor_t *e, uint16_t max_len)
{
    if (e == NULL) {
        return;
    }
    e->max_len = clamp_u16(max_len, 1, KEYPAD_MAX_TEXT);
    if (e->len > e->max_len) {
        e->len = e->max_len;
    }
    if (e->cursor > e->len) {
        e->cursor = e->len;
    }
    e->text[e->len] = '\0';
}

void keypad_editor_set_mask(keypad_editor_t *e, bool masked)
{
    if (e == NULL) {
        return;
    }
    e->masked = masked;
}

void keypad_editor_toggle_mask(keypad_editor_t *e)
{
    if (e == NULL) {
        return;
    }
    e->masked = !e->masked;
}

void keypad_editor_set_shift(keypad_editor_t *e, bool shift)
{
    if (e == NULL) {
        return;
    }
    e->shift = shift;
}

void keypad_editor_toggle_shift(keypad_editor_t *e)
{
    if (e == NULL) {
        return;
    }
    e->shift = !e->shift;
}

char keypad_editor_key_char(const keypad_editor_t *e, char lower)
{
    char c = lower;
    if (e == NULL) {
        return c;
    }
    if (e->shift && c >= 'a' && c <= 'z') {
        c = (char)(c - 'a' + 'A');
    }
    return c;
}

static bool is_printable_ascii(char c)
{
    unsigned char u = (unsigned char)c;
    return u >= 0x20u && u <= 0x7Eu;
}

bool keypad_editor_insert(keypad_editor_t *e, char c)
{
    if (e == NULL || !is_printable_ascii(c)) {
        return false;
    }
    if (e->len >= e->max_len) {
        return false;
    }
    if (e->cursor > e->len) {
        e->cursor = e->len;
    }
    /* 后移光标右侧的字符 */
    for (uint16_t i = e->len; i > e->cursor; i--) {
        e->text[i] = e->text[i - 1];
    }
    e->text[e->cursor] = c;
    e->len++;
    e->cursor++;
    e->text[e->len] = '\0';
    return true;
}

bool keypad_editor_type_key(keypad_editor_t *e, char lower)
{
    char c = keypad_editor_key_char(e, lower);
    bool ok = keypad_editor_insert(e, c);
    /* 大小写是「粘滞」的：按一次字母键后自动回到小写，
       要继续输大写就再点一次 [大小写]。 */
    e->shift = false;
    return ok;
}

void keypad_editor_backspace(keypad_editor_t *e)
{
    if (e == NULL || e->cursor == 0 || e->len == 0) {
        return;
    }
    uint16_t idx = (uint16_t)(e->cursor - 1);
    for (uint16_t i = idx; i + 1 < e->len; i++) {
        e->text[i] = e->text[i + 1];
    }
    e->len--;
    e->cursor = idx;
    e->text[e->len] = '\0';
}

void keypad_editor_delete(keypad_editor_t *e)
{
    if (e == NULL || e->cursor >= e->len) {
        return;
    }
    for (uint16_t i = e->cursor; i + 1 < e->len; i++) {
        e->text[i] = e->text[i + 1];
    }
    e->len--;
    e->text[e->len] = '\0';
}

bool keypad_editor_space(keypad_editor_t *e)
{
    return keypad_editor_insert(e, ' ');
}

void keypad_editor_move_left(keypad_editor_t *e)
{
    if (e == NULL) {
        return;
    }
    if (e->cursor > 0) {
        e->cursor--;
    }
}

void keypad_editor_move_right(keypad_editor_t *e)
{
    if (e == NULL) {
        return;
    }
    if (e->cursor < e->len) {
        e->cursor++;
    }
}

void keypad_editor_clear(keypad_editor_t *e)
{
    if (e == NULL) {
        return;
    }
    e->len = 0;
    e->cursor = 0;
    e->text[0] = '\0';
}

int keypad_editor_render(const keypad_editor_t *e, char *out, size_t cap)
{
    if (out == NULL || cap == 0) {
        return 0;
    }
    if (e == NULL) {
        out[0] = '\0';
        return 0;
    }
    size_t n = e->len;
    if (n > cap - 1) {
        n = cap - 1;
    }
    for (size_t i = 0; i < n; i++) {
        out[i] = e->masked ? '*' : e->text[i];
    }
    out[n] = '\0';
    return (int)n;
}

const char *keypad_editor_text(const keypad_editor_t *e)
{
    if (e == NULL) {
        return "";
    }
    return e->text;
}
