"""Deterministic measurements only: no model, GPS, preferences or notifications."""
from datetime import datetime,timezone,timedelta
from zoneinfo import ZoneInfo
import statistics,math
from .store import key,now,js
UNKNOWN={None,'UNKNOWN','unknown','unavailable',''}

def sessions(samples,subject,kind,*,zone='Asia/Kolkata',area=None,source_ref=None,domain=None,initial=None,return_state=False):
 """Only completed intervals with observed entry and exit; gaps never imply AWAY/OFF."""
 rows=sorted(samples,key=lambda s:s['timestamp']);result=[];opened=(initial or {}).get('opened');previous=(initial or {}).get('previous');cursor=(initial or {}).get('cursor')
 if cursor is None:cursor=float('-inf')
 for s in rows:
  value=s.get('value');at=float(s['timestamp'])
  if at<=cursor:continue
  cursor=at
  if value in UNKNOWN:
   opened=None;previous=None;continue
  if kind=='presence_session':state=value if value in {'HOME','OFFICE','AWAY'} else None
  elif domain=='media_player':state='ACTIVE' if value in {'on','playing','idle','paused','buffering'} else 'INACTIVE' if value in {'off','standby'} else None
  else:state='ACTIVE' if value=='on' else 'INACTIVE' if value=='off' else None
  if state is None:opened=None;previous=None;continue
  if previous is None:
   previous=state;continue # Initial/boundary sample never establishes an arrival/start.
  if state==previous:continue
  if opened is not None:
   start,oldstate=opened;duration=at-start
   if duration>0:
    a=datetime.fromtimestamp(start,ZoneInfo(zone));b=datetime.fromtimestamp(at,ZoneInfo(zone));result.append({'kind':kind,'subject':subject,'zone':oldstate if kind=='presence_session' else None,'state':oldstate,'area':area,'start':datetime.fromtimestamp(start,timezone.utc).isoformat(),'end':datetime.fromtimestamp(at,timezone.utc).isoformat(),'duration_minutes':duration/60,'weekday':a.strftime('%A'),'start_local_minutes':a.hour*60+a.minute+a.second/60,'end_local_minutes':b.hour*60+b.minute+b.second/60,'timezone':zone,'domain':domain,'quality':'complete_observed_boundaries','source_ref':source_ref,'measurement_semantics':'HA reported state duration; not proof of physical activity or user preference'})
  opened=(at,state) if kind=='presence_session' or state=='ACTIVE' else None
  previous=state
 return (result,{'opened':opened,'previous':previous,'cursor':cursor if math.isfinite(cursor) else None}) if return_state else result

def quantile(xs,p):
 xs=sorted(xs);pos=(len(xs)-1)*p;a=int(pos);b=min(a+1,len(xs)-1);return xs[a]+(xs[b]-xs[a])*(pos-a)
