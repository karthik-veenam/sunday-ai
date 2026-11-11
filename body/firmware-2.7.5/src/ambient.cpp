#include "ambient.h"
#include "camera.h"
#include <Arduino.h>
#include <M5Unified.h>

namespace ambient {

static constexpr uint8_t kAddr = 0x23;
static constexpr uint32_t kFreq = 400000;
static constexpr float kGain = 48.f;           // ALS gain 48x: 0.02 .. 1300 lux, fine resolution for a dark room
static bool g_on = false;
static uint32_t g_onAt = 0;

bool read(float* lux, uint16_t* ch0, uint16_t* ch1) {
  if (cam::busBorrowed()) return false;         // the camera has the I2C bus for the moment of a photo
  if (!g_on) {
    // ALS_MEAS_RATE: integration 100 ms, repeat 100 ms; ALS_CONTR: active, gain 48x
    if (!M5.In_I2C.writeRegister8(kAddr, 0x85, 0x03, kFreq)) return false;
    if (!M5.In_I2C.writeRegister8(kAddr, 0x80, 0x19, kFreq)) return false;
    g_on = true;
    g_onAt = millis();
  }
  uint32_t since = millis() - g_onAt;
  if (since < 250) delay(250 - since);
  uint8_t b[4];
  if (!M5.In_I2C.readRegister(kAddr, 0x88, b, 4, kFreq)) { g_on = false; return false; }   // CH1 low/high, CH0 low/high
  uint16_t c1 = (uint16_t)(b[0] | (b[1] << 8)), c0 = (uint16_t)(b[2] | (b[3] << 8));
  if (ch0) *ch0 = c0;
  if (ch1) *ch1 = c1;
  float l = 0.f;
  if (c0 + c1 > 0) {                            // datasheet lux formula, integration time 100 ms (factor 1)
    float ratio = (float)c1 / (float)(c0 + c1);
    if (ratio < 0.45f) l = (1.7743f * c0 + 1.1059f * c1) / kGain;
    else if (ratio < 0.64f) l = (4.2785f * c0 - 1.9548f * c1) / kGain;
    else if (ratio < 0.85f) l = (0.5926f * c0 + 0.1185f * c1) / kGain;
  }
  if (lux) *lux = l < 0.f ? 0.f : l;
  return true;
}

}  // namespace ambient
