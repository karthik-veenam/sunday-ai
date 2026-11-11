"""Candidate identity, independent support, reconciliation and durable-use gates."""
import json,re,uuid
from datetime import datetime,timezone
from memory_v1.store import key,js,now,check_text
FIELDS=('concept_key','future_impact','epistemic_state','episode_key','applicability')
EPISTEMIC={'uncertain','confirmed','disputed'}
def enabled(store):return bool(getattr(store,'v2_enabled',False))
def enrich(candidate,data):
 for k in FIELDS:
  if k in candidate:
   value=candidate[k]
   if k=='applicability':
    if not isinstance(value,dict) or set(value)-{'local_hours','weekdays','entities','areas','presence','people'}:raise ValueError('Invalid belief applicability')
    if len(js(value))>2000:raise ValueError('Applicability too large')
   elif k=='future_impact' and value=='':value=''
   else:value=check_text(value,k,800 if k=='future_impact' else 200)
   if k=='epistemic_state' and value not in EPISTEMIC:raise ValueError('Invalid epistemic state')
   data[k]=value
 data['schema_version']=2
 data.setdefault('concept_key',data['type'].lower()+':'+re.sub(r'\s+',' ',data['subject'].casefold()).strip())
 data.setdefault('epistemic_state','uncertain' if data['source_class']!='USER_EXPLICIT' or data['confidence']<.85 else 'confirmed')
 data.setdefault('future_impact','')
 return data

def support(store,refs):
 independent=set();explicit=False
 for ref in refs:
  r=store.db.execute('select kind,data from evidence where id=?',(ref,)).fetchone()
  if not r:continue
  d=json.loads(r['data']);identity=d.get('episode_key') or d.get('incident_id') or d.get('start')
  if identity:independent.add(str(identity))
  explicit|=r['kind'] in {'user_statement','dashboard_edit'}
 return len(independent),explicit

def upsert_candidate(store,c,cid,dedupe):
 """Only admitted input reaches here. Update one concept while preserving evidence/audit."""
 stamp=now();concept=c['concept_key']
 existing=None
 for row in store.db.execute("select * from inbox where state in ('NEW','ERROR','PROCESSING','AWAITING_EVIDENCE','ACCEPTED','MERGED','SUPERSEDED','CONFLICT') order by created desc limit 1000"):
  old=json.loads(row['data'])
  if old.get('concept_key')==concept and set(old.get('people',[]))==set(c.get('people',[])):
   existing=(row,old);break
 if existing:
  row,old=existing
  # Inferences cannot erase explicitly confirmed knowledge; expose a conflict.
  lower=old['source_class']=='USER_EXPLICIT' and c['source_class']!='USER_EXPLICIT'
  if lower and old.get('epistemic_state')=='confirmed':
   c['epistemic_state']='disputed';c['conflicts_with_candidate']=row['id']
  else:
   if c['statement']==old['statement'] and set(c['evidence_refs'])<=set(old['evidence_refs']):return row['id']
   c['created_at']=old['created_at'];c['evidence_refs']=sorted(set(old['evidence_refs']+c['evidence_refs']))
   c['candidate_version']=int(old.get('candidate_version',1))+1
   c['previous_statement']=old['statement'];c['updated_at']=stamp
   # Later explicit confirmation resolves uncertainty for this concept/episode.
   if c['source_class']=='USER_EXPLICIT' and c.get('epistemic_state')=='confirmed' and old.get('epistemic_state')!='confirmed':c['correction']=True
   store.db.execute("update inbox set state='NEW',data=?,error=NULL where id=?",(js(c),row['id']))
   store.db.execute('insert into audit values(?,?,?,?,?)',(uuid.uuid4().hex,row['id'],stamp,'CANDIDATE_RECONCILED',js({'before':old,'after':c,'reason':'Later supported evidence revises the same concept; earlier wording is audit only.'})))
   return row['id']
 c['candidate_version']=1;c['updated_at']=stamp
 store.db.execute("insert or ignore into inbox values(?,?,'NEW',?,?,NULL)",(cid,dedupe,stamp,js(c)))
 return cid

