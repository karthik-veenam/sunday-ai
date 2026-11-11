from dataclasses import asdict,is_dataclass
from .store import digest,js

def compact(v,depth=0):
 if depth>4:return str(v)[:120]
 if isinstance(v,dict):return {str(k)[:80]:compact(x,depth+1) for k,x in list(v.items())[:8]}
 if isinstance(v,list):return [compact(x,depth+1) for x in v[:4]]
 if isinstance(v,str):return v[:240]
 return v
ALLOWED={'DEVICE_STATE_CHANGED','DEVICE_PROPERTY_CHANGED','SENSOR_VALUE_CHANGED','AVAILABILITY_CHANGED','PRESENCE_CHANGED','AUTOMATION_TRIGGERED','SCRIPT_EXECUTED','SCENE_ACTIVATED'}
class Episodes:
 def __init__(self,store,scheduler,max_events=32,exclude_automations=False):self.store=store;self.scheduler=scheduler;self.max_events=max_events;self.exclude_automations=exclude_automations
 def excluded(self,e):
  if not self.exclude_automations:return False
  provenance=e.get('provenance') or {};correlation=e.get('correlation') or {}
  return bool(e.get('event_type')=='AUTOMATION_TRIGGERED' or str(e.get('subject',{}).get('id','')).startswith('automation.') or correlation.get('automation_id') or provenance.get('initiator',{}).get('type')=='HA_AUTOMATION' or provenance.get('via',{}).get('type')=='HA_AUTOMATION' or any(isinstance(ref,dict) and ref.get('kind')=='automation_triggered' for ref in provenance.get('evidence',[])))
 def filter_closed(self,data):
  remaining=[e for e in data['events'] if not self.excluded(e)]
  if len(remaining)==len(data['events']):return data
  before={};after={}
  for e in remaining:
   key=str(e.get('subject',{}).get('id'))+':'+str(e.get('change',{}).get('property'));before.setdefault(key,e['change'].get('old'));after[key]=e['change'].get('new')
  return data|{'events':remaining,'before':before,'after':after,'automation_events_excluded':len(data['events'])-len(remaining)}
 def add(self,event):
  e=asdict(event) if is_dataclass(event) else event
  if e.get('event_type') not in ALLOWED or self.excluded(e):return None
  if e.get('change',{}).get('old')==e.get('change',{}).get('new'):return None
  if self.store.seen('ha:'+e['event_id']):return None
  e={k:compact(v) for k,v in e.items() if k in ('event_id','event_type','occurred_at','observed_at','subject','change','source','provenance','correlation')}
  if len(js(e))>1800:
   provenance=e.get('provenance',{});e['provenance']={k:v for k,v in provenance.items() if k!='evidence'}
   e['provenance']['evidence']=[{k:v for k,v in ref.items() if k in ('action_id','request_id','context_id','entity_id','kind')} for ref in provenance.get('evidence',[])[:2] if isinstance(ref,dict)]
  if getattr(self,'situations',None):
   self.situations.add(e)
  t=self.store.clock();open_rows=[r for r in self.store.rows('episodes',('OPEN',),100) if r['due']>t]
  # A household episode is a bounded sequence across related devices, not one unbounded window per sensor.
  row=open_rows[0] if open_rows else None
  if row is None:
   identity=self.store.put('episodes',{'events':[],'before':{},'after':{},'dropped_events':0,'first_event_at':t},dedupe=['episode',e['event_id']],state='OPEN',due=t+10)
   self.scheduler.add(t+10,'EPISODE_CLOSE','SYSTEM',{'episode_id':identity},['episode-close',identity]);row=self.store.get('episodes',identity)
  data=row['data'];subject=e.get('subject',{}).get('id');prop=e.get('change',{}).get('property');k=str(subject)+':'+str(prop)
  if k in data['before'] or len(data['before'])<self.max_events:
   data['before'].setdefault(k,e.get('change',{}).get('old'));data['after'][k]=e.get('change',{}).get('new')
  if len(data['events'])<self.max_events:data['events'].append(e)
  else:data['dropped_events']+=1
  # Keep the original due; later events never move the ten-second boundary.
  self.store.change('episodes',row['id'],data=data);return row['id']
 def packet(self,row,context):
  data=row['data'];events=[dict(e) for e in data['events']]
  from datetime import datetime
  for e in events:
   try:e['stale_evidence']=self.store.clock()-datetime.fromisoformat(e['occurred_at'].replace('Z','+00:00')).timestamp()>60
   except (ValueError,KeyError,TypeError):e['stale_evidence']=True
  packet={'SITUATION BEFORE':data['before'],'OBSERVED EVENT / EPISODE':events,'SITUATION AFTER':data['after'],'PROVENANCE':[e.get('provenance',{}) for e in events],'RELEVANT MEMORY':context.get('memory',[]),'ACTIVE RELEVANT THREADS / COMMITMENTS':context.get('threads',[]),'RECENT RELEVANT HISTORY':context.get('history',[]),'WORLD CONTEXT':context.get('world',[]),'PROVIDER HEALTH':context.get('health',{}),'HOME CONTEXT':context.get('household',{}),'TEMPORAL FRAME':{'started':data['first_event_at'],'closed':row['due'],'late_observations_possible':True,'dropped_events':data['dropped_events']}}
  while len(js(packet))>26000:
   if packet['RECENT RELEVANT HISTORY']:packet['RECENT RELEVANT HISTORY'].pop()
   elif len(packet['WORLD CONTEXT'])>4:packet['WORLD CONTEXT'].pop()
   elif len(packet['OBSERVED EVENT / EPISODE'])>1:
    packet['OBSERVED EVENT / EPISODE'].pop();packet['PROVENANCE'].pop();packet['TEMPORAL FRAME']['dropped_events']+=1
   else:raise ValueError('Episode cannot fit bounded context')
  return packet
