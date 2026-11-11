// GC0308 camera (K151 / CoreS3): on-demand still capture. The sensor is only powered up/streaming for the moment of the shot
// (esp32-camera is initialised, a few frames are discarded while auto-exposure settles, one frame is kept, then it is deinitialised)
// so it never costs CPU/PSRAM bandwidth while the face is animating.
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace cam {

constexpr int kW = 320, kH = 240;

// True while the on-board I2C bus is lent to the camera driver: other tasks must not touch it (touch polling etc.).
bool busBorrowed();

// Takes a photo. Blocking (~1-1.5 s). On success the frame is in photo() as native-order RGB565 (same format as the face framebuffer).
bool capture();
const uint16_t* photo();             // kW*kH pixels, valid after a successful capture() (PSRAM, kept until the next capture)
uint32_t photoCount();               // successful captures since boot
const char* lastError();             // "" if the last capture worked
// JPEG of the last photo for the debug endpoint. *out must be free()d by the caller. false if there is no photo.
bool photoJpeg(uint8_t** out, size_t* len);

// Live view: the camera stays on and frames are shown continuously (setup/teaching tool). The shared I2C bus stays lent to the camera
// driver for the whole time (busBorrowed() is true), so touch, LEDs and codec setup are off until liveEnd().
bool liveBegin();                           // power up, let exposure settle; false on failure (bus is handed back)
const uint16_t* liveGrab();                 // next frame as native-order RGB565 (kW*kH), or nullptr if none is ready; valid until the next call
void liveEnd();                             // camera off, bus handed back

// Short synthesized camera-shutter click (22.05 kHz mono); persistent buffer, allocated on first call.
struct Clip { const int16_t* pcm; size_t samples; };
Clip shutterSound();

}  // namespace cam
