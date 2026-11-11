"""
Persistent microphone stream — one arecord process shared by wake and STT.
Both consumers read from the same asyncio Queue.
"""
import asyncio
from collections import deque
import subprocess
import sys
import threading
import time

import numpy as np

CHUNK  = 1280   # 80ms at 16kHz
RATE   = 16000


class MicStream:
    def __init__(self, device: str):
        self._device = device
        self._queue: asyncio.Queue | None = None
        self._loop:  asyncio.AbstractEventLoop | None = None
        self._cmd:   list[str] | None = None
        self._proc:  subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._subscribers: list = []
        self._feedback_buffer = deque(maxlen=150)  # 12 seconds, RAM only

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._queue = asyncio.Queue(maxsize=200)  # ~16s buffer
        self._subscribers = []
        from adaptive_audio import attach
        attach(self)

        if sys.platform == "darwin":
            self._cmd = ["sox", "-t", "coreaudio", "AIRHUG 21",
                         "-r", str(RATE), "-c", "1",
                         "-e", "signed-integer", "-b", "16", "-t", "raw", "-"]
        else:
            self._cmd = ["arecord", "-D", self._device,
                         "-r", str(RATE), "-c", "1",
                         "-f", "S16_LE", "-t", "raw", "-q"]

        self._thread = threading.Thread(target=self._reader, daemon=True, name="mic-reader")
        self._thread.start()
        print(f"[Mic] Stream started ({self._device})")

    def _start_proc(self) -> subprocess.Popen:
        return subprocess.Popen(self._cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def _reader(self) -> None:
        while True:
            self._proc = self._start_proc()
            while True:
                raw = self._proc.stdout.read(CHUNK * 2)
                if not raw:
                    break
                audio = np.frombuffer(raw, dtype=np.int16).copy()
                self._loop.call_soon_threadsafe(self._put, audio)
            print("[Mic] arecord exited — restarting in 1s")
            time.sleep(1)

    def _put(self, audio: np.ndarray) -> None:
        """Called on the event loop thread via call_soon_threadsafe."""
        self._feedback_buffer.append((time.monotonic(), audio))
        try:self.audio_tracker.observe(audio)
        except Exception:pass  # Audio measurement never interrupts wake/audio delivery.
        try:
            self._queue.put_nowait(audio)
        except asyncio.QueueFull:
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(audio)
            except Exception:
                pass
        for q in self._subscribers:
            try:
                q.put_nowait(audio)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                    q.put_nowait(audio)
                except Exception:
                    pass

    def subscribe(self) -> "asyncio.Queue":
        """Return a queue that receives every audio chunk (non-consuming copy)."""
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subscribers.append(q)
        return q

    async def read(self) -> np.ndarray:
        """Get the next chunk."""
        return await self._queue.get()

    def drain(self) -> None:
        """Discard buffered audio — call before STT to avoid stale frames."""
        drained = 0
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                drained += 1
            except Exception:
                break
        if drained:
            print(f"[Mic] Drained {drained} stale frames")
