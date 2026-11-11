"""Local reported-state sessions and coverage. No models or household actions."""
import calendar,hashlib,json,math,os,sqlite3,statistics,time
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo

def dump(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,allow_nan=False)
def ident(*x):return hashlib.sha256(dump(x).encode()).hexdigest()
def classify(domain,value):
 if value is None or str(value).lower() in ('unknown','unavailable','none',''):return None
 value=str(value)
 if domain=='presence':return value if value in ('HOME','OFFICE','AWAY') else None
 if domain in ('sensor','number','input_number'):
  try:
   n=float(value);return n if math.isfinite(n) else None
  except (ValueError,TypeError):return None
 if domain=='climate':return 'active' if value in ('cool','heat','heat_cool','auto','dry','fan_only') else 'inactive' if value=='off' else None
 if domain=='media_player':return 'active' if value in ('on','playing','paused','idle','buffering') else 'inactive' if value in ('off','standby') else None
 if domain=='vacuum':return 'active' if value in ('cleaning','returning') else 'inactive' if value in ('idle','docked','paused','off') else None
 if domain=='cover':return 'active' if value=='open' else 'inactive' if value=='closed' else None
 if domain=='lock':return 'active' if value=='locked' else 'inactive' if value=='unlocked' else None
 if domain=='water_heater':return 'inactive' if value in ('off','eco_off') else 'active'
 return 'active' if value=='on' else 'inactive' if value=='off' else None

def is_session(domain,value):return domain=='presence' and value is not None or domain not in ('sensor','number','input_number') and value=='active'
def quantile(values,p):
 if not values:return None
 xs=sorted(values);pos=(len(xs)-1)*p;a=int(pos);b=min(a+1,len(xs)-1);return xs[a]+(xs[b]-xs[a])*(pos-a)
def timing(values):
 if not values:return None
 angle=math.atan2(sum(math.sin(x*math.tau/1440) for x in values),sum(math.cos(x*math.tau/1440) for x in values));center=(angle*1440/math.tau)%1440
 offsets=[(x-center+720)%1440-720 for x in values];lo=quantile(offsets,.1);hi=quantile(offsets,.9)
 return {'start_minutes':round((center+lo)%1440,1),'end_minutes':round((center+hi)%1440,1),'width_minutes':round(hi-lo,1),'crosses_midnight':(center+lo)%1440>(center+hi)%1440}

