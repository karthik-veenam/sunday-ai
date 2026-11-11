// Sunday face renderer. One small component per facial element, all drawing into a Raster:
//   RingRenderer, HeadRenderer (blit of the baked base), CheekRenderer, EyeRenderer, BrowRenderer, MouthRenderer.
// Geometry is authored in the 320x310 reference space of the SVG face editor (see FaceGeometry.h) and
// mapped to the display by one Transform. No Arduino/M5 dependencies: also compiled by the desktop preview.
#pragma once
#include <math.h>
#include <stdint.h>
#include "FaceAssets.h"
#include "FaceGeometry.h"
#include "FaceLayout.h"
#include "FaceTypes.h"
#include "Raster.h"

namespace sunday {

// ---------------------------------------------------------------- neon ring
struct RingRenderer {
  // Recolour the baked ring pixels. The ring *shape* (segments, streaks, arcs, sparks, bloom) is in the
  // lookup table; per frame only the 256-entry palette changes (rotation, glow, comet, hot-hue bloom).
  static void draw(Raster& r, const FaceAssets& a, const FaceRenderState& s) {
    static const uint8_t bayer[16] = {0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5};
    int pr[256], pg[256], pb[256];
    float glow = clampf(s.ringGlow, 0.05f, 1.8f) * 1.15f;
    for (int i = 0; i < 256; i++) {
      float deg = i * (360.f / 256.f);
      Rgb c = ringColor(deg - s.ringRotation);
      float k = 1.f + 0.55f * smoothstep(0.45f, 0.95f, (c.r - c.b) / 255.f);  // orange hue blooms hotter
      if (s.ringComet) {
        float d = fmodf(deg + s.ringRotation * 1.5f + 720.f, 360.f);
        if (a.layout.softCometEdge && d > 340.f)
          k *= 0.22f + 1.5f * smoothstep(340.f, 360.f, d);  // lead-in ramp, meets the head value (1.72) at d = 360
        else
          k *= 0.22f + 1.5f * (d < 80.f ? 1.f - d / 80.f : 0.f);
      }
      pr[i] = (int)(c.r * glow * k); pg[i] = (int)(c.g * glow * k); pb[i] = (int)(c.b * glow * k);
    }
    const int bgR = (int)palette::kBackground.r, bgG = (int)palette::kBackground.g, bgB = (int)palette::kBackground.b;
    uint16_t* fb = r.fb;
    const int w = r.w;
    for (int n = 0; n < a.ringN; n++) {
      int x = a.ringX[n], y = a.ringY[n], ang = a.ringAng[n], v = a.ringProf[n];
      int R = bgR + ((pr[ang] * v) >> 7), G = bgG + ((pg[ang] * v) >> 7), B = bgB + ((pb[ang] * v) >> 7);
      int hot = R > G ? R : G; if (B > hot) hot = B;
      if (hot > 255) { int e = (hot - 255) >> 1; R += e; G += e; B += e; }  // white-hot spill like real neon
      R = R > 255 ? 255 : R; G = G > 255 ? 255 : G; B = B > 255 ? 255 : B;
      int dth = bayer[(y & 3) * 4 + (x & 3)];
      int r5 = (R + (dth >> 1)) >> 3, g6 = (G + (dth >> 2)) >> 2, b5 = (B + (dth >> 1)) >> 3;
      if (r5 > 31) r5 = 31; if (g6 > 63) g6 = 63; if (b5 > 31) b5 = 31;
      fb[y * w + x] = (uint16_t)((r5 << 11) | (g6 << 5) | b5);
    }
  }
};

// ---------------------------------------------------------------- head
struct HeadRenderer {
  // The white sphere (soft blue-white shading, anti-aliased edge) is baked into the base layer.
  static void draw(Raster& r, const FaceAssets& a) { r.blit(a.base); }
  static Rgb surface() { return {236, 242, 255}; }  // colour near the cheeks, for blush
};

// ---------------------------------------------------------------- stroke helper
// Round-capped stroke along a curve: fn(u, x, y, radius) for u in [0,1] adds discs to the current shape.
template <class F>
inline void strokeCurve(Raster& r, float lengthPx, F fn) {
  int n = (int)ceilf(lengthPx / 0.6f);
  if (n < 8) n = 8;
  for (int i = 0; i <= n; i++) {
    float x, y, rad;
    fn(i / (float)n, x, y, rad);
    r.disc(x, y, rad);
  }
}

// ---------------------------------------------------------------- cheeks
struct CheekRenderer {
  static void draw(Raster& r, const FaceLayout& L, const FaceRenderState& s) {
    const Transform& t = L.feat;
    float k = clampf(s.e.cheek, 0.f, 100.f) / 100.f;
    if (k < 0.02f) return;
    for (int side = -1; side <= 1; side += 2) {
      float cx = t.x(L.cx + side * L.cheekDx), cy = t.y(L.cheekY), rx = t.len(L.cheekRx), ry = t.len(L.cheekRy);
      if (L.clipCheeksToHead)
        r.softenClipped(cx, cy, rx, ry, palette::kBlush, 0.62f * k, L.head.x(ref::kCx), L.head.y(ref::kCy),
                        L.head.len(ref::kHeadR), 6.f);
      else
        r.soften(cx, cy, rx, ry, palette::kBlush, 0.62f * k);  // classic: unchanged
    }
  }
};

// ---------------------------------------------------------------- eyes
struct EyeRenderer {
  static void draw(Raster& r, const FaceLayout& L, const FaceRenderState& s) {
    const Transform& t = L.feat;
    float open = clampf(s.e.eye, 0.f, 130.f) / 100.f;
    float rx = t.len(L.eyeRadX), ry = t.len(L.eyeRadY * open);
    for (int side = -1; side <= 1; side += 2) {
      float cx = t.x((side < 0 ? L.eyeLx : L.eyeRx) + s.gazeX), cy = t.y(L.eyeY + s.gazeY);
      if (ry < 2.0f) {  // closed: relaxed lid, bowed downward in the middle
        float sag = t.len(5.0f) * (1.f - 2.f * s.e.lid), half = rx * 0.92f, th = t.len(2.4f);
        r.beginShape();
        strokeCurve(r, half * 2, [&](float u, float& x, float& y, float& rad) {
          float v = u * 2 - 1;
          x = cx + v * half;
          y = cy + sag * (1 - v * v) - sag * 0.4f;
          rad = th * (0.75f + 0.25f * (1 - v * v));
        });
        r.endShape(palette::kInk);
        continue;
      }
      r.ellipse(cx, cy, rx + L.eyeRimPx, ry + L.eyeRimPx, palette::kEyeRim);
      r.ellipse(cx, cy, rx, ry, palette::kEyeBase);
      r.ellipse(cx, cy + ry * 0.24f, rx * 0.86f, ry * 0.72f, palette::kEyeShade, 0.9f);   // deeper blue below
      r.ellipse(cx - rx * 0.08f, cy - ry * 0.34f, rx * 0.60f, ry * 0.40f, palette::kEyeSheen, 0.30f);  // soft sheen above
      if (ry >= 3.5f) {  // highlights, as in the logo
        r.ellipse(cx + rx * 0.32f, cy - ry * 0.40f, rx * 0.22f, rx * 0.22f, palette::kEyeHighlight);
        r.ellipse(cx - rx * 0.30f, cy + ry * 0.44f, rx * 0.10f, rx * 0.10f, palette::kEyeHighlight2);
      }
    }
  }
};

// ---------------------------------------------------------------- eyebrows
struct BrowRenderer {
  static void draw(Raster& r, const FaceLayout& L, const FaceRenderState& s) {
    const Transform& t = L.feat;
    for (int side = -1; side <= 1; side += 2) {
      float ex = (side < 0 ? L.eyeLx : L.eyeRx) + s.gazeX * 0.5f;
      float tiltOff = side < 0 ? s.e.tilt * 0.5f : -s.e.tilt * 0.5f;  // y grows downward: right brow rises
      float yc = L.browY - s.e.brow + tiltOff;
      r.beginShape();
      strokeCurve(r, t.len(L.browHalfW * 2), [&](float u, float& x, float& y, float& rad) {
        float v = u * 2 - 1;
        x = t.x(ex + v * L.browHalfW);
        y = t.y(yc - L.browArch * (1 - v * v));  // gentle arch
        rad = t.len(L.browThick * 0.5f) * (0.62f + 0.38f * (1 - v * v));  // tapered ends
      });
      r.endShape(palette::kInk);
    }
  }
};

// ---------------------------------------------------------------- mouth
// Quadratic Bezier per the editor:  upper: (cx-hw, cornerY) -Q(cx, controlY)-> (cx+hw, cornerY)
//                                   lower: same endpoints, control = controlY + open*1.55
// x(t) is linear in t (the control x is the midpoint), so the open mouth is the region between two
// functions of x. Filling column by column can therefore never self-intersect or invert.
struct MouthRenderer {
  static void draw(Raster& r, const FaceLayout& L, const ExpressionParams& e) {
    const Transform& t = L.feat;
    float width = clampf(e.width, 6.f, 120.f), hw = width * 0.5f;
    float open = e.open < 0 ? 0 : e.open;
    float cornerY = L.mouthY - e.corner;
    float ctrlU = L.mouthY + clampf(e.curve, -100.f, 100.f) * 0.27f;
    float ctrlL = ctrlU + open * 1.55f;
    float xL = t.x(L.cx - hw), xR = t.x(L.cx + hw);
    auto yAt = [&](float ctrl, float u) { return t.y(cornerY + 2.f * u * (1.f - u) * (ctrl - cornerY)); };

    bool opened = open * 0.775f * t.s >= 0.75f;  // mid-gap in pixels
    if (opened) {
      r.beginShape();
      for (int px = (int)ceilf(xL); px <= (int)floorf(xR); px++) {
        float u = (px + 0.5f - xL) / (xR - xL);
        r.vspan(px, yAt(ctrlU, u), yAt(ctrlL, u));
      }
      r.endShape(palette::kMouthInside);
    }
    float rad = t.len(L.mouthStroke * 0.5f);
    r.beginShape();
    strokeCurve(r, xR - xL, [&](float u, float& x, float& y, float& rr) { x = xL + (xR - xL) * u; y = yAt(ctrlU, u); rr = rad; });
    if (opened)
      strokeCurve(r, xR - xL, [&](float u, float& x, float& y, float& rr) { x = xL + (xR - xL) * u; y = yAt(ctrlL, u); rr = rad; });
    r.endShape(palette::kInk);
  }
};

// ---------------------------------------------------------------- whole face
// ---------------------------------------------------------------- pet-mode hearts (drawn last; nothing happens when heartCount == 0)
struct HeartRenderer {
  static void draw(Raster& r, const FaceRenderState& s) {
    for (int i = 0; i < s.heartCount && i < kMaxHearts; i++) {
      const Heart& h = s.hearts[i];
      r.heart(h.x, h.y, h.size, Rgb{255, 112, 158}, h.alpha);
    }
  }
};

inline void renderFace(Raster& r, const FaceAssets& a, const FaceRenderState& s) {
  HeadRenderer::draw(r, a);  // background + sphere (also resets the frame)
  RingRenderer::draw(r, a, s);
  CheekRenderer::draw(r, a.layout, s);
  EyeRenderer::draw(r, a.layout, s);
  BrowRenderer::draw(r, a.layout, s);
  MouthRenderer::draw(r, a.layout, s.e);
  HeartRenderer::draw(r, s);
}

}  // namespace sunday
