// Operational states -> face behaviour. A *state* (what Sunday is doing) is separate from an
// *expression* (a face preset): each state selects a default expression, a ring style and gaze.
#pragma once
#include "FaceController.h"

namespace sunday {

enum class State : uint8_t { Idle, Wake, Listening, Thinking, Speaking, Sleeping };
static constexpr int kStateCount = 6;
static constexpr const char* kStateNames[kStateCount] = {"idle", "wake", "listening", "thinking", "speaking", "sleeping"};

struct StateBehavior {
  ExprId expression;
  RingStyle ring;
  bool talking;
  float gazeX, gazeY, drift;  // reference units
};

static constexpr StateBehavior kStateBehavior[kStateCount] = {
    /* idle      */ {ExprId::Idle,      {18, 0.85f, 0.25f, 0.10f, false}, false, 0, 0, 1.5f},
    /* wake      */ {ExprId::Surprised, {160, 1.3f, 0.0f, 0.0f, false},   false, 0, 0, 0.0f},
    /* listening */ {ExprId::Listening, {50, 1.15f, 0.8f, 0.30f, false}, false, 0, -2, 0.4f},
    /* thinking  */ {ExprId::Thinking,  {220, 0.9f, 0.0f, 0.0f, true},    false, 9, -6, 1.0f},
    /* speaking  */ {ExprId::Speaking,  {60, 1.0f, 1.43f, 0.18f, false},  true,  0, 0, 0.5f},
    /* sleeping  */ {ExprId::Sleeping,  {4, 0.30f, 0.13f, 0.08f, false},  false, 0, 0, 0.0f},
};

static constexpr uint32_t kWakeDurationMs = 1500;  // wake is transient, then returns to idle

}  // namespace sunday
