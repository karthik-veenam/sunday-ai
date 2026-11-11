"""Deterministic proactivity acceptance. No live API, HA, Telegram, voice or production DB."""
import asyncio,json,tempfile,unittest
from pathlib import Path
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch
from config import Config
from proactivity_v1.store import Store,digest
from proactivity_v1.scheduler import Scheduler
from proactivity_v1.episodes import Episodes
from proactivity_v1.expectations import Expectations
from proactivity_v1.correlation import Execution,Health
from proactivity_v1.watches import Watches
from proactivity_v1.models import attention_parse,Models
from proactivity_v1.policy import validate_output,Gates
from proactivity_v1.service import Service
class Clock:
 def __init__(self):self.t=datetime.fromisoformat('2026-10-19T02:30:00+00:00').timestamp()
 def __call__(self):return self.t
 def advance(self,seconds):self.t+=seconds
class FakeModels:
 def __init__(self,attention='NORMAL',output=None):self.calls=[];self.attention=attention;self.output=output or {'outcomes':[{'operation':'IGNORE','reason':'No useful follow-up.'}],'reason':'Ordinary context.'};self.fail=False
 async def call(self,kind,packet,ref):
  self.calls.append((kind,packet,ref))
  if self.fail:raise TimeoutError('fake')
  return ('decision: '+self.attention+'\nreason: Contextual relationship.' if kind=='attention' else json.dumps(self.output)),'fake-usage'
def event(i='1',subject='fan.generic',old='off',new='on',typ='DEVICE_STATE_CHANGED',request=None):
 return {'event_id':i,'event_type':typ,'occurred_at':'2026-10-19T02:30:00+00:00','observed_at':'2026-10-19T02:30:00+00:00','subject':{'id':subject,'kind':'entity'},'change':{'property':'state','old':old,'new':new},'provenance':{'initiator':{'type':'UNKNOWN'},'attribution':'UNKNOWN','evidence':[],'reason':'Cause unknown'},'correlation':{'request_id':request}}
def proposal(op='TELL_USER',**kw):return {'operation':op,'reason':'Useful contextual consequence.','confidence':.9,'message':'The projector may have been left on.','destination':'telegram','already_known':False,'consequence_of_silence':'The ongoing session may be unintended.','time_sensitive':False,'useful_now':True,'can_resolve_silently':False,'interruption_cost':'low',**kw}
def envelope(authority='SUNDAY_SELF',incident='incident1'):return {'authority':authority,'subject':'fan.generic','evidence_refs':['event1'],'incident_id':incident,'wake_type':'HA_NOTEWORTHY'}
def pattern(kind='arrival',version='v1',status='ACTIVE',confidence=.9):
 group=['presence_session','Resident','OFFICE','Monday','room'] if kind=='arrival' else ['device_session','switch.generic',None,'Monday','room'];bounds={'arrival_after_local_minutes':640} if kind=='arrival' else {'duration_minutes':60}
 return {'memory_id':'memory1','type':'PATTERN_EXPECTATION','status':status,'confidence':confidence,'updated_at':version,'evidence_refs':['historical1'],'structured_value':{'group':group,'statistics':{'strong_enough':True,'sample_count':20,'last_recalculation':'2026-10-19T02:30:00+00:00'},'evidence_window':{'end':'2026-10-18T02:30:00+00:00'},'temporal_boundaries':bounds}}
