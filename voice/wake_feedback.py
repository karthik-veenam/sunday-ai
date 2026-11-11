"""Local wake-event capture and human-reviewed negative examples (no training)."""
import asyncio
from contextlib import contextmanager
import hashlib
import json
import re
import sqlite3
import time
import uuid
import wave
from pathlib import Path

ROOT = Path('/home/djpi/sunday-wake-feedback')
PHRASES = {
    'i didnt call you', 'i did not call you', 'i wasnt calling you',
    'i was not calling you', 'false alarm', 'false activation',
    'that wasnt for you', 'that was not for you', 'nobody called you',
}

def is_correction(text):
    text = re.sub(r"[^a-z0-9 ]", '', text.lower().replace('’', "'"))
    text = ' '.join(text.split())
    text = re.sub(r'^(?:hey )?sunday ', '', text)
    text = re.sub(r' (?:sunday|please)$', '', text)
    return text in PHRASES

class Store:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o700)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, created REAL, status TEXT, meta TEXT)')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.root/'events.sqlite3', timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def rows(self):
        with self.db() as db:
            return [dict(json.loads(r['meta']), id=r['id'], created=r['created'], status=r['status']) for r in db.execute('SELECT * FROM events ORDER BY created DESC')]

    def get(self, eid):
        with self.db() as db:
            r = db.execute('SELECT * FROM events WHERE id=?', (eid,)).fetchone()
        if r is None:
            raise KeyError(eid)
        return dict(json.loads(r['meta']), id=r['id'], created=r['created'], status=r['status'])

    def update(self, eid, status=None, **fields):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            r = db.execute('SELECT * FROM events WHERE id=?', (eid,)).fetchone()
            if r is None:
                raise KeyError(eid)
            meta = json.loads(r['meta']); meta.update(fields)
            db.execute('UPDATE events SET status=?,meta=? WHERE id=?', (status or r['status'], json.dumps(meta), eid))
        return self.get(eid)

    def create(self, meta):
        eid = uuid.uuid4().hex
        with self.db() as db:
            db.execute('INSERT INTO events VALUES (?,?,?,?)', (eid, time.time(), 'capturing', json.dumps(meta)))
        return eid

    def wav_path(self, eid, correction=False):
        self.get(eid)  # IDs must exist; no arbitrary paths.
        return self.root/(eid + ('-correction.wav' if correction else '.wav'))

    def write_audio(self, eid, pcm, correction=False):
        path = self.wav_path(eid, correction)
        tmp = path.with_suffix('.tmp')
        with wave.open(str(tmp), 'wb') as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(pcm)
        tmp.replace(path)
        return len(pcm)/32000

    def report(self, eid, text='', source='review_button', correction_pcm=b'', correction_started=None):
        c = self.get(eid)
        if c['status'] == 'capturing':
            raise ValueError('Audio is still being saved; try again shortly.')
        if correction_pcm:
            self.write_audio(eid, correction_pcm, correction=True)
        fields = {'correction_text': text, 'feedback_source': source, 'reported_at': time.time()}
        # Remove any captured feedback speech from the negative candidate.
        if correction_started is not None and correction_started < c['capture_end_monotonic']:
            end = max(0, min(c['duration'], correction_started-c['capture_start_monotonic']))
            with wave.open(str(self.wav_path(eid))) as w:
                pcm = w.readframes(round(end*16000))
            fields.update(duration=self.write_audio(eid, pcm), end=end, correction_removed=True)
        return self.update(eid, status='reported', **fields)

    def prune(self):
        # Only unreviewed events expire. Human-labelled examples remain until deleted.
        candidates = [c for c in self.rows() if c['status'] in ('pending', 'failed')]
        for n,c in enumerate(candidates):
            if n < 500 and time.time()-c['created'] < 7*86400:
                continue
            with self.db() as db:
                db.execute('DELETE FROM events WHERE id=? AND status IN (\'pending\',\'failed\')', (c['id'],))
                if db.total_changes:
                    for correction in (False, True):
                        (self.root/(c['id']+('-correction.wav' if correction else '.wav'))).unlink(missing_ok=True)

class Capture:
    def __init__(self, root=ROOT):
        self.store = Store(root)
        self.latest = None
        self.tasks = set()
        self.store.prune()
        for event in self.store.rows():
            if event['status'] == 'capturing':
                self.store.update(event['id'], status='failed', error='Capture interrupted by restart')

    def triggered(self, mic, audio, score, threshold, model_path):
        now = time.monotonic()
        history = list(mic._feedback_buffer)
        index = next((i for i in range(len(history)-1,-1,-1) if history[i][1] is audio), len(history)-1)
        if index < 0:
            return None
        history = history[:index+1]
        chunks = []; samples = 0
        for _, chunk in reversed(history):
            chunks.append(chunk.tobytes()); samples += len(chunk)
            if samples >= 4*16000:
                break
        pcm = b''.join(reversed(chunks))[-4*32000:]
        end = history[-1][0]
        model = Path(model_path)
        eid = self.store.create({'score':float(score), 'threshold':float(threshold),
            'model_file':str(model), 'model_sha256':hashlib.sha256(model.read_bytes()).hexdigest(),
            'detected_monotonic':now, 'capture_start_monotonic':end-len(pcm)/32000,
            'capture_end_monotonic':end, 'trigger_offset_s':len(pcm)/32000,
            'start':0, 'end':len(pcm)/32000, 'duration':len(pcm)/32000})
        self.latest = (eid, now)
        task = asyncio.create_task(self._finish(mic, eid, pcm, end))
        self.tasks.add(task); task.add_done_callback(self.tasks.discard)
        return eid

    async def _finish(self, mic, eid, pcm, end):
        try:
            await asyncio.sleep(max(0, end+2-time.monotonic()))
            post = b''.join(chunk.tobytes() for t,chunk in mic._feedback_buffer if end<t<=end+2)
            duration = await asyncio.to_thread(self.store.write_audio, eid, pcm+post)
            self.store.update(eid, status='pending', duration=duration, end=duration,
                              capture_end_monotonic=end+len(post)/32000)
            await asyncio.to_thread(self.store.prune)
        except Exception as error:
            print('[Wake feedback] Capture failed:', type(error).__name__, flush=True)
            self.store.update(eid, status='failed', error=type(error).__name__)

    async def correction(self, text, stt):
        if not is_correction(text) or self.latest is None:
            return False
        eid, detected = self.latest
        # Use speech onset, so slow transcription doesn't expire timely feedback.
        started = getattr(stt, 'last_started_at', None)
        onset = started if started is not None else time.monotonic()
        if not 0 <= onset-detected <= 20:
            return False
        await asyncio.gather(*list(self.tasks))
        try:
            await asyncio.to_thread(self.store.report, eid, text, 'spoken',
                                    getattr(stt, 'last_audio', b''), started)
        except Exception as error:
            print('[Wake feedback] Could not save correction:', type(error).__name__, flush=True)
            return False
        print('[Wake feedback] Reported false activation:', eid, flush=True)
        self.latest = None
        return True
