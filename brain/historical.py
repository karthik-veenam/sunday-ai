"""One bounded read-only historical evidence service. No model/provider imports."""
import asyncio,copy,json,math,re,sqlite3,statistics,time,uuid,os
from collections import OrderedDict
from contextlib import asynccontextmanager
from contextvars import ContextVar
_QUERY_METRICS=ContextVar("history_query_metrics",default=None)
from dataclasses import dataclass,asdict
from datetime import datetime,timezone,timedelta
from pathlib import Path
from urllib.parse import urlencode
from history_time import WindowParser,TimeWindow,HistoryError
import history_math as calc
from home_model import norm
from home_presentation import ordinary,friendly
from world import bounded,epoch

@asynccontextmanager
async def query_slot(sem):
 await asyncio.wait_for(sem.acquire(),timeout=3)
 try:yield
 finally:sem.release()

OPS={'history','statistics','transitions','duration','count_transitions','last_changed','first_changed','automation_runs','automation_last_run','automation_trace','script_runs','script_last_run','script_trace','presence_history','compare','evidence','semantic_events','action_receipts'}
@dataclass(frozen=True)
class HistoricalPlan:
 operation:str
 target:str
 window:object='last 24 hours'
 options:dict|None=None

class HistoricalEvidenceProvider:
 def __init__(self,home,presence,world,config,diagnostics_path):
  self.home=home;self.ha=home.ha;self.presence=presence;self.world=world;self.config=config;self.state_dir=Path(diagnostics_path).parent
  settings=home.household.get('history',{});self.parser=WindowParser(config.timezone,settings.get('night_start_hour',21),settings.get('night_end_hour',7))
  self.max_rows=6000;self.max_points=48;self.raw_days=7;self.sem=asyncio.Semaphore(2);self.timeout_seconds=20;self.cache=OrderedDict();self.diagnostics=sqlite3.connect(diagnostics_path);os.chmod(diagnostics_path,0o600)
  version=self.diagnostics.execute('pragma user_version').fetchone()[0]
  if version not in (0,1):raise RuntimeError('Unsupported historical diagnostics schema')
  self.diagnostics.execute('pragma user_version=1')
  self.diagnostics.execute('create table if not exists queries(id text primary key,at real,data text)');self.diagnostics.execute('create table if not exists metrics(name text primary key,value real)');self.diagnostics.commit()
 def count(self,name,value=1):
  self.diagnostics.execute('insert into metrics values(?,?) on conflict(name) do update set value=value+excluded.value',(name,value))
  local=_QUERY_METRICS.get()
  if local is not None:local[name]=local.get(name,0)+value
 def metrics(self):return dict(self.diagnostics.execute('select name,value from metrics'))
 def resolve(self,target,domain=None):
  if not isinstance(target,str) or not target.strip():raise HistoryError('TARGET_NOT_FOUND','Specify a historical target.')
  if target in self.home.entities:
   e=self.home.entities[target]
   if domain and e['domain']!=domain:raise HistoryError('TARGET_NOT_FOUND','Target domain does not match the operation.')
   return e
  q=norm(target);areas=self.home.area_ids(target);tokens=set(q.split())-{'the','automation','script','room','bedroom','our','my','history','sensor'}
  rows=[e for e in self.home.entities.values() if (not domain or e['domain']==domain) and ordinary(e)]
  exact=[e for e in rows if q in [norm(n) for n in [e['name'],*e.get('aliases',[]),friendly(e,self.home)]]]
  area_words=set()
  for aid in areas:
   for name in [aid,self.home.areas.get(aid,{}).get('name',''),*self.home.household.get('area_names',{}).get(aid,[])]:area_words.update(norm(name).split())
  generic_words=set(q.split())-area_words-{'room','my','our','the','sensor'}
  generic_sensor=generic_words in ({'temperature'},{'humidity'},{'power'},{'energy'},{'illuminance'})
  if len(exact)==1 and not generic_sensor:return exact[0]
  rows=[e for e in rows if e.get('area_id') in areas or e['domain'] in ('automation','script') and e.get('area_id') is None]
  category=next((dc for word,dc in [('temperature','temperature'),('humidity','humidity'),('illuminance','illuminance'),('power','power'),('energy','energy')] if word in tokens),None)
  if category and not domain:
   area_words=set()
   for aid in areas:
    for name in [aid,self.home.areas.get(aid,{}).get('name',''),*self.home.household.get('area_names',{}).get(aid,[])]:area_words.update(norm(name).split())
   extra=tokens-area_words-{category,'sensor','room'}
   if extra:raise HistoryError('TARGET_NOT_FOUND','The requested sensor qualifier does not resolve unambiguously.')
   candidates=[e for e in rows if e['domain']=='sensor' and e.get('attributes',{}).get('device_class')==category]
  else:
   from fast_mode import CATEGORIES
   category=CATEGORIES.get(q)
   if category and category!='everything' and not domain:candidates=[e for e in rows if e['domain']==category]
   else:
    concepts=tokens|({'climate'} if 'ac' in tokens else set());ranked=[]
    for e in rows:
     names=[norm(e['name']),norm(friendly(e,self.home)),*map(norm,e.get('aliases',[]))]
     score=max((len(concepts & set(n.split()))/max(1,len(tokens)) for n in names),default=0)
     if score>=1:ranked.append(e)
    candidates=ranked
  if len(candidates)==1:return candidates[0]
  if not candidates and domain in ('automation','script'):
   # Resolve a named physical button/device alias through existing structural references.
   try:
    linked=self.home.resolve(target.removesuffix(' automation').removesuffix(' script'))
    related=[p for p in self.home.related([linked['entity_id']],20) if p['domain']==domain]
    candidates=[self.home.entities[p['entity_id']] for p in related if p['entity_id'] in self.home.entities]
   except (ValueError,KeyError):pass
  if len(candidates)>1:raise HistoryError('AMBIGUOUS_TARGET','Which target do you mean?',{'candidates':[{'entity_id':e['entity_id'],'name':e['name']} for e in candidates[:6]]})
  raise HistoryError('TARGET_NOT_FOUND','No unambiguous matching Home Model target.')
 def plan_text(self,text):
  t=text.strip().lower().rstrip('.?!');t=re.sub(r'^(?:please |sunday[, ]+)','',t)
  window_pattern=r'(last \d+\s*(?:hours?|h|days?)|past \d+ days|last hour|today|yesterday|last night|this morning|this week|last week|since \d+(?::\d+)?\s*(?:am|pm)|between .+ and .+|(?:oct|nov|dec|jan|feb|mar|apr|may|jun|jul|aug|sep)[a-z]* \d{1,2}(?: \d{4})?)'
  match=re.search(r'\b'+window_pattern+r'$',t)
  window=match[1] if match else None;body=t[:match.start()].strip() if match else t
  body=re.sub(r'\s+(?:over|in|during|for)(?: the)?$','',body)
  if re.fullmatch(r'(?:was|is) (.+?) hotter last night than (?:the )?night before',t):
   target=re.fullmatch(r'(?:was|is) (.+?) hotter last night than (?:the )?night before',t)[1];return HistoricalPlan('compare',target,'last night',{'other_window':'the night before'})
  if (m:=re.fullmatch(r'(?:when was|when did) (.+?) last (?:triggered|trigger|run|ran)',body)):
   return HistoricalPlan('script_last_run' if 'script' in m[1].split() else 'automation_last_run',m[1],window or 'last 24 hours')
  if not window:return None
  if (m:=re.fullmatch(r'(?:what was|what is|what\x27s) (?:the )?(highest|lowest|maximum|minimum) (.+)',body)):
   return HistoricalPlan('statistics',m[2],window,{'requested_metric':'maximum' if m[1] in ('highest','maximum') else 'minimum'})
  if (m:=re.fullmatch(r'(?:what was |how was |show (?:me )?)?(?:the )?(.+?) (?:trend|history)',body)):return HistoricalPlan('statistics',m[1],window)
  if (m:=re.fullmatch(r'how long was (?:the )?(.+?) (on|off|playing|cooling|heating)',body)):
   return HistoricalPlan('duration',m[1],window,{'state':m[2],'property':'hvac_action' if m[2] in ('cooling','heating') else 'state'})
  if (m:=re.fullmatch(r'how many times did (?:the )?(.+?) turn (on|off)',body)):return HistoricalPlan('count_transitions',m[1],window,{'to_state':m[2]})
  if (m:=re.fullmatch(r'how many times did (?:the )?(.+?) run',body)):return HistoricalPlan('script_runs' if 'script' in m[1].split() else 'automation_runs',m[1],window)
  if (m:=re.fullmatch(r'when did (?:the )?(.+?) (?:switch|turn) (on|off)',body)):return HistoricalPlan('transitions',m[1],window,{'to_state':m[2]})
  if re.fullmatch(r'when did i (?:get|arrive) home',body):return HistoricalPlan('presence_history',self.home.household.get('primary_person','Karthik'),window,{'to_state':'HOME'})
  if re.fullmatch(r'when did i leave home',body):return HistoricalPlan('presence_history',self.home.household.get('primary_person','Karthik'),window,{'from_state':'HOME'})
  if re.fullmatch(r'how long was i at (?:the )?office',body):return HistoricalPlan('presence_history',self.home.household.get('primary_person','Karthik'),window,{'presence':'OFFICE'})
  if (m:=re.fullmatch(r'why did (?:the )?(.+?) (?:keep )?(?:switching|turning|switch|turn) (off|on)',body)):return HistoricalPlan('evidence',m[1],window,{'to_state':m[2]})
  if (m:=re.fullmatch(r'(?:what was|how was) (?:the )?(.+? temperature)',body)):return HistoricalPlan('statistics',m[1],window)
  return None
 async def raw(self,eid,window):
  if window.seconds>self.raw_days*86400:raise HistoryError('UNSUPPORTED_METRIC','Raw history is limited to seven days; use aggregated statistics for longer numeric periods.')
  rows=[];cursor=window.start;raw_count=0
  while cursor<window.end:
   end=min(cursor+timedelta(hours=6),window.end)
   params={'filter_entity_id':eid,'end_time':end.isoformat(),'significant_changes_only':'0'}
   path='/api/history/period/'+cursor.isoformat()+'?'+urlencode(params)
   if hasattr(self.ha,'request_bounded'):data=await self.ha.request_bounded('GET',path,max_bytes=2500000)
   else:data=await self.ha.request('GET',path)
   part=data[0] if data else [];raw_count+=len(part);self.count('history_raw_samples_read',len(part))
   if rows and part and epoch(part[0].get('last_updated') or part[0].get('last_changed'))<=cursor.timestamp():
    props=('last_triggered','temperature','current_temperature','hvac_action','brightness','percentage','unit_of_measurement')
    if rows[-1].get('state')==part[0].get('state') and all(rows[-1].get('attributes',{}).get(p)==part[0].get('attributes',{}).get(p) for p in props):part=part[1:]
   rows+=part
   if raw_count>self.max_rows:raise HistoryError('UNSUPPORTED_METRIC','Recorder result exceeds the safe sample limit; request a shorter window or statistics.')
   cursor=end
  self.count('history_source_recorder');return rows
 async def series(self,e,window,prop='state',presence_map=None):
  allowed={'state','hvac_action','temperature','current_temperature','brightness','percentage','volume_level','last_triggered','preset_mode'}
  if prop not in allowed:raise HistoryError('UNSUPPORTED_METRIC','Unsupported historical property.')
  rows=await self.raw(e['entity_id'],window);samples,notes=calc.normalize(rows,window.start.timestamp(),window.end.timestamp(),prop,presence_map)
  if not samples:raise HistoryError('NO_DATA','No recorder evidence is available for this entity/window.')
  return samples,notes
 def base(self,op,e,window):
  return {'schema_version':1,'question_type':op,'target':{'entity_id':e['entity_id'],'name':friendly(e,self.home),'domain':e['domain']},'window':window.serial(),'status':'OK','source_refs':[],'limitations':[],'model_calls':0}
 def covered(self,result,cov,window):
  result['coverage']=cov
  retention=datetime.now(timezone.utc)-timedelta(days=self.config.history_recorder_retention_days)
  if window.start<retention:
   cov['retention_limited']=True;result['limitations'].append('Requested raw window extends beyond configured recorder retention; historical continuity cannot be certified.')
  if cov['unknown_seconds']>.001 or cov.get('retention_limited'):
   result['status']='PARTIAL_COVERAGE';self.count('history_partial_coverage')
  result['limitations'].append('Coverage follows retained HA states; silent recorder downtime cannot be excluded from state samples alone.')
 async def statistic_source(self,e,window):
  metadata=await self.ha.ws_command('recorder/get_statistics_metadata',statistic_ids=[e['entity_id']]);meta=metadata[0] if metadata else None
  if not meta:raise HistoryError('NO_DATA','This entity has no HA statistics metadata; use a supported shorter recorder window.')
  period='hour' if window.seconds<=7*86400 else 'day';fields=['mean','min','max'] if meta.get('has_mean') else ['sum','state','change']
  data=await self.ha.ws_command('recorder/statistics_during_period',start_time=window.start.isoformat(),end_time=window.end.isoformat(),statistic_ids=[e['entity_id']],period=period,types=fields)
  rows=data.get(e['entity_id'],[])
  if not rows:raise HistoryError('NO_DATA','No HA aggregate statistics are retained for this window.')
  if len(rows)>1000:raise HistoryError('UNSUPPORTED_METRIC','Statistics response exceeds safe bounds.')
  self.count('history_source_statistics');self.count('history_raw_samples_read',len(rows));out=self.base('statistics',e,window);out['source_refs']=[{'source':'ha_statistics','statistic_id':e['entity_id'],'period':period}];out['unit']=meta.get('display_unit_of_measurement') or meta.get('statistics_unit_of_measurement')
  def ts(value):return value/1000 if isinstance(value,(int,float)) and value>1e11 else value
  valid=[]
  for row in rows:
   start=ts(row.get('start'));end=ts(row.get('end'))
   if not isinstance(start,(int,float)):continue
   if start<window.start.timestamp() or end is None or end>window.end.timestamp():continue
   valid.append({**row,'start':start,'end':end})
  if meta.get('has_mean'):valid=[r for r in valid if all(isinstance(r.get(k),(int,float)) and math.isfinite(r[k]) for k in ('mean','min','max'))]
  if not valid:raise HistoryError('PARTIAL_COVERAGE','No complete aggregate buckets lie inside the requested window.')
  out['coverage']={'available_fraction':min(1,sum(r['end']-r['start'] for r in valid)/window.seconds),'actual_start':min(r['start'] for r in valid),'actual_end':max(r['end'] for r in valid),'bucket_count':len(valid)}
  out['latest_available_sample']=datetime.fromtimestamp(max(r['end'] for r in valid),timezone.utc).isoformat();out['latest_available_sample_semantics']='end of latest complete HA aggregate bucket'
  if meta.get('has_mean'):
   samples=[{'timestamp':r['start'],'time':datetime.fromtimestamp(r['start'],timezone.utc).isoformat(),'value':r.get('mean')} for r in valid if isinstance(r.get('mean'),(int,float))]
   summary=calc.numeric(samples,window.start.timestamp(),window.end.timestamp(),out['unit'])
   summary['minimum']={'value':min(r['min'] for r in valid),'at':None,'bucket_start':min(valid,key=lambda r:r['min'])['start']}
   summary['maximum']={'value':max(r['max'] for r in valid),'at':None,'bucket_start':max(valid,key=lambda r:r['max'])['start']}
   summary['range']=summary['maximum']['value']-summary['minimum']['value']
   summary['trend_basis']='HA aggregate bucket means; not within-bucket variation'
   summary['mean_basis']='duration-weighted HA bucket means; median/first/last describe bucket means, not raw samples'
   summary['mean']=sum(r['mean']*(r['end']-r['start']) for r in valid if isinstance(r.get('mean'),(int,float)))/sum(r['end']-r['start'] for r in valid if isinstance(r.get('mean'),(int,float)))
   out.update(series_summary=summary,series=calc.buckets(samples,window.start.timestamp(),window.end.timestamp(),self.max_points))
  else:
   changes=[r.get('change') for r in valid if isinstance(r.get('change'),(int,float))]
   out['series_summary']={'counter_delta':sum(changes) if changes else None,'sample_count':len(valid),'semantics':'HA reset-adjusted statistics change; not an instantaneous-sensor trend'};out['series']=[{k:r.get(k) for k in ('start','end','state','sum','change')} for r in valid[:48]]
  out['limitations'].append('Aggregated buckets cannot establish exact extrema timestamps or within-bucket shape. Incomplete boundary buckets are excluded.')
  if out['coverage']['available_fraction']<.999:out['status']='PARTIAL_COVERAGE';self.count('history_partial_coverage')
  return out
 async def traces(self,e,window=None,run_id=None):
  program=self.home.programs.get(e['entity_id'])
  if not program:raise HistoryError('TRACE_UNAVAILABLE','No Home Model program identity is available for traces.')
  fields={'domain':e['domain'],'item_id':program['id'] if e['domain']=='automation' else e['entity_id'].split('.',1)[1]}
  try:data=await self.ha.ws_command('trace/get' if run_id else 'trace/list',**fields,**({'run_id':run_id} if run_id else {}))
  except Exception as error:raise HistoryError('TRACE_UNAVAILABLE','HA trace is unavailable or expired.') from error
  self.count('history_source_trace')
  if run_id:return self.normalize_trace(data,e)
  rows=list(data.values()) if isinstance(data,dict) else data
  if not isinstance(rows,list):raise HistoryError('TRACE_UNAVAILABLE','Unexpected HA trace response.')
  runs=[]
  for row in rows[:50]:
   stamp=(row.get('timestamp') or {}).get('start');at=epoch(stamp)
   if window and not window.start.timestamp()<=at<window.end.timestamp():continue
   runs.append({'started_at':stamp,'finished_at':(row.get('timestamp') or {}).get('finish'),'status':row.get('script_execution'),'trace_state':row.get('state'),'run_id':row.get('run_id'),'trigger':self.safe_trigger(row.get('trigger'))})
  return runs
 def safe_trigger(self,value):
  text=str(value or '')
  return '[redacted]' if re.search(r'(?i)webhook|token|password|secret|latitude|longitude|gps|authorization',text) else bounded(text)
 def normalize_trace(self,trace,e):
  if not isinstance(trace,dict) or not trace.get('run_id'):raise HistoryError('TRACE_UNAVAILABLE','Trace payload is not available.')
  steps=[]
  for path,entries in list((trace.get('trace') or {}).items())[:60]:
   for step in entries[-1:]:
    result=step.get('result') or {};params=result.get('params') or {};condition=result.get('result')
    item={'path':path,'timestamp':step.get('timestamp')}
    node=trace.get('config') or {};condition_node={}
    for part in path.split('/'):
     if isinstance(node,dict) and 'condition' in node:condition_node=node
     if isinstance(node,list) and part.isdigit() and int(part)<len(node):node=node[int(part)]
     elif isinstance(node,dict):node=node.get(part,node.get({'trigger':'triggers','condition':'conditions','action':'actions'}.get(part,''),{}))
     else:break
    if isinstance(node,dict) and 'condition' in node:condition_node=node
    precise_location=condition_node.get('attribute') in ('latitude','longitude','gps_accuracy')
    if condition_node and not precise_location:
     descriptor={k:v for k,v in condition_node.items() if k in ('condition','entity_id','attribute','state','above','below') and isinstance(v,(str,int,float,bool,list)) and '{{' not in str(v)}
     item['condition']=bounded(descriptor)
    for field in (() if precise_location else ('state','wanted_state','above','below')):
     value=result.get(field)
     if isinstance(value,(int,float,bool)) or isinstance(value,str) and len(value)<80 and '{{' not in value:item.setdefault('condition_evidence',{})[field]=value
    if isinstance(condition,bool):item['condition_result']=condition
    if params:
     item['action']={'domain':params.get('domain'),'service':params.get('service'),'target':bounded(params.get('target') or {}),'entity_ids':bounded((params.get('service_data') or {}).get('entity_id')),'values':{k:v for k,v in (params.get('service_data') or {}).items() if k in ('hvac_mode','temperature','percentage','brightness_pct','volume_level')}}
    if step.get('error'):item['error_type']='HA trace step error'
    steps.append(item)
  return {'run_id':trace.get('run_id'),'program':e['entity_id'],'started_at':(trace.get('timestamp') or {}).get('start'),'finished_at':(trace.get('timestamp') or {}).get('finish'),'status':trace.get('script_execution'),'trace_state':trace.get('state'),'trigger':self.safe_trigger(trace.get('trigger')),'context':{k:(trace.get('context') or {}).get(k) for k in ('id','parent_id','user_id')},'steps':steps[:60],'source_ref':{'source':'ha_trace','entity_id':e['entity_id'],'run_id':trace.get('run_id')},'limitations':['A retained trace is one execution attempt, not a complete execution history; only recorded action steps establish an action.']}
 async def logbook_runs(self,e,window):
  if window.seconds>self.raw_days*86400:raise HistoryError('UNSUPPORTED_METRIC','Execution-history windows are limited to seven days.')
  path='/api/logbook/'+window.start.isoformat()+'?'+urlencode({'entity':e['entity_id'],'end_time':window.end.isoformat()})
  data=await self.ha.request_bounded('GET',path,max_bytes=2500000) if hasattr(self.ha,'request_bounded') else await self.ha.request('GET',path)
  if not isinstance(data,list):raise HistoryError('HISTORY_UNAVAILABLE','HA logbook did not return an execution list.')
  if len(data)>2000:raise HistoryError('UNSUPPORTED_METRIC','Execution query exceeds the safe row limit; narrow the window.')
  result={}
  for row in data:
   if row.get('entity_id')!=e['entity_id'] or row.get('domain')!=e['domain']:continue
   message=str(row.get('message',''))
   if not (message.startswith('triggered') if e['domain']=='automation' else message.startswith('started')):continue
   at=epoch(row.get('when'))
   if not window.start.timestamp()<=at<window.end.timestamp():continue
   key=(row.get('context_id'),row.get('when'));result[key]={'started_at':row['when'],'finished_at':None,'status':'action_execution_started','context_id':row.get('context_id'),'trigger':self.safe_trigger(row.get('source')),'source':'ha_logbook'}
  self.count('history_source_logbook');return sorted(result.values(),key=lambda r:epoch(r['started_at']))
 async def runs(self,e,window,last=False):
  out=self.base(e['domain']+'_last_run' if last else e['domain']+'_runs',e,window);state=await self.ha.get_state(e['entity_id']);last_value=state.get('attributes',{}).get('last_triggered');accepted=[];trace_runs=[]
  try:trace_runs=await self.traces(e,window if not last else None)
  except HistoryError as error:out['limitations'].append(error.status)
  if not last:
   try:
    executions=await self.logbook_runs(e,window)
    out.update(action_run_count=len(executions),accepted_action_run_count=len(executions),action_runs=executions[:40],more=len(executions)>40)
    cov={'available_fraction':1.,'initial_state_known':True,'known_seconds':window.seconds,'unknown_seconds':0.,'count_scope':'Retained HA logbook action-start events; not trigger attempts or completions'}
    self.covered(out,cov,window)
   except (HistoryError,RuntimeError) as error:
    out.update(status='PARTIAL_COVERAGE',action_run_count=None,accepted_action_run_count=None)
    out['limitations'].append(getattr(error,'status','HISTORY_UNAVAILABLE'))
   out.update(retained_trigger_attempt_count=len(trace_runs),retained_attempts=trace_runs[:20])
  out['last_action_run_requested_at']=last_value
  out['latest_retained_attempt']=max(trace_runs,key=lambda r:epoch(r['started_at'])) if trace_runs else None
  out['limitations'].append('The installed automation recorder excludes last_triggered; counts use logbook execution events, not automation enabled-state samples.')
  out['semantics']='HA last_triggered records accepted action-script invocation after automation conditions; not every trigger, completion or physical execution. Trace attempts can include failed_conditions.'
  if e['domain']=='script':out['semantics']='HA script last_triggered records accepted invocation, not completion or physical outcome.'
  out['limitations'].append('HA normally retains only the latest five traces per program unless configured otherwise; retained attempts are a lower bound, not a complete count.')
  out['source_refs']=[{'source':'ha_logbook','entity_id':e['entity_id']},{'source':'ha_entity_attribute','entity_id':e['entity_id'],'attribute':'last_triggered'},{'source':'ha_trace_list','entity_id':e['entity_id']}]
  if last and not last_value and not trace_runs:out['status']='NO_DATA'
  return out
 def reliable_presence(self,samples,window):
  result=[];max_age=getattr(self.presence,'max_age_seconds',3600)
  for i,sample in enumerate(samples):
   point=dict(sample)
   if i==0 and point['timestamp']<=window.start.timestamp():point['value']='UNKNOWN';point['reason']='boundary_report_freshness_unknown'
   result.append(point)
   next_at=samples[i+1]['timestamp'] if i+1<len(samples) else window.end.timestamp()
   expired=point['timestamp']+max_age
   if point['value']!='UNKNOWN' and expired<next_at:
    result.append({'timestamp':expired,'time':datetime.fromtimestamp(expired,timezone.utc).isoformat(),'value':'UNKNOWN','unit':None,'context':{},'reason':'presence_evidence_expired'})
  return result

 async def query(self,operation,target,window='last 24 hours',options=None,**kwargs):
  started=time.perf_counter();qid=uuid.uuid4().hex;options={**(options or {}),**kwargs};record={'id':qid,'operation':operation,'target_phrase':'[redacted]' if re.search(r'(?i)password|secret|token|bearer|api.?key|[A-Za-z0-9_-]{40,}',target) else target,'cache_hit':False};result=None
  query_metrics={};metrics_token=_QUERY_METRICS.set(query_metrics)
  self.count('history_queries_total');self.count('history_operation_'+operation)
  try:
   if operation not in OPS:raise HistoryError('UNSUPPORTED_METRIC','Unsupported historical operation.')
   parsed=self.parser.parse(window);record['window']=parsed.serial()
   key=json.dumps([operation,target,parsed.serial(),options],sort_keys=True,default=str);cached=self.cache.get(key);ttl=10 if parsed.end.timestamp()>time.time()-60 else 120
   if cached and time.monotonic()-cached[0]<ttl:
    self.count('history_cache_hits');record['cache_hit']=True;result=copy.deepcopy(cached[1]);return result
   self.count('history_cache_misses')
   async with query_slot(self.sem):
    async with asyncio.timeout(self.timeout_seconds):
     await self.home.refresh()
     if operation=='presence_history':
      identity=await self.presence.history_identity(target);e=self.home.entities.get(identity['entity_id'],{'entity_id':identity['entity_id'],'name':target,'aliases':[],'domain':'person','area_id':None});mapping=lambda state:self.presence.map_zone(state,identity['home'],identity['office']);samples,notes=await self.series(e,parsed,presence_map=mapping)
      latest_observation=samples[-1]['time'] if samples else None
      samples=self.reliable_presence(samples,parsed)
      result=self.base(operation,e,parsed);events=calc.transitions(samples,parsed.start.timestamp(),parsed.end.timestamp(),options.get('to_state'),options.get('from_state'));cov,_=calc.coverage(samples,parsed.start.timestamp(),parsed.end.timestamp());self.covered(result,cov,parsed);result.update(events=events[:40],transition_count=len(events),series=[{k:s[k] for k in ('time','value')} for s in samples[:48]]);result['latest_available_sample']=latest_observation;result['source_refs']=[{'source':'ha_recorder','entity_id':e['entity_id']}];result['limitations'].append('Historical zones use current configured labels. Synthetic boundary states have unknown report freshness; absent renewed evidence after the Presence Provider age limit becomes UNKNOWN, never AWAY.')
      if options.get('presence'):
       desired=options['presence'];cov,segments=calc.coverage(samples,parsed.start.timestamp(),parsed.end.timestamp(),lambda v:None if v=='UNKNOWN' else v==desired);result['duration']={'seconds':sum(s['end']-s['start'] for s in segments if s['matches']),'presence':desired,'coverage':cov}
     elif operation in ('semantic_events','action_receipts'):
      result=await self.operational(operation,target,parsed,options)
     else:
      domain='automation' if operation.startswith('automation_') else 'script' if operation.startswith('script_') else None;e=self.resolve(target,domain);record['resolved_target']=e['entity_id']
      if e['domain'] in ('person','device_tracker','geo_location') or set(norm(e['entity_id']+' '+e['name']).split()) & {'latitude','longitude','gps','geocoded','location'}:raise HistoryError('UNSUPPORTED_METRIC','Precise location history is outside this milestone; use coarse presence_history.')
      if operation.endswith('_trace'):
       if not options.get('run_id'):raise HistoryError('TRACE_UNAVAILABLE','Specify one retained run_id.')
       result=self.base(operation,e,parsed);result['trace']=await self.traces(e,run_id=options['run_id']);result['source_refs']=[result['trace']['source_ref']]
      elif operation in ('automation_runs','automation_last_run','script_runs','script_last_run'):result=await self.runs(e,parsed,operation.endswith('last_run'))
      elif operation=='last_changed':
       current=await self.ha.get_state(e['entity_id']);result=self.base(operation,e,parsed)
       if options.get('property','state')!='state':
        samples,_=await self.series(e,parsed,options['property']);events=calc.transitions(samples,parsed.start.timestamp(),parsed.end.timestamp());result['last_changed']=events[-1]['time'] if events else None;result['limitations'].append('Property change time is limited to observed transitions inside the requested recorder window.')
       else:result['last_changed']=current.get('last_changed')
       result['state']=current.get('state');result['source_refs']=[{'source':'ha_current_state' if options.get('property','state')=='state' else 'ha_recorder','entity_id':e['entity_id']}]
      elif operation=='compare':
       other=self.parser.parse(options.get('other_window','the night before'));a=await self.calculate('statistics',e,parsed,options);b=await self.calculate('statistics',e,other,options)
       if a.get('unit')!=b.get('unit'):raise HistoryError('UNSUPPORTED_METRIC','Cannot compare incompatible units.')
       result=self.base(operation,e,parsed);result.update(window_a=a,window_b=b,difference_mean=a.get('series_summary',{}).get('mean',0)-b.get('series_summary',{}).get('mean',0));result['status']='OK' if a['status']==b['status']=='OK' else 'PARTIAL_COVERAGE'
       if not all(isinstance(r.get('series_summary',{}).get('mean'),(int,float)) for r in (a,b)):raise HistoryError('NO_DATA','Both windows need numeric evidence for comparison.')
       result['difference_mean']=a['series_summary']['mean']-b['series_summary']['mean']
       result['coverage']={'window_a':a.get('coverage'),'window_b':b.get('coverage')};result['source_refs']=a.get('source_refs',[])+b.get('source_refs',[])
       result['window_durations_equal']=abs(parsed.seconds-other.seconds)<1
       if not result['window_durations_equal']:result['limitations'].append('The compared windows have unequal durations.')
      elif operation=='evidence':result=await self.evidence(e,parsed,options)
      else:result=await self.calculate(operation,e,parsed,options)
   self.cache[key]=(time.monotonic(),copy.deepcopy(result));self.cache.move_to_end(key)
   while len(self.cache)>24:self.cache.popitem(last=False)
   return result
  except HistoryError as error:
   result={'schema_version':1,'status':error.status,'question_type':operation,'message':str(error),'details':error.details,'model_calls':0,'limitations':['Missing evidence is not evidence that nothing happened.']};self.count('history_no_data' if error.status=='NO_DATA' else 'history_failures');return result
  except asyncio.TimeoutError:
   result={'status':'TIMEOUT','message':'Historical evidence timed out; narrow the request.','model_calls':0};self.count('history_failures');return result
  except Exception as error:
   result={'status':'HISTORY_UNAVAILABLE','message':'Historical source unavailable.','error_type':type(error).__name__,'model_calls':0};self.count('history_failures');return result
  finally:
   from output import current_output
   output=current_output.get()
   if output:
    if not getattr(output,'historical_retrieved',False):output.history_base_model_calls=output.model_calls
    output.historical_retrieved=True
   record.update(query_metrics=dict(query_metrics),normalization=(result or {}).get('normalization'),resampling=(result or {}).get('resampling'),latest_available_sample=(result or {}).get('latest_available_sample'),duration_ms=(time.perf_counter()-started)*1000,status=(result or {}).get('status','cancelled'),source=(result or {}).get('source_refs',[]),coverage=(result or {}).get('coverage'),model_calls=0)
   self.count('history_query_latency_ms_total',record['duration_ms']);self.diagnostics.execute('insert or replace into queries values(?,?,?)',(qid,time.time(),json.dumps(bounded(record))));self.diagnostics.execute('delete from queries where id in(select id from queries order by at desc limit -1 offset 1000)');self.diagnostics.commit();_QUERY_METRICS.reset(metrics_token)
 async def calculate(self,op,e,window,options):
  numeric=e['domain']=='sensor' and e.get('attributes',{}).get('device_class') in ('temperature','humidity','pressure','illuminance','power','energy','water','gas','monetary')
  if op=='statistics' and numeric and window.seconds>2*86400:
   try:return await self.statistic_source(e,window)
   except HistoryError as error:
    if error.status!='NO_DATA' or window.seconds>self.raw_days*86400:raise
  if op=='statistics' and e['domain'] not in ('sensor','input_number','counter') and options.get('property','state')=='state':raise HistoryError('UNSUPPORTED_METRIC','Statistics require numeric states or an explicit numeric property.')
  samples,notes=await self.series(e,window,options.get('property','state'));out=self.base(op,e,window);out['source_refs']=[{'source':'ha_recorder','entity_id':e['entity_id']}];out['normalization']=notes
  if len(notes['units'])>1:raise HistoryError('UNSUPPORTED_METRIC','Units changed inside this window; an explicit conversion is required.')
  unit=next(iter(notes['units']),e.get('attributes',{}).get('unit_of_measurement'))
  if options.get('property') in ('temperature','current_temperature') and not unit:
   config=await self.ha.request('GET','/api/config');unit=config.get('unit_system',{}).get('temperature')
   if not unit:raise HistoryError('UNSUPPORTED_METRIC','Temperature unit metadata unavailable; refusing an ambiguous numeric summary.')
  if options.get('property')=='percentage':unit='%'
  if options.get('property')=='brightness':unit='HA scale 0–255'
  if options.get('property')=='volume_level':unit='fraction'
  out['unit']=unit
  start,end=window.start.timestamp(),window.end.timestamp()
  if op=='statistics':
   def valid(v):
    try:return True if math.isfinite(float(v)) else None
    except (ValueError,TypeError):return None
   cov,_=calc.coverage(samples,start,end,valid);self.covered(out,cov,window);out['series_summary']=calc.numeric(samples,start,end,unit,e.get('attributes',{}).get('state_class'));out['series']=calc.buckets(samples,start,end,self.max_points);out['resampling']={'method':'time_buckets' if len(samples)>self.max_points else 'recorded_states','max_points':self.max_points,'interpolated':False}
   if out['series_summary'].get('status')=='NO_DATA':out['status']='NO_DATA'
   if cov['available_fraction']<.8:out['series_summary']['trend']='INSUFFICIENT_DATA'
  elif op=='duration':
   out['duration']=calc.durations(samples,start,end,e['domain'],options.get('state','on'))
   if options.get('property')=='hvac_action':out['duration']['semantics']='HA reported HVAC action intervals; not independently measured compressor runtime'
   self.covered(out,out['duration']['coverage'],window)
  elif op in ('transitions','count_transitions','first_changed'):
   events=calc.transitions(samples,start,end,options.get('to_state') if e['domain']!='climate' else None,options.get('from_state'))
   if e['domain']=='climate' and options.get('to_state') in ('on','off'):
    matches=calc.condition('climate',options['to_state']);events=[event for event in events if matches(event['new']) is True and matches(event['old']) is False]
   out.update(events=events[:40],transition_count=len(events),more=len(events)>40)
   if op=='first_changed':out['first_changed']=events[0] if events else None
   cov,_=calc.coverage(samples,start,end);self.covered(out,cov,window)
  else:
   offset=max(0,min(6000,int(options.get('offset',0))));limit=max(1,min(80,int(options.get('limit',48))));out.update(samples=[{k:s[k] for k in ('time','value','unit','context')} for s in samples[offset:offset+limit]],offset=offset,total=len(samples),more=len(samples)>offset+limit)
   cov,_=calc.coverage(samples,start,end);self.covered(out,cov,window)
  self.count('history_samples_returned',len(out.get('samples',out.get('series',out.get('events',[])))));out['latest_available_sample']=samples[-1]['time'] if samples else None
  return out
 async def operational(self,op,target,window,options):
  if not self.world:raise HistoryError('HISTORY_UNAVAILABLE','Operational evidence store is not enabled.')
  subject=self.resolve(target)['entity_id'] if not target.startswith('person:') else target
  if op=='semantic_events':
   rows=self.world.store.db.execute('select data from events where subject=? and occurred>=? and occurred<? order by occurred desc limit 40',(subject,window.start.timestamp(),window.end.timestamp())).fetchall();items=[json.loads(r[0]) for r in rows]
  else:
   rows=self.world.store.db.execute("select data from actions where observed>=? and observed<? and exists(select 1 from json_each(actions.data,'$.targets') where value=?) order by observed desc limit 21",(window.start.timestamp(),window.end.timestamp(),subject)).fetchall();items=[json.loads(r[0]) for r in rows[:20]]
  extra={'more':len(rows)>20} if op=='action_receipts' else {'more':len(rows)>=40}
  extra.update({'untimed_legacy_receipt_references':self.legacy_receipts(subject,window)} if op=='action_receipts' else {})
  return {'schema_version':1,'status':'PARTIAL_COVERAGE','question_type':op,'window':window.serial(),'items':items,**extra,'limitations':['Sunday operational retention covers only observed/retained evidence; it is not a complete HA history.'],'model_calls':0}
 def legacy_receipts(self,subject,window):
  path=self.state_dir/'fast_mode.db'
  if not path.exists():return []
  result=[]
  try:
   db=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True)
   try:
    for request_id,encoded in db.execute('select id,data from attempts order by rowid desc limit 200'):
     row=json.loads(encoded)
     if not row.get('turn_completed'):continue
     for tool in row.get('actual_tools',[]):
      args=tool.get('arguments',{})
      if tool.get('name')!='home_action' or subject not in args.get('entity_ids',[]):continue
      try:outcome=json.loads(tool.get('result','{}'))
      except (ValueError,TypeError):continue
      if outcome.get('service_completed') is not True:continue
      observed=next((s for s in outcome.get('observed',[]) if s.get('entity_id')==subject),None)
      if not observed:continue
      at=epoch(observed.get('last_changed'))
      if not window.start.timestamp()<=at<window.end.timestamp():continue
      result.append({'request_id':request_id,'service':args.get('domain','')+'.'+args.get('service',''),'path':'fast_mode' if row.get('handled') else 'normal_tool','service_completed':True,'observed_state':observed.get('state'),'observed_state_changed_at':observed.get('last_changed'),'context':observed.get('context'),'request_time':None,'attribution':'POSSIBLE','reason':'Older receipt has no request/dispatch timestamp or authoritative service context; observed state reference is not proof of action timing or causality.'})
      if len(result)>=5:return result
   finally:db.close()
  except (sqlite3.Error,ValueError):return []
  return result

 def trace_link(self,trace,transition,eid):
  statectx=transition.get('context') or {};ctx=trace.get('context') or {};link=bool(ctx.get('id') and (statectx.get('id')==ctx['id'] or statectx.get('parent_id')==ctx['id']))
  actions=[]
  for step in trace.get('steps',[]):
   action=step.get('action') or {};targets=action.get('entity_ids') or (action.get('target') or {}).get('entity_id') or []
   if isinstance(targets,str):targets=[targets]
   if eid not in targets or step.get('error_type'):continue
   service=action.get('service');new=transition.get('new');domain=eid.split('.')[0]
   matches=False
   if service in ('turn_on','turn_off'):
    try:matches=calc.condition(domain,'on' if service=='turn_on' else 'off')(new) is True
    except HistoryError:pass
   elif service=='set_hvac_mode':matches=new==(action.get('values') or {}).get('hvac_mode')
   if matches:actions.append(action)
  status='CONFIRMED' if link and actions else 'STRONG' if link else 'POSSIBLE' if abs(epoch(trace.get('started_at'))-epoch(transition.get('time')))<=10 else 'UNKNOWN'
  return {'attribution':status,'trace_ref':trace.get('source_ref'),'context_id':ctx.get('id'),'actions':actions[:3],'reason':'Explicit trace context and recorded target action link to this HA transition' if status=='CONFIRMED' else 'Shared context without a recorded target action' if status=='STRONG' else 'Temporal proximity only; not a causal attribution' if status=='POSSIBLE' else 'No causal linkage'}
 async def evidence(self,e,window,options):
  out=self.base('evidence',e,window);out['programs']=[]
  try:out['transitions']=await self.calculate('transitions',e,window,options)
  except HistoryError as error:out['transitions']={'status':error.status,'events':[],'message':str(error)}
  linked=[p for p in self.home.related([e['entity_id']],8) if p['domain'] in ('automation','script')][:3]
  for p in linked:
   entity=self.home.entities.get(p['entity_id'])
   if not entity:continue
   runs=await self.runs(entity,window);attempts=runs.get('retained_attempts',[]);traces=[]
   for run in attempts[-2:]:
    try:traces.append(await self.traces(entity,run_id=run['run_id']))
    except HistoryError:pass
   out['programs'].append({'entity_id':p['entity_id'],'name':p['name'],'runs':{k:runs.get(k) for k in ('status','accepted_action_run_count','last_action_run_requested_at','latest_retained_attempt','semantics','limitations','source_refs')},'traces':traces})
  for name,op in (('operational_events','semantic_events'),('action_receipts','action_receipts')):
   try:out[name]=await self.operational(op,e['entity_id'],window,{})
   except HistoryError as error:out[name]={'status':error.status,'items':[]}
  out['related_signals']=[]
  sensors=[s for s in self.home.entities.values() if s['domain']=='sensor' and ordinary(s) and s.get('area_id')==e.get('area_id') and s.get('attributes',{}).get('device_class')=='temperature']
  if len(sensors)==1:
   try:out['related_signals'].append(await self.calculate('statistics',sensors[0],window,{}))
   except HistoryError as error:out['limitations'].append('Related temperature: '+error.status)
  out['source_refs']=out.get('transitions',{}).get('source_refs',[])+[ref for program in out['programs'] for ref in program.get('runs',{}).get('source_refs',[])]
  out['causal_links']=[]
  for transition in out['transitions'].get('events',[]):
   context=transition.get('context',{});attribution=self.world.attribute(context,e['entity_id']) if self.world else {'attribution':'UNKNOWN'}
   trace_links=[self.trace_link(trace,transition,e['entity_id']) for program in out['programs'] for trace in program['traces']]
   confirmed=next((link for link in trace_links if link['attribution']=='CONFIRMED'),None)
   if confirmed and attribution.get('attribution')!='CONFIRMED':
    program=confirmed['trace_ref']['entity_id'];attribution={'initiator':{'type':'HA_AUTOMATION' if program.startswith('automation.') else 'HA_SCRIPT','id':program},'attribution':'CONFIRMED','evidence':[confirmed],'physical_success_confirmed':False}
   out['causal_links'].append({'transition_time':transition['time'],'context':context,'provenance':attribution,'trace_links':[l for l in trace_links if l['attribution']!='UNKNOWN'][:3],'temporal_proximity_is_not_causality':True})
  out['status']='PARTIAL_COVERAGE'
  out['coverage']={'state_history':out['transitions'].get('coverage'),'traces':'retained attempts only','operational':'observed/retained records only'}
  out['limitations'].append('Related program definitions and nearby runs are not proof of cause; only linked context/recorded actions support attribution.')
  return out
 def projection(self,result):
  result=copy.deepcopy(result)
  if len(json.dumps(result,default=str))<=14000:return result
  result.pop('samples',None);result.pop('series',None)
  for key in ('window_a','window_b'):
   if isinstance(result.get(key),dict):result[key].pop('series',None)
  if 'related_signals' in result:
   for signal in result['related_signals']:signal.pop('series',None)
  if 'programs' in result:result['programs']=result['programs'][:1]
  if len(json.dumps(result,default=str))>14000:
   result={k:result[k] for k in ('schema_version','status','question_type','target','window','series_summary','duration','coverage','limitations','source_refs','causal_links') if k in result}
   if 'causal_links' in result:result['causal_links']=result['causal_links'][:3]
  if len(json.dumps(result,default=str))>14000:return {'status':'PARTIAL_COVERAGE','message':'Evidence exceeds the response budget; narrow the window or operation.','model_calls':0}
  return result
 def fast_reply(self,result,plan):
  if result.get('status')!='OK':return None
  op=plan.operation;unit=result.get('unit') or '';name=result.get('target',{}).get('name','It');summary=result.get('series_summary',{})
  metric=(plan.options or {}).get('requested_metric')
  if op=='statistics' and metric and summary.get(metric) and 'counter_delta' not in summary:
   entry=summary[metric];return f"The {'highest' if metric=='maximum' else 'lowest'} recorded {name.lower()} was {entry['value']:g}{unit}."
  if op=='count_transitions':return f"{name} turned {(plan.options or {}).get('to_state','on')} {result['transition_count']} times in that period."
  if op=='duration':
   seconds=result['duration']['seconds'];h=int(seconds//3600);m=int(seconds%3600//60);qualifier='was set '+(plan.options or {}).get('state','on') if result.get('target',{}).get('domain')=='climate' and (plan.options or {}).get('state','on') in ('on','off') else 'reported '+(plan.options or {}).get('state','on')
   return f'{name} {qualifier} for {h} hours and {m} minutes in that period.'
  return None
 def record_answer(self,normal=False,model_calls=0):
  self.count('history_normal_sunday_answers' if normal else 'history_fast_mode_answers');self.count('history_llm_calls_after_retrieval',model_calls);self.diagnostics.commit()
 def close(self):self.diagnostics.commit();self.diagnostics.close()