def promotion(store,c,assessment=None):
 """Confidence is necessary but never sufficient. Weak statistics never become knowledge."""
 n,explicit=support(store,c['evidence_refs']);assessment=assessment or {}
 if c['type']=='PATTERN_EXPECTATION':return False,'Measured distributions remain historical evidence, not durable beliefs.'
 if c.get('epistemic_state')=='disputed':return False,'Unresolved conflicting evidence.'
 if c['type']=='EPISODE':
  impact=(assessment.get('future_impact') or c.get('future_impact','')).strip()
  if explicit and c['source_class'] in ('USER_EXPLICIT','USER_INFERRED') and c['confidence']>=.8 and len(impact)>=16 and assessment.get('reusable') is True and assessment.get('grounded') is True and assessment.get('episodic_only') is True:
   return True,impact
  return False,'Episode needs user-grounded evidence, useful scoped impact and explicit episodic assessment.'
 impact=(assessment.get('future_impact') or c.get('future_impact','')).strip()
 if len(impact)<16 or assessment.get('reusable') is not True or assessment.get('grounded') is not True:return False,'No grounded, meaningful future reasoning or behavior change established.'
 if c.get('epistemic_state')!='confirmed' and not(n>=3 and assessment.get('epistemic_state')=='confirmed'):return False,'Candidate uncertainty needs resolution before durable promotion.'
 if c['source_class']!='USER_EXPLICIT' and (n<3 or c['confidence']<.75):return False,'Inferred belief needs at least three independent supporting episodes and sufficient confidence.'
 if c['source_class']=='USER_EXPLICIT' and (not explicit or c['confidence']<.8):return False,'Explicit belief lacks direct evidence or supported confidence.'
 if assessment.get('episodic_only') is True:return False,'An incident summary is not reusable knowledge.'
 return True,impact

def reconcile_assessment(store,candidate,proposal,metadata):
 assessment=proposal.pop('assessment',{})
 if not isinstance(assessment,dict):raise ValueError('Invalid usefulness assessment')
 ok,reason=promotion(store,candidate['data'],assessment)
 metadata['belief_gate']={'promotable':ok,'reason':reason,'assessment':assessment,'support_count':support(store,candidate['data']['evidence_refs'])[0]}
 if not ok:return {'operation':'IGNORE','reason':reason} if assessment.get('reusable') is False or candidate['data']['type']=='PATTERN_EXPECTATION' else None
 metadata['future_impact']=reason
 if assessment.get('epistemic_state')=='confirmed':metadata['resolved_epistemic_state']='confirmed'
 return proposal

def audit_deferred(store,cid,metadata):
 with store.lock,store.db:
  row=store.db.execute('select data from inbox where id=?',(cid,)).fetchone()
  if not row or metadata.get('candidate_revision') and key(json.loads(row[0]))!=metadata['candidate_revision']:return {'candidate':cid,'status':'candidate_changed'}
  store.db.execute("update inbox set state='AWAITING_EVIDENCE',error=NULL where id=?",(cid,))
  store.db.execute('insert into audit values(?,?,?,?,?)',(uuid.uuid4().hex,cid,now(),'AWAITING_EVIDENCE',js(metadata)))
 return {'candidate':cid,'status':'AWAITING_EVIDENCE',**metadata.get('belief_gate',{})}

def migrate(store):
 if store.meta('memory_v2_migration'):return store.meta('memory_v2_migration')
 with store.lock,store.db:
  inbox=store.db.execute("select * from inbox where state!='ARCHIVED_V1'").fetchall();memories=store.db.execute('select data from memories').fetchall()
  for r in inbox:
   store.db.execute('insert into audit values(?,?,?,?,?)',(uuid.uuid4().hex,r['id'],now(),'V2_ARCHIVE_V1_INBOX',js(dict(r))))
  store.db.execute("update inbox set state='ARCHIVED_V1' where state!='ARCHIVED_V1'")
  for r in memories:
   m=json.loads(r[0])
   if m['status'] in {'ACTIVE','CONFLICTED'}:m.update(status='RETIRED',updated_at=now(),v2_reset=True);store._put(m,'V2_ARCHIVE_V1_MEMORY')
  receipt={'at':now(),'inbox_archived':len(inbox),'memories_examined':len(memories),'evidence_preserved':store.db.execute('select count(*) from evidence').fetchone()[0]}
  store.db.execute('insert or replace into metadata values(?,?)',('memory_v2_migration',js(receipt)))
 return receipt

def retrieval_weight(memory,clock=None):
 if memory.get('type')!='EPISODE':return 1.0
 try:age=max(0,(clock or datetime.now(timezone.utc).timestamp())-datetime.fromisoformat(memory['updated_at']).timestamp())
 except (ValueError,KeyError):return 0
 return 2**(-age/604800)
