/**
 * WiFi.h —— **主机侧 stub**，只为让 `src/net/net_api.cpp` 能在 host gcc 上做语法检查。
 *
 * 签名逐条抄自 arduino-esp32 **2.0.17** 的真实头文件：
 *   - libraries/WiFi/src/WiFiSTA.h     （begin / setAutoReconnect / status / RSSI / localIP）
 *   - libraries/WiFi/src/WiFiGeneric.h （mode / setSleep）
 *   - libraries/WiFi/src/WiFiScan.h    （scanNetworks / scanComplete / scanDelete / SSID / encryptionType）
 *   - libraries/WiFi/src/WiFiClient.h  （connect / write / available / read / stop / connected / setTimeout）
 *
 * ⚠️ 注意两处**真实签名与直觉不同**的地方（本 stub 就是为了别踩）：
 *   1. `WiFiClient::setTimeout(uint32_t seconds)` 收的是**秒**，不是毫秒
 *      （实现里 `Client::setTimeout(seconds * 1000)`）—— 传毫秒会被当成几小时。
 *   2. `SSID/encryptionType/RSSI(uint8_t)` 的索引是 **uint8_t**，不是 int。
 */
#ifndef ABBOT_STUB_WIFI_H
#define ABBOT_STUB_WIFI_H

#include "Arduino.h"

typedef enum {
    WIFI_OFF = 0,
    WIFI_STA = 1,
    WIFI_AP = 2,
    WIFI_AP_STA = 3,
} wifi_mode_t;

typedef enum {
    WL_IDLE_STATUS = 0,
    WL_NO_SSID_AVAIL = 1,
    WL_CONNECTED = 3,
    WL_CONNECT_FAILED = 4,
    WL_DISCONNECTED = 6,
} wl_status_t;

typedef enum {
    WIFI_AUTH_OPEN = 0,
    WIFI_AUTH_WEP = 1,
    WIFI_AUTH_WPA_PSK = 2,
    WIFI_AUTH_WPA2_PSK = 3,
} wifi_auth_mode_t;

class IPAddress {
public:
    String toString() const { return String("0.0.0.0"); }
};

class WiFiClient {
public:
    int connect(IPAddress ip, uint16_t port, int32_t timeout);
    int connect(const char *host, uint16_t port, int32_t timeout);
    /* ⚠️ 收的是**秒**（真实实现 seconds * 1000） */
    int setTimeout(uint32_t seconds);
    size_t write(uint8_t data);
    size_t write(const uint8_t *buf, size_t size);
    int available();
    int read();
    int read(uint8_t *buf, size_t size);
    /* 抄自 cores/esp32/Stream.h 的 readBytes 家族 */
    size_t readBytes(uint8_t *buffer, size_t length);
    size_t readBytes(char *buffer, size_t length);
    void stop();
    uint8_t connected();
    size_t print(const char *s);
    size_t print(unsigned long n);
    size_t print(const String &s);
    size_t print(char c);
};

class WiFiClass {
public:
    static bool mode(wifi_mode_t m);
    bool setAutoReconnect(bool autoReconnect);
    bool setSleep(bool enabled);
    wl_status_t begin(const char *ssid, const char *passphrase = nullptr,
                      int32_t channel = 0, const uint8_t *bssid = nullptr, bool connect = true);
    static wl_status_t status();
    int16_t scanNetworks(bool async = false, bool show_hidden = false, bool passive = false,
                         uint32_t max_ms_per_chan = 300, uint8_t channel = 0,
                         const char *ssid = nullptr, const uint8_t *bssid = nullptr);
    int16_t scanComplete();
    void scanDelete();
    String SSID(uint8_t networkItem);
    wifi_auth_mode_t encryptionType(uint8_t networkItem);
    int32_t RSSI(uint8_t networkItem);
    int8_t RSSI();
    IPAddress localIP();
};

extern WiFiClass WiFi;

#endif /* ABBOT_STUB_WIFI_H */
