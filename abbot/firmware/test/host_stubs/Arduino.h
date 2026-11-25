/**
 * Arduino.h —— **主机侧 stub**，只为让 `src/net/net_api.cpp` 能在 host gcc 上做语法检查。
 *
 * 签名逐条抄自 arduino-esp32 **2.0.17** 的真实头文件：
 *   - cores/esp32/Arduino.h        （configTime / getLocalTime 声明在此，不在 esp32-hal-time.h）
 *   - cores/esp32/Esp.h            （ESP.getEfuseMac）
 *   - cores/esp32/Print.h          （print 家族）
 *   - cores/esp32/Stream.h
 *   - cores/esp32/HardwareSerial.h （Serial.printf）
 *
 * ⚠️ 本目录**不进固件构建**（platformio.ini 的 src 只含 src/），只在 CI/本机的
 *    主机侧语法检查里用 `-I` 引进来。它存在的意义：固件编译只发生在 GitHub Actions，
 *    而 Actions 一轮只能暴露一个错误；本机用 host gcc 就能一次抓出全部
 *    `-Wall -Wextra -Werror` 问题（格式串截断、类型不匹配等）。
 *
 * 若升级 Arduino core 版本，**必须同步更新本 stub** —— 否则检查结果会与真实构建脱节。
 */
#ifndef ABBOT_STUB_ARDUINO_H
#define ABBOT_STUB_ARDUINO_H

#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <ctime>
#include <string>

/* ---- 极简 String（只需 c_str / 构造自 const char*）---- */
class String {
public:
    String() : s_("") {}
    String(const char *s) : s_(s ? s : "") {}
    const char *c_str() const { return s_.c_str(); }
    size_t length() const { return s_.size(); }

private:
    std::string s_;
};

/* ---- 时间（抄自 cores/esp32/Arduino.h:222-224）---- */
extern "C" bool getLocalTime(struct tm *info, uint32_t ms = 5000);
extern "C" void configTime(long gmtOffset_sec, int daylightOffset_sec,
        const char *server1, const char *server2 = nullptr, const char *server3 = nullptr);
extern "C" void configTzTime(const char *tz,
        const char *server1, const char *server2 = nullptr, const char *server3 = nullptr);

/* ---- 杂项 ---- */
unsigned long millis();
void delay(unsigned long ms);
uint32_t random(uint32_t howbig);

/* ---- Serial（抄自 cores/esp32/HardwareSerial.h 的 printf 家族）---- */
class StubSerial {
public:
    size_t printf(const char *format, ...) __attribute__((format(printf, 2, 3)));
    size_t print(const char *s);
    size_t print(unsigned long n);
};
extern StubSerial Serial;

/* ---- ESP（抄自 cores/esp32/Esp.h:108）---- */
class EspClass {
public:
    uint64_t getEfuseMac();
};
extern EspClass ESP;

#endif /* ABBOT_STUB_ARDUINO_H */
