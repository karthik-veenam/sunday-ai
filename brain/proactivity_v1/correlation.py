"""Deterministic operational evidence; no actuation and no model arithmetic."""
from .store import digest
class Execution:
 def __init__(self,store,scheduler):self.store=store;self.scheduler=scheduler
 def intent(self,receipt,expected=None,convergence_seconds=None):
  # Only explicit generic services have deterministic state semantics; unknown capabilities remain unknown.
  expected=expected if expected is not None else {'turn_on':'on','turn_off':'off'}.get(receipt.get('service')) if receipt.get('domain') in ('light','switch','fan','input_boolean') else expected
  properties={'state':expected} if expected is not None else {}
  values=receipt.get('intended_values') or {}
  mapping={'temperature':'temperature','hvac_mode':'state','fan_mode':'fan_mode','percentage':'percentage','preset_mode':'preset_mode','volume_level':'volume_level','position':'current_position','brightness':'brightness'}
  for field,prop in mapping.items():
   if field in values and not isinstance(values[field],(dict,list)):properties[prop]=values[field]
  if 'brightness_pct' in values and isinstance(values['brightness_pct'],(int,float)):properties['brightness']=round(values['brightness_pct']*255/100)
  seconds=convergence_seconds if convergence_seconds is not None else 30 if receipt.get('domain') in ('cover','climate','media_player') else 8
  seconds=max(1,min(300,float(seconds)));data={'receipt':receipt,'expected':expected,'expected_properties':properties,'dispatch_at':self.store.clock(),'observations':{},'service_status':'pending','deadline':self.store.clock()+seconds}
  rid=self.store.put('executions',data,dedupe=receipt['action_id']);self.scheduler.add(data['deadline'],'EXECUTION_CHECK','SYSTEM',{'execution_id':rid},['execution-check',rid]);return rid
 def result(self,action_id,unknown=False):
  rid='executions:'+digest(action_id);r=self.store.get('executions',rid)
  if not r or r['state']!='PENDING':return None
  d=r['data'];d['service_status']='unknown' if unknown else 'returned';self.store.change('executions',rid,data=d);return rid
 def observe(self,entity,state,context_ids=(),request_id=None,occurred=None,available=True,property="state"):
  for r in self.store.rows('executions',('PENDING',),100):
   d=r['data'];receipt=d['receipt']
   if entity not in receipt.get('targets',[]):continue
   if occurred is not None and not d['dispatch_at']<=occurred<=d['deadline']:continue
   # Target/time coincidence alone is not causal attribution.
   refs={receipt.get('ha_context_id'),receipt.get('parent_context_id')}-{None};linked=bool(request_id and request_id==receipt.get('request_id')) or bool(refs & set(context_ids))
   if not linked:continue
   entry=d['observations'].setdefault(entity,{'properties':{},'available':True});entry['properties'][property]=state;entry['available']=bool(available);entry['at']=occurred or self.store.clock();self.store.change('executions',r['id'],data=d)
 def properties(self,data):return data.get('expected_properties',{'state':data['expected']} if data.get('expected') is not None else {})
 def matches(self,prop,value,expected):return abs(value-expected)<=1 if prop=='brightness' and isinstance(value,(int,float)) and isinstance(expected,(int,float)) else value==expected
 def classify(self,row):
  d=row['data'];targets=d['receipt'].get('targets',[]);obs=d['observations'];properties=self.properties(d)
  if d['service_status']=='unknown':return 'TIMEOUT_UNKNOWN_OUTCOME'
  if not properties:return 'UNKNOWN_CAPABILITY'
  if not targets or any(e in obs and not obs[e]['available'] for e in targets):return 'UNKNOWN_VERIFICATION'
  matching=[];contradictions=[]
  for entity in targets:
   entry=obs.get(entity,{});actual=entry.get('properties',{'state':entry['value']} if 'value' in entry else {})
   if all(p in actual and self.matches(p,actual[p],v) for p,v in properties.items()):matching.append(entity)
   if any(p in actual and not self.matches(p,actual[p],v) for p,v in properties.items()):contradictions.append(entity)
  if len(matching)==len(targets) and d['service_status']=='returned':return 'CLEAN_CONFIRMED_HA_STATE'
  if contradictions:return 'PARTIAL_COMPLETION' if matching else 'CONTRADICTION'
  if self.store.clock()<d['deadline']:return 'PENDING'
  if matching:return 'PARTIAL_COMPLETION'
  if not obs:return 'UNKNOWN_VERIFICATION'
  return 'NON_CONVERGENCE'
 def explained(self,event,require_return=True):
  request=(event.get('correlation') or {}).get('request_id')
  if not request:return False
  for raw in self.store.db.execute("select * from executions where json_extract(data,'$.receipt.request_id')=? and state in ('PENDING','CLEAN_CONFIRMED_HA_STATE') order by created desc limit 30",(request,)):
   d=self.store.decode(raw)['data'];prop=event['change'].get('property');expected=self.properties(d)
   if require_return and d['service_status']!='returned':continue
   if event['subject']['id'] in d['receipt'].get('targets',[]) and prop in expected and self.matches(prop,event['change'].get('new'),expected[prop]):return True
  return False
 def due(self,identity):
  row=self.store.get('executions',identity)
  if not row or row['state']!='PENDING':return {'state':'DONE','reason':'Already correlated'}
  if row['data']['service_status']=='pending' and not row['data'].get('return_wait_extended'):
   d=row['data'];d['return_wait_extended']=True;d['deadline']=self.store.clock()+35;self.store.change('executions',identity,data=d);self.scheduler.add(d['deadline'],'EXECUTION_CHECK','SYSTEM',{'execution_id':identity},['execution-return-wait',identity]);return {'state':'DONE','reason':'Waiting for service return; no physical outcome inferred'}
  state=self.classify(row);d=row['data'];d['classification']=state;d['physical_success_confirmed']=False;self.store.change('executions',identity,state=state,data=d)
  if state in {'TIMEOUT_UNKNOWN_OUTCOME','CONTRADICTION','PARTIAL_COMPLETION','NON_CONVERGENCE','UNKNOWN_VERIFICATION'}:
   receipt=d['receipt'];incident='request:'+receipt['request_id'] if receipt.get('request_id') else 'action:'+receipt['action_id'];wake=self.store.wake('EXECUTION_ANOMALY','SYSTEM',receipt.get('targets') or receipt['action_id'],[identity],incident,entities=receipt.get('targets',[]),anomaly=state,no_retry=True);return {'state':'DONE','wake_id':wake}
  return {'state':'DONE','classification':state}