class Store:
 def __init__(self,path,tz='Asia/Kolkata',clock=time.time):
  self.clock=clock;self.tz=ZoneInfo(tz);self.db=sqlite3.connect(path,timeout=2);self.db.row_factory=sqlite3.Row;os.chmod(path,0o600)
  version=self.db.execute('pragma user_version').fetchone()[0]
  if version not in (0,1):raise ValueError('Unsupported patterns schema')
  self.db.execute('pragma journal_mode=WAL');self.db.execute('pragma synchronous=FULL')
  self.db.executescript('''CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS subjects(id TEXT PRIMARY KEY,data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,subject TEXT NOT NULL,value TEXT NOT NULL,start REAL NOT NULL,end REAL,start_known INTEGER NOT NULL,end_known INTEGER NOT NULL,quality TEXT NOT NULL,start_ref TEXT,end_ref TEXT,source TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS session_subject_time ON sessions(subject,start);
CREATE TABLE IF NOT EXISTS coverage(id TEXT PRIMARY KEY,subject TEXT NOT NULL,start REAL NOT NULL,end REAL,reason TEXT);
CREATE INDEX IF NOT EXISTS coverage_subject_time ON coverage(subject,start);
CREATE TABLE IF NOT EXISTS observations(subject TEXT NOT NULL,bucket REAL NOT NULL,low REAL NOT NULL,high REAL NOT NULL,total REAL NOT NULL,count INTEGER NOT NULL,PRIMARY KEY(subject,bucket));
CREATE TABLE IF NOT EXISTS rollups(subject TEXT NOT NULL,kind TEXT NOT NULL,period TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(subject,kind,period));
CREATE TABLE IF NOT EXISTS patterns(id TEXT PRIMARY KEY,subject TEXT NOT NULL,computed REAL NOT NULL,data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,at REAL NOT NULL,data TEXT NOT NULL);
PRAGMA user_version=1;''')
  if self.meta('started_at') is None:self.set_meta('started_at',self.clock())
  self.db.commit()
 def meta(self,key,default=None):
  row=self.db.execute('select data from meta where key=?',(key,)).fetchone();return json.loads(row[0]) if row else default
 def set_meta(self,key,value):self.db.execute('insert or replace into meta values(?,?)',(key,dump(value)))
 def get(self,sid):
  row=self.db.execute('select data from subjects where id=?',(sid,)).fetchone();return json.loads(row[0]) if row else None
 def put(self,d):self.db.execute('insert or replace into subjects values(?,?)',(d['id'],dump(d)))
 def end(self,d,at,reason,end_known=False):
  if d.get('open_id'):
   self.db.execute('update sessions set end=?,end_known=?,quality=?,end_ref=? where id=?',(max(d['open_start'],at),int(end_known),'complete' if d['start_known'] and end_known else reason,reason,d['open_id']));d.pop('open_id',None)
  if d.get('coverage_id'):
   self.db.execute('update coverage set end=?,reason=? where id=?',(max(d['coverage_start'],at),reason,d.pop('coverage_id')))
 def select(self,descriptors,now,restarting=False):
  selected={d['id']:d for d in descriptors}
  for row in self.db.execute('select data from subjects').fetchall():
   d=json.loads(row[0]);sid=d['id']
   if d.get('selected') and (sid not in selected or restarting):
    if restarting and d.get('internal'):
     d['restart_pending']={'last_at':d.get('last_at',now),'open_id':d.get('open_id'),'open_start':d.get('open_start'),'start_known':d.get('start_known',False)}
    self.end(d,d.get('last_at',now),'restart_gap' if restarting else 'input_disabled');d['last_value']=None;d['selected']=False;self.put(d)
  for sid,desc in selected.items():
   d=self.get(sid) or {'id':sid,'enrolled':now,'first_seen':now,'last_value':None,'last_at':now}
   if not d.get('selected'):d.update(selected=True,enabled_since=now,last_value=None)
   d.update({k:v for k,v in desc.items() if k not in ('value','at')});self.put(d)
 def observe(self,sid,value,at,ref='snapshot',live=False):
  d=self.get(sid)
  if not d or not d.get('selected'):return
  if at<d.get('last_at',0):return
  v=classify(d['domain'],value);old=d.get('last_value');d.update(raw_value=value,last_at=at)
  if v is None:
   self.end(d,at,'unavailable');d['last_value']=None;d['status']='unavailable';self.put(d);return
  if not d.get('coverage_id'):
   cid=ident(sid,'coverage',at);self.db.execute('insert or ignore into coverage values(?,?,?,NULL,NULL)',(cid,sid,at));d.update(coverage_id=cid,coverage_start=at)
  if d['domain'] in ('sensor','number','input_number'):
   bucket=math.floor(at/900)*900
   self.db.execute('insert into observations values(?,?,?,?,?,1) on conflict(subject,bucket) do update set low=min(low,excluded.low),high=max(high,excluded.high),total=total+excluded.total,count=count+1',(sid,bucket,v,v,v));d.update(last_value=v,status='observing');self.put(d);return
  if old!=v:
   if d.get('open_id'):
    self.db.execute('update sessions set end=?,end_known=?,quality=?,end_ref=? where id=?',(at,int(live),'complete' if d['start_known'] and live else 'partial_start' if live else 'snapshot_gap',ref,d.pop('open_id')))
   if is_session(d['domain'],v):
    known=old is not None and live;session_id=ident(sid,v,at,ref)
    self.db.execute('insert or ignore into sessions values(?,?,?,?,NULL,?,0,?,?,NULL,?)',(session_id,sid,str(v),at,int(known),'open' if known else 'partial_start',ref,'live' if live else 'snapshot'))
    d.update(open_id=session_id,open_start=at,start_known=known)
  d.update(last_value=v,status='active' if is_session(d['domain'],v) else 'inactive');self.put(d)
 def disconnected(self,now):
  for row in self.db.execute('select data from subjects').fetchall():
   d=json.loads(row[0])
   if d.get('selected'):
    self.end(d,d.get('last_at',now),'connection_gap');d.update(last_value=None,status='connection_gap');self.put(d)
 def span_days(self,start,end):
  cursor=start
  while cursor<end:
   dt=datetime.fromtimestamp(cursor,self.tz);next_day=datetime.combine(dt.date()+timedelta(days=1),datetime.min.time(),self.tz).timestamp();stop=min(end,next_day)
   yield dt.date().isoformat(),stop-cursor
   cursor=stop
 def refresh_rollups(self,now):
  today=datetime.fromtimestamp(now,self.tz).date()
  for subject in self.db.execute('select data from subjects').fetchall():
   d=json.loads(subject[0]);sid=d['id']
   if not d.get('selected'):continue
   affected=[]
   for date in (today-timedelta(days=1),today):
    start=datetime.combine(date,datetime.min.time(),self.tz).timestamp();end=min(now,start+86400)
    if end<=start or end<d['enrolled']:continue
    entry={'date':date.isoformat(),'coverage_seconds':0.,'active_seconds':0.,'session_starts':0,'complete_sessions':0,'partial_sessions':0,'sessions':[]}
    for cov in self.db.execute('select start,end from coverage where subject=? and start<? and (end is null or end>?)',(sid,end,start)):
     entry['coverage_seconds']+=max(0,min(end,cov['end'] or d.get('last_at',end))-max(start,cov['start']))
    for row in self.db.execute('select * from sessions where subject=? and start<? and (end is null or end>?)',(sid,end,start)):
     x=dict(row);entry['active_seconds']+=max(0,min(end,x['end'] or d.get('last_at',end))-max(start,x['start']))
     if len(entry['sessions'])<100:entry['sessions'].append(x['id'])
     if start<=x['start']<end:
      entry['session_starts']+=x['start_known'];entry['complete_sessions']+=int(x['quality']=='complete');entry['partial_sessions']+=int(not x['start_known'] or x['end'] is not None and not x['end_known'])
    self.db.execute('insert or replace into rollups values(?,?,?,?)',(sid,'day',date.isoformat(),dump(entry)));affected.append(date)
   daily=[json.loads(row[0]) for row in self.db.execute("select data from rollups where subject=? and kind='day'",(sid,))]
   keys=set()
   for date in affected:
    iso=date.isocalendar();keys.update([('week',f'{iso.year}-W{iso.week:02}'),('month',date.isoformat()[:7])])
   for kind,period in keys:
    entries=[]
    for day in daily:
     date=datetime.fromisoformat(day['date']);iso=date.isocalendar();key=f'{iso.year}-W{iso.week:02}' if kind=='week' else day['date'][:7]
     if key==period:entries.append(day)
    entry={'period':period,'days':sorted(day['date'] for day in entries)}
    for key in ('coverage_seconds','active_seconds','session_starts','complete_sessions','partial_sessions'):entry[key]=sum(day[key] for day in entries)
    self.db.execute('insert or replace into rollups values(?,?,?,?)',(sid,kind,period,dump(entry)))
 def analyze(self,run_id,now,window_days=90):
  with self.db:return self._analyze(run_id,now,window_days)
 def import_controller_history(self,sid,rows,start,until):
  """Import explicit recorder boundaries for a linked controller, never device gaps."""
  d=self.get(sid)
  if not d or not d.get('internal'):raise ValueError('Not a linked controller source')
  pending=d.get('restart_pending')
  if d.get('history_imported') and not pending:return
  # Validate the whole bounded response before replacing a restart fragment.
  stamps=[datetime.fromisoformat((r.get('last_changed') or r.get('last_updated')).replace('Z','+00:00')).timestamp() for r in rows]
  if stamps!=sorted(stamps) or until<start:raise ValueError('Invalid controller history order/window')
  if pending and pending.get('open_id'):
   self.db.execute("delete from sessions where id=? and subject=? and quality='restart_gap'",(pending['open_id'],sid))
  prior=None;opened=None;first=True;covered_start=None;coverage=[]
  for row in rows:
   stamp=row.get('last_changed') or row.get('last_updated')
   at=datetime.fromisoformat(stamp.replace('Z','+00:00')).timestamp()
   if at>until:break
   at=max(start,at);value=classify('input_boolean',row.get('state'))
   if value is not None and covered_start is None:covered_start=at
   if value is None and covered_start is not None:
    coverage.append((covered_start,at));covered_start=None
   if value==prior and not first:continue
   if opened and value!='active':
    began,known=opened
    if at>began:
     key=ident(sid,'recorder',began,at)
     self.db.execute('insert or ignore into sessions values(?,?,?,?,?,?,?,?,?,?,?)',(key,sid,'active',began,at,int(known),int(value=='inactive'),'complete' if known and value=='inactive' else 'recorder_partial','HA Recorder','HA Recorder','recorder'))
    opened=None
   if value=='active':opened=(at,(not first and prior=='inactive') or bool(first and pending and pending.get('start_known') and pending.get('open_start')==at))
   prior=value;first=False
  # A current active snapshot may be extended only from its explicit recorder on
  # boundary. Do not make an already-active first history sample a known start.
  if opened and d.get('open_id') and d.get('last_value')=='active':
   began,known=opened
   self.db.execute('update sessions set start=?,start_known=?,start_ref=?,source=? where id=?',(began,int(known),'HA Recorder','recorder+live',d['open_id']))
   d.update(open_start=began,start_known=known)
  if covered_start is not None:coverage.append((covered_start,until))
  for began,ended in coverage:
   if pending:began=max(began,pending['last_at'])
   if ended>began:self.db.execute('insert or ignore into coverage values(?,?,?,?,?)',(ident(sid,'recorder-coverage',began,ended),sid,began,ended,'HA Recorder controller state history'))
  d.pop('restart_pending',None);d.update(history_imported=True,history_start=start,history_until=until);self.put(d);self.db.commit()
 def _analyze(self,run_id,now,window_days=90):
  summaries=[];cutoff=now-window_days*86400
  for row in self.db.execute('select data from subjects').fetchall():
   d=json.loads(row[0]);sid=d['id']
   if not d.get('selected') or d.get('internal'):continue
   sessions=[dict(x) for x in self.db.execute('select * from sessions where subject=? and (end is null or end>=?) order by start limit 20000',(sid,cutoff))]
   complete=[x for x in sessions if x['end'] is not None and x['start_known'] and x['end_known'] and x['quality']=='complete' and x['end']>x['start']]
   if d['domain']=='presence':values=sorted(set(x['value'] for x in sessions)) or ['HOME','OFFICE','AWAY']
   else:values=['active']
   daily={}
   def day(date):return daily.setdefault(date,{'date':date,'coverage_seconds':0.,'active_seconds':0.,'session_starts':0,'complete_sessions':0,'partial_sessions':0,'sessions':[]})
   for cov in self.db.execute('select start,end from coverage where subject=? and (end is null or end>=?)',(sid,cutoff)):
    for date,seconds in self.span_days(max(cov['start'],cutoff),min(now,cov['end'] or d.get('last_at',now))):day(date)['coverage_seconds']+=seconds
   for sess in sessions:
    for date,seconds in self.span_days(max(sess['start'],cutoff),min(now,sess['end'] or d.get('last_at',now))):
     entry=day(date);entry['active_seconds']+=seconds
     if len(entry['sessions'])<100:entry['sessions'].append(sess['id'])
    date=datetime.fromtimestamp(sess['start'],self.tz).date().isoformat();entry=day(date);entry['session_starts']+=int(bool(sess['start_known']));entry['complete_sessions']+=int(sess in complete);entry['partial_sessions']+=int(not sess['start_known'] or sess['end'] is not None and not sess['end_known'])
   full_days=[date for date,data in daily.items() if data['coverage_seconds']>=.8*86400]
   coverage=sum(x['coverage_seconds'] for x in daily.values());age=max(0,now-d['enrolled']);warm=age>=7*86400 and coverage>=5.6*86400
   for value in values:
    items=[x for x in complete if x['value']==value];durations=[(x['end']-x['start'])/60 for x in items];starts=[datetime.fromtimestamp(x['start'],self.tz) for x in items];dates={x.date().isoformat() for x in starts};weekday=[]
    for n in range(7):
     indices=[i for i,x in enumerate(starts) if x.weekday()==n];opportunities=[v for v in full_days if datetime.fromisoformat(v).weekday()==n];occurrences={starts[i].date().isoformat() for i in indices}
     weekday.append({'day':calendar.day_name[n],'sessions':len(indices),'observed_days':len(opportunities),'days_used':len(occurrences.intersection(opportunities)),'frequency':len(occurrences.intersection(opportunities))/len(opportunities) if opportunities else None,'strong_enough':len(occurrences.intersection(opportunities))>=3 and len(opportunities)>=3,'start_window':timing([starts[i].hour*60+starts[i].minute for i in indices]),'median_minutes':statistics.median([durations[i] for i in indices]) if indices else None})
    confidence=min(.92,len(items)/(len(items)+4)) if items else 0.
    if durations:
     spread=(quantile(durations,.9)-quantile(durations,.1))/max(1,statistics.median(durations));confidence/=1+min(2,spread)*.25
    strong=warm and len(items)>=8 and len(dates)>=3 and confidence>=.65 and len(sessions)<20000
    reasons=[]
    if not warm:reasons.append('Observation week or coverage is incomplete')
    if len(items)<8:reasons.append('Fewer than 8 complete sessions')
    if len(dates)<3:reasons.append('Fewer than 3 independent observed dates')
    if len(sessions)>=20000:reasons.append('Analysis window exceeds the session bound')
    pattern={'id':ident(sid,value),'subject':sid,'name':d['name'],'domain':d['domain'],'mode':d.get('mode'),'value':value,'computed':now,'window_days':window_days,'complete_sessions':len(items),'partial_sessions':sum(x['quality']!='complete' for x in sessions),'distinct_dates':len(dates),'coverage_seconds':coverage,'covered_days':len(full_days),'observation_age_days':age/86400,'warmup_complete':warm,'confidence':round(confidence,3),'strong_enough':strong,'reasons':reasons,'weekday':weekday,'median_minutes':statistics.median(durations) if durations else None,'typical_low_minutes':quantile(durations,.1),'typical_high_minutes':quantile(durations,.9),'start_window':timing([x.hour*60+x.minute for x in starts]),'evidence_sessions':[x['id'] for x in items[-64:]],'inference':'Reported device/location activity, not proof of a person\'s activity or preference.'}
    if d['domain'] in ('sensor','number','input_number'):
     numeric=self.db.execute('select min(low),max(high),sum(total),sum(count),count(*) from observations where subject=? and bucket>=?',(sid,cutoff)).fetchone()
     pattern['numeric']={'minimum':numeric[0],'maximum':numeric[1],'mean':numeric[2]/numeric[3] if numeric[3] else None,'observations':numeric[3] or 0,'buckets':numeric[4],'unit':d.get('unit')}
     pattern['reasons']=['Numeric observations; on/off session durations do not apply.']
    if not pattern.get('numeric'):
     from .semantics import session_stats
     raw_stats=session_stats(items,self.tz,full_days)
     pattern.update(raw_stats,session_basis='reported_intervals',mean_minutes=raw_stats['mean_minutes'])
     pattern['evidence_subject']=sid
     control=self.get(d.get('usage_subject')) if d.get('usage_subject') else None
     if control and control.get('selected'):
      source=control['id'];all_usage=[dict(x) for x in self.db.execute('select * from sessions where subject=? and (end is null or end>=?) order by start limit 20000',(source,cutoff))]
      usage=[x for x in all_usage if x['quality']=='complete' and x['start_known'] and x['end_known'] and x['end'] and x['end']>x['start']]
      covered={}
      for cov in self.db.execute('select start,end from coverage where subject=? and (end is null or end>=?)',(source,cutoff)):
       for date,seconds in self.span_days(max(cov['start'],cutoff),min(now,cov['end'] or control.get('last_at',now))):covered[date]=covered.get(date,0)+seconds
      full=[date for date,seconds in covered.items() if seconds>=.8*86400]
      stats=session_stats(usage,self.tz,full)
      pattern.update(stats,session_basis='controller_enabled',evidence_subject=source,controller=d['usage_control'],reported_intervals=raw_stats,
                     mode='Controller-enabled sessions',partial_sessions=sum(x['quality']!='complete' for x in all_usage),
                     coverage_seconds=sum(covered.values()),covered_days=len(full),evidence_sessions=[x['id'] for x in usage[-64:]],
                     usage_examples=all_usage[-16:])
      pattern['warmup_complete']=age>=7*86400 and pattern['coverage_seconds']>=5.6*86400
      pattern['confidence']=round(min(.92,stats['distinct_dates']/(stats['distinct_dates']+4)),3)
      pattern['strong_enough']=pattern['warmup_complete'] and stats['distinct_dates']>=3 and len(usage)>=8 and pattern['confidence']>=.65
      pattern['reasons']=[]
      if not pattern['warmup_complete']:pattern['reasons'].append('Observation week or coverage is incomplete')
      if len(usage)<8:pattern['reasons'].append('Fewer than 8 complete controller sessions')
      if stats['distinct_dates']<3:pattern['reasons'].append('Fewer than 3 independent observed dates')
      pattern['learning_eligible']=d.get('usage_learning_allowed',False)
      if not pattern['learning_eligible']:
       pattern['strong_enough']=False;pattern['reasons'].append('Controller is a pattern dependency, not an enrolled memory input')
     pattern['analysis_version']=3
    self.db.execute('insert or replace into patterns values(?,?,?,?)',(pattern['id'],sid,now,dump(pattern)));summaries.append(pattern)
   periods={}
   for date,data in sorted(daily.items()):
    self.db.execute('insert or replace into rollups values(?,?,?,?)',(sid,'day',date,dump(data)));dt=datetime.fromisoformat(date);iso=dt.isocalendar()
   # Rebuild parent summaries from all retained daily evidence, not summaries of summaries.
   for row_day in self.db.execute("select period,data from rollups where subject=? and kind='day' order by period",(sid,)):
    date=row_day['period'];data=json.loads(row_day['data']);dt=datetime.fromisoformat(date);iso=dt.isocalendar()
    for kind,period in [('week',f'{iso.year}-W{iso.week:02}'),('month',date[:7])]:
     entry=periods.setdefault((kind,period),{'period':period,'days':[],'coverage_seconds':0,'active_seconds':0,'session_starts':0,'complete_sessions':0,'partial_sessions':0})
     entry['days'].append(date)
     for k in ('coverage_seconds','active_seconds','session_starts','complete_sessions','partial_sessions'):entry[k]+=data[k]
   for (kind,period),data in periods.items():self.db.execute('insert or replace into rollups values(?,?,?,?)',(sid,kind,period,dump(data)))
  result={'id':run_id,'at':now,'subjects':len({x['subject'] for x in summaries}),'patterns':len(summaries),'strong_patterns':sum(x['strong_enough'] for x in summaries),'automatic':False,'window_days':window_days}
  self.db.execute('insert into runs values(?,?,?)',(run_id,now,dump(result)));self.set_meta('last_analysis',result);self.set_meta('analysis_version',3);self.db.commit();return result
 def snapshot(self,now,subject=None,day=None):
  selected=[];subjects=[]
  for row in self.db.execute('select data from subjects'):
   d=json.loads(row[0]);subjects.append(d)
   if not d.get('selected') or d.get('internal'):continue
   d=dict(d);d['elapsed_seconds']=max(0,now-d['open_start']) if d.get('open_id') else None;d['observed_seconds']=max(0,d.get('last_at',now)-d.get('open_start',now)) if d.get('open_id') else None
   selected.append(d)
  query='where subject=?' if subject else '';args=(subject,) if subject else ()
  session_query=query;session_args=args
  if day:
   dt=datetime.fromisoformat(day).replace(tzinfo=self.tz);start=dt.timestamp();end=(dt+timedelta(days=1)).timestamp()
   session_query+=(' and ' if query else 'where ')+'start<? and (end is null or end>?)';session_args=(*args,end,start)
  recent=[dict(x) for x in self.db.execute('select * from sessions '+session_query+' order by start desc limit 200',session_args)]
  patterns=[json.loads(x[0]) for x in self.db.execute('select data from patterns '+query+' order by computed desc limit 300',args)]
  rollups=[{'subject':x['subject'],'kind':x['kind'],'period':x['period'],**json.loads(x['data'])} for x in self.db.execute('select * from rollups '+query+' order by period desc limit 1500',args)]
  observations=[dict(x) for x in self.db.execute('select * from observations '+query+' order by bucket desc limit 150',args)]
  return {'timezone':str(self.tz),'day_window':{'start':start,'end':end} if day else None,'started_at':self.meta('started_at'),'now':now,'observation_days':max(0,(now-self.meta('started_at'))/86400),'warmup_days':7,'automatic_analysis':False,'pattern_interruptions_enabled':False,'selected':sorted(selected,key=lambda d:d['name'].casefold()),'subjects':subjects,'sessions':recent,'patterns':patterns,'rollups':rollups,'observations':observations,'last_analysis':self.meta('last_analysis'),'counts':{'sessions':self.db.execute('select count(*) from sessions').fetchone()[0],'complete_sessions':self.db.execute("select count(*) from sessions where quality='complete'").fetchone()[0]}}
 def close(self):self.db.commit();self.db.close()
