// ============================================================
// Claude Usage Monitor
// Board: ESP32-4827S040 (Sunton CYD 4.0")
// Display: 4.0" TFT ST7796S 480x320 (landscape)
//
// Shows the Claude subscription usage (5h session window and
// weekly limits) fetched from a small bridge server running on
// the Mac (bridge/claude_usage_bridge.py), refreshing every 60s.
// ============================================================

#include "config.h"
#include "data_types.h"
#include "display.h"
#include "wifi_setup.h"
#include "api_client.h"

Display display;
WifiSetup wifi;
ApiClient api;
AppData app;

unsigned long lastRetryTime = 0;
unsigned long bootButtonPressStart = 0;
bool bootButtonPressed = false;

StatusKind currentStatus() {
  if (!wifi.isConnected()) return STATUS_NO_WIFI;
  if (app.fetchFailCount >= OFFLINE_AFTER_FAILS) return STATUS_OFFLINE;
  if (app.usage.stale) return STATUS_STALE;
  return STATUS_OK;
}

bool doFetchData() {
  DBGLN("Fetching usage from bridge...");
  UsageData fresh;
  if (api.fetchUsage(app.config.bridgeUrl, fresh)) {
    app.usage = fresh;
    app.hasData = true;
    app.fetchFailCount = 0;
    app.lastDataFetch = millis();
    app.screenDirty = true;
    DBGF("Usage OK - session=%d%% weekly=%d%%\n", fresh.sessionPct, fresh.weeklyPct);
    return true;
  }
  app.fetchFailCount++;
  app.lastDataFetch = millis();  // keep the 60s cadence even on failure
  app.screenDirty = true;        // status banner may need updating
  DBGF("Fetch FAILED (%d consecutive): %s\n", app.fetchFailCount, api.getLastError());
  return false;
}

void setup() {
  Serial.begin(115200);
  delay(500);

  DBGLN("\n=============================");
  DBGLN("Claude Usage Monitor " FIRMWARE_VERSION);
  DBGLN("=============================");

  // Touch (XPT2046) shares the display SPI bus on the 4.0" board — keep its
  // CS deasserted so it never drives MISO. Touch itself is unused here.
  pinMode(TOUCH_CS_PIN, OUTPUT);
  digitalWrite(TOUCH_CS_PIN, HIGH);

  pinMode(BOOT_BUTTON_PIN, INPUT_PULLUP);

  display.begin();
  display.drawSplashScreen();
  delay(1500);

  memset(&app, 0, sizeof(AppData));
  app.state = STATE_WIFI_SETUP;

  display.drawWiFiSetupScreen();
  wifi.begin(app.config);  // blocks until connected or portal configured

  if (wifi.isConnected()) {
    DBGF("Bridge URL: %s\n", app.config.bridgeUrl);
    display.drawConnectingScreen("Fetching usage...");
    app.state = STATE_FETCHING;
  } else {
    snprintf(app.lastError, MAX_ERROR_LEN, "WiFi not connected");
    app.state = STATE_ERROR;
    display.drawErrorScreen(app.lastError);
    lastRetryTime = millis();
  }

  DBGF("Free heap after setup: %d bytes\n", ESP.getFreeHeap());
}

void loop() {
  unsigned long now = millis();

  // BOOT button held 3s -> reset WiFi/bridge config
  if (digitalRead(BOOT_BUTTON_PIN) == LOW) {
    if (!bootButtonPressed) {
      bootButtonPressed = true;
      bootButtonPressStart = now;
    } else if (now - bootButtonPressStart >= BOOT_LONG_PRESS_MS) {
      DBGLN("BOOT button held 3s - resetting config...");
      display.drawConnectingScreen("Resetting config...");
      delay(1000);
      wifi.resetSettings();
      ESP.restart();
    }
  } else {
    bootButtonPressed = false;
  }

  // ==========================================
  // STATE: FETCHING — first fetch after boot
  // ==========================================
  if (app.state == STATE_FETCHING) {
    if (doFetchData()) {
      app.state = STATE_DISPLAYING;
      display.invalidateLayout();
    } else {
      snprintf(app.lastError, MAX_ERROR_LEN, "%s", api.getLastError());
      app.state = STATE_ERROR;
      lastRetryTime = now;
      display.drawErrorScreen(app.lastError, app.config.bridgeUrl);
    }
    return;
  }

  // ==========================================
  // STATE: ERROR — no data yet; retry every 30s
  // ==========================================
  if (app.state == STATE_ERROR) {
    if (now - lastRetryTime > 30000) {
      lastRetryTime = now;
      if (!wifi.isConnected()) {
        wifi.checkConnection(now, app.wifiLostTime);
      } else {
        display.drawConnectingScreen("Fetching usage...");
        app.state = STATE_FETCHING;
      }
    }
    delay(50);
    return;
  }

  // ==========================================
  // STATE: DISPLAYING — normal operation
  // ==========================================
  if (app.state != STATE_DISPLAYING) return;

  // WiFi health check — keep showing last data, banner reports the drop
  if (now - app.lastWifiCheck >= WIFI_CHECK_INTERVAL) {
    app.lastWifiCheck = now;
    int prevWifi = (app.wifiLostTime != 0);
    int wifiStatus = wifi.checkConnection(now, app.wifiLostTime);
    if ((wifiStatus != 0) != prevWifi) app.screenDirty = true;
  }

  // Periodic usage refresh (failures keep last data; banner shows offline)
  if (wifi.isConnected() && now - app.lastDataFetch >= DATA_POLL_INTERVAL) {
    doFetchData();
  }

  if (app.screenDirty && app.hasData) {
    display.drawUsage(app.usage, currentStatus(), app.fetchFailCount);
    app.screenDirty = false;
  }

  delay(20);
}
