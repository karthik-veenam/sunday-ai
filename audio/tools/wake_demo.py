"""Standalone wake-word demo for the Stack-chan. NOT part of production and not connected to the brain / Home Assistant.

  Stack-chan mic --ws--> gateway --127.0.0.1:8771--> [this script] --> production wake model (unmodified, same threshold/refractory)
                                                                   --> Whisper verifier decision (strict: retry once, then reject)
  on an accepted "Sunday": the face reacts (expression), and 2.0 s later it is reset to idle.

Writes nothing to production: no verdict log, no negative dataset, no Telegram, no feedback captures.
    cd ~/sunday && ~/momo-env/bin/python ~/sunday-audio/tools/wake_demo.py [--reaction Surprised] [--hold 2.0] [--max-minutes 20]
"""
import argparse, asyncio, collections, io, json, os, re, sys, time, wave
import numpy as np
import requests

sys.path.insert(0, os.path.expanduser("~/sunday"))
from wake import WakeWordDetector, WakeVerifier  # noqa: E402  (production, unmodified)

ap = argparse.ArgumentParser()
ap.add_argument("--reaction", default="Surprised", help="face expression on wake (one of the ten presets)")
ap.add_argument("--hold", type=float, default=2.0, help="seconds before the face is reset to idle")
ap.add_argument("--device", default="http://192.168.88.17")
ap.add_argument("--device-fallback", default="http://sunday-face.local")
ap.add_argument("--mic-port", type=int, default=8771)
ap.add_argument("--spk-port", type=int, default=8772)
ap.add_argument("--no-chime", action="store_true")
ap.add_argument("--no-early-face", action="store_true", help="wait for the verifier before changing the face (slower, no false flashes)")
ap.add_argument("--max-minutes", type=float, default=20)
args = ap.parse_args()

cfg = json.load(open(os.path.expanduser("~/sunday/config.json")))
vcfg = json.load(open(os.path.expanduser("~/sunday/wake_verifier.json")))
CHUNK = 1280


def log(*a): print(time.strftime("%H:%M:%S"), *a, flush=True)


class NetMic:
    """Just enough of production's MicStream for WakeWordDetector: read(), drain(), _feedback_buffer."""
    def __init__(self):
        self.q = asyncio.Queue(maxsize=200)
        self._feedback_buffer = collections.deque(maxlen=150)
        self.connected = False
        self.frames = 0

    async def read(self): return await self.q.get()

    def drain(self):
        while not self.q.empty():
            try: self.q.get_nowait()
            except Exception: break

    async def pump(self):
        while True:
            try:
                r, w = await asyncio.open_connection("127.0.0.1", args.mic_port)
                self.connected = True; log("[demo] connected to the gateway microphone stream")
                while True:
                    raw = await r.readexactly(CHUNK * 2)
                    a = np.frombuffer(raw, dtype=np.int16).copy()
                    self._feedback_buffer.append((time.monotonic(), a))
                    self.frames += 1
                    if self.q.full(): self.q.get_nowait()
                    self.q.put_nowait(a)
            except Exception as e:
                if self.connected: log("[demo] gateway stream lost (%s); retrying" % type(e).__name__)
                self.connected = False
                await asyncio.sleep(1)


def norm(t): return " ".join(re.sub(r"[^a-z0-9]+", " ", t.casefold()).split())


def alias_match(text):
    toks = norm(text).split()
    for alias in vcfg["aliases"]:
        w = norm(alias).split()
        if w and any(toks[i:i + len(w)] == w for i in range(len(toks) - len(w) + 1)): return alias
    return None


def whisper(pcm):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(np.asarray(pcm, dtype="<i2").tobytes())
    r = requests.post(vcfg["url"], files={"file": ("c.wav", buf.getvalue(), "audio/wav")},
                      data={"response_format": "json", "temperature": "0", "temperature_inc": "0", "language": "en",
                            "audio_ctx": str(vcfg.get("audio_ctx", 250))}, timeout=6)
    r.raise_for_status()
    return r.json()["text"].strip()


stats = {"candidates": 0, "accepted": 0, "rejected": 0}
early = {"t": None}


