import json,time,unittest,copy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch
from body_context import snapshot,brightness
from output import TurnOutput,current_output
from agent import _OpenAIBackend,_GeminiBackend
import home_tools
class BodyTests(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  def e(eid,state,attrs=None):return {'entity_id':eid,'domain':eid.split('.')[0],'name':eid,'state':state,'available':True,'attributes':attrs or {}}
  rows=[e('select.sunday_body_expression','Auto',{'options':['Auto','Happy']}),e('select.sunday_body_face_state','idle'),e('sensor.sunday_body_room_light','4.9'),e('switch.sunday_body_night_mode','off'),e('select.sunday_body_night_screen','Sleep',{'options':['Sleep','Dim face']}),e('number.sunday_body_volume','255',{'min':0,'max':255,'step':5})]
  self.m=NS(entities={r['entity_id']:r for r in rows},states_observed_at=time.time(),areas={},devices={},household={},services_cache=[{'domain':'switch','services':{'turn_on':{'fields':{}},'turn_off':{'fields':{}}}},{'domain':'select','services':{'select_option':{'fields':{'option':{}}}}},{'domain':'number','services':{'set_value':{'fields':{'value':{}}}}}])
 def test_brightness_boundaries(self):
  for lux,expected in [(0,'very dark'),(4.99,'very dark'),(5,'barely visible'),(10,'barely visible'),(10.1,'dim'),(20,'dim'),(20.1,'well lit')]:self.assertEqual(brightness(lux)['category'],expected)
  for v in ['unknown',None,-1,float('nan'),float('inf')]:self.assertIsNone(brightness(v))
 async def test_snapshot_contains_real_contracts_without_io(self):
  self.m.ha=NS(request=AsyncMock(side_effect=AssertionError('No HA calls')))
  s=await snapshot(self.m);self.assertTrue(s['connected']);self.assertEqual(s['brightness']['category'],'very dark')
  controls={c['entity_id']:c for c in s['controls']};self.assertIn('set_value',controls['number.sunday_body_volume']['actions']);self.assertEqual(controls['number.sunday_body_volume']['settings']['max'],255);self.assertEqual(controls['select.sunday_body_night_screen']['settings']['options'],['Sleep','Dim face']);self.assertNotIn('select.sunday_body_expression',controls);self.m.ha.request.assert_not_awaited()
 async def test_stale_state_not_asserted_or_actionable(self):
  self.m.states_observed_at=time.time()-60;s=await snapshot(self.m);self.assertIsNone(s['connected']);self.assertIsNone(s['brightness']);self.assertTrue(all(not c['available'] and c['state'] is None for c in s['controls']))
 async def test_offline_and_missing(self):
  for e in self.m.entities.values():e.update(state='unavailable',available=False)
  self.assertFalse((await snapshot(self.m))['connected']);self.m.entities={};self.assertIsNone((await snapshot(self.m))['connected'])
 async def test_missing_catalog_does_not_trigger_discovery(self):
  self.m.services_cache=None;s=await snapshot(self.m);self.assertIn('controls_unavailable',s);self.assertNotIn('controls',s)
 async def test_body_context_reaches_both_providers_and_channels(self):
  out=TurnOutput('body',AsyncMock());out.body_context='BODY_TEST_SNAPSHOT';out.presence_context='';token=current_output.set(out)
  try:
   b=_OpenAIBackend.__new__(_OpenAIBackend);b._turn_context='{}'
   for telegram in [False,True]:self.assertIn('BODY_TEST_SNAPSHOT',b._system_messages(telegram)[-1]['content'])
   g=_GeminiBackend.__new__(_GeminiBackend);g._turn_context='{}';g._insights=[];g._tools=[];g._tools_telegram=[];g._types=NS(GenerateContentConfig=lambda **kw:NS(**kw),ThinkingConfig=lambda **kw:kw)
   for telegram in [False,True]:self.assertIn('BODY_TEST_SNAPSHOT',g._make_config(telegram=telegram).system_instruction)
  finally:current_output.reset(token)
class BodyActionTests(unittest.IsolatedAsyncioTestCase):
 def model(self,domain,state,attrs):
  eid=domain+'.sunday_body_test';e={'entity_id':eid,'state':state,'attributes':attrs};ha=NS(get_state=AsyncMock(return_value=e),request=AsyncMock(return_value=[{'domain':domain,'services':{'select_option':{'fields':{'option':{}}},'set_value':{'fields':{'value':{}}}}}]),actions_enabled=True,call_service=AsyncMock(return_value='ok'));return NS(ha=ha,entities={eid:e},household={}),eid
 async def test_direct_select_action_without_discovery(self):
  m,eid=self.model('select','Off',{'options':['Off','Red']});r=await home_tools.action(m,{'domain':'select','service':'select_option','entity_ids':[eid],'data':{'option':'Red'}});self.assertTrue(r['service_completed']);m.ha.call_service.assert_awaited_once_with('select','select_option',{'entity_id':[eid],'option':'Red'})
 async def test_invalid_option_sends_nothing(self):
  m,eid=self.model('select','Off',{'options':['Off','Red']})
  with self.assertRaises(ValueError):await home_tools.action(m,{'domain':'select','service':'select_option','entity_ids':[eid],'data':{'option':'Purple'}})
  m.ha.call_service.assert_not_awaited()
 async def test_valid_numeric_and_reject_bad_values(self):
  m,eid=self.model('number','255',{'min':0,'max':255});r=await home_tools.action(m,{'domain':'number','service':'set_value','entity_ids':[eid],'data':{'value':120}});self.assertTrue(r['service_completed']);m.ha.call_service.reset_mock()
  for value in [-1,256,True,'100',float('nan'),float('inf')]:
   with self.assertRaises(ValueError):await home_tools.action(m,{'domain':'number','service':'set_value','entity_ids':[eid],'data':{'value':value}})
  m.ha.call_service.assert_not_awaited()
 async def test_unavailable_or_actions_disabled_block(self):
  m,eid=self.model('number','unavailable',{'min':0,'max':255});args={'domain':'number','service':'set_value','entity_ids':[eid],'data':{'value':120}}
  with self.assertRaises(ValueError):await home_tools.action(m,args)
  m.ha.actions_enabled=False;self.assertEqual((await home_tools.action(m,args))['status'],'blocked_actions_disabled');m.ha.call_service.assert_not_awaited()
if __name__=='__main__':unittest.main()
