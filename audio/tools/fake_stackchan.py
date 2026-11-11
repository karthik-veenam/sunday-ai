#!/usr/bin/env python3
"""A simulated Stack-chan: speaks the real device protocol so the gateway can be tested end to end without hardware.
It is also the reference for the firmware client.

  fake_stackchan.py --url ws://127.0.0.1:8770/stackchan --key-file KEY [--wav mic.wav] [--out spk.wav]
"""
import argparse
import asyncio
import hashlib
import hmac
import json
import os
import struct
import sys
import wave

from aiohttp import ClientSession, WSMsgType

MIC_SAMPLES, MIC_FRAME_BYTES = 1280, 2560


def mac_for(key: bytes, nonce: str) -> str:
    return hmac.new(key, (nonce + "|stackchan").encode(), hashlib.sha256).hexdigest()


class FakeDevice:
    def __init__(self, url, key: bytes, frames=None, realtime_playback=True, fw="fake-1", mac="aa:bb:cc:00:00:01", bad_mac=False,
                 half_duplex=True):
        self.url, self.key, self.fw, self.mac = url, key, fw, mac
        self.half_duplex = half_duplex   # real hardware cannot record while it plays; tests can turn this off
        self.t_start, self.expect = {}, {}
        self.frames = frames if frames is not None else self._silence()
        self.realtime_playback = realtime_playback
        self.bad_mac = bad_mac
        self.mic_enabled = True
        self.sent_frames = 0
        self.rx_pcm = {}          # audio id -> bytearray
        self.rx_rate = {}
        self.states = []
        self.events = []
        self.close_code = None
        self.connected = asyncio.Event()
        self._task = None
        self._ws = None

    @staticmethod
    def _silence():
        while True:
            yield bytes(MIC_FRAME_BYTES)

    async def run(self):
        async with ClientSession() as s:
            async with s.ws_connect(self.url, heartbeat=5) as ws:
                self._ws = ws
                ch = json.loads((await ws.receive()).data)
                mac = mac_for(self.key, ch["nonce"]) if not self.bad_mac else "00" * 32
                await ws.send_json({"type": "hello", "device": "stackchan", "fw": self.fw, "mac": self.mac, "hmac": mac})
                streamer = None
                playing_ids = {}
                try:
                    async for m in ws:
                        if m.type == WSMsgType.TEXT:
                            j = json.loads(m.data)
                            self.events.append(j["type"])
                            if j["type"] == "welcome":
                                self.connected.set()
                                streamer = asyncio.create_task(self._stream(ws))
                            elif j["type"] == "state":
                                self.states.append(j["name"])
                            elif j["type"] == "audio_start":
                                self.rx_pcm[j["id"]] = bytearray()
                                self.rx_rate[j["id"]] = j["rate"]
                                self.t_start[j["id"]] = asyncio.get_running_loop().time()
                                self.expect[j["id"]] = j["bytes"] / (j["rate"] * 2)
                                if self.half_duplex:
                                    self.mic_enabled = False  # half-duplex: speaker active => mic off
                            elif j["type"] == "audio_end":
                                asyncio.create_task(self._finish(ws, j["id"]))
                        elif m.type == WSMsgType.BINARY and m.data[0] == 2:
                            # attribute to the newest open audio id
                            if self.rx_pcm:
                                self.rx_pcm[max(self.rx_pcm)].extend(m.data[3:])
                finally:
                    self.close_code = ws.close_code
                    if streamer:
                        streamer.cancel()

    async def _finish(self, ws, aid):
        if self.realtime_playback:
            # a real device reports playback_done when its buffer has actually drained, i.e. in real time
            elapsed = asyncio.get_running_loop().time() - self.t_start.get(aid, 0)
            await asyncio.sleep(max(0.0, self.expect.get(aid, 0) - elapsed) + 0.05)
        try:
            await ws.send_json({"type": "playback_done", "id": aid})
        except Exception:
            pass
        self.mic_enabled = True

    async def _stream(self, ws):
        loop = asyncio.get_running_loop()
        nxt = loop.time()
        seq = 0
        while True:
            nxt += 0.08
            if self.mic_enabled:
                try:
                    frame = next(self.frames)
                except StopIteration:
                    frame = bytes(MIC_FRAME_BYTES)
                if len(frame) == MIC_FRAME_BYTES:
                    await ws.send_bytes(b"\x01" + (seq & 0xFFFF).to_bytes(2, "big") + frame)
                    self.sent_frames += 1
                    seq += 1
            d = nxt - loop.time()
            if d > 0:
                await asyncio.sleep(d)

    def start(self):
        self._task = asyncio.create_task(self.run())
        return self._task

    async def stop(self):
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        if self._task:
            try:
                await asyncio.wait_for(self._task, 3)
            except Exception:
                self._task.cancel()


def wav_frames(path):
    with wave.open(path) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2, "need 16 kHz mono S16 wav"
        data = w.readframes(w.getnframes())
    for i in range(0, len(data) - MIC_FRAME_BYTES + 1, MIC_FRAME_BYTES):
        yield data[i:i + MIC_FRAME_BYTES]


async def _cli(a):
    key = open(a.key_file, "rb").read().strip()
    dev = FakeDevice(a.url, key, frames=wav_frames(a.wav) if a.wav else None)
    t = dev.start()
    await dev.connected.wait()
    print("connected; streaming mic", "from " + a.wav if a.wav else "(silence)", flush=True)
    try:
        await t
    except asyncio.CancelledError:
        pass
    if a.out and dev.rx_pcm:
        with wave.open(a.out, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
            for k in sorted(dev.rx_pcm):
                w.writeframes(bytes(dev.rx_pcm[k]))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://127.0.0.1:8770/stackchan")
    ap.add_argument("--key-file", default=os.path.expanduser("~/sunday-audio/secrets/device.key"))
    ap.add_argument("--wav")
    ap.add_argument("--out")
    asyncio.run(_cli(ap.parse_args()))
