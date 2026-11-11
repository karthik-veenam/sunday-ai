"""Versioned factual events/current facts. Deterministic, passive, no models/actions."""
import asyncio
from dataclasses import dataclass,asdict,field
from datetime import datetime,timezone,timedelta
import hashlib,json,math,re,sqlite3,time,uuid,os
from pathlib import Path
from home_presentation import ordinary,friendly,ENVIRONMENT
from home_model import norm

SCHEMA_VERSION=1
VOCABULARY={'USER','SUNDAY','HA_AUTOMATION','HA_SCRIPT','HA_SCENE','PHYSICAL_OR_DEVICE','SENSOR','EXTERNAL','UNKNOWN'}
PROPERTIES={
 'light':('brightness','color_mode','color_temp_kelvin','hs_color'),
 'fan':('percentage','preset_mode','oscillating','direction'),
 'climate':('temperature','current_temperature','hvac_action','fan_mode','preset_mode','swing_mode'),
 'cover':('current_position',), 'media_player':('volume_level','is_volume_muted','source'),
 'humidifier':('humidity','mode'), 'water_heater':('temperature','operation_mode')}
ACTUATORS={'light','switch','fan','climate','cover','media_player','humidifier','water_heater','vacuum','lock'}
UNAVAILABLE={'unknown','unavailable','UNKNOWN',None}
THRESHOLDS={'temperature':.1,'humidity':1.,'pressure':.5,'atmospheric_pressure':.5,'illuminance':5.,'power':5.,'energy':.01}

def utc():return datetime.now(timezone.utc).isoformat()
def epoch(value):
 try:return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()
 except (ValueError,AttributeError,TypeError):return 0.
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,default=str).encode()).hexdigest()
def bounded(value):
 if isinstance(value,dict):return {str(k)[:80]:bounded(v) for k,v in value.items() if not re.search(r'(?i)latitude|longitude|gps|password|token|secret|authorization|api_key',str(k))}
 if isinstance(value,list):return [bounded(v) for v in value[:40]]
 if isinstance(value,str):
  if re.search(r'(?i)Bearer |https?://|api.telegram.org',value):return '[redacted]'
  return value[:240]
 if isinstance(value,float) and not math.isfinite(value):return None
 return value

@dataclass(frozen=True)
class Provenance:
 initiator: dict
 via: dict
 attribution: str
 evidence: list
 reason: str
 physical_success_confirmed: bool=False

@dataclass(frozen=True)
class NormalizedEvent:
 event_id:str
 event_type:str
 occurred_at:str
 observed_at:str
 subject:dict
 change:dict
 source:dict
 provenance:dict
 correlation:dict
 schema_version:int=1

@dataclass(frozen=True)
class WorldFact:
 subject:dict
 property:str
 value:object
 unit:str|None
 observed_at:str
 changed_at:str
 source_observed_at:str
 availability:str
 provenance:dict
 last_event_id:str|None=None
 event_value:object=None
 reconciliation:bool=False
 schema_version:int=1
 source:dict=field(default_factory=lambda:{"class":"home_assistant"})

class EventBus:
 """Only synchronous factual consumers; callbacks must never initiate actions."""
 def __init__(self,store):self.store=store;self.consumers=[]
 def subscribe(self,consumer):self.consumers.append(consumer)
 def publish(self,event):
  created=self.store.insert_event(event)
  if created:
   for consume in self.consumers:consume(event)
  return created

