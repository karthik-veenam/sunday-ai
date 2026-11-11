"""Ordered speech for completed Codex messages; no tool/reasoning narration."""
import asyncio
import logging
import re
import sys

# Reuse Sunday's configured ElevenLabs voice without starting its runtime.
sys.path.append('/home/djpi/sunday')
from eleven_tts import ElevenTTS, settings

log = logging.getLogger('devmode-speech')


def spoken_text(text):
    text = re.sub(r'```[\s\S]*?```', '', text)
    text = re.sub(r'!?\[([^\]]+)\]\([^)]*\)', r'\1', text)
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'(?m)^\s*[#>*-]+\s*', '', text)
    return re.sub(r'\s+', ' ', text.replace('`', '').replace('**', '')).strip()


class Speech:
    def __init__(self, gate, report):
        self.gate, self.report = gate, report
        self.queue = asyncio.Queue(maxsize=20)
        self.seen = set()
        self.tts = ElevenTTS()
        self.player = None
        self.speaking = False

    def enqueue(self, item):
        if item.get('type') != 'agentMessage' or item.get('id') in self.seen:
            return
        text = spoken_text(item.get('text', ''))
        if not text:
            return
        self.seen.add(item.get('id'))
        if len(self.seen) > 2000:
            self.seen = {item.get('id')}
        try:
            self.queue.put_nowait(text)
        except asyncio.QueueFull:
            log.warning('speech queue full')

    async def run(self):
        try:
            while True:
                text = await self.queue.get()
                try:
                    # Fetch audio first so the mic stays usable during synthesis.
                    pcm = await self.tts.synthesize_pcm(text, settings())
                    self.speaking = True
                    await self.gate(True)
                    await self.report({'type': 'speech', 'speaking': True})
                    self.player = await asyncio.create_subprocess_exec(
                        'paplay', '--raw', '--format=s16le', '--rate=22050', '--channels=1',
                        stdin=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                    await asyncio.wait_for(self.player.communicate(pcm), 180)
                    if self.player.returncode:
                        raise RuntimeError('speaker playback failed')
                except Exception as exc:
                    log.warning('speech failed: %s', exc)
                    await self.report({'type': 'speech', 'speaking': False, 'error': 'Speech unavailable; see chat.'})
                finally:
                    if self.player and self.player.returncode is None:
                        self.player.kill()
                        await self.player.wait()
                    self.player = None
                    if self.speaking:
                        await asyncio.sleep(.6)
                        self.speaking = False
                        await self.gate(False)
                        await self.report({'type': 'speech', 'speaking': False})
                    self.queue.task_done()
        finally:
            await self.tts.close()
