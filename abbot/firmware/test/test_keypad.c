/*
 * test_keypad.c —— 虚拟键盘编辑逻辑的主机侧测试
 *
 * 编译: gcc -std=c11 -Wall -Wextra -Werror -Iinclude test/test_keypad.c src/ui/keypad_editor.c -o /tmp/test_keypad
 * 运行: /tmp/test_keypad   (全部通过时打印 ALL PASS)
 *
 * 这个测试要证明的是：键盘**真的能编出密码**（插入 / 退格 / 空格 / 光标 / 大小写 / 掩码），
 * 而不是只画五行静态文本。
 */

#include <stdio.h>
#include <string.h>

#include "keypad_editor.h"

static int g_fail = 0;

#define CHECK(cond)                                                            \
    do {                                                                       \
        if (!(cond)) {                                                         \
            printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond);             \
            g_fail++;                                                          \
        }                                                                      \
    } while (0)

#define CHECK_STR(actual, expect)                                              \
    do {                                                                       \
        const char *a_ = (actual);                                             \
        const char *e_ = (expect);                                             \
        if (a_ == NULL || strcmp(a_, e_) != 0) {                               \
            printf("FAIL %s:%d  got \"%s\" want \"%s\"\n", __FILE__, __LINE__, \
                   a_ ? a_ : "(null)", e_);                                     \
            g_fail++;                                                          \
        }                                                                      \
    } while (0)

static void test_init(void)
{
    keypad_editor_t e;
    keypad_editor_init(&e);
    CHECK(e.len == 0);
    CHECK(e.cursor == 0);
    CHECK(e.masked == true);   /* 密码模式默认掩码 */
    CHECK(e.shift == false);
    CHECK(e.max_len == KEYPAD_MAX_TEXT);
    CHECK_STR(e.text, "");
}

static void test_insert_and_cursor(void)
{
    keypad_editor_t e;
    keypad_editor_init(&e);
    keypad_editor_set_mask(&e, false);

    CHECK(keypad_editor_insert(&e, 'a'));
    CHECK(keypad_editor_insert(&e, 'b'));
    CHECK(keypad_editor_insert(&e, 'c'));
    CHECK(e.len == 3);
    CHECK(e.cursor == 3);
    CHECK_STR(e.text, "abc");

    /* 光标左移后在中间插入 */
    keypad_editor_move_left(&e);
    keypad_editor_move_left(&e);
    CHECK(e.cursor == 1);
    CHECK(keypad_editor_insert(&e, 'X'));
    CHECK_STR(e.text, "aXbc");
    CHECK(e.cursor == 2);
    CHECK(e.len == 4);

    /* 光标到头不再左移，到尾不再右移 */
    keypad_editor_move_left(&e);
    keypad_editor_move_left(&e);
    keypad_editor_move_left(&e);
    CHECK(e.cursor == 0);
    keypad_editor_move_right(&e);
    keypad_editor_move_right(&e);
    keypad_editor_move_right(&e);
    keypad_editor_move_right(&e);
    CHECK(e.cursor == 4);
}

static void test_backspace_delete_space(void)
{
    keypad_editor_t e;
    keypad_editor_init(&e);
    keypad_editor_set_mask(&e, false);

    keypad_editor_insert(&e, 'h');
    keypad_editor_insert(&e, 'i');
    /* 光标在末尾，退格删掉最后一个 */
    keypad_editor_backspace(&e);
    CHECK_STR(e.text, "h");
    CHECK(e.len == 1);
    CHECK(e.cursor == 1);

    /* 退格到头就不动了 */
    keypad_editor_backspace(&e);
    keypad_editor_backspace(&e);
    CHECK_STR(e.text, "");
    CHECK(e.len == 0);
    CHECK(e.cursor == 0);

    /* 空格 */
    CHECK(keypad_editor_space(&e));
    CHECK_STR(e.text, " ");
    CHECK(e.len == 1);

    /* 删除：删光标处的字符（删完光标停在原处，指向后一个字符） */
    keypad_editor_insert(&e, 'x');
    keypad_editor_move_left(&e);
    keypad_editor_delete(&e);
    CHECK_STR(e.text, " ");
    CHECK(e.len == 1);
    CHECK(e.cursor == 1);
}

