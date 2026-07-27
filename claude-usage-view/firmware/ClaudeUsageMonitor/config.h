#ifndef CONFIG_H
#define CONFIG_H

#include <Arduino.h>

// ============================================================
// Claude Usage Monitor — Configuration
// Board: Sunton CYD 4.0" (ESP32-4827S040)
// Display: ST7796S 320x480 SPI (used in landscape, 480x320)
// ============================================================

#define FIRMWARE_VERSION  "v1.0.0"

#define BOARD_NAME        "CYD 4.0\""
// Panel is portrait-native 320x480; rotation 1 = landscape 480x320.
#define DISPLAY_ROTATION  1
#define SCREEN_W          480
#define SCREEN_H          320
#define TFT_BL_PIN        27

// Touch (XPT2046) is unused, but on the 4.0" it shares the display SPI bus.
// Its CS must be held HIGH so the chip never drives MISO during TFT traffic.
#define TOUCH_CS_PIN      33

// --- Boot button (reset config) ---
#define BOOT_BUTTON_PIN    0
#define BOOT_LONG_PRESS_MS 3000  // Hold 3s to reset WiFi config

// --- Colors (RGB565; ST7796 CYDs are RGB order) ---
#define RGB565(r,g,b) ((((r)&0xF8)<<8) | (((g)&0xFC)<<3) | ((b)>>3))

#define COLOR_BG           RGB565(0x0d,0x11,0x17)  // #0d1117
#define COLOR_HEADER_BG    RGB565(0x16,0x1b,0x22)  // #161b22
#define COLOR_CARD_BG      RGB565(0x16,0x1b,0x22)
#define COLOR_BORDER       RGB565(0x30,0x36,0x3d)  // #30363d
#define COLOR_BAR_TRACK    RGB565(0x21,0x26,0x2d)
#define COLOR_GREEN        RGB565(0x66,0xbb,0x6a)  // #66bb6a
#define COLOR_YELLOW       RGB565(0xff,0xc1,0x07)  // #ffc107
#define COLOR_RED          RGB565(0xf4,0x43,0x36)  // #f44336
#define COLOR_ORANGE       RGB565(0xd9,0x77,0x57)  // Claude terracotta #d97757
#define COLOR_TEXT_PRIMARY RGB565(0xe6,0xed,0xf3)  // #e6edf3
#define COLOR_TEXT_DIM     RGB565(0x7d,0x85,0x90)  // #7d8590
#define COLOR_BLACK        0x0000
#define COLOR_WHITE        0xFFFF

// --- Usage thresholds (percent) ---
#define USAGE_WARN_PCT     70   // >= yellow
#define USAGE_CRIT_PCT     90   // >= red

// --- Timing (ms) ---
#define DATA_POLL_INTERVAL   60000   // Fetch usage from bridge every 60s
#define WIFI_CHECK_INTERVAL  10000   // Check WiFi every 10s
#define HTTP_TIMEOUT_MS      10000

// --- Data limits ---
#define MAX_URL_LEN     100
#define MAX_ERROR_LEN   96
#define OFFLINE_AFTER_FAILS 2   // show offline banner after N consecutive fails

// --- Network ---
// The bridge runs on the Axon rig (Linux laptop hosting AKSUMAEL). Set this
// to the rig's LAN IP, or override it at runtime via the WiFiManager portal.
#define WIFI_AP_NAME        "ClaudeUsage-Config"
#define DEFAULT_BRIDGE_URL  "http://192.168.1.100:8787/usage"  // <- Axon rig IP

// --- Local DEV overrides (gitignored) ---
// Create ClaudeUsageMonitor/config_local.h from config_local.h.example
// to force WiFi/bridge URL during development.
#if __has_include("config_local.h")
  #include "config_local.h"
#endif

// --- Debug ---
#define DEBUG 1
#if DEBUG
  #define DBG(x)    Serial.print(x)
  #define DBGLN(x)  Serial.println(x)
  #define DBGF(...) Serial.printf(__VA_ARGS__)
#else
  #define DBG(x)
  #define DBGLN(x)
  #define DBGF(...)
#endif

#endif // CONFIG_H
