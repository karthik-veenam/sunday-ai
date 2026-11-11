"""Isolated end-to-end proof that the UNMODIFIED production audio-input code works through shim + gateway:
   simulated Stack-chan --ws--> gateway --tcp--> shim/arecord --stdout--> production MicStream --> production WakeWordDetector
Uses real wake-event captures from ~/sunday-wake-feedback (read-only) that the production model fires on.
Sandbox only: private ports, temp HOME, verifier + feedback capture disabled (no production logs/datasets written, no brain, no HA).

    cd ~/sunday && ~/momo-env/bin/python ~/sunday-audio/tests/e2e_real_modules.py [N_CLIPS]
"""
import asyncio, glob, json, os, socket, subprocess, sys, tempfile, time
import numpy as np

SA = os.path.expanduser("~/sunday-audio")
sys.path.insert(0, os.path.expanduser("~/sunday")); sys.path.insert(0, SA + "/tools")
from fake_stackchan import FakeDevice, MIC_FRAME_BYTES      # noqa: E402
from mic import MicStream                                    # noqa: E402  (production, unmodified)
from wake import WakeWordDetector                            # noqa: E402  (production, unmodified)

cfg = json.load(open(os.path.expanduser("~/sunday/config.json")))
MODEL, THRESH = cfg["wakeword_model"], cfg["wakeword_threshold"]
KEY = b"e2e-test-key-0123456789abcdef"


def port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def load_pcm(path):
    return subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ar", "16000", "-ac", "1", "-f", "s16le", "-"], capture_output=True).stdout


def to_frames(pcm):
    pcm = pcm[: len(pcm) // MIC_FRAME_BYTES * MIC_FRAME_BYTES]
    return [pcm[i:i + MIC_FRAME_BYTES] for i in range(0, len(pcm), MIC_FRAME_BYTES)]


def first_trigger(det, frames):
    """index of the first frame whose score reaches the production threshold (None if never), and the max score"""
    det._flush(); best, first = 0.0, None
    for i, f in enumerate(frames):
        s = det._process_chunk(np.frombuffer(f, dtype=np.int16)); best = max(best, s)
        if first is None and s >= THRESH: first = i
    return first, best


async def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    det = WakeWordDetector(MODEL, THRESH)
    # Replace the verifier entirely (works for the old optional AND the new mandatory verifier): no whisper call, no verdict
    # log line, no negative-dataset write, no Telegram. This test must never write to production state.
    async def _no_verifier(*a, **k): return {"accepted": True, "record": {}}
    det.verifier.verify = _no_verifier
    det.verifier.config = lambda: {"mode": "off"}
    det.feedback = None
    guard_files = [os.path.expanduser("~/sunday-wake-verifier/verdicts.jsonl")]
    guard_before = [(os.path.getsize(f), sum(1 for _ in open(f))) for f in guard_files]
    picks = []
    for clip in sorted(glob.glob(os.path.expanduser("~/sunday-wake-feedback/*.wav"))):
        frames = to_frames(load_pcm(clip))
        first, best = first_trigger(det, frames)
        if first is not None:
            picks.append((os.path.basename(clip)[:8], frames, first, best))
        if len(picks) >= n: break
    print("model=%s threshold=%s  real triggering clips=%d" % (os.path.basename(os.path.dirname(MODEL)), THRESH, len(picks)), flush=True)

    home = tempfile.mkdtemp(prefix="sa-e2e-"); os.makedirs(home + "/state"); os.makedirs(home + "/secrets")
    open(home + "/state/backend", "w").write("stackchan"); open(home + "/secrets/device.key", "wb").write(KEY)
    ports = {k: port() for k in ("device", "mic", "spk", "health")}
    env = dict(os.environ, SUNDAY_AUDIO_HOME=home, SA_DEVICE_HOST="127.0.0.1", SA_DEVICE_PORT=str(ports["device"]), SA_MIC_PORT=str(ports["mic"]),
               SA_SPK_PORT=str(ports["spk"]), SA_HEALTH_PORT=str(ports["health"]), SA_BRAIN_WS="", SA_KEY_FILE=home + "/secrets/device.key")
    gw = subprocess.Popen([SA + "/venv/bin/python", SA + "/gateway/gateway.py"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ok, extras = 0, []
    try:
        await asyncio.sleep(1.2)
        os.environ.update({k: env[k] for k in ("SUNDAY_AUDIO_HOME", "SA_MIC_PORT")})
        os.environ["PATH"] = SA + "/shim:" + os.environ["PATH"]          # exactly what the systemd drop-in does for sunday.service
        mic = MicStream("pulse"); mic.start(asyncio.get_event_loop())     # unmodified production class; it spawns `arecord` => our shim
        for name, frames, first, best in picks:
            silence = [bytes(MIC_FRAME_BYTES)] * 30                       # 2.4 s of quiet so the detector is settled when the clip starts
            dev = FakeDevice("ws://127.0.0.1:%d/stackchan" % ports["device"], KEY, frames=iter(silence + frames))
            dev.start(); await dev.connected.wait()
            t0 = time.monotonic()
            try:
                await asyncio.wait_for(det.wait_for_wake_word(mic), 30)
                hit = time.monotonic() - t0
            except asyncio.TimeoutError:
                hit = None
            await dev.stop(); await asyncio.sleep(0.3)
            expected = (30 + first + 1) * 0.08                            # when an ideal, zero-latency path would trigger
            if hit:
                extras.append(hit - expected); ok += 1
                print("%s  direct max score %.4f | via shim+gateway: DETECTED  +%.2fs (ideal %.2fs, path adds %+.0f ms)" % (name, best, hit, expected, 1000 * (hit - expected)), flush=True)
            else:
                print("%s  direct max score %.4f | via shim+gateway: NOT DETECTED" % (name, best), flush=True)
        print("RESULT: %d/%d real wake events detected through the Stack-chan path by the unmodified production model" % (ok, len(picks)), flush=True)
        if extras: print("path latency vs ideal: min %.0f ms, max %.0f ms (includes 80 ms frame cadence + ~160 ms jitter buffer + transport)" % (1000 * min(extras), 1000 * max(extras)), flush=True)
    finally:
        gw.terminate()
    after = [(os.path.getsize(f), sum(1 for _ in open(f))) for f in guard_files]
    print("production verdict log lines before/after: %s -> %s  (%s)" % (guard_before[0][1], after[0][1], "UNCHANGED" if guard_before == after else "CHANGED!"), flush=True)
    sys.stdout.flush(); os._exit(0)

asyncio.run(main())