static void test_shift(void)
{
    keypad_editor_t e;
    keypad_editor_init(&e);
    keypad_editor_set_mask(&e, false);

    /* 默认小写 */
    CHECK(keypad_editor_key_char(&e, 'a') == 'a');

    /* 大小写切换后按键变大写 */
    keypad_editor_toggle_shift(&e);
    CHECK(e.shift == true);
    CHECK(keypad_editor_key_char(&e, 'a') == 'A');
    CHECK(keypad_editor_key_char(&e, 'z') == 'Z');

    /* 数字与符号不受大小写影响 */
    CHECK(keypad_editor_key_char(&e, '7') == '7');
    CHECK(keypad_editor_key_char(&e, '@') == '@');

    /* 粘滞：按一次字母键后自动回到小写 */
    CHECK(keypad_editor_type_key(&e, 'a'));
    CHECK_STR(e.text, "A");
    CHECK(e.shift == false);
    CHECK(keypad_editor_key_char(&e, 'b') == 'b');
}

static void test_mask_render(void)
{
    keypad_editor_t e;
    keypad_editor_init(&e);
    keypad_editor_insert(&e, 's');
    keypad_editor_insert(&e, 'e');
    keypad_editor_insert(&e, 'c');

    char buf[32];
    /* 默认掩码：不显示明文 */
    CHECK(keypad_editor_render(&e, buf, sizeof(buf)) == 3);
    CHECK_STR(buf, "***");

    keypad_editor_set_mask(&e, false);
    CHECK(keypad_editor_render(&e, buf, sizeof(buf)) == 3);
    CHECK_STR(buf, "sec");

    keypad_editor_toggle_mask(&e);
    CHECK(e.masked == true);
    CHECK(keypad_editor_render(&e, buf, sizeof(buf)) == 3);
    CHECK_STR(buf, "***");
}

static void test_limits(void)
{
    keypad_editor_t e;
    keypad_editor_init(&e);
    keypad_editor_set_max_len(&e, 3);
    keypad_editor_set_mask(&e, false);

    CHECK(keypad_editor_insert(&e, 'a'));
    CHECK(keypad_editor_insert(&e, 'b'));
    CHECK(keypad_editor_insert(&e, 'c'));
    /* 超长必须拒绝，且内容不被截断写坏 */
    CHECK(keypad_editor_insert(&e, 'd') == false);
    CHECK(e.len == 3);
    CHECK_STR(e.text, "abc");

    /* 非 ASCII 一律忽略 */
    CHECK(keypad_editor_insert(&e, '\n') == false);
    CHECK(keypad_editor_insert(&e, '\t') == false);
    CHECK(e.len == 3);

    /* 缓冲上限夹紧 */
    keypad_editor_set_max_len(&e, 9999);
    CHECK(e.max_len == KEYPAD_MAX_TEXT);
    keypad_editor_set_max_len(&e, 0);
    CHECK(e.max_len == 1);
}

static void test_clear(void)
{
    keypad_editor_t e;
    keypad_editor_init(&e);
    keypad_editor_insert(&e, 'q');
    keypad_editor_insert(&e, 'w');
    keypad_editor_clear(&e);
    CHECK(e.len == 0);
    CHECK(e.cursor == 0);
    CHECK_STR(e.text, "");
}

int main(void)
{
    test_init();
    test_insert_and_cursor();
    test_backspace_delete_space();
    test_shift();
    test_mask_render();
    test_limits();
    test_clear();

    if (g_fail == 0) {
        printf("ALL PASS\n");
        return 0;
    }
    printf("%d FAILED\n", g_fail);
    return 1;
}
