/**
 * hal_joystick.cpp —— 双轴摇杆五向事件与防误触状态机
 *
 * 契约: abbot/doc/02 §3.4
 *   - VRx = GPIO2 (ADC1_CH1), VRy = GPIO3 (ADC1_CH2), SW = GPIO0 (INPUT_PULLUP)
 *   - 开机 300ms 内连采 20 次取均值作动态中点（机械回中误差自愈）
 *   - 死区 ±650 滞回判定（doc/02 §3.4 与 §五 防线表 #3）
 *   - 短按 30ms~600ms(不含) 于释放瞬间判定 CLICK；>=600ms 触发
 *     LONG_PRESS_START，松开瞬间触发 LONG_PRESS_END
 *   - **不产出双击事件**（doc/02 §3.4 已废除 250ms 双击窗口）
 *   - 开机屏蔽 SW 事件 500ms（与长按阈值无关，二者不可混用）
 *
 * 本文件是 HAL 唯一裁定按压归类的地方，UI 层严禁自行计时判定。
 */
#include <Arduino.h>

#include "board_config.h"
#include "hal_interfaces.h"
#include "hal_shared.h"

static const char *TAG = "hal_joy";

/* 死区滞回：进入方向用 ENTER，离开方向用 EXIT。
 * EXIT 比 ENTER 小一档，避免临界抖动来回跳。
 * （doc/02 §3.4 只锁定 ±650 这个进入阈值，退出阈值是实现细节） */
#define JOY_DEADBAND_ENTER JOY_ADC_DEADBAND
#define JOY_DEADBAND_EXIT  (JOY_ADC_DEADBAND - 200)

/* 方向连发：首次触发后等 400ms 进入连发，之后每 150ms 一次 */
#define JOY_REPEAT_DELAY_MS 400
#define JOY_REPEAT_PERIOD_MS 150

/* SW 消抖：短于 30ms 的按压视为抖动丢弃 */
#define JOY_DEBOUNCE_MS 30
/* 开机屏蔽 SW 事件的静默窗口 */
#define JOY_BOOT_BLIND_MS 500

enum { DIR_NONE = 0, DIR_UP, DIR_DOWN, DIR_LEFT, DIR_RIGHT };

static int s_center_x = JOY_ADC_CENTER;
static int s_center_y = JOY_ADC_CENTER;
static bool s_calibrated = false;

static int s_dir = DIR_NONE;
static uint32_t s_dir_first_ms = 0;
static uint32_t s_next_repeat_ms = 0;

static bool s_sw_pressed = false;
static bool s_sw_long_fired = false;
static uint32_t s_sw_down_ms = 0;
static uint32_t s_boot_ms = 0;

static void joy_post(joystick_event_type_t type, uint32_t hold_ms, int32_t delta)
{
    joystick_event_t ev;
    ev.type = type;
    ev.hold_ms = hold_ms;
    ev.delta = delta;
    QueueHandle_t q = hal_joystick_queue();
    if (q == NULL) {
        return;
    }
    /* 队列满就丢弃本事件 —— 摇杆事件是瞬时信号，堆积没有意义，
     * 宁可丢也不阻塞采样任务（doc/02 §4.1：界面永不阻塞）。 */
    if (xQueueSend(q, &ev, 0) != pdTRUE) {
        Serial.printf("[%s] joystick queue full, drop %d\n", TAG, (int)type);
    }
}

hal_status_t hal_joystick_init(void)
{
    pinMode((uint8_t)JOY_PIN_SW, INPUT_PULLUP);
    analogSetPinAttenuation(JOY_PIN_VRX, ADC_11db);
    analogSetPinAttenuation(JOY_PIN_VRY, ADC_11db);

    s_boot_ms = millis();

    /* 动态中点校准：开机 300ms 内连采 20 次取均值。
     * 此时摇杆处于静止归中状态，均值即本次运行的零点基准。 */
    const uint32_t t0 = millis();
    long sum_x = 0, sum_y = 0;
    int n = 0;
    while (millis() - t0 < 300 && n < 20) {
        sum_x += analogRead(JOY_PIN_VRX);
        sum_y += analogRead(JOY_PIN_VRY);
        n++;
        delay(10);
    }
    if (n > 0) {
        s_center_x = (int)(sum_x / n);
        s_center_y = (int)(sum_y / n);
        s_calibrated = true;
    } else {
        s_center_x = JOY_ADC_CENTER;
        s_center_y = JOY_ADC_CENTER;
        s_calibrated = false;
    }

    Serial.printf("[%s] center=(%d,%d) samples=%d\n", TAG, s_center_x, s_center_y, n);

    s_dir = DIR_NONE;
    s_sw_pressed = false;
    s_sw_long_fired = false;
    return HAL_OK;
}

