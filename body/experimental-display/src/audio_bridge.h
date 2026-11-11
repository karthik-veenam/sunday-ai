// Stack-chan <-> Sunday gateway audio client (microphone out, speaker in, face-state in).
//
// Protocol: see ~/sunday-audio/README.md on the Pi (gateway/gateway.py). WebSocket client, HMAC challenge-response.
// Safety properties:
//   * the microphone is NOT started until the gateway has authenticated and sent "welcome" -> in `airhug` mode (gateway
//     stopped) no audio is ever captured or sent;
//   * mic and speaker share one I2S port, so the bridge is strictly half-duplex and hands the port over cleanly;
//   * everything runs on its own task (core 0); the face renderer on core 1 is never blocked by audio.
#pragma once
#include <Arduino.h>

namespace audiobridge {

struct Config {
  const char* host;        // mDNS name of the Pi, e.g. "djpi.local"
  const char* fallbackIp;  // used if mDNS fails, e.g. "192.168.88.108" ("" = none)
  uint16_t port;           // gateway device port (8770)
  const char* key;         // HMAC key: the ASCII text stored in the gateway's secrets/device.key
  const char* fwTag;       // reported to the gateway
};

struct Stats {
  bool enabled, connected;
  const char* mode;  // "off" | "mic" | "speaker"
  uint32_t micFrames, micDropped, spkBytes, spkDropped, spkUnderruns, plays, localPlays, reconnects, authFailures, modeSwitches;
  uint32_t lastSwitchMs, maxSwitchMs;
  float micRms;  // 0..32767, smoothed
  int micPeak;   // peak of the last second
  float micGain;
  uint8_t volume;
  char server[48];
};

using StateCb = void (*)(const char* name);  // called from the audio task: the callee must hand off, not touch the face directly

using CmdCb = void (*)(const char* name, const char* value);   // {"type":"cmd","name":..,"value":..} from the gateway (audio task: hand off, don't touch the face)
using AliveCb = void (*)(uint32_t epoch, uint32_t seq, bool enabled, const char* expression, uint32_t ttl, const char* idleExpression, uint32_t idleTtl, uint8_t idleFlags);
void setAliveHandler(AliveCb cb);
void setCommandHandler(CmdCb cb);

void begin(const Config& cfg, StateCb onState);
Stats stats();
void setMicGain(float gain);  // software gain applied to the streamed audio (default 1.0)
void setVolume(uint8_t v);    // 0..255

// Sounds generated on the device itself (pet mode). Plays on the speaker without the gateway; the microphone is paused meanwhile
// (half-duplex hardware) and resumes by itself. `pcm` must stay valid (static buffers). Returns false if it cannot play right now:
// gateway speech has priority, and a clip already playing is only cut when `interrupt` is true.
bool playLocal(const int16_t* pcm, size_t samples, uint32_t rate, uint8_t volume, bool interrupt = false);
void cancelIdleSound();                 // audio-task cancellation, never gateway speech
bool active();                       // authenticated with the gateway (the face then takes its mouth/ears from real audio, not timers)
bool speechPlaying();                // gateway speech (not a chime) is being played: the mouth should move, whatever the expression
float lipLevel();                    // 0..1 loudness of the speech being played RIGHT NOW (0 as soon as playback stops)
float micLevel();                    // 0..1 loudness of what the microphone hears (instant, per 80 ms frame)
bool takeCameraRequest();            // true once per {"type":"camera"} command received from the gateway ("Sunday, click a picture")
void queueEvent(const char* json);   // best-effort device -> gateway text message (dropped if not connected or one is already queued)

// Diagnostics (blocking; they pause streaming, use the hardware directly, then let streaming resume).
// Return false if the hardware could not be acquired. `samples` are 16 kHz mono.
bool diagRecord(int16_t* dst, size_t samples, float gain);
bool diagTone(uint32_t hz, uint32_t ms);

}  // namespace audiobridge
