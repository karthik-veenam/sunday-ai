// Plain data types shared by the face controller and renderer. No Arduino/M5 dependencies,
// so the same headers compile on the device and in the desktop preview tool.
#pragma once
#include <stdint.h>

namespace sunday {

// One expression preset. Units match the 320x310 SVG editor reference canvas.
struct ExpressionParams {
  float curve;   // mouth curvature, about -100..+100 (positive = smile)
  float width;   // mouth width (reference units)
  float open;    // mouth openness
  float corner;  // vertical mouth-corner lift
  float cheek;   // cheek blush intensity 0..100
  float eye;     // eye openness percent (0 = closed, 100 = normal, >100 = wide)
  float brow;    // eyebrow vertical displacement (positive = raised)
  float tilt;    // eyebrow asymmetry (right brow raised, left lowered by tilt/2)
  float lid;     // OPTIONAL (default 0): shape of a fully closed eye. 0 = relaxed lid (bows down), 1 = happy arc (bows up, ^_^)
};

// A floating heart for pet mode, in screen pixels.
struct Heart { float x, y, size, alpha; };
static constexpr int kMaxHearts = 8;

// Everything the renderer needs for one frame (already smoothed / blink / talk applied).
struct FaceRenderState {
  ExpressionParams e;
  float gazeX, gazeY;   // eye offset in reference units
  float ringRotation;   // degrees
  float ringGlow;       // ~0.2 .. 1.8
  bool ringComet;       // thinking: bright comet orbiting a dim ring
  Heart hearts[kMaxHearts];  // pet-mode overlay (heartCount = 0 => nothing is drawn, output identical to before)
  int heartCount;
};

}  // namespace sunday
