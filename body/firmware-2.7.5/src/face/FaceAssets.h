// Assets baked once at boot (not per frame):
//   * base layer: background + analytically shaded, anti-aliased, dithered sphere
//   * ring lookup table: for every pixel the ring can touch -> position, angle index, intensity
// Per frame the renderer only blits `base` and recolours the ring pixels from a 256-entry palette.
#pragma once
#include <math.h>
#include <stdint.h>
#include <atomic>
#include "FaceGeometry.h"
#include "FaceLayout.h"
#include "Raster.h"

namespace sunday {

struct FaceAssets {
  int w = 0, h = 0;
  FaceLayout layout{};            // head/feature transforms + feature anchors for this display mode
  std::atomic<bool> ready{false}; // set once everything below is baked (layouts can be baked on a background task)
  uint16_t* base = nullptr;
  uint16_t *ringX = nullptr, *ringY = nullptr;
  uint8_t *ringAng = nullptr, *ringProf = nullptr;  // prof: 128 == intensity 1.0, 255 ~ 2.0 (bloom)
  int ringN = 0;

  bool build(int w_, int h_, DisplayMode mode = DisplayMode::Classic) {
    w = w_; h = h_;
    layout = FaceLayout::make(mode, w, h);
    base = (uint16_t*)fbAlloc((size_t)w * h * 2);
    if (!base) return false;
    bakeBase();
    bool ok = bakeRing();
    ready.store(ok, std::memory_order_release);
    return ok;
  }

 private:
  static float angDiff(float a, float b) {
    float d = fmodf(a - b + 540.f, 360.f) - 180.f;
    return d;
  }

  void bakeBase() {
    const Transform& t = layout.head;
    const float cx = t.x(ref::kCx), cy = t.y(ref::kCy), R = t.len(ref::kHeadR);
    // light from upper-left, towards the viewer
    const float lx = -0.45f, ly = -0.60f, lz = 0.66f;
    const float ln = 1.f / sqrtf(lx * lx + ly * ly + lz * lz);
    const Rgb shadow{150, 168, 234}, mid{208, 219, 250}, lit{251, 253, 255}, bounce{168, 182, 243};
    for (int y = 0; y < h; y++)
      for (int x = 0; x < w; x++) {
        float dx = x + 0.5f - cx, dy = y + 0.5f - cy, d = sqrtf(dx * dx + dy * dy);
        float cov = clampf(R - d + 0.5f, 0.f, 1.f);
        Rgb c = palette::kBackground;
        if (cov > 0.f) {
          float nx = dx / R, ny = dy / R, r2 = nx * nx + ny * ny;
          if (r2 > 1.f) { float k = 1.f / sqrtf(r2); nx *= k; ny *= k; r2 = 1.f; }
          float nz = sqrtf(1.f - r2);
          float diff = clampf((nx * lx + ny * ly + nz * lz) * ln, 0.f, 1.f);
          Rgb s = diff < 0.5f ? mix(shadow, mid, smoothstep(0.f, 0.5f, diff)) : mix(mid, lit, smoothstep(0.45f, 0.95f, diff));
          float rim = powf(1.f - nz, 3.f);                 // cool lavender bounce light near the edge
          s = mix(s, bounce, clampf(rim * 0.55f * (1.f - diff * 0.6f), 0.f, 0.7f));
          c = mix(palette::kBackground, s, cov);
        }
        base[y * w + x] = pack565d(c.r, c.g, c.b, x, y);
      }
  }

