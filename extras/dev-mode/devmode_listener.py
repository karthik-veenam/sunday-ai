"""Sunday Dev Mode mic listener: AIRHUG mic -> ElevenLabs Scribe realtime -> bridge /api/voice.

Run only by systemd (sunday-devmode-listen.service), which the bridge starts and stops. While it runs it listens
continuously; the bridge handles "end dev mode" / "stop listening". Standalone: does not touch ~/sunday or the
production voice loop. Uses PipeWire (parec), so it shares the mic with other readers.

Test without a mic:  devmode_listener.py --wav some_16k_mono.wav
"""
import argparse
import asyncio
import base64
import json
import logging
import os
import sys
import time
from collections import deque
from pathlib import Path

import aiohttp
import numpy as np

HERE = Path(__file__).resolve().parent
BRIDGE = os.environ.get("DEVMODE_BRIDGE", "http://127.0.0.1:8775")
SOURCE = os.environ.get("DEVMODE_SOURCE", "alsa_input.usb-Generic_AIRHUG_21_AIRHUG_21-00.analog-stereo")
HA_CONFIG_ENTRIES = Path("/home/pi/homeassistant/.storage/core.config_entries")
STT_URL = "wss://api.elevenlabs.io/v1/speech-to-text/realtime"
STT_PARAMS = {"model_id": "scribe_v2_realtime", "audio_format": "pcm_16000", "language_code": "eng",
              "commit_strategy": "manual", "keyterms": "Sunday", "include_timestamps": "false"}

RATE = 16000
CHUNK = 1600                    # 100 ms of 16-bit mono
SPEECH_RMS = float(os.environ.get("DEVMODE_SPEECH_RMS", "300"))   # same thresholds the production loop uses
SILENCE_RMS = float(os.environ.get("DEVMODE_SILENCE_RMS", "150"))
END_SILENCE_S = 1.0             # this much quiet ends an utterance
MAX_UTTERANCE_S = 40.0
PREROLL_CHUNKS = 4              # keep 400 ms before speech starts so first words aren't clipped
MIN_CHARS = 2

log = logging.getLogger("devmode-listen")


def eleven_key():
    entries = json.loads(HA_CONFIG_ENTRIES.read_text())["data"]["entries"]
    return next(e["data"]["api_key"] for e in entries if e["domain"] == "elevenlabs")


class Bridge:
    def __init__(self, session):
        self.session = session
        self.token = (HERE / "token").read_text().strip()

    async def post(self, text, final):
        try:
            async with self.session.post(f"{BRIDGE}/api/voice", params={"t": self.token},
                                         json={"text": text, "final": final},
                                         timeout=aiohttp.ClientTimeout(total=15)) as r:
                return await r.json()
        except Exception as e:
            log.warning("bridge post failed: %s", e)
            return None


async def transcribe(chunks, key, session, bridge):
    """One utterance: stream chunks (an asyncio.Queue; None = end) to Scribe, return the committed text."""
    committed, final = [], asyncio.get_running_loop().create_future()
    last_partial = [0.0]
    async with session.ws_connect(STT_URL, params=STT_PARAMS, headers={"xi-api-key": key}, timeout=5,
                                  receive_timeout=20, max_msg_size=1024 * 1024) as ws:
        first = await ws.receive_json(timeout=5)
        if first.get("message_type") != "session_started":
            raise RuntimeError("scribe rejected session: %s" % first.get("message_type"))
        sent_commit = False

        async def receive():
            async for m in ws:
                if m.type != aiohttp.WSMsgType.TEXT:
                    if m.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                        break
                    continue
                ev = m.json()
                kind = ev.get("message_type")
                if kind == "partial_transcript" and ev.get("text") and time.monotonic() - last_partial[0] > 0.4:
                    last_partial[0] = time.monotonic()
                    await bridge.post(ev["text"].strip(), False)
                elif kind == "committed_transcript":
                    committed.append(ev.get("text", "").strip())
                    if sent_commit and not final.done():
                        final.set_result(" ".join(t for t in committed if t).strip())
                elif kind and (kind.endswith("_error") or kind in ("error", "quota_exceeded", "rate_limited", "auth_error")):
                    raise RuntimeError("scribe " + kind)
            if not final.done():
                final.set_exception(RuntimeError("scribe closed before commit"))

        rx = asyncio.create_task(receive())
        try:
            while True:
                item = await chunks.get()
                if item is None:
                    break
                await ws.send_json({"message_type": "input_audio_chunk", "sample_rate": RATE,
                                    "audio_base_64": base64.b64encode(item).decode()})
            sent_commit = True
            await ws.send_json({"message_type": "input_audio_chunk", "sample_rate": RATE, "audio_base_64": "", "commit": True})
            return await asyncio.wait_for(asyncio.shield(final), 10)
        finally:
            rx.cancel()
            await asyncio.gather(rx, return_exceptions=True)
            if final.done() and not final.cancelled():
                final.exception()


async def utterance_task(chunks, key, session, bridge):
    try:
        text = await transcribe(chunks, key, session, bridge)
    except Exception as e:
        log.warning("transcribe failed: %s", e)
        return
    if text and len(text) >= MIN_CHARS and any(c.isalnum() for c in text):
        log.info("heard: %s", text)
        await bridge.post(text, True)


async def mic_chunks(wav):
    """Yield 100 ms int16 numpy arrays from the PipeWire mic (or a wav file in test mode)."""
    if wav:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-loglevel", "error", "-re", "-i", wav, "-f", "s16le", "-ar", str(RATE), "-ac", "1", "pipe:1",
            stdout=asyncio.subprocess.PIPE)
    else:
        proc = await asyncio.create_subprocess_exec(
            "parec", "-d", SOURCE, "--format=s16le", f"--rate={RATE}", "--channels=1", "--latency-msec=100",
            stdout=asyncio.subprocess.PIPE)
    try:
        while True:
            data = await proc.stdout.readexactly(CHUNK * 2)
            yield np.frombuffer(data, dtype=np.int16)
    except asyncio.IncompleteReadError:
        return
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


async def main(wav):
    key = eleven_key()
    async with aiohttp.ClientSession() as session:
        bridge = Bridge(session)
        pre = deque(maxlen=PREROLL_CHUNKS)
        queue, task, quiet, dur = None, None, 0.0, 0.0
        tasks = set()
        log.info("listening on %s", "wav " + wav if wav else SOURCE)
        async for chunk in mic_chunks(wav):
            rms = float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2)))
            raw = chunk.tobytes()
            if queue is None:
                pre.append(raw)
                if rms >= SPEECH_RMS:
                    queue = asyncio.Queue()
                    for item in pre:
                        queue.put_nowait(item)
                    pre.clear()
                    quiet = dur = 0.0
                    task = asyncio.create_task(utterance_task(queue, key, session, bridge))
                    tasks.add(task)
                    task.add_done_callback(tasks.discard)
                continue
            queue.put_nowait(raw)
            dur += CHUNK / RATE
            quiet = quiet + CHUNK / RATE if rms < SILENCE_RMS else 0.0
            if quiet >= END_SILENCE_S or dur >= MAX_UTTERANCE_S:
                queue.put_nowait(None)
                queue = None
        if queue is not None:
            queue.put_nowait(None)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", help="test mode: read this file instead of the mic")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    try:
        asyncio.run(main(a.wav))
    except KeyboardInterrupt:
        sys.exit(0)
