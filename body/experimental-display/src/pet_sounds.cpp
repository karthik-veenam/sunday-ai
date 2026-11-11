#include "pet_sounds.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include <vector>
#if defined(ARDUINO)
#include <esp_heap_caps.h>
#endif

namespace petsound {
namespace {

using sunday::PetSound;
constexpr float kTwoPi = 6.2831853f;
constexpr int kMaxVariants = 3;

struct Stored { int16_t* pcm = nullptr; size_t n = 0; } g_clips[sunday::kPetSoundCount][kMaxVariants];
bool g_ready = false;

size_t N(float ms) { return (size_t)(ms * 0.001f * kRate + 0.5f); }

// One voiced note: pitch glides f0 -> f1 (exponential), optional vibrato, harmonics, attack/decay envelope. Mixed into `buf` at `start`.
void note(std::vector<float>& buf, size_t start, float ms, float f0, float f1, float amp, float attackMs, float decayPow,
          float vibHz = 0, float vibDepth = 0, float h2 = 0.25f, float h3 = 0.08f) {
  size_t n = N(ms);
  float ph = 0;
  for (size_t i = 0; i < n && start + i < buf.size(); i++) {
    float u = (float)i / (float)n;
    float f = f0 * powf(f1 / f0, u);
    if (vibHz > 0) f *= 1.f + vibDepth * sinf(kTwoPi * vibHz * (float)i / kRate);
    ph += kTwoPi * f / kRate;
    float env = (float)i / kRate < attackMs * 0.001f ? ((float)i / kRate) / (attackMs * 0.001f) : 1.f;
    env *= powf(1.f - u, decayPow);                       // decay to silence at the end of the note (no clicks)
    float v = sinf(ph) + h2 * sinf(2 * ph) + h3 * sinf(3 * ph);
    buf[start + i] += amp * env * v / (1.f + h2 + h3);
  }
}

void store(PetSound id, int variant, const std::vector<float>& buf) {
  size_t n = buf.size();
  int16_t* p =
#if defined(ARDUINO)
      (int16_t*)heap_caps_malloc(n * 2, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
#else
      (int16_t*)malloc(n * 2);
#endif
  if (!p) return;
  for (size_t i = 0; i < n; i++) {
    float v = buf[i];
    v = v > 0.95f ? 0.95f : (v < -0.95f ? -0.95f : v);   // headroom: never clip
    p[i] = (int16_t)(v * 32767.f);
  }
  g_clips[(int)id][variant] = {p, n};
}

std::vector<float> blank(PetSound id) { return std::vector<float>(N((float)sunday::kPetSoundMs[(int)id]), 0.f); }

void makeBoop(int v) {            // short rising "boop" on touch; three pitches so it is never monotonous
  auto b = blank(PetSound::Boop);
  float f0 = 520.f + 80.f * v;
  note(b, 0, 130, f0, f0 * 1.75f, 0.55f, 6, 1.6f, 0, 0, 0.35f, 0.1f);
  store(PetSound::Boop, v, b);
}
void makeChirp(int v) {           // two quick notes "pi-pi"
  auto b = blank(PetSound::Chirp);
  float f = 780.f + 100.f * v;
  note(b, 0, 80, f, f * 1.1f, 0.5f, 5, 1.3f);
  note(b, N(95), 105, f * 1.35f, f * 1.5f, 0.5f, 5, 1.5f);
  store(PetSound::Chirp, v, b);
}
void makeCoo() {                  // a long contented glide with vibrato, for strokes
  auto b = blank(PetSound::Coo);
  size_t n = b.size();
  float ph = 0;
  for (size_t i = 0; i < n; i++) {
    float u = (float)i / n;
    float f = u < 0.55f ? 430.f * powf(780.f / 430.f, u / 0.55f) : 780.f * powf(640.f / 780.f, (u - 0.55f) / 0.45f);
    f *= 1.f + 0.025f * sinf(kTwoPi * 6.5f * i / kRate);
    ph += kTwoPi * f / kRate;
    float env = fminf(1.f, u / 0.11f) * fminf(1.f, (1.f - u) / 0.32f);
    b[i] = 0.5f * env * (sinf(ph) + 0.25f * sinf(2 * ph)) / 1.25f;
  }
  store(PetSound::Coo, 0, b);
}
void makeWheee() {                // fast bright upward sweep
  auto b = blank(PetSound::Wheee);
  note(b, 0, 340, 500, 1500, 0.5f, 14, 0.9f, 0, 0, 0.2f, 0.05f);
  store(PetSound::Wheee, 0, b);
}
void makeGiggle(int v) {          // four little up-down hiccups, climbing in pitch
  auto b = blank(PetSound::Giggle);
  float base = v ? 880.f : 800.f;
  for (int k = 0; k < 4; k++) {
    float f = base * (1.f + 0.12f * k);
    size_t st = N(8.f + 128.f * k);
    note(b, st, 56, f, f * 1.28f, 0.48f, 4, 1.0f);
    note(b, st + N(46), 62, f * 1.28f, f * 1.02f, 0.4f, 2, 1.4f);
  }
  store(PetSound::Giggle, v, b);
}
void makePurr() {                 // low rumble with a 26 Hz flutter; small speakers carry it through the harmonics
  auto b = blank(PetSound::Purr);
  size_t n = b.size();
  float ph = 0;
  uint32_t rng = 12345;
  for (size_t i = 0; i < n; i++) {
    float u = (float)i / n, t = (float)i / kRate;
    float f = 150.f + 18.f * sinf(kTwoPi * 1.5f * t);
    ph += kTwoPi * f / kRate;
    float carrier = 0.f;
    for (int h = 1; h <= 6; h++) carrier += sinf(ph * h) / (float)h;          // buzzy harmonics (sawtooth-ish)
    float am = 0.5f * (1.f + sinf(kTwoPi * 26.f * t));
    am = powf(am, 1.4f);
    rng = rng * 1664525u + 1013904223u;
    float noise = ((int32_t)rng >> 16) / 32768.f * 0.008f;
    float env = fminf(1.f, u / 0.05f) * fminf(1.f, (1.f - u) / 0.05f);
    b[i] = 0.42f * env * (0.25f + 0.75f * am) * carrier / 2.2f + noise * env;
  }
  store(PetSound::Purr, 0, b);
}
void makeAww() {                  // contented sigh: two falling notes with vibrato
  auto b = blank(PetSound::Aww);
  note(b, 0, 320, 900, 720, 0.45f, 25, 0.8f, 5.5f, 0.02f, 0.2f, 0.05f);
  note(b, N(300), 480, 720, 520, 0.42f, 30, 1.1f, 5.5f, 0.025f, 0.2f, 0.05f);
  store(PetSound::Aww, 0, b);
}
void makeHappy() {                // tiny rising arpeggio C6-E6-G6
  auto b = blank(PetSound::Happy);
  const float f[3] = {1046.5f, 1318.5f, 1568.f};
  for (int k = 0; k < 3; k++) note(b, N(8.f + 105.f * k), 110, f[k], f[k], 0.42f, 3, 1.6f, 0, 0, 0.3f, 0.1f);
  store(PetSound::Happy, 0, b);
}

}  // namespace

bool init() {
  if (g_ready) return true;
  for (int v = 0; v < 3; v++) makeBoop(v);
  for (int v = 0; v < 2; v++) { makeChirp(v); makeGiggle(v); }
  makeCoo(); makeWheee(); makePurr(); makeAww(); makeHappy();
  g_ready = true;
  for (int i = 1; i < sunday::kPetSoundCount; i++)
    if (!g_clips[i][0].pcm) g_ready = false;
  return g_ready;
}

int variants(PetSound id) {
  return id == PetSound::Boop ? 3 : (id == PetSound::Chirp || id == PetSound::Giggle ? 2 : (id == PetSound::None ? 0 : 1));
}

Clip get(PetSound id, uint8_t variant) {
  int i = (int)id;
  if (i <= 0 || i >= sunday::kPetSoundCount) return {nullptr, 0};
  int v = variants(id) ? variant % variants(id) : 0;
  return {g_clips[i][v].pcm, g_clips[i][v].n};
}

}  // namespace petsound
