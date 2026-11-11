import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch,AsyncMock
from aiohttp.test_utils import TestClient,TestServer
from config import Config
from main import create_app
from runtime import Runtime
import runtime as module

class Brain:
    def __init__(self): self.calls=0
    async def process(self,text,out):self.calls+=1;await out.speak('Ready.');return False
    def reset(self):pass

class Tests(unittest.IsolatedAsyncioTestCase):
    async def test_authenticated_voice_stream_and_idempotent_reconnect(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'state').mkdir();(root/'household.json').write_text('{"primary_area":"bedroom"}')
            with patch.object(module,'ROOT',root):
                r=Runtime(Config(daemon_token='test'),agent_factory=Brain);r.home.context=AsyncMock(return_value='{}')
                r.home.refresh=AsyncMock();r.home.search=lambda *a,**k:[]
                client=TestClient(TestServer(create_app(r)));await client.start_server()
                try:
                    unauth=await client.post('/v1/chat',json={'text':'hello'});self.assertEqual(unauth.status,401)
                    begin={'type':'begin_turn','protocol':1,'request_id':'voice-check','conversation_id':'voice-room','satellite_id':'satellite','source':'ha','text':'hello'}
                    for replay in (False,True):
                        ws=await client.ws_connect('/v1/turns',headers={'Authorization':'Bearer test'});await ws.send_json(begin);events=[]
                        while True:
                            e=await ws.receive_json();events.append(e)
                            if e['type']=='final':break
                        self.assertEqual(events[-1]['text'],'Ready.');self.assertEqual(events[-1].get('replayed',False),replay)
                        self.assertTrue(all(e['request_id']=='voice-check' and e['protocol']==1 for e in events));await ws.close()
                    self.assertEqual(r.agents['voice-room'].calls,1)
                finally:await client.close()
