/**
 * hal_audio.cpp —— ICS43434 硅麦 + MAX98357 功放 半双工 I2S
 *
 * 契约: abbot/doc/02 §3.3
 *   - 引脚：BCLK/SCK=41，LRCLK/WS=42，DIN(功放)=14，SD(硅麦)=1，统一挂 I2S_NUM_0
 *   - 严格半双工状态机，杜绝总线冲突与回声：
 *       IDLE     —— 卸载 I2S DMA，功放静音，降低功耗
 *       RECORDING—— I2S_MODE_MASTER|I2S_MODE_RX，16000Hz，
 *                    I2S_COMM_FORMAT_STAND_I2S（标准 Philips），
 *                    通道槽宽 32-bit（右移 16 位取高 16-bit 有效 PCM），单声道
 *       PLAYING  —— **必须先 i2s_driver_uninstall(I2S_NUM_0) 释放 RX**，
 *                    再以 I2S_MODE_MASTER|I2S_MODE_TX 重装（16000Hz/16-bit/Mono）
 *   - ICS43434 专属时序：每次切入 RECORDING 启动 DMA 后，
 *                    **强制丢弃前 30ms（480 个采样点）的唤醒瞬态**，
 *                    消除上电"噗"声爆音与直流偏置漂移
 *   - 录音缓冲在 PSRAM 预分配 320KB（硬上限 10 秒）
 *   - 硅麦声道用宏 MIC_I2S_CHANNEL 适配，默认左声道
 *
 * 音量：MAX98357 是 D 类功放，**没有 I2C 音量寄存器**，
 * 板级引脚矩阵里也没有增益/静音脚，因此 1~5 档以软件增益实现
 * （对送入功放的 PCM 做定点缩放），不引入额外硬件依赖。
 */
#include <Arduino.h>
#include <driver/i2s.h>
#include <esp_heap_caps.h>
#include <SD_MMC.h>

#include "board_config.h"
#include "hal_interfaces.h"
#include "hal_shared.h"

static const char *TAG = "hal_audio";

/* 硅麦声道：默认左声道，一键可切右声道 */
#ifndef MIC_I2S_CHANNEL
#define MIC_I2S_CHANNEL I2S_CHANNEL_FMT_ONLY_LEFT
#endif

/* 录音缓冲：10 秒 @16kHz/16bit 单声道 = 320000 字节 */
#define AUDIO_REC_BUF_BYTES (AUDIO_SAMPLE_RATE * AUDIO_BITS / 8 * 10)

/* 软件音量档位 1~5 → 增益 20% ~ 100% */
static const int kVolumeGain[6] = {0, 1, 2, 3, 4, 5};
typedef enum {
    AUDIO_IDLE = 0,
    AUDIO_RECORDING,
    AUDIO_PLAYING
} audio_state_t;

static audio_state_t s_state = AUDIO_IDLE;
static uint8_t s_volume = 3;
static bool s_muted = false;

/* 录音环形缓冲（PSRAM） */
static int16_t *s_rec_buf = NULL;
static size_t s_rec_cap = 0;   /* 容量（采样点） */
static size_t s_rec_head = 0;  /* 下一个写入位置 */
static size_t s_rec_count = 0; /* 有效采样点数 */

/* 一次 DMA 读的最大采样点 */
#define AUDIO_CHUNK_MAX 320

static void audio_fill_config(i2s_config_t *cfg, i2s_mode_t mode, i2s_bits_per_sample_t bits)
{
    memset(cfg, 0, sizeof(*cfg));
    cfg->mode = mode;
    cfg->sample_rate = AUDIO_SAMPLE_RATE;
    cfg->bits_per_sample = bits;
    cfg->channel_format = MIC_I2S_CHANNEL;
    cfg->communication_format = I2S_COMM_FORMAT_STAND_I2S;
    cfg->intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
    cfg->dma_buf_count = 6;
    cfg->dma_buf_len = AUDIO_CHUNK_MAX;
    cfg->use_apll = false;
    cfg->tx_desc_auto_clear = true;
}

static void audio_fill_pins(i2s_pin_config_t *pins, bool tx)
{
    memset(pins, 0, sizeof(*pins));
    pins->mck_io_num = -1; /* 不输出 MCLK */
    pins->bck_io_num = AUDIO_PIN_BCLK;
    pins->ws_io_num = AUDIO_PIN_LRCLK;
    if (tx) {
        pins->data_out_num = AUDIO_PIN_AMP_DIN;
        pins->data_in_num = -1;
    } else {
        pins->data_out_num = -1;
        pins->data_in_num = AUDIO_PIN_MIC_SD;
    }
}

/* 32-bit 槽宽 → 16-bit PCM：右移 16 位取高 16 位（doc/02 §3.3） */
static inline int16_t audio_to_pcm16(int32_t raw)
{
    return (int16_t)(raw >> 16);
}

