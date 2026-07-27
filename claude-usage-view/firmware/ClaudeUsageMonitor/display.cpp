#include "display.h"

// ---- Layout constants (landscape 320x240, LAFVIN ST7789) ----
// Reworked from the upstream 480x320 layout to fit the shorter 240px panel:
// header + two stacked cards + a status strip, all derived from SCREEN_*.
static const int HEADER_H   = 26;
static const int CARD_X     = 6;
static const int CARD_W     = SCREEN_W - 12;   // 308
static const int CARD_H     = 88;
static const int CARD1_Y    = 30;              // session
static const int CARD2_Y    = 122;             // weekly  (30 + 88 + 4 gap)
static const int STRIP_Y    = 216;             // bottom strip (216..240)
static const int PAD        = 10;

// Big-percent glyph font. Font 6 is the 48px "1234567890:-.apm" set — digits
// only, which is all the percent needs, and it fits an 88px card (font 8's
// 75px glyphs do not). Letters elsewhere use full-ASCII fonts 2/4.
static const int PCT_FONT   = 6;

// "in 1h25" / "in 45min" / "in 3d14h"
static void formatMinutes(int minutes, char* buf, size_t len) {
  if (minutes <= 0) {
    snprintf(buf, len, "now");
  } else if (minutes < 60) {
    snprintf(buf, len, "in %dmin", minutes);
  } else if (minutes < 48 * 60) {
    snprintf(buf, len, "in %dh%02d", minutes / 60, minutes % 60);
  } else {
    snprintf(buf, len, "in %dd%dh", minutes / (24 * 60), (minutes % (24 * 60)) / 60);
  }
}

void Display::begin() {
  _tft.init();
  _tft.setRotation(DISPLAY_ROTATION);
  _tft.fillScreen(COLOR_BG);

  pinMode(TFT_BL_PIN, OUTPUT);
  digitalWrite(TFT_BL_PIN, HIGH);
}

void Display::drawSplashScreen() {
  _tft.fillScreen(COLOR_BG);
  _tft.setTextDatum(MC_DATUM);
  _tft.setTextColor(COLOR_ORANGE, COLOR_BG);
  // Font 4 (full ASCII) — font 6 has digits only and would render blank here.
  _tft.drawString("Claude Usage", SCREEN_W / 2, SCREEN_H / 2 - 24, 4);
  _tft.setTextColor(COLOR_TEXT_DIM, COLOR_BG);
  _tft.drawString("Monitor " FIRMWARE_VERSION, SCREEN_W / 2, SCREEN_H / 2 + 12, 2);
  _tft.drawString(BOARD_NAME, SCREEN_W / 2, SCREEN_H / 2 + 34, 2);
  _layoutDrawn = false;
}

void Display::drawWiFiSetupScreen() {
  _tft.fillScreen(COLOR_BG);
  _tft.setTextDatum(MC_DATUM);
  _tft.setTextColor(COLOR_TEXT_PRIMARY, COLOR_BG);
  _tft.drawString("WiFi Setup", SCREEN_W / 2, 34, 4);
  _tft.setTextColor(COLOR_TEXT_DIM, COLOR_BG);
  _tft.drawString("Connect to the network:", SCREEN_W / 2, 80, 2);
  _tft.setTextColor(COLOR_ORANGE, COLOR_BG);
  _tft.drawString(WIFI_AP_NAME, SCREEN_W / 2, 112, 4);
  _tft.setTextColor(COLOR_TEXT_DIM, COLOR_BG);
  _tft.drawString("then open 192.168.4.1 to set", SCREEN_W / 2, 158, 2);
  _tft.drawString("WiFi + Bridge URL", SCREEN_W / 2, 182, 2);
  _layoutDrawn = false;
}

void Display::drawConnectingScreen(const char* msg) {
  _tft.fillScreen(COLOR_BG);
  _tft.setTextDatum(MC_DATUM);
  _tft.setTextColor(COLOR_TEXT_PRIMARY, COLOR_BG);
  _tft.drawString(msg, SCREEN_W / 2, SCREEN_H / 2, 4);
  _layoutDrawn = false;
}

