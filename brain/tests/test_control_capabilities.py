import copy,json,unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch
from home_model import HomeModel,snapshot_relevant
from home_capabilities import device_controls,automation_catalog,service_fields
from fast_mode import parse,Miss
from output import TurnOutput,current_output
import home_tools

class HA:
 actions_enabled=True
 def __init__(self):self.calls=[];self.catalog_reads=0
 async def request(self,*args):
  self.catalog_reads+=1
  return [
   {'domain':'climate','services':{
    'set_temperature':{'fields':{'temperature':{},'hvac_mode':{}},'target':{'entity':[{'domain':['climate'],'supported_features':[1]}]}},
    'set_fan_mode':{'fields':{'fan_mode':{}},'target':{'entity':[{'domain':['climate'],'supported_features':[8]}]}},
    'set_preset_mode':{'fields':{'preset_mode':{}},'target':{'entity':[{'domain':['climate'],'supported_features':[16]}]}}}},
   {'domain':'light','services':{'turn_on':{'fields':{'brightness_pct':{},'transition':{}}},'turn_off':{'fields':{}}}},
   {'domain':'automation','services':{'turn_on':{'fields':{}},'turn_off':{'fields':{'stop_actions':{}}},'toggle':{'fields':{}}}}]
 async def get_state(self,eid):return copy.deepcopy(self.model.entities[eid])
 async def get_states(self):return [copy.deepcopy(e) for e in self.model.entities.values()]
 async def call_service(self,d,s,data):
  self.calls.append((d,s,data));
  for eid in data['entity_id']:self.model.entities[eid]['state']='on' if s=='turn_on' else 'off'
  return 'ok'

