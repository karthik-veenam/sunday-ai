import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from aiohttp.test_utils import TestClient,TestServer
from compatibility import Compatibility

class Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.calls=[]
        async def turn(req,emit):self.calls.append(req);await emit({'type':'final','text':'Done.'})
        self.sink=AsyncMock(return_value={'voice':'ok','telegram':'ok'})
        self.bridge=Compatibility(SimpleNamespace(turn=turn),Path(self.temp.name)/'c.db',self.sink)
        self.client=TestClient(TestServer(self.bridge.app('test-only')));await self.client.start_server()
    async def asyncTearDown(self):
        await self.client.close();await self.bridge.close();self.temp.cleanup()
    async def test_auth_json_empty_channel(self):
        for path,body,status in [('/announce',{},401),('/announce',{'token':'test-only','message':''},400),('/announce',{'token':'test-only','message':'test','channel':'invalid'},400),('/ask',{'token':'test-only','text':[]},400)]:
            r=await self.client.post(path,json=body);self.assertEqual(r.status,status)
        r=await self.client.post('/announce',data='broken',headers={'Content-Type':'application/json'});self.assertEqual(r.status,400)
        self.sink.assert_not_called()
    async def test_announce_defaults_serialization_and_channels(self):
        message='Fridge "open"\nnow'
        r=await self.client.post('/announce',json={'token':'test-only','message':message});self.assertEqual(r.status,200);self.assertEqual(await r.text(),'ok')
        self.sink.assert_awaited_once_with(message=message,channel='voice')
        for channel in ('telegram','both'):
            r=await self.client.post('/announce',json={'token':'test-only','message':'test','channel':channel});self.assertEqual(r.status,200)
        self.assertEqual(self.sink.await_count,3)
    async def test_incomplete_delivery_visible(self):
        self.sink.return_value={'telegram':'unknown_or_failed'}
        r=await self.client.post('/announce',json={'token':'test-only','message':'test','channel':'telegram'});self.assertEqual(r.status,503)
        self.assertEqual(self.bridge.db.execute('select status from requests').fetchone()[0],'unknown')
    async def test_ask_accepted_once_and_final_delivery(self):
        r=await self.client.post('/ask',json={'token':'test-only','text':'do it'});self.assertEqual(r.status,200);self.assertEqual(await r.text(),'ok')
        await asyncio.gather(*self.bridge.tasks)
        self.assertEqual(len(self.calls),1);self.sink.assert_awaited_once_with('Done.','voice')
        self.assertEqual(self.bridge.db.execute('select status from requests').fetchone()[0],'completed')
