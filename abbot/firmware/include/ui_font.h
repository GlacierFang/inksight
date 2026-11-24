/*
 * ui_font.h —— 界面用字库
 *
 * LVGL 内置的 Montserrat 没有汉字, 中文界面必须自带字库。
 * 本字库是**裁剪版**: 只含界面实际用到的字符 (中文 + ASCII),
 * 由 Noto Sans CJK SC 裁剪生成, 16px / 4bpp。
 * 重新裁剪: firmware/tools/gen_font.sh
 */
#pragma once

#include "lvgl.h"

#ifdef __cplusplus
extern "C" {
#endif

/* 裁剪字库 (src/ui/fonts/ui_font_cn_16.c 由 tools/gen_font.sh 生成) */
extern const lv_font_t ui_font_cn_16;

#ifdef __cplusplus
}
#endif
