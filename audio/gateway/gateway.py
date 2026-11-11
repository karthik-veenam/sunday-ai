#!/usr/bin/env python3
"""Sunday Stack-chan audio gateway.

One process, one device. It is the *only* thing that talks to the Stack-chan, and it exposes the Stack-chan to the
UNCHANGED production code through two loopback sockets that the PATH shims (shim/arecord, shim/aplay) connect to:

    Stack-chan --WebSocket--> gateway --127.0.0.1:MIC--> shim/arecord --stdout--> MicStream (16 kHz mono S16, 80 ms)
    Stack-chan <-WebSocket--- gateway <--127.0.0.1:SPK-- shim/aplay  <--stdin---- TTS.speak / chime / alerts

Device protocol (WebSocket, path /stackchan, LAN only, HMAC challenge-response, see README.md):
    server -> {"type":"challenge","nonce":hex}
    device -> {"type":"hello","device":"stackchan","fw":str,"mac":str,"hmac":hex(HMAC-SHA256(key, nonce|device))}
    server -> {"type":"welcome","mic":{"rate":16000,"frame_samples":1280},"spk":{"rate":22050,"chunk_bytes":1764}}
    binary device->server  0x01 | seq(u16 BE) | 2560 B  PCM16LE mono 16 kHz (80 ms)
    binary server->device  0x02 | seq(u16 BE) | <=1764 B PCM16LE mono 22.05 kHz (40 ms)
    text  server->device   audio_start{id,rate,bytes} / audio_end{id} / state{name}
    text  device->server   playback_done{id} / stats{...}

Echo control: while audio is playing (+ a tail) mic frames sent to production are replaced by digital silence, so the
wake-word detector can never hear Sunday's own voice or chime. The Stack-chan is also half-duplex in hardware.
"""
import asyncio
import collections
import hmac
import hashlib
import json
import os
import signal
import sys
import time

def wake_audit(trace, stage, **extra):
    if not isinstance(trace,dict) or not isinstance(trace.get('id'),str) or not isinstance(trace.get('accepted_ns'),int):return
    now=time.monotonic_ns()
    print('[Wake audit] '+json.dumps({**trace,'stage':stage,'at_ns':now,'since_accepted_ms':round((now-trace['accepted_ns'])/1e6,3),**extra}),flush=True)

import numpy as np
from aiohttp import web, WSMsgType, ClientSession, ClientTimeout
from scipy.signal import butter, sosfilt

HOME = os.environ.get("SUNDAY_AUDIO_HOME", os.path.expanduser("~/sunday-audio"))
CFG = {
    "device_host": os.environ.get("SA_DEVICE_HOST", "0.0.0.0"),
    "device_port": int(os.environ.get("SA_DEVICE_PORT", "8770")),
    "mic_port": int(os.environ.get("SA_MIC_PORT", "8771")),
    "spk_port": int(os.environ.get("SA_SPK_PORT", "8772")),
    "health_port": int(os.environ.get("SA_HEALTH_PORT", "8773")),
    "key_file": os.environ.get("SA_KEY_FILE", f"{HOME}/secrets/device.key"),
    "brain_ws": os.environ.get("SA_BRAIN_WS", "ws://127.0.0.1:8080/ws"),  # "" disables face-event forwarding
    "gate_tail_s": float(os.environ.get("SA_GATE_TAIL_S", "0.2")),   # silence kept after playback; long tails swallow the first word of the reply
    # Speech loudness: the tiny speaker cannot play bass and TTS is usually quiet, so cut the bass, lift the peaks to just under full
    # scale (max gain), then compress mildly so quiet words come up. Chimes are never touched. SA_SPK_SHAPE=0 turns it all off.
    "spk_shape": os.environ.get("SA_SPK_SHAPE", "1") == "1",
    "spk_hp_hz": float(os.environ.get("SA_SPK_HP_HZ", "180")),
    "spk_peak": float(os.environ.get("SA_SPK_PEAK", "0.95")),
    "spk_max_gain_db": float(os.environ.get("SA_SPK_MAX_GAIN_DB", "15")),
    "spk_soft": float(os.environ.get("SA_SPK_SOFT", "1.8")),
    "lead_s": float(os.environ.get("SA_LEAD_S", "2.0")),          # how far ahead of real time we may send speaker audio
    "done_grace_s": float(os.environ.get("SA_DONE_GRACE_S", "3.0")),
    "device_wait_s": float(os.environ.get("SA_DEVICE_WAIT_S", "6")),   # how long a reply waits for a reconnecting Stack-chan
    "highpass_hz": float(os.environ.get("SA_HIGHPASS_HZ", "120")),   # 0 = off. Removes the Stack-chan hum/rumble so production end-of-speech detection works
}
MIC_RATE, MIC_SAMPLES = 16000, 1280
MIC_FRAME_BYTES = MIC_SAMPLES * 2          # 2560
MIC_FRAME_S = MIC_SAMPLES / MIC_RATE       # 0.08
SPK_RATE = 22050
SPK_CHUNK_BYTES = 1764                     # 40 ms
SILENCE = bytes(MIC_FRAME_BYTES)
DEVICE_ID = "stackchan"
FFMPEG_FMT = {"S16_LE": "s16le", "S32_LE": "s32le", "U8": "u8", "FLOAT_LE": "f32le", "S24_3LE": "s24le"}


def log(*a):
    print("[gateway]", *a, flush=True)


def load_key():
    with open(CFG["key_file"], "rb") as f:
        key = f.read().strip()
    if len(key) < 16:
        raise SystemExit("device key too short (need >= 16 bytes)")
    return key