class WorldStore:
 def __init__(self,path,retention_days=14,diagnostic_days=2,max_events=20000):
  Path(path).parent.mkdir(parents=True,exist_ok=True)
  self.db=sqlite3.connect(path);os.chmod(path,0o600);self.db.execute('pragma journal_mode=WAL');self.db.execute('pragma synchronous=FULL')
  version=self.db.execute('pragma user_version').fetchone()[0]
  if version not in (0,SCHEMA_VERSION):self.db.close();raise RuntimeError('Unsupported world schema; back up before migration')
  self.db.executescript('''CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,type TEXT,subject TEXT,occurred REAL,observed REAL,data TEXT);
   CREATE INDEX IF NOT EXISTS events_subject ON events(subject,occurred);
   CREATE TABLE IF NOT EXISTS facts(subject TEXT,property TEXT,data TEXT,PRIMARY KEY(subject,property));
   CREATE TABLE IF NOT EXISTS contexts(id TEXT PRIMARY KEY,parent TEXT,user TEXT,observed REAL,evidence TEXT);
   CREATE TABLE IF NOT EXISTS actions(id TEXT PRIMARY KEY,request TEXT,observed REAL,data TEXT);
   CREATE TABLE IF NOT EXISTS raw_seen(id TEXT PRIMARY KEY,observed REAL);
   CREATE TABLE IF NOT EXISTS metrics(name TEXT PRIMARY KEY,value REAL);
   CREATE TABLE IF NOT EXISTS projections(id TEXT PRIMARY KEY,observed REAL,data TEXT);
   PRAGMA user_version=1;''')
  self.retention_days=retention_days;self.diagnostic_days=diagnostic_days;self.max_events=max_events
 def count(self,name,value=1):self.db.execute('insert into metrics values(?,?) on conflict(name) do update set value=value+excluded.value',(name,value))
 def commit(self):self.db.commit()
 def insert_event(self,event):
  data=bounded(asdict(event));cur=self.db.execute('insert or ignore into events values(?,?,?,?,?,?)',(event.event_id,event.event_type,event.subject['id'],epoch(event.occurred_at),time.time(),json.dumps(data)))
  if cur.rowcount:
   self.count('normalized_events_created');self.count('provenance_'+event.provenance['attribution'].lower())
  else:self.count('events_deduped')
  return bool(cur.rowcount)
 def get(self,subject,prop='state'):
  row=self.db.execute('select data from facts where subject=? and property=?',(subject,prop)).fetchone();return json.loads(row[0]) if row else None
 def put(self,fact):
  self.db.execute('insert or replace into facts values(?,?,?)',(fact.subject['id'],fact.property,json.dumps(bounded(asdict(fact)))));self.count('world_model_updates')
 def events(self,subject=None,limit=10,hours=24,event_type=None):
  limit=max(1,min(40,int(limit)));hours=max(.01,min(336,float(hours)));where=['occurred>=?'];args=[time.time()-hours*3600]
  if subject:where.append('subject=?');args.append(subject)
  if event_type:where.append('type=?');args.append(event_type)
  rows=self.db.execute('select data from events where '+' and '.join(where)+' order by occurred desc,id desc limit ?',(*args,limit));return [json.loads(r[0]) for r in rows]
 def current(self,subject,limit=20):
  return [json.loads(r[0]) for r in self.db.execute('select data from facts where subject=? order by property limit ?',(subject,min(30,max(1,limit))))]
 def prune(self):
  self.db.execute('delete from events where observed<?',(time.time()-self.retention_days*86400,))
  self.db.execute('delete from events where id in (select id from events order by observed desc limit -1 offset ?)',(self.max_events,))
  for table in ('contexts','actions','projections','raw_seen'):self.db.execute('delete from '+table+' where observed<?',(time.time()-self.diagnostic_days*86400,))
  self.db.execute('delete from projections where id in (select id from projections order by observed desc limit -1 offset 2000)')
  self.db.execute('delete from raw_seen where id in (select id from raw_seen order by observed desc limit -1 offset 100000)')
  self.commit()
 def metrics(self):
  names=('raw_events_seen','normalized_events_created','events_filtered_as_noise','events_deduped','world_model_updates','world_model_reconciliations','provenance_confirmed','provenance_strong','provenance_possible','provenance_unknown','context_projection_requests','context_projection_fact_count','context_projection_size','fast_mode_world_reads','restart_reconciliation_count','event_processing_latency_ms_total','event_processing_count')
  result=dict.fromkeys(names,0);result.update(dict(self.db.execute('select name,value from metrics')))
  n=result['event_processing_count'];result['mean_event_processing_latency_ms']=result['event_processing_latency_ms_total']/n if n else None
  return result
 def close(self):self.commit();self.db.close()

