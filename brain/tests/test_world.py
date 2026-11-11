import asyncio,copy,json,tempfile,time,unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
import test_fast_mode as fixtures
from config import Config
from world import WorldModel,WorldStore,WorldFact,NormalizedEvent,epoch,utc
from presence import PresenceSnapshot

class Presence:
 def __init__(self,state='HOME'):self.state=state
 async def get_presence(self,**kwargs):return PresenceSnapshot('Karthik',self.state,'home_assistant',utc(),evidence_at=utc())

class WorldTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'world.db';self.home=fixtures.Model();self.presence=Presence();self.config=Config(world_model_enabled=True)
  self.home.ha.get_states=AsyncMock(side_effect=lambda:list(copy.deepcopy(self.home.entities).values()))
  self.world=WorldModel(self.path,self.home,self.presence,self.config)
  await self.world.reconcile(startup=True);self.world.connected=True
 async def asyncTearDown(self):await self.world.close();self.temp.cleanup()
 def raw(self,eid,value,ctx=None,attrs=None,stamp=None):
  stamp=stamp or utc()
  state=copy.deepcopy(self.home.entities[eid]);state.update(state=value,last_updated=stamp,last_changed=stamp,context=ctx or {})
  if attrs is not None:state['attributes']=attrs
  old=copy.deepcopy(self.home.entities[eid]);self.home.entities[eid].update(state=state['state'],attributes=state['attributes'],context=state['context'],last_updated=state['last_updated'],available=value not in ('unknown','unavailable'))
  event={'event_type':'state_changed','time_fired':state['last_updated'],'context':ctx or {},'data':{'entity_id':eid,'old_state':old,'new_state':state}}
  return event
 def execution(self,typ,eid,ctx,targets=None):
  data={'entity_id':eid} if typ!='call_service' else {'domain':'light','service':'turn_on','service_data':{'entity_id':targets or ['light.a']}}
  self.world.raw_event({'event_type':typ,'time_fired':utc(),'context':ctx,'data':data})
 async def test_bootstrap_no_fake_events(self):
  self.assertEqual(self.world.store.events(),[]);self.assertEqual(self.world.store.get('light.a')['value'],'off')
 async def test_current_replacement_and_history(self):
  self.world.raw_event(self.raw('light.a','on'));event=self.world.store.events('light.a')[0]
  self.assertEqual(event['event_type'],'DEVICE_STATE_CHANGED');self.assertEqual(event['change'],{'property':'state','old':'off','new':'on'})
  self.assertEqual(self.world.store.get('light.a')['value'],'on');self.assertEqual(self.world.store.get('light.a')['last_event_id'],event['event_id'])
 async def test_unchanged_metadata_filtered(self):
  e=self.raw('light.a','off',attrs={'supported_color_modes':['brightness'],'irrelevant_timestamp':'now'})
  self.world.raw_event(e);self.assertFalse(self.world.store.events());self.assertGreater(self.world.store.metrics()['events_filtered_as_noise'],0)
 async def test_duplicate_raw_durable(self):
  event=self.raw('light.a','on');self.world.raw_event(event);before=len(self.world.store.events());self.world.raw_event(event)
  self.assertEqual(len(self.world.store.events()),before);self.assertEqual(self.world.store.metrics()['events_deduped'],1)
 async def test_availability_transition(self):
  self.world.raw_event(self.raw('light.a','unavailable'));events=self.world.store.events('light.a');self.assertEqual([e['event_type'] for e in events],['AVAILABILITY_CHANGED']);self.assertEqual(self.world.store.get('light.a')['availability'],'unavailable')
 async def test_numeric_noise_accumulates_against_meaningful_anchor(self):
  e=fixtures.entity('temp','Room Temperature','sensor');e['attributes']={'device_class':'temperature','unit_of_measurement':'°C'};e['state']='25';self.home.entities[e['entity_id']]=e
  await self.world.reconcile()
  self.world.raw_event(self.raw('sensor.temp','25.04'));self.assertFalse(self.world.store.events('sensor.temp'));self.assertEqual(self.world.store.get('sensor.temp')['value'],25.04)
  self.world.raw_event(self.raw('sensor.temp','25.12'));self.assertEqual(self.world.store.events('sensor.temp')[0]['event_type'],'SENSOR_VALUE_CHANGED')
 async def test_property_event(self):
  self.world.raw_event(self.raw('light.a','off',attrs={'supported_color_modes':['brightness'],'brightness':10}));self.assertFalse(self.world.store.events('light.a'))
  self.world.raw_event(self.raw('light.a','off',attrs={'supported_color_modes':['brightness'],'brightness':50}));self.assertEqual(self.world.store.events('light.a')[0]['event_type'],'DEVICE_PROPERTY_CHANGED')
 async def test_presence_semantics(self):
  snapshot=await self.presence.get_presence();self.world.ingest_presence(snapshot);self.assertFalse(self.world.store.events('person:Karthik'))
  self.presence.state='OFFICE';self.world.ingest_presence(await self.presence.get_presence());self.assertEqual(self.world.store.get('person:Karthik','presence')['value'],'OFFICE');self.assertEqual(self.world.store.events('person:Karthik')[0]['event_type'],'PRESENCE_CHANGED')
 async def test_fast_action_confirmed_rest_context(self):
  output=SimpleNamespace(request_id='r',initiator={'type':'USER','id':'Karthik'},execution_path='fast_mode')
  aid=self.world.action_requested('light','turn_on',{'entity_id':['light.a']},output)
  event=self.raw('light.a','on',{'id':'ctx','parent_id':None,'user_id':'ha-user'})
  self.world.raw_event(event);self.world.action_result(aid,[event['data']['new_state']]);facts=self.world.store.get('light.a')
  self.assertEqual(facts['provenance']['attribution'],'CONFIRMED');self.assertEqual(facts['provenance']['initiator']['id'],'Karthik');self.assertEqual(facts['provenance']['via']['path'],'fast_mode');self.assertFalse(facts['provenance']['physical_success_confirmed'])
  ev=next(e for e in self.world.store.events('light.a') if e['event_type']=='DEVICE_STATE_CHANGED');self.assertEqual(ev['correlation']['request_id'],'r')
 async def test_normal_tool_same_authority(self):
  aid=self.world.action_requested('light','turn_on',{'entity_id':['light.a']},SimpleNamespace(request_id='normal',initiator={'type':'USER','id':'Karthik'},execution_path='normal_tool'))
  state=self.raw('light.a','on',{'id':'normalctx'})['data']['new_state'];self.world.action_result(aid,[state])
  self.assertEqual(self.world.store.get('light.a')['provenance']['via']['path'],'normal_tool')
 async def test_missing_context_remains_unknown(self):
  aid=self.world.action_requested('light','turn_on',{'entity_id':['light.a']})
  event=self.raw('light.a','on');self.world.action_result(aid,[event['data']['new_state']]);self.assertEqual(self.world.store.get('light.a')['provenance']['attribution'],'UNKNOWN')
 async def test_assumed_state_not_physical_confirmation(self):
  self.home.entities['light.a']['attributes']['assumed_state']=True
  aid=self.world.action_requested('light','turn_on',{'entity_id':['light.a']});state=self.raw('light.a','on',{'id':'ir'})['data']['new_state'];self.world.action_result(aid,[state]);self.assertFalse(self.world.store.get('light.a')['provenance']['physical_success_confirmed'])
 async def test_automation_confirmed_only_with_chain_and_service(self):
  ctx={'id':'autoctx'};self.execution('automation_triggered','automation.test',ctx);self.execution('call_service','',ctx)
  self.world.raw_event(self.raw('light.a','on',{'id':'child','parent_id':'autoctx'}));p=self.world.store.get('light.a')['provenance'];self.assertEqual(p['attribution'],'CONFIRMED');self.assertEqual(p['initiator']['type'],'HA_AUTOMATION')
 async def test_temporal_automation_does_not_become_cause(self):
  self.execution('automation_triggered','automation.test',{'id':'unrelated'});self.world.raw_event(self.raw('light.a','on',{'id':'device'}));self.assertEqual(self.world.store.get('light.a')['provenance']['attribution'],'UNKNOWN')
  relation=self.world.explain('light.a')['temporal_relations'];self.assertEqual(relation[0]['attribution'],'POSSIBLE')
 async def test_unknown_device_not_guessed_physical_or_user(self):
  self.world.raw_event(self.raw('light.a','on'));self.assertEqual(self.world.store.get('light.a')['provenance']['initiator']['type'],'UNKNOWN')
 async def test_execution_context_without_service_is_strong_only(self):
  ctx={'id':'auto'};self.execution('automation_triggered','automation.test',ctx);self.world.raw_event(self.raw('light.a','on',ctx));self.assertEqual(self.world.store.get('light.a')['provenance']['attribution'],'STRONG')
 async def test_out_of_order_automation_evidence_enriches(self):
  ctx={'id':'auto'};self.world.raw_event(self.raw('light.a','on',ctx));self.execution('automation_triggered','automation.test',ctx);self.execution('call_service','',ctx);self.assertEqual(self.world.store.get('light.a')['provenance']['attribution'],'CONFIRMED')
  self.assertEqual(self.world.store.events('light.a')[0]['correlation']['automation_id'],'automation.test')
 async def test_service_completed_without_transition_not_state_cause(self):
  aid=self.world.action_requested('light','turn_on',{'entity_id':['light.a']});self.world.action_result(aid,[]);self.assertEqual(self.world.store.get('light.a')['provenance']['attribution'],'UNKNOWN');self.assertFalse(self.world.store.events('light.a'))
 async def test_action_unknown(self):
  aid=self.world.action_requested('light','turn_on',{'entity_id':['light.a']});self.world.action_result(aid,unknown=True,reason='timeout');self.assertEqual(self.world.store.events(aid)[0]['event_type'],'SUNDAY_ACTION_UNKNOWN')
 async def test_restart_reconcile_gap_no_fake_transition(self):
  self.world.raw_event(self.raw('light.a','on'));before=len(self.world.store.events());await self.world.close()
  self.home.entities['light.a']['state']='off';self.home.entities['light.a']['last_updated']=utc()
  self.world=WorldModel(self.path,self.home,self.presence,self.config);await self.world.reconcile(startup=True)
  self.assertEqual(len(self.world.store.events()),before);fact=self.world.store.get('light.a');self.assertEqual(fact['value'],'off');self.assertIsNone(fact['last_event_id']);self.assertEqual(fact['provenance']['attribution'],'UNKNOWN')
 async def test_restart_duplicate_raw_stays_deduped(self):
  event=self.raw('light.a','on');self.world.raw_event(event);before=len(self.world.store.events());await self.world.close();self.world=WorldModel(self.path,self.home,self.presence,self.config);self.world.raw_event(event);self.assertEqual(len(self.world.store.events()),before)
 async def test_older_event_cannot_overwrite_current(self):
  self.world.raw_event(self.raw('light.a','on'));older=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat();self.world.raw_event(self.raw('light.a','off',stamp=older));self.assertEqual(self.world.store.get('light.a')['value'],'on')
 async def test_fresh_and_stale_reads(self):
  calls=0
  original=self.home.ha.get_state
  async def tracked(eid):
   nonlocal calls;calls+=1;return await original(eid)
  self.home.ha.get_state=tracked
  self.assertEqual((await self.world.fresh_entities(['light.a']))['light.a']['state'],'off');self.assertEqual(calls,0)
  self.world.connected=False;await self.world.fresh_entities(['light.a']);self.assertEqual(calls,1)
 async def test_removed_property_cannot_stay_current(self):
  self.world.raw_event(self.raw('light.a','on',attrs={'brightness':51}));self.world.raw_event(self.raw('light.a','on',attrs={}));self.assertIsNone(self.world.store.get('light.a','brightness'));self.assertNotIn('brightness',self.world.state_view('light.a')['attributes'])
 async def test_relevant_projection(self):
  self.world.raw_event(self.raw('fan.f','on'));context=await self.world.relevant_world_context('why is the fan on?',request_id='q');self.assertIn('fan.f',context);self.assertNotIn('light.a',context);self.assertIn('UNKNOWN',context);self.assertLessEqual(len(context),5000)
 async def test_exact_moon_projection(self):
  context=await self.world.relevant_world_context('is Moon on?');self.assertIn('light.a',context);self.assertNotIn('fan.f',context)
 async def test_unrelated_projection_empty(self):
  for query in ('what is the capital of Japan?','write an email to my manager','write an email about Moon','tell me a joke','Moon orbital period'):
   with self.subTest(query=query):self.assertEqual(await self.world.relevant_world_context(query),'')
 async def test_office_here_no_home_projection(self):
  self.presence.state='OFFICE';self.assertEqual(await self.world.relevant_world_context("what's the temperature here?"),'')
 async def test_projection_budget_and_no_gps(self):
  context=await self.world.relevant_world_context('why are Moon and Cove on?');self.assertLessEqual(len(context),5000)
  self.assertNotIn('latitude',context);self.assertNotIn('longitude',context)
 async def test_read_tools_bounded(self):
  from world_tools import read
  for i in range(45):self.world.raw_event(self.raw('light.a','on' if i%2==0 else 'off'))
  result=await read(self.world,{'operation':'events','subject':'light.a','limit':9999});self.assertLessEqual(len(result['events']),40);self.assertLessEqual(len(json.dumps(result)),14000)
 async def test_retention(self):
  self.world.raw_event(self.raw('light.a','on'));self.world.store.db.execute('update events set observed=0');self.world.store.prune();self.assertFalse(self.world.store.events());self.assertEqual(self.world.store.get('light.a')['value'],'on')
 async def test_read_only_fast_world_query_no_model(self):
  from fast_mode import FastMode
  from output import TurnOutput,current_output
  import home_tools
  self.home.world=self.world;home_tools.configure(self.home)
  fast=FastMode(Path(self.temp.name)/'fast.db');out=TurnOutput('query',AsyncMock());token=current_output.set(out)
  try:
   result=await fast.attempt(self.home,'is the fan on?','c','q','test',True,True,'all',presence=await self.presence.get_presence());self.assertTrue(result['handled']);self.assertEqual(result['model_calls'],0);self.assertTrue(result['world_read'])
  finally:current_output.reset(token);fast.db.close()
 async def test_ha_authority_audits_one_dispatch(self):
  from ha import HAClient
  from output import TurnOutput,current_output
  h=HAClient('http://example','token',True);h.world=self.world
  state=self.raw('light.a','on',{'id':'receipt'})['data']['new_state'];h.request=AsyncMock(return_value=[state]);out=TurnOutput('r',AsyncMock());out.initiator={'type':'USER','id':'Karthik'};out.execution_path='fast_mode';tok=current_output.set(out)
  try:self.assertEqual(await h.call_service('light','turn_on',{'entity_id':['light.a']}),'ok');self.assertEqual(h.request.await_count,1);self.assertEqual(self.world.store.get('light.a')['provenance']['attribution'],'CONFIRMED')
  finally:current_output.reset(tok);await h.close()
 async def test_cancelled_dispatch_audited_unknown_no_retry(self):
  from ha import HAClient
  h=HAClient('http://example','token',True);h.world=self.world;h.request=AsyncMock(side_effect=asyncio.CancelledError)
  with self.assertRaises(asyncio.CancelledError):await h.call_service('light','turn_on',{'entity_id':['light.a']})
  self.assertEqual(h.request.await_count,1);self.assertEqual(self.world.store.events()[0]['event_type'],'SUNDAY_ACTION_UNKNOWN')
  await h.close()
 async def test_poll_before_websocket_preserves_authoritative_transition(self):
  raw=self.raw('light.a','on',{'id':'race'})
  self.world.ingest_state(raw['data']['new_state'],reconcile=True)
  self.assertFalse(self.world.store.events('light.a'))
  self.world.raw_event(raw);events=self.world.store.events('light.a');self.assertEqual(len(events),1);self.assertEqual(events[0]['change']['old'],'off')
 async def test_script_and_scene_events(self):
  for domain in ('script','scene'):
   entity=fixtures.entity('test','Test',domain);self.home.entities[entity['entity_id']]=entity
  self.world.raw_event(self.raw('script.test','on',{'id':'script'}));self.assertEqual(self.world.store.events('script.test')[0]['event_type'],'SCRIPT_EXECUTED')
  self.world.raw_event(self.raw('scene.test',utc(),{'id':'scene'}));self.assertEqual(self.world.store.events('scene.test')[0]['event_type'],'SCENE_ACTIVATED')
 async def test_scene_attribute_noise_not_activation(self):
  entity=fixtures.entity('test','Test','scene');self.home.entities[entity['entity_id']]=entity
  self.world.raw_event(self.raw('scene.test','off',attrs={'friendly_name':'Renamed'}));self.assertFalse(self.world.store.events('scene.test'))
 async def test_event_bus_consumer_dedupe(self):
  received=[];self.world.bus.subscribe(received.append)
  event=self.world.event('SUNDAY_ACTION_REQUESTED',{'id':'test','kind':'action'},'status',None,'requested',utc())
  self.world.bus.publish(event);self.world.bus.publish(event);self.assertEqual(len(received),1)