def statistics_for(evidence,min_samples=8):
 if not evidence:return None
 values=[e['duration_minutes'] for e in evidence];arrivals=[e['start_local_minutes'] for e in evidence]
 if any(not math.isfinite(v) or v<=0 for v in values):raise ValueError('Invalid measured duration')
 n=len(values);median=statistics.median(values);low=quantile(values,.1);high=quantile(values,.9);iqr=quantile(values,.75)-quantile(values,.25)
 # Empirical tail plus observed spread; equal samples yield their observed boundary, not a made-up duration.
 boundary=high+1.5*iqr;spread=(high-low)/max(1,median);recent=values[-max(3,n//3):];old=values[:max(1,n-len(recent))];shift=abs(statistics.median(recent)-statistics.median(old))/max(1,median)
 certainty=min(.92,n/(n+4))/(1+min(2,spread)*.25+min(2,shift))
 if n<min_samples:certainty=min(.35,certainty)
 # Arrival windows crossing midnight are withheld rather than asserting a misleading linear clock window.
 circular= max(arrivals)-min(arrivals)>720
 dates=sorted({e['start'][:10] for e in evidence});days=max(1,(datetime.fromisoformat(max(e['end'] for e in evidence))-datetime.fromisoformat(min(e['start'] for e in evidence))).total_seconds()/86400)
 return {'sample_count':n,'metric':'session_duration_minutes','median':median,'typical_low':low,'typical_high':high,'unusual_boundary':boundary,'arrival_window_minutes':None if circular else [quantile(arrivals,.1),quantile(arrivals,.9)],'unusual_arrival_after_minutes':None if circular else quantile(arrivals,.9)+1.5*(quantile(arrivals,.75)-quantile(arrivals,.25)),'confidence':round(certainty,4),'strong_enough':n>=min_samples and certainty>=.65,'change_score':shift,'observed_dates':dates,'sessions_per_observed_day':n/len(dates),'observed_window_days':days,'window':{'start':min(e['start'] for e in evidence),'end':max(e['end'] for e in evidence)},'last_recalculation':now(),'small_sample':n<min_samples,'interpretation':'probabilistic reported-state expectation, not a preference or obligation'}

def learn_patterns(store,*,min_samples=8,climate_learning=False,window_days=90):
 cutoff=(datetime.now(timezone.utc)-timedelta(days=window_days)).isoformat();groups={}
 for row in store.db.execute("select id,data from evidence where kind in ('device_session','presence_session') order by at,id"):
  import json
  e=json.loads(row['data'])
  if not getattr(store,'scope_filter',lambda data:True)(e):continue
  if e.get('start','')<cutoff or e.get('quality')!='complete_observed_boundaries':continue
  if e.get('domain')=='climate' and not climate_learning:continue
  group=(e['kind'],e['subject'],e.get('zone'),e.get('weekday'),e.get('area'));groups.setdefault(group,[]).append((row['id'],e))
 results=[]
 for group,items in groups.items():
  items.sort(key=lambda x:x[1]['start']);items=items[-64:];stats=statistics_for([e for _,e in items],min_samples)
  if getattr(store,'v2_enabled',False):
   store.evidence('pattern_statistics','ha_recorder',{'group':list(group),'statistics':stats,'evidence_refs':[ref for ref,_ in items],'interpretation':'Measured evidence only; semantic usefulness and independent support required for a belief.'},'pattern-statistics:'+key([group,stats['window'],[ref for ref,_ in items]]))
   continue
  if stats['sample_count']<2:continue
  # The source class remains measured pattern evidence, never inferred user preference.
  subject='pattern:'+key(group)[:24];structured={'group':list(group),'statistics':stats,'sample_count':stats['sample_count'],'temporal_boundaries':{'duration_minutes':stats['unusual_boundary'],'arrival_after_local_minutes':stats['unusual_arrival_after_minutes']} if stats['strong_enough'] else {},'evidence_window':stats['window']}
  c={'type':'PATTERN_EXPECTATION','subject':subject,'statement':f"Observed {group[1]} {group[0]} durations on {group[3]} have a measured median of {stats['median']:.1f} minutes across {stats['sample_count']} sessions.",'structured_value':structured,'confidence':stats['confidence'],'entities':[group[1]] if group[0]=='device_session' else [],'people':[group[1]] if group[0]=='presence_session' else [],'areas':[group[4]] if group[4] else [],'tags':['behavior',group[3],group[2] or 'device']}
  cid=store.submit(c,'HISTORICAL_PATTERN',[ref for ref,_ in items][-64:],['pattern',group,key([ref for ref,_ in items])]);related=store.related(c);existing=next((m for m in related if m['subject']==subject and m['type']=='PATTERN_EXPECTATION'),None)
  proposal={'operation':'UPDATE' if existing else 'ADD','reason':'Recalculated bounded measured distribution; no LLM arithmetic or inferred preference.'}
  if existing:proposal['target_id']=existing['memory_id']
  results.append(store.reconcile(cid,proposal,[m['memory_id'] for m in related],{'model':'deterministic-reducer-v1','tokens':0,'statistics':stats}))
 # Patterns without renewed support become weaker, preserving their prior versions and evidence.
 with store.lock,store.db:
  for row in store.db.execute("select data from memories where type='PATTERN_EXPECTATION' and status='ACTIVE'").fetchall():
   import json
   m=json.loads(row[0]);end=((m.get('structured_value') or {}).get('evidence_window') or {}).get('end')
   if end and end<cutoff and m['confidence']>.2:
    m['confidence']=.2;m['updated_at']=now();m['structured_value']['temporal_boundaries']={};m['tags']=sorted(set(m.get('tags',[])+['stale']));store._put(m,'STALE_PATTERN')
 return results

def temporal_boundaries(memory):
 if memory.get('status')!='ACTIVE' or memory.get('type')!='PATTERN_EXPECTATION':return []
 s=memory.get('structured_value') or {};stats=s.get('statistics') or {}
 if memory['confidence']<.65 or not stats.get('strong_enough'):return []
 # Data-only interface. Does not create timers, WATCHes, commitments or messages.
 return [{'memory_id':memory['memory_id'],'subject':memory['subject'],'context':s.get('group'),'kind':kind,'value':value,'confidence':memory['confidence'],'evidence_refs':memory['evidence_refs'],'requires_factual_recheck':True,'notification_authority':False} for kind,value in s.get('temporal_boundaries',{}).items() if value is not None]

def measured_response(samples,*,subject,trigger_at,threshold,direction,trigger_ref,source_ref,window_end):
 """Objective elapsed time to an explicitly supplied threshold after a recorded trigger.

 No causal claim is made. Unknown samples interrupt measurement; a timeout is not
 evidence of no response. Thresholds come from the query/evidence, not an LLM.
 """
 if direction not in ('above','below') or not trigger_ref:raise ValueError('Explicit direction and trigger evidence required')
 rows=sorted((s for s in samples if trigger_at<=s['timestamp']<=window_end),key=lambda s:s['timestamp'])
 for s in rows:
  try:value=float(s['value'])
  except (ValueError,TypeError):return None
  if not math.isfinite(value):return None
  if value>=threshold if direction=='above' else value<=threshold:
   return {'kind':'action_response_duration','subject':subject,'trigger_at':datetime.fromtimestamp(trigger_at,timezone.utc).isoformat(),'observed_at':datetime.fromtimestamp(s['timestamp'],timezone.utc).isoformat(),'duration_seconds':s['timestamp']-trigger_at,'threshold':threshold,'direction':direction,'trigger_ref':trigger_ref,'source_ref':source_ref,'causality':'temporal association only; not established cause','quality':'observed_threshold_crossing'}
 return None


def ingest_sessions(store,samples,subject,kind,*,window_start,window_end,**options):
 """Persist compact in-progress reducers across overlapping authoritative queries."""
 cursor_key='session_cursor:'+key([kind,subject]);initial=store.meta(cursor_key)
 if initial and initial.get('covered_until',0)<window_start:
  initial=None # A gap beyond available history cannot support a continuous session.
 evidence,state=sessions(samples,subject,kind,initial=initial,return_state=True,**options)
 for item in evidence:
  ref='session:'+key([kind,subject,item['start'],item['end'],item['state']])
  store.evidence(kind,'ha_recorder',item,ref)
 state['covered_until']=window_end
 store.set_meta(cursor_key,state)
 return evidence