void Display::drawErrorScreen(const char* msg, const char* detail) {
  _tft.fillScreen(COLOR_BG);
  _tft.setTextDatum(MC_DATUM);
  _tft.setTextColor(COLOR_RED, COLOR_BG);
  _tft.drawString("Error", SCREEN_W / 2, SCREEN_H / 2 - 50, 4);
  _tft.setTextColor(COLOR_TEXT_DIM, COLOR_BG);
  _tft.drawString(msg, SCREEN_W / 2, SCREEN_H / 2 - 6, 2);
  if (detail) {
    _tft.setTextColor(COLOR_ORANGE, COLOR_BG);
    _tft.drawString(detail, SCREEN_W / 2, SCREEN_H / 2 + 24, 2);
  }
  _tft.setTextColor(COLOR_TEXT_DIM, COLOR_BG);
  _tft.drawString("retrying in 30s... (hold BOOT 3s to reconfigure)", SCREEN_W / 2, SCREEN_H / 2 + 60, 2);
  _layoutDrawn = false;
}

uint16_t Display::usageColor(int pct, const char* severity) {
  // Severity from the API overrides the percent thresholds when not "normal".
  if (severity && strcmp(severity, "normal") != 0) {
    if (strcmp(severity, "warning") == 0) return COLOR_YELLOW;
    return COLOR_RED;
  }
  if (pct >= USAGE_CRIT_PCT) return COLOR_RED;
  if (pct >= USAGE_WARN_PCT) return COLOR_YELLOW;
  return COLOR_GREEN;
}

void Display::drawStaticLayout() {
  _tft.fillScreen(COLOR_BG);

  // Header
  _tft.fillRect(0, 0, SCREEN_W, HEADER_H, COLOR_HEADER_BG);
  _tft.drawFastHLine(0, HEADER_H, SCREEN_W, COLOR_BORDER);
  _tft.setTextDatum(TL_DATUM);
  _tft.setTextColor(COLOR_ORANGE, COLOR_HEADER_BG);
  // Font 2 title so it clears the 26px header bar.
  _tft.drawString("Claude Usage", PAD, 6, 2);

  // Cards
  _tft.drawRoundRect(CARD_X, CARD1_Y, CARD_W, CARD_H, 8, COLOR_BORDER);
  _tft.drawRoundRect(CARD_X, CARD2_Y, CARD_W, CARD_H, 8, COLOR_BORDER);

  _tft.setTextColor(COLOR_TEXT_DIM, COLOR_BG);
  _tft.drawString("SESSION (5h)", CARD_X + PAD, CARD1_Y + 6, 2);
  _tft.drawString("WEEK", CARD_X + PAD, CARD2_Y + 6, 2);

  // Force every dynamic field to repaint.
  memset(&_prev, 0xFF, sizeof(_prev));
  _prevUpdatedAt[0] = '\0';
  _prevStatus = (StatusKind)-1;
  _layoutDrawn = true;
}

void Display::updateCard(int y0, int pct, const char* resetsLocal, int resetsInMin,
                         const char* severity, int prevPct, int prevMin) {
  uint16_t color = usageColor(pct, severity);
  const int numY = y0 + 24;

  if (pct != prevPct) {
    // Big percent — PCT_FONT (6) is digits-only, draw "%" separately in font 4.
    _tft.setTextDatum(TL_DATUM);
    _tft.setTextColor(color, COLOR_BG);
    _tft.fillRect(CARD_X + PAD, numY, 150, 50, COLOR_BG);
    int numW = _tft.drawNumber(pct, CARD_X + PAD, numY, PCT_FONT);
    _tft.drawString("%", CARD_X + PAD + numW + 4, numY + 24, 4);
  }

  if (resetsInMin != prevMin) {
    // Line 1: the reset time ("Sun 3:00 AM"); line 2: the countdown.
    char line1[20], line2[28], countdown[20];
    snprintf(line1, sizeof(line1), "%s", resetsLocal);
    formatMinutes(resetsInMin, countdown, sizeof(countdown));
    snprintf(line2, sizeof(line2), "resets %s", countdown);

    _tft.setTextDatum(TR_DATUM);
    _tft.setTextColor(COLOR_TEXT_PRIMARY, COLOR_BG);
    _tft.setTextPadding(150);
    _tft.drawString(line1, CARD_X + CARD_W - PAD, y0 + 26, 2);
    _tft.setTextColor(COLOR_TEXT_DIM, COLOR_BG);
    _tft.drawString(line2, CARD_X + CARD_W - PAD, y0 + 46, 2);
    _tft.setTextPadding(0);
  }

  // Progress bar (always repainted — cheap, and color may change with severity)
  const int barX = CARD_X + PAD;
  const int barY = y0 + CARD_H - 14;
  const int barW = CARD_W - 2 * PAD;
  const int barH = 8;
  int fillW = (barW * constrain(pct, 0, 100)) / 100;
  _tft.fillRoundRect(barX, barY, barW, barH, 5, COLOR_BAR_TRACK);
  if (fillW > 8) {
    _tft.fillRoundRect(barX, barY, fillW, barH, 5, color);
  } else if (fillW > 0) {
    _tft.fillRect(barX + 2, barY + 2, fillW, barH - 4, color);
  }
}

