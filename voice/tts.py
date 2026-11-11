import re
import subprocess
import threading
import asyncio

from elevenlabs.client import ElevenLabs
from elevenlabs import VoiceSettings

VOICE_ID    = "3gsg3cxXyFLcGIfNbM6C"  # Jessica — clear, natural English
TURBO_MODEL_ID = "eleven_turbo_v2_5"   # fast default
EXPRESSIVE_MODEL_ID = "eleven_v3"      # supports audio tags like [laugh], [sigh]
MODEL_ID    = TURBO_MODEL_ID            # legacy default

# Detect ElevenLabs-style audio tags so we can route to the expressive model only when needed.
_TAG_RE = re.compile(r"\[[a-zA-Z][a-zA-Z\s]{0,30}\]")

def _pick_model(text: str) -> str:
    return EXPRESSIVE_MODEL_ID if _TAG_RE.search(text) else TURBO_MODEL_ID
VOICE_SPEED = 1.0      # lowest latency model
OUTPUT_FMT  = "pcm_22050"             # raw PCM, no decoding needed
SAMPLE_RATE = 22050


class TTS:
    def __init__(self, model_path: str = "", length_scale: float = 1.0,
                 elevenlabs_api_key: str = ""):
        print("[TTS] Using ElevenLabs...")
        self._client = ElevenLabs(api_key=elevenlabs_api_key)
        self.sample_rate = SAMPLE_RATE
        self._lock = threading.Lock()
        print("[TTS] ElevenLabs ready")

    def synthesize(self, text: str) -> bytes:
        """Synthesize to PCM bytes. Blocking — run in executor."""
        text = text.strip()
        if not text:
            return b""
        with self._lock:
            chunks = self._client.text_to_speech.convert(
                text=text,
                voice_id=VOICE_ID,
                model_id=_pick_model(text),
                output_format=OUTPUT_FMT,
                voice_settings=VoiceSettings(stability=0.5, similarity_boost=0.75, speed=VOICE_SPEED),
            )
            return b"".join(chunks)

    async def play(self, pcm: bytes) -> None:
        """Play raw PCM bytes through aplay."""
        if not pcm:
            return
        proc = await asyncio.create_subprocess_exec(
            "aplay", "-D", "plughw:2,0", "-r", str(SAMPLE_RATE),
            "-f", "S16_LE", "-c", "1", "-t", "raw", "-q",
            stdin=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate(input=pcm)

    async def speak(self, text: str) -> None:
        """Stream ElevenLabs chunks directly to aplay — starts playing immediately."""
        text = text.strip()
        if not text:
            return
        await asyncio.get_event_loop().run_in_executor(None, self._speak_sync, text)

    def _speak_sync(self, text: str) -> None:
        proc = subprocess.Popen(
            ["aplay", "-D", "plughw:2,0", "-r", str(SAMPLE_RATE),
             "-f", "S16_LE", "-c", "1", "-t", "raw", "-q"],
            stdin=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        with self._lock:
            for chunk in self._client.text_to_speech.stream(
                text=text,
                voice_id=VOICE_ID,
                model_id=_pick_model(text),
                output_format=OUTPUT_FMT,
                voice_settings=VoiceSettings(stability=0.5, similarity_boost=0.75, speed=VOICE_SPEED),
            ):
                if chunk:
                    proc.stdin.write(chunk)
        proc.stdin.close()
        proc.wait()
