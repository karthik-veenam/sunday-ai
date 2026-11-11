"""End-to-end tests of the gateway + shims against a simulated Stack-chan. Runs entirely in a sandbox (temp HOME, private
ports, fake 'real' binaries): it can never touch production audio, services or the AIRHUG.

    ~/sunday-audio/venv/bin/python -m unittest -v tests.test_gateway        (from ~/sunday-audio)
"""
import asyncio
import os
import socket
import struct
import sys
import tempfile
import time
import unittest
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from aiohttp import web  # noqa: E402
from fake_stackchan import FakeDevice, MIC_FRAME_BYTES  # noqa: E402

KEY = b"test-key-0123456789abcdef"
PY = sys.executable


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def frame_of(v):
    return struct.pack("<h", v) * 1280


def ramp_frames(start=1):
    i = start
    while True:
        yield frame_of(i)
        i += 1


class Sandbox:
    """A temp SUNDAY_AUDIO_HOME + gateway process on private ports."""
    def __init__(self, backend="stackchan", brain_ws="", tail="0.3", highpass="0", shape="0"):
        self.dir = tempfile.mkdtemp(prefix="sa-test-")
        os.makedirs(self.dir + "/state"); os.makedirs(self.dir + "/secrets")
        open(self.dir + "/state/backend", "w").write(backend)
        open(self.dir + "/secrets/device.key", "wb").write(KEY)
        self.ports = {k: free_port() for k in ("device", "mic", "spk", "health")}
        self.env = dict(os.environ, SUNDAY_AUDIO_HOME=self.dir, SA_DEVICE_HOST="127.0.0.1",
                        SA_DEVICE_PORT=str(self.ports["device"]), SA_MIC_PORT=str(self.ports["mic"]),
                        SA_SPK_PORT=str(self.ports["spk"]), SA_HEALTH_PORT=str(self.ports["health"]),
                        SA_BRAIN_WS=brain_ws, SA_GATE_TAIL_S=tail, SA_KEY_FILE=self.dir + "/secrets/device.key",
                        SA_HIGHPASS_HZ=highpass, SA_DEVICE_WAIT_S="0.3", SA_SHIM_RETRY_S="0.6", SA_SPK_SHAPE=shape, SA_MQTT="0")
        self.url = "ws://127.0.0.1:%d/stackchan" % self.ports["device"]
        self.proc = None

    async def __aenter__(self):
        self.log = open(self.dir + "/gateway.log", "wb")
        self.proc = await asyncio.create_subprocess_exec(PY, os.path.join(ROOT, "gateway", "gateway.py"), env=self.env,
                                                         stdout=self.log, stderr=asyncio.subprocess.STDOUT)
        for _ in range(100):
            try:
                await self.health(); return self
            except Exception:
                await asyncio.sleep(0.1)
        raise RuntimeError("gateway did not start: " + open(self.dir + "/gateway.log").read())

    async def __aexit__(self, *a):
        if self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), 5)
            except asyncio.TimeoutError:
                self.proc.kill()

    async def health(self):
        from aiohttp import ClientSession
        async with ClientSession() as s:
            async with s.get("http://127.0.0.1:%d/health" % self.ports["health"]) as r:
                return await r.json()

    def mic(self):
        return asyncio.create_subprocess_exec(os.path.join(ROOT, "shim", "arecord"), "-D", "pulse", "-r", "16000", "-c", "1",
                                              "-f", "S16_LE", "-t", "raw", "-q", env=self.env, stdout=asyncio.subprocess.PIPE,
                                              stderr=asyncio.subprocess.PIPE)

    async def aplay(self, args, data=b"", timeout=15):
        p = await asyncio.create_subprocess_exec(os.path.join(ROOT, "shim", "aplay"), *args, env=self.env,
                                                 stdin=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(p.communicate(data), timeout)
        return p.returncode, err.decode()


async def read_frames(proc, n, timeout=10):
    out = []
    for _ in range(n):
        out.append(await asyncio.wait_for(proc.stdout.readexactly(MIC_FRAME_BYTES), timeout))
    return out


def val(frame):
    return struct.unpack("<h", frame[:2])[0] if any(frame) else 0


class T(unittest.IsolatedAsyncioTestCase):
    async def test_auth_rejected_then_accepted(self):
        async with Sandbox() as gw:
            bad = FakeDevice(gw.url, KEY, bad_mac=True)
            await asyncio.wait_for(bad.run(), 5)
            self.assertEqual(bad.close_code, 4401)
            self.assertEqual((await gw.health())["auth_failures"], 1)
            self.assertFalse((await gw.health())["device_connected"])
            good = FakeDevice(gw.url, KEY); good.start()
            await asyncio.wait_for(good.connected.wait(), 5)
            h = await gw.health()
            self.assertTrue(h["device_connected"]); self.assertEqual(h["device"]["fw"], "fake-1")
            await good.stop()

    async def test_mic_path_is_bit_exact_and_ordered(self):
        async with Sandbox() as gw:
            dev = FakeDevice(gw.url, KEY, frames=ramp_frames(1)); dev.start()
            await dev.connected.wait()
            mic = await gw.mic()
            frames = await read_frames(mic, 30)
            mic.kill(); await dev.stop()
            vals = [val(f) for f in frames]
            first = next(i for i, v in enumerate(vals) if v)
            run = vals[first:]
            self.assertGreater(len(run), 20, vals)
            self.assertEqual(run, list(range(run[0], run[0] + len(run))), "frames must arrive complete, in order, no gaps")
            self.assertTrue(all(len(f) == MIC_FRAME_BYTES for f in frames))
            # exact bytes preserved
            self.assertEqual(frames[first], frame_of(run[0]))

    async def test_highpass_removes_hum_but_keeps_speech_band_and_has_no_frame_clicks(self):
        import math
        sr = 16000
        def tone(hz, amp, n0):
            return [int(amp * math.sin(2 * math.pi * hz * (n0 + i) / sr)) for i in range(1280)]
        def gen(hz, amp):
            n = 0
            while True:
                yield struct.pack("<%dh" % 1280, *tone(hz, amp, n)); n += 1280
        async def measure(hz):
            async with Sandbox(highpass="120") as gw:
                dev = FakeDevice(gw.url, KEY, frames=gen(hz, 6000)); dev.start(); await dev.connected.wait()
                mic = await gw.mic()
                frames = await read_frames(mic, 40)
                mic.kill(); await dev.stop()
                body = b"".join(frames[20:])                      # after the filter has settled
                x = struct.unpack("<%dh" % (len(body) // 2), body)
                rms = math.sqrt(sum(v * v for v in x) / len(x))
                return 20 * math.log10(max(rms, 1e-9) / (6000 / math.sqrt(2)))     # dB relative to the input RMS
        hum, voice = await measure(50), await measure(1000)
        self.assertLess(hum, -25, "50 Hz mains hum must be attenuated by >25 dB, got %.1f dB" % hum)
        self.assertGreater(voice, -0.5, "1 kHz speech band must pass unchanged, got %.1f dB" % voice)

    async def test_touch_events_from_the_device_are_counted(self):
        async with Sandbox() as gw:
            from aiohttp import ClientSession
            dev = FakeDevice(gw.url, KEY); dev.start(); await dev.connected.wait()
            await dev._ws.send_json({"type": "touch", "event": "pet_start", "affection": 0.1})
            await dev._ws.send_json({"type": "touch", "event": "pet_end", "affection": 0.0})
            await asyncio.sleep(0.4)
            h = await gw.health()
            self.assertEqual(h["touch_events"], 2); self.assertEqual(h["last_touch"]["event"], "pet_end")
            await dev.stop()

    async def test_silence_stream_without_device_keeps_micstream_alive(self):
        async with Sandbox() as gw:
            mic = await gw.mic(); t0 = time.monotonic()
            frames = await read_frames(mic, 6)
            dt = time.monotonic() - t0
            mic.kill()
            self.assertTrue(all(not any(f) for f in frames))
            self.assertTrue(0.30 < dt < 0.80, dt)  # ~80 ms cadence (first frame immediate)
            self.assertGreater((await gw.health())["silence_frames"], 4)

    async def test_speech_is_shaped_louder(self):
        """Quiet speech is made louder (bass cut, peaks to just under full scale, mild compression) without clipping; tiny sounds are untouched."""
        import math
        async with Sandbox(shape="1") as gw:
            dev = FakeDevice(gw.url, KEY); dev.start(); await dev.connected.wait()
            n = 22050
            # speech-like: 300 Hz voiced tone with a syllable envelope, quiet (peak about -15 dBFS)
            quiet = b"".join(struct.pack("<h", int(6000 * max(0.0, math.sin(2 * math.pi * 3 * i / 22050)) * math.sin(2 * math.pi * 300 * i / 22050))) for i in range(n))
            rc, err = await gw.aplay(["-D", "pulse", "-r", "22050", "-f", "S16_LE", "-c", "1", "-t", "raw", "-q"], quiet)
            self.assertEqual(rc, 0, err)
            got = bytes(dev.rx_pcm[1])
            self.assertEqual(len(got), len(quiet), "shaping must not change the length")
            a = struct.unpack("<%dh" % n, quiet); b = struct.unpack("<%dh" % n, got)
            rms = lambda v: math.sqrt(sum(x * x for x in v) / len(v))
            self.assertGreater(rms(b), 2.5 * rms(a), "quiet speech should be clearly louder")
            self.assertLess(max(abs(x) for x in b), 32767, "no clipping")
            self.assertGreater(max(abs(x) for x in b), 28000, "peaks should reach close to full scale")
            tiny = b"".join(struct.pack("<h", (i * 37) % 2000 - 1000) for i in range(1000))   # 45 ms: a click/blip, left alone
            rc, err = await gw.aplay(["-D", "pulse", "-r", "22050", "-f", "S16_LE", "-c", "1", "-t", "raw", "-q"], tiny)
            self.assertEqual(rc, 0, err)
            self.assertEqual(bytes(dev.rx_pcm[2]), tiny)
            await dev.stop()

    async def test_speaker_raw_tts_roundtrip(self):
        async with Sandbox() as gw:
            dev = FakeDevice(gw.url, KEY); dev.start(); await dev.connected.wait()
            pcm = b"".join(struct.pack("<h", (i * 37) % 20000 - 10000) for i in range(22050))   # 1.0 s @ 22.05 kHz
            t0 = time.monotonic()
            rc, err = await gw.aplay(["-D", "pulse", "-r", "22050", "-f", "S16_LE", "-c", "1", "-t", "raw", "-q"], pcm)
            dt = time.monotonic() - t0
            self.assertEqual(rc, 0, err)
            got = bytes(dev.rx_pcm[1])
            self.assertEqual(got, pcm, "speaker audio must reach the device bit-exact")
            self.assertGreater(dt, 0.9, "aplay must block until the device finished playing (like the real aplay)")
            self.assertIn("speaking", dev.states)
            h = await gw.health(); self.assertEqual(h["plays_ok"], 1)
            await dev.stop()

    async def test_wav_chime_is_converted_to_device_format(self):
        async with Sandbox() as gw:
            dev = FakeDevice(gw.url, KEY); dev.start(); await dev.connected.wait()
            path = gw.dir + "/chime.wav"
            with wave.open(path, "wb") as w:                 # 16 kHz STEREO, like a typical chime file
                w.setnchannels(2); w.setsampwidth(2); w.setframerate(16000)
                w.writeframes(struct.pack("<hh", 8000, -8000) * 8000)   # 0.5 s
            rc, err = await gw.aplay(["-D", "plughw:2,0", "-q", path])
            self.assertEqual(rc, 0, err)
            n = len(dev.rx_pcm[1])
            self.assertAlmostEqual(n, int(0.5 * 22050) * 2, delta=int(0.5 * 22050 * 2 * 0.03))
            await dev.stop()

    async def test_mic_is_gated_while_speaking_and_resumes_after(self):
        async with Sandbox(tail="0.3") as gw:
            dev = FakeDevice(gw.url, KEY, frames=ramp_frames(100), half_duplex=False); dev.start(); await dev.connected.wait()
            mic = await gw.mic()
            await read_frames(mic, 12)            # let the stream settle, all real audio
            pcm = struct.pack("<h", 3000) * 22050  # 1.0 s
            play = asyncio.create_task(gw.aplay(["-D", "pulse", "-r", "22050", "-f", "S16_LE", "-c", "1", "-t", "raw", "-q"], pcm))
            timeline, t0 = [], time.monotonic()
            while not play.done() or time.monotonic() - t0 < 4:
                f = (await read_frames(mic, 1))[0]
                timeline.append((time.monotonic(), val(f) != 0))
                if play.done() and time.monotonic() - (t0 + 1.5) > 1.0 and timeline[-1][1]:
                    break
            rc, _ = await play
            end = t0 + 1.0
            during = [nz for t, nz in timeline if t0 + 0.4 < t < t0 + 0.9]
            after = [nz for t, nz in timeline if t > end + 1.2]
            mic.kill(); h = await gw.health(); await dev.stop()
            self.assertEqual(rc, 0)
            self.assertTrue(during and not any(during), "mic frames must be silence while Sunday speaks: %s" % during)
            self.assertTrue(after and all(after[-3:]), "mic must resume after the tail: %s" % after)
            self.assertGreater(h["gated_frames"], 5)

    async def test_device_loss_and_reconnect(self):
        async with Sandbox() as gw:
            a = FakeDevice(gw.url, KEY, frames=ramp_frames(1)); a.start(); await a.connected.wait()
            mic = await gw.mic()
            self.assertTrue(any(val(f) for f in await read_frames(mic, 10)))
            await a.stop()
            await asyncio.sleep(0.5)
            tail = await read_frames(mic, 8)
            self.assertTrue(not any(any(f) for f in tail[-5:]), "offline => silence, stream stays alive")
            self.assertIsNone(mic.returncode)
            t0 = time.monotonic()
            rc, err = await gw.aplay(["-D", "pulse", "-r", "22050", "-f", "S16_LE", "-c", "1", "-t", "raw", "-q"], bytes(4410))
            # production treats a failing aplay as fatal, so an undeliverable utterance is dropped (exit 0), logged, and counted
            self.assertEqual(rc, 0); self.assertIn("DROPPED", err); self.assertIn("not connected", err); self.assertLess(time.monotonic() - t0, 3)
            self.assertEqual((await gw.health())["plays_failed"], 1)
            gw.env["SA_STRICT_PLAYBACK"] = "1"
            rc, err = await gw.aplay(["-D", "pulse", "-r", "22050", "-f", "S16_LE", "-c", "1", "-t", "raw", "-q"], bytes(4410))
            self.assertEqual(rc, 1, "strict mode still reports failure for tests/diagnostics")
            gw.env.pop("SA_STRICT_PLAYBACK")
            b = FakeDevice(gw.url, KEY, frames=ramp_frames(500)); b.start(); await b.connected.wait()
            # nobody was reading the pipe while the playback attempts above ran, so skip the stale buffered silence first
            resumed = False
            for _ in range(120):
                if val((await read_frames(mic, 1))[0]) >= 500:
                    resumed = True
                    break
            mic.kill(); await b.stop()
            self.assertTrue(resumed, "audio resumes after reconnect")

    async def test_face_state_follows_brain_events(self):
        events = [{"type": "wake"}, {"type": "transcribing"}, {"type": "thinking"}, {"type": "idle"}]
        clients = []

        async def brain_ws(request):
            ws = web.WebSocketResponse(); await ws.prepare(request); clients.append(ws)
            async for _ in ws:
                pass
            return ws
        app = web.Application(); app.router.add_get("/ws", brain_ws)
        runner = web.AppRunner(app); await runner.setup()
        port = free_port(); await web.TCPSite(runner, "127.0.0.1", port).start()
        try:
            async with Sandbox(brain_ws="ws://127.0.0.1:%d/ws" % port) as gw:
                dev = FakeDevice(gw.url, KEY); dev.start(); await dev.connected.wait()
                for _ in range(50):
                    if clients: break
                    await asyncio.sleep(0.1)
                await clients[0].send_json(events[0]); await asyncio.sleep(1.8)      # wake, then auto "listening"
                for e in events[1:]:
                    await clients[0].send_json(e); await asyncio.sleep(0.2)
                await asyncio.sleep(0.3)
                self.assertEqual(dev.states, ["wake", "listening", "thinking", "thinking", "idle"])
                await dev.stop()
        finally:
            await runner.cleanup()

    async def test_shims_pass_through_unless_exact_stackchan_call(self):
        d = tempfile.mkdtemp(prefix="sa-shim-")
        os.makedirs(d + "/state")
        for name in ("arecord", "aplay"):
            open(f"{d}/real_{name}", "w").write('#!/bin/sh\necho "REAL %s $@"\ncat >/dev/null 2>&1 </dev/null\n' % name)
            os.chmod(f"{d}/real_{name}", 0o755)
        base = dict(os.environ, SUNDAY_AUDIO_HOME=d, SUNDAY_AUDIO_REAL_ARECORD=d + "/real_arecord",
                    SUNDAY_AUDIO_REAL_APLAY=d + "/real_aplay", SA_MIC_PORT="1", SA_SPK_PORT="1")

        async def run(shim, backend, args):
            open(d + "/state/backend", "w").write(backend)
            p = await asyncio.create_subprocess_exec(os.path.join(ROOT, "shim", shim), *args, env=base,
                                                     stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                                                     stderr=asyncio.subprocess.PIPE)
            out, err = await asyncio.wait_for(p.communicate(), 10)
            return p.returncode, out.decode().strip(), err.decode().strip()
        mic = ["-D", "pulse", "-r", "16000", "-c", "1", "-f", "S16_LE", "-t", "raw", "-q"]
        tts = ["-D", "pulse", "-r", "22050", "-f", "S16_LE", "-c", "1", "-t", "raw", "-q"]
        # airhug backend: identical arguments reach the real binary, for everything
        self.assertEqual(await run("arecord", "airhug", mic), (0, "REAL arecord " + " ".join(mic), ""))
        self.assertEqual(await run("aplay", "airhug", tts), (0, "REAL aplay " + " ".join(tts), ""))
        # missing / garbage state => airhug
        open(d + "/state/backend", "w").write("garbage")
        for sh, a in (("arecord", mic), ("aplay", tts)):
            rc, out, _ = await run(sh, "garbage", a); self.assertIn("REAL", out)
        # stackchan backend but NOT the plain capture/playback => still the real binary (never silently swallowed)
        for args in (["-l"], ["-L"], ["-D", "hw:3,0", "-d", "5", "x.wav"], ["-D", "pulse", "-r", "44100", "-c", "2", "-f", "S16_LE", "-t", "raw", "-q"]):
            rc, out, _ = await run("arecord", "stackchan", args); self.assertEqual(out, "REAL arecord " + " ".join(args), args)
        for args in (["-l"], ["-L"], ["--list-devices"], ["-d", "3", "-D", "pulse", "x.wav"]):
            rc, out, _ = await run("aplay", "stackchan", args); self.assertEqual(out, "REAL aplay " + " ".join(args), args)
        # stackchan backend + exact mic/tts call + gateway absent => clean failure (never falls back to the AIRHUG)
        rc, out, err = await run("arecord", "stackchan", mic); self.assertEqual((rc, out), (1, "")); self.assertIn("not reachable", err)
        rc, out, err = await run("aplay", "stackchan", tts); self.assertEqual((rc, out), (0, "")); self.assertIn("DROPPED", err); self.assertIn("not reachable", err)


if __name__ == "__main__":
    unittest.main()
