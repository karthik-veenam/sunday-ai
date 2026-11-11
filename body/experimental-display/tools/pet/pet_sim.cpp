// Desktop test for pet mode: replays touch scenarios through the real PetEngine, checks the behaviour, and renders every sound to WAV.
//   clang++ -std=c++17 -O1 -Isrc tools/pet/pet_sim.cpp src/pet_sounds.cpp -o /tmp/pet_sim && /tmp/pet_sim /tmp/petout
#include <stdio.h>
#include <string>
#include <vector>
#include "face/PetEngine.h"
#include "pet_sounds.h"

using namespace sunday;
static int fails = 0;
#define CHECK(cond, ...) do { if (!(cond)) { fails++; printf("  FAIL: "); printf(__VA_ARGS__); printf("\n"); } } while (0)

struct Frame { uint32_t t; uint8_t z0, z1, z2; bool sf, sb; };
struct Result {
  std::vector<std::pair<uint32_t, PetSound>> sounds; std::vector<std::pair<uint32_t, std::string>> events;
  bool started = false, ended = false; uint32_t tEnded = 0; float maxAff = 0; int hearts = 0;
  ExprId firstExpr = ExprId::Idle; int bliss = 0, happy = 0, delight = 0; uint32_t tBliss = 0, tHappy = 0;
  float minGazeY = 0, rateHearts = 0; int interrupts = 0; int maxPitch = 0, minPitchActive = 99999; uint32_t tPitchBliss = 0;
};

// touchFn(t_ms) -> input; runs at 50 Hz for `total` ms
template <class F>
static Result run(PetEngine& e, uint32_t total, F touchFn, bool unused = false, uint32_t disallowAt = 0) {
  (void)unused;
  Result r;
  for (uint32_t t = 1000; t < 1000 + total; t += 20) {
    PetInput in = touchFn(t - 1000);
    bool allowed = !(disallowAt && t - 1000 >= disallowAt);
    PetOutput o = e.tick(t, in, allowed);
    if (o.sound != PetSound::None) r.sounds.push_back({t - 1000, o.sound});
    if (o.event[0]) r.events.push_back({t - 1000, o.event});
    if (o.started) r.started = true;
    if (o.ended) { r.ended = true; r.tEnded = t - 1000; }
    r.hearts += o.heartBurst;
    r.rateHearts += o.heartRate * 0.02f;
    if (o.sound != PetSound::None && o.soundInterrupts) r.interrupts++;
    if (o.active) {
      if (r.firstExpr == ExprId::Idle) r.firstExpr = o.expr;
      if (o.affection > r.maxAff) r.maxAff = o.affection;
      if (o.expr == ExprId::Bliss) { if (!r.bliss) r.tBliss = t - 1000; r.bliss++; }
      if (o.expr == ExprId::Happy) { if (!r.happy) r.tHappy = t - 1000; r.happy++; }
      if (o.expr == ExprId::Delight) r.delight++;
      if (o.gazeY < r.minGazeY) r.minGazeY = o.gazeY;
      if (o.pitch > r.maxPitch) r.maxPitch = o.pitch;
      if (o.pitch < r.minPitchActive) r.minPitchActive = o.pitch;
    }
  }
  return r;
}
static PetInput touch(uint8_t a, uint8_t b, uint8_t c) { PetInput i; i.z[0] = a; i.z[1] = b; i.z[2] = c; return i; }
static void show(const char* name, const Result& r) {
  printf("%-34s started=%d ended=%d(%ums) maxAff=%.2f hearts=%d+%.1f | face: delight %d happy %d bliss %d frames | sounds:", name, r.started, r.ended, r.tEnded, r.maxAff, r.hearts, r.rateHearts, r.delight, r.happy, r.bliss);
  for (auto& s : r.sounds) printf(" %s@%u", kPetSoundNames[(int)s.second], s.first);
  printf("\n");
}
static int count(const Result& r, PetSound s) { int n = 0; for (auto& x : r.sounds) n += x.second == s; return n; }

