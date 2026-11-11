import hashlib,json,os,sqlite3,threading,time,uuid
from pathlib import Path
TABLES={'episodes','attention','wakes','temporal','watches','incidents','proposals','decisions','health','executions','usage','audit','approvals','deliveries'}
AUTH={'USER_EXPLICIT','USER_INFERRED','SUNDAY_SELF','SYSTEM'}
WAKES={'HA_NOTEWORTHY','EXECUTION_ANOMALY','EXTERNAL_CONDITION','SYSTEM_DEGRADATION','EXPECTATION_VIOLATED','SELF_RECHECK','COMMITMENT_DUE'}
def js(x):return json.dumps(x,sort_keys=True,ensure_ascii=False,allow_nan=False)
def digest(x):return hashlib.sha256(js(x).encode()).hexdigest()
class Store:
 def __init__(self,path,clock=time.time):
  self.clock=clock;self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True);self.lock=threading.RLock();self.db=sqlite3.connect(path,check_same_thread=False,timeout=5);self.db.row_factory=sqlite3.Row;os.chmod(path,0o600);self.db.execute('pragma journal_mode=WAL');self.db.execute('pragma synchronous=FULL')
  if self.db.execute('pragma user_version').fetchone()[0] not in (0,1,2):raise RuntimeError('Unsupported proactive schema')
  for table in TABLES:
   self.db.execute('create table if not exists '+table+'(id TEXT PRIMARY KEY,dedupe TEXT UNIQUE NOT NULL,state TEXT NOT NULL,due REAL,created REAL NOT NULL,updated REAL NOT NULL,lease REAL,owner TEXT,data TEXT NOT NULL)')
   self.db.execute('create index if not exists '+table+'_state_due on '+table+'(state,due)')
  self.db.executescript('create table if not exists seen(id TEXT PRIMARY KEY,at REAL);create table if not exists metadata(key TEXT PRIMARY KEY,data TEXT);pragma user_version=2;');self.db.commit()
 def close(self):self.db.close()
 def table(self,t):
  if t not in TABLES:raise ValueError('Invalid inspection table')
  return t
 def put(self,t,data,*,dedupe,state='PENDING',due=None):
  self.table(t);identity=t+':'+digest(dedupe);stamp=self.clock()
  if len(js(data))>64000:raise ValueError('Proactive record exceeds bound')
  with self.lock,self.db:self.db.execute('insert or ignore into '+t+' values(?,?,?,?,?,?,NULL,NULL,?)',(identity,digest(dedupe),state,due,stamp,stamp,js(data)))
  return identity
 def get(self,t,identity):
  row=self.db.execute('select * from '+self.table(t)+' where id=?',(identity,)).fetchone()
  return self.decode(row) if row else None
 def decode(self,r):return dict(r)|{'data':json.loads(r['data'])}
 def rows(self,t,states=None,limit=100):
  where=' where state in ('+','.join('?' for _ in states)+')' if states else '';args=list(states or [])+[max(1,min(1000,int(limit)))];return [self.decode(r) for r in self.db.execute('select * from '+self.table(t)+where+' order by created,id limit ?',args)]
 def change(self,t,identity,*,state=None,data=None,due=None):
  row=self.get(t,identity)
  if not row:return False
  with self.lock,self.db:self.db.execute('update '+self.table(t)+' set state=?,data=?,due=?,updated=?,lease=NULL,owner=NULL where id=?',(state or row['state'],js(row['data'] if data is None else data),row['due'] if due is None else due,self.clock(),identity))
  return True
 def claim(self,t,identity,states=('PENDING',),seconds=90):
  owner=uuid.uuid4().hex
  with self.lock,self.db:
   row=self.db.execute('update '+self.table(t)+" set state='PROCESSING',lease=?,owner=?,updated=? where id=? and state in ("+','.join('?' for _ in states)+')',(self.clock()+seconds,owner,self.clock(),identity,*states))
  return owner if row.rowcount else None
 def recover(self):
  with self.lock,self.db:
   # Scheduler work is deterministic and idempotent. Model requests of unknown outcome are not replayed.
   self.db.execute("update temporal set state='PENDING',lease=NULL,owner=NULL where state='PROCESSING' and lease<=?",(self.clock(),))
   self.db.execute("update usage set state='UNKNOWN',data=json_set(data,'$.status','unknown_after_interruption') where state='STARTED' and created<?",(self.clock()-120,))
   for t in ('episodes','wakes'):
    self.db.execute('update '+t+" set state='UNKNOWN',lease=NULL,owner=NULL where state='PROCESSING' and lease<=?",(self.clock(),))
 def seen(self,identity):
  with self.lock,self.db:r=self.db.execute('insert or ignore into seen values(?,?)',(identity,self.clock()))
  return not bool(r.rowcount)
 def meta(self,k,default=None):
  r=self.db.execute('select data from metadata where key=?',(k,)).fetchone();return json.loads(r[0]) if r else default
 def set_meta(self,k,v):
  with self.lock,self.db:self.db.execute('insert or replace into metadata values(?,?)',(k,js(v)))
 def audit(self,kind,data,identity=None):return self.put('audit',{'kind':kind,**data},dedupe=identity or uuid.uuid4().hex,state='RECORDED')
 def wake(self,kind,authority,subject,evidence,incident,**refs):
  if kind not in WAKES or authority not in AUTH or not evidence:raise ValueError('Invalid bounded cognition envelope')
  incident_id=incident if isinstance(incident,str) and self.get('incidents',incident) else self.put('incidents',{'incident_key':incident,'subject':subject},dedupe=incident,state='OPEN')
  data={'wake_type':kind,'authority':authority,'created_at':self.clock(),'urgency_hint':'normal','subject':subject,'evidence_refs':evidence[:16],'incident_id':incident_id,'dedupe_key':incident,'people':refs.pop('people',[])[:8],'entities':refs.pop('entities',[])[:12],'areas':refs.pop('areas',[])[:8],'world_snapshot_refs':refs.pop('world_snapshot_refs',[])[:8],'memory_refs':refs.pop('memory_refs',[])[:8],'history_refs':refs.pop('history_refs',[])[:8],'thread_refs':refs.pop('thread_refs',[])[:8],'commitment_refs':refs.pop('commitment_refs',[])[:8],**refs}
  # One cognition per incident, regardless of HA/execution path; proposed effects share the same identity.
  existing=self.get('wakes','wakes:'+digest(incident))
  if existing:
   combined=existing['data'];combined['evidence_refs']=list(dict.fromkeys(combined['evidence_refs']+data['evidence_refs']))[:16];combined['wake_paths']=sorted(set(combined.get('wake_paths',[combined['wake_type']])+[kind]));
   with self.lock,self.db:self.db.execute('update wakes set data=?,updated=? where id=?',(js(combined),self.clock(),existing['id']))
   return existing['id']
  return self.put('wakes',data,dedupe=incident)
 def chain(self,identity):
  # Bounded graph traversal across trusted record references, including delivery -> evidence.
  graph={};pending=[identity]
  def refs(value):
   if isinstance(value,str) and value.split(':',1)[0] in TABLES:yield value
   elif isinstance(value,dict):
    for x in value.values():yield from refs(x)
   elif isinstance(value,list):
    for x in value:yield from refs(x)
  while pending and len(graph)<60:
   key=pending.pop(0)
   if key in graph:continue
   table=key.split(':',1)[0]
   if table not in TABLES:continue
   row=self.get(table,key)
   if not row:continue
   graph[key]={'table':table,**row};pending.extend(r for r in refs(row['data']) if r not in graph)
   if table=='proposals':
    pending.extend(r[0] for r in self.db.execute("select id from deliveries where json_extract(data,'$.proposal_id')=? limit 5",(key,)))
  result={'root':identity,'records':graph,'bounded_to':60}
  result['linked_audit']=[self.decode(r) for r in self.db.execute('select * from audit where instr(data,?)>0 order by created desc limit 30',(identity,))]
  result['linked_proposals']=[self.decode(r) for r in self.db.execute('select * from proposals where instr(data,?)>0 order by created desc limit 30',(identity,))]
  return result
