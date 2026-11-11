import asyncio,copy,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
try:from jinja2 import Environment
except ImportError:
 import sys
 sys.path.append('/usr/lib/python3/dist-packages')
 from jinja2 import Environment
from config import Config
from state_notifications.service import Service,normalize,build,decode,LEGACY,PREFIX,Conflict

DOORS=['binary_sensor.fridge_refrigerator_door_status','binary_sensor.fridge_freezer_door_status']
def legacy():return {'id':LEGACY,'alias':'Fridge - Telegram on any door opening','triggers':[{'trigger':'state','entity_id':e,'from':'off','to':'on'} for e in DOORS],'conditions':[],'actions':[{'action':'script.sunday_announce','data':{'channel':'telegram','message':'original fridge wording'}}],'mode':'queued','max':100}
class FakeHA:
 def __init__(self):self.actions_enabled=True;self.configs={LEGACY:legacy()};self.enabled={LEGACY:True};self.calls=[]
 async def get_states(self):return [{'entity_id':'automation.'+rid,'state':'on' if self.enabled[rid] else 'off','attributes':{'id':rid}} for rid in self.configs]
 async def request(self,method,path,data=None):
  self.calls.append((method,path,copy.deepcopy(data)))
  if path.startswith('/api/config/automation/config/'):
   rid=path.rsplit('/',1)[-1]
   if method=='GET':return copy.deepcopy(self.configs[rid])
   if method=='POST':self.configs[rid]=copy.deepcopy(data);self.enabled[rid]=bool(data.get('initial_state',True));return {'result':'ok'}
   if method=='DELETE':del self.configs[rid];del self.enabled[rid];return {'result':'ok'}
  if path.startswith('/api/services/automation/'):
   rid=data['entity_id'].split('.',1)[1];self.enabled[rid]=path.endswith('/turn_on');return []
  raise AssertionError('Unexpected or appliance/network action: '+method+' '+path)
