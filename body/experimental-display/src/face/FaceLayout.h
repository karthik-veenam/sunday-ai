// Display layouts: where the head / ring / facial features land on the panel.
//
// Both layouts share every renderer component. A layout supplies
//   * `head`  : Transform for the sphere + neon ring   (reference space -> pixels)
//   * `feat`  : Transform for eyes, brows, mouth, cheeks (uniform scale, so nothing is ever stretched)
//   * anchors : feature positions/sizes in reference units
//
// classic   : exactly the original constants (bit-identical output to the first flashed version).
// immersive : head enlarged until it overflows the screen top/bottom (ring pushed into the corners and
//             clipped by the panel); features scaled up and spread out, with the scale derived from the real
//             presets so no expression can ever crop a brow, eye or mouth.
#pragma once
#include <math.h>
#include "FaceGeometry.h"
#include "FaceTypes.h"
#include "expressions_data.h"

namespace sunday {

enum class DisplayMode : uint8_t { Classic, Immersive };
static constexpr int kDisplayModeCount = 2;
static constexpr const char* kDisplayModeNames[kDisplayModeCount] = {"classic", "immersive"};

struct Extents { float left, right, top, bottom; };  // reference units

struct FaceLayout {
  DisplayMode mode = DisplayMode::Classic;
  Transform head{}, feat{};
  float cx = ref::kCx;  // reference x of the face axis
  float eyeLx = ref::kEyeLx, eyeRx = ref::kEyeRx, eyeY = ref::kEyeY;
  float eyeRadX = ref::kEyeRadX, eyeRadY = ref::kEyeRadY, eyeRimPx = 1.1f;
  float browY = ref::kBrowY, browHalfW = ref::kBrowHalfW, browThick = ref::kBrowThick, browArch = 2.5f;
  float mouthY = ref::kMouthY, mouthStroke = ref::kMouthStroke;
  float cheekDx = ref::kCheekDx, cheekY = ref::kCheekY, cheekRx = ref::kCheekRx, cheekRy = ref::kCheekRy;
  bool softCometEdge = false;     // immersive: ramp the comet in over its last 20 deg so the leading edge is not a hard cut
  bool clipCheeksToHead = false;  // immersive: head is cropped by the panel, keep blush inside the sphere

  // Worst-case extent of the facial features for one preset, in reference units. Allows for the largest
  // runtime gaze offset (Thinking gaze + idle drift) and uses the same Bezier formulas as the renderer.
  Extents featureExtents(const ExpressionParams& e) const {
    const float gx = 11.f, gy = 8.f;  // gaze allowance (reference units)
    Extents x{1e9f, -1e9f, 1e9f, -1e9f};
    auto add = [&](float l, float r, float t, float b) {
      if (l < x.left) x.left = l;
      if (r > x.right) x.right = r;
      if (t < x.top) x.top = t;
      if (b > x.bottom) x.bottom = b;
    };
    // eyes (wide-open worst case is the preset itself; blinks only shrink them)
    float open = clampf(e.eye, 0.f, 130.f) / 100.f, ry = eyeRadY * open;
    if (ry * feat.s < 2.0f) ry = 3.5f / feat.s;  // closed lid stroke
    add(eyeLx - eyeRadX - 2 - gx, eyeRx + eyeRadX + 2 + gx, eyeY - ry - 2 - gy, eyeY + ry + 2 + gy);
    // brows
    for (int side = -1; side <= 1; side += 2) {
      float yc = browY - e.brow + (side < 0 ? e.tilt * 0.5f : -e.tilt * 0.5f);
      float ex = side < 0 ? eyeLx : eyeRx;
      add(ex - browHalfW - browThick * 0.5f - gx * 0.5f, ex + browHalfW + browThick * 0.5f + gx * 0.5f,
          yc - browArch - browThick * 0.5f, yc + browThick * 0.5f);
    }
    // mouth
    float hw = clampf(e.width, 6.f, 120.f) * 0.5f, cornerY = mouthY - e.corner;
    float ctrlU = mouthY + clampf(e.curve, -100.f, 100.f) * 0.27f, ctrlL = ctrlU + (e.open < 0 ? 0 : e.open) * 1.55f;
    float yMid = cornerY + 0.5f * (ctrlU - cornerY), yMidL = cornerY + 0.5f * (ctrlL - cornerY);
    float lo = fminf(cornerY, fminf(yMid, yMidL)), hi = fmaxf(cornerY, fmaxf(yMid, yMidL));
    add(cx - hw - mouthStroke * 0.5f, cx + hw + mouthStroke * 0.5f, lo - mouthStroke * 0.5f, hi + mouthStroke * 0.5f);
    return x;
  }
  Extents allExtents() const {  // union over all presets
    Extents u{1e9f, -1e9f, 1e9f, -1e9f};
    for (int i = 0; i < kExpressionCount; i++) {
      Extents x = featureExtents(kExpressions[i]);
      if (x.left < u.left) u.left = x.left;
      if (x.right > u.right) u.right = x.right;
      if (x.top < u.top) u.top = x.top;
      if (x.bottom > u.bottom) u.bottom = x.bottom;
    }
    return u;
  }

