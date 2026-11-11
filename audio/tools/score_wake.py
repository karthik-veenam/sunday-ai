"""Score recordings with the UNMODIFIED production wake model + ask the production Whisper verifier service what it hears.
Read-only: never calls wake.py's verifier path (no verdict log, no negative dataset, no Telegram).
    cd ~/sunday && ~/momo-env/bin/python ~/sunday-audio/tools/score_wake.py [--hp 120] file.wav [file2.wav ...]"""
import argparse, io, json, os, re, sys, wave
import numpy as np
import requests

sys.path.insert(0, os.path.expanduser("~/sunday"))
from wake import WakeWordDetector  # noqa: E402

cfg = json.load(open(os.path.expanduser("~/sunday/config.json")))
vcfg = json.load(open(os.path.expanduser("~/sunday/wake_verifier.json")))
THRESH, CHUNK = cfg["wakeword_threshold"], 1280


def load(path):
    with wave.open(path) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def highpass(x, fc, fs=16000):
    """2nd-order Butterworth high-pass (RBJ biquad)."""
    w0 = 2 * np.pi * fc / fs; c, s = np.cos(w0), np.sin(w0); q = 0.7071; a = s / (2 * q)
    b0, b1, b2 = (1 + c) / 2, -(1 + c), (1 + c) / 2
    a0, a1, a2 = 1 + a, -2 * c, 1 - a
    b0, b1, b2, a1, a2 = b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0
    y = np.zeros(len(x)); x1 = x2 = y1 = y2 = 0.0
    for i, v in enumerate(x.astype(np.float64)):
        o = b0 * v + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        x2, x1, y2, y1 = x1, v, y1, o
        y[i] = o
    return np.clip(y, -32768, 32767).astype(np.int16)


def norm(t): return " ".join(re.sub(r"[^a-z0-9]+", " ", t.casefold()).split())


def accepted(text):
    toks = norm(text).split()
    for alias in vcfg["aliases"]:
        w = norm(alias).split()
        if w and any(toks[i:i + len(w)] == w for i in range(len(toks) - len(w) + 1)): return alias
    return None


def whisper(pcm):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(pcm.tobytes())
    r = requests.post(vcfg["url"], files={"file": ("c.wav", buf.getvalue(), "audio/wav")},
                      data={"response_format": "json", "temperature": "0", "temperature_inc": "0", "language": "en",
                            "audio_ctx": str(vcfg.get("audio_ctx", 250))}, timeout=8)
    return r.json()["text"].strip()


def score(det, x):
    det._flush()
    n = len(x) // CHUNK
    return [det._process_chunk(x[i * CHUNK:(i + 1) * CHUNK]) for i in range(n)]


def analyse(det, label, x):
    sc = score(det, x)
    hits = [i for i, s in enumerate(sc) if s >= THRESH]
    events = []
    for i in hits:
        if not events or i - events[-1] > 20: events.append(i)       # merge frames within 1.6 s into one event
    print("  %-26s max score %.4f | frames >= %.2f: %d | wake events: %d" % (label, max(sc), THRESH, len(hits), len(events)), flush=True)
    for i in events:
        a, b = max(0, i + 1 - 20) * CHUNK, min(len(x), (i + 1 + 2) * CHUNK)
        txt = whisper(x[a:b]); m = accepted(txt)
        print("      t=%5.2fs score %.4f -> verifier hears %-34r %s" % ((i + 1) * CHUNK / 16000, sc[i], txt, "ACCEPT (%s)" % m if m else "REJECT"), flush=True)
    return len(events)


ap = argparse.ArgumentParser(); ap.add_argument("--hp", type=float, default=0); ap.add_argument("files", nargs="+")
args = ap.parse_args()
det = WakeWordDetector(cfg["wakeword_model"], THRESH)
print("model=%s threshold=%s" % (os.path.basename(os.path.dirname(cfg["wakeword_model"])), THRESH), flush=True)
for f in args.files:
    x = load(f); print(os.path.basename(f), flush=True)
    analyse(det, "as recorded", x)
    if args.hp:
        analyse(det, "high-pass %.0f Hz" % args.hp, highpass(x, args.hp))
sys.stdout.flush(); os._exit(0)