class RuntimeProjectionTests(unittest.IsolatedAsyncioTestCase):
 async def test_unrelated_actual_runtime_context_no_home_dump_and_shadow(self):
  import runtime as module
  from runtime import Runtime
  from output import current_output
  seen=[]
  class Brain:
   async def respond_as_text(self,text):seen.append(current_output.get().home_context);return 'Tokyo.'
   async def process(self,text,out):seen.append(out.home_context);await out.speak('Tokyo.');return False
  for injection in (False,True):
   with self.subTest(injection=injection),tempfile.TemporaryDirectory() as d:
    root=Path(d);(root/'state').mkdir();(root/'household.json').write_text('{"primary_person":"Karthik","primary_area":"bedroom"}')
    with patch.object(module,'ROOT',root):
     runtime=Runtime(Config(world_model_enabled=True,world_context_injection_enabled=injection),agent_factory=Brain)
     runtime.presence=Presence();runtime.world.presence=runtime.presence;runtime.home.context=AsyncMock(return_value='fan ON; AC cool; Moon on')
     async def emit(e):pass
     try:
      for source in ('telegram','ha'):
       await runtime.turn({'request_id':source,'conversation_id':source,'source':source,'text':'what is the capital of Japan?'},emit)
       if injection:
        self.assertNotIn('fan',seen[-1]);self.assertNotIn('AC',seen[-1]);self.assertNotIn('Moon',seen[-1])
       else:self.assertIn('fan ON',seen[-1])
     finally:await runtime.close()

