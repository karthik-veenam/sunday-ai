import asyncio
import io
import json
import sys
import tempfile
import time
import unittest
import wave
import zipfile
from collections import deque
from pathlib import Path
from types import SimpleNamespace
import numpy as np
from wake_feedback import Capture, Store, is_correction

class FeedbackTests(unittest.TestCase):
    def test_phrases_are_explicit(self):
        for phrase in ["I didn't call you!", 'Sunday, false alarm.', 'That wasn’t for you', 'I did not call you']:
            self.assertTrue(is_correction(phrase), phrase)
        for phrase in ['tell me what false alarm means', "don't mark this as a false alarm", 'Sunday turn on the light', 'I called you']:
            self.assertFalse(is_correction(phrase), phrase)

    def test_capture_then_correction_is_separate_and_expires(self):
        async def run():
            with tempfile.TemporaryDirectory() as d:
                root=Path(d);model=root/'model.onnx';model.write_bytes(b'test fixture')
                c=Capture(root/'events');now=time.monotonic();chunk=np.ones(1280,dtype=np.int16)
                history=[(now-(49-i)*.08,chunk.copy()) for i in range(50)]
                mic=SimpleNamespace(_feedback_buffer=deque(history,maxlen=150))
                eid=c.triggered(mic,history[-1][1],.999,.99,model)
                mic._feedback_buffer.extend((now+(i+1)*.08,chunk.copy()*2) for i in range(25))
                await asyncio.gather(*list(c.tasks))
                row=c.store.get(eid);self.assertAlmostEqual(row['duration'],6)
                stt=SimpleNamespace(last_started_at=now+.4,last_audio=b'\x03\x00'*8000)
                self.assertTrue(await c.correction("I didn't call you",stt))
                row=c.store.get(eid);self.assertEqual(row['status'],'reported');self.assertAlmostEqual(row['duration'],4.4,places=4)
                self.assertTrue(c.store.wav_path(eid,True).exists())
                c.latest=(eid,time.monotonic()-21);stt.last_started_at=time.monotonic()
                self.assertFalse(await c.correction('false alarm',stt))
        asyncio.run(run())

    def test_review_requires_report_and_exports_trimmed_approved_only(self):
        sys.path.insert(0,'/home/djpi/sunday-clip-review')
        import wake_feedback_routes as routes
        from flask import Flask
        with tempfile.TemporaryDirectory() as d:
            store=Store(d);eid=store.create({'duration':2.,'start':0.,'end':2.,'score':.99,'threshold':.99,'capture_start_monotonic':0,'capture_end_monotonic':2})
            store.write_audio(eid,b'\x01\x00'*32000);store.update(eid,status='pending')
            app=Flask(__name__);app.register_blueprint(routes.bp);client=app.test_client();original=routes.store;routes.store=lambda:store
            try:
                url='/key/wake-feedback/events/'+eid
                self.assertEqual(client.post(url,json={'status':'approved'}).status_code,400)
                self.assertEqual(client.post(url,json={'action':'report'}).status_code,200)
                self.assertEqual(client.post(url,json={'status':'approved','start':.2,'end':1.2}).status_code,200)
                self.assertEqual(client.post(url,json={'start':float('nan')}).status_code,400)
                response=client.get('/key/wake-feedback/export');self.assertEqual(response.status_code,200)
                with zipfile.ZipFile(io.BytesIO(response.data)) as z:
                    self.assertEqual(len(json.loads(z.read('manifest.json'))['events']),1)
                    with wave.open(io.BytesIO(z.read('negatives/'+eid+'.wav'))) as w:self.assertEqual(w.getnframes(),16000)
                self.assertEqual(client.get('/key/wake-feedback/audio/nonexistent').status_code,404)
            finally:routes.store=original

if __name__=='__main__':unittest.main()
