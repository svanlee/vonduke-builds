#ifndef WIFI_SETUP_H
#define WIFI_SETUP_H

// Fix for ESP32 Arduino Core v3.x: WebServer.h references 'FS' without namespace
#include <FS.h>
using fs::FS;
#include <WiFiManager.h>
#include <Preferences.h>
#include "config.h"
#include "data_types.h"

class WifiSetup {
public:
  void begin(AppConfig& config);
  bool isConnected();
  int  checkConnection(unsigned long now, unsigned long& wifiLostTime);  // 0=ok, 1=no wifi
  int  getReconnectAttempts() { return _reconnectAttempts; }
  void resetSettings();

private:
  WiFiManager _wm;
  Preferences _prefs;
  int _reconnectAttempts = 0;

  void loadConfig(AppConfig& config);
  void saveConfig(const AppConfig& config);

  // WiFi credential backup in our own NVS namespace — defends against
  // brownout corruption of the esp_wifi/WiFiManager internal NVS.
  void saveWifiBackup();
  bool loadWifiBackup(String& ssid, String& pass);
};

#endif // WIFI_SETUP_H
