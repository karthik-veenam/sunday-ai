import asyncio,copy,json,tempfile,unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from unittest.mock import AsyncMock,patch
from types import SimpleNamespace
from urllib.parse import urlparse,parse_qs,unquote
from history_time import WindowParser,HistoryError
import history_math as math
from historical import HistoricalEvidenceProvider,HistoricalPlan
from config import Config
from presence import PresenceProvider
import test_fast_mode as fixtures

UTC=timezone.utc
class TimeTests(unittest.TestCase):
 def setUp(self):self.p=WindowParser();self.now=datetime(2026,10,19,4,30,tzinfo=UTC)
 def test_rolling_windows(self):
  for text,seconds in [('last 24 hours',86400),('last 24h',86400),('last hour',3600),('past 7 days',604800)]:self.assertEqual(self.p.parse(text,self.now).seconds,seconds)
 def test_calendar_days(self):
  yesterday=self.p.parse('yesterday',self.now);self.assertEqual(yesterday.start.hour,0);self.assertEqual(yesterday.seconds,86400);self.assertEqual(self.p.parse('today',self.now).seconds,10*3600)
 def test_night(self):
  w=self.p.parse('last night',self.now);self.assertEqual(w.start.hour,21);self.assertEqual(w.end.hour,7);self.assertEqual(w.seconds,36000)
 def test_ongoing_night_clipped(self):
  w=self.p.parse('last night',datetime(2026,10,19,0,tzinfo=UTC));self.assertEqual(w.end.hour,5);self.assertEqual(w.end.minute,30)
 def test_weeks(self):
  w=self.p.parse('last week',self.now);self.assertEqual(w.start.weekday(),0);self.assertEqual(w.seconds,7*86400);self.assertEqual(self.p.parse('this week',self.now).seconds,10*3600)
 def test_clock_ranges(self):
  w=self.p.parse('between 10 PM and 6 AM',self.now);self.assertEqual(w.seconds,8*3600);self.assertEqual(w.end.hour,6)
  self.assertEqual(self.p.parse('since 10 PM',self.now).seconds,12*3600)
 def test_explicit_day_and_weekdays(self):
  self.assertEqual(self.p.parse('Oct 18',self.now).start.day,18)
  w=self.p.parse('from Monday to Wednesday',self.now);self.assertEqual(w.start.weekday(),0);self.assertEqual(w.end.weekday(),3);self.assertEqual(w.seconds,3*86400)
 def test_morning(self):self.assertEqual(self.p.parse('this morning',self.now).seconds,4*3600)
 def test_ambiguous_and_huge(self):
  for text in ('recently','since 10','between 10 and 6','last 99999 days'):
   with self.subTest(text=text),self.assertRaises(HistoryError):self.p.parse(text,self.now)
 def test_dst_calendar_25h(self):
  p=WindowParser('America/New_York');w=p.parse('2025-11-02',datetime(2025,11,4,tzinfo=UTC));self.assertEqual(w.seconds,25*3600)
 def test_dst_naive_ambiguous_or_nonexistent(self):
  p=WindowParser('America/New_York')
  for start,end in [('2025-11-02T01:30:00','2025-11-02T03:00:00'),('2025-03-09T02:30:00','2025-03-09T04:00:00')]:
   with self.assertRaises(HistoryError):p.parse({'start':start,'end':end},datetime(2025,11,4,tzinfo=UTC))
 def test_dst_explicit_offset(self):
  p=WindowParser('America/New_York');w=p.parse({'start':'2025-11-02T01:30:00-04:00','end':'2025-11-02T01:30:00-05:00'},datetime(2025,11,4,tzinfo=UTC));self.assertEqual(w.seconds,3600)

