// Sunday Face v1 for StackChan (CoreS3). Arduino + M5Unified/M5GFX + M5Stack StackChan-BSP.
//
// Two separate concepts:
//   * state      (what Sunday is doing): idle, wake, listening, thinking, speaking, sleeping
//   * expression (a face preset):        Idle, Happy, Grin, Laughing, Thinking, Listening, Speaking, Surprised, Sleepy, Sleeping
// Each state selects a default expression + ring style (src/face/Behavior.h). An expression can also be forced
// over HTTP (override); the next state change clears the override.
//
// Head: calibrated BSP motion, single idle/pet writer; bounded active glances yield to activity/night/manual control.
// Audio (speaker/mics) is not touched.
#include <M5Unified.h>
#include <M5StackChan.h>
#include <WiFi.h>
#include <WebServer.h>
#include <ESPmDNS.h>
#include <Preferences.h>
#include <ArduinoOTA.h>
#include "face/Behavior.h"
#include "face/FaceRenderer.h"
#include "audio_bridge.h"
#include "face/PetEngine.h"
#include "face/PetProximityGate.h"
#include "proximity.h"
#include "pet_sounds.h"
#include "camera.h"
#include "ambient.h"
#include "body_temperature.h"
#include "alive_gate.h"
#include "idle_planner.h"
#include <atomic>
#include <esp_heap_caps.h>

#if __has_include("secrets.h")
#include "secrets.h"
#endif

using namespace sunday;

static Raster raster;
// Two display layouts (classic = original face, immersive = enlarged head). The active one is baked before the
// first frame; the other is baked on a background task so a mode switch needs no pause.
static FaceAssets assets[kDisplayModeCount];
static volatile DisplayMode activeMode = DisplayMode::Classic;
static volatile DisplayMode pendingMode = DisplayMode::Classic;
static int dispW = 0, dispH = 0;
static const char* kPrefsNs = "sunday";  // own NVS namespace: never touches the BSP's "servo" calibration keys
static const char* kPrefsKeyMode = "dmode";
static FaceController face;
static WebServer server(80);

static State state = State::Idle;
static uint32_t stateSince = 0;
static bool exprOverride = false;
static ExprId overrideExpr = ExprId::Idle;

// Face-state messages arrive on the audio task (core 0); they are queued here and applied by the render loop (core 1).
static portMUX_TYPE stateMux = portMUX_INITIALIZER_UNLOCKED;
static char pendingState[16] = "";
static void cancelIdleMotion(bool manual=false);
static void onGatewayState(const char* name) {
  if (strcmp(name,"idle"))cancelIdleMotion();
  portENTER_CRITICAL(&stateMux);
  strlcpy(pendingState, name, sizeof(pendingState));
  portEXIT_CRITICAL(&stateMux);
}
static AliveGate aliveGate;
static IdlePlanner idlePlanner;
static portMUX_TYPE idleMotionMux=portMUX_INITIALIZER_UNLOCKED;
static std::mutex idleHardwareMutex;
static std::atomic<uint32_t> idleGeneration{1},idleManualUntil{0};
static std::atomic<bool> idleManualTakeover{false};
static struct {int yaw=0,pitch=0,speed=200;uint32_t generation=0;bool pending=false;} idleMotionRequest;
static std::atomic<bool> idleSoundRequest{false},idleEligible{false};
static std::atomic<uint32_t> idleSoundRequests{0};
static std::atomic<int> lastIdleSound{0};
static void cancelIdleMotion(bool manual) {
  idleEligible.store(false);idleGeneration.fetch_add(1);
  portENTER_CRITICAL(&idleMotionMux);idleMotionRequest.pending=false;portEXIT_CRITICAL(&idleMotionMux);
  if (manual) {idleManualTakeover.store(true);idleManualUntil.store(millis()+30000);}
}
static void queueIdleMotion(const IdlePlan& plan) {
  portENTER_CRITICAL(&idleMotionMux);
  idleMotionRequest.yaw=plan.yaw;idleMotionRequest.pitch=plan.pitch;idleMotionRequest.speed=plan.speed;
  idleMotionRequest.generation=idleGeneration.load();idleMotionRequest.pending=true;
  portEXIT_CRITICAL(&idleMotionMux);
}

static std::atomic<bool> alivePerformance{false}; // eligibility signal only, no cross-core render writes
static portMUX_TYPE aliveMux=portMUX_INITIALIZER_UNLOCKED;
static struct {uint32_t epoch=0,seq=0,ttl=0; bool enabled=false,pending=false; uint32_t idleTtl=0;uint8_t idleFlags=0; char expression[20]="",idleExpression[20]="";} alivePending;
static void onAlive(uint32_t epoch,uint32_t seq,bool enabled,const char* expression,uint32_t ttl,const char* idleExpression,uint32_t idleTtl,uint8_t idleFlags) {
  portENTER_CRITICAL(&aliveMux);
  alivePending.epoch=epoch;alivePending.seq=seq;alivePending.enabled=enabled;alivePending.ttl=ttl;
  strlcpy(alivePending.expression,expression,sizeof(alivePending.expression));
  strlcpy(alivePending.idleExpression,idleExpression,sizeof(alivePending.idleExpression));alivePending.idleTtl=idleTtl;alivePending.idleFlags=idleFlags;alivePending.pending=true;
  portEXIT_CRITICAL(&aliveMux);
}
static uint32_t rebootAt = 0;

// ---------------------------------------------------------------- pet mode (head touch sensor)
// The touch task (core 0, 50 Hz) senses touch and runs PetEngine; it plays the sounds. The render loop (core 1) applies the result.
static PetEngine petEngine;
static portMUX_TYPE petMux = portMUX_INITIALIZER_UNLOCKED;
static PetOutput petShared;                       // latest engine output (written by the touch task, read by the render loop)
static std::atomic<int> petBurst{0};              // hearts waiting to be spawned
static std::atomic<uint32_t> petTouches{0}, petSounds{0}, petSessions{0};
static volatile uint8_t petZ[3] = {0, 0, 0};      // last raw zones, for /status
static volatile uint8_t petSimZ[3] = {0, 0, 0};   // simulated touch (diag endpoint /pet), so reactions can be tested without hands
static volatile uint32_t petSimUntil = 0;
static volatile uint8_t petSimSwipe = 0;          // 1 = forward, 2 = backward (one-shot)
static uint8_t petVolume = 150;                   // speaker volume for pet sounds (0..255)
static char petLastEvent[24] = "";
// Physical head tilt. Hard cap everywhere: 250 = 25 degrees up from level (pitch unit = 0.1 degree; yaw is never moved).
// Pet motion stays OFF until it is switched on with /servo?pet=1 after the supervised tests.
static constexpr int kServoPitchCap = 550;
static volatile bool petMotion = true;   // validated with real petting: strokes lift the head (stock-style), /servo?pet=0 turns it off

static bool apMode = false;
static uint32_t wifiStart = 0;
static uint32_t frameMs = 33, fps = 0;

// ---------------------------------------------------------------- state / expression
static int findName(const char* const* names, int n, String s) {
  s.trim();
  for (int i = 0; i < n; i++)
    if (s.equalsIgnoreCase(names[i])) return i;
  return -1;
}

static void applyBehavior(State s) {
  const StateBehavior& b = kStateBehavior[(int)s];
  face.setRing(b.ring);
  face.setGaze(b.gazeX, b.gazeY, b.drift);
  face.setTalking(b.talking);
}

static void setState(State s) {
  state = s;
  stateSince = millis();
  if (s!=State::Idle) {cancelIdleMotion();audiobridge::cancelIdleSound();}
  if (!aliveGate.enabled) {exprOverride = false;aliveGate.clearManual();}  // Alive preserves explicit manual intent
  petEngine.cancel();    // a conversation / state change interrupts petting
  applyBehavior(s);
  face.setExpression((ExprId)aliveGate.choose((int)kStateBehavior[(int)s].expression,-1,s==State::Wake || s==State::Listening || s==State::Thinking,false,millis()));
  Serial.printf("state -> %s (expression %s)\n", kStateNames[(int)s], kExpressionNames[(int)face.expression()]);
}

static void releaseExpressionOverride() {      // back under state control (what the current state would show)
  exprOverride = false;
  aliveGate.clearManual();
  applyBehavior(state);
  face.setExpression(kStateBehavior[(int)state].expression);
  Serial.println("expression override released (auto)");
}

static uint32_t lastManualMs = 0;                 // when someone last forced an expression by hand (HA / web page)
static bool nightForcedSleep = false;            // night mode put the face to sleep (so it can be woken again when night mode ends)

static void setExpressionOverride(ExprId e) {
  lastManualMs = millis();
  cancelIdleMotion();
  exprOverride = true;
  overrideExpr = e;
  aliveGate.setManual((int)e);
  face.setExpression(e);
  face.setTalking(e == ExprId::Speaking);  // speaking preset animates its mouth
  Serial.printf("expression override -> %s\n", kExpressionNames[(int)e]);
}

// ---------------------------------------------------------------- body LEDs (subtle mirror of the ring glow)
// 0..1, smoothed loudness of what the microphone hears while Listening (feeds the equalizer bars and the LEDs)
static float listenLevel = 0.f;

