"""Memory-backed boundaries are permission to recheck, never permission to notify."""
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
from .store import digest
class Expectations:
 def __init__(self,store,scheduler,memories,enabled=lambda:False,tz='Asia/Kolkata'):
  self.store=store;self.scheduler=scheduler;self.memories=memories;self.allowed=enabled;self.tz=ZoneInfo(tz)
 def eligible(self,m):
  s=m.get('structured_value') or {};stats=s.get('statistics') or {}
  try:
   recalculated=datetime.fromisoformat(stats['last_recalculation'].replace('Z','+00:00')).timestamp();evidence_end=datetime.fromisoformat((s.get('evidence_window') or stats.get('window'))['end'].replace('Z','+00:00')).timestamp()
   fresh=-300<=self.store.clock()-recalculated<=14*86400 and -300<=self.store.clock()-evidence_end<=90*86400
  except (ValueError,TypeError,KeyError,AttributeError):fresh=False
  return self.allowed() and fresh and m.get('type')=='PATTERN_EXPECTATION' and m.get('status')=='ACTIVE' and m.get('confidence',0)>=.65 and stats.get('sample_count',0)>=8 and stats.get('strong_enough') is True and bool(s.get('temporal_boundaries'))
 def reconcile(self,memories,active_sessions=None):
  if not self.allowed():
   for r in self.store.rows('temporal',('PENDING',),1000):
    if r['data']['kind']=='EXPECTATION_BOUNDARY':self.scheduler.cancel(r['id'],'Memory integration disabled')
   return []
  now=self.store.clock();active_sessions=active_sessions or {};valid={m['memory_id']:m for m in memories if self.eligible(m)}
  for r in self.store.rows('temporal',('PENDING',),1000):
   if r['data']['kind']=='EXPECTATION_BOUNDARY':
    m=valid.get(r['data'].get('source_memory_id'))
    if not m or m['updated_at']!=r['data']['source_memory_version']:self.scheduler.cancel(r['id'],'Memory missing/inactive/version changed')
  created=[]
  for mid,m in valid.items():
   s=m['structured_value'];group=s.get('group',[])
   if len(group)<5:continue
   kind,subject,zone,weekday,area=group;bounds=s['temporal_boundaries'];local=datetime.fromtimestamp(now,self.tz)
   for offset in range(8):
    day=local.date()+timedelta(days=offset)
    if day.strftime('%A')!=weekday:continue
    if kind=='presence_session' and zone=='OFFICE' and bounds.get('arrival_after_local_minutes') is not None:
     minute=bounds['arrival_after_local_minutes']
     if not isinstance(minute,(int,float)) or not 0<=minute<1440:continue
     target=datetime.combine(day,datetime.min.time(),self.tz)+timedelta(minutes=minute)
     # No ambiguous/nonexistent civil-time boundary: require round-trip and unique fold offset.
     if target.astimezone(timezone.utc).astimezone(self.tz).replace(tzinfo=None)!=target.replace(tzinfo=None) or target.replace(fold=0).utcoffset()!=target.replace(fold=1).utcoffset():continue
     at=target.timestamp()
     if at<now:continue
     payload={'source_memory_id':mid,'source_memory_version':m['updated_at'],'expectation_kind':'arrival','subject':subject,'zone':zone,'day':str(day),'context':{'weekday':weekday,'area':area,'timezone':str(self.tz)},'required_recheck':['memory_version','provider_freshness','presence_history'],'evidence_refs':m['evidence_refs'][:8]}
     created.append(self.scheduler.add(at,'EXPECTATION_BOUNDARY','SUNDAY_SELF',payload,['expectation',mid,m['updated_at'],'arrival',str(day)]));break
    if kind=='device_session' and subject in active_sessions:
     session=active_sessions[subject]
     if session.get('weekday')!=weekday or not session.get('fresh'):continue
     duration=bounds.get('duration_minutes')
     if not isinstance(duration,(int,float)) or not 0<duration<=10080:continue
     at=session['started_at']+duration*60
     if at<now-60:continue
     payload={'source_memory_id':mid,'source_memory_version':m['updated_at'],'expectation_kind':'duration','subject':subject,'session_start':session['started_at'],'context':{'weekday':weekday,'area':area},'required_recheck':['memory_version','fresh_state','same_session'],'evidence_refs':m['evidence_refs'][:8]}
     created.append(self.scheduler.add(max(now,at),'EXPECTATION_BOUNDARY','SUNDAY_SELF',payload,['expectation',mid,m['updated_at'],'duration',session['started_at']]));break
  return created
 async def due(self,row,recheck):
  p=row['data']
  if not self.allowed():return {'state':'CANCELLED','reason':'Global or integration memory reads disabled'}
  m=self.memories(p['source_memory_id'])
  if not m or not self.eligible(m) or m['updated_at']!=p['source_memory_version']:return {'state':'CANCELLED','reason':'Source memory or version no longer eligible'}
  evidence=await recheck(p)
  if not isinstance(evidence,dict) or not evidence.get('fresh') or not evidence.get('healthy') or evidence.get('presence')=='UNKNOWN':return {'state':'UNKNOWN','reason':'Fresh authoritative evidence is insufficient'}
  if p['expectation_kind']=='arrival':
   if evidence.get('arrival_occurred'):return {'state':'SATISFIED','reason':'Arrival recorded before boundary'}
   # Absence requires complete positively-supported history, never a failed/no-result query.
   violation=evidence.get('complete_history') is True and evidence.get('arrival_occurred') is False and evidence.get('presence') in ('HOME','AWAY')
  else:
   if evidence.get('active') is False:return {'state':'SATISFIED','reason':'Device no longer active'}
   violation=evidence.get('active') is True and evidence.get('session_start')==p.get('session_start')
  if not violation:return {'state':'UNKNOWN','reason':'Cannot establish a violation'}
  # Revalidate again after asynchronous factual work: consolidation may have changed the pattern.
  latest=self.memories(p['source_memory_id'])
  if not latest or not self.eligible(latest) or latest['updated_at']!=p['source_memory_version']:return {'state':'CANCELLED','reason':'Source changed during recheck'}
  latest_wake=self.store.get('temporal',row['id'])
  if latest_wake and latest_wake['state']=='CANCELLED':return {'state':'CANCELLED','reason':'Cancelled during factual recheck'}
  incident='expectation:'+row['id'];wake=self.store.wake('EXPECTATION_VIOLATED','SUNDAY_SELF',p['subject'],[row['id']],incident,memory_refs=[{'id':m['memory_id'],'version':m['updated_at']}],history_refs=evidence.get('refs',[])[:8],expectation_evidence=evidence)
  return {'state':'VIOLATED','wake_id':wake}
