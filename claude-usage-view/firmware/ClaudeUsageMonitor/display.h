#ifndef DISPLAY_H
#define DISPLAY_H

#include <TFT_eSPI.h>
#include "config.h"
#include "data_types.h"

enum StatusKind {
  STATUS_OK,
  STATUS_STALE,     // bridge served old data (token expired etc.)
  STATUS_OFFLINE,   // bridge unreachable
  STATUS_NO_WIFI,
};

class Display {
public:
  void begin();
  void drawSplashScreen();
  void drawWiFiSetupScreen();
  void drawConnectingScreen(const char* msg);
  void drawErrorScreen(const char* msg, const char* detail = nullptr);

  // Main screen. Draws static layout once, then updates only changed values.
  void drawUsage(const UsageData& data, StatusKind status, int failCount);

  // Force full redraw on next drawUsage (after leaving another screen).
  void invalidateLayout() { _layoutDrawn = false; }

  TFT_eSPI& getTFT() { return _tft; }

private:
  TFT_eSPI _tft;
  bool _layoutDrawn = false;
  UsageData _prev;
  StatusKind _prevStatus = STATUS_OK;
  char _prevUpdatedAt[8] = {0};

  void drawStaticLayout();
  void updateCard(int y0, int pct, const char* resetsLocal, int resetsInMin,
                  const char* severity, int prevPct, int prevMin);
  void updateBottomStrip(const UsageData& data, StatusKind status, int failCount);
  uint16_t usageColor(int pct, const char* severity);
};

#endif // DISPLAY_H
