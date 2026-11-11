"""Transactional learned memory, evidence, lineage and bounded retrieval."""
import hashlib,json,math,os,re,sqlite3,threading,uuid
from datetime import datetime,timezone
from pathlib import Path
TYPES={'FACT','PREFERENCE','EPISODE','PATTERN_EXPECTATION','SEMANTIC_RELATION'}
SOURCES={'USER_EXPLICIT','USER_INFERRED','HISTORICAL_PATTERN','SUNDAY_INFERENCE','EXECUTION_OUTCOME','EXTERNAL'}
OPS={'ADD','MERGE','UPDATE','SUPERSEDE','CONFLICT','IGNORE'}
def now():return datetime.now(timezone.utc).isoformat()
def js(v):return json.dumps(v,sort_keys=True,ensure_ascii=False,allow_nan=False)
def key(v):return hashlib.sha256(js(v).encode()).hexdigest()
def words(v):
 tokens=set(re.findall(r'\w+',str(v).lower()))
 return tokens|{w[:-1] for w in tokens if len(w)>4 and w.endswith('s') and not w.endswith('ss')}
def check_text(v,name,maximum=1200):
 if not isinstance(v,str) or not v.strip() or len(v)>maximum:raise ValueError('Invalid '+name)
 return v.strip()
def confidence(v):
 if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<=v<=1:raise ValueError('Invalid confidence')
 return float(v)
def strings(v):
 if not isinstance(v,list) or len(v)>32 or any(not isinstance(x,str) or len(x)>160 for x in v):raise ValueError('Invalid structural keys')
 return sorted(set(v))
