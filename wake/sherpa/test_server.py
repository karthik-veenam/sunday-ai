import io,unittest,wave
from unittest.mock import Mock
import numpy as np
from aiohttp import FormData
from aiohttp.test_utils import TestClient,TestServer
from server import decode_wav,create_app
class Tests(unittest.IsolatedAsyncioTestCase):
 def wav(self,rate=16000):
  b=io.BytesIO()
  with wave.open(b,'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(rate);w.writeframes(b'\x00\x00'*1600)
  return b.getvalue()
 def test_format_validation(self):
  self.assertEqual(len(decode_wav(self.wav())),1600)
  with self.assertRaises(ValueError):decode_wav(self.wav(8000))
 async def test_api_schema_and_audio_stays_memory_only(self):
  result={'provider':'sherpa','accepted':False,'matched_alias':None,'text':'','model':'test'};engine=Mock();engine.score=3;engine.threshold=.02;engine.infer.return_value=result
  async with TestClient(TestServer(create_app(engine))) as client:
   r=await client.get('/health');self.assertEqual((await r.json())['provider'],'sherpa')
   data=FormData();data.add_field('file',self.wav(),filename='candidate.wav',content_type='audio/wav');r=await client.post('/inference',data=data);self.assertEqual(r.status,200);self.assertEqual(await r.json(),result);engine.infer.assert_called_once()
if __name__=='__main__':unittest.main()
