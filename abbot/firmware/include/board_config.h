/**
 * board_config.h —— ESP32-S3 引脚与总线常量
 *
 * 唯一真相源: abbot/doc/01-硬件物料清单、接线拓扑与物理装配契约.md
 * 引脚矩阵为「零冲突」硬约束, 改任何一个都要先回 doc/01 核账。
 */
#ifndef ABBOT_BOARD_CONFIG_H
#define ABBOT_BOARD_CONFIG_H

#include <stdint.h>

/* ===================== 屏幕 ST7796S (SPI2 / FSPI) ===================== */
#define LCD_PIN_MOSI   45   /* SDA  纯高阻输入脚, 无上拉 */
#define LCD_PIN_SCLK   46   /* SCL  纯高阻输入脚, 无上拉 */
#define LCD_PIN_CS     44   /* 片选, 低有效 */
#define LCD_PIN_DC     47   /* 数据/命令 */
#define LCD_PIN_RST    21   /* 硬件复位 */
#define LCD_PIN_BL     48   /* 背光 PWM */
#define LCD_WIDTH      320
#define LCD_HEIGHT     480
#define LCD_SPI_FREQ_HZ 40000000  /* 40MHz */

/* ===================== 摄像头 OV5640 (DVP) ===================== */
#define CAM_PIN_SIOD   4    /* I2C_NUM_0 SDA, 与 MAX17043 共用 */
#define CAM_PIN_SIOC   5    /* I2C_NUM_0 SCL, 与 MAX17043 共用 */
#define CAM_PIN_VSYNC  6
#define CAM_PIN_HREF   7
#define CAM_PIN_PCLK   13
#define CAM_PIN_XCLK   15
#define CAM_XCLK_HZ    12000000  /* XCLK 锁定 12MHz */

/* D0~D7 数据线, 顺序必须与 doc/01 一致 */
#define CAM_PIN_D0     11
#define CAM_PIN_D1     9
#define CAM_PIN_D2     8
#define CAM_PIN_D3     10
#define CAM_PIN_D4     12
#define CAM_PIN_D5     18
#define CAM_PIN_D6     17
#define CAM_PIN_D7     16

/* ===================== 电量计 MAX17043 (I2C_NUM_0) ===================== */
#define POWER_I2C_ADDR 0x36

/* ===================== 共享 I2C 总线 (doc/02 §2.1) =====================
 * OV5640 的 SCCB 与 MAX17043 **物理共享** I2C_NUM_0，总线频率统一锁定 100kHz。
 * 摄像头侧不另装驱动：camera_config.pin_sccb_sda = -1 + sccb_i2c_port = I2C_NUM_0，
 * 直接复用这里配置的 Wire 总线；MAX17043 也走同一条总线。
 * 因此所有跨设备访问必须持 g_i2c_bus_mutex（见 hal_shared.h），
 * 且**拍照捕获帧数据期间严禁轮询电量计**。
 */
#define I2C_BUS_FREQ_HZ 100000
#define POWER_I2C_SDA 4   /* 与 CAM_PIN_SIOD 同一根线 */
#define POWER_I2C_SCL 5   /* 与 CAM_PIN_SIOC 同一根线 */

/* ===================== 摇杆 (ADC1) ===================== */
#define JOY_PIN_VRX    2    /* ADC1_CH1, 左右 */
#define JOY_PIN_VRY    3    /* ADC1_CH2, 上下 */
#define JOY_PIN_SW     0    /* 下压, 低有效; 开机瞬间严禁按压 */

/* 摇杆模拟轴中心值与死区 —— 真机标定后回填, 见 doc/03 摇杆仲裁表
 * 死区取 doc/02 §3.4 与 §五 防线表 #3 的 **±650**（12-bit ADC 满量程 0~4095）。
 * 中心值不写死：HAL 开机动态校准（300ms 内连采 20 次取均值），
 * 这里的 JOY_ADC_CENTER 仅作校准失败时的兜底。
 */
#define JOY_ADC_CENTER 2048
#define JOY_ADC_DEADBAND 650

/* 长按阈值: HAL 层唯一裁定来源, UI 层严禁另设阈值 */
#define LONG_PRESS_MS  600

/* ===================== 音频 (I2S_NUM_0, 半双工) ===================== */
#define AUDIO_PIN_MIC_SD   1   /* ICS43434 硅麦输出 */
#define AUDIO_PIN_AMP_DIN  14  /* MAX98357 功放输入 */
#define AUDIO_PIN_BCLK     41  /* 功放 BCLK 与硅麦 SCK 并接 */
#define AUDIO_PIN_LRCLK    42  /* 功放 LRCLK 与硅麦 WS 并接 */
#define AUDIO_SAMPLE_RATE   16000
#define AUDIO_BITS         16
#define AUDIO_CHANNELS     1
/* 切入 AUDIO_RECORDING 后丢弃的唤醒瞬态采样点数: 30ms @16kHz */
#define AUDIO_WARMUP_DISCARD_SAMPLES 480

/* ===================== TF 卡 (板载 SD_MMC 1-Bit) ===================== */
/* 注意参数顺序: CLK 在前, CMD 在后 */
#define TF_PIN_CLK 39
#define TF_PIN_CMD 38
#define TF_PIN_D0  40

/* ===================== 保留脚 (严禁外接) =====================
 * GPIO 19/20  原生 USB D-/D+  (USB_CDC_ON_BOOT=1 烧录调试)
 * GPIO 26~37 内部 8 线 OPI PSRAM / Flash
 * GPIO 43    原生 U0TXD
 */

#endif /* ABBOT_BOARD_CONFIG_H */
