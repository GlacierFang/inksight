/*
 * keypad_editor.h —— 摇杆矩阵虚拟键盘的**纯编辑逻辑**（不依赖 LVGL / Arduino）
 *
 * 契约来源: abbot/doc/03 §五 第 12 页（摇杆矩阵虚拟键盘 PAGE_KEYBOARD）。
 *
 * 为什么单独抽出来：
 *   - 键盘是「能真正输进 Wi-Fi 密码」的关键，逻辑必须可证；
 *   - 本模块**不碰 LVGL、不碰网络、不碰 TF 卡**，只管文本编辑状态，
 *     因此可以在主机侧用 gcc 直接编译并跑测试（见 test/test_keypad.c）。
 *
 * 交互宪法（doc/02 §五 防线 4）：短按 = 输入，长按 = 退出；
 * 本模块只提供「输入一个字符 / 退格 / 空格 / 移动光标 / 大小写」这些原子动作，
 * 短按与长按的裁定仍由 HAL 负责。
 *
 * 安全约定：结构体里的 text 是**明文**，只能停留在内存里，
 * 严禁整段打印到日志（见 net_api.cpp 里保存密码时只记 SSID）。
 */
#ifndef ABBOT_KEYPAD_EDITOR_H
#define ABBOT_KEYPAD_EDITOR_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/** 密码缓冲区上限（WPA  passphrase 最长 63 字节，留 1 给结束符）。 */
#define KEYPAD_MAX_TEXT 64

/**
 * 键盘编辑状态。
 *
 * - `text`    : 明文内容（**不外传、不打日志**）
 * - `cursor`  : 光标位置，语义是「在第几个字符之前」，取值 0..len
 * - `shift`   : 大小写切换（**粘滞**，切了就一直大写，直到再切一次；doc/03 §12）
 * - `masked`  : 掩码显示（密码模式默认 true；doc/03 §12 的 [显/隐] 可切换）
 */
typedef struct {
    char text[KEYPAD_MAX_TEXT + 1];
    uint16_t len;
    uint16_t cursor;
    bool shift;
    bool masked;
    uint16_t max_len;
} keypad_editor_t;

/** 复位：清空文本与光标，shift 关闭，掩码打开，长度上限取默认。 */
void keypad_editor_init(keypad_editor_t *e);

/** 设置最大长度（超出后拒绝写入）。max_len 会被夹到 KEYPAD_MAX_TEXT。 */
void keypad_editor_set_max_len(keypad_editor_t *e, uint16_t max_len);

/** 掩码显示开关（密码模式用）。 */
void keypad_editor_set_mask(keypad_editor_t *e, bool masked);
void keypad_editor_toggle_mask(keypad_editor_t *e);

/** 大小写切换（sticky）。 */
void keypad_editor_set_shift(keypad_editor_t *e, bool shift);
void keypad_editor_toggle_shift(keypad_editor_t *e);

/**
 * 该键位在当前大小写状态下应显示 / 写入的字符。
 * `lower` 传小写字母；数字与符号原样返回。
 */
char keypad_editor_key_char(const keypad_editor_t *e, char lower);

/** 在光标处插入一个字符（非 ASCII 一律忽略）。超长则拒绝并返回 false。 */
bool keypad_editor_insert(keypad_editor_t *e, char c);

/**
 * 按下一个字母键：按当前 shift 状态写入，并把 shift 复位为小写。
 * 数字键用 keypad_editor_insert 直接写。
 */
bool keypad_editor_type_key(keypad_editor_t *e, char lower);

/** 退格：删除光标前一个字符；光标已在开头则什么都不做。 */
void keypad_editor_backspace(keypad_editor_t *e);

/** 删除：删除光标处的一个字符。 */
void keypad_editor_delete(keypad_editor_t *e);

/** 空格。 */
bool keypad_editor_space(keypad_editor_t *e);

/** 光标左右移动（到边界就停住，不会越界）。 */
void keypad_editor_move_left(keypad_editor_t *e);
void keypad_editor_move_right(keypad_editor_t *e);

/** 清空。 */
void keypad_editor_clear(keypad_editor_t *e);

/**
 * 渲染当前可见内容到 out（掩码时是 '*'，否则是明文）。
 * 返回可见字符数；out 空间不足时按能放下的部分输出并保证结束符。
 * **明文模式下调用方要负责不把它写进日志。**
 */
int keypad_editor_render(const keypad_editor_t *e, char *out, size_t cap);

/** 当前明文（供连接时使用；调用方不得打印）。 */
const char *keypad_editor_text(const keypad_editor_t *e);

#ifdef __cplusplus
}
#endif

#endif /* ABBOT_KEYPAD_EDITOR_H */