async def verify(mic, audio, score, event_id, mode):
    """Replacement for WakeVerifier.verify: same window + same decision rule, but no logging and no dataset writes."""
    stats["candidates"] += 1
    t_in = time.monotonic()
    early["t"] = t_in
    if not args.no_early_face:
        asyncio.create_task(asyncio.to_thread(call, "POST", "/expression?name=" + args.reaction))   # instant acknowledgement
    stamp = next((t for t, a in reversed(mic._feedback_buffer) if a is audio), t_in)
    lag_ms = 1000 * (t_in - stamp)                     # how stale the trigger frame already is when we get to process it
    qdepth = mic.q.qsize()                             # frames waiting behind it (>0 means the detector is falling behind real time)
    await asyncio.sleep(vcfg.get("post_s", 0.16) + 0.02)
    pcm = WakeVerifier.window(mic, audio, vcfg.get("pre_s", 1.6), vcfg.get("post_s", 0.16))
    text, err = None, None
    for attempt in range(2):                    # strict: retry the same audio once, then reject
        try:
            text = await asyncio.to_thread(whisper, pcm); err = None; break
        except Exception as e:
            err = type(e).__name__
    w_ms = 1000 * (time.monotonic() - t_in) - 1000 * (vcfg.get("post_s", 0.16) + 0.02)
    m = alias_match(text) if text is not None else None
    if m: stats["accepted"] += 1
    else:
        stats["rejected"] += 1
        if not args.no_early_face:
            asyncio.create_task(asyncio.to_thread(call, "POST", "/state?name=idle"))              # verifier said no: undo the reaction
    log("[demo] candidate #%d score %.4f | detector lag %.0f ms, backlog %d frames | whisper %.0f ms -> heard %s%s" % (
        stats["candidates"], score, lag_ms, qdepth, w_ms, ("%r" % text) if text is not None else "ERROR " + str(err),
        ("  => ACCEPT (%s)" % m) if m else "  => reject"))
    return {"accepted": bool(m), "record": {}}


CHIME = cfg.get("awake_sound") or os.path.expanduser("~/sunday/awake.wav")   # Sunday's own wake chime


async def play_chime():
    """Send the chime through the gateway speaker socket, exactly as the aplay shim would (the gateway converts it to the device format,
    blocks until the Stack-chan reports it finished, and silences the mic meanwhile so Sunday cannot hear its own chime)."""
    t0 = time.monotonic()
    try:
        data = open(CHIME, "rb").read()
        r, w = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", args.spk_port), 3)
        w.write((json.dumps({"container": "file"}) + "\n").encode()); w.write(data); await w.drain(); w.write_eof()
        reply = (await asyncio.wait_for(r.readline(), 15)).strip()
        w.close()
        log("[demo] chime %s in %.0f ms%s" % ("played" if reply == b"OK" else "FAILED: " + reply.decode(), 1000 * (time.monotonic() - t0),
            " (includes the device finishing playback)" if reply == b"OK" else ""))
    except Exception as e:
        log("[demo] chime FAILED: %s %s" % (type(e).__name__, e))


def call(method, path):
    for base in (args.device, args.device_fallback):
        try:
            r = requests.request(method, base + path, timeout=2.5)
            return r.status_code
        except Exception:
            continue
    return None


async def main():
    mic = NetMic()
    det = WakeWordDetector(cfg["wakeword_model"], cfg["wakeword_threshold"])
    det.verifier.verify = verify
    det.feedback = None
    asyncio.create_task(mic.pump())

    async def heartbeat():
        last = 0
        while True:
            await asyncio.sleep(30)
            log("[demo] stream ok: %d frames in 30 s (ideal 375), backlog %d" % (mic.frames - last, mic.q.qsize())); last = mic.frames
    asyncio.create_task(heartbeat())
    log("[demo] model=%s threshold=%s | reaction=%s for %.1fs then idle | auto-stop after %.0f min" %
        (os.path.basename(os.path.dirname(cfg["wakeword_model"])), cfg["wakeword_threshold"], args.reaction, args.hold, args.max_minutes))
    end = time.monotonic() + args.max_minutes * 60
    n = 0
    while time.monotonic() < end:
        try:
            await asyncio.wait_for(det.wait_for_wake_word(mic), timeout=max(1.0, end - time.monotonic()))
        except asyncio.TimeoutError:
            break
        n += 1
        t0 = time.monotonic()
        if not args.no_chime:
            asyncio.create_task(play_chime())          # chime and face change happen together, neither waits for the other
        code = await asyncio.to_thread(call, "POST", "/expression?name=" + args.reaction)
        since = time.monotonic() - (early["t"] or t0)
        log("[demo] WAKE #%d verified %.2fs after the model fired -> face '%s' (device answered %s in %.0f ms); reset in %.1fs" % (
            n, since, args.reaction, code, 1000 * (time.monotonic() - t0), max(0.3, args.hold - since)))
        await asyncio.sleep(max(0.3, args.hold - since))
        code = await asyncio.to_thread(call, "POST", "/state?name=idle")
        log("[demo] face reset to idle (device answered %s)" % code)
    log("[demo] finished: %d wakes | %s" % (n, stats))
    sys.stdout.flush(); os._exit(0)

asyncio.run(main())