def auth_mac(key: bytes, nonce_hex: str) -> str:
    return hmac.new(key, (nonce_hex + "|" + DEVICE_ID).encode(), hashlib.sha256).hexdigest()


class HighPass:
    """4th-order Butterworth high-pass, state carried across frames (no clicks at frame boundaries).
    The Stack-chan mic has ~27 dB more low-frequency hum/rumble than the AIRHUG; production decides you have stopped talking by
    watching for 0.8 s below a fixed level, so without this it never sees 'quiet' and records until its 25 s limit."""
    def __init__(self, fc, fs=MIC_RATE):
        self.sos = butter(4, fc, "highpass", fs=fs, output="sos")
        self.reset()

    def reset(self):
        self.zi = np.zeros((self.sos.shape[0], 2))

    def __call__(self, frame: bytes) -> bytes:
        x = np.frombuffer(frame, dtype="<i2").astype(np.float64)
        y, self.zi = sosfilt(self.sos, x, zi=self.zi)
        return np.clip(y, -32768, 32767).astype("<i2").tobytes()


class MicClient:
    def __init__(self, writer):
        self.writer = writer
        self.q = asyncio.Queue(maxsize=40)  # 3.2 s
        self.dropped = 0

    def push(self, frame):
        if self.q.full():
            try:
                self.q.get_nowait()
                self.dropped += 1
            except Exception:
                pass
        self.q.put_nowait(frame)


_SPK_SOS = None


def shape_playback(pcm):
    """Make speech louder and cleaner for the small speaker. Returns (pcm, info) - info is None if left untouched."""
    global _SPK_SOS
    if not CFG["spk_shape"] or len(pcm) < int(SPK_RATE * 2 * 0.15):
        return pcm, None
    from scipy import signal
    if _SPK_SOS is None:
        _SPK_SOS = signal.butter(2, CFG["spk_hp_hz"] / (SPK_RATE / 2), "highpass", output="sos")
    x = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    pk0 = float(np.max(np.abs(x))) + 1e-9
    rms0 = float(np.sqrt(np.mean(x * x))) + 1e-9
    y = signal.sosfilt(_SPK_SOS, x).astype(np.float32)
    pk = float(np.max(np.abs(y))) + 1e-9
    g = min(CFG["spk_peak"] / pk, 10 ** (CFG["spk_max_gain_db"] / 20))
    y = y * g
    k = CFG["spk_soft"]
    y = np.clip(np.tanh(k * y) / np.tanh(k), -1.0, 1.0)
    rms1 = float(np.sqrt(np.mean(y * y))) + 1e-9
    db = lambda v: 20 * np.log10(v)
    return (y * 32767.0).astype("<i2").tobytes(), (db(pk0), db(float(np.max(np.abs(y))) + 1e-9), db(rms0), db(rms1), db(g))


from alive_arbiter import Arbiter


