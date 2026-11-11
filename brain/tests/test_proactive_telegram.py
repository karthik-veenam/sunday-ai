"""Release acceptance uses fake Telegram and temporary databases only."""
import asyncio,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,Mock
from config import Config
from proactivity_v1.delivery import Delivery,route,read_controls
from proactivity_v1.delivery_recheck import recheck,final_check,invalidate_event
from proactivity_v1.release_fixtures import Harness,proposal,scenario
from proactivity_v1.policy import validate_output
from proactivity_v1.store import digest
from proactivity_v1.inspection import inspect
from telegram_transport import TelegramTransport,Rejected

class Base(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'proactive.db';self.h=Harness(self.path);self.h.d.revalidate=AsyncMock(return_value={'eligible':True,'presence':'HOME','context':{'fresh':True,'healthy':True,'quiet':False}})
 async def asyncTearDown(self):await self.h.close();self.temp.cleanup()
 async def make(self,op='TELL_USER',**kwargs):return await self.h.proposed(op,**kwargs)
 def receipt(self,did):return self.h.s.get('deliveries',did)
 def change_proposal(self,pid,mutate=None,**fields):
  row=self.h.s.get('proposals',pid);data=row['data'];data.update(fields)
  if mutate:mutate(data)
  self.h.s.change('proposals',pid,data=data)

class RouterTests(Base):
 async def test_all_presence_states_only_telegram(self):
  for presence in ['HOME','OFFICE','AWAY','UNKNOWN']:
   self.assertEqual(route(presence,True),{'channel':'telegram','presence_at_route_time':presence})
 async def test_presence_without_gate_cannot_route(self):
  for presence in ['HOME','OFFICE','AWAY','UNKNOWN']:
   with self.assertRaises(ValueError):route(presence)
  self.assertEqual(self.h.s.rows('proposals'),[])
 async def test_arbitrary_channel_and_voice_rejected(self):
  for channel in ['voice','email','https://example.invalid','both']:
   with self.assertRaises(ValueError):route('HOME',True,channel)
 async def test_model_cannot_choose_voice(self):
  pid,did=await self.make();self.change_proposal(pid,lambda d:d['proposal'].update(destination='voice'));await self.h.d.process(did);self.assertEqual(self.receipt(did)['state'],'SUPPRESSED');self.assertEqual(self.h.api.calls,[])
 async def test_telegram_failure_never_falls_back_to_voice(self):
  self.h.api.error=TimeoutError();pid,did=await self.make();await self.h.d.process(did);self.assertEqual(self.receipt(did)['state'],'UNKNOWN');self.assertEqual([m for m,_ in self.h.api.calls],['sendMessage'])

class AdapterTests(Base):
 async def test_approved_tell(self):
  pid,did=await self.make();await self.h.d.process(did);self.assertEqual(self.receipt(did)['state'],'DELIVERED')
 async def test_approved_ask(self):
  pid,did=await self.make('ASK_USER');await self.h.d.process(did);self.assertEqual(self.receipt(did)['data']['operation'],'ASK_USER')
 async def test_act_rejected_no_dispatch(self):
  pid,did=await self.make('ACT');self.assertIsNone(did);self.assertIsNone(self.h.d.enqueue(pid));self.assertEqual(self.h.api.calls,[])
 async def test_missing_gate_rejected(self):
  pid,did=await self.make();self.change_proposal(pid,gate_decision_id=None);await self.h.d.process(did);self.assertEqual(self.receipt(did)['state'],'SUPPRESSED')
 async def test_stale_and_cancelled_rejected(self):
  pid,did=await self.make();self.h.clock.t+=181;await self.h.d.process(did);self.assertEqual(self.receipt(did)['state'],'SUPPRESSED')
  pid,did=await self.make(incident='cancelled');self.h.s.change('proposals',pid,state='CANCELLED');await self.h.d.process(did);self.assertEqual(self.h.api.calls,[])
 async def test_malformed_message_and_authority_tampering_rejected(self):
  for i,field in enumerate([{'message':''},{'message':'x'*1201},{'message':'x\x00y'}]):
   pid,did=await self.make(incident=str(i));self.change_proposal(pid,lambda d:d['proposal'].update(field));await self.h.d.process(did)
  pid,did=await self.make(incident='authority');self.change_proposal(pid,authority='USER_EXPLICIT');await self.h.d.process(did);self.assertEqual(self.h.api.calls,[])
 async def test_arbitrary_destination_fields_rejected(self):
  for i,k in enumerate(['chat_id','token','url','channel']):
   pid,did=await self.make(incident=str(i));self.change_proposal(pid,lambda d:d['proposal'].update({k:'UNTRUSTED'}));await self.h.d.process(did)
  self.assertEqual(self.h.api.calls,[])
 async def test_only_trusted_destination_and_no_secret_audit(self):
  self.h.cfg.telegram_bot_token='SUPER_PRIVATE_SECRET';pid,did=await self.make();await self.h.d.process(did);self.assertEqual(self.h.api.calls[0][1]['chat_id'],'10001');self.assertNotIn('SUPER_PRIVATE_SECRET',json.dumps(inspect(self.h.service,{'id':did},self.h.cfg)));self.assertNotIn('10001',json.dumps(self.receipt(did)))

class FlagTests(Base):
 async def test_tell_and_ask_independent(self):
  self.h.cfg.proactive_tell_enabled=False;pid,did=await self.make();self.assertIsNone(did);self.assertEqual(self.h.s.get('proposals',pid)['state'],'WOULD_TELL')
  pid,did=await self.make('ASK_USER',incident='ask');await self.h.d.process(did);self.assertEqual(len(self.h.api.calls),1);self.assertFalse(self.h.cfg.proactive_act_enabled);self.assertFalse(self.h.cfg.proactive_voice_enabled)
 async def test_ask_disabled_does_not_disable_tell(self):
  self.h.cfg.proactive_ask_enabled=False;pid,did=await self.make('ASK_USER');self.assertIsNone(did);self.assertEqual(self.h.s.get('proposals',pid)['state'],'WOULD_ASK');pid,did=await self.make(incident='tell');await self.h.d.process(did);self.assertEqual(len(self.h.api.calls),1)
 async def test_flags_cannot_be_model_output(self):
  for extra in ['proactive_tell_enabled','proactive_ask_enabled','proactive_act_enabled','proactive_voice_enabled','authority']:
   with self.assertRaises(ValueError):validate_output({'reason':'x','outcomes':[proposal()|{extra:True}]})
 async def test_file_rollback_hot_controls_and_restart_safety(self):
  path=Path(self.temp.name)/'config.json';data={'proactive_tell_enabled':True,'proactive_ask_enabled':True,'proactive_act_enabled':False,'proactive_voice_enabled':False};path.write_text(json.dumps(data));self.h.d.controls_reader=lambda:read_controls(path);pid,did=await self.make();data['proactive_tell_enabled']=False;path.write_text(json.dumps(data));await self.h.d.process(did);self.assertEqual(self.receipt(did)['state'],'SUPPRESSED');path.unlink();self.assertFalse(any(read_controls(path).values()))

class RevalidationTests(Base):
 async def test_resolved_incident(self):
  pid,did=await self.make();incident=self.receipt(did)['data']['incident_id'];self.h.s.change('incidents',incident,state='RESOLVED');await self.h.d.process(did);self.assertEqual(self.receipt(did)['state'],'CANCELLED');self.assertEqual(self.h.api.calls,[])
 async def test_office_arrival_between_rechecks(self):
  self.h.d.revalidate=lambda row:recheck(self.h.service,row);pid,did=await self.h.office();self.h.presence.get_presence=AsyncMock(side_effect=[NS(presence='HOME'),NS(presence='OFFICE')]);await self.h.d.process(did);self.assertEqual(self.receipt(did)['state'],'SUPPRESSED');self.assertEqual(self.h.api.calls,[])
 async def test_unknown_provider_suppresses_absence(self):
  self.h.d.revalidate=lambda row:recheck(self.h.service,row);pid,did=await self.h.office();self.h.service.recheck=AsyncMock(return_value={'fresh':False,'healthy':False,'presence':'UNKNOWN'});await self.h.d.process(did);self.assertEqual(self.receipt(did)['state'],'SUPPRESSED')
 async def test_newer_decision_supersedes(self):
  pid,did=await self.make();p=self.h.s.get('proposals',pid);self.h.s.put('decisions',{'envelope':p['data']['wake'],'reason':'Updated evidence'},dedupe='newer',state='VALIDATED');await self.h.d.process(did);self.assertEqual(self.receipt(did)['state'],'SUPPRESSED')
 async def test_gate_revocation(self):
  pid,did=await self.make();aid=self.receipt(did)['data']['gate_decision_id'];self.h.s.change('approvals',aid,state='REVOKED');await self.h.d.process(did);self.assertEqual(self.h.api.calls,[])
 async def test_flag_disabled_during_async_recheck(self):
  pid,did=await self.make()
  async def check(row):self.h.cfg.proactive_tell_enabled=False;return {'eligible':True,'presence':'HOME','context':{'fresh':True,'healthy':True,'quiet':False}}
  self.h.d.revalidate=check;await self.h.d.process(did);self.assertEqual(self.h.api.calls,[])
 async def test_exact_message_changed_during_recheck(self):
  pid,did=await self.make()
  async def check(row):self.change_proposal(pid,lambda d:d['proposal'].update(message='Changed wording'));return {'eligible':True,'presence':'HOME','context':{'fresh':True,'healthy':True,'quiet':False}}
  self.h.d.revalidate=check;await self.h.d.process(did);self.assertEqual(self.h.api.calls,[])
 async def test_office_event_invalidates_inflight_approval(self):
  pid,did=await self.h.office();invalidate_event(self.h.service,{'event_id':'arrival','subject':{'id':'person:karthik'},'change':{'new':'OFFICE'}});await self.h.d.process(did);self.assertEqual(self.receipt(did)['state'],'CANCELLED')
 async def test_final_fact_guard_and_quiet_hours(self):
  self.h.service.world=NS(connected=True,store=NS(get=lambda subject,prop:{'value':'off','availability':'available'}));self.assertFalse(final_check(self.h.service,{}, {'fact_guard':[{'subject':'fan.x','property':'state','value':'on'}]}));self.h.service.world=None
  pid,did=await self.make();self.h.d.revalidate=AsyncMock(return_value={'eligible':True,'context':{'fresh':True,'healthy':True,'quiet':True}});await self.h.d.process(did);self.assertEqual(self.h.api.calls,[])

class IdempotencyTests(Base):
 async def test_repeat_and_concurrent_processing_one_send(self):
  pid,did=await self.make();await asyncio.gather(self.h.d.process(did),self.h.d.process(did));await self.h.d.process(did);self.assertEqual(len(self.h.api.calls),1)
 async def test_same_incident_wording_cannot_bypass(self):
  pid,did=await self.make();await self.h.d.process(did);pid2,did2=await self.make('ASK_USER');self.assertEqual(pid,pid2);self.assertEqual(did,did2);await self.h.d.process(did2);self.assertEqual(len(self.h.api.calls),1)
 async def test_cross_path_incident_once(self):
  pid,did=await self.make(incident='request:a');pid2,did2=await self.make(incident='request:a',kind='EXECUTION_ANOMALY');await self.h.d.process(did);self.assertEqual(did,did2);self.assertEqual(len(self.h.s.rows('wakes')),1)
 async def test_restart_after_delivered_never_resends(self):
  pid,did=await self.make();await self.h.d.process(did);api=self.h.api;clock=self.h.clock;await self.h.close();self.h=Harness(self.path,clock,api);self.h.d.recover();await self.h.d.process(did);self.assertEqual(len(api.calls),1)
 async def test_restart_dispatching_unknown(self):
  pid,did=await self.make();self.h.s.change('deliveries',did,state='DISPATCHING');api=self.h.api;clock=self.h.clock;await self.h.close();self.h=Harness(self.path,clock,api);self.h.d.recover();await self.h.d.process(did);self.assertEqual(self.receipt(did)['state'],'UNKNOWN');self.assertEqual(api.calls,[])
 async def test_duplicate_wake_no_new_cognition(self):
  pid,did=await self.make();before=len(self.h.s.rows('decisions'));await self.make(kind='EXECUTION_ANOMALY');self.assertEqual(len(self.h.s.rows('decisions')),before);await self.h.d.process(did);self.assertEqual(len(self.h.api.calls),1)

class FailureTests(Base):
 async def test_definite_flood_retries_bounded_and_persisted(self):
  self.h.api.error=Rejected(1,429);pid,did=await self.make()
  for _ in range(3):await self.h.d.process(did);self.h.clock.t+=10
  r=self.receipt(did);self.assertEqual(r['state'],'FAILED');self.assertEqual(r['data']['attempts'],3);self.assertFalse(r['data']['retryable']);await self.h.d.process(did);self.assertEqual(len(self.h.api.calls),3)
 async def test_permanent_failure_no_loop(self):
  self.h.api.error=Rejected(None,403);pid,did=await self.make();await self.h.d.process(did);await self.h.d.process(did);self.assertEqual(self.receipt(did)['state'],'FAILED');self.assertEqual(len(self.h.api.calls),1)
 async def test_ambiguous_timeout_no_resend(self):
  self.h.api.error=TimeoutError('SECRET_TOKEN_MUST_NOT_LOG');pid,did=await self.make();await self.h.d.process(did);await self.h.d.process(did);self.assertEqual(self.receipt(did)['state'],'UNKNOWN');self.assertEqual(len(self.h.api.calls),1);self.assertNotIn('SECRET_TOKEN_MUST_NOT_LOG',json.dumps(self.h.s.chain(did)))
 async def test_restart_keeps_retry_counter_and_backoff(self):
  self.h.api.error=Rejected(20,429);pid,did=await self.make();await self.h.d.process(did);api=self.h.api;clock=self.h.clock;await self.h.close();self.h=Harness(self.path,clock,api);self.h.d.recover();await self.h.d.process(did);self.assertEqual(len(api.calls),1);self.assertEqual(self.receipt(did)['data']['attempts'],1);self.h.clock.t+=21;self.h.d.revalidate=AsyncMock(return_value={'eligible':True,'context':{'fresh':True,'healthy':True}});await self.h.d.process(did);self.assertEqual(self.receipt(did)['data']['attempts'],2)
 async def test_server_or_missing_receipt_unknown(self):
  self.h.api.error=Rejected(None,500);pid,did=await self.make();await self.h.d.process(did);self.assertEqual(self.receipt(did)['state'],'UNKNOWN')
  self.h.api.error=None;self.h.api.call=AsyncMock(return_value={});pid,did=await self.make(incident='missing-receipt');await self.h.d.process(did);self.assertEqual(self.receipt(did)['state'],'UNKNOWN')

class ContinuityTests(Base):
 async def test_ask_reply_enters_existing_telegram_brain(self):
  pid,did=await self.make('ASK_USER');await self.h.d.process(did)
  class Runtime:
   config=self.h.cfg;proactivity=self.h.service
   def __init__(self):self.calls=[]
   async def turn(self,request,emit):self.calls.append(request);await emit({'type':'final','text':'Understood.'})
  rt=Runtime();transport=TelegramTransport(rt,Path(self.temp.name)/'telegram.db',self.h.api)
  try:
   msg={'update_id':1,'message':{'chat':{'id':10001,'type':'private'},'from':{'id':10001},'text':'Yes, working from home.','reply_to_message':{'message_id':self.receipt(did)['data']['telegram_message_id']}}};await transport.handle(msg);self.assertEqual(len(rt.calls),1);self.assertEqual(rt.calls[0]['conversation_id'],'telegram:10001');self.assertEqual(rt.calls[0]['proactive_reply_to'],did)
  finally:await transport.close()
 async def test_missing_correlation_normal_reply_still_works(self):
  class Runtime:
   config=self.h.cfg;proactivity=self.h.service
   def __init__(self):self.calls=[]
   async def turn(self,request,emit):self.calls.append(request);await emit({'type':'final','text':'Ready.'})
  rt=Runtime();transport=TelegramTransport(rt,Path(self.temp.name)/'telegram.db',self.h.api)
  try:
   await transport.handle({'update_id':1,'message':{'chat':{'id':10001,'type':'private'},'from':{'id':10001},'text':'Hi','reply_to_message':{'message_id':99999}}});self.assertEqual(len(rt.calls),1);self.assertNotIn('proactive_reply_to',rt.calls[0])
  finally:await transport.close()
 async def test_delivered_context_linked_to_existing_history_once(self):
  from runtime import Runtime
  pid,did=await self.make('ASK_USER');await self.h.d.process(did);rt=NS(proactivity=self.h.service,config=self.h.cfg,saved_histories={},agents={},persist=Mock());Runtime.record_proactive_delivery(rt,self.receipt(did));Runtime.record_proactive_delivery(rt,self.receipt(did));self.assertEqual(len(rt.saved_histories['telegram:10001']['messages']),1);self.assertTrue(self.receipt(did)['data']['conversation_linked'])

class CompatibilityTests(Base):
 async def test_old_shadow_never_flushed(self):
  self.h.cfg.proactive_tell_enabled=False;pid,did=await self.make();self.assertIsNone(did);self.h.cfg.proactive_tell_enabled=True;self.h.d.recover();self.assertIsNone(self.h.d.enqueue(pid));self.assertEqual(self.h.s.rows('deliveries'),[]);self.assertEqual(self.h.s.get('proposals',pid)['state'],'WOULD_TELL')
 async def test_inspection_delivered_suppressed_failed_and_chain(self):
  pid,did=await self.make();await self.h.d.process(did);r=inspect(self.h.service,{'view':'delivered'},self.h.cfg);self.assertEqual(len(r['records']),1);chain=self.h.s.chain(did);tables={x['table'] for x in chain['records'].values()};self.assertTrue({'deliveries','proposals','decisions','approvals','wakes','incidents','audit'}<=tables);self.assertEqual(len(inspect(self.h.service,{'view':'would'},self.h.cfg)['records']),1)
 async def test_safe_defaults_and_unresolved_destination(self):
  defaults=Config();self.assertFalse(defaults.proactive_tell_enabled or defaults.proactive_ask_enabled or defaults.proactive_act_enabled or defaults.proactive_voice_enabled);self.h.cfg.telegram_users={};pid,did=await self.make();await self.h.d.process(did);self.assertEqual(self.h.api.calls,[])

class AdditionalSafetyTests(Base):
 async def test_conditional_source_cancelled_before_send(self):
  self.h.d.revalidate=lambda row:recheck(self.h.service,row);pid,did,tid=await self.h.conditional();self.h.s.change('temporal',tid,state='CANCELLED');await self.h.d.process(did);self.assertEqual(self.h.api.calls,[])
 async def test_condition_no_longer_holds(self):
  self.h.d.revalidate=lambda row:recheck(self.h.service,row);pid,did,tid=await self.h.conditional();self.h.service.world.store.get=lambda *args:{'value':'on','availability':'available'};await self.h.d.process(did);self.assertEqual(self.h.api.calls,[])
 async def test_dispatch_is_durable_before_network(self):
  pid,did=await self.make()
  async def send(method,**data):
   self.assertEqual(self.receipt(did)['state'],'DISPATCHING');self.assertEqual(self.receipt(did)['data']['attempts'],1);return {'message_id':9}
  self.h.api.call=send;await self.h.d.process(did);self.assertEqual(self.receipt(did)['state'],'DELIVERED')
 async def test_cancelled_transport_is_unknown(self):
  self.h.api.error=asyncio.CancelledError();pid,did=await self.make()
  with self.assertRaises(asyncio.CancelledError):await self.h.d.process(did)
  self.assertEqual(self.receipt(did)['state'],'UNKNOWN');await self.h.d.process(did);self.assertEqual(len(self.h.api.calls),1)

class MigrationTests(Base):
 async def test_schema_upgrade_preserves_shadow_and_no_backlog_flush(self):
  import sqlite3
  from proactivity_v1.store import Store
  self.h.cfg.proactive_tell_enabled=False;pid,did=await self.make();self.h.s.db.execute('pragma user_version=1');self.h.s.db.commit();clock=self.h.clock;api=self.h.api;await self.h.close();self.h=Harness(self.path,clock,api);self.h.d.recover();self.assertEqual(self.h.s.db.execute('pragma user_version').fetchone()[0],2);self.assertEqual(self.h.s.get('proposals',pid)['state'],'WOULD_TELL');self.assertEqual(self.h.s.rows('deliveries'),[])

class ConversationRaceTests(Base):
 async def test_active_telegram_turn_suppresses_overlapping_ping(self):
  self.h.telegram.runtime=NS(active={'endpoint':'telegram:10001'});pid,did=await self.make();await self.h.d.process(did);self.assertEqual(self.h.api.calls,[]);self.assertEqual(self.receipt(did)['state'],'SUPPRESSED')
 async def test_delivered_context_waits_until_turn_idle(self):
  from runtime import Runtime
  pid,did=await self.make('ASK_USER');await self.h.d.process(did);rt=NS(proactivity=self.h.service,config=self.h.cfg,saved_histories={},agents={},persist=Mock(),active={'endpoint':'telegram:10001'})
  Runtime.record_proactive_delivery(rt,self.receipt(did));self.assertEqual(rt.saved_histories,{})
  rt.active.clear();Runtime.record_proactive_delivery(rt,self.receipt(did));self.assertEqual(len(rt.saved_histories['telegram:10001']['messages']),1)

class ReleaseE2E(unittest.IsolatedAsyncioTestCase):pass
for label in 'ABCDEFGHIJ':
 async def test(self,label=label):self.assertTrue((await scenario(label))['passed'])
 setattr(ReleaseE2E,'test_'+label,test)
