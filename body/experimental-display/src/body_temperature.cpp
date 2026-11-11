#include "body_temperature.h"
#include "camera.h"
#include <M5Unified.h>
#include <cmath>
namespace bodytemperature {
bool read(float* celsius) {
  if (!celsius || cam::busBorrowed() || !M5.Imu.isEnabled()) return false;
  float value = 0;
  if (!M5.Imu.getTemp(&value) || !std::isfinite(value) || value == -41.0f) return false;
  // BMI270 ADC 0x8000 means not ready; M5Unified converts that sentinel to -41 C.
  *celsius = value;
  return true;
}
}