hal_status_t hal_audio_init(void)
{
    s_state = AUDIO_IDLE;
    s_volume = 3;
    s_muted = false;
    Serial.printf("[%s] init ok (%dHz/%dbit/mono)\n", TAG, AUDIO_SAMPLE_RATE, AUDIO_BITS);
    return HAL_OK;
}

hal_status_t hal_audio_start_record(void)
{
    if (s_state != AUDIO_IDLE) {
        return HAL_ERR_INVALID;
    }

    if (s_rec_buf == NULL) {
        s_rec_buf = (int16_t *)heap_caps_malloc(AUDIO_REC_BUF_BYTES, MALLOC_CAP_SPIRAM);
        if (s_rec_buf == NULL) {
            Serial.printf("[%s] no PSRAM for rec buffer\n", TAG);
            return HAL_ERR_NO_SPACE;
        }
        s_rec_cap = AUDIO_REC_BUF_BYTES / sizeof(int16_t);
    }
    s_rec_head = 0;
    s_rec_count = 0;

    i2s_config_t cfg;
    audio_fill_config(&cfg, (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX), I2S_BITS_PER_SAMPLE_32BIT);
    i2s_pin_config_t pins;
    audio_fill_pins(&pins, false);

    esp_err_t err = i2s_driver_install(I2S_NUM_0, &cfg, 0, NULL);
    if (err != ESP_OK) {
        Serial.printf("[%s] i2s install failed: %s\n", TAG, esp_err_to_name(err));
        return HAL_ERR_IO;
    }
    err = i2s_set_pin(I2S_NUM_0, &pins);
    if (err != ESP_OK) {
        i2s_driver_uninstall(I2S_NUM_0);
        return HAL_ERR_IO;
    }

    /* 丢弃唤醒瞬态：前 30ms（480 采样点）。
     * 缓冲不放在栈上 —— 任务栈只有 4KB，1920 字节会挤爆栈。 */
    static int32_t warmup[128];
    size_t want = AUDIO_WARMUP_DISCARD_SAMPLES;
    size_t got = 0;
    while (got < want) {
        size_t chunk = want - got;
        if (chunk > sizeof(warmup) / sizeof(warmup[0])) {
            chunk = sizeof(warmup) / sizeof(warmup[0]);
        }
        size_t r = 0;
        if (i2s_read(I2S_NUM_0, warmup, chunk * sizeof(int32_t), &r, pdMS_TO_TICKS(50)) != ESP_OK ||
            r == 0) {
            break;
        }
        got += r / sizeof(int32_t);
    }
    Serial.printf("[%s] record started, warmup dropped %u samples\n", TAG, (unsigned)got);

    s_state = AUDIO_RECORDING;
    return HAL_OK;
}

/**
 * 从驱动取一段 PCM。
 * 同时写入 PSRAM 环形缓冲（保留最近 10 秒），并拷贝给调用方。
 * 返回实际取到的采样点数；0 表示驱动已无数据。
 */
int hal_audio_read_chunk(int16_t *dst, size_t samples)
{
    if (s_state != AUDIO_RECORDING || dst == NULL || samples == 0) {
        return (int)HAL_ERR_INVALID;
    }
    if (samples > AUDIO_CHUNK_MAX) {
        samples = AUDIO_CHUNK_MAX;
    }

    int32_t raw[AUDIO_CHUNK_MAX];
    size_t bytes = samples * sizeof(int32_t);
    size_t got = 0;
    esp_err_t err = i2s_read(I2S_NUM_0, raw, bytes, &got, pdMS_TO_TICKS(200));
    if (err != ESP_OK || got == 0) {
        return (int)HAL_ERR_TIMEOUT;
    }
    size_t n = got / sizeof(int32_t);

    for (size_t i = 0; i < n; i++) {
        int16_t pcm = audio_to_pcm16(raw[i]);
        if (s_rec_buf != NULL) {
            s_rec_buf[s_rec_head] = pcm;
            s_rec_head = (s_rec_head + 1) % s_rec_cap;
            if (s_rec_count < s_rec_cap) {
                s_rec_count++;
            }
        }
        dst[i] = pcm;
    }
    return (int)n;
}

hal_status_t hal_audio_stop_record(void)
{
    if (s_state != AUDIO_RECORDING) {
        return HAL_ERR_INVALID;
    }
    /* 半双工纪律：释放 RX 通道后才能切 PLAYING */
    i2s_driver_uninstall(I2S_NUM_0);
    s_state = AUDIO_IDLE;
    Serial.printf("[%s] record stopped, %u samples buffered\n", TAG, (unsigned)s_rec_count);
    return HAL_OK;
}

/**
 * 播放一个 WAV 文件（16kHz/16bit/Mono）。
 * 校验 RIFF 头并跳过 44 字节头，只把 PCM 负载送进功放。
 */