// "Speak now" cue: a row of cyan equalizer bars at the bottom of the screen. They breathe gently while Sunday waits and jump with
// your voice. Drawn straight into the frame buffer before it is pushed (no flicker), only while the state is Listening.
static void drawListeningBars(uint16_t* fb, int w, int h, float t, float level) {
  const int n = 7, bw = 7, gap = 5, maxH = 32, minH = 8;
  const int x0 = w / 2 - (n * bw + (n - 1) * gap) / 2, base = h - 8;
  const uint16_t col = (uint16_t)(((90 >> 3) << 11) | ((235 >> 2) << 5) | (255 >> 3));
  const uint16_t hi = (uint16_t)(((200 >> 3) << 11) | ((250 >> 2) << 5) | (255 >> 3));
  for (int i = 0; i < n; i++) {
    float centre = 1.f - 0.45f * fabsf((float)(i - n / 2)) / (n / 2);
    float breathe = 9.f * (0.5f + 0.5f * sinf(t * 2.6f + i * 0.9f));
    float voice = level * (maxH - minH) * (0.6f + 0.4f * sinf(t * 11.f + i * 1.7f));
    int bh = minH + (int)((breathe + voice) * centre);
    if (bh > maxH) bh = maxH;
    int x = x0 + i * (bw + gap);
    for (int yy = 0; yy < bh; yy++) {
      int y = base - yy;
      if (y < 0 || y >= h) continue;
      bool corner = (yy == 0 || yy == bh - 1);                  // rounded ends: trim the corner pixels
      for (int xx = corner ? 1 : 0; xx < (corner ? bw - 1 : bw); xx++) fb[y * w + x + xx] = (yy >= bh - 3) ? hi : col;
    }
  }
}

// ---------------------------------------------------------------- night mode (settings persist in the "sunday" NVS namespace)
static const char* const kLedNames[] = {"Auto", "Off", "Red", "Orange", "Amber", "Green", "Cyan", "Blue", "Purple", "Pink", "White", "Warm white"};
static constexpr int kLedCount = 12;
static const uint8_t kLedRgb[kLedCount][3] = {{0, 0, 0}, {0, 0, 0}, {255, 0, 0}, {255, 110, 0}, {255, 170, 0}, {0, 255, 40},
                                               {0, 220, 255}, {0, 60, 255}, {170, 0, 255}, {255, 40, 160}, {255, 255, 255}, {255, 170, 90}};
static bool nightMode = false;
static uint8_t nightScreen = 1;     // 0 = sleep (screen off), 1 = dim black-and-white face
static uint8_t nightBright = 3;     // backlight 1..30 for the dim face
static uint8_t ledDay = 0;          // index into kLedNames: 0 Auto (state colours), 1 Off, or a fixed colour
static uint8_t ledNight = 1;        // same list; at night Auto counts as Off
static bool petProximityRequired = true;
static uint16_t petProximityThreshold = 200;
static std::atomic<int> petProximityCounts{-1};
static std::atomic<bool> petProximityNear{false};
static PetProximityGate petProximityGate;
static bool petEnabled = true;       // master switch for petting (face reactions, sounds, hearts, head lift)
static bool petAtNight = false;      // allow petting while Night mode is on (default off: a quiet room, no phantom-touch noise)
static constexpr uint8_t kDayBrightness = 180;
static bool screenBlank = false;         // night + Sleep and nothing going on: screen dark
static uint32_t nightWakeUntil = 0;     // the screen stays awake until this time after Sunday last did something

static void applyBrightness() {
  uint8_t b = kDayBrightness;
  if (nightMode) b = screenBlank ? 0 : nightBright;
  M5.Display.setBrightness(b);
}

static void saveNight() {
  Preferences p;
  if (!p.begin(kPrefsNs, false)) return;
  p.putUChar("night", nightMode ? 1 : 0); p.putUChar("nscr", nightScreen); p.putUChar("nbr", nightBright);
  p.putUChar("ledd", ledDay); p.putUChar("ledn", ledNight);
  p.putUChar("pete", petEnabled ? 1 : 0); p.putUChar("petn", petAtNight ? 1 : 0);
  p.putUChar("petprox", petProximityRequired ? 1 : 0); p.putUShort("petprth", petProximityThreshold);
  p.end();
}

static void loadNight() {
  Preferences p;
  if (!p.begin(kPrefsNs, true)) return;
  nightMode = p.getUChar("night", 0) != 0;
  uint8_t v = p.getUChar("nscr", 1); nightScreen = v > 1 ? 1 : v;
  v = p.getUChar("nbr", 3); nightBright = v < 1 ? 1 : (v > 30 ? 30 : v);
  v = p.getUChar("ledd", 0); ledDay = v < kLedCount ? v : 0;
  v = p.getUChar("ledn", 1); ledNight = v < kLedCount ? v : 1;
  petEnabled = p.getUChar("pete", 1) != 0; petAtNight = p.getUChar("petn", 0) != 0;
  petProximityRequired = p.getUChar("petprox", 1) != 0;
  petProximityThreshold = constrain(p.getUShort("petprth", 200), 1, 2047);
  p.end();
}

// black-and-white version of the finished frame (the dim night face)
static void toGray(uint16_t* fb, int n) {
  for (int i = 0; i < n; i++) {
    uint16_t c = fb[i];
    int r = (c >> 11) & 31, g = (c >> 5) & 63, b = c & 31;
    int r8 = (r << 3) | (r >> 2), g8 = (g << 2) | (g >> 4), b8 = (b << 3) | (b >> 2);
    int y = (r8 * 77 + g8 * 150 + b8 * 29) >> 8;
    fb[i] = (uint16_t)(((y >> 3) << 11) | ((y >> 2) << 5) | (y >> 3));
  }
}

static void updateLeds() {
  static uint32_t last = 0;
  if (millis() - last < 80) return;
  last = millis();
  int idx = nightMode ? ledNight : ledDay;
  if (nightMode && idx == 0) idx = 1;                 // at night "Auto" means off
  if (idx == 1) { M5StackChan.showRgbColor(0, 0, 0); return; }
  float g = clampf(face.state().ringGlow, 0.f, 1.2f) * 0.22f;
  if (state == State::Listening) g = 0.30f + 0.18f * sinf(millis() / 1000.f * 2.6f) + 0.55f * listenLevel;   // clear cyan "speak now" glow
  if (nightMode) g = 0.05f + 0.03f * clampf(face.state().ringGlow, 0.f, 1.2f);                              // very dim at night
  float r, gg, b;
  switch (state) {
    case State::Wake:      r = 255; gg = 160; b = 60; break;
    case State::Listening: r = 40;  gg = 210; b = 255; break;
    case State::Thinking:  r = 160; gg = 80;  b = 240; break;
    case State::Speaking:  r = 70;  gg = 130; b = 255; break;
    case State::Sleeping:  r = 20;  gg = 40;  b = 130; break;
    default:               r = 50;  gg = 100; b = 255; break;
  }
  if (idx >= 2) { r = kLedRgb[idx][0]; gg = kLedRgb[idx][1]; b = kLedRgb[idx][2]; }   // a fixed colour chosen in Home Assistant
  M5StackChan.showRgbColor((uint8_t)(r * g), (uint8_t)(gg * g), (uint8_t)(b * g));
}

// ---------------------------------------------------------------- HTTP
static bool authorized() {
#ifdef SUNDAY_TOKEN
  return server.arg("token") == SUNDAY_TOKEN || server.header("X-Token") == SUNDAY_TOKEN;
#else
  return true;
#endif
}

static String ipString() { return apMode ? WiFi.softAPIP().toString() : WiFi.localIP().toString(); }

// ---------------------------------------------------------------- Wi-Fi credentials (own NVS keys; never in the firmware image)
static bool loadWifi(String& ssid, String& pass) {
  Preferences prefs;
  if (!prefs.begin(kPrefsNs, true)) return false;
  ssid = prefs.getString("wssid", "");
  pass = prefs.getString("wpass", "");
  prefs.end();
  return ssid.length() > 0;
}
static bool wifiConfigured() { String a, b; return loadWifi(a, b); }