class MathTests(unittest.TestCase):
 def samples(self,values):return [{'timestamp':i*3600,'time':datetime.fromtimestamp(i*3600,UTC).isoformat(),'value':v,'unit':'°C','context':{}} for i,v in enumerate(values)]
 def test_numeric_summary(self):
  r=math.numeric(self.samples([20,21,22]),0,7200,'°C');self.assertEqual(r['mean'],21);self.assertEqual(r['median'],21);self.assertEqual(r['maximum']['value'],22);self.assertEqual(r['absolute_change'],2);self.assertNotIn('percentage_change',r);self.assertEqual(r['trend'],'RISING')
 def test_conservative_trends(self):
  for values,expected in [([22,21,20],'FALLING'),([20,20.01,20.02],'ROUGHLY_STABLE'),([20,30,18,29,21],'MIXED'),([20],'INSUFFICIENT_DATA')]:
   with self.subTest(values=values):self.assertEqual(math.numeric(self.samples(values),0,max(1,len(values)-1)*3600,'°C')['trend'],expected)
 def test_invalid_numeric(self):
  r=math.numeric(self.samples(['unknown','n/a',float('nan')]),0,7200,'°C');self.assertEqual(r['status'],'NO_DATA')
 def test_resampling_shape(self):
  r=math.buckets(self.samples(list(range(1000))),0,999*3600,48);self.assertEqual(len(r),48);self.assertEqual(min(p['minimum'] for p in r),0);self.assertEqual(max(p['maximum'] for p in r),999)
 def test_boundary_on_and_final_on(self):
  r=math.durations(self.samples(['on','off','on']),1800,3*3600,'fan');self.assertEqual(r['seconds'],5400);self.assertEqual(r['coverage']['unknown_seconds'],0)
 def test_unknown_not_off(self):
  r=math.durations(self.samples(['on','unavailable','off']),0,3*3600,'fan');self.assertEqual(r['seconds'],3600);self.assertEqual(r['coverage']['unknown_seconds'],3600)
 def test_initial_unknown(self):
  r=math.durations(self.samples(['off','on'])[1:],0,7200,'fan');self.assertEqual(r['coverage']['unknown_seconds'],3600)
 def test_duplicates_not_transitions(self):self.assertEqual(len(math.transitions(self.samples(['off','off','on','on','off']),0,5*3600,to_state='on')),1)
 def test_climate_mode_not_compressor(self):
  r=math.durations(self.samples(['cool','dry','off']),0,3*3600,'climate');self.assertEqual(r['seconds'],7200);self.assertIn('not compressor',r['semantics'])
 def test_automation_on_is_enabled(self):
  with self.assertRaises(HistoryError):math.durations(self.samples(['on','off']),0,7200,'automation')
 def test_counter_reset(self):
  r=math.numeric(self.samples([98,100,2,3]),0,3*3600,'kWh','total_increasing');self.assertEqual(r['counter_delta'],5);self.assertEqual(r['counter_resets_observed'],1)
 def test_total_decrement_not_guessed_reset(self):self.assertIsNone(math.numeric(self.samples([10,5]),0,3600,'kWh','total')['counter_delta'])
 def test_normalization_duplicate_and_bad_timestamp(self):
  rows=[{'state':'1','last_updated':'2026-01-01T00:00:00Z'},{'state':'1','last_updated':'2026-01-01T00:00:00Z'},{'state':'2','last_updated':'invalid'}]
  points,notes=math.normalize(rows,0,2e9);self.assertEqual(len(points),1);self.assertEqual(notes['malformed_rows'],1);self.assertEqual(notes['duplicates_removed'],1)

class HA:
 def __init__(self,home):self.home=home;self.calls=[];self.rows={};self.trace_rows=[];self.trace={};self.fail=False
 async def get_state(self,eid):return self.home.entities[eid]
 async def request(self,method,path):
  self.calls.append(path)
  if self.fail:raise RuntimeError('source down')
  if '/api/history/period/' in path:
   q=parse_qs(urlparse(path).query);eid=q['filter_entity_id'][0];start=math.timestamp(unquote(path.split('/api/history/period/')[1].split('?')[0]));end=math.timestamp(q['end_time'][0]);rows=copy.deepcopy(self.rows.get(eid,[]));prior=[r for r in rows if math.timestamp(r['last_updated'])<=start];body=[r for r in rows if start<math.timestamp(r['last_updated'])<end]
   initial=prior[-1:] if prior else []
   if initial:initial[0]['last_updated']=datetime.fromtimestamp(start,UTC).isoformat()
   return [initial+body] if initial or body else []
  return {'entity_id':path.rsplit('/',1)[1],'state':'0','attributes':{'friendly_name':'Home' if path.endswith('zone.home') else 'Work'}}
 async def ws_command(self,op,**kw):
  self.calls.append(op)
  if op=='trace/list':return self.trace_rows
  if op=='trace/get':return self.trace
  if op=='recorder/get_statistics_metadata':return []
  return {}