  // intensity of the ring at reference radius r / angle a (degrees). 1.0 = full colour, up to ~2 = bloom.
  static float ringIntensity(float r, float a) {
    auto gauss = [](float x, float s) { return expf(-(x * x) / (s * s)); };
    float sa = a * 3.14159265f / 180.f;
    // angular segmentation: bright arcs and darker gaps, like the logo's segmented band
    float seg = 0.62f + 0.38f * (0.5f + 0.5f * sinf(3.f * sa + 0.7f)) * (0.65f + 0.35f * sinf(7.f * sa + 2.f));
    float edgeIn = smoothstep(140.2f, 141.8f, r), edgeOut = 1.f - smoothstep(161.2f, 162.8f, r);
    float core = edgeIn * edgeOut;
    float shade = 0.60f + 0.40f * clampf(1.f - (r - 141.f) / 21.f, 0.f, 1.f);
    float streak = 0.55f * gauss(r - 145.f, 1.3f) - 0.20f * gauss(r - 155.f, 1.4f) + 0.30f * gauss(r - 160.f, 0.9f);
    float v = core * (shade + streak) * seg;
    // soft bloom either side of the band
    if (r > 162.f) v += 0.55f * expf(-(r - 162.f) / 8.f) * (0.5f + 0.5f * seg);
    if (r < 141.f) v += 0.42f * expf(-(141.f - r) / 7.f) * (0.5f + 0.5f * seg);
    // thin broken circuit arcs outside, one faint line inside
    float m1 = sinf(5.f * sa + 0.3f) > -0.25f ? 1.f : 0.f, m2 = sinf(4.f * sa + 2.4f) > 0.1f ? 1.f : 0.f;
    float m3 = sinf(6.f * sa + 1.1f) > 0.f ? 1.f : 0.f;
    v += 0.55f * gauss(r - 167.f, 0.8f) * m1 + 0.45f * gauss(r - 170.5f, 0.7f) * m2 + 0.35f * gauss(r - 135.f, 0.7f) * m3;
    return v;
  }

  bool bakeRing() {
    const Transform& t = layout.head;
    const float cx = t.x(ref::kCx), cy = t.y(ref::kCy);
    // spark dots just outside the ring (angle deg, radius ref, size px)
    struct Spark { float a, r, px; };
    static const Spark sparks[] = {{214, 168.5f, 1.5f}, {318, 167.f, 1.3f}, {24, 169.f, 1.2f}, {96, 170.f, 1.3f}, {262, 169.5f, 1.1f}, {160, 168.f, 1.2f}};
    for (int pass = 0; pass < 2; pass++) {
      int n = 0;
      for (int y = 0; y < h; y++)
        for (int x = 0; x < w; x++) {
          float dx = x + 0.5f - cx, dy = y + 0.5f - cy;
          float r = sqrtf(dx * dx + dy * dy) / t.s;
          if (r < ref::kRingMin || r > ref::kRingMax + 1.f) continue;
          float ang = atan2f(dy, dx) * (180.f / 3.14159265f);
          if (ang < 0) ang += 360.f;
          float v = ringIntensity(r, ang);
          for (const Spark& sp : sparks) {
            float sx = cx + sp.r * t.s * cosf(sp.a * 3.14159265f / 180.f), sy = cy + sp.r * t.s * sinf(sp.a * 3.14159265f / 180.f);
            float d = sqrtf((x + 0.5f - sx) * (x + 0.5f - sx) + (y + 0.5f - sy) * (y + 0.5f - sy));
            v += 1.7f * clampf(1.f - d / (sp.px * 1.6f), 0.f, 1.f);
          }
          int prof = (int)(v * 128.f + 0.5f);
          if (prof < 3) continue;
          if (pass == 1) {
            ringX[n] = (uint16_t)x; ringY[n] = (uint16_t)y;
            ringAng[n] = (uint8_t)((int)(ang * (256.f / 360.f)) & 255);
            ringProf[n] = (uint8_t)(prof > 255 ? 255 : prof);
          }
          n++;
        }
      if (pass == 0) {
        ringN = n;
        ringX = (uint16_t*)fbAlloc((size_t)n * 2);
        ringY = (uint16_t*)fbAlloc((size_t)n * 2);
        ringAng = (uint8_t*)fbAlloc(n);
        ringProf = (uint8_t*)fbAlloc(n);
        if (!ringX || !ringY || !ringAng || !ringProf) return false;
      }
    }
    return true;
  }
};

}  // namespace sunday
