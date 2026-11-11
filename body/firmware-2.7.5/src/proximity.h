#pragma once
#include <stdint.h>
namespace proximity {
// Nonblocking 50ms sampling. False means unavailable/warming up; counts are 0..2047.
bool read(uint16_t* counts);
}