static String statusJson() {
  char buf[640];
  snprintf(buf, sizeof(buf),
           "{\"state\":\"%s\",\"expression\":\"%s\",\"override\":%s,\"display_mode\":\"%s\",\"display_mode_pending\":\"%s\","
           "\"layouts_ready\":{\"classic\":%s,\"immersive\":%s},\"ip\":\"%s\",\"mode\":\"%s\","
           "\"width\":%d,\"height\":%d,\"fps\":%u,\"frame_ms\":%u,\"heap\":%u,\"psram\":%u,\"psram_free\":%u,\"uptime_s\":%u}",
           kStateNames[(int)state], kExpressionNames[(int)face.expression()], exprOverride ? "true" : "false",
           kDisplayModeNames[(int)activeMode], kDisplayModeNames[(int)pendingMode],
           assets[0].ready.load() ? "true" : "false", assets[1].ready.load() ? "true" : "false", ipString().c_str(),
           apMode ? "ap" : "sta", raster.w, raster.h, (unsigned)fps, (unsigned)frameMs, (unsigned)ESP.getFreeHeap(),
           (unsigned)ESP.getPsramSize(), (unsigned)ESP.getFreePsram(), (unsigned)(millis() / 1000));
  String out(buf);
  out.remove(out.length() - 1);  // reopen the object to append the audio block
  audiobridge::Stats a = audiobridge::stats();
  char ab[640];
  snprintf(ab, sizeof(ab),
           ",\"audio\":{\"enabled\":%s,\"connected\":%s,\"mode\":\"%s\",\"server\":\"%s\",\"mic_frames\":%u,\"mic_dropped\":%u,"
           "\"mic_rms\":%.0f,\"mic_peak\":%d,\"mic_gain\":%.2f,\"volume\":%u,\"spk_bytes\":%u,\"spk_dropped\":%u,\"spk_underruns\":%u,"
           "\"plays\":%u,\"local_plays\":%u,\"reconnects\":%u,\"auth_failures\":%u,\"mode_switches\":%u,\"last_switch_ms\":%u,\"max_switch_ms\":%u},"
           "\"wifi_configured\":%s}",
           a.enabled ? "true" : "false", a.connected ? "true" : "false", a.mode, a.server, (unsigned)a.micFrames, (unsigned)a.micDropped,
           a.micRms, a.micPeak, a.micGain, (unsigned)a.volume, (unsigned)a.spkBytes, (unsigned)a.spkDropped, (unsigned)a.spkUnderruns,
           (unsigned)a.plays, (unsigned)a.localPlays, (unsigned)a.reconnects, (unsigned)a.authFailures, (unsigned)a.modeSwitches, (unsigned)a.lastSwitchMs,
           (unsigned)a.maxSwitchMs, wifiConfigured() ? "true" : "false");
  out += ab;
  PetOutput pp;
  portENTER_CRITICAL(&petMux);
  pp = petShared;
  portEXIT_CRITICAL(&petMux);
  float lux = 0; uint16_t lc0 = 0, lc1 = 0;
  bool lightOk = ambient::read(&lux, &lc0, &lc1);
  char pb[460];
  snprintf(pb, sizeof(pb), ",\"pet\":{\"active\":%s,\"affection\":%.2f,\"expression\":\"%s\",\"event\":\"%s\",\"zones\":[%u,%u,%u],\"touches\":%u,\"sessions\":%u,\"sounds\":%u,\"volume\":%u,\"motion\":%s},\"levels\":{\"lip\":%.2f,\"mic\":%.2f},\"light\":{\"ok\":%s,\"lux\":%.1f,\"ch0\":%u,\"ch1\":%u}}",
           pp.active ? "true" : "false", pp.affection, kExpressionNames[(int)pp.expr], petLastEvent, petZ[0], petZ[1], petZ[2],
           (unsigned)petTouches.load(), (unsigned)petSessions.load(), (unsigned)petSounds.load(), petVolume, petMotion ? "true" : "false", audiobridge::lipLevel(), audiobridge::micLevel(), lightOk ? "true" : "false", lux, (unsigned)lc0, (unsigned)lc1);
  out.remove(out.length() - 1);   // reopen the object
  out += pb;
  { char px[160]; snprintf(px,sizeof(px),",\"proximity\":{\"counts\":%d,\"near\":%s,\"required\":%s,\"threshold\":%u}",petProximityCounts.load(),petProximityNear.load()?"true":"false",petProximityRequired?"true":"false",(unsigned)petProximityThreshold); out.remove(out.length()-1);out+=px;out+="}"; }
  // Internal BMI270 sensor temperature; never present it as room temperature.
  float bodyC = 0;
  const bool tempOk = bodytemperature::read(&bodyC);
  char tc[24] = "null";
  if (tempOk) snprintf(tc, sizeof(tc), "%.2f", bodyC);
  char tb[160];
  snprintf(tb, sizeof(tb), ",\"temperature\":{\"ok\":%s,\"celsius\":%s,\"source\":\"bmi270\",\"kind\":\"internal\"}}", tempOk ? "true" : "false", tc);
  out.remove(out.length() - 1);
  out += tb;
  out.remove(out.length()-1);
  char aliveStatus[560];
  snprintf(aliveStatus,sizeof(aliveStatus),",\"alive\":{\"enabled\":%s,\"manual\":%s,\"performing\":%s,\"idle_lease\":%s,\"resting_expression\":\"%s\",\"idle_flags\":%u,\"idle_behavior\":%d,\"idle_actions_this_hour\":%u,\"next_idle_in_ms\":%u,\"look_phase\":%d,\"target_yaw_deg\":%.1f,\"target_pitch_deg\":%.1f,\"idle_sound_requests\":%u,\"last_idle_sound\":\"%s\"}}",
      aliveGate.enabled?"true":"false",aliveGate.manual?"true":"false",aliveGate.performing(millis())?"true":"false",
      aliveGate.idleValid(millis())?"true":"false",aliveGate.idleHint>=0?kExpressionNames[aliveGate.idleHint]:"",(unsigned)aliveGate.idleFlags,
      idlePlanner.active,idlePlanner.actions,idlePlanner.next && (int32_t)(idlePlanner.next-millis())>0?(unsigned)(idlePlanner.next-millis()):0,idlePlanner.phase,idlePlanner.targetYaw/10.f,idlePlanner.targetPitch/10.f,(unsigned)idleSoundRequests.load(),kPetSoundNames[lastIdleSound.load()]);
  out+=aliveStatus;
  return out;
}

static String requestedName() {
  String name = server.arg("name");
  if (name.isEmpty() && server.method() == HTTP_POST) name = server.arg("plain");
  name.trim();
  return name;
}

static void sendJson(int code, const String& body) { server.send(code, "application/json", body); }

static void handleState() {
  String name = requestedName();
  if (name.isEmpty()) { sendJson(200, statusJson()); return; }
  if (!authorized()) { sendJson(401, "{\"error\":\"unauthorized\"}"); return; }
  int i = findName(kStateNames, kStateCount, name);
  if (i < 0) { sendJson(400, "{\"error\":\"unknown state\"}"); return; }
  if (aliveGate.enabled)aliveGate.hint=-1; // explicit HTTP action cancels decoration
  setState((State)i);
  sendJson(200, statusJson());
}

static void handleExpression() {
  String name = requestedName();
  if (name.isEmpty()) { sendJson(200, statusJson()); return; }
  if (!authorized()) { sendJson(401, "{\"error\":\"unauthorized\"}"); return; }
  int i = findName(kExpressionNames, kExpressionCount, name);
  if (i < 0) { sendJson(400, "{\"error\":\"unknown expression\"}"); return; }
  setExpressionOverride((ExprId)i);
  sendJson(200, statusJson());
}

static void saveDisplayMode(DisplayMode m) {
  Preferences prefs;
  if (!prefs.begin(kPrefsNs, false)) return;
  if (prefs.getUChar(kPrefsKeyMode, 255) != (uint8_t)m) prefs.putUChar(kPrefsKeyMode, (uint8_t)m);  // write only on change
  prefs.end();
}

static void handleDisplayMode() {
  String name = requestedName();
  if (name.isEmpty()) { sendJson(200, statusJson()); return; }
  if (!authorized()) { sendJson(401, "{\"error\":\"unauthorized\"}"); return; }
  int i = findName(kDisplayModeNames, kDisplayModeCount, name);
  if (i < 0) { sendJson(400, "{\"error\":\"unknown display mode\"}"); return; }
  pendingMode = (DisplayMode)i;  // the render loop swaps as soon as that layout is baked (instantly if it already is)
  saveDisplayMode((DisplayMode)i);
  Serial.printf("display mode -> %s\n", kDisplayModeNames[i]);
  sendJson(200, statusJson());
}

// Diagnostics (mic recorder, tone, audio config, Wi-Fi setup over the LAN) need SUNDAY_DIAG_TOKEN. The mic recorder in
// particular must never be open to the network, so without a configured token these are always refused.
static bool diagAuthorized() {
#ifdef SUNDAY_DIAG_TOKEN
  return server.arg("token") == SUNDAY_DIAG_TOKEN || server.header("X-Token") == SUNDAY_DIAG_TOKEN;
#else
  return false;
#endif
}

static void handleWifi() {
  // Provisioning: allowed from the device's own access point (you are physically close and know its password), or with the diag token.
  if (!apMode && !diagAuthorized()) { sendJson(403, "{\"error\":\"Wi-Fi setup is only allowed on the Sunday-Face access point (or with the diag token)\"}"); return; }
  if (server.arg("forget") == "1") {
    Preferences prefs;
    if (prefs.begin(kPrefsNs, false)) { prefs.remove("wssid"); prefs.remove("wpass"); prefs.end(); }
    rebootAt = millis() + 1500;
    sendJson(200, "{\"ok\":true,\"action\":\"forgot Wi-Fi, rebooting into setup mode\"}");
    return;
  }
  String ssid = server.arg("ssid"), pass = server.arg("pass");
  if (ssid.isEmpty()) {
    String cur, p; loadWifi(cur, p);
    sendJson(200, "{\"configured\":" + String(cur.length() ? "true" : "false") + ",\"ssid\":\"" + cur + "\"}");  // never returns the password
    return;
  }
  if (ssid.length() > 32 || pass.length() > 63) { sendJson(400, "{\"error\":\"ssid <=32 / password <=63 chars\"}"); return; }
  Preferences prefs;
  if (!prefs.begin(kPrefsNs, false)) { sendJson(500, "{\"error\":\"nvs\"}"); return; }
  prefs.putString("wssid", ssid);
  prefs.putString("wpass", pass);
  prefs.end();
  rebootAt = millis() + 1500;
  Serial.printf("wifi: saved credentials for '%s'; rebooting\n", ssid.c_str());
  sendJson(200, "{\"ok\":true,\"action\":\"saved; rebooting and joining the network\"}");
}

