import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from aiohttp.test_utils import TestClient,TestServer
from config import Config
from memory_v1.service import MemoryService
from memory_v1.dashboard import snapshot
class DashboardTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.temp=tempfile.TemporaryDirectory();self.svc=MemoryService(Path(self.temp.name)/'m.db',Config(memory_write_enabled=True));self.s=self.svc.store
  ref=self.s.evidence('user_statement','test',{'quote':'I like tea.'});self.cid=self.s.submit({'type':'PREFERENCE','subject':'tea preference','statement':'Likes tea.','confidence':.9,'people':['Karthik']},'USER_EXPLICIT',[ref],'one');self.mid=self.s.reconcile(self.cid,{'operation':'ADD','reason':'test evidence'},[])['affected_memory_ids'][0]
 async def asyncTearDown(self):self.svc.close();self.temp.cleanup()
 async def test_inbox_includes_processed_and_consolidated_is_separate(self):
  a=snapshot(self.svc,{'view':'inbox'});b=snapshot(self.svc,{'view':'consolidated'});self.assertEqual(a['records'][0]['state'],'ACCEPTED');self.assertEqual(b['records'][0]['id'],self.mid);self.assertFalse(a['flags']['memory_read_enabled'])
 async def test_filters_pagination_and_literal_search(self):
  self.assertEqual(snapshot(self.svc,{'q':'tea','status':'ACCEPTED'})['total'],1);self.assertEqual(snapshot(self.svc,{'q':'%_'})['total'],0);self.assertEqual(snapshot(self.svc,{'offset':'1'})['records'],[]);self.assertEqual(snapshot(self.svc,{'limit':'1000'})['limit'],50)
 async def test_evidence_audit_and_versions_detail(self):
  r=snapshot(self.svc,{'view':'consolidated','id':self.mid})['records'][0];self.assertEqual(r['evidence'][0]['data']['quote'],'I like tea.');self.assertEqual(len(r['audit']),1);self.assertEqual(len(r['versions']),1)
 async def test_inspection_is_read_only(self):
  before=self.s.db.total_changes
  snapshot(self.svc,{'view':'inbox','id':self.cid});snapshot(self.svc,{'view':'consolidated'});self.assertEqual(self.s.db.total_changes,before);self.assertFalse(self.svc.config.memory_read_enabled)
 async def test_bad_parameters_and_disabled(self):
  for params in [{'view':'memories;DROP TABLE inbox'},{'limit':'notnumber'}]:
   with self.assertRaises(ValueError):snapshot(self.svc,params)
  self.assertFalse(snapshot(None,{})['enabled'])
 async def test_http_auth_and_no_write_route(self):
  from main import create_app
  rt=NS(config=Config(daemon_token='test'),learned_memory=self.svc,start=AsyncMock(),close=AsyncMock())
  client=TestClient(TestServer(create_app(rt)));await client.start_server()
  try:
   self.assertEqual((await client.get('/v1/memory')).status,401)
   response=await client.get('/v1/memory?view=consolidated',headers={'Authorization':'Bearer test'});self.assertEqual(response.status,200);self.assertEqual((await response.json())['total'],1)
   self.assertEqual((await client.post('/v1/memory',json={},headers={'Authorization':'Bearer test'})).status,405)
  finally:await client.close()
