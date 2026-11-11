import unittest,json
from unittest.mock import AsyncMock
from aiohttp import web
from aiohttp.test_utils import TestServer
from voice_brain import VoiceBrain

class VoiceBridgeTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.requests=[];self.events=[{'type':'assistant_text','text':'Ready.'},{'type':'final','text':'Ready.','continue_conversation':True}]
  async def handler(request):
   self.requests.append((request.headers.get('Authorization'),await request.json()))
   return web.Response(text='\n'.join(json.dumps(e) for e in self.events)+'\n',content_type='application/x-ndjson')
  app=web.Application();app.router.add_post('/v1/chat',handler);self.server=TestServer(app);await self.server.start_server()
  self.brain=VoiceBrain.__new__(VoiceBrain);self.brain.url=str(self.server.make_url('/v1/chat'));self.brain.token='test-only';self.brain.reset();self.tts=type('TTS',(),{'speak':AsyncMock()})()
 async def asyncTearDown(self):await self.server.close()
 async def test_stream_plays_once_and_routes_voice_to_shared_brain(self):
  self.assertTrue(await self.brain.process('hello',self.tts));self.tts.speak.assert_awaited_once_with('Ready.')
  auth,req=self.requests[0];self.assertEqual(auth,'Bearer test-only');self.assertEqual(req['source'],'voice');self.assertEqual(req['satellite_id'],'local-airhug');self.assertEqual(req['conversation_id'],self.brain.conversation_id)
 async def test_error_never_retries_tools_and_ends_conversation(self):
  self.events=[{'type':'error','error':'test'}];self.assertFalse(await self.brain.process('test',self.tts));self.assertEqual(len(self.requests),1)
 async def test_reset_changes_conversation(self):
  before=self.brain.conversation_id;self.brain.reset();self.assertNotEqual(before,self.brain.conversation_id)

if __name__=='__main__':unittest.main()
