"""V2 acceptance: real stores/services with deterministic provider fixtures, no effects."""
import asyncio,json,tempfile,unittest
from pathlib import Path
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from config import Config
from memory_v1.store import Store as MemoryStore,key
from memory_v1.consolidation import Consolidator
from memory_v1.behavior import learn_patterns
from memory_v1.dashboard import snapshot
from memory_v2.beliefs import migrate,promotion,retrieval_weight
from proactivity_v1.store import Store as ProactiveStore
from proactivity_v2.incidents import Incidents
from proactivity_v2.attention import applicable,assess

class BeliefTests(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'memory.db';self.s=MemoryStore(self.path);self.s.v2_enabled=True
 def tearDown(self):self.s.close();self.tmp.cleanup()
 def submit(self,text='Prefers room at 28 C',concept='bedroom.comfort',epistemic='confirmed',episode='episode1',source='USER_EXPLICIT',typ='PREFERENCE',impact='Use this to choose a comfortable room target.'):
  ref=self.s.evidence('user_statement','telegram',{'quote':text,'episode_key':episode,'request_id':key([text,episode])})
  c={'type':typ,'subject':concept,'concept_key':concept,'statement':text,'confidence':.95,'epistemic_state':epistemic,'future_impact':impact,'episode_key':episode,'people':['Karthik'],'entities':[]}
  cid=self.s.submit(c,source,[ref],[episode,text]);return cid
 def current(self,cid):return json.loads(self.s.db.execute('select data from inbox where id=?',(cid,)).fetchone()[0])
 async def consolidate(self,proposal):
  c=Consolidator(self.s,Config(memory_v2_enabled=True));c.propose=AsyncMock(return_value=(proposal,{'model':'fixture'}));return await c.nightly()
 async def test_useful_explicit_preference_promotes_with_provenance(self):
  cid=self.submit();r=await self.consolidate({'operation':'ADD','reason':'Useful stable preference','assessment':{'reusable':True,'grounded':True,'future_impact':'Choose the room target Karthik finds comfortable.'}})
  self.assertEqual(r[0]['operation_applied'],'ADD');m=self.s.inspect('memories')[0]['data'];self.assertEqual(m['concept_key'],'bedroom.comfort');self.assertTrue(self.s.chain(m['memory_id'])['evidence'])
 async def test_f_explicit_irrelevant_does_not_consolidate(self):
  self.submit(text='A purple alien in a film looked funny',concept='film.opinion',typ='FACT',impact='')
  await self.consolidate({'operation':'ADD','reason':'Explicit opinion','assessment':{'reusable':False,'grounded':True,'future_impact':''}})
  self.assertEqual(self.s.inspect('memories'),[])
 async def test_a_later_confirmation_replaces_uncertainty_in_same_candidate(self):
  cid=self.submit('Visitor may have operated the room devices','visitor.room_activity','uncertain',typ='FACT')
  await self.consolidate({'operation':'ADD','reason':'Potential context','assessment':{'reusable':True,'grounded':True,'future_impact':'Explain activity in this room during visits.'}})
  self.assertEqual(self.s.db.execute('select state from inbox').fetchone()[0],'AWAITING_EVIDENCE')
  same=self.submit('Visitor did operate the room devices','visitor.room_activity','confirmed',typ='FACT')
  self.assertEqual(cid,same);c=self.current(cid);self.assertEqual(c['epistemic_state'],'confirmed');self.assertNotIn('may have',c['statement']);self.assertEqual(len(c['evidence_refs']),2)
  self.assertTrue(any(a['operation']=='CANDIDATE_RECONCILED' for a in self.s.inspect('audit')))
 async def test_single_incident_not_generalized_into_inferred_routine(self):
  cid=self.submit('A visitor usually operates devices','visitor.routine',source='SUNDAY_INFERENCE',typ='FACT')
  await self.consolidate({'operation':'ADD','reason':'Potential routine','assessment':{'reusable':True,'grounded':True,'future_impact':'Explain later device use during the visit window.'}})
  self.assertEqual(self.s.inspect('memories'),[])
  self.assertEqual(self.s.db.execute('select state from inbox').fetchone()[0],'AWAITING_EVIDENCE')
 async def test_three_independent_episodes_update_one_concept(self):
  ids=[self.submit('Visitor commonly uses bedroom devices','visitor.routine',episode='independent'+str(i),source='SUNDAY_INFERENCE',typ='FACT') for i in range(3)]
  self.assertEqual(len(set(ids)),1)
  await self.consolidate({'operation':'ADD','reason':'Repeated supported useful context','assessment':{'reusable':True,'grounded':True,'future_impact':'Explain expected bedroom activity during visits.'}})
  self.assertEqual(len(self.s.inspect('memories')),1)
 def test_migration_is_idempotent_preserves_evidence_versions_and_tombstones(self):
  self.s.v2_enabled=False;cid=self.submit();result=self.s.reconcile(cid,{'operation':'ADD','reason':'V1 derived record'},[]);before=self.s.db.execute('select count(*) from evidence').fetchone()[0]
  self.s.db.execute("insert into memory_controls values('tombstone','old','remove','now')");self.s.db.commit();receipt=migrate(self.s);self.assertEqual(receipt,migrate(self.s));self.s.v2_enabled=True
  self.assertEqual(self.s.db.execute('select count(*) from evidence').fetchone()[0],before);self.assertTrue(self.s.inspect('versions'));self.assertEqual(self.s.db.execute('select count(*) from memory_controls').fetchone()[0],1)
  service=NS(store=self.s,config=Config(memory_write_enabled=True),flags={},sync_status=lambda:{})
  self.assertEqual(snapshot(service,{'view':'inbox'})['total'],0);self.assertEqual(snapshot(service,{'view':'consolidated'})['total'],0)
 def test_e_weak_statistics_remain_evidence(self):
  for i in range(3):
   self.s.evidence('device_session','ha_recorder',{'kind':'device_session','subject':'light.display','start':f'2026-10-{19+i:02d}T05:00:00+00:00','end':f'2026-10-{19+i:02d}T05:10:00+00:00','duration_minutes':10,'weekday':'Monday','start_local_minutes':630,'end_local_minutes':640,'domain':'light','quality':'complete_observed_boundaries'})
  learn_patterns(self.s)
  self.assertFalse(self.s.inspect('inbox'));self.assertFalse(self.s.inspect('memories'));self.assertTrue(any(e['kind']=='pattern_statistics' for e in self.s.inspect('evidence')))
 def test_episode_retrieval_priority_decays(self):
  old={'type':'EPISODE','updated_at':(datetime.now(timezone.utc)-timedelta(days=14)).isoformat()};self.assertLess(retrieval_weight(old),.26)
 def test_direct_write_cannot_bypass_utility_gate(self):
  cid=self.submit()
  with self.assertRaisesRegex(ValueError,'usefulness'):self.s.reconcile(cid,{'operation':'ADD','reason':'Confidence alone'},[])

class SituationTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.now=datetime(2026,10,26,6,15,tzinfo=timezone.utc).timestamp();self.s=ProactiveStore(Path(self.tmp.name)/'p.db',lambda:self.now);self.inc=Incidents(self.s)
 def tearDown(self):self.s.close();self.tmp.cleanup()
 def event(self,i,entity='light.a',new='on'):
  return {'event_id':str(i),'occurred_at':datetime.fromtimestamp(self.now,timezone.utc).isoformat(),'subject':{'id':entity,'area':'bedroom'},'change':{'property':'state','new':new,'old':'off' if new=='on' else 'on'},'correlation':{}}
 def context(self,memory=None):
  return {'now':self.now,'fresh':True,'healthy':True,'world':[{'subject':{'id':'person:Karthik'},'value':'OFFICE','availability':'available','stale':False},{'subject':{'id':'light.a','area':'bedroom'},'value':'on','availability':'available','last_event_id':'1'}],'memory':memory or [],'history':[]}
 def raw(self,**kw):return {'outcome':'TELL','reason':'Multiple unexplained room devices active while away.','unexpectedness':.95,'severity':.3,'relevance':1,'confidence':.95,'explanation_fit':0,'explanation_refs':[],'context_refs':['1'],**kw}
 def belief(self):return {'memory_id':'memory:visitor','status':'ACTIVE','epistemic_state':'confirmed','applicability':{'local_hours':[10,13],'entities':['light.a'],'presence':['OFFICE']}}
 def test_a_unexplained_away_activity_crosses_attention_once(self):
  iid=self.inc.add(self.event(1));self.inc.add(self.event(2,'light.b'));d=self.s.get('incidents',iid)['data'];result=assess(self.raw(),self.context(),d,0);self.assertEqual(result['outcome'],'TELL');self.inc.assess(iid,result)
  wid=self.s.wake('HA_NOTEWORTHY','SUNDAY_SELF',['light.a','light.b'],['attention:ref'],iid);self.assertEqual(self.s.get('wakes',wid)['data']['incident_id'],iid)
  self.s.change('incidents',iid,state='NOTIFIED',data=self.s.get('incidents',iid)['data']|{'notification_state':'DELIVERED'})
  self.now+=120;self.assertEqual(self.inc.add(self.event(3,'light.b')),iid);self.assertEqual(assess(self.raw(),self.context(),self.s.get('incidents',iid)['data'],0)['outcome'],'SILENT')
 def test_b_applicable_learned_context_reduces_false_anomaly(self):
  m=self.belief();ctx=self.context([m]);self.assertTrue(applicable(m,ctx,'Asia/Kolkata'))
  r=assess(self.raw(explanation_fit=.95,explanation_refs=[m['memory_id']]),ctx,{'event_refs':['1']},0);self.assertNotIn(r['outcome'],{'TELL','URGENT'});self.assertEqual(r['matched_memory_refs'],[m['memory_id']])
 def test_c_same_activity_3am_not_explained_by_daytime_belief(self):
  self.now=datetime(2026,10,26,21,30,tzinfo=timezone.utc).timestamp();ctx=self.context();self.assertFalse(applicable(self.belief(),ctx,'Asia/Kolkata'));self.assertEqual(assess(self.raw(),ctx,{'event_refs':['1']},0)['outcome'],'TELL')
 def test_d_projector_transitions_one_persistent_situation(self):
  ids=[]
  for i,(minutes,device,value) in enumerate([(0,'switch.projector','on'),(13,'switch.projector','off'),(26,'switch.projector','on'),(2,'media_player.projector','on'),(2,'light.nearby','on')]):
   self.now+=minutes*60;ids.append(self.inc.add(self.event(i,device,value)))
  self.assertEqual(len(set(ids)),1);row=self.s.get('incidents',ids[0]);self.assertEqual(len(row['data']['transitions']),5)
  result=assess(self.raw(outcome='ACCUMULATE',unexpectedness=.1,severity=.1),self.context(),row['data'],0);self.assertEqual(result['outcome'],'ACCUMULATE')
 def test_restart_preserves_grouping_and_notification_state(self):
  iid=self.inc.add(self.event(1));self.s.close();self.s=ProactiveStore(Path(self.tmp.name)/'p.db',lambda:self.now);self.inc=Incidents(self.s);self.assertEqual(self.inc.add(self.event(2,'light.b')),iid)
 def test_no_unsupported_memory_explanation_or_unknown_absence(self):
  with self.assertRaises(ValueError):assess(self.raw(explanation_refs=['invented']),self.context(),{},0)
  ctx=self.context();ctx['fresh']=False;self.assertNotIn(assess(self.raw(),ctx,{},0)['outcome'],{'TELL','URGENT'})
 def test_feedback_does_not_overfit_single_ignore(self):
  iid=self.inc.add(self.event(1));self.inc.feedback(iid,'ignored','signal1');self.inc.feedback(iid,'ignored','signal1');d=self.s.get('incidents',iid)['data'];self.assertEqual(len(d['feedback']),1);self.assertEqual(d['notification_state'],'NONE')
 def test_expired_incident_does_not_suppress_new_day(self):
  old=self.inc.add(self.event(1));self.now+=3600;new=self.inc.add(self.event(2));self.assertNotEqual(old,new);self.assertEqual(self.s.get('incidents',old)['state'],'RESOLVED')

class ServiceIntegrationTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.clock=NS(t=datetime(2026,10,26,6,tzinfo=timezone.utc).timestamp())
  from proactivity_v1.service import Service
  cfg=Config(proactivity_v2_enabled=True,ha_attention_enabled=True,proactive_cognition_enabled=True)
  self.raw={'outcome':'TELL','reason':'Unexpected activity in an empty room.','unexpectedness':.95,'severity':.4,'relevance':1,'confidence':.95,'explanation_fit':0,'explanation_refs':[],'context_refs':['e1']}
  async def call(kind,packet,ref):return json.dumps(self.raw),'test-usage'
  self.models=NS(call=AsyncMock(side_effect=call));self.svc=Service(Path(self.tmp.name)/'p.db',cfg,clock=lambda:self.clock.t,models=self.models)
  self.context={'now':self.clock.t,'fresh':True,'healthy':True,'quiet':False,'world':[],'history':[],'threads':[],'memory':[],'input_policy_revision':None}
  self.svc.context=AsyncMock(side_effect=lambda **kw:dict(self.context,now=self.clock.t))
 async def asyncTearDown(self):await self.svc.close();self.tmp.cleanup()
 def event(self,id='e1',subject='light.a'):
  return {'event_id':id,'event_type':'DEVICE_STATE_CHANGED','occurred_at':datetime.fromtimestamp(self.clock.t,timezone.utc).isoformat(),'subject':{'id':subject,'area':'room'},'change':{'property':'state','old':'off','new':'on'},'provenance':{},'correlation':{}}
 async def test_real_episode_path_groups_wakes_over_multiple_windows(self):
  e=self.svc.episodes.add(self.event());self.clock.t+=10;await self.svc.close_episode(e)
  self.assertEqual(len(self.svc.store.rows('wakes')),1)
  self.clock.t+=60;e2=self.svc.episodes.add(self.event('e2','light.b'));self.clock.t+=10;await self.svc.close_episode(e2)
  self.assertEqual(len(self.svc.store.rows('wakes')),1)
  self.assertTrue(self.models.call.call_args.args[1]['ONGOING SITUATION']['transitions'])
 async def test_notified_incident_silently_updates_without_new_model_call(self):
  e=self.svc.episodes.add(self.event());self.clock.t+=10;await self.svc.close_episode(e)
  wake=self.svc.store.rows('wakes')[0];iid=wake['data']['incident_id'];r=self.svc.store.get('incidents',iid)
  self.svc.store.change('incidents',iid,state='NOTIFIED',data=r['data']|{'notification_state':'DELIVERED'})
  calls=self.models.call.call_count;self.clock.t+=60;e2=self.svc.episodes.add(self.event('e2','light.b'));self.clock.t+=10;result=await self.svc.close_episode(e2)
  self.assertEqual(result['classification'],'SILENT');self.assertEqual(self.models.call.call_count,calls)
 async def test_learned_memory_reads_remain_behind_existing_source_gate(self):
  from unittest.mock import Mock
  self.svc.memory=NS(store=NS(retrieve=Mock(return_value=[])))
  self.assertFalse(self.svc.source_allowed('learned_memory'))
  # Unmock original context, using no World provider, so retrieval should still not run.
  from proactivity_v1.service import Service
  await Service.context(self.svc,subject='light.a');self.svc.memory.store.retrieve.assert_not_called()
 async def test_policy_revision_change_cancels_episode(self):
  e=self.svc.episodes.add(self.event());self.clock.t+=10
  self.svc.policy_revision=lambda:'changed'
  result=await self.svc.close_episode(e);self.assertEqual(result['state'],'DONE')

class SafetyAndMigrationTests(unittest.TestCase):
 def test_new_safety_condition_is_separate_from_already_notified_routine(self):
  with tempfile.TemporaryDirectory() as t:
   s=ProactiveStore(Path(t)/'p.db',lambda:1000);inc=Incidents(s)
   e={'event_id':'1','subject':{'id':'light.a','area':'room'},'change':{'old':'off','new':'on'}};iid=inc.add(e);r=s.get('incidents',iid);s.change('incidents',iid,state='NOTIFIED',data=r['data']|{'notification_state':'DELIVERED'})
   hazard={'event_id':'2','subject':{'id':'binary_sensor.hazard','area':'room','device_class':'smoke'},'change':{'old':'off','new':'on'}}
   self.assertNotEqual(inc.add(hazard),iid);s.close()
 def test_migration_preserves_explicit_commitment_and_audit(self):
  from proactivity_v2.migration import migrate
  with tempfile.TemporaryDirectory() as t:
   s=ProactiveStore(Path(t)/'p.db');a=s.put('wakes',{'authority':'SUNDAY_SELF'},dedupe='a');u=s.put('temporal',{'authority':'USER_EXPLICIT','kind':'COMMITMENT_DUE'},dedupe='u');ref=s.audit('test',{'evidence':'kept'})
   receipt=migrate(s);self.assertEqual(receipt,migrate(s));self.assertEqual(s.get('wakes',a)['state'],'ARCHIVED_V1');self.assertEqual(s.get('temporal',u)['state'],'PENDING');self.assertIsNotNone(s.get('audit',ref));s.close()

class AdditionalBeliefTests(unittest.IsolatedAsyncioTestCase):
 setUp=BeliefTests.setUp
 tearDown=BeliefTests.tearDown
 submit=BeliefTests.submit
 current=BeliefTests.current
 consolidate=BeliefTests.consolidate
 async def test_changed_visit_window_updates_same_belief_without_stale_card(self):
  cid=self.submit('Visitor usually arrives at 11 AM','visitor.visit_window',typ='FACT')
  good={'reusable':True,'grounded':True,'future_impact':'Explain expected activity during the correct visit window.'}
  await self.consolidate({'operation':'ADD','reason':'Supported routine','assessment':good})
  mid=self.s.inspect('memories')[0]['id']
  self.submit('Visitor now usually arrives at 2 PM','visitor.visit_window',typ='FACT',episode='later')
  await self.consolidate({'operation':'MERGE','target_id':mid,'reason':'Same concept with supported revised timing','assessment':good})
  active=[r['data'] for r in self.s.inspect('memories') if r['status']=='ACTIVE'];self.assertEqual(len(active),1);self.assertEqual(active[0]['memory_id'],mid);self.assertIn('2 PM',active[0]['statement']);self.assertGreater(len(self.s.chain(mid)['versions']),1)
 async def test_awaiting_evidence_does_not_hide_concurrent_new_confirmation(self):
  cid=self.submit('Possibly a routine','visitor.routine','uncertain',typ='FACT');candidate={'id':cid,'data':self.current(cid)}
  c=Consolidator(self.s,Config(memory_v2_enabled=True))
  async def changed(*args):
   self.submit('Confirmed routine','visitor.routine','confirmed',typ='FACT',episode='later')
   return {'operation':'ADD','reason':'Needs support','assessment':{'reusable':True,'grounded':True,'future_impact':'Explain activity during repeat visits.'}},{'model':'fixture'}
  c.propose=changed;result=await c.nightly();self.assertEqual(result[0]['status'],'candidate_changed');self.assertEqual(self.s.db.execute('select state from inbox').fetchone()[0],'NEW')

class ContextReferenceTests(unittest.TestCase):
 def test_recent_intention_can_be_cited_without_inventing_an_execution(self):
  ctx={'now':1,'fresh':True,'healthy':True,'world':[],'history':[],'memory':[],'conversation':[{'context_ref':'conversation:actual','role':'user','content':'Testing the projector.'}]}
  raw={'outcome':'SILENT','reason':'Matches the stated test.','unexpectedness':.2,'severity':.1,'relevance':.5,'confidence':.9,'explanation_fit':.8,'explanation_refs':['conversation:actual'],'context_refs':[]}
  self.assertEqual(assess(raw,ctx,{},0)['outcome'],'SILENT')