class Home(fixtures.Model):
 def __init__(self):
  super().__init__();self.programs={};self.household.update(primary_person='Karthik',people={'karthik':{'ha_person':'person.user','presence_zones':{'home':'zone.home','office':'zone.work'}}});self.ha=HA(self)
 def area_ids(self,target):return ['primary']
 def related(self,ids,limit=8):return [p for p in self.programs.values() if set(ids)&set(p.get('related_entities',[]))][:limit]
 def resolve(self,target):
  rows=[e for e in self.entities.values() if target.lower() in [e['name'].lower(),*map(str.lower,e.get('aliases',[]))]]
  if len(rows)!=1:raise ValueError('ambiguous')
  return rows[0]

class ProviderTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.temp=tempfile.TemporaryDirectory();self.home=Home();self.config=Config();self.presence=PresenceProvider(self.home.ha,self.home.household);self.p=HistoricalEvidenceProvider(self.home,self.presence,None,self.config,Path(self.temp.name)/'history.db')
  self.end=datetime.now(UTC)-timedelta(seconds=5);self.start=self.end-timedelta(hours=6);self.window={'start':self.start.isoformat(),'end':self.end.isoformat()}
  e=fixtures.entity('t','Room Temperature','sensor');e['attributes']={'device_class':'temperature','unit_of_measurement':'°C'};self.home.entities[e['entity_id']]=e
  self.home.ha.rows[e['entity_id']]=[{'entity_id':e['entity_id'],'last_updated':(self.start+timedelta(hours=i)).isoformat(),'state':str(25+i),'attributes':{'unit_of_measurement':'°C'}} for i in range(6)]
  auto=fixtures.entity('program','Night Button','automation');auto['attributes']={'last_triggered':self.start.isoformat()};self.home.entities[auto['entity_id']]=auto;self.home.programs[auto['entity_id']]={'domain':'automation','entity_id':auto['entity_id'],'id':'stable-id','name':'Night Button','related_entities':[]}
 async def asyncTearDown(self):self.p.close();self.temp.cleanup()
 async def test_generic_resolution_and_stats(self):
  r=await self.p.query('statistics','room temperature',self.window);self.assertEqual(r['status'],'OK');self.assertEqual(r['series_summary']['mean'],27.5);self.assertEqual(r['series_summary']['maximum']['value'],30);self.assertEqual(r['model_calls'],0)
 async def test_dynamic_rename(self):
  self.home.entities['sensor.t']['name']='New Sensor';self.assertEqual(self.p.resolve('New Sensor')['entity_id'],'sensor.t')
 async def test_no_guess_sensor_qualifier(self):
  r=await self.p.query('statistics','AC temperature',self.window);self.assertEqual(r['status'],'TARGET_NOT_FOUND')
 async def test_ambiguity(self):
  e=fixtures.entity('t2','Other Temperature','sensor');e['attributes']={'device_class':'temperature'};self.home.entities[e['entity_id']]=e
  r=await self.p.query('statistics','room temperature',self.window);self.assertEqual(r['status'],'AMBIGUOUS_TARGET')
 async def test_no_data_not_zero(self):
  self.home.ha.rows={};r=await self.p.query('statistics','room temperature',self.window);self.assertEqual(r['status'],'NO_DATA');self.assertNotIn('series_summary',r)
 async def test_unavailable_source_not_no_data(self):
  self.home.ha.fail=True;r=await self.p.query('statistics','room temperature',self.window);self.assertEqual(r['status'],'HISTORY_UNAVAILABLE')
 async def test_partial_initial_coverage(self):
  self.home.ha.rows['sensor.t']=self.home.ha.rows['sensor.t'][1:];r=await self.p.query('statistics','room temperature',self.window);self.assertEqual(r['status'],'PARTIAL_COVERAGE');self.assertEqual(r['coverage']['unknown_seconds'],3600)
 async def test_unit_mismatch_refused(self):
  self.home.ha.rows['sensor.t'][-1]['attributes']['unit_of_measurement']='°F';r=await self.p.query('statistics','room temperature',self.window);self.assertEqual(r['status'],'UNSUPPORTED_METRIC')
 async def test_duration_boundaries(self):
  self.home.ha.rows['fan.f']=[{'state':v,'last_updated':(self.start+timedelta(hours=i)).isoformat(),'attributes':{}} for i,v in enumerate(['on','on','off','unavailable','off','on'])]
  r=await self.p.query('duration','fan',self.window);self.assertEqual(r['duration']['seconds'],10800);self.assertEqual(r['status'],'PARTIAL_COVERAGE')
 async def test_climate_transition_semantics(self):
  self.home.ha.rows['climate.t']=[{'state':v,'last_updated':(self.start+timedelta(hours=i)).isoformat(),'attributes':{}} for i,v in enumerate(['off','cool','dry','off','cool'])]
  r=await self.p.query('count_transitions','AC',self.window,{'to_state':'on'});self.assertEqual(r['transition_count'],2)
 async def test_huge_window_no_raw_query(self):
  r=await self.p.query('duration','fan','last 100 days');self.assertEqual(r['status'],'UNSUPPORTED_METRIC');self.assertFalse(any('history/period' in c for c in self.home.ha.calls))
 async def test_cache(self):
  a=await self.p.query('statistics','room temperature',self.window);before=len(self.home.ha.calls);b=await self.p.query('statistics','room temperature',self.window);self.assertEqual(a,b);self.assertEqual(len(self.home.ha.calls),before);self.assertEqual(self.p.metrics()['history_cache_hits'],1)
 async def test_timeout(self):
  self.p.timeout_seconds=.01
  async def hang(*a):await asyncio.sleep(2)
  self.home.ha.request=hang;r=await self.p.query('history','fan',self.window);self.assertEqual(r['status'],'TIMEOUT')
 async def test_run_semantics_blocked_trace(self):
  self.home.ha.trace_rows=[{'run_id':'r','timestamp':{'start':self.end.isoformat()},'script_execution':'failed_conditions','state':'stopped'}]
  r=await self.p.query('automation_last_run','Night Button',self.window);self.assertEqual(r['last_action_run_requested_at'],self.start.isoformat());self.assertEqual(r['latest_retained_attempt']['status'],'failed_conditions');self.assertIn('not every trigger',r['semantics'])
 async def test_trace_sanitization(self):
  self.home.ha.trace={'run_id':'r','timestamp':{},'context':{'id':'ctx'},'script_execution':'finished','trace':{'action/0':[{'timestamp':'now','changed_variables':{'latitude':12,'password':'private'},'result':{'params':{'domain':'climate','service':'turn_off','service_data':{'entity_id':'climate.t','password':'private'}}}}]}}
  r=await self.p.query('automation_trace','Night Button',self.window,{'run_id':'r'});text=json.dumps(r);self.assertNotIn('private',text);self.assertNotIn('latitude',text);self.assertIn('turn_off',text)
 async def test_no_trace_not_no_action(self):
  self.home.ha.trace_rows=[];r=await self.p.query('automation_last_run','Night Button',self.window);self.assertIsNone(r['latest_retained_attempt']);self.assertIsNotNone(r['last_action_run_requested_at'])
 async def test_presence_history_coarse_and_unknown(self):
  self.home.ha.rows['person.user']=[{'state':v,'last_updated':(self.start+timedelta(hours=i)).isoformat(),'attributes':{'latitude':12,'longitude':34}} for i,v in enumerate(['not_home','home','Work','unavailable','Shop','home'])]
  r=await self.p.query('presence_history','Karthik',self.window);self.assertEqual(r['status'],'PARTIAL_COVERAGE');self.assertNotIn('latitude',json.dumps(r));self.assertIn('OFFICE',json.dumps(r));self.assertEqual(r['coverage']['unknown_seconds'],7200)
 async def test_compare_deterministic(self):
  r=await self.p.query('compare','room temperature',self.window,{'other_window':self.window});self.assertEqual(r['difference_mean'],0);self.assertTrue(r['window_durations_equal'])
 async def test_compare_missing_not_zero(self):
  other={'start':(self.start-timedelta(days=2)).isoformat(),'end':(self.end-timedelta(days=2)).isoformat()};r=await self.p.query('compare','room temperature',self.window,{'other_window':other});self.assertEqual(r['status'],'NO_DATA');self.assertNotIn('difference_mean',r)
 async def test_strict_projection_budget(self):
  huge={'status':'OK','series':['large'*1000]*1000};self.assertLessEqual(len(json.dumps(self.p.projection(huge))),14000)
 async def test_planner_examples(self):
  examples={'When was the Night Button last triggered?':'automation_last_run','How many times did the AC automation run yesterday?':'automation_runs','What was the RoKa temperature trend over the last 24 hours?':'statistics','What was the highest bedroom temperature this week?':'statistics','How long was the AC on yesterday?':'duration','How many times did the fan turn on this week?':'count_transitions','When did I get home yesterday?':'presence_history','Was the room hotter last night than the night before?':'compare','Why did the AC keep turning off last night?':'evidence'}
  for text,op in examples.items():
   with self.subTest(text=text):plan=self.p.plan_text(text);self.assertIsNotNone(plan);self.assertEqual(plan.operation,op)
 async def test_no_unrelated_planning(self):
  for text in ('What is the capital of Japan?','Write an email','Moon on','make the room cozy'):self.assertIsNone(self.p.plan_text(text))

 async def test_logbook_run_count_not_enabled_states(self):
  original=self.home.ha.request
  async def request(method,path):
   if '/api/logbook/' in path:return [{'entity_id':'automation.program','domain':'automation','message':'triggered by state','context_id':'a','when':self.start.isoformat()},{'entity_id':'automation.program','domain':'automation','message':'turned on','context_id':'b','when':self.start.isoformat()}]
   return await original(method,path)
  self.home.ha.request=request;r=await self.p.query('automation_runs','Night Button',self.window);self.assertEqual(r['action_run_count'],1);self.assertEqual(r['status'],'OK')
 async def test_statistics_adapter_and_extrema_timestamps(self):
  old=self.home.ha.ws_command;now=self.end.timestamp();start=now-3*86400
  async def ws(op,**kw):
   if op=='recorder/get_statistics_metadata':return [{'has_mean':True,'display_unit_of_measurement':'°C'}]
   if op=='recorder/statistics_during_period':return {'sensor.t':[{'start':(start+i*3600)*1000,'end':(start+(i+1)*3600)*1000,'mean':20+i/100,'min':19+i/100,'max':21+i/100} for i in range(72)]}
   return await old(op,**kw)
  self.home.ha.ws_command=ws;w={'start':datetime.fromtimestamp(start,UTC).isoformat(),'end':self.end.isoformat()};r=await self.p.query('statistics','room temperature',w)
  self.assertEqual(r['source_refs'][0]['source'],'ha_statistics');self.assertIsNone(r['series_summary']['maximum']['at']);self.assertLessEqual(len(r['series']),48)
 async def test_trace_causal_link_requires_context_and_action(self):
  trace={'context':{'id':'ctx'},'started_at':self.start.isoformat(),'source_ref':{'entity_id':'automation.program','run_id':'r'},'steps':[{'action':{'domain':'climate','service':'turn_off','entity_ids':['climate.t']}}]}
  transition={'time':self.start.isoformat(),'new':'off','context':{'id':'ctx'}}
  self.assertEqual(self.p.trace_link(trace,transition,'climate.t')['attribution'],'CONFIRMED')
  transition['context']={'id':'other'};self.assertEqual(self.p.trace_link(trace,transition,'climate.t')['attribution'],'POSSIBLE')
 async def test_reverse_context_not_causal(self):
  trace={'context':{'id':'child','parent_id':'parent'},'started_at':self.start.isoformat(),'source_ref':{},'steps':[{'action':{'entity_ids':['climate.t']}}]};transition={'time':self.start.isoformat(),'context':{'id':'parent'}}
  self.assertEqual(self.p.trace_link(trace,transition,'climate.t')['attribution'],'POSSIBLE')
 async def test_presence_expiry_never_becomes_away(self):
  start=self.start.timestamp();points=[{'timestamp':start+1,'time':self.start.isoformat(),'value':'HOME','unit':None,'context':{}}];r=self.p.reliable_presence(points,self.p.parser.parse(self.window));self.assertEqual(r[-1]['value'],'UNKNOWN')

