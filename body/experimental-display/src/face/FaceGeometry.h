// Reference-space constants, transform, colour helpers. Shared by device firmware and desktop preview.
#pragma once
#include <math.h>
#include <stdint.h>

namespace sunday {

// ---------------------------------------------------------------- reference space (320x310 SVG editor canvas)
namespace ref {
constexpr float kCx = 160.f, kCy = 150.f, kHeadR = 120.f;            // face centre / head radius
constexpr float kEyeLx = 117.f, kEyeRx = 203.f, kEyeY = 142.f;       // eye centres
constexpr float kEyeRadX = 21.f, kEyeRadY = 26.f;                    // eye size at eye=100 (measured from the logo)
constexpr float kBrowY = 98.f, kBrowHalfW = 20.f, kBrowThick = 5.5f; // eyebrow centre line
constexpr float kMouthY = 214.f, kMouthStroke = 5.f;                 // mouth baseline / outline thickness
constexpr float kCheekDx = 62.f, kCheekY = 182.f, kCheekRx = 24.f, kCheekRy = 13.f;
// Neon ring (outside the head): soft core band plus glow falloff both sides.
constexpr float kRingIn = 146.f, kRingOut = 160.f, kRingMax = 172.f, kRingMin = 124.f;
}  // namespace ref

struct Transform {
  float s, ox, oy;  // screen = o + ref * s   (continuous coords: pixel (x,y) has its centre at x+0.5, y+0.5)
  // Fit the reference face so the outer ring glow just fits the shorter display side.
  static Transform fit(int w, int h) {
    float m = (w < h ? w : h) * 0.5f - 1.0f;
    float s = m / ref::kRingMax;
    return {s, w * 0.5f - ref::kCx * s, h * 0.5f - ref::kCy * s};
  }
  float x(float rx) const { return ox + rx * s; }
  float y(float ry) const { return oy + ry * s; }
  float len(float r) const { return r * s; }
};

// ---------------------------------------------------------------- colour
struct Rgb { float r, g, b; };  // 0..255
inline Rgb mix(Rgb a, Rgb b, float t) { return {a.r + (b.r - a.r) * t, a.g + (b.g - a.g) * t, a.b + (b.b - a.b) * t}; }
inline float clampf(float v, float lo, float hi) { return v < lo ? lo : (v > hi ? hi : v); }
inline float smoothstep(float a, float b, float x) {
  float t = clampf((x - a) / (b - a), 0.f, 1.f);
  return t * t * (3.f - 2.f * t);
}

namespace palette {  // sampled from ~/sunday/sunday.png
constexpr Rgb kBackground{4, 6, 20};
constexpr Rgb kEyeRim{150, 168, 238};
constexpr Rgb kEyeBase{28, 70, 232};
constexpr Rgb kEyeShade{16, 40, 178};
constexpr Rgb kEyeSheen{110, 160, 255};
constexpr Rgb kEyeHighlight{224, 240, 255};
constexpr Rgb kEyeHighlight2{150, 195, 255};
constexpr Rgb kInk{28, 52, 178};         // eyebrows, mouth outline, closed lids
constexpr Rgb kMouthInside{12, 20, 74};  // open-mouth interior
constexpr Rgb kBlush{255, 128, 165};
}  // namespace palette

// Ring hue around the circle (angle in degrees, 0 = 3 o'clock, clockwise): orange at upper-left,
// purple upper-right, blue right, cyan lower-right, violet at the bottom/left -- as in the logo.
inline Rgb ringColor(float deg) {
  struct Stop { float a; Rgb c; };
  static const Stop S[] = {{30, {40, 105, 255}},  {70, {40, 205, 255}},  {130, {55, 105, 245}}, {185, {135, 70, 225}},
                           {225, {255, 140, 30}}, {280, {190, 70, 235}}, {340, {90, 85, 255}},  {390, {40, 105, 255}}};
  deg = fmodf(deg, 360.f);
  if (deg < 0) deg += 360.f;
  if (deg < S[0].a) deg += 360.f;
  for (int i = 0; i < 7; i++)
    if (deg <= S[i + 1].a) return mix(S[i].c, S[i + 1].c, (deg - S[i].a) / (S[i + 1].a - S[i].a));
  return S[7].c;
}

}  // namespace sunday