class Hub:
    def __init__(self, key: bytes):
        self.key = key
        self.alive = Arbiter()
        self._alive_handle = None
        self._mood_refresh = asyncio.Event()
        from microphone import Microphone
        self.microphone=Microphone(self)
        self.reply_model=None
        self.sleep_telemetry={};self.sleep_telemetry_at=0
        self.sleep=None
        if self.alive.flags.get('sleep_cycle'):
            from sleep_runtime import SleepRuntime
            self.sleep=SleepRuntime(self)
        self._touch_at = 0
        self._touch_tasks = set()
        self.alive_deliveries = collections.deque(maxlen=3)
        self.alive_body_observation = None
        self._active_delivery = None
        self.dev = None
        self.dev_since = None
        self.dev_info = {}
        self.primed = False
        self.hp = HighPass(CFG["highpass_hz"]) if CFG["highpass_hz"] > 0 else None
        self.pending = collections.deque(maxlen=6)   # device frames waiting for the 80 ms feeder (bounds latency to ~0.5 s)
        self.mic_clients = []
        self.frames_in = self.frames_out = self.silence_frames = self.gated_frames = 0
        self.auth_failures = 0
        self.playing = 0
        self.gate_until = 0.0
        self.play_lock = asyncio.Lock()
        self.play_id = 0
        self.done = {}
        self.plays_ok = self.plays_failed = self.play_timeouts = 0
        self.last_play = None
        self.last_failure = None
        self.touch_events = 0
        self.last_touch = None
        self.last_state = None
        from dual_mic import DualMic
        self.dual_mic=DualMic()
        self.ha = None                             # optional Home Assistant (MQTT) bridge, see ha_mqtt.py
        self.light_waiter = None                   # future resolved by the device's {"type":"light"} reply
        self.battery_info = None                   # last {"percent","charging","millivolts"} the body reported
        self.battery_at = 0.0
        self.battery_waiter = None                 # future resolved by the body's next {"type":"battery"} event
        self.brain_state = "idle"                  # the production brain's phase: wake / transcribing / thinking / idle
        self.started = time.time()
        self._listen_handle = None
        self._idle_handle = None

    async def sync_alive(self):
        if self.dev is not None and not self.dev.closed:
            try:
                await self.dev.send_json(self.alive.message())
                return True
            except Exception:pass
        return False

    async def follow_emotional_state(self):
        """Independent low-frequency cached read; never awaited by mic/TTS/turns."""
        from pathlib import Path
        try:
            cfg=json.loads(Path('/home/djpi/sunday2-rebuild/config.json').read_text())
            url='http://127.0.0.1:%s/v1/alive/status'%cfg['daemon_port']
            headers={'Authorization':'Bearer '+cfg['daemon_token']}
        except Exception:return
        if not self.alive.enabled or not any(self.alive.flags.get(k) for k in ('idle_expressions','idle_motion','idle_sounds')):return
        async with ClientSession(timeout=ClientTimeout(total=1)) as session:
            while True:
                self._mood_refresh.clear()
                try:
                    async with session.get(url,headers=headers) as response:
                        data=await response.json()
                        if type(data.get('flags',{}).get('idle_sounds')) is bool:self.alive.flags['idle_sounds']=data['flags']['idle_sounds']
                        if isinstance(data.get('reply_model'),dict):
                            self.reply_model=data['reply_model']
                            if self.ha:self.ha.runtime_status()
                        self.alive.set_idle(data.get('snapshot') if response.status==200 and data.get('enabled') else None,time.time())
                except Exception:
                    self.alive.set_idle(None,time.time())
                await self.sync_alive()
                try:await asyncio.wait_for(self._mood_refresh.wait(),timeout=15)
                except asyncio.TimeoutError:pass

    def observe_alive_body(self,j):
        if not self.alive.enabled:return
        expression=j.get('expr');state=j.get('st')
        if not isinstance(expression,str) or not isinstance(state,str):return
        sample={'at':time.time(),'expression':expression[:24],'state':state[:16],
                'manual_override':j.get('ov')==1,'night':j.get('n')==1}
        self.alive_body_observation=sample
        entry=self._active_delivery
        if entry and entry['started_at']<=sample['at'] and entry['status']=='playing':
            entry['body_observations']=(entry['body_observations']+[sample])[-2:]

    def begin_delivery_record(self,context,pid,intent_sent):
        context=context if isinstance(context,dict) else {}
        proposed=context.get('performance') or {}
        entry={'playback_id':pid,'request_id':context.get('request_id'),'utterance_id':context.get('utterance_id'),
               'proposed_expression':proposed.get('expression') if isinstance(proposed,dict) else None,
               'authorized_expression':self.alive.message()['expression'] or None,
               'intent_sent':bool(intent_sent),'started_at':time.time(),'status':'playing',
               'playback_acknowledged':False,'body_observations':[]}
        self.alive_deliveries.append(entry);self._active_delivery=entry
        return entry

    def schedule_alive_expiry(self):
        self._cancel(self._alive_handle)
        token=self.alive.token()
        delay=max(0,self.alive.expiry-time.monotonic())+.02
        self._alive_handle=asyncio.get_running_loop().call_later(min(120.1,delay),lambda:asyncio.create_task(self.expire_alive(token)))

    async def expire_alive(self,token):
        if self.alive.valid(token):
            await self.sync_alive()
            if not self.playing and not self.alive.pending:
                await self.guarded_idle(token)

    async def reply_end(self,request):
        supplied=request.headers.get('Authorization','').removeprefix('Bearer ')
        if not hmac.compare_digest(supplied.encode(),self.key):raise web.HTTPUnauthorized()
        if not self.alive.enabled:raise web.HTTPNotFound()
        data=await request.json()
        if not isinstance(data,dict):raise web.HTTPBadRequest()
        if self.alive.end(data.get('request_id'),data.get('cancelled') is True):
            self._mood_refresh.set()
            await self.sync_alive()
            self.schedule_alive_expiry()
        return web.json_response({'ok':True})

    async def touch_evidence(self,event_id):
        # Anonymous reference only; never audio, camera data, or an invented identity.
        try:
            from pathlib import Path
            cfg=json.loads(Path('/home/djpi/sunday2-rebuild/config.json').read_text())
            url='http://127.0.0.1:%s/v1/alive/events'%cfg['daemon_port']
            async with ClientSession(timeout=ClientTimeout(total=.5)) as session:
                async with session.post(url,headers={'Authorization':'Bearer '+cfg['daemon_token']},json={'event_id':event_id,'kind':'touch','ref':event_id}) as response:await response.read()
        except Exception:pass

    async def guarded_idle(self,token):
        if self.alive.enabled:
            if not self.alive.valid(token) or (self.alive.reply and self.alive.expiry>time.monotonic()):return
        if self.last_state=='speaking':await self.send_state('idle')

    # ---------------------------------------------------------------- mic path
    def gated(self):
        return self.playing > 0 or time.monotonic() < self.gate_until

    async def feeder(self):
        """Emit exactly one 80 ms frame to every mic client every 80 ms: real device audio when available, silence when
        the device is offline/late, digital silence while the speaker is active. MicStream therefore never stalls."""
        nxt = time.monotonic()
        while True:
            nxt += MIC_FRAME_S
            if not self.primed and len(self.pending) >= (1 if self.microphone.active=='airhug' else 2):
                self.primed = True            # jitter buffer: start consuming once 2 frames are queued (~160 ms)
            frame = None
            if self.primed:
                if self.pending:
                    frame = self.pending.popleft()
                    if self.hp is not None and self.microphone.active!='airhug':
                        frame = self.hp(frame)          # filter every real frame (even gated ones) so the filter state stays continuous
                else:
                    self.primed = False       # underrun: emit silence until the buffer refills to 2 frames
            if frame is None:
                frame = SILENCE
                self.silence_frames += 1
            elif self.gated():
                frame = SILENCE
                self.gated_frames += 1
            for c in list(self.mic_clients):
                c.push(frame)
            self.frames_out += 1
            delay = nxt - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            elif delay < -1.0:
                nxt = time.monotonic()  # we were stalled; resync instead of bursting

    async def mic_client(self, reader, writer):
        c = MicClient(writer)
        self.mic_clients.append(c)
        log("mic client connected (%d)" % len(self.mic_clients))
        try:
            while True:
                frame = await c.q.get()
                writer.write(frame)
                await writer.drain()
        except Exception:
            pass
        finally:
            if c in self.mic_clients:
                self.mic_clients.remove(c)
            try:
                writer.close()
            except Exception:
                pass
            log("mic client gone (%d left)" % len(self.mic_clients))

    # ---------------------------------------------------------------- face state
    async def send_state(self, name):
        if self.alive.enabled:
            self.alive.activity_change(name)
            if name==self.last_state:return
        log("face state ->", name)
        self.last_state = name
        if self.dev is not None and not self.dev.closed:
            try:
                await self.dev.send_json({"type": "state", "name": name})
            except Exception:
                pass

    def _cancel(self, h):
        if h:
            h.cancel()

    async def send_device_cmd(self,name,value):
        if self.dev is not None and not self.dev.closed:
            await self.dev.send_json({'type':'cmd','name':name,'value':value})

    async def set_idle_sounds(self,on):
        from pathlib import Path
        c=json.loads(Path('/home/djpi/sunday2-rebuild/config.json').read_text())
        async with ClientSession(timeout=ClientTimeout(total=2)) as session:
            async with session.post('http://127.0.0.1:8090/v1/alive/settings',headers={'Authorization':'Bearer '+c['daemon_token']},json={'idle_sounds':on}) as response:
                if response.status!=200:raise RuntimeError('Idle sounds setting failed')
                await response.read()
        self.alive.flags['idle_sounds']=on
        await self.sync_alive()
        if self.ha:self.ha.runtime_status()

    async def body_cmd(self, name, value):
        """A command from Home Assistant for the body (expression, display, volume, ...). Dropped if the body is not connected."""
        if name=='reply_model':
            from pathlib import Path
            labels={'Luna (Fast)':'luna','Claude Haiku 5.5':'haiku','GPT-5.4 nano':'nano'}
            if value not in labels:raise ValueError('Unknown reply model')
            cfg=json.loads(Path('/home/djpi/sunday2-rebuild/config.json').read_text())
            async with ClientSession(timeout=ClientTimeout(total=3)) as session:
                async with session.post('http://127.0.0.1:8090/v1/reply-model',headers={'Authorization':'Bearer '+cfg['daemon_token']},json={'selected':labels[value]}) as response:
                    if response.status!=200:raise RuntimeError('Reply model change failed (HTTP '+str(response.status)+'). Wait for any current reply to finish.')
                    self.reply_model=await response.json()
            if self.ha:self.ha.runtime_status()
            return
        if name in ('stt_silence_threshold', 'stt_speech_threshold', 'stt_silence_seconds'):
            from stt_thresholds import save
            await asyncio.to_thread(save, name, value)
            if self.ha:self.ha.runtime_status()
            return
        if name=='stt_mode':
            from stt_mode import set_mode
            await set_mode(value)
            if self.ha:self.ha.runtime_status()
            return
        if name.startswith('dual_mic'):
            if name=='dual_mic_capture':await asyncio.to_thread(self.dual_mic.capture)
            else:
                self.dual_mic.control(name,value)
                if self.dev is not None and not self.dev.closed:await self.dev.send_json({'type':'config','dual_mic':int(self.dual_mic.cfg['enabled'])})
            if self.ha:self.ha.runtime_status()
            return
        if name=='adaptive_speech_detection':
            from adaptive_detection import set_enabled
            await set_enabled(value=='1')
            if self.ha:self.ha.runtime_status()
            return
        if name=='stt_cleanup':
            from stt_processing import set_enabled
            await set_enabled(self.microphone.selected,value=='1')
            if self.ha:self.ha.runtime_status()
            return
        if name=='microphone':
            await self.microphone.select(value);return
        if name=='idle_sounds':
            await self.set_idle_sounds(value=='1');return
        if self.sleep and name in ('night','stay_awake'):
            await self.sleep.control(name,value=='1');return
        if self.dev is None or self.dev.closed:
            return
        if self.alive.enabled and name in ('expression','state'):
            if name=='expression':self.alive.manual_change(value)
            else:self.alive.reset()
            self._cancel(self._listen_handle);self._cancel(self._idle_handle)
            await self.sync_alive()
        try:
            await self.dev.send_json({"type": "cmd", "name": name, "value": value})
            log("ha command ->", name, value)
        except Exception:
            pass

    def on_battery(self, j):
        """The body reported its battery ({"type":"battery","ok":true,"pct":82,"chg":0,"mv":4012})."""
        if j.get("ok"):
            try:
                self.battery_info = {"percent": int(j.get("pct")), "charging": int(j.get("chg", 2)), "millivolts": int(j.get("mv", 0))}
                self.battery_at = time.time()
                if self.ha is not None and hasattr(self.ha, "battery"):
                    self.ha.battery(self.battery_info)
            except Exception as e:
                log("bad battery report:", type(e).__name__)
        w = self.battery_waiter
        if w is not None and not w.done():
            w.set_result(bool(j.get("ok")))

    async def request_battery(self, timeout=2.5):
        """Ask the body for a fresh battery reading. Falls back to the last report (with its age) if the body does not answer in time."""
        if self.dev is None or self.dev.closed:
            return {"ok": False, "detail": "body not connected"}
        fut = asyncio.get_running_loop().create_future()
        self.battery_waiter = fut
        try:
            await self.send_device_cmd("battery", "read")
            await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            pass
        except Exception as e:
            return {"ok": False, "detail": "failed: " + type(e).__name__}
        finally:
            if self.battery_waiter is fut:
                self.battery_waiter = None
        if self.battery_info is None:
            return {"ok": False, "detail": "battery not available (the body has not reported one)"}
        return dict(self.battery_info, ok=True, age_s=round(time.time() - self.battery_at, 1))

    async def body_volume(self, set_pct=None, delta_pct=None, floor_pct=10):
        """Read or change the body's speaker volume in percent (device range 0..255). By voice it never goes below `floor_pct`
        so she can always be heard confirming (Home Assistant can still set anything down to 0)."""
        if self.dev is None or self.dev.closed:
            return {"ok": False, "detail": "body not connected"}
        t = self.sleep_telemetry or {}
        raw = t.get("vol")
        if raw is None:
            return {"ok": False, "detail": "volume not reported yet"}
        cur = int(round(int(raw) * 100 / 255))
        if set_pct is None and delta_pct is None:
            return {"ok": True, "percent": cur, "raw": int(raw)}
        want = set_pct if set_pct is not None else cur + delta_pct
        target = max(floor_pct, min(100, int(round(want))))
        new_raw = int(round(target * 255 / 100))
        await self.send_device_cmd("volume", str(new_raw))
        self.sleep_telemetry = dict(t, vol=new_raw)          # so a second command a moment later builds on this value
        log("volume", cur, "->", target, "percent")
        return {"ok": True, "percent": target, "previous": cur, "raw": new_raw, "limited": target != int(round(want))}

    async def request_light(self, timeout=2.5):
        """Ask the Stack-chan for the room brightness. Returns a dict {ok, lux, ...}."""
        if self.dev is None or self.dev.closed:
            return {"ok": False, "detail": "device not connected"}
        if self.light_waiter is not None and not self.light_waiter.done():
            self.light_waiter.cancel()
        fut = asyncio.get_running_loop().create_future()
        self.light_waiter = fut
        try:
            await self.dev.send_json({"type": "light"})
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return {"ok": False, "detail": "no answer from the device"}
        except Exception as e:
            return {"ok": False, "detail": "failed: " + type(e).__name__}
        finally:
            if self.light_waiter is fut:
                self.light_waiter = None

    async def request_camera(self):
        """Ask the Stack-chan to take a photo and show it on its screen. Returns (ok, detail)."""
        if self.dev is None or self.dev.closed:
            return False, "device not connected"
        if self.alive.enabled:
            self.alive.reset()
            await self.sync_alive()
        try:
            await self.dev.send_json({"type": "camera", "action": "capture"})
        except Exception as e:
            return False, "send failed: " + type(e).__name__
        log("camera capture requested")
        return True, "requested"

    async def on_brain_event(self, ev):
        t = ev.get("type")
        if self.sleep and t in ("wake","transcribing","thinking"):self.sleep.interact()
        if t != "transcript":
            log("brain event:", t)
        loop = asyncio.get_running_loop()
        if t in ("wake", "transcribing", "thinking", "idle"):
            self.brain_state = t
        if t == "wake":
            await self.send_state("wake")
            self._cancel(self._listen_handle)  # wake is a 0.5 s flash on the face (the chime is 0.3 s); then hold "listening" while recording
            wake_token=self.alive.token()
            self._listen_handle = loop.call_later(0.5, lambda: asyncio.ensure_future(self._after_wake(wake_token)))
        elif t in ("transcribing", "thinking"):
            self._cancel(self._listen_handle)
            await self.send_state("thinking")
        elif t == "idle":
            self._mood_refresh.set()
            self._cancel(self._listen_handle)
            if not self.playing:
                await self.send_state("idle")

    async def _after_wake(self,token=None):
        if self.alive.enabled and token is not None and not self.alive.valid(token):return
        if self.last_state == "wake":
            await self.send_state("listening")

    async def brain_events(self):
        url = CFG["brain_ws"]
        if not url:
            return
        while True:
            try:
                async with ClientSession(timeout=ClientTimeout(total=None, sock_connect=5)) as s:
                    async with s.ws_connect(url, heartbeat=15) as ws:
                        log("following brain events at", url)
                        async for m in ws:
                            if m.type == WSMsgType.TEXT:
                                try:
                                    await self.on_brain_event(json.loads(m.data))
                                except Exception as e:
                                    log("bad brain event:", type(e).__name__)
            except Exception as e:
                pass
            await asyncio.sleep(3)

    # ---------------------------------------------------------------- device websocket
    async def ws_device(self, request):
        ws = web.WebSocketResponse(heartbeat=5.0, max_msg_size=1 << 20)
        await ws.prepare(request)
        nonce = os.urandom(16).hex()
        await ws.send_json({"type": "challenge", "nonce": nonce})
        try:
            msg = await asyncio.wait_for(ws.receive(), 5)
            hello = json.loads(msg.data) if msg.type == WSMsgType.TEXT else {}
            ok = (hello.get("type") == "hello" and hello.get("device") == DEVICE_ID
                  and hmac.compare_digest(str(hello.get("hmac", "")), auth_mac(self.key, nonce)))
        except Exception:
            hello, ok = {}, False
        if not ok:
            self.auth_failures += 1
            log("device auth FAILED from", request.remote)
            await ws.close(code=4401, message=b"auth")
            return ws
        if self.dev is not None and not self.dev.closed:
            await self.dev.close(code=4000, message=b"replaced")
        self.dev, self.dev_since, self.dev_info = ws, time.time(), {k: hello.get(k) for k in ("fw", "mac")}
        self.alive.reset()
        self._mood_refresh.set()
        self.pending.clear()
        if self.hp is not None:
            self.hp.reset()
        log("device connected from", request.remote, self.dev_info)
        if self.ha is not None:
            self.ha.device_online(self.dev_info)
        await ws.send_json({"type": "welcome", "mic": {"rate": MIC_RATE, "frame_samples": MIC_SAMPLES},
                            "spk": {"rate": SPK_RATE, "chunk_bytes": SPK_CHUNK_BYTES}})
        self.dual_mic.reset_audio()
        await ws.send_json({'type':'config','dual_mic':int(self.dual_mic.cfg['enabled'])})
        await self.sync_alive()
        if self.last_state:
            await ws.send_json({"type": "state", "name": self.last_state})
        try:
            async for m in ws:
                if m.type == WSMsgType.BINARY:
                    d = m.data
                    if len(d) == 3 + MIC_FRAME_BYTES and d[0] == 1:
                        self.frames_in += 1
                        if self.microphone.active=='body':self.pending.append(bytes(d[3:]))
                    elif len(d)==5123 and d[0]==4:
                        self.dual_mic.feed(d,self.gated())
                elif m.type == WSMsgType.TEXT:
                    try:
                        j = json.loads(m.data)
                    except Exception:
                        continue
                    if j.get("type") == "playback_done":
                        f = self.done.get(j.get("id"))
                        if f and not f.done():
                            f.set_result(True)
                    elif j.get("type") == "dual_mic_status":
                        self.dual_mic.ack(j)
                        if self.ha:self.ha.runtime_status()
                    elif j.get("type") == "stats":
                        self.dev_info["stats"] = j
                    elif j.get("type") == "telemetry":
                        self.sleep_telemetry=j;self.sleep_telemetry_at=time.time()
                        self.observe_alive_body(j)
                        if self.ha is not None:
                            self.ha.telemetry(j)
                    elif j.get("type") == "battery":
                        self.on_battery(j)
                    elif j.get("type") == "light":
                        w = self.light_waiter
                        if w is not None and not w.done():
                            w.set_result({k: j[k] for k in ("ok", "lux", "ch0", "ch1") if k in j})
                    elif j.get("type") == "touch":                # pet mode on the device (not forwarded to the brain yet)
                        if self.alive.flags['alive_enabled'] and self.alive.flags['emotion_memory'] and time.monotonic()-self._touch_at>30 and len(self._touch_tasks)<4:
                            self._touch_at=time.monotonic()
                            event_id='body:%s:%s:%s'%(self.alive.epoch,self.alive.generation,self.touch_events)
                            task=asyncio.create_task(self.touch_evidence(event_id));self._touch_tasks.add(task);task.add_done_callback(self._touch_tasks.discard)
                        if self.sleep:self.sleep.interact()
                        self.touch_events += 1
                        self.last_touch = {"event": str(j.get("event"))[:24], "affection": j.get("affection"), "at": time.time()}
                        log("touch:", self.last_touch["event"], "affection", j.get("affection"), "zones", j.get("z"))
        finally:
            if self.dev is ws:
                self.dev = None
                self.alive.reset()
                log("device disconnected")
                if self.ha is not None:
                    self.ha.device_offline()
                for f in self.done.values():
                    if not f.done():
                        f.set_exception(ConnectionError("device went away"))
        return ws

    # ---------------------------------------------------------------- speaker path
    @staticmethod
    async def to_pcm22(data: bytes, hdr: dict) -> bytes:
        if hdr.get("container") != "file" and hdr.get("fmt", "S16_LE") == "S16_LE" \
                and int(hdr.get("rate", SPK_RATE)) == SPK_RATE and int(hdr.get("channels", 1)) == 1:
            return data
        if hdr.get("container") == "file":
            cmd = ["ffmpeg", "-v", "error", "-i", "pipe:0"]
        else:
            fmt = FFMPEG_FMT.get(hdr.get("fmt", "S16_LE"))
            if not fmt:
                raise ValueError("unsupported sample format " + str(hdr.get("fmt")))
            cmd = ["ffmpeg", "-v", "error", "-f", fmt, "-ar", str(int(hdr.get("rate", SPK_RATE))), "-ac",
                   str(int(hdr.get("channels", 1))), "-i", "pipe:0"]
        cmd += ["-f", "s16le", "-ar", str(SPK_RATE), "-ac", "1", "pipe:1"]
        p = await asyncio.create_subprocess_exec(*cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                                 stderr=asyncio.subprocess.PIPE)
        out, err = await p.communicate(data)
        if p.returncode:
            raise ValueError("ffmpeg: " + err.decode(errors="replace")[:120])
        return out

    async def play(self, pcm: bytes, speech_context=None, wake_trace=None):
        """Send PCM (22.05 kHz mono S16) to the device and wait until it reports the audio has finished playing."""
        wake_audit(wake_trace, "play_queue_enter")
        async with self.play_lock:
            wake_audit(wake_trace, "play_queue_acquired")
            end = time.monotonic() + CFG["device_wait_s"]
            while (self.dev is None or self.dev.closed) and time.monotonic() < end:
                await asyncio.sleep(0.2)                  # the device usually reconnects within a few seconds
            dev = self.dev
            if dev is None or dev.closed:
                raise ConnectionError("Stack-chan is not connected")
            self.play_id += 1
            pid = self.play_id
            fut = asyncio.get_running_loop().create_future()
            self.done[pid] = fut
            self.playing += 1
            self._cancel(self._idle_handle)
            # The wake chime plays while the brain is in "wake" and the done chime while it is "transcribing": those are not Sunday
            # speaking, so they must not change the face (they used to flip Listening/Thinking to Speaking, then to Idle).
            is_chime = bool(wake_trace) or (not speech_context and self.brain_state in ("wake", "transcribing"))
            if self.alive.enabled and not is_chime and speech_context is None:
                self.alive.reset()
                await self.sync_alive()
            if not is_chime:
                await self.send_state("speaking")
            if not is_chime:
                try:
                    pcm, info = await asyncio.get_running_loop().run_in_executor(None, shape_playback, pcm)
                    if info:
                        log("speech shaped: peak %.1f->%.1f dBFS, rms %.1f->%.1f dBFS, gain %+.1f dB" % info)
                except Exception as e:
                    log("speech shaping skipped:", type(e).__name__, e)
            lease=self.alive.begin(speech_context,pid) if not is_chime else None
            intent_sent=False
            if lease:
                intent_sent=await self.sync_alive()
                self.schedule_alive_expiry()
            delivery=self.begin_delivery_record(speech_context,pid,intent_sent) if self.alive.enabled and not is_chime else None
            acknowledged=False
            dur = len(pcm) / (SPK_RATE * 2)
            try:
                await dev.send_json({"type": "audio_start", "id": pid, "rate": SPK_RATE, "bytes": len(pcm), "lip": 0 if is_chime else 1})   # chimes do not move the mouth
                wake_audit(wake_trace, "audio_start_sent", playback_id=pid)
                t0 = time.monotonic()
                seq = sent = 0
                while sent < len(pcm):
                    # stay at most `lead_s` ahead of real time so the device buffer never overflows on long speech
                    allowed = (time.monotonic() - t0 + CFG["lead_s"]) * SPK_RATE * 2
                    if sent > allowed:
                        await asyncio.sleep(0.02)
                        continue
                    chunk = pcm[sent:sent + SPK_CHUNK_BYTES]
                    await dev.send_bytes(b"\x02" + (seq & 0xFFFF).to_bytes(2, "big") + chunk)
                    if sent == 0:wake_audit(wake_trace, "first_pcm_sent", playback_id=pid)
                    sent += len(chunk)
                    seq += 1
                await dev.send_json({"type": "audio_end", "id": pid})
                wake_audit(wake_trace, "audio_end_sent", playback_id=pid)
                try:
                    await asyncio.wait_for(asyncio.shield(fut), max(0.0, dur - (time.monotonic() - t0)) + CFG["done_grace_s"])
                    acknowledged=True
                    wake_audit(wake_trace, "body_playback_done_received", playback_id=pid)
                except asyncio.TimeoutError:
                    self.play_timeouts += 1
                    log("no playback_done for", pid, "- delivery unknown")
                    if lease:raise TimeoutError('playback delivery unknown')
                self.plays_ok += 1
                self.last_play = {"id": pid, "seconds": round(dur, 2), "at": time.time()}
            finally:
                if delivery:
                    delivery['playback_acknowledged']=acknowledged
                    delivery['status']='playback_acknowledged' if acknowledged else 'delivery_unknown'
                    delivery['finished_at']=time.time()
                    if self._active_delivery is delivery:self._active_delivery=None
                    log('alive delivery:',json.dumps({k:delivery[k] for k in ('playback_id','request_id','proposed_expression','authorized_expression','intent_sent','status')},separators=(',',':')))
                if lease:
                    self.alive.ack(lease,pid,acknowledged)
                    await self.sync_alive()
                    self.schedule_alive_expiry()
                self.done.pop(pid, None)
                self.playing -= 1
                self.gate_until = time.monotonic() + CFG["gate_tail_s"]
                loop = asyncio.get_running_loop()
                self._cancel(self._idle_handle)
                if not self.playing:   # production emits "idle" at turn end; this is only a safety net
                    idle_token=self.alive.token()
                    self._idle_handle = loop.call_later(4.0, lambda: asyncio.ensure_future(self.guarded_idle(idle_token)))

    async def spk_client(self, reader, writer):
        try:
            hdr = json.loads((await asyncio.wait_for(reader.readline(), 5)).decode())
            trace=hdr.get("wake_trace")
            wake_audit(trace,"gateway_header_received")
            data = bytearray()
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    break
                data += chunk
                if len(data) > 64 << 20:
                    raise ValueError("too much audio")
            if not data:
                writer.write(b"OK\n")
            else:
                wake_audit(trace,"audio_received", bytes=len(data))
                pcm = await self.to_pcm22(bytes(data), hdr)
                wake_audit(trace,"conversion_done", bytes=len(pcm))
                from alive_contracts import speech_context as validate_context
                context=validate_context(hdr.get('speech_context')) if self.alive.enabled else None
                if trace:await self.play(pcm,context,wake_trace=trace)
                else:await self.play(pcm,context)
                writer.write(b"OK\n")
        except Exception as e:
            self.plays_failed += 1                       # counted once, whichever stage failed (device absent, conversion, mid-stream)
            self.last_failure = {"at": time.time(), "why": (str(e) or type(e).__name__)[:100]}
            log("play failed:", type(e).__name__, str(e)[:100])
            try:
                writer.write(("ERR %s\n" % (str(e) or type(e).__name__)).encode())
            except Exception:
                pass
        finally:
            try:
                await writer.drain()
                writer.close()
            except Exception:
                pass

    # ---------------------------------------------------------------- health
    def health(self):
        return {
            "backend": "stackchan", "ok": True, "microphone":self.microphone.status(), "sleep":self.sleep.cached if self.sleep else None, "highpass_hz": CFG["highpass_hz"], "uptime_s": round(time.time() - self.started, 1),
            "device_connected": self.dev is not None and not self.dev.closed,
            "device_since": self.dev_since, "device": self.dev_info, "auth_failures": self.auth_failures,
            "mic_subscribers": len(self.mic_clients), "frames_in": self.frames_in, "frames_out": self.frames_out,
            "silence_frames": self.silence_frames, "gated_frames": self.gated_frames,
            "mic_dropped": sum(c.dropped for c in self.mic_clients),
            "playing": self.playing > 0, "gate_active": self.gated(), "plays_ok": self.plays_ok,
            "plays_failed": self.plays_failed, "last_failure": self.last_failure, "play_timeouts": self.play_timeouts, "last_play": self.last_play,
            "dual_mic":self.dual_mic.status(),
            "display":{"received_age_ms":self.sleep_telemetry.get("ui_age"),"rendered_age_ms":self.sleep_telemetry.get("ui_draw_age"),"page":self.sleep_telemetry.get("ui_page"),"telemetry_at":self.sleep_telemetry_at},
            "face_state": self.last_state, "touch_events": self.touch_events, "last_touch": self.last_touch,
            "alive": {"enabled":self.alive.enabled,"recent_deliveries":list(self.alive_deliveries),
                      "body_observation":self.alive_body_observation,
                      "idle_policy":self.alive.idle,"idle_lease_active":self.alive.idle_expiry>time.monotonic(),
                      "note":"intent_sent is a transport result; playback acknowledgement is not a render acknowledgement. Body observations reuse existing telemetry and may miss short clips."},
        }


