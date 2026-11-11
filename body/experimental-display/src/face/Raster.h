// Tiny anti-aliased software rasteriser writing RGB565. Used by BOTH the firmware and the desktop
// preview, so the preview is pixel-identical to what the device draws into its framebuffer.
#pragma once
#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include "FaceGeometry.h"
#if defined(ARDUINO)
#include <esp_heap_caps.h>
#endif

namespace sunday {

inline void* fbAlloc(size_t n) {
#if defined(ARDUINO)
  void* p = heap_caps_malloc(n, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);  // large buffers live in PSRAM
  if (p) return p;
#endif
  return malloc(n);
}

inline uint16_t pack565(float r, float g, float b) {
  int R = (int)(r * (31.f / 255.f) + 0.5f), G = (int)(g * (63.f / 255.f) + 0.5f), B = (int)(b * (31.f / 255.f) + 0.5f);
  R = R < 0 ? 0 : (R > 31 ? 31 : R); G = G < 0 ? 0 : (G > 63 ? 63 : G); B = B < 0 ? 0 : (B > 31 ? 31 : B);
  return (uint16_t)((R << 11) | (G << 5) | B);
}
// Ordered-dither variant (4x4 Bayer) used when baking smooth gradients to hide 565 banding.
inline uint16_t pack565d(float r, float g, float b, int x, int y) {
  static const uint8_t bayer[16] = {0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5};
  float t = (bayer[(y & 3) * 4 + (x & 3)] + 0.5f) / 16.f;
  int R = (int)(r * (31.f / 255.f) + t), G = (int)(g * (63.f / 255.f) + t), B = (int)(b * (31.f / 255.f) + t);
  R = R < 0 ? 0 : (R > 31 ? 31 : R); G = G < 0 ? 0 : (G > 63 ? 63 : G); B = B < 0 ? 0 : (B > 31 ? 31 : B);
  return (uint16_t)((R << 11) | (G << 5) | B);
}
inline void unpack565(uint16_t p, float& r, float& g, float& b) {
  r = ((p >> 11) & 31) * (255.f / 31.f);
  g = ((p >> 5) & 63) * (255.f / 63.f);
  b = (p & 31) * (255.f / 31.f);
}

class Raster {
 public:
  int w = 0, h = 0;
  uint16_t* fb = nullptr;  // RGB565, native byte order (what M5GFX pushImage expects)

  bool init(int w_, int h_) {
    w = w_; h = h_;
    fb = (uint16_t*)fbAlloc((size_t)w * h * 2);
    cov = (uint8_t*)fbAlloc((size_t)w * h);
    if (!fb || !cov) return false;
    memset(fb, 0, (size_t)w * h * 2);
    memset(cov, 0, (size_t)w * h);
    return true;
  }
  void blit(const uint16_t* src) { memcpy(fb, src, (size_t)w * h * 2); }

  void blendPx(int x, int y, Rgb c, float a) {
    if ((unsigned)x >= (unsigned)w || (unsigned)y >= (unsigned)h || a <= 0.003f) return;
    if (a > 1.f) a = 1.f;
    uint16_t* p = &fb[y * w + x];
    float r, g, b;
    unpack565(*p, r, g, b);
    *p = pack565(r + (c.r - r) * a, g + (c.g - g) * a, b + (c.b - b) * a);
  }

  // Anti-aliased filled ellipse (axis aligned), optional constant alpha.
  void ellipse(float cx, float cy, float rx, float ry, Rgb c, float alpha = 1.f) {
    if (rx < 0.3f || ry < 0.3f) return;
    float m = rx < ry ? rx : ry;
    int x0 = (int)floorf(cx - rx - 1), x1 = (int)ceilf(cx + rx + 1), y0 = (int)floorf(cy - ry - 1), y1 = (int)ceilf(cy + ry + 1);
    for (int y = y0; y <= y1; y++)
      for (int x = x0; x <= x1; x++) {
        float dx = (x + 0.5f - cx) / rx, dy = (y + 0.5f - cy) / ry;
        float a = 0.5f - (sqrtf(dx * dx + dy * dy) - 1.f) * m;
        if (a > 0.f) blendPx(x, y, c, (a > 1.f ? 1.f : a) * alpha);
      }
  }
  // Soft-edged blob (blush): alpha falls smoothly from the centre to the rim.
  void soften(float cx, float cy, float rx, float ry, Rgb c, float alpha) {
    int x0 = (int)floorf(cx - rx), x1 = (int)ceilf(cx + rx), y0 = (int)floorf(cy - ry), y1 = (int)ceilf(cy + ry);
    for (int y = y0; y <= y1; y++)
      for (int x = x0; x <= x1; x++) {
        float dx = (x + 0.5f - cx) / rx, dy = (y + 0.5f - cy) / ry, d = sqrtf(dx * dx + dy * dy);
        if (d < 1.f) blendPx(x, y, c, alpha * (1.f - smoothstep(0.f, 1.f, d)));
      }
  }
  // Same blob, but confined to a circle (the head): alpha fades to zero over `feather` px before the rim, so blush
  // can never spill outside the sphere when the head is cropped by the screen.
  void softenClipped(float cx, float cy, float rx, float ry, Rgb c, float alpha, float hx, float hy, float hr, float feather) {
    int x0 = (int)floorf(cx - rx), x1 = (int)ceilf(cx + rx), y0 = (int)floorf(cy - ry), y1 = (int)ceilf(cy + ry);
    for (int y = y0; y <= y1; y++)
      for (int x = x0; x <= x1; x++) {
        float dx = (x + 0.5f - cx) / rx, dy = (y + 0.5f - cy) / ry, d = sqrtf(dx * dx + dy * dy);
        if (d >= 1.f) continue;
        float px = x + 0.5f - hx, py = y + 0.5f - hy;
        float inside = smoothstep(0.f, feather, hr - sqrtf(px * px + py * py));
        if (inside > 0.f) blendPx(x, y, c, alpha * (1.f - smoothstep(0.f, 1.f, d)) * inside);
      }
  }