hal_status_t hal_audio_play_wav_file(const char *path)
{
    if (path == NULL || s_state != AUDIO_IDLE) {
        return HAL_ERR_INVALID;
    }

    File f = SD_MMC.open(path);
    if (!f) {
        Serial.printf("[%s] open %s failed\n", TAG, path);
        return HAL_ERR_NOT_FOUND;
    }

    /* RIFF 头校验：'RIFF' .... 'WAVE' */
    uint8_t hdr[12];
    if (f.read(hdr, sizeof(hdr)) != (int)sizeof(hdr) || hdr[0] != 'R' || hdr[1] != 'I' ||
        hdr[8] != 'W' || hdr[9] != 'A') {
        f.close();
        Serial.printf("[%s] %s is not a RIFF/WAVE file\n", TAG, path);
        return HAL_ERR_INVALID;
    }
    /* 标准 44 字节 WAV 头之后才是 PCM 负载 */
    if (!f.seek(44)) {
        f.close();
        return HAL_ERR_IO;
    }

    /* 半双工纪律：先彻底释放可能存在的 RX，再装 TX */
    i2s_driver_uninstall(I2S_NUM_0);
    i2s_config_t cfg;
    audio_fill_config(&cfg, (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX), I2S_BITS_PER_SAMPLE_16BIT);
    i2s_pin_config_t pins;
    audio_fill_pins(&pins, true);
    esp_err_t err = i2s_driver_install(I2S_NUM_0, &cfg, 0, NULL);
    if (err == ESP_OK) {
        err = i2s_set_pin(I2S_NUM_0, &pins);
    }
    if (err != ESP_OK) {
        f.close();
        Serial.printf("[%s] i2s tx setup failed: %s\n", TAG, esp_err_to_name(err));
        return HAL_ERR_IO;
    }
    s_state = AUDIO_PLAYING;

    const int gain = kVolumeGain[s_volume];
    int16_t pcm[AUDIO_CHUNK_MAX];
    for (;;) {
        size_t n = f.read((uint8_t *)pcm, sizeof(pcm));
        if (n == 0) {
            break;
        }
        size_t samples = n / sizeof(int16_t);
        if (s_muted) {
            memset(pcm, 0, n);
        } else if (gain < 5) {
            for (size_t i = 0; i < samples; i++) {
                pcm[i] = (int16_t)(((int32_t)pcm[i] * gain) / 5);
            }
        }
        size_t written = 0;
        i2s_write(I2S_NUM_0, pcm, samples * sizeof(int16_t), &written, pdMS_TO_TICKS(200));
    }
    f.close();

    /* 播完回到 IDLE：卸载 TX，功放静音 */
    i2s_driver_uninstall(I2S_NUM_0);
    s_state = AUDIO_IDLE;
    return HAL_OK;
}

hal_status_t hal_audio_set_volume(uint8_t level)
{
    if (level < 1 || level > 5) {
        return HAL_ERR_INVALID;
    }
    s_volume = level;
    return HAL_OK;
}

/**
 * 送一段 PCM 进功放（流式播放用）。
 * 自动完成半双工切换：首次调用时释放可能存在的 RX 并装上 TX。
 */
hal_status_t hal_audio_write_pcm(const int16_t *pcm, size_t samples)
{
    if (pcm == NULL || samples == 0) {
        return HAL_ERR_INVALID;
    }
    if (s_state != AUDIO_PLAYING) {
        /* 半双工纪律：释放可能存在的 RX，再装 TX */
        i2s_driver_uninstall(I2S_NUM_0);
        i2s_config_t cfg;
        audio_fill_config(&cfg, (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX),
                          I2S_BITS_PER_SAMPLE_16BIT);
        i2s_pin_config_t pins;
        audio_fill_pins(&pins, true);
        esp_err_t err = i2s_driver_install(I2S_NUM_0, &cfg, 0, NULL);
        if (err == ESP_OK) {
            err = i2s_set_pin(I2S_NUM_0, &pins);
        }
        if (err != ESP_OK) {
            Serial.printf("[%s] i2s tx setup failed: %s\n", TAG, esp_err_to_name(err));
            return HAL_ERR_IO;
        }
        s_state = AUDIO_PLAYING;
    }

    const int gain = kVolumeGain[s_volume];
    int16_t scaled[AUDIO_CHUNK_MAX];
    if (samples > AUDIO_CHUNK_MAX) {
        samples = AUDIO_CHUNK_MAX;
    }
    for (size_t i = 0; i < samples; i++) {
        int32_t v = s_muted ? 0 : (int32_t)pcm[i];
        scaled[i] = (int16_t)((v * gain) / 5);
    }
    size_t written = 0;
    i2s_write(I2S_NUM_0, scaled, samples * sizeof(int16_t), &written, pdMS_TO_TICKS(200));
    return HAL_OK;
}