class AdditionalWorldTests(unittest.IsolatedAsyncioTestCase):
 asyncSetUp=WorldTests.asyncSetUp
 asyncTearDown=WorldTests.asyncTearDown
 raw=WorldTests.raw
 async def test_newer_home_snapshot_never_overwritten_by_older_world(self):
  self.home.entities['light.a']['state']='on';self.home.entities['light.a']['last_updated']=utc()
  result=await self.world.fresh_entities(['light.a']);self.assertEqual(result['light.a']['state'],'on')
 async def test_out_of_order_evidence_retained_without_current_corruption(self):
  self.world.raw_event(self.raw('light.a','on'))
  past=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()
  self.world.raw_event(self.raw('light.a','off',stamp=past));self.assertEqual(self.world.store.get('light.a')['value'],'on');self.assertEqual(self.world.store.metrics()['out_of_order_events_retained'],1)
 async def test_same_presence_restart_preserves_last_transition(self):
  self.presence.state='OFFICE';self.world.ingest_presence(await self.presence.get_presence());previous=self.world.store.get('person:Karthik','presence')['last_event_id']
  self.world.ingest_presence(await self.presence.get_presence(),reconcile=True);self.assertEqual(self.world.store.get('person:Karthik','presence')['last_event_id'],previous)

 async def test_rest_before_stream_dedupes_the_same_transition(self):
  aid=self.world.action_requested('light','turn_on',{'entity_id':['light.a']})
  raw=self.raw('light.a','on',{'id':'receipt-first'})
  raw['time_fired']=(datetime.now(timezone.utc)+timedelta(milliseconds=10)).isoformat()
  self.world.action_result(aid,[raw['data']['new_state']]);self.world.raw_event(raw)
  transitions=[e for e in self.world.store.events('light.a') if e['event_type']=='DEVICE_STATE_CHANGED']
  self.assertEqual(len(transitions),1);self.assertEqual(transitions[0]['provenance']['attribution'],'CONFIRMED')

 async def test_scene_context_distinguished_from_unknown_user(self):
  scene=fixtures.entity('test','Test Scene','scene');self.home.entities[scene['entity_id']]=scene
  ctx={'id':'scene-context'}
  self.world.raw_event(self.raw('scene.test',utc(),ctx))
  self.world.raw_event(self.raw('light.a','on',ctx))
  p=self.world.store.get('light.a')['provenance'];self.assertEqual(p['initiator']['type'],'HA_SCENE');self.assertEqual(p['attribution'],'STRONG')