  // Anti-aliased heart, `size` = half-width in px. Implicit curve (x^2+y^2-1)^3 - x^2 y^3 <= 0, 2x2 supersampled.
  void heart(float cx, float cy, float size, Rgb c, float alpha) {
    if (size < 2.f || alpha <= 0.01f) return;
    float k = size / 1.15f;
    int x0 = (int)floorf(cx - size - 1), x1 = (int)ceilf(cx + size + 1), y0 = (int)floorf(cy - size - 1), y1 = (int)ceilf(cy + size * 1.15f + 1);
    for (int y = y0; y <= y1; y++)
      for (int x = x0; x <= x1; x++) {
        int in = 0;
        for (int sy = 0; sy < 2; sy++)
          for (int sx = 0; sx < 2; sx++) {
            float px = (x + 0.25f + 0.5f * sx - cx) / k, py = -(y + 0.25f + 0.5f * sy - cy) / k + 0.12f;
            float a = px * px + py * py - 1.f;
            if (a * a * a - px * px * py * py * py <= 0.f) in++;
          }
        if (in) blendPx(x, y, c, alpha * (in / 4.f));
      }
  }

  // ---- shapes: union of discs / vertical spans, composited once => clean AA edges, no double blending
  void beginShape() { bx0 = w; by0 = h; bx1 = -1; by1 = -1; }
  void disc(float cx, float cy, float r) {
    if (r < 0.4f) r = 0.4f;
    int x0 = (int)floorf(cx - r - 1), x1 = (int)ceilf(cx + r + 1), y0 = (int)floorf(cy - r - 1), y1 = (int)ceilf(cy + r + 1);
    for (int y = y0; y <= y1; y++)
      for (int x = x0; x <= x1; x++) {
        float dx = x + 0.5f - cx, dy = y + 0.5f - cy;
        float a = r - sqrtf(dx * dx + dy * dy) + 0.5f;
        if (a > 0.f) mark(x, y, a > 1.f ? 1.f : a);
      }
  }
  void vspan(int x, float yTop, float yBot) {  // fractional ends give vertical anti-aliasing
    if (yBot <= yTop) return;
    for (int y = (int)floorf(yTop); y < (int)ceilf(yBot); y++) {
      float a = fminf((float)y + 1.f, yBot) - fmaxf((float)y, yTop);
      if (a > 0.f) mark(x, y, a);
    }
  }
  void endShape(Rgb c, float alpha = 1.f) {
    for (int y = by0; y <= by1; y++)
      for (int x = bx0; x <= bx1; x++) {
        uint8_t& v = cov[y * w + x];
        if (v) { blendPx(x, y, c, (v / 255.f) * alpha); v = 0; }
      }
  }

 private:
  void mark(int x, int y, float a) {
    if ((unsigned)x >= (unsigned)w || (unsigned)y >= (unsigned)h) return;
    uint8_t v = (uint8_t)(a * 255.f + 0.5f);
    uint8_t& c = cov[y * w + x];
    if (v > c) c = v;
    if (x < bx0) bx0 = x;
    if (x > bx1) bx1 = x;
    if (y < by0) by0 = y;
    if (y > by1) by1 = y;
  }
  uint8_t* cov = nullptr;
  int bx0 = 0, by0 = 0, bx1 = -1, by1 = -1;
};

}  // namespace sunday
