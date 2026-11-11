// Pet mode logic: turns raw head-touch samples into feelings and reactions. Pure logic (no Arduino/M5), unit-tested on the desktop.
//
// Input, ~every 20 ms: the three head touch zones (0..3 each: front, middle, back) and the BSP's swipe flags.
// Output: what the face / ring / hearts should do and which sound to play.
//
// Behaviour in one paragraph: touching Sunday's head perks it up (boop, wide happy eyes looking up at your hand). While you keep
// petting, an *affection* level climbs (faster with a firmer touch, strokes give a bonus); it drives the face from Delight -> Happy ->
// Bliss (eyes closed in happy arcs, big blush, purring, hearts, now and then a peek up at you). Quick repeated taps make it giggle.
// When you stop it lingers a moment, then lets out a contented "aww" and goes back to what it was doing. It only reacts while
// the brain is idle (or Sunday is asleep), so a conversation is never interrupted.
#pragma once
#include <math.h>
#include <stdint.h>
#include "FaceController.h"
#include "expressions_data.h"

namespace sunday {

enum class PetSound : uint8_t { None, Boop, Chirp, Coo, Wheee, Giggle, Purr, Aww, Happy };
// Length of each sound in ms (the synthesiser produces exactly these; the engine uses them to avoid overlapping sounds).
static constexpr uint32_t kPetSoundMs[] = {0, 150, 220, 560, 360, 560, 640, 800, 380};
static constexpr int kPetSoundCount = 9;
static constexpr const char* kPetSoundNames[kPetSoundCount] = {"none", "boop", "chirp", "coo", "wheee", "giggle", "purr", "aww", "happy"};

// Hard limit for the physical head tilt used by pet mode (0.1 degree units): 250 = 25 degrees up from level.
static constexpr int kPetPitchMax = 550;

struct PetInput {
  uint8_t z[3] = {0, 0, 0};        // front, middle, back intensity 0..3
  bool swipeForward = false;       // one-shot, from the BSP gesture detector
  bool swipeBackward = false;
};

struct PetOutput {
  bool active = false;
  bool started = false, ended = false;
  ExprId expr = ExprId::Delight;
  float gazeX = 0, gazeY = 0, drift = 0;      // reference units
  RingStyle ring{18, 0.85f, 0.25f, 0.10f, false};
  float heartRate = 0;                         // hearts per second
  int heartBurst = 0;                          // extra hearts to spawn right now
  PetSound sound = PetSound::None;
  uint8_t soundVariant = 0;
  bool soundInterrupts = false;                // cut the purr that is playing so this sound is heard at once
  float affection = 0;                         // 0..1
  int pitch = 0;                               // head tilt-up target in 0.1 degree units (0 = level); only meaningful while active
  int pitchSpeed = 250;                        // BSP motion speed 0..1000 (low = slow and gentle)
  const char* event = "";                      // last notable event, for logs / status
};

class PetEngine {
 public:
  void seed(uint32_t s) { rng_ = s ? s : 0xC0FFEEu; }
  void cancel() { cancel_ = true; }            // external interruption (wake word, state change): end at once, silently

