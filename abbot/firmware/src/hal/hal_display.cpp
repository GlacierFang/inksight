/**
 * hal_display.c —— ST7796S 竖屏驱动与背光 PWM
 *
 * 对齐 abbot/doc/02 §3.1：
 *   - 引脚 MOSI=45 SCLK=46 CS=44 DC=47 RST=21 BLK=48；
 *   - 驱动用 LovyanGFX（DMA 刷新效率高于 TFT_eSPI），SPI 写时钟 40MHz；
 *   - 逻辑分辨率锁定竖屏 320x480（rotation = 0 或 2，以实测排针朝向为准）；
 *   - 背光 GPIO48 绑 LEDC PWM（5kHz, 8-bit），5 档 = 0/50/110/180/255，
 *     Light-Sleep 前平滑淡出到 0。
 *
 * HAL 层只返回 hal_status_t 语义码，**严禁在此拼装面向用户的中文文案**
 * （doc/02 §六 强制），错误一律由 UI 层经 ui_show_friendly_error(ErrorCode) 渲染。
 *
 * LovyanGFX 1.1.16 API 要点（已逐符号核对上游 1.1.16 tag 源码）：
 *   - 命名空间是 `lgfx::v1::`，不是 `lgfx::`；
 *   - 背光对象挂载用 `panel.setLight(&bl)`，**没有 `setBacklight()` 这个方法**；
 *   - 等 DMA 完成用 `waitDisplay()`，**没有 `wait()`**；
 *   - Bus_SPI config 字段：spi_host / dma_channel / freq_write / freq_read /
 *     spi_mode / spi_3wire / use_lock / pin_sclk / pin_mosi / pin_miso / pin_dc；
 *   - Light_PWM config 字段：pin_bl / invert / freq / pwm_channel；
 *   - Panel config 字段在 Panel_Device：pin_cs / pin_rst / pin_busy /
 *     memory_width / memory_height / panel_width / panel_height /
 *     offset_x / offset_y / offset_rotation / dummy_read_pixel /
 *     dummy_read_bits / readable / dlen_16bit / bus_shared。
 */

#include "hal_interfaces.h"

#include <Arduino.h>

#include "board_config.h"

#include <LovyanGFX.hpp>

/* ------------------------------------------------------------------ 背光 PWM */
#define BL_PWM_CHANNEL 0
#define BL_PWM_FREQ    5000   /* 5kHz */
#define BL_PWM_BITS    8      /* 8-bit 分辨率 */

/* 5 档亮度表（doc/02 §3.1 强制值） */
static const uint8_t kBacklightLevels[6] = {0, 50, 110, 180, 255, 255};

static uint8_t s_backlight_level = 0; /* 当前档位 0~5 */
static uint8_t s_backlight_value = 0; /* 当前 PWM 占空比 0~255 */

/* ------------------------------------------------------------------ LovyanGFX 配置 */
class AbbotDisplay : public lgfx::v1::LGFX_Device {
    lgfx::v1::Bus_SPI bus_;
    lgfx::v1::Light_PWM bl_;
    lgfx::v1::Panel_ST7796 panel_;

public:
    AbbotDisplay(void)
    {
        {
            auto cfg = bus_.config();
            cfg.spi_host = SPI2_HOST;         /* FSPI */
            cfg.spi_mode = 0;
            cfg.freq_write = LCD_SPI_FREQ_HZ; /* 40MHz */
            cfg.freq_read = 16000000;
            cfg.spi_3wire = false;
            cfg.use_lock = true;
            /* dma_channel 不能写 0 —— 上游语义是「0 = 关闭 DMA 走 SPI」，
             * 非 0 才是 SPI_DMA_CH_AUTO。留默认（LGFX_ESP32_SPI_DMA_CH）即自动分配。 */
            cfg.pin_sclk = LCD_PIN_SCLK;
            cfg.pin_mosi = LCD_PIN_MOSI;
            cfg.pin_miso = -1;
            cfg.pin_dc = LCD_PIN_DC;
            bus_.config(cfg);
            panel_.setBus(&bus_);
        }
        {
            auto cfg = bl_.config();
            cfg.pin_bl = LCD_PIN_BL;
            cfg.invert = false;
            cfg.freq = BL_PWM_FREQ;
            cfg.pwm_channel = BL_PWM_CHANNEL;
            bl_.config(cfg);
            panel_.setLight(&bl_);   /* 上游是 setLight, 没有 setBacklight */
        }
        {
            auto cfg = panel_.config();
            cfg.pin_cs = LCD_PIN_CS;
            cfg.pin_rst = LCD_PIN_RST;
            cfg.pin_busy = -1;
            cfg.panel_width = LCD_WIDTH;
            cfg.panel_height = LCD_HEIGHT;
            cfg.memory_width = LCD_WIDTH;
            cfg.memory_height = LCD_HEIGHT;
            cfg.offset_x = 0;
            cfg.offset_y = 0;
            cfg.offset_rotation = 0;
            cfg.dummy_read_pixel = 8;
            cfg.dummy_read_bits = 1;
            cfg.readable = true;
            cfg.dlen_16bit = false;
            cfg.bus_shared = false;
            panel_.config(cfg);
        }
        setPanel(&panel_);
    }

    /* 背光淡出到 0（Light-Sleep 前调用），steps 为渐暗步数 */
    void fadeOutBacklight(uint16_t steps)
    {
        if (steps == 0) {
            steps = 1;
        }
        uint8_t from = s_backlight_value;
        for (uint16_t i = 1; i <= steps; i++) {
            uint16_t v = (uint16_t)((uint32_t)from * (steps - i) / steps);
            setBacklightDuty((uint8_t)v);
            delay(5);
        }
        setBacklightDuty(0);
    }

    void setBacklightDuty(uint8_t duty)
    {
        /* duty 是 uint8_t, 上限天然是 255 —— 不要再写 `duty > 255` 之类的检查,
         * 那会被 gcc 判成恒假 (-Werror=type-limits) 直接编译失败。 */
        s_backlight_value = duty;
        /* LGFX_Device::setBrightness(uint8_t) -> panel->setBrightness -> Light_PWM 写占空比 */
        setBrightness(duty);
    }
};

static AbbotDisplay s_display;

/* ------------------------------------------------------------------ 接口实现 */
hal_status_t hal_display_init(void)
{
    s_display.init();
    s_display.setRotation(0); /* 竖屏；实测排针朝向若反了改 2 */
    s_display.setColorDepth(16);
    s_display.setBrightness(0);

    /* 背光 LEDC 由 LovyanGFX 的 Light_PWM 自行管理（内部 ledcSetup/ledcAttachPin），
     * 此处不得重复配置同一通道 —— 重复配置会覆盖 LovyanGFX 的频率/分辨率设置。 */
    s_backlight_level = 0;
    s_backlight_value = 0;
    return HAL_OK;
}

hal_status_t hal_display_flush(void)
{
    /* LovyanGFX 的 DMA 刷新在调用方绘制结束后由 lvgl 的 flush_cb 驱动；
     * 这里保证缓冲区确实已经送出去（等待 DMA 完成）。 */
    s_display.waitDisplay();
    return HAL_OK;
}

hal_status_t hal_display_set_backlight(uint8_t level)
{
    if (level > 5) {
        return HAL_ERR_INVALID;
    }
    s_backlight_level = level;
    s_backlight_value = kBacklightLevels[level];
    s_display.setBacklightDuty(s_backlight_value);
    return HAL_OK;
}

uint8_t hal_display_get_backlight(void)
{
    return s_backlight_level;
}