class HistoryRuntimeTests(unittest.IsolatedAsyncioTestCase):
 asyncSetUp=ProviderTests.asyncSetUp
 asyncTearDown=ProviderTests.asyncTearDown
 async def build(self,root,factory):
  import runtime as module
  from runtime import Runtime
  from presence import PresenceSnapshot
  (root/'state').mkdir();(root/'household.json').write_text(json.dumps(self.home.household))
  patcher=patch.object(module,'ROOT',root);patcher.start()
  r=Runtime(Config(historical_evidence_enabled=True,fast_mode_enabled=True,fast_mode_execute=True,fast_mode_stage='all'),agent_factory=factory)
  r.home=self.home;r.history.home=self.home;r.history.ha=self.home.ha;self.home.history=r.history;self.home.context=AsyncMock(return_value='{}');r.presence.get_presence=AsyncMock(return_value=PresenceSnapshot('Karthik','HOME','home_assistant','now'));return r,patcher
 async def test_shared_fast_history_zero_model_and_replay(self):
  def forbidden():raise AssertionError('Historical fast path constructed a model')
  with tempfile.TemporaryDirectory() as d:
   r,p=await self.build(Path(d),forbidden);events=[]
   async def emit(e):events.append(e)
   try:
    for source in ('telegram','ha'):
     req={'request_id':source,'conversation_id':source,'source':source,'text':"What's the highest room temperature last hour?"}
     await r.turn(req,emit);self.assertEqual(events[-1]['model_calls'],0);self.assertIn('30',events[-1]['text'])
     before=len(self.home.ha.calls);await r.turn(req,emit);self.assertTrue(events[-1]['replayed']);self.assertEqual(before,len(self.home.ha.calls))
   finally:await r.close();p.stop()
 async def test_normal_projection_and_unrelated_absence(self):
  from output import current_output,record_model_call
  seen=[]
  class Brain:
   async def respond_as_text(self,text):seen.append(current_output.get().home_context);record_model_call();return 'Summary.'
   async def process(self,text,out):seen.append(out.home_context);record_model_call();await out.speak('Summary.');return False
  with tempfile.TemporaryDirectory() as d:
   r,p=await self.build(Path(d),Brain)
   async def emit(e):pass
   try:
    for source in ('telegram','ha'):
     await r.turn({'request_id':source,'conversation_id':source,'source':source,'text':'What was the room temperature trend last hour?'},emit);self.assertIn('Historical factual evidence',seen[-1]);self.assertIn('series_summary',seen[-1])
    before=r.history.metrics()['history_queries_total'];await r.turn({'request_id':'unrelated','text':'Write an email to my manager','source':'telegram'},emit);self.assertNotIn('Historical factual evidence',seen[-1]);self.assertEqual(r.history.metrics()['history_queries_total'],before)
   finally:await r.close();p.stop()
 async def test_trace_failed_action_not_confirmed(self):
  trace={'context':{'id':'a'},'started_at':self.start.isoformat(),'source_ref':{},'steps':[{'error_type':'service failed','action':{'service':'turn_off','entity_ids':['climate.t']}}]}
  transition={'context':{'id':'a'},'time':self.start.isoformat(),'new':'off'};self.assertEqual(self.p.trace_link(trace,transition,'climate.t')['attribution'],'STRONG')
 async def test_wrong_action_direction_not_confirmed(self):
  trace={'context':{'id':'a'},'started_at':self.start.isoformat(),'source_ref':{},'steps':[{'action':{'service':'turn_on','entity_ids':['climate.t']}}]}
  transition={'context':{'id':'a'},'time':self.start.isoformat(),'new':'off'};self.assertEqual(self.p.trace_link(trace,transition,'climate.t')['attribution'],'STRONG')
 async def test_operational_missing_does_not_erase_other_evidence(self):
  self.home.ha.rows['climate.t']=[{'state':v,'last_updated':(self.start+timedelta(hours=i)).isoformat(),'attributes':{}} for i,v in enumerate(['cool','off'])]
  r=await self.p.query('evidence','AC',self.window);self.assertEqual(r['status'],'PARTIAL_COVERAGE');self.assertEqual(r['transitions']['transition_count'],1)
 async def test_historical_tool_read_only_and_bounded(self):
  import history_tools
  value=json.loads(await history_tools.execute(self.p,{'operation':'statistics','target':'room temperature','window':self.window}));self.assertEqual(value['model_calls'],0);self.assertLessEqual(len(json.dumps(value)),14000)
