from .service import FLAGS
from .store import TABLES

def inspect(service,params,config):
 flags={k:bool(getattr(config,k,False)) for k in FLAGS};flags['memory_read_enabled']=bool(config.memory_read_enabled);flags['memory_v2_enabled']=bool(getattr(config,'memory_v2_enabled',False));flags['proactivity_v2_enabled']=bool(getattr(config,'proactivity_v2_enabled',False))
 if service and getattr(service,'delivery',None):flags.update(service.delivery.controls())
 if not service:return {'enabled':False,'flags':flags,'records':[]}
 view=params.get('view','would');identity=params.get('id');store=service.store
 if identity:return {'enabled':True,'flags':flags,'chain':store.chain(str(identity)[:160])}
 try:limit=max(1,min(100,int(params.get('limit',30))));hours=max(0,min(720,float(params.get('hours',24))))
 except (ValueError,TypeError):raise ValueError('Invalid inspection bounds')
 if view=='flags':return {'enabled':True,'flags':flags}
 if view=='delivery_status':
  delivery=getattr(service,'delivery',None);resolved=False
  if delivery:
   try:delivery.destination();resolved=True
   except ValueError:pass
  return {'enabled':bool(delivery),'worker_running':bool(delivery and delivery.task and not delivery.task.done()),'trusted_destination_resolved':resolved,'destination_ref':'configured-primary-private-chat','channels':['telegram'] if delivery else [],'voice_executor':False,'ha_action_executor':False,'flags':flags,'release_started_at':store.meta('telegram_release_started_at'),'read_only':True}
 delivery_view=view in ('delivered','suppressed','failed')
 table='deliveries' if delivery_view else 'proposals' if view=='would' else 'temporal' if view=='expectations' else view
 if table not in TABLES:raise ValueError('Unknown proactive view')
 where=[];args=[]
 if view=='would' or delivery_view:where.append('created>=?');args.append(store.clock()-hours*3600)
 if delivery_view:
  states={'delivered':('DELIVERED',),'suppressed':('SUPPRESSED','CANCELLED'),'failed':('FAILED','UNKNOWN')}[view];where.append('state in ('+','.join('?' for _ in states)+')');args.extend(states)
 if view=='expectations':where.append("json_extract(data,'$.kind')='EXPECTATION_BOUNDARY'")
 if params.get('state'):where.append('state=?');args.append(str(params['state'])[:50])
 clause=' where '+' and '.join(where) if where else ''
 rows=store.db.execute('select * from '+table+clause+' order by created desc,id desc limit ?',(*args,limit))
 records=[store.decode(r)|{'record_type':table} for r in rows]
 if view=='suppressed' and not params.get('state'):
  initial=[store.decode(r)|{'record_type':'proposals'} for r in store.db.execute("select * from proposals where created>=? and state in ('SUPPRESSED','CANCELLED') and json_extract(data,'$.delivery_id') is null order by created desc limit ?",(store.clock()-hours*3600,limit))]
  records=sorted(records+initial,key=lambda r:(r['created'],r['id']),reverse=True)[:limit]
 return {'enabled':True,'flags':flags,'view':view,'records':records,'read_only':True}