class Health:
 def __init__(self,store,scheduler,persistence_seconds=120):self.store=store;self.scheduler=scheduler;self.persistence=persistence_seconds
 def update(self,provider,healthy,detail=''):
  identity='health:'+digest(provider);old=self.store.get('health',identity)
  if healthy:
   self.store.put('health',{'provider':provider,'healthy':True,'since':self.store.clock()},dedupe=provider,state='HEALTHY');self.store.change('health',identity,state='HEALTHY',data={'provider':provider,'healthy':True,'since':self.store.clock()});return identity
  if old and old['state'] in ('DEGRADED','NOTED'):return identity
  since=self.store.clock();self.store.put('health',{'provider':provider,'healthy':False,'since':since,'detail':str(detail)[:160]},dedupe=provider,state='DEGRADED');self.store.change('health',identity,state='DEGRADED',data={'provider':provider,'healthy':False,'since':since,'detail':str(detail)[:160]});self.scheduler.add(since+self.persistence,'HEALTH_CHECK','SYSTEM',{'health_id':identity,'failure_since':since},['health-check',provider,since]);return identity
 def due(self,row):
  d=row['data'];h=self.store.get('health',d['health_id'])
  if not h or h['state']!='DEGRADED' or h['data']['since']!=d['failure_since']:return {'state':'SATISFIED'}
  incident='health:'+d['health_id']+':'+str(d['failure_since']);wake=self.store.wake('SYSTEM_DEGRADATION','SYSTEM',h['data']['provider'],[h['id']],incident,provider_state=h['data']);self.store.change('health',h['id'],state='NOTED');return {'state':'DONE','wake_id':wake}