int main(int argc, char** argv) {
  std::string out = argc > 1 ? argv[1] : ".";
  printf("== PetEngine scenarios (50 Hz touch samples)\n");
  {
    PetEngine e; e.seed(1);
    Result r = run(e, 5000, [](uint32_t) { return touch(0, 0, 0); });
    show("1. no touch at all", r);
    CHECK(!r.started && r.sounds.empty() && !r.hearts, "an untouched Sunday must do nothing");
  }
  {
    PetEngine e; e.seed(2);
    Result r = run(e, 6000, [](uint32_t t) { return t >= 500 && t < 620 ? touch(0, 2, 0) : touch(0, 0, 0); });
    show("2. one quick tap (120 ms)", r);
    CHECK(r.started && r.ended, "tap should start and end a pet session");
    CHECK(count(r, PetSound::Boop) >= 1, "a boop on the first touch");
    CHECK(r.delight > 0 && r.bliss == 0, "a single tap is delight, not bliss");
    CHECK(r.tEnded > 500 + 1800 && r.tEnded < 500 + 4500, "lingers ~2 s after the tap then settles (ended at %u)", r.tEnded);
    CHECK(count(r, PetSound::Happy) + count(r, PetSound::Aww) == 1, "one goodbye sound");
    CHECK(r.minGazeY < -5.f, "eyes look up toward the hand (gazeY %.1f)", r.minGazeY);
    CHECK(r.maxPitch <= 300, "a tap tilts the head up gently (max %d = %.1f deg)", r.maxPitch, r.maxPitch / 10.f);
  }
  {
    PetEngine e; e.seed(3);
    Result r = run(e, 9000, [](uint32_t t) { return t >= 300 && t < 5300 ? touch(1, 3, 2) : touch(0, 0, 0); });
    show("3. firm hold for 5 s", r);
    CHECK(r.maxAff > 0.9f, "5 s of petting reaches high affection (%.2f)", r.maxAff);
    CHECK(r.delight > 0 && r.happy > 0 && r.bliss > 0, "goes through delight -> happy -> bliss");
    CHECK(r.tHappy < r.tBliss, "happy comes before bliss");
    CHECK(r.tBliss > 1200 && r.tBliss < 4000, "bliss builds over a couple of seconds (reached at %u ms)", r.tBliss);
    CHECK(count(r, PetSound::Purr) >= 3, "purrs while it is enjoying it (%d purr chunks)", count(r, PetSound::Purr));
    CHECK(count(r, PetSound::Aww) == 1, "ends with a contented aww");
    CHECK(r.maxPitch >= 480 && r.maxPitch <= kPetPitchMax, "full bliss leans the head up ~50 deg and never beyond the %d cap (max %d)", kPetPitchMax, r.maxPitch);
    CHECK(r.minPitchActive >= 0, "pitch never negative");
    CHECK(r.hearts + r.rateHearts >= 6, "hearts float up (%d bursts + %.1f from the continuous rate)", r.hearts, r.rateHearts);
    // purr chunks must not overlap
    for (size_t i = 1; i < r.sounds.size(); i++)
      CHECK(r.sounds[i].first >= r.sounds[i - 1].first + kPetSoundMs[(int)r.sounds[i - 1].second] - 20, "sounds must not overlap (%s then %s)",
            kPetSoundNames[(int)r.sounds[i - 1].second], kPetSoundNames[(int)r.sounds[i].second]);   // (no strokes in this scenario, so no interruptions)
  }
  {
    PetEngine e; e.seed(4);
    std::vector<uint32_t> swipes = {900, 2300};
    Result r = run(e, 7000, [&](uint32_t t) {
      PetInput in = t >= 300 && t < 3500 ? touch(1, 2, 1) : touch(0, 0, 0);
      for (auto s : swipes) if (t >= s && t < s + 20) in.swipeForward = true;
      return in;
    });
    show("4. petting with two strokes", r);
    CHECK(count(r, PetSound::Wheee) + count(r, PetSound::Coo) == 2, "each stroke gets a coo/wheee (%d)", count(r, PetSound::Wheee) + count(r, PetSound::Coo));
    CHECK(count(r, PetSound::Wheee) == 1 && count(r, PetSound::Coo) == 1, "strokes alternate wheee / coo");
    CHECK(r.interrupts >= 1, "a stroke may cut through the purr (%d interruptions)", r.interrupts);
    // a purr must always resume after the stroke sound, never overlap it
    for (size_t i = 1; i < r.sounds.size(); i++)
      if (r.sounds[i].second == PetSound::Purr)
        CHECK(r.sounds[i].first >= r.sounds[i - 1].first + kPetSoundMs[(int)r.sounds[i - 1].second] - 20 || r.sounds[i - 1].second == PetSound::Purr,
              "purr must wait for the stroke sound before it (%u vs %u)", r.sounds[i].first, r.sounds[i - 1].first);
  }
  {
    PetEngine e; e.seed(5);
    Result r = run(e, 7000, [](uint32_t t) {          // 6 rapid taps (tickling)
      uint32_t k = t >= 400 ? (t - 400) / 260 : 99;
      return k < 6 && (t - 400) % 260 < 90 ? touch(0, 3, 0) : touch(0, 0, 0);
    });
    show("5. six rapid taps", r);
    CHECK(count(r, PetSound::Giggle) == 1, "rapid tapping makes it giggle exactly once (%d)", count(r, PetSound::Giggle));
  }
  {
    PetEngine e; e.seed(6);
    Result r = run(e, 4000, [](uint32_t t) { return t >= 300 ? touch(0, 3, 3) : touch(0, 0, 0); }, true, 1500);
    show("6. conversation starts mid-pet", r);
    CHECK(r.ended, "petting must stop at once when Sunday is no longer idle");
    CHECK(r.tEnded <= 1540, "...within one tick of the brain leaving idle (%u)", r.tEnded);
    CHECK(count(r, PetSound::Aww) + count(r, PetSound::Happy) == 0, "no goodbye sound over a conversation");
  }
  {
    PetEngine e; e.seed(7);
    Result r = run(e, 3000, [](uint32_t t) { return t >= 300 && t < 320 ? touch(0, 3, 0) : touch(0, 0, 0); });
    show("7. one 20 ms glitch (noise)", r);
    CHECK(!r.started, "a single noisy sample must not start petting");
  }
  {
    PetEngine e; e.seed(8);
    uint32_t t0 = 0; bool cancelled = false;
    Result r;
    for (uint32_t t = 1000; t < 3000; t += 20) {
      if (t == 2000) { e.cancel(); cancelled = true; }
      PetOutput o = e.tick(t, touch(0, 3, 3), true);
      if (o.ended) { r.ended = true; r.tEnded = t - 1000; }
    }
    (void)t0; printf("%-34s ended=%d at %ums\n", "8. cancel() while petting", r.ended, r.tEnded);
    CHECK(cancelled && r.ended && r.tEnded == 1000, "cancel() ends the session immediately");
  }

  printf("\n== sounds\n");
  if (!petsound::init()) { printf("  FAIL: sound init\n"); fails++; }
  for (int i = 1; i < kPetSoundCount; i++) {
    for (int v = 0; v < petsound::variants((PetSound)i); v++) {
      auto c = petsound::get((PetSound)i, v);
      uint32_t ms = (uint32_t)(c.samples * 1000ull / petsound::kRate);
      int peak = 0; double acc = 0;
      for (size_t k = 0; k < c.samples; k++) { int a = abs(c.pcm[k]); if (a > peak) peak = a; acc += (double)c.pcm[k] * c.pcm[k]; }
      printf("  %-7s v%d  %4u ms (table %3u)  peak %5.1f%%  rms %5.1f%%  first/last sample %d/%d\n", kPetSoundNames[i], v, ms, kPetSoundMs[i], 100.0 * peak / 32767,
             100.0 * sqrt(acc / c.samples) / 32767, c.pcm[0], c.pcm[c.samples - 1]);
      CHECK(ms >= kPetSoundMs[i] - 2 && ms <= kPetSoundMs[i] + 2, "%s duration %u must equal the table %u (the engine relies on it)", kPetSoundNames[i], ms, kPetSoundMs[i]);
      CHECK(peak < 32767 * 0.96, "%s must not clip", kPetSoundNames[i]);
      CHECK(abs(c.pcm[0]) < 600 && abs(c.pcm[c.samples - 1]) < 600, "%s must start/end near silence (no click)", kPetSoundNames[i]);
      if (!out.empty()) {
        std::string path = out + "/" + kPetSoundNames[i] + "_v" + std::to_string(v) + ".wav";
        FILE* f = fopen(path.c_str(), "wb");
        if (f) {
          uint32_t dl = c.samples * 2, rl = dl + 36, sr = petsound::kRate, br = sr * 2; uint16_t fmt = 1, ch = 1, ba = 2, bps = 16; uint32_t fl = 16;
          fwrite("RIFF", 1, 4, f); fwrite(&rl, 4, 1, f); fwrite("WAVEfmt ", 1, 8, f); fwrite(&fl, 4, 1, f); fwrite(&fmt, 2, 1, f); fwrite(&ch, 2, 1, f);
          fwrite(&sr, 4, 1, f); fwrite(&br, 4, 1, f); fwrite(&ba, 2, 1, f); fwrite(&bps, 2, 1, f); fwrite("data", 1, 4, f); fwrite(&dl, 4, 1, f);
          fwrite(c.pcm, 2, c.samples, f); fclose(f);
        }
      }
    }
  }
  printf("\n%s (%d failed checks)\n", fails ? "FAILED" : "ALL PET CHECKS PASSED", fails);
  return fails ? 1 : 0;
}
