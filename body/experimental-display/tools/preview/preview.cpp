// Desktop preview: compiles the *same* face/ sources as the firmware (FaceRenderer, FaceAssets, FaceLayout,
// Raster, FaceController) -- the framebuffer it writes is the framebuffer the device pushes to the LCD.
//
//   preview <out_dir>
//     classic   : expr_<Name>.ppm, state_<name>.ppm
//     immersive : imm_expr_<Name>.ppm, imm_state_<name>.ppm
//   Also prints the worst-case feature margins of every preset against the screen edges and the head circle.
#include <cstdlib>
#include <stdio.h>
#include <string>
#include "../../src/face/Behavior.h"
#include "../../src/face/FaceRenderer.h"

using namespace sunday;

static void writePpm(const Raster& r, const std::string& path) {
  FILE* f = fopen(path.c_str(), "wb");
  fprintf(f, "P6\n%d %d\n255\n", r.w, r.h);
  for (int i = 0; i < r.w * r.h; i++) {
    float R, G, B;
    unpack565(r.fb[i], R, G, B);
    uint8_t px[3] = {(uint8_t)(R + 0.5f), (uint8_t)(G + 0.5f), (uint8_t)(B + 0.5f)};
    fwrite(px, 1, 3, f);
  }
  fclose(f);
}

static void settle(FaceController& fc, uint32_t& now) {
  for (int i = 0; i < 120; i++) { now += 33; fc.update(now); }  // ~4 s of simulated time
}

// Feature margins (px) of one preset in one layout: distance from the feature bounding box to the screen edges,
// and clearance of its corners from the head circle edge.
static void report(const FaceLayout& L, int W, int H, int expr, float& minEdge, float& minHead) {
  Extents e = L.featureExtents(kExpressions[expr]);
  float x0 = L.feat.x(e.left), x1 = L.feat.x(e.right), y0 = L.feat.y(e.top), y1 = L.feat.y(e.bottom);
  float edge = fminf(fminf(x0, W - x1), fminf(y0, H - y1));
  float hcx = L.head.x(ref::kCx), hcy = L.head.y(ref::kCy), hr = L.head.len(ref::kHeadR);
  float worst = 1e9f;
  for (float x : {x0, x1}) for (float y : {y0, y1}) worst = fminf(worst, hr - sqrtf((x - hcx) * (x - hcx) + (y - hcy) * (y - hcy)));
  if (edge < minEdge) minEdge = edge;
  if (worst < minHead) minHead = worst;
  printf("  %-10s features x[%6.1f..%6.1f] y[%6.1f..%6.1f]  edge margin %5.1f px  head clearance(bbox corners) %6.1f px\n",
         kExpressionNames[expr], x0, x1, y0, y1, edge, worst);
}

int main(int argc, char** argv) {
  std::string out = argc > 1 ? argv[1] : ".";
  const int W = 320, H = 240;  // CoreS3 landscape
  Raster r;
  if (!r.init(W, H)) { fprintf(stderr, "alloc failed\n"); return 1; }

  for (int m = 0; m < kDisplayModeCount; m++) {
    DisplayMode mode = (DisplayMode)m;
    std::string pre = m == 0 ? "" : "imm_";
    FaceAssets assets;
    if (!assets.build(W, H, mode)) { fprintf(stderr, "bake failed\n"); return 1; }
    const FaceLayout& L = assets.layout;
    printf("== %s: head scale %.4f (radius %.1f px, centre %.1f,%.1f)  feature scale %.4f  ring pixels %d\n", kDisplayModeNames[m],
           L.head.s, L.head.len(ref::kHeadR), L.head.x(ref::kCx), L.head.y(ref::kCy), L.feat.s, assets.ringN);
    float minEdge = 1e9f, minHead = 1e9f;
    for (int i = 0; i < kExpressionCount; i++) report(L, W, H, i, minEdge, minHead);
    printf("  -> worst feature-to-screen-edge margin %.1f px, worst head clearance %.1f px\n", minEdge, minHead);

    for (int i = 0; i < kExpressionCount; i++) {
      FaceController fc;
      fc.freezeForPreview(getenv("PREVIEW_TALK") ? (float)atof(getenv("PREVIEW_TALK")) : 1.f);  // no blink / talk wobble / drift: shows the preset itself (PREVIEW_TALK scales the mouth opening)
      fc.setRing(kStateBehavior[0].ring);
      fc.snap((ExprId)i);
      uint32_t now = 1000;
      settle(fc, now);
      renderFace(r, assets, fc.state());
      writePpm(r, out + "/" + pre + "expr_" + kExpressionNames[i] + ".ppm");
    }
    // pet mode: the three moods with floating hearts (new frames only; they never touch the Classic baseline files)
    struct PetShot { const char* name; ExprId e; float gy; int hearts; };
    const PetShot shots[] = {{"pet_delight", ExprId::Delight, -9, 0}, {"pet_happy", ExprId::Happy, -9, 3}, {"pet_bliss", ExprId::Bliss, -3, 6}, {"pet_peek", ExprId::Happy, -9, 5}};
    for (const PetShot& ps : shots) {
      FaceController fc;
      fc.freezeForPreview(1.f);
      fc.setRing(RingStyle{70.f, 1.35f, 0.f, 0.f, false});
      fc.setGaze(0, ps.gy, 0);
      fc.snap(ps.e);
      uint32_t now = 1000;
      settle(fc, now);
      FaceRenderState rs = fc.state();
      const float hx[] = {0.10f, 0.90f, 0.14f, 0.86f, 0.08f, 0.92f}, hy[] = {0.50f, 0.44f, 0.30f, 0.26f, 0.12f, 0.16f}, hz[] = {13, 11, 9, 9, 7, 7}, ha[] = {0.9f, 0.85f, 0.7f, 0.65f, 0.45f, 0.4f};
      rs.heartCount = ps.hearts;
      for (int k = 0; k < ps.hearts; k++) rs.hearts[k] = Heart{W * hx[k], H * hy[k], hz[k] * H / 240.f, ha[k]};
      renderFace(r, assets, rs);
      writePpm(r, out + "/" + pre + ps.name + ".ppm");
    }
    for (int i = 0; i < kStateCount; i++) {
      FaceController fc;
      fc.freezeForPreview(1.f);
      const StateBehavior& b = kStateBehavior[i];
      fc.setRing(b.ring);
      fc.setGaze(b.gazeX, b.gazeY, 0);
      fc.snap(b.expression);
      uint32_t now = 1000;
      settle(fc, now);
      renderFace(r, assets, fc.state());
      writePpm(r, out + "/" + pre + "state_" + kStateNames[i] + ".ppm");
    }
  }
  return 0;
}
