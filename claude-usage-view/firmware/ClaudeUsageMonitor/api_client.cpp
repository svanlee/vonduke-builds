#include "api_client.h"

#include <HTTPClient.h>
#include <ArduinoJson.h>

static void copyStr(char* dst, size_t dstLen, const char* src) {
  if (!src) src = "";
  strncpy(dst, src, dstLen - 1);
  dst[dstLen - 1] = '\0';
}

bool ApiClient::fetchUsage(const char* url, UsageData& out) {
  HTTPClient http;
  http.setTimeout(HTTP_TIMEOUT_MS);
  http.setConnectTimeout(HTTP_TIMEOUT_MS);

  if (!http.begin(url)) {
    copyStr(_lastError, MAX_ERROR_LEN, "invalid URL");
    return false;
  }

  int code = http.GET();
  if (code != 200) {
    snprintf(_lastError, MAX_ERROR_LEN, "bridge HTTP %d", code);
    http.end();
    return false;
  }

  String body = http.getString();
  http.end();

  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, body);
  if (err) {
    snprintf(_lastError, MAX_ERROR_LEN, "JSON: %s", err.c_str());
    return false;
  }
  if (!doc["session_pct"].is<int>()) {
    copyStr(_lastError, MAX_ERROR_LEN, "response missing session_pct");
    return false;
  }

  out.sessionPct        = doc["session_pct"] | 0;
  out.sessionResetsInMin = doc["session_resets_in_min"] | 0;
  out.weeklyPct         = doc["weekly_pct"] | 0;
  out.weeklyResetsInMin = doc["weekly_resets_in_min"] | 0;
  out.opusPct           = doc["opus_pct"] | -1;  // null -> -1
  out.stale             = doc["stale"] | false;

  copyStr(out.sessionSeverity, sizeof(out.sessionSeverity), doc["session_severity"] | "normal");
  copyStr(out.weeklySeverity, sizeof(out.weeklySeverity), doc["weekly_severity"] | "normal");
  copyStr(out.sessionResetsLocal, sizeof(out.sessionResetsLocal), doc["session_resets_local"] | "--:--");
  copyStr(out.weeklyResetsLocal, sizeof(out.weeklyResetsLocal), doc["weekly_resets_local"] | "--:--");
  copyStr(out.updatedAtLocal, sizeof(out.updatedAtLocal), doc["updated_at_local"] | "--:--");

  return true;
}