  PetOutput tick(uint32_t now, const PetInput& in, bool allowed) {
    PetOutput o;
    float dt = last_ ? (now - last_) / 1000.f : 0.02f;
    if (dt > 0.2f) dt = 0.2f;
    last_ = now;

    if (cancel_) {
      cancel_ = false;
      if (active_) { reset(); o.ended = true; o.event = "cancelled"; }
      return o;
    }
    if (!allowed) {
      if (active_) { reset(); o.ended = true; o.event = "interrupted"; }
      onCount_ = offCount_ = 0; touched_ = false;
      return o;
    }

    // ---- debounce the raw touch: 2 samples (~40 ms) to start, 4 (~80 ms) to end
    bool raw = (in.z[0] | in.z[1] | in.z[2]) != 0;
    if (raw) { onCount_++; offCount_ = 0; } else { offCount_++; onCount_ = 0; }
    bool rising = false, falling = false;
    if (!touched_ && onCount_ >= 2) { touched_ = true; rising = true; }
    else if (touched_ && offCount_ >= 4) { touched_ = false; falling = true; }
    float firm = (in.z[0] + in.z[1] + in.z[2]) / 6.f;   // two zones at level 3 = full firmness
    if (firm > 1.f) firm = 1.f;

    auto emit = [&](PetSound snd, uint8_t variant = 0) {
      if (o.sound != PetSound::None) return;
      bool busy = now < soundBusyUntil_;
      bool cutsPurr = busy && curSound_ == PetSound::Purr && snd != PetSound::Purr;   // the purr is background texture
      if (busy && !cutsPurr) return;
      o.sound = snd; o.soundVariant = variant; o.soundInterrupts = cutsPurr;
      curSound_ = snd;
      soundBusyUntil_ = now + kPetSoundMs[(int)snd];
      if (snd != PetSound::Purr && nextPurr_ < soundBusyUntil_) nextPurr_ = soundBusyUntil_;   // purr resumes after it
    };

    if (rising) {
      touchStart_ = now;
      if (!active_) {
        active_ = true; activeSince_ = now; o.started = true;
        if (aff_ < 0.08f) aff_ = 0.08f;
        stage_ = 0; maxAff_ = aff_; strokes_ = 0;
        o.event = "pet_start";
        emit(PetSound::Boop, (uint8_t)(rnd() % 3));
      } else {
        aff_ += 0.10f;                                // touched again while still lingering
        o.event = "touch_again";
        emit(PetSound::Chirp, (uint8_t)(rnd() % 2));
      }
      o.heartBurst += 1;
    }

    if (active_) {
      if (touched_) {
        lastTouch_ = now;
        aff_ += (0.15f + 0.20f * firm) * dt;           // builds over a few seconds: delight -> happy -> bliss
        if (in.swipeForward || in.swipeBackward) {
          aff_ += 0.16f; o.heartBurst += 3; strokes_++; o.event = "stroke";
          if (!stroked_ || now - lastStroke_ > 600) emit(strokes_ % 2 ? PetSound::Wheee : PetSound::Coo);
          stroked_ = true;
          lastStroke_ = now;
        }
        // purring: once it is really enjoying it, keep a purr going (a new chunk as the last one finishes)
        if (aff_ > 0.5f && now >= nextPurr_ && now >= soundBusyUntil_ && now - touchStart_ > 500) {
          emit(PetSound::Purr);
          nextPurr_ = now + kPetSoundMs[(int)PetSound::Purr] - 30;
        }
      }
      if (falling) {
        lastTouchEnd_ = now;
        if (now - touchStart_ < 350) {                // a tap
          aff_ += 0.20f; o.heartBurst += 1; o.event = "tap";
          emit(rnd() & 1 ? PetSound::Chirp : PetSound::Boop, (uint8_t)(rnd() % 2));
          for (int i = 7; i > 0; i--) taps_[i] = taps_[i - 1];
          taps_[0] = now;
          int recent = 0;
          for (int i = 0; i < 8; i++) if (taps_[i] && now - taps_[i] < 2000) recent++;
          if (recent >= 5 && (!giggled_ || now - lastGiggle_ > 4000)) {   // rapid tapping = tickling
            giggled_ = true; lastGiggle_ = now; aff_ += 0.12f; o.heartBurst += 4; o.event = "giggle";
            o.sound = PetSound::Giggle;                // overrides the tap sound of this same tick
            o.soundVariant = (uint8_t)(rnd() % 2);
            o.soundInterrupts = true;
            curSound_ = PetSound::Giggle;
            soundBusyUntil_ = now + kPetSoundMs[(int)PetSound::Giggle];
          }
        }
      }
      if (aff_ > 1.f) aff_ = 1.f;
      if (aff_ > maxAff_) maxAff_ = aff_;

      // ---- after the hand leaves: hold the mood a moment, let it fade, then say goodbye
      if (!touched_) {
        uint32_t since = now - lastTouchEnd_;
        if (since > 800) aff_ -= 0.20f * dt;
        if (since > 2800 || aff_ <= 0.01f) {
          curSound_ = PetSound::None;
          if (maxAff_ >= 0.40f) { soundBusyUntil_ = 0; emit(PetSound::Aww); o.soundInterrupts = true; }
          else if (maxAff_ >= 0.15f) { soundBusyUntil_ = 0; emit(PetSound::Happy); o.soundInterrupts = true; }
          reset();
          o.ended = true; o.event = "pet_end";
          return o;
        }
      }
    }

    // ---- what the face should look like (with hysteresis between the three moods)
    if (active_) {
      float up = 0.24f + (stage_ >= 1 ? -0.05f : 0.f), up2 = 0.58f + (stage_ >= 2 ? -0.05f : 0.f);
      stage_ = aff_ >= up2 ? 2 : (aff_ >= up ? 1 : 0);
      ExprId e = stage_ == 2 ? ExprId::Bliss : (stage_ == 1 ? ExprId::Happy : ExprId::Delight);
      // in bliss, now and then, peek open and look up at the person
      if (stage_ == 2 && touched_ && now >= nextPeek_ && now >= peekUntil_) {
        peekUntil_ = now + 380;
        nextPeek_ = now + 2200 + rnd() % 1800;
      }
      bool peeking = now < peekUntil_;
      if (stage_ == 2 && (peeking || !touched_)) e = ExprId::Happy;   // eyes open while peeking or while the hand has just left
      o.expr = e;
      o.active = true;
      float t = (now - activeSince_) / 1000.f;
      float ramp = (now - activeSince_) < 220 ? (now - activeSince_) / 220.f : 1.f;       // eyes snap up toward the hand
      o.gazeY = -9.f * ramp * (e == ExprId::Bliss ? 0.3f : 1.f);
      o.gazeX = (stage_ >= 1 ? 2.2f : 0.8f) * sinf(6.2831853f * 0.9f * t);
      o.drift = 0.5f;
      o.ring = RingStyle{30.f + 70.f * aff_, 0.95f + 0.55f * aff_, 1.2f + 2.8f * aff_, 0.15f + 0.25f * aff_, false};
      o.heartRate = aff_ > 0.5f ? 1.5f + 5.f * (aff_ - 0.5f) : 0.f;
      o.affection = aff_;
      // physical head: tilt up toward the hand, lean in as it enjoys it more, a slow bob while blissful, relax a little when the hand leaves
      float lean = (aff_ - 0.28f) / 0.72f * 500.f;                               // none on first touch; creeps up as affection builds, ~50 degrees at full bliss
      lean = lean < 0.f ? 0.f : (float)((int)(lean / 25.f) * 25);                // quantised to 2.5 degree steps: slow small moves, no constant retargeting
      if (!touched_) lean *= 0.6f;
      float bob = (stage_ == 2 && touched_) ? 14.f * sinf(6.2831853f * 0.7f * t) : 0.f;
      int p = (int)(lean + bob);
      o.pitch = p < 0 ? 0 : (p > kPetPitchMax ? kPetPitchMax : p);              // hard cap: never beyond 55 degrees
      o.pitchSpeed = (now - activeSince_) < 400 ? 150 : 110;                     // a quick perk-up, then slow and gentle
    }
    return o;
  }

