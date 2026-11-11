import json,unittest
from unittest.mock import patch,AsyncMock
from test_aliases import Tests as Setup
import aliases as A
class Response:
 status=200
 async def __aenter__(self):return self
 async def __aexit__(self,*args):pass
 async def json(self):return {'status':'ok'}
class Session:
 async def __aenter__(self):return self
 async def __aexit__(self,*args):pass
 def get(self,url):return Response()
class Tests(unittest.IsolatedAsyncioTestCase):
 setUp=Setup.setUp;tearDown=Setup.tearDown
 async def test_switch_and_revert_preserve_settings_without_restart(self):
  m=A.AliasManager(AsyncMock())
  with patch.object(A,'ClientSession',return_value=Session()):
   for provider,port in [('sherpa','8184'),('whisper','8183')]:
    r=await m.set_provider(provider);c=json.loads(self.config.read_text());self.assertEqual(r['provider'],provider);self.assertEqual(c['aliases'],self.original['aliases']);self.assertEqual(c['audio_ctx'],128);self.assertEqual(c['mode'],'enforce');self.assertIn(port,c['url'])
  m.reload.assert_not_awaited()
 async def test_unhealthy_target_never_changes_selection(self):
  m=A.AliasManager(AsyncMock());old=self.config.read_text()
  with patch.object(A,'ClientSession',return_value=Session()),patch.object(Response,'status',503):
   with self.assertRaises(ValueError):await m.set_provider('sherpa')
  self.assertEqual(self.config.read_text(),old)
if __name__=='__main__':unittest.main()
