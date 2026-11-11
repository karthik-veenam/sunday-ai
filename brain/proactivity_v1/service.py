"""Event-driven cognition; optional trusted Telegram communication, never HA/voice effects."""
import asyncio,json,time
from dataclasses import asdict,is_dataclass
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
from .store import Store,digest,js,AUTH
from .scheduler import Scheduler
from .episodes import Episodes
from .expectations import Expectations
from .correlation import Execution,Health
from .watches import Watches
from .models import Models,attention_parse
from .policy import Gates,validate_output
FLAGS=('proactive_cognition_enabled','ha_attention_enabled','ha_automation_attention_enabled','expectation_engine_enabled','self_watch_enabled','proactive_tell_enabled','proactive_ask_enabled','proactive_act_enabled','proactive_voice_enabled','proactive_memory_read_enabled')
def epoch(s):
 try:return datetime.fromisoformat(str(s).replace('Z','+00:00')).timestamp()
 except (ValueError,TypeError):return 0
class Service:
 def __init__(self,path,config,*,world=None,home=None,presence=None,history=None,memory=None,conversations=lambda:[],clock=time.time,models=None):
  self.delivery=None
  self.config=config;self.world=world;self.home=home;self.presence=presence;self.history=history;self.memory=memory;self.conversations=conversations;self.store=Store(path,clock);self.scheduler=Scheduler(self.store);self.episodes=Episodes(self.store,self.scheduler,exclude_automations=not config.ha_automation_attention_enabled);self.execution=Execution(self.store,self.scheduler);self.health=Health(self.store,self.scheduler);self.watches=Watches(self.store,self.scheduler,config.proactive_max_self_watches);self.models=models or Models(self.store,config);self.action_validation={};self.gates=Gates(self.store,config,self.valid_action);self.models.health_callback=self.health.update;self.worker_event=asyncio.Event();self.tasks=[]
  from proactivity_v2.incidents import Incidents
  self.situations=Incidents(self.store,self.home) if getattr(config,'proactivity_v2_enabled',False) else None
  self.episodes.situations=self.situations
  if self.situations:
   from proactivity_v2.migration import migrate
   migrate(self.store)
  self.expectations=Expectations(self.store,self.scheduler,lambda mid:self.memory.store.get(mid) if self.memory else None,enabled=self.memory_allowed,tz=config.timezone);self.store.set_meta('flags',{k:bool(getattr(config,k)) for k in FLAGS});self.store.recover()
 def in_scope(self,data,seen=None):
  from autonomy_scope import allowed,enabled
  if not enabled(self.config) and not getattr(self.home,'input_policy',None):return True
  if not allowed(self.home,self.config,data,purpose='proactivity'):return False
  if isinstance(data,dict) and data.get('input_policy_revision') is not None and data['input_policy_revision']!=self.policy_revision():return False
  seen=set(seen or ())
  if isinstance(data,dict):return all(self.in_scope(v,seen) for k,v in data.items() if k not in {'semantic_name','service','domain'})
  if isinstance(data,list):return all(self.in_scope(v,seen) for v in data)
  if isinstance(data,str) and ':' in data and data not in seen:
   table=data.split(':',1)[0]
   if table in {'episodes','wakes','temporal','watches','executions','attention','proposals','decisions','deliveries'}:
    if len(seen)>40:return False
    seen.add(data);row=self.store.get(table,data)
    if row:return row['state']!='CANCELLED_SCOPE' and self.in_scope(row['data'],seen)
  return True
 def enforce_scope(self):
  for table,states in [('episodes',('OPEN',)),('wakes',('PENDING','READY','PROCESSING')),('watches',('ACTIVE',)),('executions',('PENDING',)),('temporal',('PENDING','PROCESSING')),('deliveries',('PROPOSED','GATE_APPROVED','RETRY','FAILED'))]:
   for row in self.store.rows(table,states,1000):
    if not self.in_scope(row['data']):self.store.change(table,row['id'],state='CANCELLED_SCOPE')
 def bind_telegram(self,telegram,*,controls=None,on_delivered=None):
  from .delivery import Delivery
  from .delivery_recheck import recheck,final_check
  self.delivery=Delivery(self.store,self.config,self.gates,telegram,lambda row:recheck(self,row),scheduler=self.scheduler,controls=controls,on_delivered=on_delivered,final_check=lambda row,check:final_check(self,row,check))
 def refresh_controls(self):
  if self.delivery:
   flags=self.delivery.controls()
   for key in ('proactive_tell_enabled','proactive_ask_enabled'):setattr(self.config,key,flags.get(key,False))
 def queue_gate(self,gate):
  if self.delivery and gate.get('result')=='GATE_APPROVED':return self.delivery.enqueue(gate['proposal_id'])
 def diagnostic(self):
  if not self.delivery:raise ValueError('Trusted Telegram adapter unavailable')
  self.refresh_controls()
  ref=self.store.audit('explicit_telegram_smoke_authorization',{'source':'SUNDAY_PROACTIVE_TELEGRAM_RELEASE.md','message':'Sunday proactive Telegram delivery test ✅'},'telegram-release-smoke-v1')
  wid=self.store.wake('EXTERNAL_CONDITION','USER_EXPLICIT','telegram-delivery-diagnostic',[ref],'telegram-release-smoke-v1',diagnostic='telegram-release-v1')
  row=self.store.get('wakes',wid);p={'operation':'TELL_USER','reason':'Explicitly authorized harmless delivery diagnostic.','message':'Sunday proactive Telegram delivery test ✅','destination':'telegram','confidence':1.0,'already_known':False,'consequence_of_silence':'The requested delivery path could not be verified.','time_sensitive':True,'useful_now':True,'can_resolve_silently':False,'interruption_cost':'low'}
  decision=self.store.put('decisions',{'wake_id':wid,'envelope':row['data'],'output':{'outcomes':[p],'reason':'Fixed authorized diagnostic; no model or household event.'}},dedupe=wid,state='VALIDATED')
  gate=self.gates.evaluate(row['data'],p,{'fresh':True,'healthy':True,'quiet':False},decision_ref=decision)
  self.queue_gate(gate);self.store.change('wakes',wid,state='DONE')
  proposal=self.store.get('proposals',gate['proposal_id']);receipt=self.store.get('deliveries','deliveries:'+digest(['telegram-incident',row['data']['incident_id']]))
  return {'proposal_id':proposal['id'],'delivery_id':receipt['id'] if receipt else None,'state':receipt['state'] if receipt else proposal['state']}
 def correlate_reply(self,chat,message_id,update_id):
  if not self.delivery:return None
  try:
   if str(chat)!=self.delivery.destination():return None
  except ValueError:return None
  found=self.store.db.execute("select * from deliveries where state='DELIVERED' and json_extract(data,'$.telegram_message_id')=? order by created desc limit 1",(message_id,)).fetchone()
  if not found:return None
  row=self.store.decode(found)
  if self.situations:
   proposal=self.store.get('proposals',row['data']['proposal_id'])
   if proposal:self.situations.feedback(proposal['data']['wake']['incident_id'],'engaged','telegram-update:'+str(update_id))
  self.store.audit('telegram_proactive_reply',{'delivery_id':row['id'],'proposal_id':row['data']['proposal_id'],'telegram_update_id':update_id},['reply',update_id]);return row['id']
 def source_allowed(self,source):
  policy=getattr(self.home,'input_policy',None)
  if policy:return policy.source('proactivity',source)
  if source=='learned_memory':return bool(self.config.memory_read_enabled and self.config.proactive_memory_read_enabled)
  if source=='automations':return bool(self.config.ha_automation_attention_enabled)
  return True
 def policy_revision(self):
  policy=getattr(self.home,'input_policy',None)
  return policy.revision if policy else None
 def memory_allowed(self):return bool(self.memory and self.source_allowed('learned_memory') and self.config.expectation_engine_enabled)
 def incident(self,events):
  if self.situations:
   row=self.situations.find(events)
   if row:return row['id']
  for e in events:
   c=e.get('correlation',{})
   if c.get('request_id'):return 'request:'+c['request_id']
  for e in events:
   c=e.get('correlation',{});cid=c.get('parent_context_id') or c.get('ha_context_id')
   if cid:return 'context:'+cid
  return 'event:'+events[0]['event_id']
 def on_event(self,event):
  try:self._on_event(asdict(event) if is_dataclass(event) else event)
  except Exception as error:
   try:self.store.audit('event_ingestion_failed_closed',{'error_type':type(error).__name__})
   except Exception:pass # No failure may escape into factual ingestion or dispatch.
 def _on_event(self,e):
  if not self.in_scope(e):return
  self.episodes.exclude_automations=not self.source_allowed('automations')
  if self.store.seen('service:'+e['event_id']):return
  if self.delivery:
   from .delivery_recheck import invalidate_event
   invalidate_event(self,e)
  typ=e['event_type'];subject=e.get('subject',{}).get('id');change=e.get('change',{});c=e.get('correlation',{})
  if typ.startswith('SUNDAY_ACTION_'):
   if self.world:
    row=self.world.store.db.execute('select data from actions where id=?',(subject,)).fetchone()
    if row:
     receipt=json.loads(row[0])
     if not self.in_scope(receipt):return
     if typ=='SUNDAY_ACTION_REQUESTED':self.execution.intent(receipt)
     else:
      self.execution.result(subject,unknown=typ=='SUNDAY_ACTION_UNKNOWN')
      # Revisit correlated service-return states that may precede the return event.
      for target in receipt.get('targets',[]):
       for fact in self.world.store.current(target,limit=16):
        if any(ref.get('request_id')==receipt.get('request_id') for ref in fact.get('provenance',{}).get('evidence',[])):
         self.execution.observe(target,fact['value'],request_id=receipt.get('request_id'),occurred=epoch(fact['source_observed_at']),available=fact.get('availability')=='available',property=fact['property'])
   self.worker_event.set();return
  self.execution.observe(subject,change.get('new'),context_ids=[c.get('ha_context_id'),c.get('parent_context_id')],request_id=c.get('request_id'),occurred=epoch(e.get('occurred_at')),available=change.get('new') not in ('unknown','UNKNOWN','unavailable'),property=change.get('property','state'))
  self.watches.event(subject,change.get('property'),change.get('new'),e['event_id'])
  # Confirmed expected consequences of a normal/Fast user action are zero-model execution work.
  clean_expected=self.execution.explained(e,require_return=False)
  if self.config.ha_attention_enabled and not clean_expected:self.episodes.add(e)
  if self.memory_allowed():self.scan_expectations()
  # OFF and presence-arrival events resolve appropriate pending boundaries without an LLM.
  for r in self.store.rows('temporal',('PENDING',),1000):
   p=r['data']
   if p['kind']!='EXPECTATION_BOUNDARY' or p.get('subject')!=subject and 'person:'+str(p.get('subject')).lower()!=str(subject).lower():continue
   if p.get('expectation_kind')=='duration' and change.get('new') in ('off','standby'):self.scheduler.cancel(r['id'],'Target session ended')
   if p.get('expectation_kind')=='arrival' and change.get('new')=='OFFICE':self.scheduler.cancel(r['id'],'Expected arrival observed')
  self.worker_event.set()
 def valid_action(self,action):
  validated=self.action_validation.get(digest(action))
  return validated is not None and self.store.clock()-validated<=5
 def active_sessions(self):
  result={}
  if not self.world:return result
  for eid in self.world.selected():
   if not self.in_scope(eid):continue
   fact=self.world.store.get(eid)
   if not fact or fact['value'] not in ('on','playing') or fact.get('availability')!='available':continue
   start=epoch(fact.get('changed_at'));result[eid]={'started_at':start,'weekday':datetime.fromtimestamp(start,ZoneInfo(self.config.timezone)).strftime('%A'),'fresh':self.world.connected and self.store.clock()-epoch(fact['observed_at'])<=60}
  return result
 def scan_expectations(self):
  if not self.config.expectation_engine_enabled:return []
  if not self.memory_allowed():return self.expectations.reconcile([])
  rows=self.memory.store.db.execute("select data from memories where type='PATTERN_EXPECTATION' and status='ACTIVE' limit 200").fetchall();return self.expectations.reconcile([json.loads(r[0]) for r in rows if self.in_scope(json.loads(r[0]))],self.active_sessions())
 async def recheck(self,p):
  if not self.in_scope(p):return {'fresh':False,'healthy':False}
  if not self.world or not self.world.connected:return {'fresh':False,'healthy':False}
  if p['expectation_kind']=='duration':
   states=await self.world.fresh_entities([p['subject']]);state=states.get(p['subject'],{}).get('state');fact=self.world.store.get(p['subject']);fresh=bool(fact and fact['availability']=='available' and self.store.clock()-epoch(fact['observed_at'])<30)
   return {'fresh':fresh,'healthy':True,'active':state in ('on','playing'),'session_start':epoch(fact['changed_at']) if fact else None,'refs':[fact.get('last_event_id')] if fact else []}
  if not self.source_allowed('presence') or not self.source_allowed('history'):return {'fresh':False,'healthy':False,'presence':'UNKNOWN'}
  if not self.presence or not self.history:return {'fresh':False,'healthy':False,'presence':'UNKNOWN'}
  snapshot=await self.presence.get_presence(person=p['subject'],force=True)
  if snapshot.presence=='UNKNOWN':return {'fresh':False,'healthy':False,'presence':'UNKNOWN'}
  start=datetime.fromisoformat(p['day']).replace(tzinfo=ZoneInfo(self.config.timezone));end=datetime.fromtimestamp(self.store.clock(),timezone.utc)
  result=await self.history.query('presence_history',p['subject'],{'start':start.isoformat(),'end':end.isoformat()})
  events=result.get('events',[]);arrived=any(e.get('new')=='OFFICE' for e in events) or snapshot.presence=='OFFICE';coverage=result.get('coverage') or {};complete=coverage.get('available_fraction',0)>=.999 and result.get('status')=='OK'
  return {'fresh':True,'healthy':True,'presence':snapshot.presence,'arrival_occurred':arrived,'complete_history':complete,'refs':result.get('source_refs',[]),'coverage':coverage}
 async def context(self,subject=None,events=None,wake=None):
  subjects=[]
  for e in events or []:
   sid=e.get('subject',{}).get('id')
   if sid and sid not in subjects:subjects.append(sid)
  if isinstance(subject,str):subjects.append(subject)
  elif isinstance(subject,list):subjects+=subject
  if wake:subjects+=wake.get('entities',[])
  subjects=[sid for sid in subjects if self.in_scope(sid)]
  facts=[];history=[];healthy=bool(self.world and self.world.connected)
  if self.world:
   for sid in list(dict.fromkeys(subjects))[:8]:
    for fact in self.world.store.current(sid,limit=5):
     fact=dict(fact);fact['stale']=self.store.clock()-epoch(fact.get('observed_at'))>60;facts.append(fact)
   # Coarse household presence and current room occupancy are context, never inferred from device activity.
   if self.source_allowed('presence'):
    for row in self.world.store.db.execute("select data from facts where subject like 'person:%' limit 4"):facts.append(json.loads(row[0]))
   if self.home:
    occupancy=[e for e in self.home.entities.values() if e.get('domain')=='binary_sensor' and e.get('attributes',{}).get('device_class') in ('occupancy','motion','presence') and (e.get('area_id') in [x.get('subject',{}).get('area') for x in facts] or e.get('area_id')==self.home.household.get('primary_area'))]
    for entity in occupancy[:3]:
     fact=self.world.store.get(entity['entity_id'])
     if fact:facts.append(fact)
   if self.source_allowed('history'):
    for sid in subjects[:3]:history+=self.world.store.events(sid,limit=3,hours=6)
  memories=[]
  # Both independent flags must permit learned influence. Even scheduler scans use this gate.
  if self.memory and self.source_allowed('learned_memory'):
   memories=self.memory.store.retrieve(str(subject or ' '.join(subjects)),entities=subjects[:8],limit=4,scope_filter=self.in_scope)
  threads=[{'id':r['id'],**r['data']} for r in self.store.rows('watches',('ACTIVE',),30) if r['data'].get('subject') in subjects][:4]
  relevant=[]
  tokens=set(' '.join(map(str,subjects)).replace('.',' ').replace('_',' ').lower().split())
  for msg in self.conversations()[-40:] if self.source_allowed('conversation') else []:
   content=str(msg.get('content',''))
   if tokens and tokens & set(content.lower().split()):relevant.append({'role':msg.get('role'),'content':content[:600]})
  quiet_hour=datetime.fromtimestamp(self.store.clock(),ZoneInfo(self.config.timezone)).hour;quiet=(self.config.quiet_start<=quiet_hour<self.config.quiet_end) if self.config.quiet_start<self.config.quiet_end else (quiet_hour>=self.config.quiet_start or quiet_hour<self.config.quiet_end)
  facts=[f for f in facts if self.in_scope(f)];history=[h for h in history if self.in_scope(h)]
  for fact in facts:fact['stale']=self.store.clock()-epoch(fact.get('observed_at'))>60
  fresh=healthy and bool(facts) and not any(f.get('stale') or f.get('availability') in ('unknown','unavailable') or f.get('value')=='UNKNOWN' for f in facts)
  extra={}
  if self.situations:
   from proactivity_v2.attention import applicable
   if self.home:
    for f in facts:
     sid=f.get('subject',{}).get('id');entity=self.home.entities.get(sid,{})
     if entity.get('area_id'):f.setdefault('subject',{})['area_id']=entity['area_id']
   memories=[m for m in memories if applicable(m,{'now':self.store.clock(),'world':facts},self.config.timezone)]
   actions=[{'id':r['id'],**r['data']} for r in self.store.rows('executions',None,1000) if self.store.clock()-r['created']<3600 and self.in_scope(r['data']) and set(r['data'].get('receipt',{}).get('targets',[])).intersection(subjects)][-8:] if self.source_allowed('actions') else []
   programs=[p for p in getattr(self.home,'programs',{}).values() if set(p.get('related_entities',[])).intersection(subjects) and self.source_allowed('automations' if p['domain']=='automation' else 'programs') and self.in_scope(p)][:4] if self.home else []
   incidents=[{'id':r['id'],**r['data']} for r in self.store.rows('incidents',('OPEN','NOTIFIED'),1000) if r['data'].get('schema_version')==2 and set(r['data'].get('entities',[])).intersection(subjects) and self.in_scope(r['data'])][-4:]
   extra={'actions':actions,'programs':programs,'incidents':incidents}
   if self.source_allowed('conversation') and not relevant:relevant=[{'role':m.get('role'),'content':str(m.get('content',''))[:600]} for m in self.conversations()[-6:]]
   relevant=[dict(m,context_ref='conversation:'+digest([m.get('role'),m.get('content')])) for m in relevant]
  return {**extra,'world':facts[:16],'history':history[:8],'memory':memories,'threads':threads,'conversation':relevant[-6:],'health':{'ha_world_connected':healthy},'healthy':healthy,'fresh':fresh,'quiet':quiet,'household':({'primary_person':self.home.household.get('primary_person'),'occupants':{self.home.household.get('primary_area'):self.home.household.get('occupants',{}).get(self.home.household.get('primary_area'),[])}} if self.home and self.source_allowed('household') else {}),'now':self.store.clock(),'input_policy_revision':self.policy_revision(),'memory_reads_allowed':self.source_allowed('learned_memory')}
 async def close_episode(self,identity):
  row=self.store.get('episodes',identity)
  if row and not self.in_scope(row['data']):
   self.store.change('episodes',identity,state='CANCELLED_SCOPE');return {'state':'DONE','reason':'Outside primary room scope'}
  if not row or row['state']!='OPEN':return {'state':'DONE','reason':'Already closed'}
  if not self.config.ha_attention_enabled:self.store.change('episodes',identity,state='CANCELLED');return {'state':'CANCELLED'}
  if self.store.clock()-row['due']>120:self.store.change('episodes',identity,state='STALE');return {'state':'STALE'}
  if not self.store.claim('episodes',identity,states=('OPEN',)):return {'state':'DONE'}
  try:
   # Provenance may be enriched after the original transition, before the episode closes.
   if self.world:
    for i,e in enumerate(row['data']['events']):
     newest=self.world.store.db.execute('select data from events where id=?',(e['event_id'],)).fetchone()
     if newest:
      from .episodes import compact
      updated=json.loads(newest[0]);row['data']['events'][i]={k:compact(v) for k,v in updated.items() if k in ('event_id','event_type','occurred_at','observed_at','subject','change','source','provenance','correlation')}
   row['data']=self.episodes.filter_closed(row['data'])
   if not row['data']['events']:
    self.store.change('episodes',identity,state='EXCLUDED_AUTOMATION',data=row['data']);return {'state':'DONE','classification':'EXCLUDED_AUTOMATION'}
   if all(self.execution.explained(e) for e in row['data']['events']):
    self.store.change('episodes',identity,state='EXPLAINED_EXECUTION');self.store.audit('episode_zero_model_execution',{'episode_id':identity});return {'state':'DONE','classification':'EXPLAINED_EXECUTION'}
   context=await self.context(events=row['data']['events']);revision=self.policy_revision();packet=self.episodes.packet(row,context)
   situation=self.situations.find(row['data']['events']) if self.situations else None
   if situation:
    packet['ONGOING SITUATION']=situation['data'];packet['RECENT CONVERSATION']=context.get('conversation',[]);packet['SUNDAY ACTION OUTCOMES']=context.get('actions',[]);packet['ELIGIBLE PROGRAM CONTEXT']=context.get('programs',[])
    if situation['data'].get('notification_state')=='DELIVERED':
     self.store.change('episodes',identity,state='DONE');self.store.audit('v2_silent_incident_update',{'episode_id':identity,'incident_id':situation['id'],'reason':'Ongoing already-notified situation; no transition-by-transition messages.'});return {'state':'DONE','classification':'SILENT'}
   raw,usage=await self.models.call('attention',packet,identity)
   if self.situations:
    from proactivity_v2.attention import assess
    recent=self.store.db.execute("select count(*) from deliveries where state='DELIVERED' and updated>?",(self.store.clock()-3600,)).fetchone()[0]
    judgment=assess(raw,context,situation['data'] if situation else {},recent)
    decision={'decision':'NOTEWORTHY' if judgment['outcome'] in {'TELL','URGENT'} else 'NORMAL','reason':judgment['reason'],'v2_assessment':judgment}
    if situation:self.situations.assess(situation['id'],judgment)
   else:decision=attention_parse(raw)
   if revision!=self.policy_revision() or not self.in_scope(row['data']):
    self.store.change('episodes',identity,state='CANCELLED_SCOPE');return {'state':'DONE','reason':'Input selection changed'}
   aid=self.store.put('attention',{'episode_id':identity,'packet':packet,'raw_response':raw,**decision,'usage_ref':usage},dedupe=identity,state=decision['decision'])
   if decision['decision']=='NOTEWORTHY':
    events=row['data']['events'];subjects=list(dict.fromkeys(e['subject']['id'] for e in events));self.store.wake('HA_NOTEWORTHY','SUNDAY_SELF',subjects,[aid],self.incident(events),entities=[s for s in subjects if not s.startswith('person:')],reason=decision['reason']);self.worker_event.set()
   self.store.change('episodes',identity,state='DONE');return {'state':'DONE','attention_id':aid}
  except asyncio.CancelledError:raise
  except Exception as error:self.store.change('episodes',identity,state='ERROR',data=row['data']|{'error_type':type(error).__name__});self.store.audit('attention_failed_closed',{'episode_id':identity,'error_type':type(error).__name__});return {'state':'ERROR'}
 async def temporal(self,row):
  if not self.in_scope(row['data']):return {'state':'CANCELLED_SCOPE'}
  p=row['data'];kind=p['kind']
  if kind=='TELEGRAM_DELIVERY_RETRY':
   if self.delivery:await self.delivery.process(p['delivery_id'])
   return {'state':'DONE'}
  if kind=='EPISODE_CLOSE':return await self.close_episode(p['episode_id'])
  if kind=='EXPECTATION_BOUNDARY':result=await self.expectations.due(row,self.recheck)
  elif kind=='EXECUTION_CHECK':result=self.execution.due(p['execution_id'])
  elif kind=='HEALTH_CHECK':result=self.health.due(row)
  elif kind=='WATCH_TIMEOUT':result=self.watches.timeout(row)
  elif kind=='EXPECTATION_SCAN':
   self.scan_expectations();self.scheduler.add(self.store.clock()+86400,'EXPECTATION_SCAN','SYSTEM',{},['expectation-scan',int(self.store.clock()//86400)+1]);return {'state':'DONE'}
  else:
   authority=p['authority'];refs=p.get('evidence_refs') or [row['id']]
   if kind=='COMMITMENT_DUE' and (authority!='USER_EXPLICIT' or not p.get('user_request_id')):return {'state':'ERROR','reason':'No user commitment authority'}
   if kind=='SELF_RECHECK' and authority!='SUNDAY_SELF':return {'state':'ERROR','reason':'Self timer authority cannot be upgraded'}
   wid=self.store.wake(kind,authority,p.get('subject','commitment'),refs,'temporal:'+row['id'],commitment_refs=[row['id']] if kind=='COMMITMENT_DUE' else [],commitment=p.get('commitment'),depth=p.get('depth',0));result={'state':'DONE','wake_id':wid}
   if kind=='COMMITMENT_DUE':self.complete_commitment(self.store.get('wakes',wid),row)
  self.worker_event.set();return result
 def complete_commitment(self,wake,source=None):
  data=wake['data']
  if not self.in_scope(data):self.store.change('wakes',wake['id'],state='CANCELLED_SCOPE');return
  if source is None:
   refs=data.get('commitment_refs',[]);source=self.store.get('temporal',refs[0]) if refs else None
  if not source or source['data'].get('authority')!='USER_EXPLICIT' or not source['data'].get('user_request_id') or data.get('authority')!='USER_EXPLICIT':
   self.store.change('wakes',wake['id'],state='ERROR');return
  wording=(source['data'].get('commitment') or {}).get('message','')
  if not wording:self.store.change('wakes',wake['id'],state='ERROR');return
  proposal={'operation':'TELL_USER','message':wording,'destination':'telegram','reason':'Explicit user commitment became due.','confidence':1.0,'already_known':False,'consequence_of_silence':'The requested reminder would not be delivered.','time_sensitive':True,'useful_now':True,'can_resolve_silently':False,'interruption_cost':'low'}
  self.refresh_controls();decision=self.store.put('decisions',{'wake_id':wake['id'],'envelope':data,'deterministic_commitment':True,'proposal':proposal},dedupe=wake['id'],state='VALIDATED')
  gate=self.gates.evaluate(data,proposal,{'fresh':True,'healthy':True,'quiet':False},decision_ref=decision);self.queue_gate(gate);self.store.change('wakes',wake['id'],state='DONE')
 def commitment(self,at,subject,message,user_request_id,evidence):
  if not user_request_id or not evidence:raise ValueError('Explicit user evidence required')
  return self.scheduler.add(at,'COMMITMENT_DUE','USER_EXPLICIT',{'subject':subject,'commitment':{'message':str(message)[:1200]},'user_request_id':user_request_id,'evidence_refs':evidence},['commitment',user_request_id,subject,at])
 def self_recheck(self,at,subject,evidence,depth=0):
  if not evidence or depth>2 or at<=self.store.clock() or at>self.store.clock()+604800:raise ValueError('Invalid bounded self-recheck')
  return self.scheduler.add(at,'SELF_RECHECK','SUNDAY_SELF',{'subject':subject,'evidence_refs':evidence,'depth':depth},['self-recheck',subject,evidence,at])
 def external(self,watch_id,event_id,value,changed=True):
  watch=self.store.get('watches',watch_id)
  if not watch or watch['state']!='ACTIVE' or not changed or self.store.seen('external:'+event_id):return []
  return self.watches.event(watch['data']['subject'],watch['data']['trigger_conditions']['property'],value,event_id)
 async def investigate(self,query):
  if query.get('operation')!='world' and not self.source_allowed('history'):raise ValueError('Historical input is disabled')
  if not self.in_scope(query):raise ValueError('Outside primary room scope')
  op=query.get('operation');target=query.get('target')
  if not isinstance(target,str) or len(target)>160:raise ValueError('Invalid investigation target')
  if op=='world' and self.world:return await self.world.fresh_entities([target])
  if self.history and op in {'history','presence_history','duration','evidence','action_receipts'}:
   return self.history.projection(await self.history.query(op,target,query.get('window','last 6 hours')))
  raise ValueError('Read-only investigation provider unavailable')
 async def apply(self,wake_row,output,context,usage_ref=None):
  if not self.in_scope(wake_row['data']) or context.get('input_policy_revision')!=self.policy_revision():return [{'suppressed':'Input selection changed'}]
  if self.situations:
   incident=self.store.get('incidents',wake_row['data']['incident_id'])
   if incident and incident['data'].get('schema_version')==2:
    context=dict(context);context['v2_incident']=incident['data']
  outcomes=validate_output(output);wake=wake_row['data'];decision_id=self.store.put('decisions',{'wake_id':wake_row['id'],'envelope':wake,'output':output,'usage_ref':usage_ref},dedupe=wake_row['id'],state='VALIDATED')
  previous=self.store.meta('applied:'+decision_id,False)
  if previous:return []
  results=[]
  self.refresh_controls()
  # All effect proposals are gated; only approved communication reaches the trusted adapter.
  for i,p in enumerate(outcomes):
   if not self.in_scope(p):results.append({'suppressed':'Outside primary room scope'});continue
   op=p['operation']
   if op in {'ACT','TELL_USER','ASK_USER'}:
    if op=='ACT' and self.home:
     try:
      import home_tools
      await self.home.refresh()
      await home_tools.prepare_action(self.home,p['action']) # Generic validation and reads only; never dispatch.
      self.action_validation[digest(p['action'])]=self.store.clock()
     except Exception:pass
    gate=self.gates.evaluate(wake,p,context,decision_ref=decision_id);results.append(gate);self.queue_gate(gate)
   elif op=='REMEMBER' and self.memory and self.config.memory_write_enabled:
    ref=self.memory.store.evidence('proactive_conclusion','SUNDAY_INFERENCE',{'decision_ref':decision_id,'incident_id':wake['incident_id'],'evidence_refs':wake['evidence_refs'],'reason':p['reason']},'proactivity:'+decision_id+':'+str(i));candidate=dict(p['candidate']);candidate['confidence']=min(.8,float(candidate.get('confidence',p.get('confidence',.5))));cid=self.memory.submit_inference(candidate,'SUNDAY_INFERENCE',[ref],['proactivity',decision_id,i]);results.append({'inbox_candidate':cid})
   elif op=='WATCH' and self.config.self_watch_enabled:
    watch_subject=p.get('subject',wake['subject'])
    if isinstance(watch_subject,list):
     if len(watch_subject)!=1:raise ValueError('WATCH target ambiguous')
     watch_subject=watch_subject[0]
    wid=self.watches.create(str(watch_subject),p['condition'],p['reason'],p['expires_at'],authority='SUNDAY_SELF',evidence=[decision_id],depth=int(wake.get('depth',0))+1);results.append({'watch_id':wid})
   elif op=='INVESTIGATE':results.append({'investigation':await self.investigate(p['query'])})
   elif op=='IGNORE':results.append({'ignored':True})
  self.store.set_meta('applied:'+decision_id,True);self.store.audit('cognition_outcomes',{'decision_id':decision_id,'wake_id':wake_row['id'],'results':results},decision_id);return results
 async def cognize(self,row):
  if not self.in_scope(row['data']):self.store.change('wakes',row['id'],state='CANCELLED_SCOPE');return
  if row['data']['wake_type']=='COMMITMENT_DUE':self.complete_commitment(row);return
  if not self.config.proactive_cognition_enabled:self.store.change('wakes',row['id'],state='DISABLED');return
  if not self.store.claim('wakes',row['id']):return
  try:
   context=await self.context(subject=row['data']['subject'],wake=row['data']);packet={'wake':row['data'],'context':context}
   if self.situations and row['data'].get('authority')!='USER_EXPLICIT' and row['data']['wake_type']!='HA_NOTEWORTHY':
    incident=self.store.get('incidents',row['data']['incident_id'])
    d={'schema_version':2,'first_event_at':incident['created'],'latest_event_at':self.store.clock(),'entities':row['data'].get('entities',[]),'areas':row['data'].get('areas',[]),'event_refs':row['data']['evidence_refs'],'transitions':[],'current_state':{},'notification_state':'NONE','assessments':[],**incident['data']}
    self.store.change('incidents',incident['id'],data=d)
    from proactivity_v2.attention import assess
    recent=self.store.db.execute("select count(*) from deliveries where state='DELIVERED' and updated>?",(self.store.clock()-3600,)).fetchone()[0]
    raw,usage=await self.models.call('attention',packet,row['id']+':attention')
    judgment=assess(raw,context,d,recent);self.situations.assess(incident['id'],judgment)
    self.store.put('attention',{'wake_id':row['id'],'packet':packet,'raw_response':raw,'v2_assessment':judgment,'usage_ref':usage},dedupe=[row['id'],'v2-wake'],state=judgment['outcome'])
    if judgment['outcome'] not in {'TELL','URGENT'}:
     self.store.change('wakes',row['id'],state='DONE');return
   raw,usage=await self.models.call('cognition',packet,row['id']);output=json.loads(raw);results=await self.apply(row,output,context,usage)
   # At most one bounded factual investigation follow-up; no recursive model heartbeat.
   findings=[r['investigation'] for r in results if 'investigation' in r]
   if findings and context.get('input_policy_revision')==self.policy_revision() and self.in_scope(row['data']):
    packet['investigation_results']=findings[:1];raw,usage=await self.models.call('cognition',packet,row['id']+':investigation');follow=json.loads(raw)
    if any(p['operation']=='INVESTIGATE' for p in validate_output(follow)):raise ValueError('Recursive investigation not allowed')
    followrow=dict(row);followrow['id']=row['id']+':followup';await self.apply(followrow,follow,context,usage)
   self.store.change('wakes',row['id'],state='DONE')
  except asyncio.CancelledError:raise
  except Exception as error:self.store.change('wakes',row['id'],state='ERROR',data=row['data']|{'error_type':type(error).__name__});self.store.audit('cognition_failed_closed',{'wake_id':row['id'],'error_type':type(error).__name__})
 async def worker(self):
  while True:
   self.worker_event.clear()
   try:
    self.store.recover()
    for row in self.store.rows('wakes',('PENDING',),10):await self.cognize(row)
    if self.store.rows('wakes',('PENDING',),1):continue
   except asyncio.CancelledError:raise
   except Exception as error:self.last_error=type(error).__name__
   try:await asyncio.wait_for(self.worker_event.wait(),timeout=60)
   except asyncio.TimeoutError:pass # deterministic lease recovery; never a model heartbeat
 async def health_loop(self):
  while True:
   try:self.health.update('ha_event_stream',bool(self.world and self.world.connected),'World event stream disconnected' if self.world and not self.world.connected else '')
   except Exception as error:self.last_error=type(error).__name__
   await asyncio.sleep(30)
 async def start(self):
  self.episodes.exclude_automations=not self.source_allowed('automations')
  self.enforce_scope()
  # Repair interrupted multi-step inserts with stable dedupe keys; never replay actions/model calls.
  for row in self.store.rows('episodes',('OPEN',),1000):
   if self.store.clock()-row['due']>120:self.store.change('episodes',row['id'],state='STALE');continue
   self.scheduler.add(row['due'],'EPISODE_CLOSE','SYSTEM',{'episode_id':row['id']},['episode-close',row['id']])
  for row in self.store.rows('executions',('PENDING',),1000):
   d=row['data']
   if self.store.clock()-d['deadline']>86400:self.store.change('executions',row['id'],state='STALE_UNKNOWN');continue
   key='execution-return-wait' if d.get('return_wait_extended') else 'execution-check';self.scheduler.add(d['deadline'],'EXECUTION_CHECK','SYSTEM',{'execution_id':row['id']},[key,row['id']])
  for row in self.store.rows('watches',('ACTIVE',),1000):self.scheduler.add(row['data']['expires_at'],'WATCH_TIMEOUT',row['data']['authority'],{'watch_id':row['id']},['watch-timeout',row['id']])
  if self.delivery:self.delivery.start()
  self.scan_expectations();self.scheduler.add(self.store.clock()+86400,'EXPECTATION_SCAN','SYSTEM',{},['expectation-scan',int(self.store.clock()//86400)+1]);self.tasks=[asyncio.create_task(self.scheduler.loop(self.temporal),name='proactive-scheduler'),asyncio.create_task(self.worker(),name='proactive-cognition'),asyncio.create_task(self.health_loop(),name='proactive-health')]
 async def close(self):
  if self.world and self.on_event in self.world.bus.consumers:self.world.bus.consumers.remove(self.on_event)
  for task in self.tasks:task.cancel()
  await asyncio.gather(*self.tasks,return_exceptions=True)
  if self.delivery:await self.delivery.close()
  self.store.close()
