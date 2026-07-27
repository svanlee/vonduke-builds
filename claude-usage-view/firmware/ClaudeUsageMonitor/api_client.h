#ifndef API_CLIENT_H
#define API_CLIENT_H

#include "config.h"
#include "data_types.h"

class ApiClient {
public:
  // GET the bridge URL and fill `out`. Returns false on any HTTP/parse error.
  bool fetchUsage(const char* url, UsageData& out);
  const char* getLastError() { return _lastError; }

private:
  char _lastError[MAX_ERROR_LEN] = {0};
};

#endif // API_CLIENT_H
