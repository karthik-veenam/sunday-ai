import asyncio,json,tempfile,unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch
from memory_v1.store import Store,key
from memory_v1.capture import capture,TOOL_NAME
from memory_v1.behavior import sessions,statistics_for,learn_patterns,temporal_boundaries
from memory_v1.service import MemoryService
from memory_v1.consolidation import Consolidator
from config import Config
from output import TurnOutput,current_output
from agent import _exec_tool,_without_capture_history,_OpenAIBackend,_to_openai_tools
class Storage(unittest.TestCase):
 def setUp(self):self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'new.db';self.s=Store(self.path);self.ev=self.s.evidence('user_statement','test',{'quote':'I like tea.'},'source1')
 def tearDown(self):self.s.close();self.temp.cleanup()
 def candidate(self,statement='Likes tea.',subject='tea preference',typ='PREFERENCE',source='USER_EXPLICIT',correction=False,refs=None):
  return self.s.submit({'type':typ,'subject':subject,'statement':statement,'confidence':.85,'people':['Karthik'],'tags':['drinks'],'correction':correction},source,refs or [self.ev],[statement,source,correction])
 def add(self,cid):return self.s.reconcile(cid,{'operation':'ADD','reason':'Explicit user evidence.'},[])['affected_memory_ids'][-1]
 def test_fact_and_preference_capture(self):
  for t in ['FACT','PREFERENCE']:
   ids=capture(self.s,{'candidates':[{'type':t,'subject':t,'statement':'Likes tea.','quote':'like tea','explicit':True,'confidence':.9}]},request_id='x'+t,user_text='I like tea.',source='telegram',person='Karthik');self.assertEqual(len(ids),1)
  self.assertEqual(len(self.s.candidates()),2);self.assertEqual(self.s.inspect('memories'),[])
 def test_capture_quote_must_be_real(self):
  with self.assertRaises(ValueError):capture(self.s,{'candidates':[{'type':'FACT','subject':'test','statement':'made up','quote':'unseen','explicit':True,'confidence':.9}]},request_id='x',user_text='hello',source='test')
 def test_idempotency_and_restart(self):
  cid=self.candidate();mid=self.add(cid);self.assertTrue(self.s.reconcile(cid,{'operation':'ADD','reason':'retry'},[])['idempotent']);self.s.close();self.s=Store(self.path);self.assertEqual(self.candidate(),cid);self.assertEqual(len(self.s.inspect('memories')),1);self.assertEqual(self.s.get(mid)['source_class'],'USER_EXPLICIT')
 def test_paraphrase_merge_and_reconfirm_lineage(self):
  mid=self.add(self.candidate());ev=self.s.evidence('user_statement','test',{'quote':'Tea is my favourite.'});cid=self.candidate('Prefers drinking tea.',refs=[ev]);self.s.reconcile(cid,{'operation':'MERGE','target_id':mid,'reason':'Paraphrase of same explicit preference.'},[mid]);m=self.s.get(mid);self.assertEqual(len(self.s.inspect('memories')),1);self.assertEqual(len(m['evidence_refs']),2);self.assertEqual(len(self.s.chain(mid)['versions']),2)
 def test_explicit_correction_supersedes(self):
  mid=self.add(self.candidate());cid=self.candidate('Actually prefers coffee.',correction=True);a=self.s.reconcile(cid,{'operation':'SUPERSEDE','target_id':mid,'reason':'Explicit correction.'},[mid]);new=self.s.get(a['affected_memory_ids'][-1]);self.assertEqual(self.s.get(mid)['status'],'SUPERSEDED');self.assertEqual(new['supersedes'],[mid]);self.assertEqual(self.s.get(mid)['superseded_by'],new['memory_id'])
 def test_behavior_cannot_override_explicit(self):
  mid=self.add(self.candidate());cid=self.candidate('Prefers coffee.','tea preference','FACT','SUNDAY_INFERENCE');a=self.s.reconcile(cid,{'operation':'UPDATE','target_id':mid,'reason':'inferred'},[mid]);self.assertEqual(a['operation_applied'],'CONFLICT');self.assertEqual(self.s.get(mid)['statement'],'Likes tea.');self.assertEqual(self.s.get(mid)['status'],'ACTIVE')
 def test_unresolved_explicit_conflict(self):
  mid=self.add(self.candidate());cid=self.candidate('Dislikes tea.');self.s.reconcile(cid,{'operation':'CONFLICT','target_id':mid,'reason':'Unresolved contradictory evidence.'},[mid]);self.assertEqual(self.s.get(mid)['status'],'CONFLICTED');self.assertEqual(self.s.retrieve('tea'),[])
 def test_malformed_and_stale_target_do_not_mutate(self):
  mid=self.add(self.candidate());cid=self.candidate('Another claim.')
  for p in [{'operation':'REWRITE','reason':'bad'},{'operation':'MERGE','target_id':mid,'reason':'not considered'},{'operation':'ADD','reason':'duplicate subject'}]:
   with self.assertRaises(ValueError):self.s.reconcile(cid,p,[])
  self.assertEqual(len(self.s.inspect('memories')),1);self.assertEqual(self.s.candidates()[0]['state'],'NEW')
 def test_routine_action_not_preference(self):
  cid=self.candidate('Likes automated cooling.',source='HISTORICAL_PATTERN');a=self.s.reconcile(cid,{'operation':'ADD','reason':'Observed device usage.'},[]);self.assertEqual(a['operation_applied'],'IGNORE');self.assertEqual(self.s.inspect('memories'),[])
 def test_retrieval_relevant_bounded_and_provenance(self):
  mid=self.add(self.candidate());self.add(self.candidate('Dog name is Hugo.','dog','FACT'))
  result=self.s.retrieve('tea preference');self.assertEqual([m['memory_id'] for m in result],[mid]);self.assertEqual(result[0]['confidence'],.85);self.assertEqual(result[0]['evidence_refs'],[self.ev]);self.assertEqual(self.s.retrieve('Jupiter orbit'),[])
 def test_schema_has_no_legacy_import(self):self.assertEqual(self.s.inspect('memories'),[]);self.assertEqual(self.s.inspect('inbox'),[])
 def test_evidence_is_not_inbox_and_no_gps(self):
  self.s.evidence('device_session','ha_recorder',{'subject':'fan','duration_minutes':10});self.assertEqual(self.s.inspect('inbox'),[])
  with self.assertRaises(ValueError):self.s.evidence('presence','test',{'latitude':5})