class WorldModel:
 def __init__(self,path,home,presence,config):
  self.home=home;self.ha=home.ha;self.presence=presence;self.config=config
  self.store=WorldStore(path,config.world_event_retention_days,config.world_diagnostic_retention_days,config.world_max_events)
  self.bus=EventBus(self.store);self.bus.subscribe(self._update_from_event)
  self.active=set();self.connected=False;self.session_ready=False;self.last_sync=0.;self.last_error='';self._closed=False
  self.ingestion_task=None;self.projection_budget=5000;self.reconnect_cutoff=0.
  self.last_projection={}
 def selected(self):
  from autonomy_scope import enabled
  policy=getattr(self.home,'input_policy',None)
  areas={self.home.household['primary_area']} if enabled(self.config) else {self.home.household['primary_area'],*self.home.household.get('common_areas',[])}
  assistants={e.get('device_id') for e in self.home.entities.values() if e['domain']=='assist_satellite'}-{None}
  result=[]
  for e in self.home.entities.values():
   if policy:
    if e['entity_id'] not in self.active and e['entity_id'] not in getattr(self,'pattern_dependencies',set()) and not any(policy.entity(p,e['entity_id']) for p in ('memory','proactivity')):continue
    if e['domain'] not in ('automation','script','scene'):result.append(e['entity_id'])
    continue
   if e.get('area_id') not in areas and e['entity_id'] not in self.active:continue
   if not ordinary(e) or e.get('device_id') in assistants:continue
   if self.home.devices.get(e.get('device_id'),{}).get('entry_type')=='service':continue
   domain=e['domain'];attrs=e.get('attributes',{})
   if domain in ACTUATORS or domain=='sensor' and attrs.get('device_class') in ENVIRONMENT|{'power','energy'} or domain=='binary_sensor':result.append(e['entity_id'])
  return set(sorted(result,key=lambda eid:(self.home.entities[eid].get('area_id')!=self.home.household['primary_area'],eid))[:1500 if policy else 250])
 def subject(self,eid):
  e=self.home.entities.get(eid,{});aid=e.get('area_id');area=self.home.household.get('area_names',{}).get(aid,[])
  return {'kind':'entity','id':eid,'semantic_name':friendly(e,self.home) if e else eid,'area':area[0] if area else self.home.areas.get(aid,{}).get('name'), 'home_model_entity_id':eid}
 def unknown(self,reason='No authoritative causal linkage',evidence=None,initiator='UNKNOWN'):
  return asdict(Provenance({'type':initiator},{},'UNKNOWN',evidence or [],reason))
 def context(self,ctx,evidence=None):
  if not isinstance(ctx,dict) or not ctx.get('id'):return
  old=self.store.db.execute('select evidence from contexts where id=?',(ctx['id'],)).fetchone()
  refs=json.loads(old[0]) if old else []
  if evidence and evidence not in refs:refs.append(evidence)
  self.store.db.execute('insert or replace into contexts values(?,?,?,?,?)',(ctx['id'],ctx.get('parent_id'),ctx.get('user_id'),time.time(),json.dumps(bounded(refs[-20:]))))
 def attribute(self,ctx,eid):
  chain=[];current=ctx.get('id') if isinstance(ctx,dict) else None;seen=set();user=None
  for _ in range(12):
   if not current or current in seen:break
   seen.add(current);row=self.store.db.execute('select parent,user,evidence from contexts where id=?',(current,)).fetchone()
   if not row:break
   parent,uid,encoded=row;user=user or uid;refs=json.loads(encoded);chain.append({'context_id':current,'parent_id':parent})
   for ref in refs:
    if ref.get('kind')=='sunday_service_result' and eid in ref.get('targets',[]):
     return asdict(Provenance(ref['initiator'],{'type':'SUNDAY','path':ref['path']},'CONFIRMED',[ref,*chain],'HA REST response authoritatively links this transition context to the correlated Sunday service request'))
   current=parent
  # An automation/script context plus its target-specific service event is causal evidence.
  executions=[];services=[]
  for link in chain:
   row=self.store.db.execute('select evidence from contexts where id=?',(link['context_id'],)).fetchone()
   for ref in json.loads(row[0]) if row else []:
    if ref.get('kind') in ('automation_triggered','script_started','scene_activated'):executions.append(ref)
    if ref.get('kind')=='call_service' and eid in ref.get('targets',[]):services.append(ref)
  if executions and services:
   execution=executions[0];kind={'automation_triggered':'HA_AUTOMATION','script_started':'HA_SCRIPT','scene_activated':'HA_SCENE'}[execution['kind']]
   return asdict(Provenance({'type':kind,'id':execution.get('entity_id')},{'type':kind},'CONFIRMED',[execution,*services[:2],*chain],'Execution, target-specific service and state share an authoritative HA context chain'))
  if executions:
   execution=executions[0];kind={'automation_triggered':'HA_AUTOMATION','script_started':'HA_SCRIPT','scene_activated':'HA_SCENE'}[execution['kind']]
   return asdict(Provenance({'type':kind,'id':execution.get('entity_id')},{'type':kind},'STRONG',[execution,*chain],'Execution and state contexts link, but target-specific service evidence is missing'))
  if user:return asdict(Provenance({'type':'USER','id':user},{'type':'EXTERNAL'},'STRONG',chain,'HA supplied a user context; identity/device and physical outcome are not inferred'))
  return self.unknown(evidence=chain)
 def event(self,typ,subject,prop,old,new,occurred,ctx=None,provenance=None,request=None,extra=None,identity=None):
  ctx=ctx or {};provenance=provenance or self.attribute(ctx,subject['id'])
  if not request:request=next((r.get('request_id') for r in provenance.get('evidence',[]) if r.get('request_id')),None)
  correlation={'request_id':request,'ha_context_id':ctx.get('id'),'parent_context_id':ctx.get('parent_id'),'automation_id':None,'script_id':None}
  origin=provenance.get('initiator',{})
  if origin.get('type')=='HA_AUTOMATION':correlation['automation_id']=origin.get('id')
  if origin.get('type')=='HA_SCRIPT':correlation['script_id']=origin.get('id')
  if extra:correlation.update(extra)
  key=identity or digest([SCHEMA_VERSION,typ,subject['id'],prop,old,new,occurred,ctx.get('id'),request])
  return NormalizedEvent(key,typ,occurred or utc(),utc(),subject,{'property':prop,'old':old,'new':new},{'class':'home_assistant' if not typ.startswith('SUNDAY_') else 'sunday'},provenance,correlation)
 def _update_from_event(self,event):
  # Current facts are written by their provider with live freshness metadata.
  # This consumer enriches the newest matching fact with its semantic transition.
  fact=self.store.get(event.subject['id'],event.change['property'])
  if fact and fact['value']==event.change['new'] and epoch(event.occurred_at)>=epoch(fact['changed_at']):
   fact.update(last_event_id=event.event_id,provenance=event.provenance)
   self.store.put(WorldFact(**fact))
 def values(self,eid,state):
  domain=eid.split('.')[0];attrs=state.get('attributes',{});value=state.get('state','unknown');available='unknown' if value=='unknown' else 'unavailable' if value=='unavailable' else 'available'
  values={'state':(value,attrs.get('unit_of_measurement')),'availability':(available,None)}
  for prop in PROPERTIES.get(domain,()):
   if prop in attrs:values[prop]=(bounded(attrs[prop]),attrs.get('temperature_unit','°C') if prop in ('temperature','current_temperature') else None)
  if domain=='sensor' and value not in UNAVAILABLE:
   try:
    number=float(value)
    if math.isfinite(number):values['state']=(number,attrs.get('unit_of_measurement'))
   except (ValueError,TypeError):pass
  return values,available
 def ingest_state(self,state,occurred=None,reconcile=False,live=False,old_state=None):
  start=time.perf_counter();eid=state.get('entity_id','');domain=eid.split('.')[0]
  if eid not in self.selected() and domain not in ('automation','script','scene'):return
  ctx=state.get('context') or {};self.context(ctx)
  if domain in ('automation','script','scene'):
   if domain=='scene' and live:
    self.context(ctx,{'kind':'scene_activated','entity_id':eid,'context_id':ctx.get('id'),'occurred_at':state.get('last_updated') or occurred or utc()})
    self.bus.publish(self.event('SCENE_ACTIVATED',self.subject(eid),'last_activation',None,state.get('state'),state.get('last_updated') or occurred or utc(),ctx))
    self.enrich_context(ctx.get('id'))
   return
  values,availability=self.values(eid,state);raw_old=self.values(eid,old_state)[0] if old_state else {};stamp=state.get('last_updated') or occurred or utc();observed=utc()
  for prop,(new,unit) in values.items():
   old=self.store.get(eid,prop)
   if old and epoch(stamp)<epoch(old['source_observed_at']):
    if live and prop in raw_old and raw_old[prop][0]!=new:
     typ='AVAILABILITY_CHANGED' if prop=='availability' else 'SENSOR_VALUE_CHANGED' if domain=='sensor' and prop=='state' else 'DEVICE_STATE_CHANGED' if prop=='state' else 'DEVICE_PROPERTY_CHANGED'
     if prop=='availability' or not (new in UNAVAILABLE if not isinstance(new,(list,dict)) else False):
      self.bus.publish(self.event(typ,self.subject(eid),prop,raw_old[prop][0],new,stamp,ctx))
      self.store.count('out_of_order_events_retained')
    else:self.store.count('events_deduped')
    continue
   prior=raw_old[prop][0] if live and prop in raw_old else old['value'] if old else None
   changed=old is not None and prior!=new
   source=self.attribute(ctx,eid) if live else self.unknown('Snapshot/reconciliation is current evidence, not a reconstructed cause')
   if old and not changed:source=old['provenance']
   last=old.get('last_event_id') if old and not (reconcile and changed) else None
   anchor=old.get('event_value',old['value']) if old else new
   if reconcile and changed:anchor=new
   newfact=WorldFact(self.subject(eid),prop,new,unit,observed,(state.get('last_changed') or stamp) if (changed or not old) and prop=='state' else stamp if changed or not old else old['changed_at'],stamp,availability,source,last,anchor,reconcile)
   self.store.put(newfact)
   if not old or reconcile or not changed:continue
   if prop!='availability' and (new in UNAVAILABLE if not isinstance(new,(list,dict)) else False):continue
   if prop=='state' and old['value'] in UNAVAILABLE:continue
   typ='AVAILABILITY_CHANGED' if prop=='availability' else 'DEVICE_STATE_CHANGED' if prop=='state' else 'DEVICE_PROPERTY_CHANGED'
   oldevent=prior
   if domain=='sensor' and prop=='state':
    typ='SENSOR_VALUE_CHANGED';threshold=THRESHOLDS.get(state.get('attributes',{}).get('device_class'),0)
    if live and old_state and old['value']==new:anchor=prior
    if isinstance(anchor,(int,float)) and isinstance(new,(int,float)) and abs(new-anchor)<threshold:continue
    oldevent=anchor
   ev=self.event(typ,newfact.subject,prop,oldevent,new,stamp,ctx,source)
   self.bus.publish(ev)
   current=self.store.get(eid,prop);current['event_value']=new;self.store.put(WorldFact(**current))
  for prop in PROPERTIES.get(domain,()):
   if prop not in values:self.store.db.execute('delete from facts where subject=? and property=?',(eid,prop))
  self.store.count('event_processing_latency_ms_total',(time.perf_counter()-start)*1000);self.store.count('event_processing_count')
 def ingest_presence(self,snapshot,reconcile=False):
  subject={'kind':'person','id':'person:'+snapshot.person,'semantic_name':snapshot.person,'area':None,'home_model_entity_id':snapshot.ha_person}
  old=self.store.get(subject['id'],'presence');changed=old is not None and old['value']!=snapshot.presence
  prov=self.unknown('Shared Presence Provider semantic observation; no room occupancy inferred',initiator='SENSOR')
  fact=WorldFact(subject,'presence',snapshot.presence,None,snapshot.observed_at,snapshot.observed_at if changed or not old else old['changed_at'],snapshot.evidence_at or snapshot.observed_at,'unknown' if snapshot.presence=='UNKNOWN' else 'available',prov,old.get('last_event_id') if old and not (reconcile and changed) else None,snapshot.presence,reconcile)
  self.store.put(fact)
  if changed and not reconcile:self.bus.publish(self.event('PRESENCE_CHANGED',subject,'presence',old['value'],snapshot.presence,snapshot.observed_at,provenance=prov))
  self.store.commit()
 def raw_event(self,event):
  self.store.count('raw_events_seen');typ=event.get('event_type');data=event.get('data') or {};ctx=event.get('context') or {};stamp=event.get('time_fired') or utc()
  rawid=digest(event)
  if not self.store.db.execute('insert or ignore into raw_seen values(?,?)',(rawid,time.time())).rowcount:
   self.store.count('events_deduped');self.store.commit();return
  count=self.store.metrics().get('normalized_events_created',0)
  if typ=='state_changed':
   new=data.get('new_state')
   if new and epoch(stamp)>=self.reconnect_cutoff:
    old=data.get('old_state') or {}
    domain=new['entity_id'].split('.')[0]
    if domain=='scene':
     if old and old.get('state')!=new.get('state') and epoch(new.get('state')) and abs(epoch(new.get('state'))-epoch(stamp))<10:self.ingest_state(new,stamp,live=True)
    elif domain=='script':
     if new.get('state')=='on' and old.get('state')!='on':
      ref={'kind':'script_started','entity_id':new['entity_id'],'context_id':ctx.get('id'),'occurred_at':stamp}
      self.context(ctx,ref)
      self.bus.publish(self.event('SCRIPT_EXECUTED',self.subject(new['entity_id']),'execution',None,'started',stamp,ctx,self.unknown('HA script entered running state',[ref]),extra={'script_id':new['entity_id']}))
    else:self.ingest_state(new,stamp,live=True,old_state=old or None)
  elif typ in ('automation_triggered','script_started','call_service'):
   eid=data.get('entity_id');domain=data.get('domain');service=data.get('service');targets=(data.get('service_data') or {}).get('entity_id',[])
   if isinstance(targets,str):targets=[targets]
   ref={'kind':typ,'entity_id':eid,'domain':domain,'service':service,'targets':targets,'occurred_at':stamp,'context_id':ctx.get('id')}
   self.context(ctx,ref)
   if typ!='call_service' and eid:
    self.bus.publish(self.event('AUTOMATION_TRIGGERED' if typ=='automation_triggered' else 'SCRIPT_EXECUTED',self.subject(eid),'execution',None,'started',stamp,ctx,self.unknown('HA reports execution started; completion/physical outcome are not inferred',[ref]),extra={('automation_id' if typ=='automation_triggered' else 'script_id'):eid}))
   self.enrich_context(ctx.get('id'))
  if self.store.metrics().get('normalized_events_created',0)==count:self.store.count('events_filtered_as_noise')
  self.store.commit()
 def enrich_context(self,context_id):
  if not context_id:return
  # Re-evaluate recent state events after out-of-order authoritative evidence arrives.
  rows=self.store.db.execute('select id,data from events order by observed desc limit 500').fetchall()
  for eid,encoded in rows:
   event=json.loads(encoded)
   if event['event_type'] not in ('DEVICE_STATE_CHANGED','DEVICE_PROPERTY_CHANGED','SENSOR_VALUE_CHANGED','AVAILABILITY_CHANGED'):continue
   ctx={'id':event['correlation'].get('ha_context_id')}
   updated=self.attribute(ctx,event['subject']['id'])
   ranks={'UNKNOWN':0,'POSSIBLE':1,'STRONG':2,'CONFIRMED':3}
   if ranks[updated['attribution']]<=ranks[event['provenance']['attribution']]:continue
   event['provenance']=updated
   origin=updated.get('initiator',{})
   if origin.get('type')=='HA_AUTOMATION':event['correlation']['automation_id']=origin.get('id')
   if origin.get('type')=='HA_SCRIPT':event['correlation']['script_id']=origin.get('id')
   event['correlation']['request_id']=next((r.get('request_id') for r in updated.get('evidence',[]) if r.get('request_id')),event['correlation'].get('request_id'))
   self.store.db.execute('update events set data=? where id=?',(json.dumps(event),eid));self.store.count('provenance_'+updated['attribution'].lower())
   fact=self.store.get(event['subject']['id'],event['change']['property'])
   if fact and fact.get('last_event_id')==eid:fact['provenance']=updated;self.store.put(WorldFact(**fact))
 def action_requested(self,domain,service,data,output=None):
  aid=uuid.uuid4().hex;targets=data.get('entity_id',[]);targets=[targets] if isinstance(targets,str) else targets
  self.active.update(targets)
  record={'action_id':aid,'request_id':getattr(output,'request_id',None),'domain':domain,'service':service,'targets':targets,'intended_values':{k:v for k,v in data.items() if k!='entity_id'},'initiator':getattr(output,'initiator',{'type':'UNKNOWN'}),'path':getattr(output,'execution_path','unknown'),'status':'requested','physical_success_confirmed':False}
  self.store.db.execute('insert into actions values(?,?,?,?)',(aid,record['request_id'],time.time(),json.dumps(bounded(record))))
  prov=asdict(Provenance(record['initiator'],{'type':'SUNDAY','path':record['path']},'CONFIRMED',[{'action_id':aid,'request_id':record['request_id']}],'Sunday requested this HA service; this does not confirm any resulting state/physical change'))
  self.bus.publish(self.event('SUNDAY_ACTION_REQUESTED',{'kind':'action','id':aid,'semantic_name':domain+'.'+service},'status',None,'requested',utc(),provenance=prov,request=record['request_id'],identity=aid+':requested'))
  self.store.commit();return aid
 def action_result(self,aid,changed_states=None,unknown=False,reason=''):
  row=self.store.db.execute('select data from actions where id=?',(aid,)).fetchone()
  if not row:return
  rec=json.loads(row[0]);rec['status']='unknown' if unknown else 'service_completed';rec['reason']=reason;rec['changed_entities']=[]
  for state in changed_states or []:
   if not isinstance(state,dict) or state.get('entity_id') not in rec['targets']:continue
   eid=state['entity_id'];ctx=state.get('context') or {}
   ref={'kind':'sunday_service_result','action_id':aid,'request_id':rec['request_id'],'targets':[eid],'initiator':rec['initiator'],'path':rec['path']}
   self.context(ctx,ref);rec['changed_entities'].append(eid)
   self.ingest_state(state,live=True);self.enrich_context(ctx.get('id'))
  self.store.db.execute('update actions set data=? where id=?',(json.dumps(bounded(rec)),aid))
  prov=asdict(Provenance(rec['initiator'],{'type':'SUNDAY','path':rec['path']},'UNKNOWN' if unknown else 'CONFIRMED',[{'action_id':aid,'request_id':rec['request_id'],'service_completed':not unknown}],'Service outcome unknown; never auto-retry' if unknown else 'HA service completed; physical actuation is not proven'))
  self.bus.publish(self.event('SUNDAY_ACTION_UNKNOWN' if unknown else 'SUNDAY_ACTION_COMPLETED',{'kind':'action','id':aid,'semantic_name':rec['domain']+'.'+rec['service']},'status','requested',rec['status'],utc(),provenance=prov,request=rec['request_id'],identity=aid+':result'))
  self.store.commit()
 async def reconcile(self,startup=False):
  await self.home.refresh();states=await self.ha.get_states();seen=set()
  # Existing action targets retain active relevance after a process restart.
  for (encoded,) in self.store.db.execute('select data from actions where observed>?',(time.time()-86400,)):
   self.active.update(json.loads(encoded).get('targets',[]))
  selected=self.selected()
  for state in states:
   if state['entity_id'] not in selected:continue
   seen.add(state['entity_id']);self.ingest_state(state,reconcile=True)
  for eid in selected-seen:
   self.ingest_state({'entity_id':eid,'state':'unknown','attributes':{},'last_updated':utc()},reconcile=True)
  # Facts whose entities ceased to be relevant do not become a growing HA mirror.
  for (eid,) in self.store.db.execute("select distinct subject from facts where subject not like 'person:%'").fetchall():
   if eid not in selected:self.store.db.execute('delete from facts where subject=?',(eid,))
  policy=getattr(self.home,'input_policy',None)
  if not policy or any(policy.source(p,'presence') for p in ('memory','proactivity')):
   snapshot=await self.presence.get_presence();self.ingest_presence(snapshot,reconcile=startup)
  self.store.count('world_model_reconciliations');self.store.count('restart_reconciliation_count' if startup else 'periodic_reconciliation_count')
  self.last_sync=time.time();self.session_ready=True;self.store.prune();self.store.commit()
 async def fresh_entities(self,ids,max_age=None):
  """Bounded current reads. Disconnected/old/missing facts force live HA reads."""
  max_age=self.config.world_fact_max_age_seconds if max_age is None else max_age
  result={}
  for eid in list(dict.fromkeys(ids))[:40]:
   fact=self.store.get(eid)
   metadata=self.home.entities.get(eid,{})
   newer_home_snapshot=fact and epoch(metadata.get('last_updated') or metadata.get('last_changed'))>epoch(fact['source_observed_at'])
   if self.connected and fact and not newer_home_snapshot and time.time()-epoch(fact['observed_at'])<=max_age and fact['availability']=='available':
    result[eid]=self.state_view(eid);continue
   state=await self.ha.get_state(eid)
   # A fresh query is a snapshot, not evidence of when a gap transition happened.
   self.active.add(eid);self.ingest_state(state,reconcile=True)
   result[eid]=self.state_view(eid)
  self.store.commit();return result
 def state_view(self,eid):
  facts=self.store.current(eid);base=self.home.entities.get(eid,{})
  attrs={k:v for k,v in base.get('attributes',{}).items() if k not in PROPERTIES.get(eid.split('.')[0],())};state='unknown'
  for fact in facts:
   if fact['property']=='state':state=str(fact['value'])
   elif fact['property']!='availability':attrs[fact['property']]=fact['value']
  return {'entity_id':eid,'state':state,'attributes':attrs}
 def get_current(self,subject,limit=20):
  facts=self.store.current(subject,limit)
  for fact in facts:
   fact.pop('event_value',None)
   fact['freshness_age_seconds']=round(max(0,time.time()-epoch(fact['observed_at'])),3)
   fact['fresh']=self.connected and fact['freshness_age_seconds']<=self.config.world_fact_max_age_seconds
  return facts
 def explain(self,subject):
  facts=self.get_current(subject);latest=self.store.get(subject)
  transitions=self.store.events(subject,10)
  # Timing is returned as a separate weak relation, never promoted to cause.
  temporal=[]
  event=next((e for e in transitions if e['event_type'] in ('DEVICE_STATE_CHANGED','DEVICE_PROPERTY_CHANGED')),None)
  if event:
   at=epoch(event['occurred_at'])
   for item in self.store.events(None,40,24):
    if item['event_type'] in ('AUTOMATION_TRIGGERED','SCRIPT_EXECUTED') and abs(epoch(item['occurred_at'])-at)<=10 and item['correlation'].get('ha_context_id')!=event['correlation'].get('ha_context_id'):
     temporal.append({'event_id':item['event_id'],'subject':item['subject'],'attribution':'POSSIBLE','reason':'Temporal proximity only; not a causal attribution'})
  return {'current':facts,'recent_transitions':transitions[:5],'temporal_relations':temporal[:3], 'causality_policy':'Unknown remains unknown; temporal proximity does not prove causality','physical_success_confirmed':False}
 def project_provenance(self,p):
  return {k:p.get(k) for k in ('initiator','via','attribution','reason','physical_success_confirmed')} | {'evidence':p.get('evidence',[])[:3]}
 def relevant_ids(self,query):
  q=' '+norm(query)+' '
  if not re.search(r'\b(?:is|are|was|were|why|what|state|status|on|off|turn|change|changed|running|temperature|humidity|brightness|speed|volume|open|closed)\b',norm(query)):return []
  # Explicitly unrelated work tasks never acquire state context from incidental words.
  if re.search(r'\b(?:capital|write|email|translate|summarize|poem|joke|essay|code)\b',norm(query)):return []
  from fast_mode import Planner,CATEGORIES,Miss
  planner=Planner(self.home);rows=planner.universe('primary_context');selected=set()
  named=[]
  for e in rows:
   names=[e['name'],*e.get('aliases',[]),friendly(e,self.home)]
   names=[norm(n) for n in names if len(norm(n))>=2]
   if any(' '+name+' ' in q for name in names):named.append(e['entity_id'])
  # Domain terms like "fan" must not resolve both household fans by incidental substrings.
  for word,domain in CATEGORIES.items():
   if domain=='everything' or ' '+word+' ' not in q:continue
   choices=[e for e in rows if e['domain']==domain]
   if len(choices)==1:selected.add(choices[0]['entity_id'])
  selected.update(named)
  if re.search(r'\b(?:temperature|humidity)\b',norm(query)):
   prop='temperature' if 'temperature' in norm(query).split() else 'humidity'
   sensors=[e['entity_id'] for e in self.home.entities.values() if e['domain']=='sensor' and e.get('area_id')==self.home.household['primary_area'] and e.get('attributes',{}).get('device_class')==prop and ordinary(e)]
   if len(sensors)==1:selected.add(sensors[0])
  for eid in re.findall(r'\b[a-z_]+\.[a-z0-9_]+\b',query):
   if eid in self.home.entities:selected.add(eid)
  return sorted(selected)[:4]
 async def relevant_world_context(self,query,conversation_context=None,request_id=None):
  self.store.count('context_projection_requests');ids=self.relevant_ids(query)
  # Contextual presence boundary is shared, including normal-brain projection.
  snapshot=await self.presence.get_presence()
  from presence import contextual_text
  try:contextual_text(query,snapshot)
  except ValueError:ids=[]
  if ids:await self.fresh_entities(ids)
  projected=[]
  for eid in ids:
   facts=self.get_current(eid)
   words=set(norm(query).split())
   facts.sort(key=lambda f:(f['property']!='state',f['property'] not in words,f['property']!='availability',f['property']))
   # The projection is evidence, never a giant attribute/configuration dump.
   item={'entity':self.subject(eid),'facts':[{'property':f['property'],'value':f['value'],'unit':f['unit'],'changed_at':f['changed_at'],'observed_at':f['observed_at'],'fresh':f['fresh'],'availability':f['availability'],'last_event_id':f['last_event_id'],'provenance':self.project_provenance(f['provenance'])} for f in facts[:6]]}
   latest=self.store.events(eid,1)
   if latest:
    ev=latest[0];item['latest_transition']={k:ev[k] for k in ('event_id','event_type','occurred_at','change','correlation')}
    item['latest_transition']['provenance']=self.project_provenance(ev['provenance'])
   candidate=json.dumps({'facts':projected+[item]},default=str)
   if len(candidate)>self.projection_budget:
    # Keep the exact current state/provenance before extra properties/history.
    item['facts']=item['facts'][:2];item.pop('latest_transition',None)
    candidate=json.dumps({'facts':projected+[item]},default=str)
   if len(candidate)<=self.projection_budget:projected.append(item)
  result=json.dumps({'facts':projected},default=str) if projected else ''
  self.last_projection={'request_id':request_id,'subjects':ids,'fact_count':sum(len(i['facts']) for i in projected),'size':len(result),'projection':result,'injected':bool(self.config.world_context_injection_enabled)}
  self.store.count('context_projection_fact_count',self.last_projection['fact_count']);self.store.count('context_projection_size',len(result))
  if request_id:
   diagnostic={**self.last_projection,'projection':json.loads(result) if result else {}}
   self.store.db.execute('insert or replace into projections values(?,?,?)',(request_id,time.time(),json.dumps(bounded(diagnostic))))
  self.store.commit();return result
 async def ingest_ha_event(self,event):
  self.raw_event(event)
  if event.get('event_type')=='state_changed':
   cfg=self.home.household.get('people',{}).get(norm(self.home.household.get('primary_person','Karthik')),{})
   related={cfg.get('ha_person'),*cfg.get('presence_zones',{}).values()}-{None}
   policy=getattr(self.home,'input_policy',None)
   if event.get('data',{}).get('entity_id') in related and (not policy or any(policy.source(p,'presence') for p in ('memory','proactivity'))):
    self.ingest_presence(await self.presence.get_presence(force=True))

 async def watch(self):
  """One passive HA websocket; reconnect/reconcile instead of inventing missed history."""
  import aiohttp
  event_types=('state_changed','automation_triggered','script_started','call_service')
  while not self._closed:
   try:
    if self.ha.session is None:await self.ha.get_states()
    async with self.ha.session.ws_connect(self.ha.url+'/api/websocket',heartbeat=25) as ws:
     async with asyncio.timeout(15):
      if (await ws.receive_json()).get('type')!='auth_required':raise RuntimeError('HA websocket greeting')
      await ws.send_json({'type':'auth','access_token':self.ha.token})
      if (await ws.receive_json()).get('type')!='auth_ok':raise RuntimeError('HA websocket auth')
      for i,event_type in enumerate(event_types,1):await ws.send_json({'id':i,'type':'subscribe_events','event_type':event_type})
      ack=set();buffer=[]
      while len(ack)<len(event_types):
       result=await ws.receive_json()
       if result.get('type')=='result':
        if not result.get('success'):raise RuntimeError('HA subscription denied')
        ack.add(result['id'])
       elif result.get('type')=='event':buffer.append(result['event'])
     self.reconnect_cutoff=time.time()
     await self.reconcile(startup=True);self.connected=True;self.last_error=''
     for event in buffer:await self.ingest_ha_event(event)
     while not self._closed:
      try:
       result=await asyncio.wait_for(ws.receive_json(),timeout=30)
       if result.get('type')=='event':await self.ingest_ha_event(result['event'])
      except asyncio.TimeoutError:pass
      if time.time()-self.last_sync>=60:await self.reconcile()
   except asyncio.CancelledError:raise
   except Exception as error:
    self.last_error=type(error).__name__;self.store.count('ingestion_disconnects');self.store.commit()
   finally:self.connected=False
   if not self._closed:await asyncio.sleep(3)
 async def start(self):
  self.ingestion_task=asyncio.create_task(self.watch(),name='factual-world-ingestion')
 async def close(self):
  self._closed=True
  if self.ingestion_task:self.ingestion_task.cancel();await asyncio.gather(self.ingestion_task,return_exceptions=True)
  self.store.close()
