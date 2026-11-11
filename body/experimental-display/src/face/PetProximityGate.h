#pragma once
#include <stdint.h>
namespace sunday {
// Raw optical counts are not centimetres. Freshness and a short grace cover a moving hand.
class PetProximityGate {
 public:
  void observe(uint32_t now, bool valid, uint16_t counts, uint16_t threshold) {
    valid_=valid; sampleAt_=now;
    if (valid && counts>=threshold) { nearAt_=now; seenNear_=true; }
  }
  bool permits(uint32_t now) const {
    return valid_ && seenNear_ && (uint32_t)(now-sampleAt_)<=250 && (uint32_t)(now-nearAt_)<=600;
  }
 private:
  bool valid_=false,seenNear_=false;
  uint32_t sampleAt_=0,nearAt_=0;
};
}