class CapabilityTests(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.ha=HA();self.m=HomeModel(self.ha,{'primary_area':'room'});self.ha.model=self.m
  self.m.areas={'room':{'name':'Main room'},'guest':{'name':'Guest'}};self.m.devices={}
  def e(eid,name,area='room',state='off',attrs=None):return {'entity_id':eid,'domain':eid.split('.')[0],'name':name,'aliases':[],'area_id':area,'state':state,'available':True,'attributes':attrs or {}}
  self.m.entities={x['entity_id']:x for x in (
   e('climate.unit','Room AC',attrs={'supported_features':393,'min_temp':18,'max_temp':30,'hvac_modes':['off','cool'],'temperature':24,'fan_mode':'high','fan_modes':['low','mid','high'],'local_key':'NEVER_INCLUDE'}),
   e('light.binary','Lamp',attrs={'supported_color_modes':['onoff']}),
   e('fan.guest','Guest fan','guest'),
   e('automation.comfort','Room Comfort'),e('automation.sleep','Guest Sleep','guest',state='on'),e('automation.global_rule','Global rule',None),
  )}
  self.m.programs={'automation.comfort':{'entity_id':'automation.comfort','source':{'description':'Maintain comfort'},'related_entities':['climate.unit']},'automation.sleep':{'entity_id':'automation.sleep','source':{},'related_entities':['fan.guest']}}
  self.m.refresh=AsyncMock()
 def test_automation_questions_retain_context_but_unrelated_work_does_not(self):
  self.assertTrue(snapshot_relevant('Which automations exist?',self.m));self.assertFalse(snapshot_relevant('Write an automation script tutorial',self.m))
 def test_service_groups_expose_actual_parameter_names(self):
  self.assertEqual(service_fields({'fields':{'temperature_range':{'fields':{'target_temp_low':{},'target_temp_high':{}}},'hvac_mode':{}}}),['target_temp_low','target_temp_high','hvac_mode'])
 async def test_compact_climate_contract_has_real_limits_and_only_supported_actions(self):
  caps=device_controls(self.m,list(self.m.entities.values()),await self.m.service_definitions());ac=caps[0]
  self.assertEqual(ac['entity_id'],'climate.unit');self.assertEqual(ac['settings']['min_temp'],18)
  self.assertEqual(ac['settings']['fan_mode'],'high');self.assertIn('set_temperature',ac['actions']);self.assertIn('set_fan_mode',ac['actions']);self.assertNotIn('set_preset_mode',ac['actions']);self.assertNotIn('NEVER_INCLUDE',json.dumps(caps))
 async def test_binary_light_does_not_advertise_brightness(self):
  caps=device_controls(self.m,[self.m.entities['light.binary']],await self.m.service_definitions())
  self.assertNotIn('brightness_pct',caps[0]['actions']['turn_on']['fields'])
 async def test_service_catalog_is_cached_without_extra_model_calls(self):
  await self.m.service_definitions();await self.m.service_definitions();self.assertEqual(self.ha.catalog_reads,1)
  self.m.services_loaded_at=0;await self.m.service_definitions();self.assertEqual(self.ha.catalog_reads,2)
 def test_automation_inventory_includes_off_and_global_but_respects_room(self):
  self.assertEqual({e['entity_id'] for e in automation_catalog(self.m)}, {'automation.comfort','automation.global_rule'})
  self.assertEqual(len(automation_catalog(self.m,all_areas=True)),3)
  self.assertEqual(automation_catalog(self.m,'Guest Sleep')[0]['entity_id'],'automation.sleep')
  self.assertFalse(next(e for e in automation_catalog(self.m) if e['entity_id']=='automation.comfort')['enabled'])
 async def test_program_discovery_paginates_all_rooms_and_excludes_device_dump(self):
  with patch.object(home_tools,'_model',self.m):
   r=json.loads(await home_tools.execute('home_query',{'operation':'discover','view':'programs','scope':'all','limit':1}))
  self.assertEqual(r['total'],3);self.assertTrue(r['more']);self.assertNotIn('devices',r)
 async def test_automation_enable_changes_flag_without_triggering_actions(self):
  r=await home_tools.action(self.m,{'domain':'automation','service':'turn_on','entity_ids':['automation.comfort']})
  self.assertEqual(self.ha.calls,[('automation','turn_on',{'entity_id':['automation.comfort']})]);self.assertEqual(r['status'],'observed_expected_state')
 async def test_automation_disable_preserves_current_runs_unless_requested(self):
  await home_tools.action(self.m,{'domain':'automation','service':'turn_off','entity_ids':['automation.sleep']})
  self.assertEqual(self.ha.calls[-1],('automation','turn_off',{'entity_id':['automation.sleep'],'stop_actions':False}))
 async def test_explicit_stop_actions_true_is_supported(self):
  await home_tools.action(self.m,{'domain':'automation','service':'turn_off','entity_ids':['automation.sleep'],'data':{'stop_actions':True}})
  self.assertTrue(self.ha.calls[-1][2]['stop_actions'])
 async def test_enabling_never_uses_trigger_and_unknown_service_is_blocked(self):
  with self.assertRaises(ValueError):await home_tools.action(self.m,{'domain':'automation','service':'trigger','entity_ids':['automation.comfort']})
  self.assertEqual(self.ha.calls,[])
 async def test_read_only_diagnostic_blocks_automation_mutation(self):
  o=TurnOutput('test',AsyncMock());o.read_only=True;token=current_output.set(o)
  try:
   r=await home_tools.action(self.m,{'domain':'automation','service':'turn_on','entity_ids':['automation.comfort']})
   self.assertEqual(r['status'],'blocked_read_only');self.assertEqual(self.ha.calls,[])
  finally:await o.close();current_output.reset(token)
 async def test_service_domain_can_be_inferred_from_explicit_entity_id(self):
  with patch.object(home_tools,'_model',self.m):r=json.loads(await home_tools.execute('home_query',{'operation':'services','query':'climate.unit'}))
  self.assertEqual(r['domain'],'climate')

class ReasonClauseTests(unittest.TestCase):
 def test_off_command_with_explanatory_reason_remains_fast(self):
  for s in ("Turn off the AC, I don't need it anymore.","Turn the fan off because I do not need it now."):
   self.assertEqual(parse(s).operations[0].operation,'OFF')
 def test_negation_conditions_and_conflicting_on_commands_still_fall_back(self):
  for s in ("Don't turn off the AC, I don't need it anymore.","Turn on the AC, I don't need it anymore.","Turn off the AC if it is cold, I don't need it anymore.","Turn off the AC, I don't need it anymore and turn on the fan."):
   with self.assertRaises(Miss,msg=s):parse(s)