static void handleMicWav() {
  if (!diagAuthorized()) { sendJson(403, "{\"error\":\"diag token required\"}"); return; }
  uint32_t ms = constrain((int)server.arg("ms").toInt() ? (int)server.arg("ms").toInt() : 3000, 500, 10000);
  float gain = server.hasArg("gain") ? server.arg("gain").toFloat() : 1.0f;
  size_t n = (size_t)ms * 16;
  int16_t* buf = (int16_t*)heap_caps_malloc(n * 2, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
  if (!buf) { sendJson(500, "{\"error\":\"no memory\"}"); return; }
  Serial.printf("diag: recording %u ms\n", (unsigned)ms);
  if (!audiobridge::diagRecord(buf, n, gain > 0 ? gain : 1.0f)) { heap_caps_free(buf); sendJson(500, "{\"error\":\"could not acquire microphone\"}"); return; }
  uint8_t h[44] = {'R','I','F','F',0,0,0,0,'W','A','V','E','f','m','t',' ',16,0,0,0,1,0,1,0,0x80,0x3E,0,0,0,0x7D,0,0,2,0,16,0,'d','a','t','a',0,0,0,0};
  uint32_t dl = n * 2, rl = dl + 36;
  memcpy(h + 4, &rl, 4);
  memcpy(h + 40, &dl, 4);
  server.setContentLength(44 + dl);
  server.send(200, "audio/wav", "");
  server.sendContent((const char*)h, 44);
  for (size_t off = 0; off < dl; off += 4096) server.sendContent((const char*)buf + off, (size_t)min((uint32_t)4096, (uint32_t)(dl - off)));
  heap_caps_free(buf);
}

static void handleTone() {
  if (!diagAuthorized()) { sendJson(403, "{\"error\":\"diag token required\"}"); return; }
  uint32_t hz = constrain((int)server.arg("hz").toInt() ? (int)server.arg("hz").toInt() : 440, 100, 8000);
  uint32_t ms = constrain((int)server.arg("ms").toInt() ? (int)server.arg("ms").toInt() : 800, 100, 5000);
  if (server.hasArg("vol")) audiobridge::setVolume((uint8_t)constrain(server.arg("vol").toInt(), 0, 255));
  bool ok = audiobridge::diagTone(hz, ms);
  sendJson(ok ? 200 : 500, ok ? "{\"ok\":true}" : "{\"error\":\"could not acquire speaker\"}");
}

static void handleAudioConfig() {
  if (!diagAuthorized()) { sendJson(403, "{\"error\":\"diag token required\"}"); return; }
  if (server.hasArg("mic_gain")) audiobridge::setMicGain(server.arg("mic_gain").toFloat());
  if (server.hasArg("volume")) audiobridge::setVolume((uint8_t)constrain(server.arg("volume").toInt(), 0, 255));
  sendJson(200, statusJson());
}

// Pet mode test endpoint (diag token): simulate touches or audition a sound, no hands needed.
//   /pet?event=tap | stroke | hold[&ms=3000] | release | cancel      /pet?sound=boop|chirp|coo|wheee|giggle|purr|aww|happy[&variant=n]    /pet?volume=0..255
static void handlePet() {
  if (!diagAuthorized()) { sendJson(403, "{\"error\":\"diag token required\"}"); return; }
  String ev = server.arg("event"), snd = server.arg("sound");
  if (server.hasArg("volume")) petVolume = (uint8_t)constrain(server.arg("volume").toInt(), 0, 255);
  if (snd.length()) {
    int i = findName(kPetSoundNames, kPetSoundCount, snd);
    if (i <= 0) { sendJson(400, "{\"error\":\"unknown sound\"}"); return; }
    petsound::Clip c = petsound::get((PetSound)i, (uint8_t)server.arg("variant").toInt());
    bool ok = audiobridge::playLocal(c.pcm, c.samples, petsound::kRate, petVolume, true);
    sendJson(ok ? 200 : 409, String("{\"played\":\"") + snd + (ok ? "\"}" : "\",\"error\":\"speaker busy (gateway speech or diagnostics)\"}"));
    return;
  }
  uint32_t now = millis();
  if (ev == "tap") { petSimZ[0] = 0; petSimZ[1] = 3; petSimZ[2] = 0; petSimUntil = now + 130; }
  else if (ev == "hold") {
    uint32_t ms = constrain((int)server.arg("ms").toInt() ? (int)server.arg("ms").toInt() : 3000, 200, 20000);
    petSimZ[0] = 1; petSimZ[1] = 3; petSimZ[2] = 2; petSimUntil = now + ms;
  }
  else if (ev == "stroke") { petSimZ[0] = 0; petSimZ[1] = 2; petSimZ[2] = 0; petSimUntil = now + 300; petSimSwipe = 1; }
  else if (ev == "release") petSimUntil = 0;
  else if (ev == "cancel") petEngine.cancel();
  else if (ev.length()) { sendJson(400, "{\"error\":\"event: tap|stroke|hold|release|cancel\"}"); return; }
  sendJson(200, statusJson());
}

// Supervised servo test (diag token). Only the PITCH servo can be commanded here, capped at 25 degrees up and limited speed.
//   GET /servo                      read the real head angles        POST /servo?pitch=<deg 0..25>[&speed=50..600]   tilt up (0 = level)
//   POST /servo?home=1              back to level                    POST /servo?release=1   let go of the servos (torque off)
//   POST /servo?pet=1|0             allow / forbid pet mode to move the head
static void handleServo() {
  if (!diagAuthorized()) { sendJson(403, "{\"error\":\"diag token required\"}"); return; }
  std::lock_guard<std::mutex> headLock(idleHardwareMutex);
  auto& m = M5StackChan.Motion;
  if (server.hasArg("pitch") || server.hasArg("yaw") || server.hasArg("home") || server.hasArg("release"))cancelIdleMotion(true);
  if (server.hasArg("pet")) petMotion = server.arg("pet").toInt() != 0;
  if (server.hasArg("release")) m.setTorqueEnabled(false);
  if (server.hasArg("home")) m.move(0, 0, 250);
  if (server.hasArg("yaw")) {int a=(int)(server.arg("yaw").toFloat()*10.f);m.moveYaw(constrain(a,-350,350),200);}
  if (server.hasArg("pitch")) {
    int a = (int)(server.arg("pitch").toFloat() * 10.f);
    a = a < 0 ? 0 : (a > kServoPitchCap ? kServoPitchCap : a);
    int sp = server.arg("speed").toInt() ? (int)server.arg("speed").toInt() : 250;
    sp = sp < 50 ? 50 : (sp > 600 ? 600 : sp);
    Serial.printf("servo: pitch -> %.1f deg at speed %d\n", a / 10.f, sp);
    m.movePitch(a, sp);
  }
  char b[200];
  snprintf(b, sizeof(b), "{\"pitch_deg\":%.1f,\"yaw_deg\":%.1f,\"pet_motion\":%s,\"pitch_cap_deg\":%.0f}", m.getCurrentPitchAngle() / 10.f,
           m.getCurrentYawAngle() / 10.f, petMotion ? "true" : "false", kServoPitchCap / 10.f);
  sendJson(200, b);
}

// ---- camera: "Sunday, click a picture" (gateway sends {"type":"camera"}) -> shutter sound, photo on screen for a few seconds
static volatile bool snapRequested = false;
static uint32_t photoUntil = 0;                 // while now < photoUntil the photo is on screen and the face is not drawn
static constexpr uint32_t kPhotoMs = 8000;

static uint32_t liveUntil = 0;                  // live view: the camera stays on and its picture is shown continuously (teaching tool)
static bool liveOn = false;
static uint32_t liveFrames = 0;

static void liveStep(uint32_t now) {
  if (!liveOn) {
    if (!cam::liveBegin()) { Serial.printf("live: camera failed (%s)\n", cam::lastError()); liveUntil = 0; return; }
    liveOn = true; liveFrames = 0;
  }
  const uint16_t* f = cam::liveGrab();
  if (!f) { delay(5); return; }
  M5.Display.pushImage(0, 0, cam::kW, cam::kH, f);
  liveFrames++;
  // overlay: centre crosshair and the head angles (read at ~5 Hz, the servo bus is slow)
  static uint32_t lastPose = 0; static int yawT = 0, pitchT = 0;
  if (now - lastPose > 200) { lastPose = now; yawT = M5StackChan.Motion.getCurrentYawAngle(); pitchT = M5StackChan.Motion.getCurrentPitchAngle(); }
  const int cx = cam::kW / 2, cy = cam::kH / 2;
  M5.Display.drawFastHLine(cx - 14, cy, 28, 0xFFE0);
  M5.Display.drawFastVLine(cx, cy - 14, 28, 0xFFE0);
  M5.Display.drawCircle(cx, cy, 5, 0xFFE0);
  char t[40];
  snprintf(t, sizeof(t), " turn %+.1f  tilt %+.1f ", yawT / 10.f, pitchT / 10.f);
  M5.Display.setTextSize(2);
  M5.Display.setTextColor(0xFFFF, 0x0000);
  M5.Display.setCursor(2, 2);
  M5.Display.print(t);
}

static void doSnap() {
  uint32_t t0 = millis();
  bool ok = cam::capture();
  if (!ok) { Serial.printf("snap: failed (%s)\n", cam::lastError()); return; }
  cam::Clip k = cam::shutterSound();
  if (k.pcm) audiobridge::playLocal(k.pcm, k.samples, 22050, 200, true);
  M5.Display.fillScreen(0xFFFF);                // shutter flash
  delay(60);
  M5.Display.pushImage(0, 0, cam::kW, cam::kH, cam::photo());
  photoUntil = millis() + kPhotoMs;
  Serial.printf("snap: photo #%u shown (%u ms total)\n", (unsigned)cam::photoCount(), (unsigned)(millis() - t0));
}

// Diag token. GET /camera = status; POST /camera?act=snap takes + shows a photo (same as the voice command); GET /photo = last photo as JPEG.
static void handleCamera() {
  if (!diagAuthorized()) { sendJson(403, "{\"error\":\"diag token required\"}"); return; }
  if (server.arg("act") == "snap") snapRequested = true;
  if (server.arg("act") == "live") { int secs = server.arg("secs").toInt(); secs = secs <= 0 ? 300 : (secs > 900 ? 900 : secs); liveUntil = millis() + (uint32_t)secs * 1000; }
  if (server.arg("act") == "stop") liveUntil = 0;
  char b[180];
  snprintf(b, sizeof(b), "{\"photos\":%u,\"showing\":%s,\"live\":%s,\"error\":\"%s\"}", (unsigned)cam::photoCount(), millis() < photoUntil ? "true" : "false", liveUntil && millis() < liveUntil ? "true" : "false", cam::lastError());
  sendJson(200, b);
}

// Diag token. GET /light = the room's brightness from the ambient light sensor.
static void handleLight() {
  if (!diagAuthorized()) { sendJson(403, "{\"error\":\"diag token required\"}"); return; }
  float lux = 0; uint16_t c0 = 0, c1 = 0; char b[120];
  if (ambient::read(&lux, &c0, &c1)) snprintf(b, sizeof(b), "{\"ok\":true,\"lux\":%.1f,\"ch0\":%u,\"ch1\":%u}", lux, (unsigned)c0, (unsigned)c1);
  else snprintf(b, sizeof(b), "{\"ok\":false}");
  sendJson(200, b);
}

static void handlePhoto() {
  if (!diagAuthorized()) { sendJson(403, "{\"error\":\"diag token required\"}"); return; }
  uint8_t* jpg = nullptr; size_t n = 0;
  if (!cam::photoJpeg(&jpg, &n)) { sendJson(404, "{\"error\":\"no photo yet\"}"); return; }
  server.sendHeader("Cache-Control", "no-store");
  server.send_P(200, "image/jpeg", (const char*)jpg, n);
  free(jpg);
}

static void handleExpressions() {  // the presets exactly as compiled in
  String s = "{\"version\":1,\"character\":\"Sunday\",\"expressions\":{";
  for (int i = 0; i < kExpressionCount; i++) {
    const ExpressionParams& e = kExpressions[i];
    char buf[200];
    snprintf(buf, sizeof(buf), "%s\"%s\":{\"curve\":%g,\"width\":%g,\"open\":%g,\"corner\":%g,\"cheek\":%g,\"eye\":%g,\"brow\":%g,\"tilt\":%g,\"lid\":%g}",
             i ? "," : "", kExpressionNames[i], e.curve, e.width, e.open, e.corner, e.cheek, e.eye, e.brow, e.tilt, e.lid);
    s += buf;
  }
  s += "}}";
  sendJson(200, s);
}

static void handleRoot() {
  String h =
      "<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'><title>Sunday</title>"
      "<style>body{font-family:system-ui,sans-serif;background:#050818;color:#e8eeff;margin:0;padding:16px;text-align:center}"
      "h2{margin:.2em 0}h3{margin:1em 0 .3em;color:#8fa3ff;font-weight:500}"
      "button{font-size:1em;margin:4px;padding:.6em 1em;border-radius:10px;border:1px solid #3b4aa0;background:#101a4a;color:#fff}"
      "button:active{background:#3b4aa0}pre{background:#0b1030;padding:8px;border-radius:8px;text-align:left;white-space:pre-wrap;font-size:.8em}"
      "input{padding:.4em;border-radius:6px;border:1px solid #3b4aa0;background:#0b1030;color:#fff}</style>"
      "<h2>Sunday</h2><h3>Light sensor</h3><div id=lx style='font-size:2.6em;font-weight:600'>&mdash; lux</div>"
      "<div id=lr style='opacity:.7;font-size:.9em'>ch0 &mdash; &middot; ch1 &mdash;</div>"
      "<div>token <input id=t size=12 placeholder='(if configured)'></div><h3>Expressions</h3>";
  for (int i = 0; i < kExpressionCount; i++) {
    h += "<button onclick=\"go('/expression','";
    h += kExpressionNames[i];
    h += "')\">";
    h += kExpressionNames[i];
    h += "</button>";
  }
  h += "<h3>Wi-Fi</h3><input id=ws placeholder='network name'> <input id=wp type=password placeholder=password> "
       "<button onclick=\"wifi()\">save &amp; reboot</button>";
  h += "<h3>Display mode</h3>";
  for (int i = 0; i < kDisplayModeCount; i++) {
    h += "<button onclick=\"go('/display-mode','";
    h += kDisplayModeNames[i];
    h += "')\">";
    h += kDisplayModeNames[i];
    h += "</button>";
  }
  h += "<h3>States</h3>";
  for (int i = 0; i < kStateCount; i++) {
    h += "<button onclick=\"go('/state','";
    h += kStateNames[i];
    h += "')\">";
    h += kStateNames[i];
    h += "</button>";
  }
  h +=
      "<pre id=o>-</pre><script>"
      "async function tick(){try{const j=await(await fetch('/status')).json();const l=j.light||{};"
      "document.getElementById('lx').textContent=l.ok?l.lux.toFixed(1)+' lux':'sensor unavailable';"
      "document.getElementById('lr').textContent=l.ok?'ch0 '+l.ch0+' \u00b7 ch1 '+l.ch1:'';}catch(e){}}setInterval(tick,1000);tick();"
      "const t=document.getElementById('t');t.value=localStorage.getItem('tok')||'';"
      "async function wifi(){const b=new URLSearchParams({ssid:document.getElementById('ws').value,pass:document.getElementById('wp').value});"
      "const r=await fetch('/wifi',{method:'POST',body:b});document.getElementById('o').textContent=r.status+' '+await r.text();}"
      "async function go(p,n){localStorage.setItem('tok',t.value);"
      "const r=await fetch(p+'?name='+n+(t.value?'&token='+encodeURIComponent(t.value):''),{method:'POST'});"
      "document.getElementById('o').textContent=r.status+' '+await r.text();}"
      "go('/status','');</script>";
  server.send(200, "text/html", h);
}

static void startAp() {
  apMode = true;
  if (WiFi.getMode() != WIFI_OFF) WiFi.disconnect(true);  // avoids ESP_ERR_WIFI_NOT_INIT when STA never started
  WiFi.mode(WIFI_AP);
  WiFi.softAP("Sunday-Face", "sunday-setup");
  Serial.printf("AP mode: Sunday-Face @ %s\n", WiFi.softAPIP().toString().c_str());
}

static void startNetwork() {
  wifiStart = millis();
  String ssid, pass;
  bool have = loadWifi(ssid, pass);   // credentials entered on the setup page (stored in NVS) take priority
#ifdef SUNDAY_WIFI_SSID
  if (!have) { ssid = SUNDAY_WIFI_SSID; pass = SUNDAY_WIFI_PASS; have = true; }
#endif
  if (have) {
    WiFi.mode(WIFI_STA);
    WiFi.setHostname("sunday-face");
    WiFi.begin(ssid.c_str(), pass.c_str());
    Serial.printf("wifi: joining '%s'\n", ssid.c_str());
  } else {
    startAp();
  }
  server.on("/", handleRoot);
  server.on("/status", HTTP_GET, []() { sendJson(200, statusJson()); });
  server.on("/state", HTTP_ANY, handleState);
  server.on("/expression", HTTP_ANY, handleExpression);
  server.on("/display-mode", HTTP_ANY, handleDisplayMode);
  server.on("/expressions", HTTP_GET, handleExpressions);
  server.on("/wifi", HTTP_ANY, handleWifi);
  server.on("/audio/mic.wav", HTTP_GET, handleMicWav);
  server.on("/audio/tone", HTTP_ANY, handleTone);
  server.on("/audio/config", HTTP_ANY, handleAudioConfig);
  server.on("/pet", HTTP_ANY, handlePet);
  server.on("/servo", HTTP_ANY, handleServo);
  server.on("/camera", HTTP_ANY, handleCamera);
  server.on("/photo", HTTP_GET, handlePhoto);
  server.on("/light", HTTP_GET, handleLight);
  const char* hdrs[] = {"X-Token"};
  server.collectHeaders(hdrs, 1);
  server.begin();
}

static void serviceNetwork() {
  static bool mdnsUp = false;
  if (!apMode && WiFi.status() != WL_CONNECTED && millis() - wifiStart > 20000) startAp();
  if (!mdnsUp && (apMode || WiFi.status() == WL_CONNECTED)) {
    mdnsUp = MDNS.begin("sunday-face");
    if (mdnsUp) MDNS.addService("http", "tcp", 80);
    Serial.printf("net up: %s\n", statusJson().c_str());
  }
  server.handleClient();
}

// ---------------------------------------------------------------- setup / loop
static void fatal(const char* msg) {
  Serial.printf("FATAL: %s\n", msg);
  M5.Display.fillScreen(0x0000);
  M5.Display.setTextColor(0xF800);
  M5.Display.setTextSize(2);
  M5.Display.setCursor(8, 8);
  M5.Display.print(msg);
  while (true) delay(1000);
}

// ---------------------------------------------------------------- commands from Home Assistant (via the gateway)
static portMUX_TYPE cmdMux = portMUX_INITIALIZER_UNLOCKED;
static struct { char name[24]; char value[32]; } cmdQ[4];
static int cmdCount = 0;
static uint32_t telemetryAt = 0;                // next status report to the gateway (0 = now)

static void onBridgeCmd(const char* name, const char* value) {   // audio task: just queue
  portENTER_CRITICAL(&cmdMux);
  if (cmdCount < 4) { strlcpy(cmdQ[cmdCount].name, name, sizeof(cmdQ[0].name)); strlcpy(cmdQ[cmdCount].value, value, sizeof(cmdQ[0].value)); cmdCount++; }
  portEXIT_CRITICAL(&cmdMux);
}

static bool truthy(const char* v) { return v[0] == '1' || !strcasecmp(v, "on") || !strcasecmp(v, "true"); }

static void runBodyCmd(const char* n, const char* v) {            // loop thread
  Serial.printf("cmd: %s = %s\n", n, v);
  if (!strcmp(n, "expression")) {
    if (!strcasecmp(v, "auto")) releaseExpressionOverride();
    else { int i = findName(kExpressionNames, kExpressionCount, String(v)); if (i >= 0) setExpressionOverride((ExprId)i); }
  } else if (!strcmp(n, "state")) {
    int i = findName(kStateNames, kStateCount, String(v)); if (i >= 0) {if (aliveGate.enabled)aliveGate.hint=-1;setState((State)i);}
  } else if (!strcmp(n, "display")) {
    int i = findName(kDisplayModeNames, kDisplayModeCount, String(v)); if (i >= 0) { pendingMode = (DisplayMode)i; saveDisplayMode((DisplayMode)i); }
  } else if (!strcmp(n, "pet_motion")) {
    petMotion = truthy(v);if (!petMotion)cancelIdleMotion();
  } else if (!strcmp(n, "volume")) {
    audiobridge::setVolume((uint8_t)constrain(atoi(v), 0, 255));
  } else if (!strcmp(n, "live")) {
    liveUntil = truthy(v) ? millis() + 900000UL : 0;                 // 15 minutes, then the face comes back by itself
  } else if (!strcmp(n, "snap")) {
    snapRequested = true;
  } else if (!strcmp(n, "pet_proximity")) {
    petProximityRequired = truthy(v); petEngine.cancel(); saveNight();
  } else if (!strcmp(n, "pet_proximity_threshold")) {
    petProximityThreshold = constrain(atoi(v), 1, 2047); petEngine.cancel(); saveNight();
  } else if (!strcmp(n, "pet_enable")) {
    petEnabled = truthy(v); if (!petEnabled) petEngine.cancel(); saveNight();
  } else if (!strcmp(n, "pet_night")) {
    petAtNight = truthy(v); if (nightMode && !petAtNight) petEngine.cancel(); saveNight();
  } else if (!strcmp(n, "night")) {
    nightMode = truthy(v); if (nightMode && !petAtNight) petEngine.cancel(); applyBrightness(); saveNight();
  } else if (!strcmp(n, "night_screen")) {
    nightScreen = !strcasecmp(v, "sleep") ? 0 : 1; applyBrightness(); saveNight();
  } else if (!strcmp(n, "night_brightness")) {
    nightBright = (uint8_t)constrain(atoi(v), 1, 30); applyBrightness(); saveNight();
  } else if (!strcmp(n, "led_day")) {
    int i = findName(kLedNames, kLedCount, String(v)); if (i >= 0) { ledDay = (uint8_t)i; saveNight(); }
  } else if (!strcmp(n, "led_night")) {
    int i = findName(kLedNames, kLedCount, String(v)); if (i >= 0) { ledNight = (uint8_t)i; saveNight(); }
  }
  telemetryAt = 0;                              // report the new state right away
}

static void sendTelemetry(uint32_t now) {       // compact status for the gateway -> Home Assistant (event buffer is 200 bytes)
  float lux = -1; uint16_t c0, c1;
  if (!ambient::read(&lux, &c0, &c1)) lux = -1;
  char m[480];
  snprintf(m, sizeof(m), "{\"type\":\"telemetry\",\"expr\":\"%s\",\"st\":\"%s\",\"ov\":%d,\"disp\":\"%s\",\"pet\":%d,\"vol\":%u,\"lux\":%.1f,\"rssi\":%d,\"up\":%u,\"live\":%d,\"n\":%d,\"ns\":%d,\"nb\":%u,\"ld\":\"%s\",\"ln\":\"%s\",\"pe\":%d,\"pn\":%d,\"px\":%d,\"pxn\":%d,\"pxg\":%d,\"pxt\":%u}",
           kExpressionNames[(int)face.expression()], kStateNames[(int)state], exprOverride ? 1 : 0, kDisplayModeNames[(int)activeMode], petMotion ? 1 : 0,
           (unsigned)audiobridge::stats().volume, lux, (int)WiFi.RSSI(), (unsigned)(now / 1000), liveUntil && now < liveUntil ? 1 : 0,
           nightMode ? 1 : 0, (int)nightScreen, (unsigned)nightBright, kLedNames[ledDay], kLedNames[ledNight], petEnabled ? 1 : 0, petAtNight ? 1 : 0, petProximityCounts.load(), petProximityNear.load() ? 1 : 0, petProximityRequired ? 1 : 0, (unsigned)petProximityThreshold);
  audiobridge::queueEvent(m);
}

// ---------------------------------------------------------------- touch task: 50 Hz sensing + PetEngine + sounds
static void touchTask(void*) {
  for (;;) {
    uint32_t now = millis();
    if (cam::busBorrowed()) { vTaskDelay(pdMS_TO_TICKS(20)); continue; }   // the camera has the I2C bus for the moment of a photo
    static uint32_t proximityAt=0;
    if ((uint32_t)(now-proximityAt)>=50) {
      proximityAt=now; uint16_t counts=0; bool ok=proximity::read(&counts);
      petProximityCounts.store(ok ? counts : -1);
      petProximityGate.observe(now,ok,counts,petProximityThreshold);
    }
    petProximityNear.store(petProximityGate.permits(now));
    auto& ts = M5StackChan.TouchSensor;
    ts.update();                                       // I2C read of the Si12T (thread-safe through M5Unified's bus lock)
    PetInput in;
    const auto& z = ts.getIntensities();
    for (int i = 0; i < 3; i++) in.z[i] = z[i];
    in.swipeForward = ts.wasSwipedForward();
    in.swipeBackward = ts.wasSwipedBackward();
    if (now < petSimUntil) for (int i = 0; i < 3; i++) if (petSimZ[i] > in.z[i]) in.z[i] = petSimZ[i];
    if (petSimSwipe) { in.swipeForward |= petSimSwipe == 1; in.swipeBackward |= petSimSwipe == 2; petSimSwipe = 0; }
    static uint8_t lastAny = 0;
    uint8_t any = (in.z[0] | in.z[1] | in.z[2]) != 0;
    if (any && !lastAny) petTouches++;
    lastAny = any;
    for (int i = 0; i < 3; i++) petZ[i] = in.z[i];

    // Petting only while Sunday is idle or asleep: never in the middle of a conversation, or while an expression is forced.
    State st = *(volatile State*)&state;
    bool allowed = (st == State::Idle || st == State::Sleeping) && !*(volatile bool*)&exprOverride;
    allowed = allowed && !alivePerformance.load() && petEnabled && (!nightMode || petAtNight);   // petting off (master switch) or Night mode without 'pet at night': ignore the sensor entirely
    bool petConfirmed = !petProximityRequired || petProximityNear.load();
    PetOutput o = petEngine.tick(now, in, allowed && petConfirmed);

    // One physical head writer for idle and petting; manual diagnostics share its lock.
    // The latest bounded pose is consumed once, never reissued at frame rate.
    std::unique_lock<std::mutex> headLock(idleHardwareMutex);
    static bool idleOwnsYaw=false,idleOwnsPitch=false;
    decltype(idleMotionRequest) request;
    portENTER_CRITICAL(&idleMotionMux);request=idleMotionRequest;idleMotionRequest.pending=false;portEXIT_CRITICAL(&idleMotionMux);
    if (idleManualTakeover.exchange(false)) {idleOwnsYaw=idleOwnsPitch=false;request.pending=false;}
    uint32_t manualUntil=idleManualUntil.load();
    bool manualHead=manualUntil && (int32_t)(now-manualUntil)<0;
    bool canIdle=idleEligible.load() && allowed && !o.active && petMotion && !manualHead;
    auto& motion=M5StackChan.Motion;
    bool petTakesIdlePitch=o.active && idleOwnsPitch;
    if (!canIdle && (idleOwnsYaw || idleOwnsPitch)) {
      if (!petMotion) {motion.stop();motion.setTorqueEnabled(false);}
      else {
        if (idleOwnsYaw)motion.moveYaw(0,190);
        if (idleOwnsPitch && !o.active && !manualHead)motion.movePitch(0,190);
      }
      idleOwnsYaw=idleOwnsPitch=false;
    }
    if (canIdle && request.pending && request.generation==idleGeneration.load()) {
      // SDK applies calibrated servo limits too. Never change NVS zero positions.
      motion.move(constrain(request.yaw,-350,350),constrain(request.pitch,0,220),constrain(request.speed,150,300));
      idleOwnsYaw=request.yaw!=0;idleOwnsPitch=request.pitch!=0;
    }
    headLock.unlock();
    if (idleSoundRequest.exchange(false) && idleEligible.load() && allowed && !o.active && !nightMode) {
      PetSound sound=(esp_random()%3)==0?PetSound::Chirp:PetSound::Boop;
      petsound::Clip c=petsound::get(sound,esp_random()%petsound::variants(sound));
      uint8_t volume=audiobridge::stats().volume;volume=volume>55?55:volume;
      if (volume && audiobridge::playLocal(c.pcm,c.samples,petsound::kRate,volume,false)) {
        idleSoundRequests++;lastIdleSound.store((int)sound);
      }
    }
    if (o.sound != PetSound::None) {
      petsound::Clip c = petsound::get(o.sound, o.soundVariant);
      if (audiobridge::playLocal(c.pcm, c.samples, petsound::kRate, petVolume, o.soundInterrupts)) petSounds++;
    }
    if (o.heartBurst) petBurst += o.heartBurst;
    if (o.started) petSessions++;
    if (o.event[0]) {
      strlcpy(petLastEvent, o.event, sizeof(petLastEvent));
      if (!strcmp(o.event, "pet_start") || !strcmp(o.event, "pet_end") || !strcmp(o.event, "giggle") || !strcmp(o.event, "stroke")) {
        char m[110];
        snprintf(m, sizeof(m), "{\"type\":\"touch\",\"event\":\"%s\",\"affection\":%.2f,\"z\":\"%u%u%u\"}", o.event, o.affection, (unsigned)in.z[0], (unsigned)in.z[1], (unsigned)in.z[2]);
        audiobridge::queueEvent(m);                    // lets the Pi know (logged by the gateway; the brain can use it later)
      }
    }
    // ---- physical head, modelled on the stock firmware's HeadPetModifier: ONE deliberate move per stroke (never continuous
    // re-aiming), only when the head is not already moving; hold that pose while petting, level again ~3 s after the hand leaves.
    // Pitch only (yaw is never commanded).
    {
      std::lock_guard<std::mutex> petHeadLock(idleHardwareMutex);
      static bool headRaised = false;
      static uint32_t lastActiveAt = 0;
      if (petMotion && o.active) lastActiveAt = now;
      if (!manualHead && petMotion && !strcmp(o.event, "stroke") && (petTakesIdlePitch || !M5StackChan.Motion.isPitchMoving())) {
        int big = (esp_random() % 3) == 2;                                      // stock: 'look up' (15-25) or 'big happy' (25-40)
        int tgt = big ? 250 + (int)(esp_random() % 151) : 150 + (int)(esp_random() % 101);   // 0.1 degree units
        int speed = 300 + (int)(esp_random() % 201);                            // 300..500, as stock
        tgt = tgt > kServoPitchCap ? kServoPitchCap : tgt;
        M5StackChan.Motion.movePitch(tgt, speed);
        headRaised = true;
      } else if (!manualHead && headRaised && (!petMotion || (!o.active && now - lastActiveAt >= 1000))) {   // petting over: back to level gently (stock: speed 200)
        M5StackChan.Motion.movePitch(0, 200);
        headRaised = false;
      }
    }
    portENTER_CRITICAL(&petMux);
    petShared = o;
    portEXIT_CRITICAL(&petMux);
    vTaskDelay(pdMS_TO_TICKS(20));
  }
}

// Floating hearts (render side): a handful of pink hearts drifting up while it is being petted.
struct HeartSim { Heart h[kMaxHearts]; float vx[kMaxHearts], vy[kMaxHearts], age[kMaxHearts], life[kMaxHearts], phase[kMaxHearts]; int n = 0; float acc = 0; };
static HeartSim hs;
static uint32_t heartRng = 0xBADA55u;
static float frand() { heartRng ^= heartRng << 13; heartRng ^= heartRng >> 17; heartRng ^= heartRng << 5; return (heartRng & 0xFFFF) / 65535.f; }

static void stepHearts(float dt, float rate, int burst) {
  hs.acc += rate * dt + burst;
  while (hs.acc >= 1.f) {
    hs.acc -= 1.f;
    if (hs.n >= kMaxHearts) break;
    int i = hs.n++;
    float sc = dispH / 240.f;
    bool left = frand() < 0.5f;                              // hearts rise along the sides, never across the eyes
    float xr = left ? 0.05f + 0.11f * frand() : 0.84f + 0.11f * frand();
    hs.h[i] = Heart{dispW * xr, dispH * (0.50f + 0.22f * frand()), (7.f + 6.f * frand()) * sc, 0.f};
    hs.vx[i] = (left ? -1.f : 1.f) * (2.f + 6.f * frand()) * sc;
    hs.vy[i] = -(40.f + 26.f * frand()) * sc;
    hs.age[i] = 0; hs.life[i] = 1.4f + 0.8f * frand(); hs.phase[i] = frand() * 6.28f;
  }
  for (int i = 0; i < hs.n;) {
    hs.age[i] += dt;
    if (hs.age[i] >= hs.life[i]) {                          // retire: swap with the last one
      int l = --hs.n;
      hs.h[i] = hs.h[l]; hs.vx[i] = hs.vx[l]; hs.vy[i] = hs.vy[l]; hs.age[i] = hs.age[l]; hs.life[i] = hs.life[l]; hs.phase[i] = hs.phase[l];
      continue;
    }
    float u = hs.age[i] / hs.life[i];
    hs.h[i].x += (hs.vx[i] + 7.f * sinf(hs.age[i] * 5.f + hs.phase[i])) * dt;
    hs.h[i].y += hs.vy[i] * dt;
    hs.h[i].alpha = fminf(1.f, hs.age[i] / 0.12f) * (1.f - u * u) * 0.92f;
    i++;
  }
}

void setup() {
  M5StackChan.begin();  // M5.begin (display/PMIC/audio init) + IO expander + servo bus (torque OFF) + INA226
  Serial.begin(115200);
  Serial.setTxTimeoutMs(0);   // never block the render loop on the USB log when nothing is reading it (it cost ~2 s per state change)
  delay(300);
  Serial.println("\n=== Sunday Face v1 boot ===");
  Serial.printf("chip: %s rev %d, %d cores @ %u MHz\n", ESP.getChipModel(), ESP.getChipRevision(), ESP.getChipCores(), ESP.getCpuFreqMHz());
  Serial.printf("flash: %u bytes (%s), PSRAM: %u bytes (free %u), heap free %u\n", ESP.getFlashChipSize(), psramFound() ? "psram ok" : "NO PSRAM",
                ESP.getPsramSize(), ESP.getFreePsram(), ESP.getFreeHeap());

  loadNight();
  applyBrightness();
  { float l; bool ok = ambient::read(&l, nullptr, nullptr); Serial.printf("ambient light sensor: %s (%.1f lux)\n", ok ? "ok" : "not answering", ok ? l : 0.f); }   // powers it up now so later readings are instant
  M5.Display.setSwapBytes(true);  // framebuffer is native-order RGB565; the panel path needs the bytes swapped
  if (M5.Display.width() < M5.Display.height()) M5.Display.setRotation((M5.Display.getRotation() + 1) & 3);  // want landscape
  int w = M5.Display.width(), h = M5.Display.height();
  Serial.printf("display: %dx%d rotation %d\n", w, h, (int)M5.Display.getRotation());
  if (w != 320 || h != 240) Serial.println("WARN: unexpected display size; layout scales to fit but verify visually");

  dispW = w;
  dispH = h;
  // Persisted display mode (own NVS namespace); anything unexpected falls back to classic.
  DisplayMode saved = DisplayMode::Classic;
  {
    Preferences prefs;
    if (prefs.begin(kPrefsNs, true)) {
      uint8_t v = prefs.getUChar(kPrefsKeyMode, 0);
      if (v < kDisplayModeCount) saved = (DisplayMode)v;
      prefs.end();
    }
  }
  activeMode = saved;
  pendingMode = saved;
  Serial.printf("display mode: %s\n", kDisplayModeNames[(int)saved]);

  uint32_t t0 = millis();
  if (!raster.init(w, h)) fatal("framebuffer alloc failed");
  if (!assets[(int)saved].build(w, h, saved)) fatal("asset bake failed");
  Serial.printf("layout '%s' baked in %u ms (ring pixels %d, head scale %.3f, feature scale %.3f); PSRAM free %u\n",
                kDisplayModeNames[(int)saved], millis() - t0, assets[(int)saved].ringN, assets[(int)saved].layout.head.s,
                assets[(int)saved].layout.feat.s, ESP.getFreePsram());
  // Bake the other layout in the background (core 0, low priority) so switching later is instant.
  static DisplayMode otherMode;
  otherMode = saved == DisplayMode::Classic ? DisplayMode::Immersive : DisplayMode::Classic;
  xTaskCreatePinnedToCore(
      [](void*) {
        uint32_t b0 = millis();
        bool ok = assets[(int)otherMode].build(dispW, dispH, otherMode);
        Serial.printf("layout '%s' baked in background in %u ms (%s); PSRAM free %u\n", kDisplayModeNames[(int)otherMode],
                      millis() - b0, ok ? "ok" : "FAILED", ESP.getFreePsram());
        vTaskDelete(nullptr);
      },
      "bake", 8192, nullptr, 1, nullptr, 0);

  face.seed(esp_random());
  petEngine.seed(esp_random());
  if (!petsound::init()) Serial.println("pet: sound synthesis failed (out of memory?)");
  xTaskCreatePinnedToCore(touchTask, "touch", 4096, nullptr, 4, nullptr, 0);   // 50 Hz, independent of the ~7 fps render loop
  face.snap(ExprId::Idle);
  stateSince = millis();
  state = State::Idle;
  applyBehavior(state);
  face.setExpression(kStateBehavior[0].expression);
  startNetwork();
#ifdef SUNDAY_DEVICE_KEY
  {
    static audiobridge::Config ac;
    ac.host = SUNDAY_GATEWAY_HOST;
#ifdef SUNDAY_GATEWAY_IP
    ac.fallbackIp = SUNDAY_GATEWAY_IP;
#else
    ac.fallbackIp = "";
#endif
    ac.port = 8770;
    ac.key = SUNDAY_DEVICE_KEY;
    ac.fwTag = "sunday-face-2.7.5-dual-mic-test";
    audiobridge::setCommandHandler(onBridgeCmd);
    audiobridge::setAliveHandler(onAlive);
    audiobridge::begin(ac, onGatewayState);   // idles until Wi-Fi (station mode) is up; mic stays off until the gateway authenticates
  }
#endif
#ifdef SUNDAY_DIAG_TOKEN
  // Wireless firmware updates (no USB cable needed after this version): password = the diag token, written to the OTA slot
  // that is not running, so a failed update leaves the current firmware untouched.
  ArduinoOTA.setHostname("sunday-face");
  ArduinoOTA.setPassword(SUNDAY_DIAG_TOKEN);
  ArduinoOTA.setMdnsEnabled(false);   // we already run our own mDNS responder
  ArduinoOTA.onStart([]() { Serial.println("ota: update starting"); });
  ArduinoOTA.onEnd([]() { Serial.println("ota: update finished, rebooting"); });
  ArduinoOTA.onError([](ota_error_t e) { Serial.printf("ota: error %u\n", (unsigned)e); });
  ArduinoOTA.begin();
#endif
}

void loop() {
  static uint32_t lastFrame = 0, fpsStart = 0, frames = 0;
  if (!cam::busBorrowed()) M5.update();   // (the head touch sensor is polled by touchTask at 50 Hz; the panel needs the I2C bus the camera may have)
  serviceNetwork();
  if (rebootAt && millis() > rebootAt) ESP.restart();
#ifdef SUNDAY_DIAG_TOKEN
  ArduinoOTA.handle();
#endif
  {
    char n[16] = "";
    portENTER_CRITICAL(&stateMux);
    if (pendingState[0]) { strlcpy(n, pendingState, sizeof(n)); pendingState[0] = 0; }
    portEXIT_CRITICAL(&stateMux);
    if (n[0]) {
      int i = findName(kStateNames, kStateCount, n);
      if (i >= 0) setState((State)i);
    }
  }

  uint32_t now = millis();
  {
    decltype(alivePending) intent;
    portENTER_CRITICAL(&aliveMux);intent=alivePending;alivePending.pending=false;portEXIT_CRITICAL(&aliveMux);
    if (intent.pending) {
      int expression=findName(kExpressionNames,kExpressionCount,String(intent.expression));
      int idle=findName(kExpressionNames,kExpressionCount,String(intent.idleExpression));
      aliveGate.update(intent.epoch,intent.seq,intent.enabled,expression,intent.ttl,now,idle,intent.idleTtl,intent.idleFlags);
    }
    alivePerformance.store(aliveGate.performing(now));
  }
  for (;;) {                                   // commands queued by the bridge
    char n[24], v[32]; bool have = false;
    portENTER_CRITICAL(&cmdMux);
    if (cmdCount > 0) { strlcpy(n, cmdQ[0].name, sizeof(n)); strlcpy(v, cmdQ[0].value, sizeof(v)); for (int i = 1; i < cmdCount; i++) cmdQ[i - 1] = cmdQ[i]; cmdCount--; have = true; }
    portEXIT_CRITICAL(&cmdMux);
    if (!have) break;
    runBodyCmd(n, v);
  }
  if (now >= telemetryAt) { sendTelemetry(now); telemetryAt = now + 5000; }
  if (audiobridge::takeCameraRequest()) snapRequested = true;
  {   // night + Sleep: the screen is dark when idle, wakes (dimly) whenever Sunday is invoked or shows a photo, and sleeps again a few seconds after
    bool busy = (state != State::Idle && state != State::Sleeping) || snapRequested || now < photoUntil || (liveUntil && now < liveUntil)
               || (lastManualMs && now - lastManualMs < 20000);   // a face you set by hand stays visible for 20 s
    if (busy) nightWakeUntil = now + 4000;
    // Night mode: when nothing is going on (and nobody forced an expression in the last 20 s) the face goes back to SLEEP — eyes closed, dim ring.
    // This also undoes a manual expression, and returns to sleep after Sunday was woken up and has finished.
    if (nightMode && !busy && now >= nightWakeUntil && now - lastManualMs > 20000 && (state != State::Sleeping || exprOverride)) {
      aliveGate.clearManual();exprOverride=false;
      setState(State::Sleeping);
      nightForcedSleep = true;
    }
    if (!nightMode && nightForcedSleep) {          // night mode ended: wake the face up again
      nightForcedSleep = false;
      if (state == State::Sleeping && !exprOverride) setState(State::Idle);
    }
    bool wantBlank = nightMode && nightScreen == 0 && now >= nightWakeUntil;
    if (wantBlank != screenBlank) {
      screenBlank = wantBlank;
      if (screenBlank) M5.Display.fillScreen(0);          // clear the last picture before the backlight goes off
      applyBrightness();
    }
  }

  if (snapRequested || (liveUntil && now<liveUntil) || nightMode || screenBlank) {if (idleEligible.load())cancelIdleMotion();audiobridge::cancelIdleSound();}
  if (snapRequested) { snapRequested = false; doSnap(); now = millis(); }
  if (liveUntil && now < liveUntil) { liveStep(now); return; }       // live view: the camera picture replaces the face
  if (liveOn) { cam::liveEnd(); liveOn = false; liveUntil = 0; lastFrame = 0; }
  if (now < photoUntil) { delay(20); return; }
  bool asleep = screenBlank;
  if (asleep) { updateLeds(); delay(40); return; }                        // night mode, Sleep, nothing going on: nothing to draw
  if (state == State::Wake && (!exprOverride || aliveGate.enabled) && now - stateSince > kWakeDurationMs) setState(State::Idle);
  frameMs = (!exprOverride && (state == State::Idle || state == State::Sleeping)) ? 50 : 33;  // calmer states redraw less often
  if (now - lastFrame < frameMs) { delay(1); return; }
  lastFrame = now;

  int petAppliedForAlive=-1;
  // ---- pet mode: apply the engine's output to the face, and let hearts float up
  {
    static bool petApplied = false;
    static uint32_t lastHeartT = 0;
    PetOutput p;
    portENTER_CRITICAL(&petMux);
    p = petShared;
    portEXIT_CRITICAL(&petMux);
    if (p.active && !aliveGate.performing(now)) {
      petApplied = true;
      petAppliedForAlive=(int)p.expr;
      face.setExpression(p.expr);
      face.setGaze(p.gazeX, p.gazeY, p.drift);
      face.setRing(p.ring);
      face.setTalking(false);
    } else if (petApplied) {                              // petting is over: hand the face back to the current state
      petApplied = false;
      if (!exprOverride) {
        applyBehavior(state);
        face.setExpression(kStateBehavior[(int)state].expression);
      }
    }
    float hdt = lastHeartT ? (now - lastHeartT) / 1000.f : 0.03f;
    lastHeartT = now;
    stepHearts(hdt > 0.2f ? 0.2f : hdt, p.active ? p.heartRate : 0.f, petBurst.exchange(0));
  }

  // Idle proposals never become manual overrides or block essential activities.
  bool eligible=aliveGate.idleValid(now) && state==State::Idle && !nightMode &&
      !aliveGate.manual && !exprOverride && !aliveGate.performing(now) && petAppliedForAlive<0 &&
      !audiobridge::speechPlaying() && now-stateSince>2000 &&
      (!idleManualUntil.load() || (int32_t)(now-idleManualUntil.load())>=0);
  if (!eligible && idleEligible.load())cancelIdleMotion();
  else if (eligible)idleEligible.store(true);
  static bool wasIdleEligible=false;
  if (wasIdleEligible && !eligible)audiobridge::cancelIdleSound();
  wasIdleEligible=eligible;
  bool subdued=aliveGate.idleHint==(int)ExprId::Sad;
  IdlePlan plan=idlePlanner.tick(now,eligible,aliveGate.idleHint==(int)ExprId::Happy,subdued,
      (aliveGate.idleFlags&2)!=0,(aliveGate.idleFlags&4)!=0,esp_random());
  if (eligible && plan.move)queueIdleMotion(plan);
  if (plan.sound)idleSoundRequest.store(true);
  static bool idleGazeApplied=false;
  if (eligible && (aliveGate.idleFlags&2)) {
    face.setGaze(plan.gazeX,plan.gazeY,subdued?.3f:.8f);idleGazeApplied=true;
  } else if (idleGazeApplied) {
    if (petAppliedForAlive<0)applyBehavior(state);
    idleGazeApplied=false;
  }
  if (aliveGate.enabled) {
    int selected=aliveGate.choose((int)kStateBehavior[(int)state].expression,
        petAppliedForAlive, state==State::Wake || state==State::Listening || state==State::Thinking,
        nightForcedSleep && state==State::Sleeping, now, !nightMode);
    if (eligible && plan.expression>=0 && !subdued)selected=plan.expression;
    if ((int)face.expression()!=selected)face.setExpression((ExprId)selected);
  }

  // ---- real lip sync and listening reaction, from the audio itself (not from state timers)
  bool showListenBars = false;
  {
    bool bridge = audiobridge::active();
    bool speakingNow = state == State::Speaking && !exprOverride;
    // The mouth follows the voice on top of WHATEVER expression is showing (the brain can pick any face while it speaks).
    // Lip mode stays on for the whole Speaking state so the mouth is steady between sentences, and while speech audio plays in any state.
    bool lipMode = bridge && (audiobridge::speechPlaying() || state == State::Speaking);
    if (lipMode) { face.setLip(true, audiobridge::lipLevel()); if (speakingNow) face.setTalking(false); }
    else { face.setLip(false, 0.f); if (speakingNow) face.setTalking(kStateBehavior[(int)State::Speaking].talking); }
    bool petOn = false;
    { portENTER_CRITICAL(&petMux); petOn = petShared.active; portEXIT_CRITICAL(&petMux); }
    face.setAttention(state == State::Listening && (!exprOverride || aliveGate.enabled) && !petOn && bridge ? audiobridge::micLevel() : 0.f);
    float lv = (state == State::Listening && bridge) ? audiobridge::micLevel() : 0.f;
    listenLevel += (lv - listenLevel) * (lv > listenLevel ? 0.5f : 0.2f);
    showListenBars = state == State::Listening && (!exprOverride || aliveGate.enabled) && !petOn;
  }

  face.update(now);
  if (pendingMode != activeMode && assets[(int)pendingMode].ready.load(std::memory_order_acquire)) {
    activeMode = pendingMode;  // swap between frames: the new layout is complete, so no partial frame is ever shown
    Serial.printf("display mode active: %s\n", kDisplayModeNames[(int)activeMode]);
  }
  FaceRenderState rs = face.state();
  rs.heartCount = hs.n;
  for (int i = 0; i < hs.n; i++) rs.hearts[i] = hs.h[i];
  renderFace(raster, assets[(int)activeMode], rs);
  if (showListenBars) drawListeningBars(raster.fb, raster.w, raster.h, now / 1000.f, listenLevel);
  if (nightMode) toGray(raster.fb, raster.w * raster.h);                        // night: the face is black and white (also when Sleep wakes for a conversation)
  M5.Display.pushImage(0, 0, raster.w, raster.h, raster.fb);
  if (now < 10000) {  // address overlay for the first seconds after boot
    M5.Display.setTextColor(0x8D9F);
    M5.Display.setTextSize(1);
    M5.Display.setCursor(4, M5.Display.height() - 10);
    M5.Display.print(ipString());
  }
  updateLeds();

  frames++;
  if (now - fpsStart >= 2000) {
    fps = frames * 1000 / (now - fpsStart);
    frames = 0;
    fpsStart = now;
  }
}
