// Ambient light (LTR-553ALS at 0x23 on the CoreS3's internal I2C bus): "is the room dark?" Read on demand, so it costs nothing otherwise.
#pragma once
#include <stdint.h>

namespace ambient {

// Reads the sensor (the first call powers it up and waits ~250 ms for the first measurement). Returns false if the sensor does not
// answer or the I2C bus is lent to the camera at the moment. `lux` is an estimate; ch0 is visible+IR, ch1 is IR (raw counts).
bool read(float* lux, uint16_t* ch0, uint16_t* ch1);

}  // namespace ambient
