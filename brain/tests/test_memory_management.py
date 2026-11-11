import asyncio,tempfile,unittest
from pathlib import Path
from unittest.mock import AsyncMock
from config import Config
from memory_v1.service import MemoryService
from memory_v1.dashboard import snapshot
from memory_v1.management import change,Conflict
from memory_v1.store import key
class ManagementTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'m.db';self.svc=MemoryService(self.path,Config(memory_write_enabled=True));self.s=self.svc.store
  self.ev=self.s.evidence('user_statement','test',{'quote':'I like tea.'});self.cid=self.s.submit({'type':'PREFERENCE','subject':'drinks','statement':'Likes tea.','confidence':.9,'tags':['tea']},'USER_EXPLICIT',[self.ev],'one');self.mid=self.s.reconcile(self.cid,{'operation':'ADD','reason':'test'},[])['affected_memory_ids'][0]
 async def asyncTearDown(self):await self.svc.shutdown();self.tmp.cleanup()
 def request(self,view,rid,action,**kw):
  record=snapshot(self.svc,{'view':view,'id':rid})['records'][0];return {'view':view,'id':rid,'action':action,'revision':record['revision'],**kw}
 async def test_edit_retrieval_versions_and_stale_rejection(self):
  body=self.request('consolidated',self.mid,'edit',patch={'statement':'Prefers coffee.','tags':['coffee']});change(self.svc,body)
  self.assertEqual(self.s.get(self.mid)['statement'],'Prefers coffee.');self.assertEqual(len(self.s.chain(self.mid)['versions']),2);self.assertTrue(self.s.retrieve('coffee'));self.assertFalse(self.s.retrieve('tea'));self.assertFalse(self.svc.config.memory_read_enabled)
  with self.assertRaises(Conflict):change(self.svc,body)
 async def test_remove_hidden_and_not_replayed(self):
  change(self.svc,self.request('consolidated',self.mid,'remove'));self.assertEqual(snapshot(self.svc,{'view':'consolidated'})['total'],0);self.assertEqual(self.s.retrieve('drinks'),[])
  cid=self.s.submit({'type':'PREFERENCE','subject':'drinks','statement':'Likes tea.'},'USER_EXPLICIT',[self.ev],'two');self.assertEqual(self.s.reconcile(cid,{'operation':'ADD','reason':'retry'},[])['state'],'DELETED')
 async def test_pending_edit_requeues_and_rejects_old_proposal(self):
  cid=self.s.submit({'type':'FACT','subject':'pet','statement':'Dog is Hugo.'},'USER_EXPLICIT',[self.ev],'pending');before=self.s.candidates()[0]['data'];change(self.svc,self.request('inbox',cid,'edit',patch={'statement':'Dog is Luna.'}))
  self.assertEqual(self.s.reconcile(cid,{'operation':'ADD','reason':'old'},[],{'candidate_revision':key(before)})['status'],'candidate_changed');self.assertEqual(self.s.candidates()[0]['data']['statement'],'Dog is Luna.')
 async def test_inbox_remove_does_not_remove_canonical(self):
  change(self.svc,self.request('inbox',self.cid,'remove'));self.assertEqual(snapshot(self.svc,{'view':'inbox'})['total'],0);self.assertIsNotNone(self.s.get(self.mid));self.assertEqual(snapshot(self.svc,{'view':'inbox','status':'DELETED'})['total'],1)
 async def test_validation_does_not_mutate(self):
  n=self.s.db.total_changes
  for patch in [{'statement':''},{'subject':'x'*201},{'status':'ACTIVE'},{'tags':['x'*161]}]:
   with self.assertRaises(ValueError):change(self.svc,self.request('consolidated',self.mid,'edit',patch=patch))
  self.assertEqual(self.s.db.total_changes,n)
 async def test_manual_sync_deduplicated_and_records_result(self):
  self.svc.collect_history=AsyncMock(return_value=[]);self.svc.consolidator.nightly=AsyncMock(return_value=[]);one=self.svc.start_sync();two=self.svc.start_sync();self.assertEqual(one['id'],two['id']);await self.svc.sync_task
  self.assertEqual(self.svc.sync_status()['status'],'COMPLETED');self.svc.consolidator.nightly.assert_awaited_once();self.assertFalse(self.svc.config.memory_read_enabled)
 async def test_sync_failure_recorded(self):
  self.svc.collect_history=AsyncMock(side_effect=RuntimeError('test'));self.svc.start_sync();await self.svc.sync_task;self.assertEqual(self.svc.sync_status()['status'],'FAILED')
 async def test_pre_edit_candidate_cannot_override(self):
  cid=self.s.submit({'type':'PREFERENCE','subject':'drinks','statement':'Likes tea very much.'},'USER_EXPLICIT',[self.ev],'older');change(self.svc,self.request('consolidated',self.mid,'edit',patch={'statement':'Prefers coffee.'}))
  result=self.s.reconcile(cid,{'operation':'UPDATE','target_id':self.mid,'reason':'older'},[self.mid]);self.assertEqual(result['operation_applied'],'IGNORE');self.assertEqual(self.s.get(self.mid)['statement'],'Prefers coffee.')
 async def test_tag_only_pending_edit_processes(self):
  cid=self.s.submit({'type':'FACT','subject':'pet','statement':'Dog is Hugo.'},'USER_EXPLICIT',[self.ev],'tags');change(self.svc,self.request('inbox',cid,'edit',patch={'tags':['pets']}));self.assertEqual(self.s.reconcile(cid,{'operation':'ADD','reason':'explicit'},[])['operation_applied'],'ADD')
 async def test_pattern_edit_removes_old_statistical_actions(self):
  cid=self.s.submit({'type':'PATTERN_EXPECTATION','subject':'pattern:test','statement':'Observed long sessions.','structured_value':{'group':['device_session','fan.test','device','Mon',None],'temporal_boundaries':{'duration_minutes':20}}},'HISTORICAL_PATTERN',[self.ev],'pattern')
  mid=self.s.reconcile(cid,{'operation':'ADD','reason':'measured'},[])['affected_memory_ids'][0]
  change(self.svc,self.request('consolidated',mid,'edit',patch={'statement':'I run the fan for ten minutes.'}))
  self.assertEqual(self.s.get(mid)['type'],'FACT');self.assertIsNone(self.s.get(mid)['structured_value'])
  duplicate=self.s.submit({'type':'PATTERN_EXPECTATION','subject':'pattern:test','statement':'New measured wording.','structured_value':{'group':['device_session','fan.test','device','Mon',None]}},'HISTORICAL_PATTERN',[self.ev],'pattern-new')
  self.assertEqual(self.s.reconcile(duplicate,{'operation':'ADD','reason':'recalculated'},[])['state'],'DELETED')
 async def test_http_management_auth_and_validation(self):
  from aiohttp.test_utils import TestClient,TestServer
  from types import SimpleNamespace
  from main import create_app
  rt=SimpleNamespace(config=Config(daemon_token='test'),learned_memory=self.svc,start=AsyncMock(),close=AsyncMock())
  client=TestClient(TestServer(create_app(rt)));await client.start_server()
  try:
   body=self.request('consolidated',self.mid,'edit',patch={'statement':'Coffee please.'})
   self.assertEqual((await client.post('/v1/memory/manage',json=body)).status,401)
   self.assertEqual((await client.post('/v1/memory/manage',json=body,headers={'Authorization':'Bearer test'})).status,200)
   self.assertEqual((await client.post('/v1/memory/manage',json=body,headers={'Authorization':'Bearer test'})).status,409)
  finally:await client.close()
if __name__=='__main__':unittest.main()