/* 依据当前偏移判定方向；滞回：已进入某方向时用 EXIT 阈值才允许退出 */
static int joy_pick_dir(int dx, int dy)
{
    const int enter = JOY_DEADBAND_ENTER;
    const int exit_ = JOY_DEADBAND_EXIT;

    if (s_dir != DIR_NONE) {
        int keep = exit_;
        switch (s_dir) {
        case DIR_UP:
            if (dy > keep) return DIR_UP;
            break;
        case DIR_DOWN:
            if (-dy > keep) return DIR_DOWN;
            break;
        case DIR_LEFT:
            if (-dx > keep) return DIR_LEFT;
            break;
        case DIR_RIGHT:
            if (dx > keep) return DIR_RIGHT;
            break;
        default:
            break;
        }
    }

    /* 无当前方向：按偏移绝对值大的轴优先判定 */
    int adx = dx < 0 ? -dx : dx;
    int ady = dy < 0 ? -dy : dy;
    if (ady >= adx) {
        if (ady > enter) {
            return (dy > 0) ? DIR_UP : DIR_DOWN;
        }
    } else {
        if (adx > enter) {
            return (dx > 0) ? DIR_RIGHT : DIR_LEFT;
        }
    }
    return DIR_NONE;
}

hal_status_t hal_joystick_poll(void)
{
    const uint32_t now = millis();

    /* ---------- 方向轴 ---------- */
    const int dx = analogRead(JOY_PIN_VRX) - s_center_x;
    const int dy = s_center_y - analogRead(JOY_PIN_VRY); /* 上推为正 */
    const int dir = joy_pick_dir(dx, dy);

    if (dir != s_dir) {
        if (dir == DIR_NONE) {
            s_dir = DIR_NONE;
        } else {
            s_dir = dir;
            s_dir_first_ms = now;
            s_next_repeat_ms = now + JOY_REPEAT_DELAY_MS;
            const int32_t delta = (dir == DIR_UP || dir == DIR_DOWN) ? (int32_t)dy : (int32_t)dx;
            joy_post((dir == DIR_UP)     ? JOY_EVENT_UP
                     : (dir == DIR_DOWN) ? JOY_EVENT_DOWN
                     : (dir == DIR_LEFT) ? JOY_EVENT_LEFT
                                         : JOY_EVENT_RIGHT,
                     0, delta);
        }
    } else if (s_dir != DIR_NONE && now >= s_next_repeat_ms) {
        s_next_repeat_ms = now + JOY_REPEAT_PERIOD_MS;
        const int32_t delta = (s_dir == DIR_UP || s_dir == DIR_DOWN) ? (int32_t)dy : (int32_t)dx;
        joy_post(JOY_EVENT_REPEAT, 0, delta);
    }

    /* ---------- SW 中心键 ---------- */
    const bool down = (digitalRead(JOY_PIN_SW) == LOW);
    const bool blind = (now - s_boot_ms) < JOY_BOOT_BLIND_MS;

    if (down && !s_sw_pressed) {
        s_sw_pressed = true;
        s_sw_down_ms = now;
        s_sw_long_fired = false;
    } else if (down && s_sw_pressed && !s_sw_long_fired) {
        if (now - s_sw_down_ms >= LONG_PRESS_MS) {
            s_sw_long_fired = true;
            if (!blind) {
                joy_post(JOY_EVENT_LONG_PRESS_START, now - s_sw_down_ms, 0);
            }
        }
    } else if (!down && s_sw_pressed) {
        s_sw_pressed = false;
        const uint32_t held = now - s_sw_down_ms;
        if (!blind && held >= JOY_DEBOUNCE_MS) {
            if (s_sw_long_fired) {
                joy_post(JOY_EVENT_LONG_PRESS_END, held, 0);
            } else {
                /* 30ms ~ 600ms(不含) —— 短按确定，于释放瞬间判定 */
                joy_post(JOY_EVENT_CLICK, held, 0);
            }
        }
        s_sw_long_fired = false;
    }

    return HAL_OK;
}
