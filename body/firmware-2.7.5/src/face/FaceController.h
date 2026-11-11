// Expression manager + animation/transition controller. Pure logic: no Arduino/M5 dependencies.
#pragma once
#include <math.h>
#include <stdint.h>
#include "FaceTypes.h"
#include "expressions_data.h"

namespace sunday {

// Ring animation style. Belongs to the operational *state*, not to the expression.
struct RingStyle {
  float speed;    // degrees per second
  float glow;     // base intensity
  float pulseHz;  // glow pulse frequency
  float pulseAmp; // glow pulse amplitude
  bool comet;     // thinking: orbiting comet
};

// All animation timing in one place (milliseconds unless noted).
struct FaceTiming {
  float eyeTau = 70, browTau = 90, mouthTau = 110, cheekTau = 150, tiltTau = 120;  // exponential smoothing
  float gazeTau = 180, ringGlowTau = 120, talkBlendTau = 80;
  uint32_t blinkMinMs = 2500, blinkMaxMs = 6000;
  float blinkCloseMs = 70, blinkHoldMs = 30, blinkOpenMs = 110;
  float blinkMinEye = 30;      // no blinking when the eye is more closed than this (sleepy/sleeping)
  float talkHz = 3.3f;         // mouth flap rate while speaking
  float talkMin = 0.30f;       // mouth never closes below this fraction of the preset's `open`
};

class FaceController {
 public:
  FaceController() { snap(ExprId::Idle); }

  void seed(uint32_t s) { rng_ = s ? s : 0x9E3779B9u; scheduleBlink(0); }
  FaceTiming& timing() { return timing_; }

  void snap(ExprId id) {  // jump without animating (boot, previews)
    target_ = cur_ = kExpressions[(int)id];
    id_ = id;
    blinking_ = false;
  }
  void setExpression(ExprId id) { id_ = id; target_ = kExpressions[(int)id]; }
  ExprId expression() const { return id_; }
  void setRing(const RingStyle& r) { ring_ = r; }
  void setTalking(bool t) { talking_ = t; }
  // Real lip sync: while `active`, the mouth follows `level` (0..1, loudness of the audio being played) instead of the timer flap.
  void setLip(bool active, float level) { lipActive_ = active; lipTarget_ = level < 0.f ? 0.f : (level > 1.f ? 1.f : level); }
  // Listening: `level` (0..1, what the microphone hears) widens the eyes, lifts the brows and quickens the ring. 0 = no effect.
  void setAttention(float level) { attnTarget_ = level < 0.f ? 0.f : (level > 1.f ? 1.f : level); }
  void setGaze(float x, float y, float drift) { gazeTx_ = x; gazeTy_ = y; drift_ = drift; }
  // Previews: freeze talk/blink/drift/ring phase for deterministic frames.
  void freezeForPreview(float talkFactor, float lip = -1.f) { frozen_ = true; frozenTalk_ = talkFactor; frozenLip_ = lip; }   // lip >= 0 previews the audio-driven mouth at that level

  void update(uint32_t nowMs) {
    if (!started_) { last_ = nowMs; started_ = true; scheduleBlink(nowMs); }
    float dt = (float)(nowMs - last_);
    last_ = nowMs;
    if (dt > 100.f) dt = 100.f;
    float ts = nowMs / 1000.f;

    smooth(cur_.curve, target_.curve, timing_.mouthTau, dt);
    smooth(cur_.width, target_.width, timing_.mouthTau, dt);
    smooth(cur_.open, target_.open, timing_.mouthTau, dt);
    smooth(cur_.corner, target_.corner, timing_.mouthTau, dt);
    smooth(cur_.cheek, target_.cheek, timing_.cheekTau, dt);
    smooth(cur_.eye, target_.eye, timing_.eyeTau, dt);
    smooth(cur_.brow, target_.brow, timing_.browTau, dt);
    smooth(cur_.tilt, target_.tilt, timing_.tiltTau, dt);
    smooth(cur_.lid, target_.lid, timing_.eyeTau, dt);

    // gaze with a small idle drift
    float gx = gazeTx_ + (frozen_ ? 0.f : drift_ * sinf(ts * 0.5f));
    float gy = gazeTy_ + (frozen_ ? 0.f : drift_ * 0.6f * sinf(ts * 0.37f));
    smooth(gaze_x_, gx, timing_.gazeTau, dt);
    smooth(gaze_y_, gy, timing_.gazeTau, dt);

    // ring
    smooth(attn_, attnTarget_, attnTarget_ > attn_ ? 60.f : 220.f, dt);
    ringRot_ = frozen_ ? 0.f : fmodf(ringRot_ + ring_.speed * (1.f + 2.2f * attn_) * dt / 1000.f, 360.f);
    float gt = ring_.glow + 0.35f * attn_ + (frozen_ ? 0.f : ring_.pulseAmp * sinf(6.2831853f * ring_.pulseHz * ts));
    smooth(glow_, gt, timing_.ringGlowTau, dt);

    // talking blend
    smooth(talkAmt_, talking_ ? 1.f : 0.f, timing_.talkBlendTau, dt);
    smooth(lip_, lipTarget_, lipTarget_ > lip_ ? 30.f : 70.f, dt);   // quick to open, a little slower to close
    smooth(lipBlend_, lipActive_ ? 1.f : 0.f, 80.f, dt);

    updateBlink(nowMs);
    compose(ts);
  }