void Display::updateBottomStrip(const UsageData& data, StatusKind status, int failCount) {
  bool opusChanged = data.opusPct != _prev.opusPct;
  bool statusChanged = status != _prevStatus;
  if (!opusChanged && !statusChanged) return;

  _tft.fillRect(0, STRIP_Y, SCREEN_W, SCREEN_H - STRIP_Y, COLOR_BG);

  // Left: Opus weekly limit, when the account has one.
  if (data.opusPct >= 0) {
    char buf[24];
    snprintf(buf, sizeof(buf), "Opus: %d%%", data.opusPct);
    _tft.setTextDatum(TL_DATUM);
    _tft.setTextColor(usageColor(data.opusPct, "normal"), COLOR_BG);
    _tft.drawString(buf, CARD_X + PAD, STRIP_Y + 4, 2);
  }

  // Right: connectivity/staleness status.
  const char* msg = nullptr;
  uint16_t color = COLOR_TEXT_DIM;
  char offlineBuf[40];
  switch (status) {
    case STATUS_NO_WIFI:
      msg = "no WiFi - reconnecting"; color = COLOR_RED; break;
    case STATUS_OFFLINE:
      snprintf(offlineBuf, sizeof(offlineBuf), "bridge offline (%d fails)", failCount);
      msg = offlineBuf; color = COLOR_RED; break;
    case STATUS_STALE:
      msg = "stale data (token?)"; color = COLOR_YELLOW; break;
    default:
      break;
  }
  if (msg) {
    _tft.setTextDatum(TR_DATUM);
    _tft.setTextColor(color, COLOR_BG);
    _tft.drawString(msg, SCREEN_W - PAD, STRIP_Y + 4, 2);
  }
}

void Display::drawUsage(const UsageData& data, StatusKind status, int failCount) {
  if (!_layoutDrawn) drawStaticLayout();

  // Header: last-update time (right aligned)
  if (strcmp(data.updatedAtLocal, _prevUpdatedAt) != 0) {
    char buf[24];
    snprintf(buf, sizeof(buf), "updated %s", data.updatedAtLocal);
    _tft.setTextDatum(TR_DATUM);
    _tft.setTextColor(COLOR_TEXT_DIM, COLOR_HEADER_BG);
    _tft.setTextPadding(130);
    _tft.drawString(buf, SCREEN_W - PAD, 6, 2);
    _tft.setTextPadding(0);
    strncpy(_prevUpdatedAt, data.updatedAtLocal, sizeof(_prevUpdatedAt) - 1);
  }

  updateCard(CARD1_Y, data.sessionPct, data.sessionResetsLocal, data.sessionResetsInMin,
             data.sessionSeverity, _prev.sessionPct, _prev.sessionResetsInMin);
  updateCard(CARD2_Y, data.weeklyPct, data.weeklyResetsLocal, data.weeklyResetsInMin,
             data.weeklySeverity, _prev.weeklyPct, _prev.weeklyResetsInMin);
  updateBottomStrip(data, status, failCount);

  _prev = data;
  _prevStatus = status;
}