  static FaceLayout classic(int w, int h) {
    FaceLayout L;
    L.mode = DisplayMode::Classic;
    L.head = L.feat = Transform::fit(w, h);
    return L;
  }

  // Tunables (fractions of the display height so the layout follows the panel size).
  static constexpr float kHeadRadiusFrac = 0.575f;  // head radius / display height: 0.575 -> 276 px circle on 240 px height
  static constexpr float kFeatureFill = 0.905f;     // features may use this much of the display height (all presets)
  static constexpr float kFeatureWidthFill = 0.80f; // ... and this much of the width

  static FaceLayout immersive(int w, int h) {
    FaceLayout L;
    L.mode = DisplayMode::Immersive;
    // Feature anchors for the big face: eyes spread wider, brows a little closer to the eyes, mouth a little higher,
    // so the whole vertical stack stays compact enough to scale up. Eye / mouth *shapes* are unchanged.
    L.eyeLx = 160.f - 56.f;
    L.eyeRx = 160.f + 56.f;
    L.eyeY = 142.f;
    L.eyeRimPx = 1.7f;
    L.browY = 142.f - 41.f;
    L.browHalfW = 23.f;
    L.mouthY = 142.f + 63.f;
    L.cheekDx = 84.f;
    L.cheekY = 142.f + 33.f;
    L.cheekRx = 27.f;
    L.cheekRy = 15.f;
    L.clipCheeksToHead = true;
    L.softCometEdge = true;
    // Feature scale: fit every preset's worst-case extents inside the allowed fraction of the display.
    L.feat = Transform{1.f, 0.f, 0.f};
    Extents e = L.allExtents();
    float sH = kFeatureFill * h / (e.bottom - e.top);
    float halfW = fmaxf(e.right - L.cx, L.cx - e.left);
    float sW = kFeatureWidthFill * w * 0.5f / halfW;
    float sf = sH < sW ? sH : sW;
    L.feat.s = sf;
    L.feat.ox = w * 0.5f - L.cx * sf;
    L.feat.oy = h * 0.5f - 0.5f * (e.top + e.bottom) * sf;
    // Head + ring: bigger than the display; the sphere keeps the same relation to the features as in classic
    // (reference centre y=150), so the face sits naturally on it.
    float sh = kHeadRadiusFrac * h / ref::kHeadR;
    L.head.s = sh;
    L.head.ox = w * 0.5f - ref::kCx * sh;
    L.head.oy = L.feat.y(ref::kCy) - ref::kCy * sh;
    return L;
  }

  static FaceLayout make(DisplayMode m, int w, int h) { return m == DisplayMode::Immersive ? immersive(w, h) : classic(w, h); }
};

}  // namespace sunday
