#pragma once
#include <stdint.h>
namespace sunday {
struct NetworkActions { bool startRecoveryAp=false, stopRecoveryAp=false, retryStation=false; };
class NetworkRecovery {
 public:
  void begin(uint32_t now) { downSince_=retryAt_=now; wasConnected_=false; }
  NetworkActions tick(uint32_t now, bool credentials, bool connected, bool ap) {
    NetworkActions a;
    if (!credentials) return a; // Unconfigured devices retain their setup portal.
    if (connected) {
      wasConnected_=true; downSince_=retryAt_=now;
      a.stopRecoveryAp=ap; return a;
    }
    if (wasConnected_) { downSince_=retryAt_=now; wasConnected_=false; }
    a.startRecoveryAp=!ap && uint32_t(now-downSince_)>=20000;
    if (uint32_t(now-retryAt_)>=30000) { retryAt_=now; a.retryStation=true; }
    return a;
  }
 private:
  uint32_t downSince_=0, retryAt_=0;
  bool wasConnected_=false;
};
}
