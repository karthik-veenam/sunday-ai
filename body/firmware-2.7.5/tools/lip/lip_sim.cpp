// Offline checks for the audio-driven mouth and the listening reaction (no hardware).
//   clang++ -std=c++17 -O1 -Isrc tools/lip/lip_sim.cpp -o /tmp/lip_sim && /tmp/lip_sim
#include <cstdio>
#include "face/Behavior.h"
using namespace sunday;
static int fails = 0;
#define CHECK(c, ...) do { if (c) printf("  ok:   "); else { printf("  FAIL: "); fails++; } printf(__VA_ARGS__); printf("\n"); } while (0)

static void run(FaceController& fc, uint32_t& now, int ms) { for (int t = 0; t < ms; t += 33) { now += 33; fc.update(now); } }

int main() {
  FaceController fc; uint32_t now = 1000;
  fc.snap(ExprId::Speaking);
  fc.setExpression(ExprId::Speaking); fc.setRing(kStateBehavior[(int)State::Speaking].ring);
  run(fc, now, 1500);
  float flapBase = fc.state().e.open;

  printf("lip sync\n");
  fc.setLip(true, 0.f); run(fc, now, 400);
  float closed = fc.state().e.open;
  CHECK(closed < 0.15f * flapBase + 0.01f, "silence -> mouth (nearly) closed: %.2f vs preset %.2f", closed, flapBase);
  fc.setLip(true, 1.f); run(fc, now, 200);
  float wide = fc.state().e.open;
  CHECK(wide > 1.0f * flapBase, "loud speech -> mouth wide open: %.2f", wide);
  fc.setLip(true, 0.4f); run(fc, now, 200);
  float mid = fc.state().e.open;
  CHECK(mid > closed + 0.1f && mid < wide, "medium level in between: %.2f (closed %.2f, wide %.2f)", mid, closed, wide);
  fc.setLip(true, 0.f); run(fc, now, 200);
  CHECK(fc.state().e.open < 0.25f * flapBase, "audio stops -> mouth closes within 200 ms: %.2f", fc.state().e.open);
  // end of playback: lip mode off and no talk flap -> stays at the preset's resting open (never keeps flapping)
  fc.setTalking(false); fc.setLip(false, 0.f); run(fc, now, 600);
  float a = fc.state().e.open; run(fc, now, 300); float b = fc.state().e.open;
  CHECK(a == b, "after speech the mouth is steady (no wobble): %.3f == %.3f", a, b);

  printf("listening reaction\n");
  fc.setExpression(ExprId::Listening); fc.setRing(kStateBehavior[(int)State::Listening].ring);
  fc.setAttention(0.f); run(fc, now, 1500);
  float eye0 = fc.state().e.eye, brow0 = fc.state().e.brow, glow0 = fc.state().ringGlow;
  fc.setAttention(1.f); run(fc, now, 400);
  CHECK(fc.state().e.eye > eye0 * 1.05f || fc.state().e.eye == 0.f, "voice -> eyes widen: %.1f -> %.1f (blink may close them)", eye0, fc.state().e.eye);
  CHECK(fc.state().e.brow > brow0 + 3.f, "voice -> brows lift: %.1f -> %.1f", brow0, fc.state().e.brow);
  CHECK(fc.state().ringGlow > glow0 + 0.15f, "voice -> ring brightens: %.2f -> %.2f", glow0, fc.state().ringGlow);
  fc.setAttention(0.f); run(fc, now, 1500);
  CHECK(fabsf(fc.state().e.brow - brow0) < 0.5f, "silence -> back to the calm listening face: brow %.1f", fc.state().e.brow);

  printf(fails ? "\n%d FAILED\n" : "\nALL LIP/LISTEN CHECKS PASSED\n", fails);
  return fails != 0;
}