  const FaceRenderState& state() const { return out_; }

 private:
  static void smooth(float& cur, float tgt, float tau, float dt) {
    cur += (tgt - cur) * (1.f - expf(-dt / (tau > 1.f ? tau : 1.f)));
  }
  uint32_t rnd() { rng_ ^= rng_ << 13; rng_ ^= rng_ >> 17; rng_ ^= rng_ << 5; return rng_; }
  void scheduleBlink(uint32_t now) {
    nextBlink_ = now + timing_.blinkMinMs + rnd() % (timing_.blinkMaxMs - timing_.blinkMinMs + 1);
  }
  void updateBlink(uint32_t now) {
    if (frozen_) { blinkF_ = 1.f; return; }
    bool eyesOpenEnough = cur_.eye >= timing_.blinkMinEye && target_.eye >= timing_.blinkMinEye;
    if (!blinking_) {
      blinkF_ = 1.f;
      if (now >= nextBlink_) {
        if (eyesOpenEnough) { blinking_ = true; blinkStart_ = now; }
        else scheduleBlink(now);
      }
      return;
    }
    float t = (float)(now - blinkStart_), c = timing_.blinkCloseMs, h = timing_.blinkHoldMs, o = timing_.blinkOpenMs;
    if (t < c) blinkF_ = 1.f - ease(t / c);
    else if (t < c + h) blinkF_ = 0.f;
    else if (t < c + h + o) blinkF_ = ease((t - c - h) / o);
    else { blinkF_ = 1.f; blinking_ = false; scheduleBlink(now); }
  }
  static float ease(float x) { x = x < 0 ? 0 : (x > 1 ? 1 : x); return x * x * (3 - 2 * x); }

  void compose(float ts) {
    out_.e = cur_;
    out_.e.eye = cur_.eye * blinkF_;
    float talk = 0.f;
    if (frozen_) talk = frozenTalk_;
    else {
      float env = fabsf(sinf(6.2831853f * timing_.talkHz * ts)) * (0.6f + 0.4f * sinf(6.2831853f * 1.1f * ts + 1.f));
      talk = timing_.talkMin + (1.f - timing_.talkMin) * env;
    }
    float blend = frozen_ ? (frozenTalk_ >= 0 ? 1.f : 0.f) : talkAmt_;
    out_.e.open = cur_.open * (1.f + (talk - 1.f) * blend);
    if (frozen_ ? frozenLip_ >= 0.f : lipBlend_ > 0.001f) {   // audio-driven mouth, on top of ANY expression
      const float lipv = frozen_ ? frozenLip_ : lip_, lb = frozen_ ? 1.f : lipBlend_;
      // The expression keeps its own character (smile curve, corners, cheeks); talking only drives how far the mouth opens and
      // makes sure a closed or narrow mouth is wide enough to look like speech, not a pout.
      float base = cur_.open < kTalkMinOpen ? kTalkMinOpen : (cur_.open > kTalkMaxOpen ? kTalkMaxOpen : cur_.open);
      float lipOpen = base * (0.06f + 1.25f * lipv);
      out_.e.open = out_.e.open * (1.f - lb) + lipOpen * lb;
      float w = cur_.width;
      float wTalk = w < kTalkNarrow ? w * 1.3f + 6.f : (w < kTalkMinWidth ? kTalkMinWidth : w);
      out_.e.width = (w * (1.f - lb) + wTalk * lb) * (1.f + 0.06f * lipv * lb);
    }
    if (attn_ > 0.003f) {                    // attentive: eyes a touch wider, brows up while it hears you
      out_.e.eye *= 1.f + 0.12f * attn_;
      out_.e.brow += 7.f * attn_;
    }
    out_.gazeX = gaze_x_;
    out_.gazeY = gaze_y_;
    out_.ringRotation = ringRot_;
    out_.ringGlow = glow_;
    out_.ringComet = ring_.comet;
  }

  // talking mouth limits (reference units): minimum/maximum opening used for the lip-sync swing, minimum width of a talking mouth
  static constexpr float kTalkMinOpen = 18.f, kTalkMaxOpen = 24.f, kTalkMinWidth = 56.f, kTalkNarrow = 36.f;
  FaceTiming timing_;
  ExpressionParams cur_{}, target_{};
  ExprId id_ = ExprId::Idle;
  RingStyle ring_{18, 0.85f, 0.25f, 0.10f, false};
  FaceRenderState out_{};
  uint32_t rng_ = 0x9E3779B9u, last_ = 0, nextBlink_ = 3000, blinkStart_ = 0;
  bool started_ = false, blinking_ = false, talking_ = false, frozen_ = false;
  float blinkF_ = 1.f, frozenTalk_ = 1.f, talkAmt_ = 0.f, frozenLip_ = -1.f;
  bool lipActive_ = false;
  float lip_ = 0.f, lipTarget_ = 0.f, lipBlend_ = 0.f, attn_ = 0.f, attnTarget_ = 0.f;
  float gazeTx_ = 0, gazeTy_ = 0, drift_ = 0, gaze_x_ = 0, gaze_y_ = 0;
  float ringRot_ = 0, glow_ = 0.85f;
};

}  // namespace sunday
