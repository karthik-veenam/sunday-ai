#include "camera.h"
#include <Arduino.h>
#include <M5Unified.h>
#include <esp_camera.h>
#include <esp_heap_caps.h>
#include <atomic>
#include <math.h>
#include <string.h>

namespace cam {

static uint16_t* g_photo = nullptr;   // native-order RGB565
static uint32_t g_count = 0;
static char g_err[64] = "";
static int16_t* g_shutter = nullptr;
static size_t g_shutterN = 0;

static std::atomic<bool> g_busBorrowed{false};
bool busBorrowed() { return g_busBorrowed.load(); }

// M5Unified drives the shared on-board I2C with its own register-level driver, which esp32-camera cannot attach to. So for the
// moment of the shot we release it, let esp32-camera bring up an ESP-IDF I2C bus on the same two pins (only the sensor setup uses it;
// the pixels arrive over the parallel DVP pins), and give the bus back to M5Unified afterwards.
static int g_sda = 12, g_scl = 11;
static int g_port = 1;

static bool g_live = false;
static uint16_t* g_liveBuf = nullptr;

static bool initCamera(int fbCount = 1) {
  camera_config_t c = {};
  c.pin_pwdn = -1;
  c.pin_reset = -1;
  c.pin_xclk = -1;                       // the GC0308 is clocked by the board's own 20 MHz oscillator (as in the stock firmware)
  c.pin_sccb_sda = g_sda;                // sensor control bus: the shared on-board I2C pins (borrowed, see above)
  c.pin_sccb_scl = g_scl;
  c.sccb_i2c_port = g_port == 0 ? 1 : 0; // a port M5Unified is not using
  c.pin_d0 = 39; c.pin_d1 = 40; c.pin_d2 = 41; c.pin_d3 = 42;
  c.pin_d4 = 15; c.pin_d5 = 16; c.pin_d6 = 48; c.pin_d7 = 47;
  c.pin_vsync = 46;
  c.pin_href = 38;
  c.pin_pclk = 45;
  c.xclk_freq_hz = 20000000;
  c.ledc_timer = LEDC_TIMER_0;
  c.ledc_channel = LEDC_CHANNEL_0;
  c.pixel_format = PIXFORMAT_RGB565;
  c.frame_size = FRAMESIZE_QVGA;         // 320x240: exactly the screen
  c.jpeg_quality = 12;
  c.fb_count = fbCount;
  c.fb_location = CAMERA_FB_IN_PSRAM;
  c.grab_mode = CAMERA_GRAB_LATEST;
  esp_err_t e = esp_camera_init(&c);
  if (e != ESP_OK) {
    snprintf(g_err, sizeof(g_err), "esp_camera_init 0x%x", (unsigned)e);
    return false;
  }
  return true;
}

bool capture() {
  g_err[0] = 0;
  if (!g_photo) {
    g_photo = (uint16_t*)heap_caps_malloc(kW * kH * 2, MALLOC_CAP_SPIRAM);
    if (!g_photo) { strlcpy(g_err, "out of memory", sizeof(g_err)); return false; }
  }
  g_sda = M5.getPin(m5::pin_name_t::in_i2c_sda);
  g_scl = M5.getPin(m5::pin_name_t::in_i2c_scl);
  g_port = (int)M5.In_I2C.getPort();
  g_busBorrowed = true;                  // the head-touch task and anything else on the bus must stay off it from here
  delay(40);
  M5.In_I2C.release();
  if (!initCamera()) {
    M5.In_I2C.begin((i2c_port_t)g_port, g_sda, g_scl);
    g_busBorrowed = false;
    return false;
  }

  bool ok = false;
  // auto-exposure / white balance need a moment after power-up: throw away the first frames, keep the last
  const int kSettleFrames = 14;
  uint32_t t0 = millis();
  for (int i = 0; i < kSettleFrames; i++) {
    camera_fb_t* fb = esp_camera_fb_get();
    if (!fb) { snprintf(g_err, sizeof(g_err), "no frame (#%d)", i); break; }
    if (i == kSettleFrames - 1) {
      if (fb->width == kW && fb->height == kH && fb->len >= (size_t)kW * kH * 2) {
        // the sensor sends RGB565 big-endian; the display path wants native order: swap each pixel
        const uint8_t* s = fb->buf;
        for (int p = 0; p < kW * kH; p++) g_photo[p] = (uint16_t)((s[2 * p] << 8) | s[2 * p + 1]);
        ok = true;
      } else {
        snprintf(g_err, sizeof(g_err), "unexpected frame %dx%d len %u", fb->width, fb->height, (unsigned)fb->len);
      }
    }
    esp_camera_fb_return(fb);
    if (millis() - t0 > 4000) { if (!ok && !g_err[0]) strlcpy(g_err, "timeout", sizeof(g_err)); break; }
  }
  esp_camera_deinit();
  M5.In_I2C.begin((i2c_port_t)g_port, g_sda, g_scl);   // hand the bus back to M5Unified
  g_busBorrowed = false;
  if (ok) g_count++;
  Serial.printf("camera: capture %s in %u ms %s\n", ok ? "ok" : "FAILED", (unsigned)(millis() - t0), g_err);
  return ok;
}

const uint16_t* photo() { return g_count ? g_photo : nullptr; }
uint32_t photoCount() { return g_count; }
const char* lastError() { return g_err; }

bool photoJpeg(uint8_t** out, size_t* len) {
  if (!g_count || !g_photo) return false;
  uint8_t* rgb = (uint8_t*)heap_caps_malloc(kW * kH * 3, MALLOC_CAP_SPIRAM);
  if (!rgb) return false;
  for (int p = 0; p < kW * kH; p++) {
    uint16_t v = g_photo[p];
    rgb[3 * p] = (uint8_t)(((v >> 11) & 31) * 255 / 31);
    rgb[3 * p + 1] = (uint8_t)(((v >> 5) & 63) * 255 / 63);
    rgb[3 * p + 2] = (uint8_t)((v & 31) * 255 / 31);
  }
  bool ok = fmt2jpg(rgb, (size_t)kW * kH * 3, kW, kH, PIXFORMAT_RGB888, 85, out, len);
  free(rgb);
  return ok;
}

bool liveBegin() {
  if (g_live) return true;
  if (!g_liveBuf) {
    g_liveBuf = (uint16_t*)heap_caps_malloc(kW * kH * 2, MALLOC_CAP_SPIRAM);
    if (!g_liveBuf) { strlcpy(g_err, "out of memory", sizeof(g_err)); return false; }
  }
  g_sda = M5.getPin(m5::pin_name_t::in_i2c_sda);
  g_scl = M5.getPin(m5::pin_name_t::in_i2c_scl);
  g_port = (int)M5.In_I2C.getPort();
  g_busBorrowed = true;
  delay(40);
  M5.In_I2C.release();
  if (!initCamera(2)) {
    M5.In_I2C.begin((i2c_port_t)g_port, g_sda, g_scl);
    g_busBorrowed = false;
    return false;
  }
  g_live = true;
  for (int i = 0; i < 10; i++) { camera_fb_t* fb = esp_camera_fb_get(); if (fb) esp_camera_fb_return(fb); }   // let auto-exposure settle
  Serial.println("camera: live view on");
  return true;
}

const uint16_t* liveGrab() {
  if (!g_live) return nullptr;
  camera_fb_t* fb = esp_camera_fb_get();
  if (!fb) return nullptr;
  const uint16_t* out = nullptr;
  if (fb->width == kW && fb->height == kH && fb->len >= (size_t)kW * kH * 2) {
    const uint8_t* s = fb->buf;
    for (int p = 0; p < kW * kH; p++) g_liveBuf[p] = (uint16_t)((s[2 * p] << 8) | s[2 * p + 1]);
    out = g_liveBuf;
  }
  esp_camera_fb_return(fb);
  return out;
}

void liveEnd() {
  if (!g_live) return;
  esp_camera_deinit();
  M5.In_I2C.begin((i2c_port_t)g_port, g_sda, g_scl);
  g_busBorrowed = false;
  g_live = false;
  Serial.println("camera: live view off");
}

Clip shutterSound() {
  if (!g_shutter) {
    const uint32_t rate = 22050;
    g_shutterN = rate * 220 / 1000;
    g_shutter = (int16_t*)heap_caps_malloc(g_shutterN * 2, MALLOC_CAP_SPIRAM);
    if (!g_shutter) return {nullptr, 0};
    memset(g_shutter, 0, g_shutterN * 2);
    uint32_t rng = 0x1234567u;
    // two mechanical clicks: a bright one (shutter opening) then a lower, longer one (closing)
    const struct { float t, dur, tone, amp; } clicks[2] = {{0.0f, 0.016f, 2600.f, 0.55f}, {0.085f, 0.028f, 1500.f, 0.45f}};
    for (const auto& k : clicks) {
      size_t i0 = (size_t)(k.t * rate), n = (size_t)(k.dur * rate);
      for (size_t i = 0; i < n && i0 + i < g_shutterN; i++) {
        rng ^= rng << 13; rng ^= rng >> 17; rng ^= rng << 5;
        float noise = ((rng & 0xFFFF) / 32768.f) - 1.f;
        float env = expf(-6.f * i / (float)n);
        float tone = sinf(6.2831853f * k.tone * i / rate);
        float v = (0.65f * noise + 0.35f * tone) * env * k.amp;
        g_shutter[i0 + i] = (int16_t)(v * 30000.f);
      }
    }
  }
  return {g_shutter, g_shutterN};
}

}  // namespace cam
