#include "proximity.h"
#include "camera.h"
#include <Arduino.h>
#include <M5Unified.h>
namespace proximity {
static bool initialized=false;
static uint32_t readyAt=0;
bool read(uint16_t* counts) {
  if (!counts || cam::busBorrowed()) return false;
  constexpr uint8_t addr=0x23; constexpr uint32_t freq=400000;
  if (!initialized) {
    // LTR553 PS only: 40kHz / 100% duty / 100mA, one pulse, 50ms repeat, PS active.
    // Leave ALS registers alone: room-light readings retain their existing configuration.
    if (!M5.In_I2C.writeRegister8(addr,0x82,0x3c,freq) ||
        !M5.In_I2C.writeRegister8(addr,0x83,0x01,freq) ||
        !M5.In_I2C.writeRegister8(addr,0x84,0x00,freq) ||
        !M5.In_I2C.writeRegister8(addr,0x81,0x02,freq)) return false;
    initialized=true;readyAt=millis()+100;return false;
  }
  if ((int32_t)(millis()-readyAt)<0) return false;
  uint8_t b[2];
  if (!M5.In_I2C.readRegister(addr,0x8d,b,2,freq)) {initialized=false;return false;}
  if (b[1]&0x80) return false;  // saturated/invalid proximity result
  *counts=(uint16_t)b[0]|((uint16_t)(b[1]&0x07)<<8);
  return true;
}
}