class Behavior(unittest.TestCase):
 def sample(self,values):return [{'timestamp':i*60,'value':v} for i,v in enumerate(values)]
 def test_device_duration_and_gap(self):
  rows=sessions(self.sample(['off','on','on','off']),'switch.any','device_session');self.assertEqual(len(rows),1);self.assertEqual(rows[0]['duration_minutes'],2)
  self.assertEqual(sessions(self.sample(['off','on','unavailable','off']),'fan','device_session'),[])
 def test_office_and_unknown_not_away(self):
  rows=sessions(self.sample(['HOME','OFFICE','OFFICE','AWAY']),'Person','presence_session');self.assertEqual(rows[0]['zone'],'OFFICE');self.assertEqual(rows[0]['duration_minutes'],2)
  self.assertEqual(sessions(self.sample(['HOME','UNKNOWN','AWAY','OFFICE']),'Person','presence_session'),[])
 def test_initial_state_and_open_not_sessions(self):self.assertEqual(sessions(self.sample(['on','on','off']),'fan','device_session'),[]);self.assertEqual(sessions(self.sample(['off','on']),'fan','device_session'),[])
 def evidence(self,n=12,durations=None):
  out=[];base=datetime.now(timezone.utc)-timedelta(days=30)
  for i in range(n):
   a=base+timedelta(days=i);duration=(durations or [40+i%3 for i in range(n)])[i];out.append({'kind':'device_session','subject':'switch.projector','zone':None,'state':'ACTIVE','area':'room','start':a.isoformat(),'end':(a+timedelta(minutes=duration)).isoformat(),'duration_minutes':duration,'weekday':'Tuesday','start_local_minutes':1200+i%3,'end_local_minutes':1240+i%3,'timezone':'Asia/Kolkata','domain':'switch','quality':'complete_observed_boundaries','source_ref':{'source':'ha_recorder'}})
  return out
 def test_weak_small_sample_and_changed_pattern(self):
  self.assertLessEqual(statistics_for(self.evidence(2))['confidence'],.35);self.assertFalse(statistics_for(self.evidence(2))['strong_enough'])
  stable=statistics_for(self.evidence());changed=statistics_for(self.evidence(12,[40]*8+[100]*4));self.assertGreater(stable['confidence'],changed['confidence']);self.assertGreater(changed['unusual_boundary'],stable['unusual_boundary'])
 def test_pattern_update_restart_no_duplicates(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'m.db';s=Store(path)
   for e in self.evidence():s.evidence('device_session','ha_recorder',e)
   learn_patterns(s);m=s.inspect('memories')[0]['data'];self.assertEqual(m['type'],'PATTERN_EXPECTATION');self.assertTrue(temporal_boundaries(m));self.assertTrue(all(not b['notification_authority'] for b in temporal_boundaries(m)))
   learn_patterns(s);s.close();s=Store(path);learn_patterns(s);self.assertEqual(len(s.inspect('memories')),1);self.assertEqual(len(s.inspect('versions')),1)
   extra=self.evidence(13)[-1];s.evidence('device_session','ha_recorder',extra);learn_patterns(s);self.assertEqual(len(s.inspect('memories')),1);self.assertEqual(s.inspect('memories')[0]['data']['structured_value']['sample_count'],13);s.close()
class Integration(unittest.IsolatedAsyncioTestCase):
 async def test_candidate_write_failure_cannot_fail_reply(self):
  o=TurnOutput('x',AsyncMock());o.memory_capture=lambda args:(_ for _ in ()).throw(OSError('disk'))
  token=current_output.set(o)
  try:
   result=await _exec_tool(TOOL_NAME,{'candidates':[]},'hello');self.assertIn('not_staged',result);self.assertEqual(o.tool_count,0)
  finally:current_output.reset(token)
 async def test_same_inference_reply_capture_no_second_model_call(self):
  with tempfile.TemporaryDirectory() as d:
   service=MemoryService(Path(d)/'m.db',Config(memory_write_enabled=True));o=TurnOutput('x',AsyncMock());o.memory_capture=lambda args:service.capture(args,request_id='x',user_text='I like tea.',source='test')
   token=current_output.set(o)
   try:
    backend=_OpenAIBackend('fake-key');args={'candidates':[{'type':'PREFERENCE','subject':'tea','statement':'Likes tea.','quote':'like tea','explicit':True,'confidence':.9}]}
    tc=NS(id='memorytool',function=NS(name=TOOL_NAME,arguments=json.dumps(args)))
    backend._client.chat.completions.create=AsyncMock(return_value=NS(usage=None,choices=[NS(message=NS(content='Tea it is.',tool_calls=[tc]))]))
    reply=await backend.respond_as_text('I like tea.');self.assertEqual(reply,'Tea it is.');self.assertEqual(backend._client.chat.completions.create.await_count,1);self.assertEqual(len(service.store.candidates()),1);self.assertNotIn(TOOL_NAME,json.dumps(backend.history));await backend._client.close()
   finally:current_output.reset(token);service.close()
 async def test_shadow_results_never_enter_live_context(self):
  with tempfile.TemporaryDirectory() as d:
   cfg=Config(memory_write_enabled=True,memory_read_enabled=False);m=MemoryService(Path(d)/'m.db',cfg);ref=m.store.evidence('test','test',{'quote':'tea'});cid=m.store.submit({'type':'FACT','subject':'tea','statement':'Secret learned tea fact.','confidence':.9},'USER_EXPLICIT',[ref],'x');m.store.reconcile(cid,{'operation':'ADD','reason':'test'},[])
   self.assertEqual(m.turn_context('tea','turn'), '');self.assertEqual(len(m.store.inspect('retrievals')[0]['data']['would_retrieve']),1);self.assertFalse(cfg.memory_read_enabled);m.close()
 async def test_empty_nightly_no_llm_call(self):
  with tempfile.TemporaryDirectory() as d:
   s=Store(Path(d)/'m.db');c=Consolidator(s,Config());c.propose=AsyncMock();self.assertEqual(await c.nightly(),[]);c.propose.assert_not_awaited();s.close()
 async def test_disposable_ignored_and_malformed_consolidator_safe(self):
  with tempfile.TemporaryDirectory() as d:
   s=Store(Path(d)/'m.db');ev=s.evidence('user_statement','test',{'quote':'hello'});cid=s.submit({'type':'EPISODE','subject':'greeting','statement':'Said hello.','confidence':.5},'USER_EXPLICIT',[ev],'one');c=Consolidator(s,Config());c.propose=AsyncMock(return_value=({'operation':'IGNORE','reason':'Disposable chat.'},{'model':'mock'}));await c.nightly();self.assertEqual(s.candidates(('IGNORED',))[0]['id'],cid);self.assertEqual(s.inspect('memories'),[])
   cid=s.submit({'type':'FACT','subject':'test','statement':'Untrusted','confidence':.5},'SUNDAY_INFERENCE',[ev],'two');c.propose=AsyncMock(return_value=({'operation':'REWRITE','reason':'bad'},{'model':'mock'}));await c.nightly();self.assertEqual(s.candidates(('ERROR',))[0]['id'],cid);self.assertEqual(s.inspect('memories'),[]);s.close()
 async def test_private_history_does_not_leak_to_future_turn(self):
  h=[{'role':'user','content':'Tea'},{'role':'assistant','content':'Okay.','tool_calls':[{'id':'m','function':{'name':TOOL_NAME,'arguments':'secret inferred memory'}}]},{'role':'tool','tool_call_id':'m','content':'staged'}];clean=_without_capture_history(h);self.assertEqual(clean,[{'role':'user','content':'Tea'},{'role':'assistant','content':'Okay.'}])

class MoreAcceptance(unittest.IsolatedAsyncioTestCase):
 async def test_streaming_capture_does_not_repeat_answer_or_call_provider_again(self):
  o=TurnOutput('v',AsyncMock());o.memory_capture=lambda args:['candidate'];token=current_output.set(o)
  try:
   b=object.__new__(_OpenAIBackend);b.history=[];b._voice_model='fake';b._voice_service_tier='default';b._tools=[];b._tools_telegram=[];b._system_messages=lambda:[];b._safe_history_slice=lambda:[]
   async def stream():
    yield NS(choices=[NS(delta=NS(content='Tea it is.',tool_calls=None))],usage=None,service_tier=None)
    yield NS(choices=[NS(delta=NS(content=None,tool_calls=[NS(index=0,id='m',function=NS(name=TOOL_NAME,arguments='{"candidates":[]}'))]))],usage=None,service_tier=None)
   create=AsyncMock(return_value=stream());b._client=NS(chat=NS(completions=NS(create=create)));text,tools,ended,pairs=await b._stream_turn(o,'I prefer tea.')
   self.assertEqual(text,'Tea it is.');self.assertEqual(tools,[]);self.assertTrue(ended);create.assert_awaited_once();self.assertEqual(o.spoken,['Tea it is.']);self.assertNotIn(TOOL_NAME,json.dumps(b.history))
  finally:await o.close();current_output.reset(token)
 async def test_fast_mode_memory_enabled_no_agent_or_llm(self):
  from runtime import Runtime
  import runtime as runtime_module
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'state').mkdir();(root/'household.json').write_text(json.dumps({'primary_area':'room'}))
   with patch.object(runtime_module,'ROOT',root):
    rt=Runtime(Config(memory_write_enabled=True,llm_provider='openai'),agent_factory=lambda:(_ for _ in ()).throw(AssertionError('Fast Mode called an agent')))
    rt.fast_mode=NS(attempt=AsyncMock(return_value={'handled':True,'reply':'Done.','decision':'EXECUTE'}),save=lambda record:None,db=NS(close=lambda:None));rt.config.fast_mode_execute=True;events=[]
    try:
     await rt.turn({'request_id':'fast','conversation_id':'fast','text':'Moon on'},AsyncMock(side_effect=lambda e:events.append(e)))
     self.assertEqual(events[-1]['model_calls'],0);self.assertEqual(rt.learned_memory.store.inspect('inbox'),[])
    finally:await rt.close()
 async def test_lease_blocks_concurrent_consolidators_and_recovers(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'m.db';a=Store(path);b=Store(path)
   self.assertTrue(a.acquire_lease('consolidation'));self.assertFalse(b.acquire_lease('consolidation'));a.release_lease('consolidation');self.assertTrue(b.acquire_lease('consolidation'));b.release_lease('consolidation');a.close();b.close()
 async def test_session_evidence_restart_is_idempotent(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'m.db';samples=[{'timestamp':t,'value':v} for t,v in [(0,'off'),(60,'on'),(120,'off')]];e=sessions(samples,'switch.generic','device_session')[0]
   for _ in range(2):
    s=Store(path);s.evidence('device_session','ha_recorder',e,'session:'+key([e['subject'],e['start'],e['end']]));self.assertEqual(len(s.inspect('evidence')),1);s.close()
 async def test_measured_response_is_arithmetic_not_causal_inference(self):
  from memory_v1.behavior import measured_response
  samples=[{'timestamp':100,'value':29},{'timestamp':160,'value':28},{'timestamp':220,'value':27}]
  r=measured_response(samples,subject='sensor.generic',trigger_at=100,threshold=28,direction='below',trigger_ref='action:recorded',source_ref={'source':'ha_recorder'},window_end=230);self.assertEqual(r['duration_seconds'],60);self.assertIn('not established',r['causality'])
  self.assertIsNone(measured_response([{'timestamp':101,'value':'unavailable'}]+samples[1:],subject='sensor.generic',trigger_at=100,threshold=28,direction='below',trigger_ref='action:recorded',source_ref={},window_end=230))
 async def test_open_session_survives_restart_and_overlapping_history(self):
  from memory_v1.behavior import ingest_sessions
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'m.db';s=Store(p);a=[{'timestamp':0,'value':'off'},{'timestamp':60,'value':'on'}]
   self.assertEqual(ingest_sessions(s,a,'switch.generic','device_session',window_start=0,window_end=120),[]);s.close();s=Store(p)
   b=a+[{'timestamp':600,'value':'off'}];r=ingest_sessions(s,b,'switch.generic','device_session',window_start=0,window_end=610);self.assertEqual(r[0]['duration_minutes'],9)
   ingest_sessions(s,b,'switch.generic','device_session',window_start=0,window_end=620);self.assertEqual(len(s.inspect('evidence')),1);s.close()
 async def test_tool_only_capture_disabled_on_next_normal_round(self):
  from agent import _active_openai_tools
  o=TurnOutput('x',AsyncMock());o.memory_capture=lambda args:['x'];token=current_output.set(o)
  b=_OpenAIBackend('fake')
  try:
   async def answer(**kw):
    if any(d['function']['name']==TOOL_NAME for d in kw.get('tools',[])):
     return NS(usage=None,choices=[NS(message=NS(content=None,tool_calls=[NS(id='m',function=NS(name=TOOL_NAME,arguments='{"candidates":[]}'))]))])
    return NS(usage=None,choices=[NS(message=NS(content='Okay.',tool_calls=[]))])
   create=AsyncMock(side_effect=answer);b._client.chat.completions.create=create
   self.assertEqual(await b.respond_as_text('I like tea.'),'Okay.');self.assertEqual(create.await_count,2);self.assertTrue(o.memory_capture_done)
   self.assertEqual(_active_openai_tools([]),[])
   o.memory_capture_done=False;self.assertEqual(_active_openai_tools([])[0]['function']['name'],TOOL_NAME)
  finally:await b._client.close();current_output.reset(token)
 async def test_reply_in_private_capture_payload_uses_one_inference(self):
  o=TurnOutput('x',AsyncMock());o.memory_capture=lambda args:[];token=current_output.set(o);b=_OpenAIBackend('fake')
  try:
   tc=NS(id='m',function=NS(name=TOOL_NAME,arguments=json.dumps({'candidates':[],'reply':'Tea it is.'})))
   create=AsyncMock(return_value=NS(usage=None,choices=[NS(message=NS(content=None,tool_calls=[tc]))]));b._client.chat.completions.create=create
   self.assertEqual(await b.respond_as_text('Tea please.'),'Tea it is.');create.assert_awaited_once();self.assertNotIn(TOOL_NAME,json.dumps(b.history))
  finally:await b._client.close();current_output.reset(token)
 async def test_voice_reply_in_private_capture_payload_is_spoken_once(self):
  o=TurnOutput('v',AsyncMock());o.memory_capture=lambda args:[];token=current_output.set(o)
  try:
   b=object.__new__(_OpenAIBackend);b.history=[];b._voice_model='fake';b._voice_service_tier='default';b._tools=[];b._tools_telegram=[];b._system_messages=lambda:[];b._safe_history_slice=lambda:[]
   async def stream():
    yield NS(choices=[NS(delta=NS(content=None,tool_calls=[NS(index=0,id='m',function=NS(name=TOOL_NAME,arguments='{"candidates":[],"reply":"Tea it is."}'))]))],usage=None,service_tier=None)
   create=AsyncMock(return_value=stream());b._client=NS(chat=NS(completions=NS(create=create)));text,tools,ended,_=await b._stream_turn(o,'Tea please.')
   self.assertEqual(text,'Tea it is.');self.assertEqual(tools,[]);self.assertTrue(ended);self.assertEqual(o.spoken,['Tea it is.']);create.assert_awaited_once()
  finally:await o.close();current_output.reset(token)
