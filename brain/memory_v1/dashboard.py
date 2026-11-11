"""Authenticated inspection projection, revisions and management-job status."""
import json

def snapshot(service,params):
 if not service:return {'enabled':False,'records':[],'total':0,'flags':{'memory_read_enabled':False}}
 store=service.store;view=params.get('view','inbox')
 if view not in ('inbox','consolidated'):raise ValueError('Unknown memory view')
 table='inbox' if view=='inbox' else 'memories';status_column='state' if view=='inbox' else 'status';query=str(params.get('q','')).strip()[:200];status=str(params.get('status',''))[:30]
 try:offset=max(0,min(100000,int(params.get('offset',0))));limit=max(1,min(50,int(params.get('limit',50))))
 except (ValueError,TypeError):raise ValueError('Invalid pagination')
 where=[];args=[]
 if status:where.append(status_column+'=?');args.append(status)
 elif not params.get('id') and view=='inbox' and getattr(store,'v2_enabled',False):where.append("state in ('NEW','ERROR','PROCESSING','AWAITING_EVIDENCE','CONFLICT')")
 elif not params.get('id'):where.append(status_column+" != ?");args.append('DELETED' if view=='inbox' else 'RETIRED')
 if query:
  # Literal substring search: percent/underscore are not wildcard operators.
  where.append("data LIKE ? ESCAPE '\\'");args.append('%'+query.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%')
 record_id=str(params.get('id',''))[:100]
 if record_id:where.append('id=?');args.append(record_id)
 clause=' where '+' and '.join(where) if where else ''
 total=store.db.execute('select count(*) from '+table+clause,args).fetchone()[0]
 records=[]
 order='created' if view=='inbox' else 'updated'
 for row in store.db.execute('select * from '+table+clause+' order by '+order+' desc,id desc limit ? offset ?',(*args,limit,offset)):
  item=dict(row);item['data']=json.loads(item['data'])
  from .management import revision
  item['revision']=revision(item)
  if record_id:
   refs=item['data'].get('evidence_refs',[]);item['evidence']=[dict(r)|{'data':json.loads(r['data'])} for ref in refs[:64] for r in store.db.execute('select * from evidence where id=?',(ref,))]
   if view=='consolidated':item['versions']=[dict(r)|{'data':json.loads(r['data'])} for r in store.db.execute('select * from versions where memory_id=? order by id desc limit 20',(record_id,))]
   item['audit']=[dict(r)|{'data':json.loads(r['data'])} for r in store.db.execute("select * from audit where candidate_id=? or instr(data,?)>0 order by at desc limit 20",(record_id,record_id))]
  records.append(item)
 counts={r[0]:r[1] for r in store.db.execute('select '+status_column+',count(*) from '+table+' group by '+status_column) if not(getattr(store,'v2_enabled',False) and r[0] in {'ARCHIVED_V1','RETIRED'})}
 return {'schema_version':2 if getattr(store,'v2_enabled',False) else 1,'semantics':'Candidate beliefs awaiting useful, grounded support' if view=='inbox' else 'Durable reusable beliefs; evidence stays separate','enabled':True,'view':view,'flags':dict(service.flags),'records':records,'total':total,'offset':offset,'limit':limit,'counts':counts,'read_only':False,'sync':service.sync_status(),'can_sync':bool(service.config.memory_write_enabled),'sync_batch_limit':service.config.memory_max_candidates_per_run}
