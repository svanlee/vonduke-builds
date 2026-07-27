#ifndef DATA_TYPES_H
#define DATA_TYPES_H

#include "config.h"

struct AppConfig {
  char bridgeUrl[MAX_URL_LEN];
};

// Mirrors the pre-digested JSON served by bridge/claude_usage_bridge.py.
struct UsageData {
  int  sessionPct;
  char sessionSeverity[16];
  char sessionResetsLocal[16];   // "2:00 PM"
  int  sessionResetsInMin;
  int  weeklyPct;
  char weeklySeverity[16];
  char weeklyResetsLocal[16];    // "Sun 3:00 AM"
  int  weeklyResetsInMin;
  int  opusPct;                  // -1 when absent
  bool stale;                    // bridge served cached data (token expired etc.)
  char updatedAtLocal[12];       // "12:34 PM"
};

enum AppState {
  STATE_WIFI_SETUP,
  STATE_FETCHING,
  STATE_DISPLAYING,
  STATE_ERROR,
};

struct AppData {
  AppState  state;
  AppConfig config;
  UsageData usage;
  bool      hasData;
  int       fetchFailCount;
  bool      screenDirty;
  char      lastError[MAX_ERROR_LEN];
  unsigned long lastDataFetch;
  unsigned long lastWifiCheck;
  unsigned long wifiLostTime;
};

#endif // DATA_TYPES_H
