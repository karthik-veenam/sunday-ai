import asyncio,json,tempfile,unittest
from pathlib import Path
from unittest.mock import AsyncMock,patch
from aiohttp.test_utils import TestClient,TestServer
import aliases as A
class Tests(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name);self.config=root/'c';self.env=root/'e';self.audit=root/'audit';self.original={'aliases':['sunday','some may'],'mode':'enforce','audio_ctx':128,'timeout_s':1.5};self.config.write_text(json.dumps(self.original));self.env.write_text('SUNDAY_ALIASES="sunday|some may"\n');self.patches=[patch.object(A,'CONFIG',self.config),patch.object(A,'ENV',self.env),patch.object(A,'AUDIT',self.audit)]
  for p in self.patches:p.start()
 def tearDown(self):
  for p in self.patches:p.stop()
  self.tmp.cleanup()
 async def test_add_updates_both_lists_preserves_other_settings(self):
  reload=AsyncMock();m=A.AliasManager(reload);v=await m.add('Somebody, somebody!');self.assertEqual(v['aliases'][-1],'somebody somebody');self.assertIn('somebody somebody',self.env.read_text());c=json.loads(self.config.read_text());self.assertEqual({k:v for k,v in c.items() if k!='aliases'},{k:v for k,v in self.original.items() if k!='aliases'});reload.assert_awaited_once()
 async def test_duplicate_is_idempotent_no_restart(self):
  reload=AsyncMock();m=A.AliasManager(reload);v=await m.add('SUNDAY!');self.assertFalse(v['changed']);reload.assert_not_awaited()
 async def test_remove_updates_both_lists_preserves_other_settings(self):
  reload=AsyncMock();m=A.AliasManager(reload);v=await m.remove('Some May!')
  self.assertEqual(v['aliases'],['sunday']);self.assertEqual(self.env.read_text(),'SUNDAY_ALIASES="sunday"\n')
  self.assertEqual({k:v for k,v in json.loads(self.config.read_text()).items() if k!='aliases'},{k:v for k,v in self.original.items() if k!='aliases'});reload.assert_awaited_once()
 async def test_core_alias_cannot_be_removed(self):
  m=A.AliasManager(AsyncMock())
  with self.assertRaises(ValueError):await m.remove('SUNDAY!')
  self.assertEqual(json.loads(self.config.read_text()),self.original);m.reload.assert_not_awaited()
 async def test_missing_alias_removal_is_idempotent(self):
  m=A.AliasManager(AsyncMock());self.assertFalse((await m.remove('missing alias'))['changed']);m.reload.assert_not_awaited()
 async def test_failed_remove_restores_both_files(self):
  old=self.env.read_text();m=A.AliasManager(AsyncMock(side_effect=RuntimeError))
  with self.assertRaises(RuntimeError):await m.remove('some may')
  self.assertEqual(json.loads(self.config.read_text()),self.original);self.assertEqual(self.env.read_text(),old)
 async def test_concurrent_add_remove_preserve_each_other(self):
  m=A.AliasManager(AsyncMock());await asyncio.gather(m.add('something'),m.remove('some may'))
  self.assertEqual(m.snapshot()['aliases'],['sunday','something'])
 async def test_authenticated_remove_endpoint(self):
  m=A.AliasManager(AsyncMock());client=TestClient(TestServer(A.create_app(m,token='test')));await client.start_server()
  try:
   self.assertEqual((await client.post('/v1/wake-aliases',json={'action':'remove','alias':'some may'})).status,401)
   r=await client.post('/v1/wake-aliases',headers={'Authorization':'Bearer test'},json={'action':'remove','alias':'some may'});self.assertEqual(r.status,200);self.assertEqual((await r.json())['aliases'],['sunday'])
  finally:await client.close()
 async def test_concurrent_additions_not_lost(self):
  m=A.AliasManager(AsyncMock());await asyncio.gather(m.add('something'),m.add('some days'));self.assertIn('something',m.snapshot()['aliases']);self.assertIn('some days',m.snapshot()['aliases'])
 async def test_failed_reload_restores_both_files(self):
  old=self.env.read_text();m=A.AliasManager(AsyncMock(side_effect=RuntimeError))
  with self.assertRaises(RuntimeError):await m.add('new phrase')
  self.assertEqual(json.loads(self.config.read_text()),self.original);self.assertEqual(self.env.read_text(),old)
 async def test_auth_and_invalid_input_do_not_mutate(self):
  m=A.AliasManager(AsyncMock());client=TestClient(TestServer(A.create_app(m,token='test')));await client.start_server()
  try:
   self.assertEqual((await client.post('/v1/wake-aliases',json={'action':'add','alias':'bad'})).status,401)
   headers={'Authorization':'Bearer test'}
   for value in ['',42,'x\nENV=anything','$(touch /tmp/no)','a'*81]:
    r=await client.post('/v1/wake-aliases',headers=headers,json={'action':'add','alias':value});self.assertEqual(r.status,400,value)
   r=await client.post('/v1/wake-aliases',headers=headers,json={'action':'list'});self.assertEqual((await r.json())['aliases'],self.original['aliases'])
  finally:await client.close()
  m.reload.assert_not_awaited()
if __name__=='__main__':unittest.main()

class ContextTests(unittest.IsolatedAsyncioTestCase):
 setUp=Tests.setUp
 tearDown=Tests.tearDown
 async def test_context_change_preserves_aliases_and_does_not_restart(self):
  reload=AsyncMock();m=A.AliasManager(reload);v=await m.set_context(192);self.assertEqual(v['audio_ctx'],192);self.assertEqual(v['aliases'],self.original['aliases']);self.assertEqual(json.loads(self.config.read_text())['timeout_s'],1.5);reload.assert_not_awaited()
 async def test_context_rejects_invalid_values(self):
  m=A.AliasManager(AsyncMock())
  for x in [True,'192',0,1501,128.5]:
   with self.assertRaises(ValueError):await m.set_context(x)
  self.assertEqual(json.loads(self.config.read_text()),self.original)