async def main():
    try:
        key = load_key()
    except FileNotFoundError:
        raise SystemExit("device key missing: %s (create it with: sunday-audio init-key)" % CFG["key_file"])
    hub = Hub(key)
    if os.environ.get("SA_MQTT", "1") == "1":
        try:
            from ha_mqtt import HaBridge
            hub.ha = HaBridge(asyncio.get_running_loop(), hub, os.environ.get("SA_MQTT_HOST", "127.0.0.1"),
                              int(os.environ.get("SA_MQTT_PORT", "1883")), log)
            hub.ha.start()
        except Exception as e:                     # Home Assistant integration is optional: never block voice
            log("Home Assistant bridge not started:", type(e).__name__, e)

    dev_app = web.Application()
    dev_app.router.add_get("/stackchan", hub.ws_device)
    dev_runner = web.AppRunner(dev_app)
    await dev_runner.setup()
    await web.TCPSite(dev_runner, CFG["device_host"], CFG["device_port"]).start()

    health_app = web.Application()
    health_app.router.add_get("/health", lambda r: web.json_response(hub.health()))
    async def glance_endpoint(request):
        from display_projection import validate_packet
        if request.content_length is None or request.content_length > 4096:
            return web.json_response({'error':'Invalid display payload'},status=400)
        try:
            packet=validate_packet(await request.json())
        except (ValueError,TypeError):
            return web.json_response({'error':'Invalid display payload'},status=400)
        if hub.dev is None or hub.dev.closed:
            return web.json_response({'ok':False},status=503)
        # A display-only message over the existing authenticated body connection.
        await hub.dev.send_json({'type':'glance',**packet})
        return web.json_response({'ok':True})
    health_app.router.add_post('/display',glance_endpoint)


    async def camera_capture(request):
        ok, detail = await hub.request_camera()
        return web.json_response({"ok": ok, "detail": detail}, status=200 if ok else 503)
    health_app.router.add_post("/camera/capture", camera_capture)

    async def light(request):
        return web.json_response(await hub.request_light())
    health_app.router.add_get("/light", light)

    async def battery_ep(request):
        return web.json_response(await hub.request_battery())
    health_app.router.add_get("/battery", battery_ep)

    async def volume_ep(request):
        q = request.query
        try:
            set_pct = float(q["set"]) if "set" in q else None
            delta = float(q["delta"]) if "delta" in q else None
        except ValueError:
            return web.json_response({"ok": False, "detail": "set/delta must be numbers"}, status=400)
        return web.json_response(await hub.body_volume(set_pct, delta))
    health_app.router.add_get("/volume", volume_ep)
    health_app.router.add_post("/volume", volume_ep)
    health_app.router.add_post("/alive/reply_end", hub.reply_end)
    h_runner = web.AppRunner(health_app)
    await h_runner.setup()
    await web.TCPSite(h_runner, "127.0.0.1", CFG["health_port"]).start()

    mic_srv = await asyncio.start_server(hub.mic_client, "127.0.0.1", CFG["mic_port"])
    spk_srv = await asyncio.start_server(hub.spk_client, "127.0.0.1", CFG["spk_port"])
    tasks = [asyncio.create_task(hub.feeder()), asyncio.create_task(hub.brain_events()),asyncio.create_task(hub.follow_emotional_state())]
    tasks.append(asyncio.create_task(hub.microphone.run()))
    if hub.sleep:tasks.append(asyncio.create_task(hub.sleep.run()))
    log("up: device ws :%d/stackchan  mic 127.0.0.1:%d  spk 127.0.0.1:%d  health 127.0.0.1:%d" %
        (CFG["device_port"], CFG["mic_port"], CFG["spk_port"], CFG["health_port"]))

    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    await stop.wait()
    log("stopping")
    for t in tasks:
        t.cancel()
    await hub.microphone.stop_usb()
    mic_srv.close()
    spk_srv.close()
    if hub.dev is not None:
        await hub.dev.close(code=1001, message=b"gateway stopping")
    await dev_runner.cleanup()
    await h_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