class Store:
 def __init__(self,path):
  self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True);self.lock=threading.RLock()
  self.db=sqlite3.connect(str(path),check_same_thread=False,timeout=10);self.db.row_factory=sqlite3.Row;os.chmod(path,0o600)
  self.db.execute('pragma journal_mode=WAL');self.db.execute('pragma synchronous=FULL');self.db.execute('pragma foreign_keys=ON')
  if self.db.execute('pragma user_version').fetchone()[0] not in (0,1):raise RuntimeError('Unsupported learned memory schema')
  self.db.executescript('''
 CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY,kind TEXT NOT NULL,source TEXT NOT NULL,at TEXT NOT NULL,data TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS inbox(id TEXT PRIMARY KEY,dedupe TEXT UNIQUE NOT NULL,state TEXT NOT NULL,created TEXT NOT NULL,data TEXT NOT NULL,error TEXT);
 CREATE INDEX IF NOT EXISTS inbox_state ON inbox(state,created);
 CREATE TABLE IF NOT EXISTS memories(id TEXT PRIMARY KEY,type TEXT NOT NULL,subject TEXT NOT NULL,status TEXT NOT NULL,confidence REAL NOT NULL,updated TEXT NOT NULL,data TEXT NOT NULL);
 CREATE INDEX IF NOT EXISTS memories_subject ON memories(subject,status);
 CREATE TABLE IF NOT EXISTS memory_keys(memory_id TEXT NOT NULL REFERENCES memories(id),kind TEXT NOT NULL,value TEXT NOT NULL,PRIMARY KEY(memory_id,kind,value));
 CREATE INDEX IF NOT EXISTS keys_lookup ON memory_keys(kind,value);
 CREATE TABLE IF NOT EXISTS versions(id INTEGER PRIMARY KEY,memory_id TEXT NOT NULL,at TEXT NOT NULL,operation TEXT NOT NULL,data TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS audit(id TEXT PRIMARY KEY,candidate_id TEXT,at TEXT NOT NULL,operation TEXT NOT NULL,data TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS retrievals(id TEXT PRIMARY KEY,at TEXT NOT NULL,shadow INTEGER NOT NULL,data TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS memory_controls(fingerprint TEXT PRIMARY KEY,record_id TEXT NOT NULL,action TEXT NOT NULL,at TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,kind TEXT NOT NULL,started TEXT NOT NULL,finished TEXT,status TEXT NOT NULL,data TEXT NOT NULL);
 PRAGMA user_version=1;
 ''');self.db.commit();self.v2_enabled=bool(self.meta('memory_v2_migration'))
 def close(self):self.db.close()
 def acquire_lease(self,name,seconds=900):
  import time
  t=time.time()
  with self.lock,self.db:
   row=self.db.execute('insert into metadata values(?,?) on conflict(key) do update set value=excluded.value where cast(metadata.value as real) < ?',('lease:'+name,str(t+seconds),t))
  return bool(row.rowcount)
 def release_lease(self,name):self.set_meta('lease:'+name,0)
 def meta(self,k,default=None):
  row=self.db.execute('select value from metadata where key=?',(k,)).fetchone();return json.loads(row[0]) if row else default
 def set_meta(self,k,v):
  with self.lock,self.db:self.db.execute('insert or replace into metadata values(?,?)',(k,js(v)))
 def evidence(self,kind,source,data,reference=None):
  # References and summaries only. Callers must never supply GPS/credentials or raw event batches.
  encoded=js(data)
  if len(encoded)>24000 or re.search(r'"(?:latitude|longitude|gps|api_key|password|access_token)"',encoded,re.I):raise ValueError('Unsafe evidence fields')
  eid=reference or 'ev:'+key([kind,source,data])
  with self.lock,self.db:
   old=self.db.execute('select data from evidence where id=?',(eid,)).fetchone()
   if old and old[0]!=encoded:raise ValueError('Evidence reference reused with different content')
   self.db.execute('insert or ignore into evidence values(?,?,?,?,?)',(eid,kind,source,now(),encoded))
  return eid
 def submit(self,candidate,source,evidence_refs,dedupe_key):
  if source not in SOURCES:raise ValueError('Unsupported source class')
  if not evidence_refs or len(evidence_refs)>64:raise ValueError('Evidence required')
  for ref in evidence_refs:
   if not self.db.execute('select 1 from evidence where id=?',(ref,)).fetchone():raise ValueError('Missing evidence reference')
  c={'subject':check_text(candidate.get('subject'),'subject',200),'statement':check_text(candidate.get('statement'),'statement'),'type':candidate.get('type'),'source_class':source,'confidence':confidence(candidate.get('confidence',.5)),'evidence_refs':sorted(set(evidence_refs)),'structured_value':candidate.get('structured_value'),'correction':bool(candidate.get('correction',False)),'created_at':now()}
  if c['type'] not in TYPES:raise ValueError('Invalid memory type')
  for name in ('tags','entities','areas','people'):c[name]=strings(candidate.get(name,[]))
  if getattr(self,'v2_enabled',False):
   from memory_v2.beliefs import enrich
   c=enrich(candidate,c)
  if len(js(c))>16000:raise ValueError('Candidate too large')
  cid='candidate:'+key(dedupe_key)
  with self.lock,self.db:
   from .management import blocked
   state='DELETED' if blocked(self,c) or not getattr(self,'scope_filter',lambda data:True)(c) else 'NEW'
   if getattr(self,'v2_enabled',False) and state=='NEW':
    from memory_v2.beliefs import upsert_candidate
    return upsert_candidate(self,c,cid,key(dedupe_key))
   self.db.execute('insert or ignore into inbox values(?,?,?,?,?,NULL)',(cid,key(dedupe_key),state,now(),js(c)))
  return cid
 def candidates(self,states=('NEW',),limit=20):
  rows=self.db.execute('select * from inbox where state in ('+','.join('?' for _ in states)+') order by created,id limit ?',(*states,1000));return [dict(r)|{'data':json.loads(r['data'])} for r in rows if getattr(self,'scope_filter',lambda data:True)(json.loads(r['data']))][:min(100,max(1,limit))]
 def get(self,mid):
  row=self.db.execute('select data from memories where id=?',(mid,)).fetchone();return json.loads(row[0]) if row else None
 def related(self,c,limit=8):
  result=self.retrieve(c.get('statement',''),subject=c.get('subject'),people=c.get('people',[]),entities=c.get('entities',[]),areas=c.get('areas',[]),tags=c.get('tags',[]),limit=limit,min_confidence=0,include_conflicts=True)
  if getattr(self,'v2_enabled',False) and c.get('concept_key'):
   exact=[json.loads(r[0]) for r in self.db.execute("select data from memories where status in ('ACTIVE','CONFLICTED') and json_extract(data,'$.concept_key')=? limit 8",(c['concept_key'],)) if getattr(self,'scope_filter',lambda data:True)(json.loads(r[0]))]
   result=exact+[m for m in result if m['memory_id'] not in {x['memory_id'] for x in exact}]
  return result[:limit]
 def retrieve(self,query='',*,subject=None,people=(),entities=(),areas=(),tags=(),limit=5,min_confidence=.55,include_conflicts=False,require_query_match=False,trace=None,scope_filter=None):
  limit=max(1,min(8,int(limit)));q=words(query);ignore={'the','a','i','you','is','and','to','of','my','what','are','in','it','on','off','me'};q-=ignore
  wanted={'people':set(people),'entities':set(entities),'areas':set(areas),'tags':set(tags)};ids=set()
  for kind,values in wanted.items():
   for value in values:
    ids.update(r[0] for r in self.db.execute('select memory_id from memory_keys where kind=? and value=? limit 200',(kind,str(value).lower())))
  # Structural hits first, bounded lexical scan supplements them without sending a whole wiki to a model.
  records=[];seen=set()
  if subject:
   for r in self.db.execute("select id from memories where subject=? and status in ('ACTIVE','CONFLICTED') limit 50",(subject,)):ids.add(r[0])
  for mid in sorted(ids):
   m=self.get(mid)
   if m:records.append(m);seen.add(mid)
  for r in self.db.execute("select data from memories where status in ('ACTIVE','CONFLICTED') order by updated desc limit 1000"):
   m=json.loads(r[0])
   if m['memory_id'] not in seen:records.append(m)
  ranked=[];suppressed_person_only=0;excluded_confidence=0
  for m in records:
   if not (scope_filter or getattr(self,'scope_filter',lambda data:True))(m):continue
   if m['status'] not in ({'ACTIVE','CONFLICTED'} if include_conflicts else {'ACTIVE'}) or m['confidence']<min_confidence:
    excluded_confidence+=1;continue
   exact=bool(subject and subject.casefold()==m['subject'].casefold());struct=sum(bool(set(m.get(k,[]))&v) for k,v in wanted.items());overlap=len(q & words(m['subject']+' '+m['statement']+' '+m.get('future_impact','')+' '+' '.join(m.get('tags',[]))))
   lexical=overlap>=min(2,max(1,len(q)))
   structural={k:sorted(set(m.get(k,[]))&v) for k,v in wanted.items()}
   # A speaker identity is ambient context, not evidence that every memory about
   # that person belongs in the current turn. Explicit names still support recall.
   named_person=any(words(person) and words(person)<=words(query) for person in m.get('people',[]))
   anchored=exact or lexical or named_person or any(structural[k] for k in ('entities','areas','tags'))
   if require_query_match and struct and not anchored:
    suppressed_person_only+=1;continue
   if exact or struct or lexical:
    if not q and not exact and not struct:continue
    score=100*exact+20*struct+overlap
    if getattr(self,'v2_enabled',False):
     from memory_v2.beliefs import retrieval_weight
     score*=retrieval_weight(m)
    reason={'score':score,'exact_subject':exact,'lexical_overlap':overlap,'structural_matches':structural,'explicit_person_in_query':named_person}
    ranked.append((score,m['updated_at'],m,reason))
  ranked.sort(key=lambda x:(x[0],x[1]),reverse=True)
  selected=ranked[:limit]
  if trace is not None:
   trace.update(policy='query-anchored-v1' if require_query_match else 'structural-lexical-v1',candidate_count=len(records),excluded_status_or_confidence=excluded_confidence,suppressed_person_only=suppressed_person_only,min_confidence=min_confidence,limit=limit,selected=[])
   for _,_,m,reason in selected:
    version=self.db.execute('select max(id) from versions where memory_id=?',(m['memory_id'],)).fetchone()[0]
    trace['selected'].append({'memory_id':m['memory_id'],'version_id':version,'content_sha256':key(m),'updated_at':m['updated_at'],'confidence':m['confidence'],'status':m['status'],'evidence_refs':m['evidence_refs'],**reason})
  return [x[2] for x in selected]
 def _put(self,m,operation):
  self.db.execute('insert or replace into memories values(?,?,?,?,?,?,?)',(m['memory_id'],m['type'],m['subject'],m['status'],m['confidence'],m['updated_at'],js(m)))
  self.db.execute('delete from memory_keys where memory_id=?',(m['memory_id'],))
  for kind in ('people','entities','areas','tags'):
   self.db.executemany('insert or ignore into memory_keys values(?,?,?)',[(m['memory_id'],kind,str(x).lower()) for x in m.get(kind,[])])
  self.db.execute('insert into versions(memory_id,at,operation,data) values(?,?,?,?)',(m['memory_id'],now(),operation,js(m)))
 def reconcile(self,cid,proposal,related_ids,metadata=None):
  if not isinstance(proposal,dict) or set(proposal)-{'operation','target_id','reason','confidence'}:raise ValueError('Malformed reconciliation')
  op=proposal.get('operation');reason=check_text(proposal.get('reason'),'reconciliation reason',800)
  if op not in OPS:raise ValueError('Unknown operation')
  with self.lock,self.db:
   row=self.db.execute('select * from inbox where id=?',(cid,)).fetchone()
   if not row:raise ValueError('Unknown candidate')
   if 'input_policy_revision' in (metadata or {}) and metadata['input_policy_revision']!=getattr(self,'input_revision',lambda:None)():return {'status':'input_selection_changed','candidate':cid}
   if row['state']=='DELETED':return {'idempotent':True,'state':'DELETED'}
   if (metadata or {}).get('candidate_revision') and key(json.loads(row['data']))!=metadata['candidate_revision']:return {'status':'candidate_changed','candidate':cid}
   if row['state'] in ('ACCEPTED','MERGED','SUPERSEDED','CONFLICT','IGNORED'):return {'idempotent':True,'state':row['state']}
   if row['state'] not in ('NEW','PROCESSING','ERROR'):raise ValueError('Candidate not processable')
   c=json.loads(row['data']);target=proposal.get('target_id');old=self.get(target) if target else None
   from .management import blocked
   if not getattr(self,'scope_filter',lambda data:True)(c):op='IGNORE';reason='Outside primary room scope';target=None;old=None
   if blocked(self,c):op='IGNORE';reason='Explicit dashboard removal or correction blocks replay of this candidate.';target=None;old=None
   if old and old.get('dashboard_edited') and c.get('created_at','')<old['updated_at'] and op in ('MERGE','UPDATE','SUPERSEDE','CONFLICT'):
    op='IGNORE';reason='Candidate predates explicit dashboard correction.';target=None;old=None
   if op in ('MERGE','UPDATE','SUPERSEDE','CONFLICT') and (not old or target not in related_ids or old['status'] not in ('ACTIVE','CONFLICTED')):raise ValueError('Target must be a supplied related current memory')
   if op in ('ADD','IGNORE') and target:raise ValueError('Unexpected target')
   # Refresh related memory inside the write transaction; stale LLM proposals cannot append duplicates.
   if getattr(self,'v2_enabled',False) and op in ('ADD','MERGE','UPDATE','SUPERSEDE'):
    gate=(metadata or {}).get('belief_gate',{})
    if gate.get('promotable') is not True:raise ValueError('V2 usefulness and evidence gate required')
    if c['type']=='PATTERN_EXPECTATION':raise ValueError('Evidence-only record cannot become durable belief')
   current=self.related(c)
   if getattr(self,'v2_enabled',False) and op=='ADD' and any(m.get('concept_key')==c.get('concept_key') for m in current):raise ValueError('Existing concept requires UPDATE or explicit reconciliation')
   if op=='ADD' and any(m['subject'].casefold()==c['subject'].casefold() and m['type']==c['type'] for m in current):raise ValueError('Existing subject requires explicit reconciliation')
   if old and old['source_class']=='USER_EXPLICIT' and c['source_class']!='USER_EXPLICIT' and op in ('UPDATE','SUPERSEDE'):
    op='CONFLICT';reason='Lower-authority inference cannot replace explicit user evidence. '+reason
   if getattr(self,'v2_enabled',False) and old and op=='MERGE' and old.get('concept_key')==c.get('concept_key') and old['statement']!=c['statement'] and c.get('epistemic_state')=='confirmed' and c['source_class']=='USER_EXPLICIT':op='UPDATE'
   if old and op in ('MERGE','UPDATE') and old['type']!=c['type']:raise ValueError('Cannot merge different memory classes')
   if op=='SUPERSEDE' and c['source_class']=='USER_EXPLICIT' and not c['correction']:raise ValueError('Explicit supersession requires a documented correction')
   if c['type']=='PREFERENCE' and c['source_class'] in ('HISTORICAL_PATTERN','EXECUTION_OUTCOME'):op='IGNORE';reason='Behavior and automated actions do not establish user preference.'
   if getattr(self,'v2_enabled',False) and (metadata or {}).get('future_impact'):c['future_impact']=metadata['future_impact']
   if getattr(self,'v2_enabled',False) and (metadata or {}).get('resolved_epistemic_state'):c['epistemic_state']=metadata['resolved_epistemic_state']
   stamp=now();affected=[];before=old['confidence'] if old else None
   if op=='IGNORE':state='IGNORED'
   elif op=='CONFLICT':
    # Preserve the authoritative record active when disagreement comes only from inference.
    if old and not(old['source_class']=='USER_EXPLICIT' and c['source_class']!='USER_EXPLICIT'):
     old['status']='CONFLICTED';old['updated_at']=stamp;self._put(old,op);affected.append(old['memory_id'])
    state='CONFLICT'
   elif op in ('MERGE','UPDATE'):
    m=dict(old)
    if op=='UPDATE':
     m.update(statement=c['statement'],structured_value=c['structured_value'],source_class=c['source_class'])
     for field in ('concept_key','future_impact','epistemic_state','applicability','episode_key'):
      if field in c:m[field]=c[field]
    # A lower-authority corroboration adds evidence, not stronger epistemic authority.
    if c['source_class']==old['source_class']:m['confidence']=max(old['confidence'],c['confidence']) if op=='MERGE' else c['confidence']
    if c['source_class']=='USER_EXPLICIT' and old['source_class']!='USER_EXPLICIT':m['source_class']='USER_EXPLICIT';m['confidence']=c['confidence']
    m['evidence_refs']=sorted(set(m['evidence_refs']+c['evidence_refs']));m['last_confirmed_at']=stamp;m['updated_at']=stamp
    for k in ('people','entities','areas','tags'):m[k]=sorted(set(m.get(k,[])+c.get(k,[])))
    self._put(m,op);affected.append(m['memory_id']);state='MERGED' if op=='MERGE' else 'ACCEPTED'
   else:
    mid='memory:'+key(cid);m={**c,'memory_id':mid,'status':'ACTIVE','created_at':stamp,'updated_at':stamp,'last_confirmed_at':stamp,'supersedes':[target] if old else [],'superseded_by':None,'related_memory_ids':[target] if old else []}
    if op=='SUPERSEDE':old.update(status='SUPERSEDED',superseded_by=mid,updated_at=stamp);self._put(old,op);affected.append(target)
    self._put(m,op);affected.append(mid);state='SUPERSEDED' if op=='SUPERSEDE' else 'ACCEPTED'
   duplicate_id=(metadata or {}).get('maintenance_duplicate_id')
   if duplicate_id and op in ('MERGE','CONFLICT'):
    duplicate=self.get(duplicate_id)
    if not duplicate or duplicate_id==target or duplicate['type']!=c['type']:raise ValueError('Invalid maintenance lineage')
    if op=='MERGE':
     duplicate.update(status='SUPERSEDED',superseded_by=target,updated_at=stamp)
     old=self.get(target);old['supersedes']=sorted(set(old['supersedes']+[duplicate_id]));self._put(old,'MERGE_LINEAGE')
    elif not(duplicate['source_class']=='USER_EXPLICIT' and c['source_class']!='USER_EXPLICIT'):duplicate.update(status='CONFLICTED',updated_at=stamp)
    self._put(duplicate,'WEEKLY_'+op);affected.append(duplicate_id)
   audit={'candidate':cid,'related_memories_considered':related_ids,'affected_memory_ids':affected,'operation_requested':proposal['operation'],'operation_applied':op,'reason':reason,'confidence_before':before,'confidence_after':self.get(affected[-1])['confidence'] if affected else None,'source_class':c['source_class'],'evidence_refs':c['evidence_refs'],'metadata':metadata or {}}
   self.db.execute('insert into audit values(?,?,?,?,?)',(uuid.uuid4().hex,cid,stamp,op,js(audit)));self.db.execute('update inbox set state=?,error=NULL where id=?',(state,cid))
  return audit
 def fail(self,cid,error,metadata=None):
  with self.lock,self.db:
   self.db.execute("update inbox set state='ERROR',error=? where id=? and state in ('NEW','PROCESSING','ERROR')",(str(error)[:160],cid))
   self.db.execute('insert into audit values(?,?,?,?,?)',(uuid.uuid4().hex,cid,now(),'ERROR',js({'error':str(error)[:160],'metadata':metadata or {}})))
 def shadow_retrieve(self,query,request_id,*,injection_enabled=False,**keys):
  trace={}
  with self.lock:
   result=self.retrieve(query,require_query_match=True,trace=trace,**keys)
  with self.lock,self.db:
   self.db.execute('insert or replace into retrievals values(?,?,?,?)',(request_id,now(),int(not injection_enabled),js({'query':query[:500],'would_retrieve':[m['memory_id'] for m in result],'trace':trace,'injection_enabled':bool(injection_enabled)})))
   self.db.execute('delete from retrievals where id in (select id from retrievals order by at desc limit -1 offset 2000)')
  return result
 def chain(self,mid):
  m=self.get(mid)
  if not m:return None
  return {'memory':m,'evidence':[dict(r)|{'data':json.loads(r['data'])} for ref in m['evidence_refs'] for r in self.db.execute('select * from evidence where id=?',(ref,))],'versions':[json.loads(r[0]) for r in self.db.execute('select data from versions where memory_id=? order by id',(mid,))],'audit':[json.loads(r[0]) for r in self.db.execute('select data from audit where data like ? order by at',('%'+mid+'%',))]}
 def inspect(self,table,limit=30):
  if table not in {'inbox','memories','evidence','audit','runs','retrievals','versions','metadata'}:raise ValueError('Unknown inspection view')
  return [dict(r)|({'data':json.loads(r['data'])} if 'data' in r.keys() else {}) for r in self.db.execute('select * from '+table+' order by rowid desc limit ?',(min(200,max(1,int(limit))),))]
 def render_wiki(self,directory):
  path=Path(directory);path.mkdir(parents=True,exist_ok=True)
  groups={t:[] for t in TYPES}
  for row in self.db.execute('select data from memories order by subject'):
   m=json.loads(row[0]);groups[m['type']].append(f"## {m['subject']}\n\n{m['statement']}\n\nID: {m['memory_id']} · {m['status']} · confidence {m['confidence']:.2f} · {m['source_class']}\n\nEvidence: {', '.join(m['evidence_refs'])}\n\nStructured value: `{js(m['structured_value'])}`\n")
  for t,entries in groups.items():
   p=path/(t.lower()+'.md');tmp=p.with_suffix('.tmp');tmp.write_text('# '+t+'\n\n'+'\n'.join(entries));tmp.chmod(0o600);tmp.replace(p)
