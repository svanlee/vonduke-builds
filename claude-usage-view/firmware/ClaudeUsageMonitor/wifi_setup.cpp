#include "wifi_setup.h"

static const char* NVS_NAMESPACE = "claudeusage";

void WifiSetup::begin(AppConfig& config) {
  loadConfig(config);

#ifdef DEV_FORCE_BRIDGE_URL
  strncpy(config.bridgeUrl, DEV_FORCE_BRIDGE_URL, MAX_URL_LEN);
  DBGLN("DEV: bridge URL override active");
#endif

#if defined(DEV_FORCE_WIFI_SSID) && defined(DEV_FORCE_WIFI_PASS)
  // DEV mode: connect directly to hardcoded WiFi, skip WiFiManager
  DBGLN("DEV: connecting to hardcoded WiFi...");
  WiFi.mode(WIFI_STA);
  WiFi.begin(DEV_FORCE_WIFI_SSID, DEV_FORCE_WIFI_PASS);
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 30000) {
    delay(500);
    DBG(".");
  }
  DBGLN("");
  bool connected = (WiFi.status() == WL_CONNECTED);
#else
  // 1) Try restoring from our own credential backup before WiFiManager.
  bool connected = false;
  String bkSsid, bkPass;
  if (loadWifiBackup(bkSsid, bkPass)) {
    DBGF("Restoring WiFi from backup (ssid: %s)...\n", bkSsid.c_str());
    WiFi.mode(WIFI_STA);
    WiFi.begin(bkSsid.c_str(), bkPass.c_str());
    unsigned long start = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - start < 20000) {
      delay(500);
      DBG(".");
    }
    DBGLN("");
    connected = (WiFi.status() == WL_CONNECTED);
    if (!connected) DBGLN("Backup restore failed, falling back to WiFiManager");
  }

  // 2) WiFiManager (opens the captive portal if needed).
  if (!connected) {
    WiFiManagerParameter paramBridgeUrl(
        "bridgeurl", "Bridge URL (http://axon-rig-ip:8787/usage)",
        config.bridgeUrl, MAX_URL_LEN);

    _wm.addParameter(&paramBridgeUrl);
    _wm.setConfigPortalTimeout(0);
    _wm.setConnectTimeout(15);

    DBGLN("Starting WiFiManager autoConnect...");
    connected = _wm.autoConnect(WIFI_AP_NAME);

    if (connected) {
#ifndef DEV_FORCE_BRIDGE_URL
      strncpy(config.bridgeUrl, paramBridgeUrl.getValue(), MAX_URL_LEN);
#endif
      saveConfig(config);
    }
  }

  if (connected) {
    saveWifiBackup();
  }
#endif

  if (connected) {
    DBGLN("WiFi connected!");
    DBGF("IP: %s\n", WiFi.localIP().toString().c_str());
  } else {
    DBGLN("WiFi connection failed!");
  }
}

bool WifiSetup::isConnected() {
  return WiFi.status() == WL_CONNECTED;
}

int WifiSetup::checkConnection(unsigned long now, unsigned long& wifiLostTime) {
  if (!isConnected()) {
    if (wifiLostTime == 0) {
      wifiLostTime = now;
      _reconnectAttempts = 0;
      DBGLN("WiFi lost! Will keep trying to reconnect...");
    }

    _reconnectAttempts++;

    if (_reconnectAttempts % 6 == 0) {
      DBGF("WiFi reconnect: full cycle (attempt %d)\n", _reconnectAttempts);
      WiFi.disconnect(true);
      delay(1000);
      WiFi.mode(WIFI_STA);
      WiFi.begin();
    } else {
      DBGF("WiFi reconnect attempt %d...\n", _reconnectAttempts);
      WiFi.reconnect();
    }
    return 1;
  }

  if (wifiLostTime > 0) {
    DBGF("WiFi reconnected after %d attempts!\n", _reconnectAttempts);
    wifiLostTime = 0;
    _reconnectAttempts = 0;
    saveWifiBackup();
  }
  return 0;
}

void WifiSetup::resetSettings() {
  _wm.resetSettings();
  _prefs.begin(NVS_NAMESPACE, false);
  _prefs.clear();
  _prefs.end();
}

void WifiSetup::loadConfig(AppConfig& config) {
  _prefs.begin(NVS_NAMESPACE, true);
  String url = _prefs.getString("bridgeurl", DEFAULT_BRIDGE_URL);
  strncpy(config.bridgeUrl, url.c_str(), MAX_URL_LEN);
  _prefs.end();
  DBGF("Loaded config - bridge URL: %s\n", config.bridgeUrl);
}

void WifiSetup::saveConfig(const AppConfig& config) {
  _prefs.begin(NVS_NAMESPACE, false);
  _prefs.putString("bridgeurl", config.bridgeUrl);
  _prefs.end();
  DBGLN("Config saved to NVS");
}

void WifiSetup::saveWifiBackup() {
  String ssid = WiFi.SSID();
  String pass = WiFi.psk();
  if (ssid.length() == 0) return;

  _prefs.begin(NVS_NAMESPACE, false);
  // Only write when changed — avoids flash wear on every reconnect.
  if (_prefs.getString("wifi_ssid", "") != ssid ||
      _prefs.getString("wifi_pass", "") != pass) {
    _prefs.putString("wifi_ssid", ssid);
    _prefs.putString("wifi_pass", pass);
    DBGF("WiFi backup saved (ssid: %s)\n", ssid.c_str());
  }
  _prefs.end();
}

bool WifiSetup::loadWifiBackup(String& ssid, String& pass) {
  _prefs.begin(NVS_NAMESPACE, true);
  ssid = _prefs.getString("wifi_ssid", "");
  pass = _prefs.getString("wifi_pass", "");
  _prefs.end();
  return ssid.length() > 0;
}
