"""Named program settings remain distinct from downstream physical devices."""
import asyncio,json,unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch
from home_controls import relevant_controls
import home_tools
from home_model import HomeModel
from home_presentation import devices
from output import TurnOutput,current_output

class ControlsTests(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.m=HomeModel(NS(),{'primary_area':'bedroom','area_names':{'bedroom':['our room']}})
  self.m.areas={'bedroom':{'name':'Bedroom'},'guest':{'name':'Guest Room'}};self.m.devices={}
  def ent(eid,name,area='bedroom',platform=None,attrs=None,state='on'):
   return {'entity_id':eid,'name':name,'domain':eid.split('.')[0],'aliases':[],'area_id':area,'platform':platform,'state':state,'available':True,'attributes':attrs or {}}
  self.m.entities={e['entity_id']:e for e in (
   ent('climate.unit','Bedroom AC',attrs={'temperature':24}),
   ent('input_number.goal','Bedroom Target Temperature',state='26.5',attrs={'min':20,'max':32,'step':.5,'unit_of_measurement':'°C'}),
   ent('input_boolean.enabled','Bedroom Auto Climate'),
   ent('input_boolean.session','Session'),
   ent('climate.display','Bedroom Auto Target',platform='generic_thermostat'),
   ent('automation.comfort','Bedroom Auto Climate'),
   ent('automation.guest','Guest Auto Climate',area='guest'),
  )}
  src={'description':'Maintain the room at the selected target.',
       'triggers':[{'trigger':'state','entity_id':'input_number.goal'}],
       'conditions':[{'condition':'state','entity_id':'input_boolean.enabled','state':'on'}],
       'actions':[{'variables':{'target':"{{ states('input_number.goal') | float }}"}},
                  {'action':'input_boolean.turn_on','target':{'entity_id':'input_boolean.session'}},
                  {'action':'climate.set_temperature','target':{'entity_id':'climate.unit'},'data':{'temperature':'{{ target - 2 }}'}}]}
  self.m.programs={'automation.comfort':{'entity_id':'automation.comfort','name':'Bedroom Auto Climate','domain':'automation','source':src,'enabled':'on','related_entities':['input_number.goal','input_boolean.enabled','input_boolean.session','climate.unit']},
                   'automation.guest':{'entity_id':'automation.guest','name':'Guest Auto Climate','source':src,'related_entities':['input_number.goal']}}
 def test_named_automation_reveals_numeric_setting_and_enable_flag(self):
  data=relevant_controls(self.m,'Set auto climate temperature to 26')
  self.assertEqual(len(data['programs']),1);p=data['programs'][0]
  self.assertEqual({e['entity_id'] for e in p['settings']},{'input_number.goal','input_boolean.enabled'})
  numeric=next(e for e in p['settings'] if e['domain']=='input_number')
  self.assertEqual(numeric['state'],'26.5');self.assertEqual(numeric['attributes']['step'],.5)
  self.assertTrue(any(r['path']=='actions.0.variables.target' for r in numeric['references']))
  self.assertEqual([e['entity_id'] for e in p['state_helpers']],['input_boolean.session'])
 def test_setting_name_finds_owning_program_without_program_name(self):
  for query in ("What's my room target temperature?",'bedroom target temperature'):
   programs=relevant_controls(self.m,query)['programs']
   self.assertEqual([p['entity_id'] for p in programs],['automation.comfort'])
   self.assertIn('input_number.goal',[e['entity_id'] for e in programs[0]['settings']])
 def test_setting_match_requires_source_reference_and_respects_area(self):
  self.assertEqual(relevant_controls(self.m,'guest target temperature')['programs'][0]['entity_id'],'automation.guest')
  self.m.programs['automation.comfort']['source']={}
  self.assertEqual(relevant_controls(self.m,'my room target temperature'),{})
 def test_unrelated_or_empty_inventory_does_not_dump_helpers(self):
  for q in ('','all devices in our room','turn AC off','What is the temperature?'):
   self.assertEqual(relevant_controls(self.m,q),{})
  self.assertNotIn('Bedroom Auto Target',json.dumps(devices(self.m,list(self.m.entities.values()))))
 def test_other_room_remains_scoped_unless_explicitly_named(self):
  self.assertEqual([p['entity_id'] for p in relevant_controls(self.m,'auto climate')['programs']],['automation.comfort'])
  self.assertEqual([p['entity_id'] for p in relevant_controls(self.m,'guest auto climate')['programs']],['automation.guest'])
 def test_names_and_entity_ids_are_not_hardcoded(self):
  p=self.m.programs.pop('automation.comfort');e=self.m.entities.pop('automation.comfort')
  p.update(entity_id='automation.sleep',name='Quiet Sleep Routine');e.update(entity_id='automation.sleep',name='Quiet Sleep Routine')
  self.m.programs={'automation.sleep':p};self.m.entities['automation.sleep']=e
  self.assertEqual(relevant_controls(self.m,'Set quiet sleep routine target')['programs'][0]['settings'][0]['entity_id'],'input_number.goal')
 def test_aliases_work_but_disabled_programs_are_not_selected(self):
  self.m.entities['automation.comfort']['aliases']=['Night Comfort']
  self.assertTrue(relevant_controls(self.m,'Change night comfort target'))
  self.m.entities['automation.comfort']['disabled_by']='user'
  self.assertEqual(relevant_controls(self.m,'night comfort'),{})
 async def test_default_discovery_includes_named_controls_without_raw_entity_dump(self):
  self.m.refresh=AsyncMock();self.m.refresh_current_states=AsyncMock()
  with patch.object(home_tools,'_model',self.m):
   data=json.loads(await home_tools.execute('home_query',{'operation':'discover','query':'auto climate thermostat'}))
  self.assertIn('program_controls',data);self.assertNotIn('entities',data)
  self.assertIn('input_number.goal',json.dumps(data['program_controls']))
  self.assertNotIn('climate.unit',json.dumps(data['program_controls']['programs'][0]['settings']))
 async def test_explicit_program_inspection_is_not_swamped_by_related_sources(self):
  self.m.refresh=AsyncMock()
  with patch.object(home_tools,'_model',self.m):
   data=json.loads(await home_tools.execute('home_query',{'operation':'inspect','query':'automation.comfort'}))
  self.assertEqual([p['entity_id'] for p in data['programs']],['automation.comfort'])
 async def test_diagnostic_action_is_blocked_before_any_device_or_setting_change(self):
  o=TurnOutput('test',AsyncMock());o.read_only=True;token=current_output.set(o)
  try:
   with patch.object(home_tools,'_model',self.m):
    self.m.refresh=AsyncMock()
    r=json.loads(await home_tools.execute('home_action',{'domain':'input_number','service':'set_value','entity_ids':['input_number.goal'],'data':{'value':26}}))
   self.assertEqual(r['status'],'blocked_read_only')
  finally:await o.close();current_output.reset(token)