class Base(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'p.db';self.clock=Clock();self.s=Store(self.path,self.clock);self.sch=Scheduler(self.s)
 async def asyncTearDown(self):self.s.close();self.tmp.cleanup()
 def engine(self,mem=None,allowed=True):
  self.mem={'memory1':mem or pattern()};return Expectations(self.s,self.sch,lambda k:self.mem.get(k),lambda:allowed)
 def service(self,**flags):
  cfg=Config(proactive_cognition_enabled=True,ha_attention_enabled=True,expectation_engine_enabled=True,self_watch_enabled=True,**flags);m=FakeModels();service=Service(Path(self.tmp.name)/'service.db',cfg,clock=self.clock,models=m);service.context=AsyncMock(return_value={'fresh':True,'healthy':True,'quiet':False,'world':[],'history':[],'threads':[],'memory':[]});self.addAsyncCleanup(service.close);return service,m
class EpisodeTests(Base):
 async def test_first_event_fixed_ten_seconds(self):
  e=Episodes(self.s,self.sch);i=e.add(event());self.assertEqual(self.s.get('episodes',i)['due'],self.clock()+10)
 async def test_related_event_joins(self):
  e=Episodes(self.s,self.sch);a=e.add(event());b=e.add(event('2','light.generic'));self.assertEqual(a,b);self.assertEqual(len(self.s.get('episodes',a)['data']['events']),2)
 async def test_later_event_never_resets_deadline(self):
  e=Episodes(self.s,self.sch);i=e.add(event());due=self.s.get('episodes',i)['due'];self.clock.advance(9);e.add(event('2'));self.assertEqual(self.s.get('episodes',i)['due'],due)
 async def test_after_close_new_episode(self):
  e=Episodes(self.s,self.sch);a=e.add(event());self.clock.advance(10);b=e.add(event('2'));self.assertNotEqual(a,b)
 async def test_ordered_before_after(self):
  e=Episodes(self.s,self.sch);i=e.add(event());e.add(event('2',old='on',new='off'));r=self.s.get('episodes',i)['data'];self.assertEqual(r['before']['fan.generic:state'],'off');self.assertEqual(r['after']['fan.generic:state'],'off');self.assertEqual([x['change']['new'] for x in r['events']],['on','off'])
 async def test_noise_excluded(self):self.assertIsNone(Episodes(self.s,self.sch).add(event(typ='RAW_CHANGED')))
 async def test_unchanged_excluded(self):self.assertIsNone(Episodes(self.s,self.sch).add(event(old='on',new='on')))
 async def test_duplicate_event_no_new_window(self):
  e=Episodes(self.s,self.sch);e.add(event());self.assertIsNone(e.add(event()));self.assertEqual(len(self.s.rows('episodes')),1)
 async def test_provenance_kept(self):
  e=event();e['provenance']['initiator']={'type':'HA_AUTOMATION','id':'automation.humidity'};i=Episodes(self.s,self.sch).add(e);self.assertEqual(self.s.get('episodes',i)['data']['events'][0]['provenance']['initiator']['type'],'HA_AUTOMATION')
 async def test_burst_bounds(self):
  e=Episodes(self.s,self.sch)
  for n in range(300):i=e.add(event(str(n),'light.'+str(n)))
  d=self.s.get('episodes',i)['data'];self.assertEqual(len(d['events']),32);self.assertLessEqual(len(d['before']),32);self.assertGreater(d['dropped_events'],0)
 async def test_restart_open_episode_restored(self):
  i=Episodes(self.s,self.sch).add(event());self.s.close();self.s=Store(self.path,self.clock);self.assertEqual(self.s.get('episodes',i)['state'],'OPEN');self.assertEqual(len(self.s.rows('temporal',('PENDING',))),1)
 async def test_world_facts_already_updated_before_consumer(self):
  # Real EventBus publishes only after insertion; earlier factual consumer runs before attention.
  from world import EventBus,NormalizedEvent
  order=[];bus=EventBus(NS(insert_event=lambda e:True));bus.subscribe(lambda e:order.append('world'));bus.subscribe(lambda e:order.append('attention'));bus.publish(None);self.assertEqual(order,['world','attention'])
class AttentionTests(Base):
 async def test_strict_normal(self):self.assertEqual(attention_parse('decision: NORMAL\nreason: Occupied room.')['decision'],'NORMAL')
 async def test_strict_noteworthy(self):self.assertEqual(attention_parse('decision: NOTEWORTHY\nreason: Unexpected activation.')['decision'],'NOTEWORTHY')
 async def test_malformed_fail_closed(self):
  for raw in ['NORMAL','decision: NORMAL\nreason:','```yaml\ndecision: NORMAL\nreason: Fine.\n```','decision: normal\nreason: Fine.','decision: NORMAL\nreason: Fine.\nACT']:
   with self.assertRaises(ValueError):attention_parse(raw)
 async def test_normal_one_attention_zero_cognition(self):
  svc,m=self.service();i=svc.episodes.add(event());self.clock.advance(10);await svc.close_episode(i);self.assertEqual([k for k,_,_ in m.calls],['attention']);self.assertEqual(svc.store.rows('wakes'),[])
 async def test_noteworthy_at_most_one_wake(self):
  svc,m=self.service();m.attention='NOTEWORTHY';i=svc.episodes.add(event());self.clock.advance(10);await svc.close_episode(i);await svc.close_episode(i);self.assertEqual(len(svc.store.rows('wakes')),1);self.assertEqual(len(m.calls),1)
 async def test_timeout_no_effect_or_repair(self):
  svc,m=self.service();m.fail=True;i=svc.episodes.add(event());self.clock.advance(10);await svc.close_episode(i);self.assertEqual(len(m.calls),1);self.assertEqual(svc.store.rows('wakes'),[]);self.assertEqual(svc.store.rows('proposals'),[])
 async def test_attention_cannot_create_watch_or_memory(self):
  svc,m=self.service();m.attention='NOTEWORTHY';i=svc.episodes.add(event());self.clock.advance(10);await svc.close_episode(i);self.assertEqual(svc.store.rows('watches'),[]);self.assertEqual(svc.store.rows('decisions'),[])
 async def test_packet_bounded_and_temporal_frame(self):
  e=Episodes(self.s,self.sch);i=e.add(event());p=e.packet(self.s.get('episodes',i),{'world':[{'huge':'x'*2000} for _ in range(30)]});self.assertLessEqual(len(json.dumps(p)),30000);self.assertEqual(p['TEMPORAL FRAME']['closed']-p['TEMPORAL FRAME']['started'],10)
 async def test_old_episode_does_not_infer_stale_world(self):
  svc,m=self.service();i=svc.episodes.add(event());self.clock.advance(200);await svc.close_episode(i);self.assertEqual(m.calls,[]);self.assertEqual(svc.store.get('episodes',i)['state'],'STALE')
class SchedulerTests(Base):
 async def test_nearest_first(self):
  self.sch.add(self.clock()+20,'SELF_RECHECK','SUNDAY_SELF',{},'late');self.sch.add(self.clock()+10,'SELF_RECHECK','SUNDAY_SELF',{},'early');self.clock.advance(30);order=[];await self.sch.run_due(AsyncMock(side_effect=lambda r:order.append(r['due']) or {}));self.assertEqual(order,sorted(order))
 async def test_earlier_insert_signals(self):
  self.sch.add(self.clock()+100,'SELF_RECHECK','SUNDAY_SELF',{},'one');self.sch.changed.clear();self.sch.add(self.clock()+1,'SELF_RECHECK','SUNDAY_SELF',{},'two');self.assertTrue(self.sch.changed.is_set());self.assertEqual(self.sch.nearest(),self.clock()+1)
 async def test_cancellation_no_fire(self):
  i=self.sch.add(self.clock(),'SELF_RECHECK','SUNDAY_SELF',{},'x');self.sch.cancel(i);h=AsyncMock();await self.sch.run_due(h);h.assert_not_awaited()
 async def test_completed_never_refires(self):
  self.sch.add(self.clock(),'SELF_RECHECK','SUNDAY_SELF',{},'x');h=AsyncMock(return_value={});await self.sch.run_due(h);await self.sch.run_due(h);h.assert_awaited_once()
 async def test_duplicate_schedule_dedupe(self):
  a=self.sch.add(self.clock(),'SELF_RECHECK','SUNDAY_SELF',{},'x');b=self.sch.add(self.clock(),'SELF_RECHECK','SUNDAY_SELF',{},'x');self.assertEqual(a,b)
 async def test_restart_pending_restore(self):
  i=self.sch.add(self.clock()+10,'SELF_RECHECK','SUNDAY_SELF',{},'x');self.s.close();self.s=Store(self.path,self.clock);sch=Scheduler(self.s);self.clock.advance(10);h=AsyncMock(return_value={});await sch.run_due(h);h.assert_awaited_once()
 async def test_crashed_processing_recovers_after_lease(self):
  i=self.sch.add(self.clock(),'SELF_RECHECK','SUNDAY_SELF',{},'x');self.s.claim('temporal',i,seconds=5);self.assertEqual(self.sch.nearest(),self.clock()+5);self.clock.advance(6);h=AsyncMock(return_value={});await self.sch.run_due(h);h.assert_awaited_once()
 async def test_same_time_deterministic_order(self):
  for k in ['b','a','c']:self.sch.add(self.clock(),'SELF_RECHECK','SUNDAY_SELF',{},k)
  ids=[];await self.sch.run_due(AsyncMock(side_effect=lambda r:ids.append(r['id']) or {}));self.assertEqual(ids,sorted(ids))
 async def test_old_self_work_closed_not_replayed(self):
  self.sch.add(self.clock()-90000,'SELF_RECHECK','SUNDAY_SELF',{},'x');h=AsyncMock();await self.sch.run_due(h);h.assert_not_awaited();self.assertEqual(self.s.rows('temporal')[0]['state'],'STALE')
 async def test_overdue_explicit_grace(self):
  self.sch.add(self.clock()-60,'COMMITMENT_DUE','USER_EXPLICIT',{'user_request_id':'u'},'x');h=AsyncMock(return_value={});await self.sch.run_due(h);h.assert_awaited_once()
 async def test_clock_forward_and_backward(self):
  self.sch.add(self.clock()+10,'SELF_RECHECK','SUNDAY_SELF',{},'x');h=AsyncMock(return_value={});self.clock.advance(-100);await self.sch.run_due(h);h.assert_not_awaited();self.clock.advance(111);await self.sch.run_due(h);h.assert_awaited_once()
 async def test_idle_zero_calls(self):
  h=AsyncMock();await self.sch.run_due(h);h.assert_not_awaited();self.assertIsNone(self.sch.nearest())
class ExecutionTests(Base):
 def ex(self,targets=None):
  ex=Execution(self.s,self.sch);receipt={'action_id':'a','request_id':'r','domain':'light','service':'turn_on','targets':targets or ['light.x']};i=ex.intent(receipt);return ex,i
 async def test_service_and_observed_clean(self):
  ex,i=self.ex();ex.result('a');ex.observe('light.x','on',request_id='r',occurred=self.clock());self.assertEqual(ex.due(i)['classification'],'CLEAN_CONFIRMED_HA_STATE');self.assertEqual(self.s.rows('wakes'),[])
  svc,models=self.service();receipt={'action_id':'fast-a','request_id':'r','domain':'light','service':'turn_on','targets':['fan.generic']};svc.execution.intent(receipt);svc.execution.result('fast-a');eid=svc.episodes.add(event(request='r'));self.clock.advance(10);await svc.close_episode(eid);self.assertEqual(models.calls,[])
 async def test_service_success_not_physical_success(self):
  ex,i=self.ex();ex.result('a');self.clock.advance(9);ex.due(i);r=self.s.get('executions',i);self.assertEqual(r['data']['classification'],'UNKNOWN_VERIFICATION');self.assertFalse(r['data']['physical_success_confirmed'])
 async def test_timeout_no_retry(self):
  ex,i=self.ex();ex.result('a',unknown=True);ex.due(i);self.assertTrue(self.s.rows('wakes')[0]['data']['no_retry']);self.assertEqual(len(self.s.rows('executions')),1)
 async def test_delayed_convergence(self):
  ex,i=self.ex();ex.result('a');self.clock.advance(5);ex.observe('light.x','on',request_id='r',occurred=self.clock());self.assertEqual(ex.classify(self.s.get('executions',i)),'CLEAN_CONFIRMED_HA_STATE')
  other=ex.intent({'action_id':'climate-a','request_id':'r2','domain':'climate','service':'set_temperature','targets':['climate.generic'],'intended_values':{'temperature':24}});ex.result('climate-a');ex.observe('climate.generic',24,request_id='r2',occurred=self.clock(),property='temperature');self.assertEqual(ex.classify(self.s.get('executions',other)),'CLEAN_CONFIRMED_HA_STATE')
 async def test_contradiction(self):
  ex,i=self.ex();ex.result('a');ex.observe('light.x','off',request_id='r',occurred=self.clock());self.assertEqual(ex.classify(self.s.get('executions',i)),'CONTRADICTION')
 async def test_partial_group(self):
  ex,i=self.ex(['light.x','light.y']);ex.result('a');ex.observe('light.x','on',request_id='r',occurred=self.clock());ex.observe('light.y','off',request_id='r',occurred=self.clock());self.assertEqual(ex.classify(self.s.get('executions',i)),'PARTIAL_COMPLETION')
 async def test_unavailable_neither_success_nor_failure(self):
  ex,i=self.ex();ex.result('a');ex.observe('light.x','unavailable',request_id='r',available=False);self.assertEqual(ex.classify(self.s.get('executions',i)),'UNKNOWN_VERIFICATION')
 async def test_unrelated_context_not_attributed(self):
  ex,i=self.ex();ex.observe('light.x','on',request_id='different');self.assertEqual(self.s.get('executions',i)['data']['observations'],{})
 async def test_late_unrelated_state_excluded(self):
  ex,i=self.ex();self.clock.advance(20);ex.observe('light.x','on',request_id='r',occurred=self.clock());self.assertEqual(self.s.get('executions',i)['data']['observations'],{})
 async def test_context_link_works(self):
  ex=Execution(self.s,self.sch);i=ex.intent({'action_id':'a','domain':'light','service':'turn_on','targets':['light.x'],'ha_context_id':'ctx'});ex.observe('light.x','on',context_ids=['ctx']);self.assertIn('light.x',self.s.get('executions',i)['data']['observations'])
 async def test_duplicate_receipt_idempotent_and_restart(self):
  ex,i=self.ex();ex.intent(self.s.get('executions',i)['data']['receipt']);self.assertEqual(len(self.s.rows('executions')),1);self.s.close();self.s=Store(self.path,self.clock);self.assertEqual(len(self.s.rows('executions',('PENDING',))),1)
class HealthTests(Base):
 async def test_transient_recovers_zero_wake(self):
  h=Health(self.s,self.sch);h.update('ha',False);self.clock.advance(5);h.update('ha',True);self.clock.advance(200);await self.sch.run_due(AsyncMock(side_effect=h.due));self.assertEqual(self.s.rows('wakes'),[])
 async def test_persistent_one_wake(self):
  h=Health(self.s,self.sch);h.update('ha',False);self.clock.advance(121);await self.sch.run_due(AsyncMock(side_effect=h.due));self.assertEqual(len(self.s.rows('wakes')),1)
 async def test_repeated_failure_no_storm(self):
  h=Health(self.s,self.sch)
  for _ in range(100):h.update('ha',False)
  self.assertEqual(len(self.s.rows('temporal')),1)
 async def test_recovery_new_incident(self):
  h=Health(self.s,self.sch);h.update('ha',False);self.clock.advance(121);await self.sch.run_due(AsyncMock(side_effect=h.due));h.update('ha',True);self.clock.advance(1);h.update('ha',False);self.clock.advance(121);await self.sch.run_due(AsyncMock(side_effect=h.due));self.assertEqual(len(self.s.rows('wakes')),2)
 async def test_health_cannot_directly_act(self):
  h=Health(self.s,self.sch);h.update('ha',False);self.clock.advance(121);await self.sch.run_due(AsyncMock(side_effect=h.due));self.assertEqual(self.s.rows('proposals'),[])
class ExpectationTests(Base):
 def schedule(self,m=None):
  e=self.engine(m);ids=e.reconcile(list(self.mem.values()));return e,ids
 async def test_eligible_arrival_wake(self):
  e,ids=self.schedule();self.assertEqual(len(ids),1);self.assertEqual(self.s.get('temporal',ids[0])['data']['authority'],'SUNDAY_SELF')
 async def test_weak_no_wake(self):
  self.assertEqual(self.engine(pattern(confidence=.2)).reconcile(list(self.mem.values())),[])
  m=pattern();m['structured_value']['statistics']['sample_count']=2;self.assertEqual(self.engine(m).reconcile([m]),[])
  m=pattern();m['structured_value']['statistics']['last_recalculation']='2020-01-01T00:00:00+00:00';self.assertEqual(self.engine(m).reconcile([m]),[])

 async def test_no_boundary_no_wake(self):
  m=pattern();m['structured_value']['temporal_boundaries']={};self.assertEqual(self.engine(m).reconcile([m]),[])
 async def test_inactive_classes_no_wake(self):
  for state in ['SUPERSEDED','CONFLICTED','RETIRED']:
   m=pattern(status=state);self.assertEqual(self.engine(m).reconcile([m]),[])
 async def test_version_change_cancels_old_and_recomputes(self):
  e,ids=self.schedule();m=pattern(version='v2');m['structured_value']['temporal_boundaries']['arrival_after_local_minutes']=700;self.mem['memory1']=m;new=e.reconcile([m]);self.assertEqual(self.s.get('temporal',ids[0])['state'],'CANCELLED');self.assertNotEqual(ids,new);self.assertGreater(self.s.get('temporal',new[0])['due'],self.s.get('temporal',ids[0])['due'])
 async def test_deleted_source_closes(self):
  e,ids=self.schedule();self.mem.clear();r=await e.due(self.s.get('temporal',ids[0]),AsyncMock());self.assertEqual(r['state'],'CANCELLED')
 async def test_satisfied_zero_model_and_wake(self):
  e,ids=self.schedule();r=await e.due(self.s.get('temporal',ids[0]),AsyncMock(return_value={'fresh':True,'healthy':True,'presence':'OFFICE','arrival_occurred':True}));self.assertEqual(r['state'],'SATISFIED');self.assertEqual(self.s.rows('wakes'),[])
 async def test_valid_violation_once(self):
  e,ids=self.schedule();row=self.s.get('temporal',ids[0]);recheck=AsyncMock(return_value={'fresh':True,'healthy':True,'presence':'HOME','complete_history':True,'arrival_occurred':False});await e.due(row,recheck);await e.due(row,recheck);self.assertEqual(len(self.s.rows('wakes')),1)
 async def test_unknown_never_absence(self):
  e,ids=self.schedule();r=await e.due(self.s.get('temporal',ids[0]),AsyncMock(return_value={'fresh':True,'healthy':True,'presence':'UNKNOWN','complete_history':True,'arrival_occurred':False}));self.assertEqual(r['state'],'UNKNOWN');self.assertEqual(self.s.rows('wakes'),[])
 async def test_unavailable_never_violation(self):
  e,ids=self.schedule();r=await e.due(self.s.get('temporal',ids[0]),AsyncMock(return_value={'fresh':True,'healthy':False,'presence':'AWAY','complete_history':True,'arrival_occurred':False}));self.assertEqual(r['state'],'UNKNOWN')
 async def test_stale_evidence_never_violation(self):
  e,ids=self.schedule();r=await e.due(self.s.get('temporal',ids[0]),AsyncMock(return_value={'fresh':False,'healthy':True,'presence':'HOME','complete_history':True,'arrival_occurred':False}));self.assertEqual(r['state'],'UNKNOWN')
 async def test_partial_history_not_absence(self):
  e,ids=self.schedule();r=await e.due(self.s.get('temporal',ids[0]),AsyncMock(return_value={'fresh':True,'healthy':True,'presence':'HOME','complete_history':False,'arrival_occurred':False}));self.assertEqual(r['state'],'UNKNOWN')
 async def test_exact_boundary_arrival_satisfied(self):
  e,ids=self.schedule();row=self.s.get('temporal',ids[0]);self.clock.t=row['due'];r=await e.due(row,AsyncMock(return_value={'fresh':True,'healthy':True,'presence':'OFFICE','arrival_occurred':True}));self.assertEqual(r['state'],'SATISFIED')
 async def test_duration_generic_session(self):
  m=pattern('duration');e=self.engine(m);ids=e.reconcile([m],{'switch.generic':{'started_at':self.clock()-1800,'weekday':'Monday','fresh':True}});self.assertEqual(len(ids),1);self.assertEqual(self.s.get('temporal',ids[0])['due'],self.clock()+1800)
 async def test_device_stopped_satisfied(self):
  m=pattern('duration');e=self.engine(m);ids=e.reconcile([m],{'switch.generic':{'started_at':self.clock()-1800,'weekday':'Monday','fresh':True}});r=await e.due(self.s.get('temporal',ids[0]),AsyncMock(return_value={'fresh':True,'healthy':True,'active':False}));self.assertEqual(r['state'],'SATISFIED')
 async def test_device_still_same_session_violation(self):
  m=pattern('duration');e=self.engine(m);start=self.clock()-1800;ids=e.reconcile([m],{'switch.generic':{'started_at':start,'weekday':'Monday','fresh':True}});r=await e.due(self.s.get('temporal',ids[0]),AsyncMock(return_value={'fresh':True,'healthy':True,'active':True,'session_start':start}));self.assertEqual(r['state'],'VIOLATED');self.assertEqual(self.s.rows('proposals'),[])
 async def test_changed_session_not_old_violation(self):
  m=pattern('duration');e=self.engine(m);ids=e.reconcile([m],{'switch.generic':{'started_at':self.clock()-1800,'weekday':'Monday','fresh':True}});r=await e.due(self.s.get('temporal',ids[0]),AsyncMock(return_value={'fresh':True,'healthy':True,'active':True,'session_start':self.clock()}));self.assertEqual(r['state'],'UNKNOWN')
 async def test_confidence_drop_cancels(self):
  e,ids=self.schedule();m=pattern(confidence=.3);e.reconcile([m]);self.assertEqual(self.s.get('temporal',ids[0])['state'],'CANCELLED')
 async def test_consolidation_duplicate_not_duplicate_wake(self):
  e,ids=self.schedule();e.reconcile(list(self.mem.values()));self.assertEqual(len(self.s.rows('temporal')),1)
 async def test_memory_changes_during_factual_recheck(self):
  e,ids=self.schedule()
  async def recheck(p):self.mem['memory1']=pattern(version='v2');return {'fresh':True,'healthy':True,'presence':'HOME','arrival_occurred':False,'complete_history':True}
  r=await e.due(self.s.get('temporal',ids[0]),recheck);self.assertEqual(r['state'],'CANCELLED');self.assertEqual(self.s.rows('wakes'),[])
 async def test_cancellation_during_recheck(self):
  e,ids=self.schedule()
  async def recheck(p):self.sch.cancel(ids[0]);return {'fresh':True,'healthy':True,'presence':'HOME','arrival_occurred':False,'complete_history':True}
  r=await e.due(self.s.get('temporal',ids[0]),recheck);self.assertEqual(r['state'],'CANCELLED')
 async def test_global_integration_disabled_no_boundary(self):
  m=pattern();e=self.engine(m,allowed=False);self.assertEqual(e.reconcile([m]),[])
 async def test_dst_nonexistent_arrival_not_scheduled(self):
  self.clock.t=datetime.fromisoformat('2026-03-08T05:00:00+00:00').timestamp();m=pattern();m['structured_value']['group'][3]='Sunday';m['structured_value']['statistics']['last_recalculation']='2026-03-08T05:00:00+00:00';m['structured_value']['evidence_window']['end']='2026-03-07T05:00:00+00:00';m['structured_value']['temporal_boundaries']['arrival_after_local_minutes']=150;e=Expectations(self.s,self.sch,lambda _:m,lambda:True,'America/New_York');ids=e.reconcile([m]);self.assertTrue(all(self.s.get('temporal',i)['data']['day']!='2026-03-08' for i in ids))
class WatchTests(Base):
 def watches(self):return Watches(self.s,self.sch,max_self=2)
 def create(self,w,subject='fan.generic',**kw):return w.create(subject,{'property':'state','equals':'off'},'Check after ongoing session.',self.clock()+60,evidence=['e'],**kw)
 async def test_create_and_equivalent_dedupe(self):
  w=self.watches();self.assertEqual(self.create(w),self.create(w));self.assertEqual(len(self.s.rows('watches')),1)
 async def test_relevant_condition_wakes_once(self):
  w=self.watches();self.create(w);w.event('fan.generic','state','off','event1');w.event('fan.generic','state','off','event2');self.assertEqual(len(self.s.rows('wakes')),1)
 async def test_unrelated_event_no_wake(self):
  w=self.watches();self.create(w);self.assertEqual(w.event('light.other','state','off','event'),[])
 async def test_timeout_wakes_only_cognition(self):
  w=self.watches();self.create(w);self.clock.advance(61);await self.sch.run_due(AsyncMock(side_effect=w.timeout));self.assertEqual(len(self.s.rows('wakes')),1);self.assertEqual(self.s.rows('proposals'),[])
 async def test_closed_never_wakes(self):
  w=self.watches();i=self.create(w);w.close(i);self.assertEqual(w.event('fan.generic','state','off','e'),[])
 async def test_max_self_count(self):
  w=self.watches();self.create(w,'fan.a');self.create(w,'fan.b')
  with self.assertRaises(ValueError):self.create(w,'fan.c')
 async def test_depth_and_expiry_bounded(self):
  w=self.watches()
  with self.assertRaises(ValueError):self.create(w,depth=3)
  with self.assertRaises(ValueError):w.create('x',{'property':'state','equals':'on'},'x',self.clock(),evidence=['e'])
 async def test_explicit_requires_provenance_and_retains_authority(self):
  w=self.watches()
  with self.assertRaises(ValueError):self.create(w,authority='USER_EXPLICIT')
  i=self.create(w,authority='USER_EXPLICIT',commitment_ref='user:req');self.assertEqual(self.s.get('watches',i)['data']['authority'],'USER_EXPLICIT')
class GateTests(Base):
 def gate(self,**flags):return Gates(self.s,Config(**flags),lambda a:True)
 async def test_tell_shadow_wording_and_evidence_retained(self):
  g=self.gate();r=g.evaluate(envelope(),proposal(),{'fresh':True,'healthy':True});self.assertEqual(r['result'],'WOULD_TELL');d=self.s.get('proposals',r['proposal_id'])['data'];self.assertEqual(d['proposal']['message'],'The projector may have been left on.');self.assertEqual(d['evidence_refs'],['event1']);self.assertFalse(d['external_effect'])
 async def test_ask_shadow_independent(self):
  g=self.gate(proactive_tell_enabled=True);r=g.evaluate(envelope(),proposal('ASK_USER'),{'fresh':True,'healthy':True});self.assertEqual(r['result'],'WOULD_ASK')
 async def test_act_shadow_generic_target(self):
  g=self.gate(proactive_tell_enabled=True,proactive_ask_enabled=True);p={'operation':'ACT','reason':'Explicit target request.','confidence':.9,'action':{'domain':'light','service':'turn_off','entity_ids':['light.x']}};r=g.evaluate(envelope('USER_EXPLICIT'),p,{'fresh':True,'healthy':True,'action_authorized':True});self.assertEqual(r['result'],'WOULD_ACT')
 async def test_duplicate_cross_channel_suppressed(self):
  g=self.gate();g.evaluate(envelope(),proposal(),{'fresh':True,'healthy':True});r=g.evaluate(envelope(),proposal('ASK_USER',message='Is that intended?',destination='voice'),{'fresh':True,'healthy':True});self.assertEqual(r['result'],'SUPPRESSED_DUPLICATE')
 async def test_distinct_incidents_not_suppressed(self):
  g=self.gate();g.evaluate(envelope(),proposal(),{'fresh':True,'healthy':True});r=g.evaluate(envelope(incident='different'),proposal(),{'fresh':True,'healthy':True});self.assertEqual(r['result'],'WOULD_TELL')
 async def test_already_known_suppressed(self):self.assertEqual(self.gate().evaluate(envelope(),proposal(already_known=True),{'fresh':True,'healthy':True})['result'],'SUPPRESSED')
 async def test_silently_resolvable_suppressed(self):self.assertEqual(self.gate().evaluate(envelope(),proposal(can_resolve_silently=True),{'fresh':True,'healthy':True})['result'],'SUPPRESSED')
 async def test_low_confidence_even_with_flag_true(self):self.assertEqual(self.gate(proactive_tell_enabled=True).evaluate(envelope(),proposal(confidence=.3),{'fresh':True,'healthy':True})['result'],'SUPPRESSED')
 async def test_unknown_provider_even_with_flag_true(self):self.assertEqual(self.gate(proactive_tell_enabled=True).evaluate(envelope(),proposal(),{'fresh':True,'healthy':False})['result'],'SUPPRESSED')
 async def test_invalid_target_fails_closed(self):
  g=Gates(self.s,Config(proactive_act_enabled=True),lambda a:False);p={'operation':'ACT','reason':'x','confidence':.9,'action':{'domain':'light','service':'turn_off','entity_ids':['ambiguous']}};self.assertEqual(g.evaluate(envelope('USER_EXPLICIT'),p,{'fresh':True,'healthy':True})['result'],'SUPPRESSED')
 async def test_unknown_outcome_no_retry(self):
  p={'operation':'ACT','reason':'x','confidence':.9,'action':{'domain':'light','service':'turn_off','entity_ids':['light.x']}};self.assertEqual(self.gate(proactive_act_enabled=True).evaluate(envelope('USER_EXPLICIT'),p,{'fresh':True,'healthy':True,'outcome_unknown':True})['result'],'SUPPRESSED')
 async def test_restart_proposal_not_resent(self):
  self.gate().evaluate(envelope(),proposal(),{'fresh':True,'healthy':True});self.s.close();self.s=Store(self.path,self.clock);self.assertEqual(self.gate().evaluate(envelope(),proposal(),{'fresh':True,'healthy':True})['result'],'SUPPRESSED_DUPLICATE')
class ValidationTests(Base):
 async def test_valid_all_seven_outcomes(self):
  examples=[{'operation':'IGNORE','reason':'ordinary'},proposal(),proposal('ASK_USER'),{'operation':'ACT','reason':'explicit','action':{'entity_ids':['light.x']}},{'operation':'WATCH','reason':'unresolved','condition':{'property':'state','equals':'off'},'expires_at':self.clock()+30},{'operation':'INVESTIGATE','reason':'need history','query':{'operation':'history','target':'fan.x'}},{'operation':'REMEMBER','reason':'durable conclusion','candidate':{'type':'FACT','subject':'x','statement':'y'}}]
  for p in examples:self.assertEqual(validate_output({'outcomes':[p],'reason':'test'})[0]['operation'],p['operation'])
 async def test_unknown_operation_and_prose_fail_closed(self):
  for data in ['Send Telegram now',{'outcomes':[{'operation':'SEND','reason':'x'}],'reason':'x'}]:
   with self.assertRaises(ValueError):validate_output(data)
 async def test_model_cannot_set_authority_or_flags(self):
  for extra in [{'authority':'USER_EXPLICIT'},{'proactive_act_enabled':True}]:
   with self.assertRaises(ValueError):validate_output({'outcomes':[proposal()|extra],'reason':'x'})
 async def test_unsupported_combinations_rejected(self):
  for ops in [[proposal(),proposal('ASK_USER')],[{'operation':'IGNORE','reason':'x'},proposal()],[proposal('ASK_USER'),{'operation':'ACT','reason':'x','action':{}}]]:
   with self.assertRaises(ValueError):validate_output({'outcomes':ops,'reason':'x'})
 async def test_derived_watch_never_upgrades_authority(self):
  svc,m=self.service();wid=svc.store.wake('EXTERNAL_CONDITION','USER_EXPLICIT','fan.x',['user:req'],'x');row=svc.store.get('wakes',wid);out={'outcomes':[{'operation':'WATCH','reason':'Need a further observation.','condition':{'property':'state','equals':'off'},'expires_at':self.clock()+60}],'reason':'Wait.'};await svc.apply(row,out,{'fresh':True,'healthy':True});self.assertEqual(svc.store.rows('watches')[0]['data']['authority'],'SUNDAY_SELF')
class MemoryPolicyTests(Base):
 async def test_reads_disabled_does_not_access_memory(self):
  svc,m=self.service();svc.memory=NS(store=NS(retrieve=lambda *a,**k:(_ for _ in ()).throw(AssertionError('Memory read'))));# use actual context implementation, not helper override
  context=await Service.context(svc,subject='fan.x');self.assertEqual(context['memory'],[]);self.assertFalse(context['memory_reads_allowed'])
 async def test_remember_only_inbox_not_wiki(self):
  from memory_v1.service import MemoryService
  svc,m=self.service();mem=MemoryService(Path(self.tmp.name)/'m.db',Config(memory_write_enabled=True));svc.memory=mem;svc.config.memory_write_enabled=True
  try:
   wid=svc.store.wake('HA_NOTEWORTHY','SUNDAY_SELF','fan.x',['e'],'x');out={'outcomes':[{'operation':'REMEMBER','reason':'Durable inferred relationship backed by evidence.','candidate':{'type':'FACT','subject':'test','statement':'Synthetic inference.','confidence':.7}}],'reason':'Durable inference.'};await svc.apply(svc.store.get('wakes',wid),out,{});self.assertEqual(len(mem.store.candidates()),1);self.assertEqual(mem.store.inspect('memories'),[]);self.assertEqual(mem.store.candidates()[0]['data']['source_class'],'SUNDAY_INFERENCE')
  finally:mem.close()
 async def test_effects_cannot_enable_memory_reads(self):
  svc,m=self.service(proactive_tell_enabled=True,proactive_ask_enabled=True,proactive_act_enabled=True);self.assertFalse(svc.memory_allowed());self.assertFalse(svc.config.memory_read_enabled)
class PersistenceTests(Base):
 async def test_concurrent_claim_only_one_worker(self):
  i=self.s.wake('HA_NOTEWORTHY','SUNDAY_SELF','fan',['e'],'x');b=Store(self.path,self.clock)
  try:self.assertTrue(self.s.claim('wakes',i));self.assertIsNone(b.claim('wakes',i))
  finally:b.close()
 async def test_unknown_model_outcome_not_replayed(self):
  i=self.s.wake('HA_NOTEWORTHY','SUNDAY_SELF','fan',['e'],'x');self.s.claim('wakes',i,seconds=1);self.clock.advance(2);self.s.recover();self.assertEqual(self.s.get('wakes',i)['state'],'UNKNOWN')
 async def test_failed_oversize_write_is_atomic(self):
  with self.assertRaises(ValueError):self.s.put('decisions',{'bad':'x'*65000},dedupe='bad')
  self.assertEqual(self.s.rows('decisions'),[])
  import sqlite3
  svc,models=self.service();svc.store.seen=lambda identity:(_ for _ in ()).throw(sqlite3.OperationalError('fixture unavailable'))
  svc.on_event(event());self.assertEqual(models.calls,[]);self.assertEqual(svc.store.rows('proposals'),[])
 async def test_cross_path_incident_dedup_and_restart(self):
  a=self.s.wake('HA_NOTEWORTHY','SUNDAY_SELF','fan',['ha'],'request:r');b=self.s.wake('EXECUTION_ANOMALY','SYSTEM','fan',['execution'],'request:r');self.assertEqual(a,b);self.s.close();self.s=Store(self.path,self.clock);self.assertEqual(self.s.wake('HA_NOTEWORTHY','SUNDAY_SELF','fan',['ha'],'request:r'),a)
class CostTests(Base):
 async def test_rate_zero_fails_before_provider(self):
  model=Models(self.s,Config(proactive_attention_calls_per_hour=0))
  with patch('aiohttp.ClientSession',side_effect=AssertionError('Network forbidden')):
   with self.assertRaises(RuntimeError):await model.call('attention',{},'x')
  self.assertEqual(self.s.rows('usage'),[])
 async def test_bounded_context_rejects_before_provider(self):
  model=Models(self.s,Config())
  with patch('aiohttp.ClientSession',side_effect=AssertionError('Network forbidden')):
   with self.assertRaises(ValueError):await model.call('attention',{'text':'x'*31000},'x')
 async def test_model_usage_cost_and_no_tools(self):
  cfg=Config(openai_api_key='fake',proactive_attention_calls_per_hour=1);model=Models(self.s,cfg);self.s.set_meta('pricing:gpt-6-luna',{'at':self.clock(),'input':.1,'cached':.01,'write':.125,'output':.5,'source':'fixture'})
  sent=[]
  class Response:
   status=200
   async def __aenter__(self):return self
   async def __aexit__(self,*args):pass
   async def json(self):return {'status':'completed','model':'gpt-6-luna','id':'response-fixture','output':[{'type':'message','content':[{'type':'output_text','text':'decision: NORMAL\nreason: Ordinary.'}]}],'usage':{'input_tokens':100,'output_tokens':10,'input_tokens_details':{'cached_tokens':0}}}
  class Session:
   def __init__(self,*a,**kw):pass
   async def __aenter__(self):return self
   async def __aexit__(self,*args):pass
   def post(self,url,**kw):sent.append(kw['json']);return Response()
  with patch('aiohttp.ClientSession',Session):
   raw,uid=await model.call('attention',{},'x')
   with self.assertRaises(RuntimeError):await model.call('attention',{},'y')
  self.assertNotIn('tools',sent[0]);self.assertNotIn('previous_response_id',sent[0]);self.assertEqual(sent[0]['reasoning'],{'effort':'low'});u=self.s.get('usage',uid);self.assertAlmostEqual(u['data']['estimated_cost_usd'],.000015)
  # Two concurrent autonomous slots are enforced even with four simultaneous requests.
  active=[0,0]
  class ConcurrentResponse(Response):
   async def __aenter__(self):active[0]+=1;active[1]=max(active);return self
   async def __aexit__(self,*args):active[0]-=1
   async def json(self):await asyncio.sleep(.01);return await super().json()
  class ConcurrentSession(Session):
   def post(self,url,**kw):return ConcurrentResponse()
  model2=Models(self.s,Config(openai_api_key='fake',proactive_model_concurrency=2,proactive_attention_calls_per_hour=10))
  with patch('aiohttp.ClientSession',ConcurrentSession):await asyncio.gather(*(model2.call('attention',{},'concurrent'+str(i)) for i in range(4)))
  self.assertEqual(active[1],2)
class AuthorityTests(Base):
 async def test_all_authorities_preserved_invalid_missing_fail_safe(self):
  for authority in ('USER_EXPLICIT','USER_INFERRED','SUNDAY_SELF','SYSTEM'):
   i=self.s.wake('SELF_RECHECK',authority,'subject',['e'],authority);self.assertEqual(self.s.get('wakes',i)['data']['authority'],authority)
  for bad in (None,'MODEL','',True):
   with self.assertRaises(ValueError):self.s.wake('SELF_RECHECK',bad,'subject',['e'],'bad')
class E2EShadowTests(unittest.IsolatedAsyncioTestCase):pass
for _label in 'ABCDEFGHIJKL':
 def make(label):
  async def test(self):
   from proactivity_v1.fixtures import scenario
   result=await scenario(label);self.assertTrue(result['passed']);self.assertEqual(result['external_effects'],0)
  return test
 setattr(E2EShadowTests,'test_shadow_fixture_'+_label,make(_label))
