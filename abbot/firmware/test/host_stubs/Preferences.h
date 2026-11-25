/**
 * Preferences.h —— **主机侧 stub**，只为让 `src/net/net_api.cpp` 能在 host gcc 上做语法检查。
 *
 * 签名抄自 arduino-esp32 **2.0.17** 的真实头文件
 * `libraries/Preferences/src/Preferences.h`：
 *   - `bool begin(const char* name, bool readOnly=false, const char* partition_label=NULL)`
 *   - `String getString(const char* key, String defaultValue = String())`
 *   - `size_t putString(const char* key, const char* value)`
 *   - `void end()`
 */
#ifndef ABBOT_STUB_PREFERENCES_H
#define ABBOT_STUB_PREFERENCES_H

#include "Arduino.h"

class Preferences {
public:
    bool begin(const char *name, bool readOnly = false, const char *partition_label = nullptr);
    void end();
    size_t putString(const char *key, const char *value);
    size_t putString(const char *key, String value);
    String getString(const char *key, String defaultValue = String());
    bool remove(const char *key);
    bool clear();
};

#endif /* ABBOT_STUB_PREFERENCES_H */