  bool active() const { return active_; }
  float affection() const { return aff_; }

 private:
  void reset() { stroked_ = false; active_ = false; aff_ = 0; maxAff_ = 0; stage_ = 0; touched_ = false; onCount_ = offCount_ = 0; nextPurr_ = 0; strokes_ = 0; }
  uint32_t rnd() { rng_ ^= rng_ << 13; rng_ ^= rng_ >> 17; rng_ ^= rng_ << 5; return rng_; }

  uint32_t rng_ = 0xC0FFEEu, last_ = 0;
  bool cancel_ = false, active_ = false, touched_ = false, stroked_ = false, giggled_ = false;
  PetSound curSound_ = PetSound::None;
  uint8_t onCount_ = 0, offCount_ = 0;
  uint32_t touchStart_ = 0, lastTouch_ = 0, lastTouchEnd_ = 0, activeSince_ = 0;
  uint32_t soundBusyUntil_ = 0, nextPurr_ = 0, lastStroke_ = 0, lastGiggle_ = 0, nextPeek_ = 0, peekUntil_ = 0;
  uint32_t taps_[8] = {0, 0, 0, 0, 0, 0, 0, 0};
  float aff_ = 0, maxAff_ = 0;
  int stage_ = 0, strokes_ = 0;
};

}  // namespace sunday
