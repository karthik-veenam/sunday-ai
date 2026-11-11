// Procedurally synthesised pet-mode sounds (no audio files): boops, chirps, coos, a purr, a giggle, a contented "aww".
// Mono 16-bit at 22.05 kHz, generated once at boot into PSRAM. No Arduino/M5 dependency, so the desktop tests can render them to WAV.
#pragma once
#include <stddef.h>
#include <stdint.h>
#include "face/PetEngine.h"

namespace petsound {

constexpr uint32_t kRate = 22050;
struct Clip { const int16_t* pcm; size_t samples; };

bool init();                                            // idempotent; false if out of memory
Clip get(sunday::PetSound id, uint8_t variant = 0);     // {nullptr,0} for None / unknown
int variants(sunday::PetSound id);

}  // namespace petsound