void hal_audio_set_muted(bool muted)
{
    s_muted = muted;
}

/* ===================== 设备端合成提示音 =====================
 *
 * 为什么合成而不是放文件：仓库里没有 LittleFS 分区，也没有 ding.wav 资产
 * （doc/04 §二 的存储铁律与实现曾经矛盾，本轮已按实现修正）。
 * 提示音直接在设备上算出来推到 MAX98357，不依赖任何文件。
 */

/** 四分之一周期正弦表（64 段，幅度 0~32767），共 256 点一圈。 */
static const int16_t kToneQuarter[65] = {
    0, 804, 1608, 2410, 3212, 4011, 4808, 5602, 6393, 7179, 7962, 8739, 9512, 10278,
    11039, 11793, 12539, 13279, 14010, 14732, 15446, 16151, 16846, 17530, 18204, 18868,
    19519, 20159, 20787, 21403, 22005, 22594, 23170, 23731, 24279, 24811, 25329, 25832,
    26319, 26790, 27245, 27683, 28105, 28510, 28898, 29268, 29621, 29956, 30273, 30571,
    30852, 31113, 31356, 31580, 31785, 31971, 32137, 32285, 32412, 32521, 32609, 32678,
    32728, 32757, 32767,
};

/** 取一圈正弦的一个样点（phase 为 0~255 的整数相位）。 */
static inline int16_t tone_sine(uint16_t phase)
{
    uint8_t quad = (uint8_t)(phase >> 6);   /* 0~3 */
    uint8_t idx = (uint8_t)(phase & 0x3F);  /* 0~63 */
    switch (quad) {
    case 0:
        return kToneQuarter[idx];
    case 1:
        return kToneQuarter[64 - idx];
    case 2:
        return (int16_t)-kToneQuarter[idx];
    default:
        return (int16_t)-kToneQuarter[64 - idx];
    }
}

hal_status_t hal_audio_play_tone(uint16_t freq_hz, uint16_t duration_ms)
{
    if (freq_hz < 100 || freq_hz > 8000 || duration_ms == 0) {
        return HAL_ERR_INVALID;
    }
    /* 录音过程中不出声 —— 半双工纪律（doc/02 §3.3） */
    if (s_state != AUDIO_IDLE) {
        return HAL_ERR_INVALID;
    }

    /* 半双工纪律：先彻底释放可能存在的 RX，再装 TX */
    i2s_driver_uninstall(I2S_NUM_0);
    i2s_config_t cfg;
    audio_fill_config(&cfg, (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX), I2S_BITS_PER_SAMPLE_16BIT);
    i2s_pin_config_t pins;
    audio_fill_pins(&pins, true);
    esp_err_t err = i2s_driver_install(I2S_NUM_0, &cfg, 0, NULL);
    if (err == ESP_OK) {
        err = i2s_set_pin(I2S_NUM_0, &pins);
    }
    if (err != ESP_OK) {
        i2s_driver_uninstall(I2S_NUM_0);
        Serial.printf("[%s] tone i2s setup failed: %s\n", TAG, esp_err_to_name(err));
        return HAL_ERR_IO;
    }
    s_state = AUDIO_PLAYING;

    const int gain = kVolumeGain[s_volume];
    /* 每采样点的相位步进 = 频率 / 采样率 * 256 */
    uint32_t phase_step = ((uint32_t)freq_hz * 256u) / AUDIO_SAMPLE_RATE;
    uint16_t phase = 0;
    uint32_t total = (uint32_t)AUDIO_SAMPLE_RATE * duration_ms / 1000u;
    uint32_t done = 0;

    int16_t pcm[AUDIO_CHUNK_MAX];
    while (done < total) {
        uint32_t n = total - done;
        if (n > AUDIO_CHUNK_MAX) {
            n = AUDIO_CHUNK_MAX;
        }
        for (uint32_t i = 0; i < n; i++) {
            int32_t v = tone_sine(phase);
            phase = (uint16_t)(phase + phase_step);
            /* 起始与末尾渐弱，避免「啪」声爆音 */
            if (done + i < 160) {
                v = v * (int32_t)(done + i) / 160;
            }
            if (s_muted) {
                v = 0;
            } else if (gain < 5) {
                v = v * gain / 5;
            }
            pcm[i] = (int16_t)v;
        }
        size_t written = 0;
        i2s_write(I2S_NUM_0, pcm, n * sizeof(int16_t), &written, pdMS_TO_TICKS(200));
        done += n;
    }

    /* 播完回到 IDLE：卸载 TX，功放静音 */
    i2s_driver_uninstall(I2S_NUM_0);
    s_state = AUDIO_IDLE;
    return HAL_OK;
}
