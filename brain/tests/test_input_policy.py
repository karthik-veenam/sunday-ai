import asyncio,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,Mock
from config import Config
from input_policy import InputPolicy,Conflict
from memory_v1.service import MemoryService
from proactivity_v1.service import Service
from test_autonomy_scope import home
from test_proactivity_v1 import event,FakeModels

class PolicyTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.h=home();self.h.devices={};self.h.areas={};self.h.world=NS(selected=lambda:{'fan.room','switch.hall'});self.h.household['people']={'karthik':{'ha_person':'person.karthik'}}
  self.c=Config(autonomy_primary_room_only=True,memory_write_enabled=True,ha_attention_enabled=True,proactive_cognition_enabled=True,expectation_engine_enabled=True)
  self.path=Path(self.tmp.name)/'inputs.json';self.policy=InputPolicy(self.path,self.h,self.c);self.policy.initialize();self.h.input_policy=self.policy
  self.m=MemoryService(Path(self.tmp.name)/'m.db',self.c,home=self.h);self.models=FakeModels();self.p=Service(Path(self.tmp.name)/'p.db',self.c,home=self.h,memory=self.m,models=self.models)
 async def asyncTearDown(self):await self.m.shutdown();await self.p.close();self.tmp.cleanup()
 def change(self,kind,key,purpose='proactivity',enabled=False):self.policy.change({'revision':self.policy.revision,'changes':[{'kind':kind,'id':key,'purpose':purpose,'enabled':enabled}]})
 def test_initial_defaults_match_room_and_location(self):
  self.assertTrue(self.policy.entity('memory','fan.room'));self.assertFalse(self.policy.entity('proactivity','switch.hall'));self.assertTrue(self.policy.entity('proactivity','person.karthik'));self.assertFalse(self.policy.source('proactivity','automations'));self.assertFalse(self.policy.source('proactivity','learned_memory'))
 def test_sources_have_supported_matrix_and_correct_checks(self):
  snap=self.policy.snapshot();src={s['id']:s for s in snap['sources']};self.assertTrue(src['presence']['selected']['memory']);self.assertFalse(src['automations']['supported']['memory']);self.assertEqual(len(snap['devices']),len(self.h.entities))
 def test_independent_purpose_and_cross_room_opt_in(self):
  self.change('entity','fan.room');self.change('entity','switch.hall',enabled=True)
  self.assertTrue(self.policy.entity('memory','fan.room'));self.assertFalse(self.policy.entity('proactivity','fan.room'));self.assertTrue(self.policy.allows('proactivity',{'subject':{'id':'switch.hall','area':'hall'}}));self.assertFalse(self.policy.entity('memory','switch.hall'))
 def test_source_pauses_without_forgetting_device_selection(self):
  self.change('source','devices');self.assertFalse(self.policy.entity('proactivity','fan.room'));self.assertTrue(self.policy.data['entities']['fan.room']['proactivity']);self.change('source','devices',enabled=True);self.assertTrue(self.policy.entity('proactivity','fan.room'))
 def test_location_toggle_is_separate_from_devices(self):
  self.change('source','presence');self.assertFalse(self.policy.entity('proactivity','person.karthik'));self.assertTrue(self.policy.entity('memory','person.karthik'));self.assertTrue(self.policy.entity('proactivity','fan.room'))
 def test_persist_reload_and_new_devices_default_off(self):
  self.change('entity','fan.room');loaded=InputPolicy(self.path,self.h,self.c);self.assertFalse(loaded.entity('proactivity','fan.room'));self.h.entities['light.new']={'entity_id':'light.new','domain':'light','area_id':'bedroom'};self.assertFalse(loaded.entity('memory','light.new'));self.assertEqual(self.path.stat().st_mode&0o777,0o600)
 def test_stale_revision_rejected_without_overwriting(self):
  revision=self.policy.revision;self.change('source','devices')
  with self.assertRaises(Conflict):self.policy.change({'revision':revision,'changes':[{'kind':'source','id':'presence','purpose':'memory','enabled':False}]})
  self.assertTrue(self.policy.source('memory','presence'))
 def test_invalid_batch_is_atomic(self):
  revision=self.policy.revision
  with self.assertRaises(ValueError):self.policy.change({'revision':revision,'changes':[{'kind':'entity','id':'fan.room','purpose':'memory','enabled':False},{'kind':'entity','id':'not_real','purpose':'memory','enabled':True}]})
  self.assertEqual(self.policy.revision,revision);self.assertTrue(self.policy.entity('memory','fan.room'))
 async def test_disabled_device_blocks_events_queues_and_models(self):
  self.change('entity','fan.room');self.p.on_event(event(subject='fan.room'));self.assertEqual(self.p.store.rows('episodes'),[])
  wid=self.p.store.wake('HA_NOTEWORTHY','SUNDAY_SELF','fan.room',['ev'],'incident');await self.p.cognize(self.p.store.get('wakes',wid));self.assertEqual(self.models.calls,[])
 async def test_memory_history_independent_and_no_disabled_recorder_read(self):
  self.change('entity','fan.room','memory');hist=NS(series=AsyncMock(return_value=([],[])));self.m.history=hist;self.h.household['people']={};await self.m.collect_history();hist.series.assert_not_awaited();self.assertTrue(self.p.in_scope('fan.room'))
 async def test_history_off_stops_recorder_collection(self):
  self.change('source','history','memory');self.m.history=NS(series=AsyncMock());r=await self.m.collect_history();self.assertEqual(r['status'],'disabled_by_input_selection');self.m.history.series.assert_not_awaited()
 def test_conversation_off_captures_nothing(self):
  self.change('source','conversation','memory');self.assertEqual(self.m.capture({'candidates':[]},request_id='x',user_text='hello',source='test'),[]);self.assertEqual(self.m.store.db.execute('select count(*) from evidence').fetchone()[0],0)
 def test_memory_toggle_preserves_existing_record_and_restores_visibility(self):
  s=self.m.store;ev=s.evidence('test','test',{});cid=s.submit({'type':'FACT','subject':'fan','statement':'A useful fan fact','entities':['fan.room'],'confidence':.9},'USER_EXPLICIT',[ev],'fact');mid=s.reconcile(cid,{'operation':'ADD','reason':'fixture'},[])['affected_memory_ids'][0]
  self.change('entity','fan.room','memory');self.m.enforce_scope();self.assertEqual(s.get(mid)['status'],'ACTIVE');self.assertEqual(s.retrieve('fan useful'),[]);self.change('entity','fan.room','memory',True);self.assertEqual(len(s.retrieve('fan useful')),1)
 def test_proactivity_memory_read_selection_independent_of_learning(self):
  self.change('source','learned_memory',enabled=True);self.assertTrue(self.p.memory_allowed());self.assertFalse(self.c.memory_read_enabled)
 def test_actions_semantic_service_names_are_metadata(self):
  self.assertTrue(self.p.in_scope({'event_type':'SUNDAY_ACTION_REQUESTED','subject':{'kind':'action','id':'abc','semantic_name':'fan.turn_on'}}));self.change('source','actions');self.assertFalse(self.p.in_scope({'event_type':'SUNDAY_ACTION_REQUESTED'}))
 async def test_changed_selection_rejects_inflight_outcome(self):
  context={'input_policy_revision':self.policy.revision};self.change('source','conversation');r=await self.p.apply({'id':'wake','data':{'subject':'fan.room'}},{'outcomes':[]},context);self.assertIn('suppressed',r[0]);self.assertEqual(self.p.store.rows('decisions'),[])
 def test_old_gate_revision_blocks_dispatch(self):
  from proactivity_v1.delivery_recheck import final_check
  row={'data':{'gate_context':{'input_policy_revision':self.policy.revision},'wake':{'entities':['fan.room']}}};self.change('source','conversation');self.assertFalse(final_check(self.p,row,{}))
 async def test_history_investigation_cannot_bypass_toggle(self):
  self.change('source','history')
  with self.assertRaises(ValueError):await self.p.investigate({'operation':'history','target':'fan.room'})
 async def test_context_omits_disabled_conversation_and_household(self):
  self.change('source','conversation');self.change('source','household');self.p.conversations=lambda:[{'role':'user','content':'fan room is noisy'}];c=await self.p.context(subject='fan.room');self.assertEqual(c['conversation'],[]);self.assertEqual(c['household'],{})
 async def test_http_auth_atomic_save_stale_conflict_and_no_commands(self):
  from main import create_app
  from aiohttp.test_utils import TestClient,TestServer
  world=NS(reconcile=AsyncMock());rt=NS(config=Config(daemon_token='test'),home=self.h,input_policy=self.policy,input_controls_lock=asyncio.Lock(),proactivity=self.p,world=world,start=AsyncMock(),close=AsyncMock())
  client=TestClient(TestServer(create_app(rt)));await client.start_server()
  try:
   self.assertEqual((await client.get('/v1/inputs')).status,401);self.assertEqual((await client.post('/v1/inputs/manage',json={})).status,401)
   h={'Authorization':'Bearer test'};r=await client.get('/v1/inputs',headers=h);self.assertEqual(r.status,200);revision=(await r.json())['revision']
   body={'revision':revision,'changes':[{'kind':'entity','id':'fan.room','purpose':'proactivity','enabled':False}]}
   r=await client.post('/v1/inputs/manage',headers=h,json=body);self.assertEqual(r.status,200);self.assertFalse(self.policy.entity('proactivity','fan.room'));world.reconcile.assert_awaited_once();self.assertEqual(self.models.calls,[])
   self.assertEqual((await client.post('/v1/inputs/manage',headers=h,json=body)).status,409)
  finally:await client.close()
 def test_memory_inflight_result_held_after_selection_changes(self):
  s=self.m.store;ev=s.evidence('test','test',{});cid=s.submit({'type':'FACT','subject':'fan','statement':'A useful fan fact','entities':['fan.room'],'confidence':.9},'USER_EXPLICIT',[ev],'flight');revision=self.policy.revision
  self.change('source','conversation','memory')
  result=s.reconcile(cid,{'operation':'ADD','reason':'fixture'},[],{'input_policy_revision':revision});self.assertEqual(result['status'],'input_selection_changed');self.assertEqual(s.db.execute('select count(*) from memories').fetchone()[0],0);self.assertEqual(s.candidates(),[])
  self.change('source','conversation','memory',True);self.assertEqual(len(s.candidates()),1)
 async def test_automation_selection_applies_before_pending_work_on_start(self):
  self.change('source','automations',enabled=True);self.assertTrue(self.p.episodes.exclude_automations);await self.p.start();self.assertFalse(self.p.episodes.exclude_automations)
