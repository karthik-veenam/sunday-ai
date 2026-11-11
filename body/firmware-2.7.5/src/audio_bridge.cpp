#include "audio_bridge.h"
#include <M5Unified.h>
#include <WiFi.h>
#include <ESPmDNS.h>
#include <WebSocketsClient.h>
#include <esp_heap_caps.h>
#include "mbedtls/md.h"
#include <atomic>
#include "ambient.h"
#include <math.h>

namespace audiobridge {

namespace {

constexpr uint32_t kMicRate = 16000;
constexpr size_t kMicSamples = 1280;                 // 80 ms: one gateway frame
constexpr size_t kSpkBlockSamples = 4410;            // 200 ms slices handed to the speaker queue
constexpr size_t kSpkMaxBytes = 4u * 1024 * 1024;    // longest utterance we accept (~95 s @ 22.05 kHz)
constexpr uint32_t kMicResumeDelayMs = 40;           // back to the mic almost at once: the mode switch only costs ~5-20 ms, and every ms here blanks the start of the user's reply

enum class Mode : uint8_t { Off, Mic, Speaker };

Config g_cfg{};
StateCb g_onState = nullptr;
AliveCb g_onAlive = nullptr;
CmdCb g_onCmd = nullptr;
std::atomic<bool> g_cameraReq{false};
std::atomic<float> g_lip{0.f}, g_micLvl{0.f};
std::atomic<bool> g_lipOn{false};              // gateway speech (not a chime) is playing right now
bool g_spkLip = true;                       // lip-sync the current utterance (the gateway sets lip:0 for chimes)
uint32_t g_localStartedAt = 0, g_spkBusyAt = 0;
// where in the utterance the speaker is: anchored when the first slice is queued (and again after an underrun)
uint32_t g_spkT0 = 0;
size_t g_spkBase = 0;                     // samples already played when the anchor was set
bool g_spkAnchored = false;
constexpr int kLipLagMs = 40;             // speaker pipeline latency: look at the audio this much behind the queue clock
constexpr int kLipWinMs = 30;
WebSocketsClient g_ws;
TaskHandle_t g_task = nullptr;

// connection
std::atomic<bool> g_enabled{false};
bool g_started = false, g_authed = false;
uint32_t g_connectedAt = 0, g_lastAttempt = 0;
IPAddress g_ip;
char g_server[48] = "";
uint32_t g_reconnects = 0, g_authFailures = 0;

// audio mode / arbitration
Mode g_mode = Mode::Off;
std::atomic<bool> g_diagRequest{false}, g_diagOwned{false};
uint32_t g_switches = 0, g_lastSwitchMs = 0, g_maxSwitchMs = 0;

// microphone
int16_t g_micBuf[2][kMicSamples];
// Optional stereo side channel. Default off; allocation and extra packets only when enabled.
bool g_dualRequested = false, g_dualActive = false;
int16_t* g_dualBuf = nullptr;
uint8_t* g_dualFrame = nullptr;
void sendText(const char* s);
void reportDual(const char* error = "") {
 char status[160]; snprintf(status,sizeof(status),"{\"type\":\"dual_mic_status\",\"enabled\":%s,\"rate\":16000,\"channels\":%d,\"error\":\"%s\"}",g_dualActive?"true":"false",g_dualActive?2:1,error);sendText(status);
}
std::atomic<bool> g_micReady[2] = {false, false};
bool g_micArmed[2] = {false, false};
int g_armIdx = 0, g_sendIdx = 0;
uint16_t g_micSeq = 0;
uint32_t g_micFrames = 0, g_micDropped = 0;
float g_micRms = 0, g_micGain = 1.0f;
int g_micPeak = 0, g_peakWin = 0;
uint32_t g_peakWinStart = 0;

// local (device-generated) sounds
struct LocalReq { const int16_t* pcm; size_t n; uint32_t rate; uint8_t vol; bool interrupt; } g_localReq{};
std::atomic<bool> g_localPending{false};
std::atomic<bool> g_cancelIdleSound{false};
bool g_localPlaying = false;
std::atomic<int> g_localInflight{0};
portMUX_TYPE g_evMux = portMUX_INITIALIZER_UNLOCKED;
char g_evBuf[512] = "";
bool g_evPending = false;

// speaker
uint8_t* g_spk = nullptr;
size_t g_spkFill = 0, g_spkQueued = 0, g_spkExpect = 0;
std::atomic<int> g_spkInflight{0};
uint32_t g_spkId = 0, g_spkRate = 22050, g_spkBytes = 0, g_spkDropped = 0, g_spkUnderruns = 0, g_plays = 0, g_localPlays = 0;
bool g_spkEnded = false, g_spkStarted = false, g_spkRejected = false;
uint32_t g_lastDoneAt = 0;
uint8_t g_volume = 255;

// ---------------------------------------------------------------------------- small helpers
void hexOf(const uint8_t* p, size_t n, char* out) {
  static const char* d = "0123456789abcdef";
  for (size_t i = 0; i < n; i++) { out[2 * i] = d[p[i] >> 4]; out[2 * i + 1] = d[p[i] & 15]; }
  out[2 * n] = 0;
}

void hmacHex(const char* key, const char* msg, char* out65) {
  uint8_t mac[32];
  mbedtls_md_context_t ctx;
  mbedtls_md_init(&ctx);
  mbedtls_md_setup(&ctx, mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), 1);
  mbedtls_md_hmac_starts(&ctx, (const uint8_t*)key, strlen(key));
  mbedtls_md_hmac_update(&ctx, (const uint8_t*)msg, strlen(msg));
  mbedtls_md_hmac_finish(&ctx, mac);
  mbedtls_md_free(&ctx);
  hexOf(mac, 32, out65);
}

// Tiny JSON field readers for the flat messages the gateway sends (tolerates spaces after ':' and ',').
const char* findKey(const char* s, const char* key) {
  char pat[40];
  snprintf(pat, sizeof(pat), "\"%s\"", key);
  const char* p = strstr(s, pat);
  if (!p) return nullptr;
  p += strlen(pat);
  while (*p == ' ' || *p == ':') p++;
  return p;
}
bool jstr(const char* s, const char* key, char* out, size_t n) {
  const char* p = findKey(s, key);
  if (!p || *p != '"') return false;
  p++;
  size_t i = 0;
  while (*p && *p != '"' && i + 1 < n) out[i++] = *p++;
  out[i] = 0;
  return true;
}
bool jnum(const char* s, const char* key, double* v) {
  const char* p = findKey(s, key);
  if (!p || (*p != '-' && (*p < '0' || *p > '9'))) return false;
  *v = atof(p);
  return true;
}

void sendText(const char* s) { g_ws.sendTXT(s); }

// ---------------------------------------------------------------------------- microphone
void micRelease(void*, void* data, size_t) {
  for (int i = 0; i < 2; i++)
    if (data == g_micBuf[i] || (g_dualBuf && data == g_dualBuf + i*kMicSamples*2)) g_micReady[i].store(true, std::memory_order_release);
}

bool startMic() {
  const char* dualError="";
  uint32_t t0 = millis();
  if (M5.Speaker.isRunning()) M5.Speaker.end();  // shared I2S port
  auto c = M5.Mic.config();
  c.sample_rate = kMicRate;
  if (g_dualRequested && !g_dualBuf) {
    g_dualBuf = (int16_t*)heap_caps_malloc(kMicSamples*8 + 3+kMicSamples*4, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    if (!g_dualBuf) { g_dualRequested=false;g_dualActive=false;dualError="allocation_failed"; }
    else g_dualFrame=(uint8_t*)(g_dualBuf + kMicSamples*4);
  }
  if (!g_dualRequested && g_dualBuf) { heap_caps_free(g_dualBuf);g_dualBuf=nullptr;g_dualFrame=nullptr; }
  g_dualActive=g_dualRequested && g_dualBuf;
  c.stereo = g_dualActive;
  M5.Mic.config(c);
  bool started=M5.Mic.begin();
  if (!started && g_dualActive) {
    M5.Mic.end();g_dualRequested=false;g_dualActive=false;
    heap_caps_free(g_dualBuf);g_dualBuf=nullptr;g_dualFrame=nullptr;
    c.stereo=false;M5.Mic.config(c);started=M5.Mic.begin();dualError="stereo_begin_failed";
  }
  if (!started) return false;
  M5.Mic.setBufferReleaseCallback(nullptr, micRelease);
  for (int i = 0; i < 2; i++) { g_micReady[i] = false; g_micArmed[i] = false; }
  g_armIdx = g_sendIdx = 0;
  g_mode = Mode::Mic;
  g_lastSwitchMs = millis() - t0;
  if (g_lastSwitchMs > g_maxSwitchMs) g_maxSwitchMs = g_lastSwitchMs;
  g_switches++;
  reportDual(dualError);
  return true;
}

void stopMic() {
  if (M5.Mic.isRunning()) M5.Mic.end();
  g_mode = Mode::Off;
}

void pumpMic() {
  // keep both capture slots armed, in order
  while (!g_micArmed[g_armIdx] && !g_micReady[g_armIdx].load(std::memory_order_acquire) &&
         M5.Mic.record(g_dualActive ? g_dualBuf+g_armIdx*kMicSamples*2 : g_micBuf[g_armIdx], kMicSamples*(g_dualActive?2:1), kMicRate, g_dualActive)) {
    g_micArmed[g_armIdx] = true;
    g_armIdx ^= 1;
  }
  int i = g_sendIdx;
  if (!g_micReady[i].load(std::memory_order_acquire)) return;
  static uint8_t frame[3 + kMicSamples * 2];
  frame[0] = 1;
  frame[1] = g_micSeq >> 8;
  frame[2] = g_micSeq & 255;
  double acc = 0;
  int peak = 0;
  for (size_t k = 0; k < kMicSamples; k++) {
    float v = (g_dualActive ? g_dualBuf[i*kMicSamples*2 + 2*k+1] : g_micBuf[i][k]) * g_micGain;
    int s = v > 32767 ? 32767 : (v < -32768 ? -32768 : (int)v);
    frame[3 + 2 * k] = s & 255;           // little endian, byte-wise: no alignment assumptions
    frame[4 + 2 * k] = (s >> 8) & 255;
    acc += (double)s * s;
    int a = s < 0 ? -s : s;
    if (a > peak) peak = a;
  }
  if (g_dualActive) {
    g_dualFrame[0]=4;g_dualFrame[1]=g_micSeq>>8;g_dualFrame[2]=g_micSeq&255;
    memcpy(g_dualFrame+3,g_dualBuf+i*kMicSamples*2,kMicSamples*4);
  }
  g_micReady[i].store(false, std::memory_order_release);
  g_micArmed[i] = false;
  g_sendIdx ^= 1;
  float rms = sqrtf((float)(acc / kMicSamples));
  g_micRms = g_micRms * 0.9f + rms * 0.1f;
  {
    float db = 20.f * log10f((rms < 1.f ? 1.f : rms) / 32768.f);          // noise floor here is about -53 dBFS, speech -35..-20
    float l = (db + 44.f) / 22.f;
    g_micLvl.store(l < 0.f ? 0.f : (l > 1.f ? 1.f : l));
  }
  uint32_t now = millis();
  if (peak > g_peakWin) g_peakWin = peak;
  if (now - g_peakWinStart > 1000) { g_micPeak = g_peakWin; g_peakWin = 0; g_peakWinStart = now; }
  if (g_ws.sendBIN(frame, sizeof(frame))) { g_micSeq++; g_micFrames++; }
  else g_micDropped++;
  if (g_dualActive) g_ws.sendBIN(g_dualFrame,3+kMicSamples*4);
}

// ---------------------------------------------------------------------------- speaker
void spkRelease(void*, const void*, uint8_t) {
  if (g_localInflight.load() > 0) g_localInflight.fetch_sub(1);   // a local clip finished (local and gateway audio never overlap)
  else g_spkInflight.fetch_sub(1);
}

void freeSpk() {
  if (g_spk) { heap_caps_free(g_spk); g_spk = nullptr; }
  g_spkFill = g_spkQueued = g_spkExpect = 0;
  g_spkAnchored = false;
  g_lip.store(0.f);
  g_lipOn.store(false);
}

void sendPlaybackDone(uint32_t id) {
  char m[64];
  snprintf(m, sizeof(m), "{\"type\":\"playback_done\",\"id\":%u}", (unsigned)id);
  sendText(m);
}

void beginSpeaker(uint32_t id, uint32_t rate, size_t bytes) {
  g_spkId = id;
  g_spkRate = rate ? rate : 22050;
  g_spkEnded = g_spkStarted = g_spkRejected = false;
  if (g_localPlaying) { M5.Speaker.stop(); g_localPlaying = false; }   // gateway speech has priority over pet sounds
  g_spkInflight = 0;
  g_lastDoneAt = 0;   // more speech arrived: stay in speaker mode
  freeSpk();
  if (bytes == 0 || bytes > kSpkMaxBytes) { g_spkRejected = true; g_spkDropped += bytes; Serial.printf("audio: rejecting %u byte utterance\n", (unsigned)bytes); return; }
  g_spk = (uint8_t*)heap_caps_malloc(bytes + 8, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
  if (!g_spk) { g_spkRejected = true; g_spkDropped += bytes; Serial.println("audio: out of PSRAM for utterance"); return; }
  g_spkExpect = bytes;
  if (g_mode != Mode::Speaker) {
    uint32_t t0 = millis();
    stopMic();
    if (!M5.Speaker.begin()) { Serial.println("audio: speaker begin failed"); freeSpk(); g_spkRejected = true; return; }
    M5.Speaker.setVolume(g_volume);
    M5.Speaker.setBufferReleaseCallback(nullptr, spkRelease);
    g_mode = Mode::Speaker;
    g_lastSwitchMs = millis() - t0;
    if (g_lastSwitchMs > g_maxSwitchMs) g_maxSwitchMs = g_lastSwitchMs;
    g_switches++;
  }
  // A local chirp may already own speaker mode at a lower volume.
  M5.Speaker.setVolume(g_volume);
}

void spkData(const uint8_t* p, size_t n) {
  if (g_spkRejected || !g_spk) { g_spkDropped += n; return; }
  size_t room = g_spkExpect - g_spkFill;
  if (n > room) { g_spkDropped += n - room; n = room; }
  memcpy(g_spk + g_spkFill, p, n);
  g_spkFill += n;
  g_spkBytes += n;
}

void pumpSpeaker() {
  if (g_spk && !g_spkRejected) {
    size_t availBytes = (g_spkFill - g_spkQueued) & ~(size_t)1;
    // queue 200 ms slices; the first slice waits for a full 200 ms (or the end of the utterance)
    while (availBytes && M5.Speaker.isPlaying(0) < 2) {
      size_t want = kSpkBlockSamples * 2;
      if (availBytes < want && !g_spkEnded) break;
      size_t n = availBytes < want ? availBytes : want;
      g_spkInflight.fetch_add(1);
      if (!M5.Speaker.playRaw((const int16_t*)(g_spk + g_spkQueued), n / 2, g_spkRate, false, 1, 0, false)) { g_spkInflight.fetch_sub(1); break; }
      if (!g_spkAnchored) { g_spkT0 = millis(); g_spkBase = g_spkQueued / 2; g_spkAnchored = true; }
      g_spkQueued += n;
      availBytes -= n;
      g_spkStarted = true;
    }
    if (g_spkStarted && !g_spkEnded && g_spkInflight.load() == 0 && !M5.Speaker.isPlaying()) { g_spkUnderruns++; g_spkAnchored = false; }
  }
  // lip level: loudness of the audio at the position being played now (0 whenever nothing is playing)
  {
    float lip = 0.f;
    if (g_spkLip && g_spk && g_spkAnchored && !g_spkRejected && (g_spkInflight.load() > 0 || M5.Speaker.isPlaying())) {
      int32_t posMs = (int32_t)(millis() - g_spkT0) - kLipLagMs;
      if (posMs >= 0) {
        size_t center = g_spkBase + (size_t)posMs * g_spkRate / 1000, w = (size_t)g_spkRate * kLipWinMs / 1000;
        size_t i0 = center > w / 2 ? center - w / 2 : 0, i1 = i0 + w, have = g_spkFill / 2;
        if (i1 <= have) {
          const int16_t* x = (const int16_t*)g_spk;
          double acc = 0;
          for (size_t k = i0; k < i1; k++) acc += (double)x[k] * x[k];
          float rms = sqrtf((float)(acc / w));
          float db = 20.f * log10f((rms < 1.f ? 1.f : rms) / 32768.f);
          float l = (db + 46.f) / 28.f;                                    // -46 dBFS closed .. -18 dBFS wide open
          lip = l < 0.f ? 0.f : (l > 1.f ? 1.f : powf(l, 0.8f));
        }
      }
    }
    g_lip.store(lip);
    g_lipOn.store(g_spkLip && g_spk && g_spkAnchored && !g_spkRejected && (g_spkInflight.load() > 0 || M5.Speaker.isPlaying()));
  }
  bool drained = g_spkEnded && (!g_spk || (g_spkQueued >= (g_spkFill & ~(size_t)1) && g_spkInflight.load() == 0 && !M5.Speaker.isPlaying()));
  if (drained) {
    sendPlaybackDone(g_spkId);
    freeSpk();
    g_plays++;
    g_spkEnded = false;
    g_spkRejected = false;
    g_spkStarted = false;
    g_lastDoneAt = millis();
    g_spkId = 0;
    g_spkAnchored = false;
    g_lip.store(0.f);
  }
  // back to the microphone once the speaker has been idle for a moment
  if (g_spkId == 0 && g_lastDoneAt && millis() - g_lastDoneAt > kMicResumeDelayMs) {
    g_lastDoneAt = 0;
    if (M5.Speaker.isRunning()) M5.Speaker.end();
    g_mode = Mode::Off;
    if (g_authed) startMic();
  }
}

// ---------------------------------------------------------------------------- websocket
void leaveStreaming() {
  g_localPlaying = false;
  if (g_mode == Mode::Speaker) { M5.Speaker.stop(); M5.Speaker.end(); }
  if (g_mode == Mode::Mic) stopMic();
  g_mode = Mode::Off;
  freeSpk();
  g_spkId = 0; g_spkEnded = false; g_lastDoneAt = 0;
}

void handleText(const char* s) {
  char type[24];
  if (!jstr(s, "type", type, sizeof(type))) return;
  if (!strcmp(type, "challenge")) {
    char nonce[48], msg[96], mac[65], hello[320];
    if (!jstr(s, "nonce", nonce, sizeof(nonce))) return;
    snprintf(msg, sizeof(msg), "%s|stackchan", nonce);
    hmacHex(g_cfg.key, msg, mac);
    snprintf(hello, sizeof(hello), "{\"type\":\"hello\",\"device\":\"stackchan\",\"fw\":\"%s\",\"mac\":\"%s\",\"hmac\":\"%s\"}",
             g_cfg.fwTag, WiFi.macAddress().c_str(), mac);
    sendText(hello);
  } else if (!strcmp(type, "welcome")) {
    g_authed = true;
    Serial.println("audio: gateway authenticated; starting microphone");
    if (g_mode == Mode::Off) startMic();
  } else if (!strcmp(type, "state")) {
    char name[16];
    if (jstr(s,"name",name,sizeof(name)) && (!strcmp(name,"wake") || !strcmp(name,"listening") || !strcmp(name,"thinking")))g_cancelIdleSound.store(true);
    if (g_onState && jstr(s, "name", name, sizeof(name))) g_onState(name);
  } else if (!strcmp(type, "alive") && g_authed) {
    double epoch=0, seq=0, enabled=0, ttl=0, idleTtl=0, idleFlags=0; char expression[20]="",idleExpression[20]="";
    bool valid=jnum(s,"epoch",&epoch) && jnum(s,"seq",&seq) && jnum(s,"enabled",&enabled) && jnum(s,"ttl_ms",&ttl);
    jstr(s,"expression",expression,sizeof(expression));
    jstr(s,"idle_expression",idleExpression,sizeof(idleExpression));
    jnum(s,"idle_ttl_ms",&idleTtl);jnum(s,"idle_flags",&idleFlags);
    if (!(idleTtl>=0 && idleTtl<=45000 && idleFlags>=0 && idleFlags<=7)) {idleTtl=0;idleFlags=0;}
    if (valid && epoch>=1 && epoch<=4294967295.0 && seq>=0 && seq<=4294967295.0 && ttl>=0 && ttl<=120000 && g_onAlive)
      g_onAlive((uint32_t)epoch,(uint32_t)seq,enabled==1,expression,(uint32_t)ttl,idleExpression,(uint32_t)idleTtl,(uint8_t)idleFlags);
  } else if (!strcmp(type, "audio_start")) {
    double id = 0, rate = 22050, bytes = 0;
    jnum(s, "id", &id); jnum(s, "rate", &rate); jnum(s, "bytes", &bytes);
    double lipf = 1; jnum(s, "lip", &lipf);
    if (g_authed && !g_diagOwned.load()) beginSpeaker((uint32_t)id, (uint32_t)rate, (size_t)bytes);
    g_spkLip = lipf != 0;
  } else if (!strcmp(type, "cmd")) {               // Home Assistant (via the gateway) controls the body
    char name[24], val[32];
    if (g_onCmd && jstr(s, "name", name, sizeof(name))) { if (!jstr(s, "value", val, sizeof(val))) val[0] = 0; g_onCmd(name, val); }
  } else if (!strcmp(type, "light")) {            // gateway asks: how bright is the room?
    float lux = 0; uint16_t c0 = 0, c1 = 0; char m[120];
    if (ambient::read(&lux, &c0, &c1)) snprintf(m, sizeof(m), "{\"type\":\"light\",\"ok\":true,\"lux\":%.1f,\"ch0\":%u,\"ch1\":%u}", lux, (unsigned)c0, (unsigned)c1);
    else snprintf(m, sizeof(m), "{\"type\":\"light\",\"ok\":false}");
    sendText(m);
  } else if (!strcmp(type, "camera")) {
    g_cameraReq = true;
  } else if (!strcmp(type, "audio_end")) {
    g_spkEnded = true;
  } else if (!strcmp(type, "config")) {
    double v;
    if (g_authed && jnum(s,"dual_mic",&v)) {g_dualRequested=(v==1);if(g_dualRequested==g_dualActive)reportDual();}
    if (jnum(s, "mic_gain", &v) && v > 0 && v < 64) g_micGain = (float)v;
    if (jnum(s, "volume", &v) && v >= 0 && v <= 255) { g_volume = (uint8_t)v; if (M5.Speaker.isRunning()) M5.Speaker.setVolume(g_volume); }
  }
}

void wsEvent(WStype_t type, uint8_t* payload, size_t length) {
  switch (type) {
    case WStype_CONNECTED:
      g_connectedAt = millis();
      Serial.printf("audio: websocket open to %s\n", g_server);
      break;
    case WStype_DISCONNECTED:
      if (g_authed || g_connectedAt) {
        if (!g_authed && millis() - g_connectedAt < 3000) g_authFailures++;
        g_reconnects++;
        Serial.printf("audio: disconnected (authed=%d)\n", (int)g_authed);
      }
      g_authed = false;
      if (g_onAlive)g_onAlive(0,0,false,"",0,"",0,0);
      g_connectedAt = 0;
      leaveStreaming();
      break;
    case WStype_TEXT:
      handleText((const char*)payload);
      break;
    case WStype_BIN:
      if (length >= 3 && payload[0] == 2 && g_authed) spkData(payload + 3, length - 3);
      break;
    default: break;
  }
}

bool resolveGateway() {
  String h = g_cfg.host;
  if (h.endsWith(".local")) h.remove(h.length() - 6);
  IPAddress ip = MDNS.queryHost(h.c_str(), 1500);
  if (ip == IPAddress((uint32_t)0) || ip == IPAddress(255, 255, 255, 255)) {
    if (g_ip != IPAddress((uint32_t)0)) ip = g_ip;                       // last good address
    else if (g_cfg.fallbackIp && *g_cfg.fallbackIp) ip.fromString(g_cfg.fallbackIp);
  }
  if (ip == IPAddress((uint32_t)0)) return false;
  g_ip = ip;
  snprintf(g_server, sizeof(g_server), "%s:%u", ip.toString().c_str(), g_cfg.port);
  return true;
}

// Runs on the audio task. Starts a queued local clip, and hands the hardware back when it has finished.
void handleLocal() {
  if (g_cancelIdleSound.exchange(false)) {
    g_localPending.store(false);
    if (g_localPlaying && g_spkId==0) {
      M5.Speaker.stop();g_localPlaying=false;g_localInflight.store(0);g_lastDoneAt=millis();
    }
  }
  // A clip is finished when the speaker hardware really stopped (the inflight counter can lose count when one clip interrupts another,
  // which used to leave the device stuck in speaker mode = deaf).
  if (g_localPlaying && !M5.Speaker.isPlaying() && millis() - g_localStartedAt > 120) {
    g_localPlaying = false;
    g_localInflight.store(0);
    g_lastDoneAt = millis();                       // with the gateway up, pumpSpeaker() resumes the microphone 40 ms later
    if (!g_authed) { if (M5.Speaker.isRunning()) M5.Speaker.end(); g_mode = Mode::Off; g_lastDoneAt = 0; }
  }
  if (!g_localPending.load(std::memory_order_acquire)) return;
  LocalReq r = g_localReq;
  g_localPending.store(false, std::memory_order_release);
  if (g_diagOwned.load()) return;
  if (g_mode == Mode::Speaker && g_spkId != 0) return;                  // gateway speech in progress: it has priority
  if (g_localPlaying) { if (!r.interrupt) return; M5.Speaker.stop(); }
  if (g_mode == Mode::Mic) stopMic();
  if (!M5.Speaker.isRunning()) {
    if (!M5.Speaker.begin()) return;
    M5.Speaker.setBufferReleaseCallback(nullptr, spkRelease);
  }
  M5.Speaker.setVolume(r.vol);
  g_localInflight.fetch_add(1);
  if (!M5.Speaker.playRaw(r.pcm, r.n, r.rate, false, 1, 0, false)) { g_localInflight.fetch_sub(1); return; }
  g_localPlaying = true;g_localPlays++;
  g_localStartedAt = millis();
  g_mode = Mode::Speaker;
  g_lastDoneAt = 0;
}

void sendQueuedEvent() {
  char buf[sizeof(g_evBuf)];
  bool have = false;
  portENTER_CRITICAL(&g_evMux);
  if (g_evPending) { strlcpy(buf, g_evBuf, sizeof(buf)); g_evPending = false; have = true; }
  portEXIT_CRITICAL(&g_evMux);
  if (have && g_authed) g_ws.sendTXT(buf);
}

void audioTask(void*) {
  for (;;) {
    if (!g_diagRequest.load()) handleLocal();
    bool run = g_enabled.load() && WiFi.status() == WL_CONNECTED && (WiFi.getMode() & WIFI_MODE_STA);
    if (g_diagRequest.load()) {
      if (!g_diagOwned.load()) {
        leaveStreaming();
        g_diagOwned = true;
      }
      vTaskDelay(pdMS_TO_TICKS(20));
      continue;
    }
    g_diagOwned = false;
    if (!run) {
      if (g_started) { g_ws.disconnect(); g_started = false; g_authed = false; leaveStreaming(); }
      vTaskDelay(pdMS_TO_TICKS(g_localPending.load() || g_localPlaying ? 10 : 250));
      continue;
    }
    if (!g_started) {
      if (millis() - g_lastAttempt < 2000) { vTaskDelay(pdMS_TO_TICKS(50)); continue; }
      g_lastAttempt = millis();
      if (!resolveGateway()) { vTaskDelay(pdMS_TO_TICKS(500)); continue; }
      g_ws.begin(g_ip.toString().c_str(), g_cfg.port, "/stackchan");
      g_ws.onEvent(wsEvent);
      g_ws.setReconnectInterval(2000);
      g_ws.enableHeartbeat(5000, 3000, 2);
      g_started = true;
    }
    g_ws.loop();
    sendQueuedEvent();
    if (g_authed && g_mode == Mode::Off && g_spkId == 0 && g_lastDoneAt == 0 && millis() - g_lastAttempt > 1000) {
      g_lastAttempt = millis();   // e.g. after a diagnostic released the hardware: resume streaming
      startMic();
    }
    if (g_authed) {
      if (g_mode == Mode::Mic) {
        if (g_dualRequested != g_dualActive) {stopMic();startMic();}
        else pumpMic();
      }
      else if (g_mode == Mode::Speaker) {
        pumpSpeaker();
        if (g_spkBusyAt == 0 || g_spkId != 0 || g_localPlaying || g_localPending.load() || M5.Speaker.isPlaying()) g_spkBusyAt = millis();
        else if (g_lastDoneAt == 0 && millis() - g_spkBusyAt > 1500) {
          Serial.println("audio: watchdog: speaker idle, handing the hardware back to the microphone");
          g_lastDoneAt = millis() - kMicResumeDelayMs - 1;   // pumpSpeaker() resumes the microphone on its next pass
          g_spkBusyAt = 0;
        }
      }
    }
    vTaskDelay(1);
  }
}

}  // namespace

// ---------------------------------------------------------------------------- public API
void setAliveHandler(AliveCb cb) {g_onAlive=cb;}

void begin(const Config& cfg, StateCb onState) {
  g_cfg = cfg;
  g_onState = onState;
  g_enabled = true;
  g_peakWinStart = millis();
  if (!g_task) xTaskCreatePinnedToCore(audioTask, "audio", 12288, nullptr, 5, &g_task, 0);
}

Stats stats() {
  Stats s{};
  s.enabled = g_enabled.load();
  s.connected = g_authed;
  s.mode = g_mode == Mode::Mic ? "mic" : (g_mode == Mode::Speaker ? "speaker" : "off");
  s.micFrames = g_micFrames; s.micDropped = g_micDropped; s.spkBytes = g_spkBytes; s.spkDropped = g_spkDropped;
  s.spkUnderruns = g_spkUnderruns; s.plays = g_plays; s.localPlays=g_localPlays; s.reconnects = g_reconnects; s.authFailures = g_authFailures;
  s.modeSwitches = g_switches; s.lastSwitchMs = g_lastSwitchMs; s.maxSwitchMs = g_maxSwitchMs;
  s.micRms = g_micRms; s.micPeak = g_micPeak; s.micGain = g_micGain; s.volume = g_volume;
  strlcpy(s.server, g_server, sizeof(s.server));
  return s;
}

void setCommandHandler(CmdCb cb) { g_onCmd = cb; }
bool active() { return g_authed; }
float lipLevel() { return g_lip.load(); }
bool speechPlaying() { return g_lipOn.load(); }
float micLevel() { return g_micLvl.load(); }
bool takeCameraRequest() { return g_cameraReq.exchange(false); }

void cancelIdleSound() {g_cancelIdleSound.store(true);}

bool playLocal(const int16_t* pcm, size_t samples, uint32_t rate, uint8_t volume, bool interrupt) {
  if (!g_task || !pcm || !samples) return false;
  if (g_diagRequest.load() || g_localPending.load()) return false;
  if (g_mode == Mode::Speaker && g_spkId != 0) return false;           // gateway speech has priority
  if (g_localPlaying && !interrupt) return false;
  g_localReq = LocalReq{pcm, samples, rate, volume, interrupt};
  g_localPending.store(true, std::memory_order_release);
  return true;
}

void queueEvent(const char* json) {
  portENTER_CRITICAL(&g_evMux);
  if (!g_evPending) { strlcpy(g_evBuf, json, sizeof(g_evBuf)); g_evPending = true; }
  portEXIT_CRITICAL(&g_evMux);
}

void setMicGain(float gain) { if (gain > 0 && gain < 64) g_micGain = gain; }
void setVolume(uint8_t v) { g_volume = v; if (M5.Speaker.isRunning()) M5.Speaker.setVolume(v); }

// Diagnostics: pause the streaming task, own the hardware for the duration, then release it.
static bool acquire() {
  g_diagRequest = true;
  for (int i = 0; i < 100 && !g_diagOwned.load(); i++) {
    if (!g_task) break;
    delay(10);
  }
  if (g_task && !g_diagOwned.load()) { g_diagRequest = false; return false; }
  if (M5.Mic.isRunning()) M5.Mic.end();
  if (M5.Speaker.isRunning()) M5.Speaker.end();
  return true;
}
static void release() {
  if (M5.Mic.isRunning()) M5.Mic.end();
  if (M5.Speaker.isRunning()) M5.Speaker.end();
  g_diagRequest = false;
}

bool diagRecord(int16_t* dst, size_t samples, float gain) {
  if (!acquire()) return false;
  auto c = M5.Mic.config();
  c.sample_rate = kMicRate;
  c.stereo = false;
  M5.Mic.config(c);
  bool ok = M5.Mic.begin();
  if (ok) {
    size_t done = 0;
    uint32_t deadline = millis() + samples / 16 + 3000;
    while (done < samples && millis() < deadline) {
      size_t n = samples - done < kMicSamples ? samples - done : kMicSamples;
      if (M5.Mic.record(dst + done, n, kMicRate, false)) done += n;
      else delay(1);
    }
    while (M5.Mic.isRecording() && millis() < deadline + 1000) delay(1);
    ok = done >= samples;
    if (ok && gain != 1.0f)
      for (size_t i = 0; i < samples; i++) { float v = dst[i] * gain; dst[i] = v > 32767 ? 32767 : (v < -32768 ? -32768 : (int16_t)v); }
  }
  release();
  return ok;
}

bool diagTone(uint32_t hz, uint32_t ms) {
  if (!acquire()) return false;
  bool ok = M5.Speaker.begin();
  if (ok) {
    M5.Speaker.setVolume(g_volume);
    M5.Speaker.tone((float)hz, ms);
    uint32_t t0 = millis();
    while (millis() - t0 < ms + 300) delay(10);
    M5.Speaker.stop();
  }
  release();
  return ok;
}

}  // namespace audiobridge