class Rules(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.ha=FakeHA();ids=DOORS+['light.room','sensor.room_temperature','climate.room','script.sunday_announce'];self.home=NS(ha=self.ha,entities={e:{'entity_id':e,'domain':e.split('.')[0],'name':e,'area_id':'room','state':'off','attributes':{'current_temperature':22,'access_token':'secret'}} for e in ids},areas={'room':{'name':'Room'}},refresh=AsyncMock());self.service=Service(self.home,Config(telegram_enabled=True),self.tmp.name)
 async def asyncTearDown(self):self.tmp.cleanup()
 def rule(self,**kw):return normalize({'name':'Light notifications','entity_ids':['light.room'],'condition':'equals','value':'on',**kw},self.home)
 async def manage(self,**body):return await self.service.manage({'revision':(await self.service.snapshot(False))['revision'],**body})
 async def test_import_existing_fridge_without_writes(self):
  s=await self.service.snapshot();self.assertEqual(len(s['rules']),1);r=s['rules'][0]['rule'];self.assertEqual(r['entity_ids'],DOORS);self.assertTrue(r['enabled']);self.assertEqual(r['from_value'],'off');self.assertTrue(all(c[0]=='GET' for c in self.ha.calls))
 async def test_create_disable_update_remove_and_credential_free_backup(self):
  result=await self.manage(action='save',rule=self.rule());self.assertEqual(len(result['rules']),2);r=next(r for r in result['rules'] if r['rule']['id']!=LEGACY);rid=r['rule']['id'];self.assertTrue(rid.startswith(PREFIX))
  await self.manage(action='toggle',id=rid,enabled=False);self.assertFalse(self.ha.enabled[rid]);await self.manage(action='save',rule={'id':rid,'cooldown_seconds':10});saved=decode(self.ha.configs[rid]);self.assertEqual(saved['entity_ids'],['light.room']);self.assertFalse(saved['enabled']);self.assertEqual(saved['cooldown_seconds'],10)
  await self.manage(action='remove',id=rid);self.assertEqual(set(self.ha.configs),{LEGACY});self.assertTrue(list(Path(self.tmp.name).glob('notification_backups/*.json')))
 async def test_fridge_toggle_preserves_original_actions_no_duplicate(self):
  before=legacy();await self.manage(action='toggle',id=LEGACY,enabled=False);self.assertFalse(self.ha.enabled[LEGACY]);self.assertEqual(self.ha.configs[LEGACY]['actions'],before['actions']);self.assertEqual(self.ha.configs[LEGACY]['triggers'],before['triggers']);await self.manage(action='toggle',id=LEGACY,enabled=True);self.assertEqual(len(self.ha.configs),1)
 async def test_stale_revision_rejected_before_any_write(self):
  with self.assertRaises(Conflict):await self.service.manage({'revision':'old','action':'toggle','id':LEGACY,'enabled':False})
  self.assertTrue(self.ha.enabled[LEGACY]);self.assertTrue(all(c[0]=='GET' for c in self.ha.calls))
 async def test_cannot_change_unowned_automation(self):
  with self.assertRaises(ValueError):await self.manage(action='remove',id='house_lighting')
 async def test_readonly_runtime_cannot_write(self):
  self.ha.actions_enabled=False
  with self.assertRaises(ValueError):await self.manage(action='save',rule=self.rule())
  self.assertTrue(all(c[0]=='GET' for c in self.ha.calls))
 def test_numeric_crossing_and_hold(self):
  c=build(self.rule(entity_ids=['sensor.room_temperature'],condition='above',value='30',for_seconds=60));t=c['triggers'][0];self.assertEqual(t['trigger'],'numeric_state');self.assertEqual(t['above'],30);self.assertEqual(t['for'],{'seconds':60})
 def test_attribute_boolean_preserves_type(self):
  c=build(self.rule(entity_ids=['climate.room'],attribute='some_flag',value='true'));self.assertIs(c['triggers'][0]['to'],True)
 def test_any_change_ignores_attribute_only_refresh_and_startup(self):
  c=build(self.rule(condition='change'));self.assertIsNone(c['triggers'][0]['to']);guard=c['conditions'][0]['value_template'];self.assertIn('trigger.from_state is not none',guard);self.assertIn('trigger.from_state.state != trigger.to_state.state',guard);self.assertIn("['unknown','unavailable']",guard)
 def test_unavailable_can_be_an_explicit_condition(self):
  guard=build(self.rule(value='unavailable'))['conditions'][0]['value_template'];self.assertNotIn("trigger.to_state.state not in",guard)
 def test_cooldown_is_per_rule_and_not_repeated_messages(self):
  c=build(self.rule(cooldown_seconds=30));self.assertEqual(c['mode'],'single');self.assertEqual(c['actions'][-1],{'delay':{'seconds':30}});self.assertEqual(build(self.rule())['mode'],'queued')
 def test_custom_text_is_literal_not_template_code(self):
  c=build(self.rule(message='{{ 7*7 }} {device}: {new}'));template=c['actions'][0]['data']['message'];state=NS(name='Lamp',state='on',attributes={});trigger=NS(to_state=state,from_state=NS(state='off',attributes={}),entity_id='light.room');text=Environment().from_string(template).render(trigger=trigger,now=lambda:NS(strftime=lambda _:'10:00 AM'));self.assertEqual(text,'{{ 7*7 }} Lamp: on')
 def test_no_program_or_unknown_entity_feedback_loops(self):
  for entity in ['script.sunday_announce','automation.fridge','light.missing']:
   with self.assertRaises(ValueError):self.rule(entity_ids=[entity])
 def test_bad_threshold_and_duration_rejected(self):
  for patch in [{'condition':'above','value':'nan'},{'for_seconds':-1},{'cooldown_seconds':True},{'enabled':'false'},{'message':'x'*1001},{'attribute':'access_token'}]:
   with self.assertRaises(ValueError):self.rule(**patch)
 async def test_inventory_never_exposes_secret_attributes(self):
  s=await self.service.snapshot();self.assertNotIn('secret',json.dumps(s));self.assertTrue(all('access_token' not in e['attributes'] for e in s['entities']))
 async def test_tool_round_trip_and_list_only_readonly_turn(self):
  from state_notifications import tools as nt
  nt.configure(self.service);result=json.loads(await nt.execute({'operation':'list'}));self.assertEqual(len(result['rules']),1)
  result=json.loads(await nt.execute({'operation':'create','rule':{'entity_ids':['light.room'],'value':'off','name':'Light off'}}));self.assertEqual(result['status'],'saved');self.assertEqual(len(result['rules']),2)
 async def test_http_auth_and_conflict(self):
  from main import create_app
  from aiohttp.test_utils import TestClient,TestServer
  rt=NS(config=Config(daemon_token='test'),state_notifications=self.service,start=AsyncMock(),close=AsyncMock());client=TestClient(TestServer(create_app(rt)));await client.start_server()
  try:
   self.assertEqual((await client.get('/v1/notifications')).status,401);self.assertEqual((await client.post('/v1/notifications/manage',json={})).status,401)
   h={'Authorization':'Bearer test'};r=await client.get('/v1/notifications',headers=h);self.assertEqual(r.status,200);r=await client.post('/v1/notifications/manage',headers=h,json={'revision':'old','action':'remove','id':LEGACY});self.assertEqual(r.status,409);self.assertTrue(self.ha.enabled[LEGACY])
  finally:await client.close()
 async def test_tool_registry_respects_readonly_turn(self):
  import tools
  from output import current_output
  from state_notifications import tools as nt
  nt.configure(self.service);token=current_output.set(NS(read_only=True))
  try:
   result=await tools.execute('manage_device_notifications',{'operation':'disable','id':LEGACY},'disable fridge notifications');self.assertIn('Read-only',result);self.assertTrue(self.ha.enabled[LEGACY]);result=json.loads(await tools.execute('manage_device_notifications',{'operation':'list'},'show notifications'));self.assertEqual(len(result['rules']),1)
  finally:current_output.reset(token)
 def test_notification_settings_cannot_be_physical_fast_commands(self):
  from fast_mode import parse,Miss
  for text in ['turn off fridge notifications','turn fridge alerts off','switch off fan notifications','fridge notifications off','turn off fan in proactivity','memory off']:
   with self.assertRaises(Miss,msg=text):parse(text)
  self.assertEqual(parse('turn the fan off').operations[0].operation,'OFF')
