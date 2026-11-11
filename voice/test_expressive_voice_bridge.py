import json,tempfile,unittest
from pathlib import Path
from unittest.mock import AsyncMock
from aiohttp import web
from aiohttp.test_utils import TestServer
from voice_brain import VoiceBrain
class Tests(unittest.IsolatedAsyncioTestCase):
 async def test_tts_metadata_crosses_bridge_once_plain_final_not_replayed(self):
  calls=[]
  async def handle(request):
   calls.append(await request.json())
   events=[{'type':'assistant_text','text':'Hello there. ','tts_text':'[happy] Hello there.'},{'type':'final','text':'Hello there.','continue_conversation':False}]
   return web.Response(text='\n'.join(json.dumps(e) for e in events)+'\n',content_type='application/x-ndjson')
  app=web.Application();app.router.add_post('/v1/chat',handle)
  async with TestServer(app) as server:
   with tempfile.TemporaryDirectory() as tmp:
    p=Path(tmp)/'config';p.write_text(json.dumps({'daemon_port':1,'daemon_token':'test'}));b=VoiceBrain(str(p));b.url=str(server.make_url('/v1/chat'));tts=type('TTS',(),{'speak':AsyncMock()})();await b.process('Hello',tts);tts.speak.assert_awaited_once_with('[happy] Hello there.');self.assertEqual(len(calls),1)
if __name__=='__main__':unittest.main()
