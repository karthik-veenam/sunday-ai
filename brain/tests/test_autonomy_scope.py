import tempfile,unittest,json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from config import Config
from autonomy_scope import allowed,entity_allowed
from memory_v1.service import MemoryService
from proactivity_v1.service import Service
from test_proactivity_v1 import event,FakeModels

def home():
 return NS(household={'primary_area':'bedroom','primary_person':'Karthik','area_names':{'bedroom':['RoKa','my room']},'occupants':{'bedroom':['Karthik'],'hall':['Other']},'people':{}},entities={eid:{'entity_id':eid,'area_id':area,'domain':eid.split('.')[0]} for eid,area in [('fan.room','bedroom'),('switch.hall','hall'),('sensor.unknown',None),('automation.mixed','bedroom'),('scene.room','bedroom')]},programs={'automation.mixed':{'related_entities':['fan.room','switch.hall']},'scene.room':{'related_entities':['fan.room']}},refresh=AsyncMock())

class ScopeTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.temp=tempfile.TemporaryDirectory();self.h=home();self.c=Config(autonomy_primary_room_only=True,memory_write_enabled=True,ha_attention_enabled=True,proactive_cognition_enabled=True)
  self.m=MemoryService(Path(self.temp.name)/'m.db',self.c,home=self.h)
  self.models=FakeModels();self.p=Service(Path(self.temp.name)/'p.db',self.c,home=self.h,models=self.models)
 async def asyncTearDown(self):
  await self.m.shutdown();await self.p.close();self.temp.cleanup()
 def test_entity_area_alias_and_mixed_program_scope(self):
  for value in ['fan.room',{'areas':['RoKa']},'person:Karthik','scene.room',{'statement':'Likes tea'}]:self.assertTrue(allowed(self.h,self.c,value),value)
  for value in ['switch.hall','sensor.unknown','fan.missing','automation.mixed','person:Other',{'areas':['hall']}, {'statement':'Use switch.hall after dinner'}]:self.assertFalse(allowed(self.h,self.c,value),value)
 async def test_history_fallback_and_active_cross_room_filtered(self):
  hist=NS(series=AsyncMock(return_value=([],[])));self.m.history=hist
  for world in [None,NS(selected=lambda:set(self.h.entities))]:
   self.h.world=world;hist.series.reset_mock();await self.m.collect_history()
   self.assertEqual([c.args[0]['entity_id'] for c in hist.series.call_args_list],['fan.room'])
 def test_outside_event_does_not_create_episode(self):
  self.p.on_event(event(subject='switch.hall'));self.assertEqual(self.p.store.rows('episodes',limit=20),[])
  self.p.on_event(event(i='2',subject='fan.room'));self.assertEqual(len(self.p.store.rows('episodes',limit=20)),1)
 async def test_outside_wake_and_investigation_no_model(self):
  wid=self.p.store.wake('HA_NOTEWORTHY','SUNDAY_SELF','switch.hall',['ev'],'test')
  await self.p.cognize(self.p.store.get('wakes',wid));self.assertEqual(self.p.store.get('wakes',wid)['state'],'CANCELLED_SCOPE');self.assertEqual(self.models.calls,[])
  with self.assertRaises(ValueError):await self.p.investigate({'operation':'world','target':'switch.hall'})
 def test_existing_memory_retirement_and_replay_block(self):
  s=self.m.store;gate=s.scope_filter;s.scope_filter=lambda _:True
  mids=[]
  for name,entities in [('room',['fan.room']),('other',['switch.hall']),('tea',[])]:
   ev=s.evidence('test','test',{'name':name});cid=s.submit({'type':'FACT','subject':name,'statement':name+' useful fact','entities':entities,'confidence':.9},'USER_EXPLICIT',[ev],name);mids.append(s.reconcile(cid,{'operation':'ADD','reason':'fixture'},[])['affected_memory_ids'][0])
  s.scope_filter=gate;self.m.enforce_scope()
  self.assertEqual([s.get(mid)['status'] for mid in mids],['ACTIVE','RETIRED','ACTIVE'])
  self.assertFalse(any(m['entities']==['switch.hall'] for m in s.retrieve('other useful',min_confidence=0)))
  cid=s.submit({'type':'FACT','subject':'outside','statement':'Outside fact','entities':['switch.hall']},'USER_EXPLICIT',[ev],'replay')
  self.assertEqual(s.db.execute('select state from inbox where id=?',(cid,)).fetchone()[0],'DELETED')
 async def test_persisted_episode_reference_and_delivery_guard(self):
  from proactivity_v1.delivery_recheck import recheck,final_check
  eid=self.p.store.put('episodes',{'events':[event(subject='switch.hall')]},dedupe='old',state='OPEN')
  tid=self.p.store.put('temporal',{'kind':'EPISODE_CLOSE','episode_id':eid},dedupe='timer')
  self.assertFalse(self.p.in_scope({'refs':[tid]}));self.p.enforce_scope()
  self.assertEqual(self.p.store.get('temporal',tid)['state'],'CANCELLED_SCOPE')
  row={'data':{'wake':{'entities':['switch.hall']}}}
  self.assertFalse((await recheck(self.p,row))['eligible']);self.assertFalse(final_check(self.p,row,{}))
 def test_historical_outside_evidence_cannot_relearn_patterns(self):
  from memory_v1.behavior import learn_patterns
  from test_memory_v1 import Behavior
  # Old recorder evidence remains auditable, but must never feed the reducer again.
  for sample in Behavior().evidence():
   sample.update(subject='switch.hall',area='hall')
   self.m.store.evidence('device_session','ha_recorder',sample)
  self.assertEqual(learn_patterns(self.m.store),[])
  self.assertEqual(self.m.store.db.execute('select count(*) from inbox').fetchone()[0],0)

 def test_configured_location_identity_is_allowed_without_room_assignment(self):
  self.h.household['people']={'karthik':{'ha_person':'person.karthik','presence_zones':{'home':'zone.home','office':'zone.work'}},'Other':{'ha_person':'person.other'}}
  self.assertTrue(allowed(self.h,self.c,{'subject':{'id':'person:Karthik','home_model_entity_id':'person.karthik','area':None},'change':{'new':'OFFICE'}}))
  self.assertTrue(allowed(self.h,self.c,{'source_ref':{'entity_id':'person.karthik'}}))
  self.assertFalse(allowed(self.h,self.c,'person.other'))
  e=event(i='location',subject='person:Karthik',typ='PRESENCE_CHANGED')
  e['subject']['home_model_entity_id']='person.karthik'
  self.p.on_event(e)
  self.assertEqual(len(self.p.store.rows('episodes',limit=20)),1)
