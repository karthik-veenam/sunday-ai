"""Explicit dashboard edits/removals with history, stale-write protection and tombstones."""
import json
import uuid
from .store import now,js,key,check_text,strings

class Conflict(ValueError):pass

def fingerprint(c):
 return key([c['type'],' '.join(c['subject'].casefold().split()),' '.join(c['statement'].casefold().split())])

def anchors(c):
 result=[fingerprint(c)]
 if c.get('concept_key'):result.append('concept:'+key(c['concept_key']))
 group=(c.get('structured_value') or {}).get('group') if isinstance(c.get('structured_value'),dict) else None
 if c['type']=='PATTERN_EXPECTATION' and group:result.append('pattern:'+key(group))
 return result

def blocked(store,c):
 if c.get('dashboard_edited'):return False
 keys=anchors(c)
 return bool(store.db.execute('select 1 from memory_controls where fingerprint in ('+','.join('?' for _ in keys)+')',keys).fetchone())

def revision(item):return key([item.get('state',item.get('status')),item['data']])

def change(service,body):
 if not service:raise ValueError('Memory is disabled')
 if not isinstance(body,dict) or set(body)-{'action','view','id','revision','patch'}:raise ValueError('Invalid request fields')
 action=body.get('action');view=body.get('view');rid=body.get('id')
 if action not in ('edit','remove') or view not in ('inbox','consolidated') or not isinstance(rid,str):raise ValueError('Invalid action or record')
 store=service.store;table='inbox' if view=='inbox' else 'memories';col='state' if view=='inbox' else 'status'
 with store.lock,store.db:
  row=store.db.execute('select * from '+table+' where id=?',(rid,)).fetchone()
  if not row:raise KeyError('Memory not found')
  item=dict(row);item['data']=json.loads(item['data']);old=item['data']
  if not body.get('revision') or body['revision']!=revision(item):raise Conflict('This memory changed. Refresh it before saving.')
  if action=='edit' and ((view=='inbox' and row[col] not in ('NEW','ERROR','PROCESSING','AWAITING_EVIDENCE')) or (view=='consolidated' and row[col] not in ('ACTIVE','CONFLICTED'))):raise Conflict('Edit an active consolidated memory or a pending inbox item.')
  stamp=now();updated=dict(old)
  if action=='edit':
   patch=body.get('patch')
   if not isinstance(patch,dict) or not patch or set(patch)-{'subject','statement','tags','people','entities','areas'}:raise ValueError('Invalid editable fields')
   for field,value in patch.items():
    updated[field]=check_text(value,field,200 if field=='subject' else 1200) if field in ('subject','statement') else strings(value)
   if all(updated[k]==old.get(k) for k in patch):return {'action':'unchanged','id':rid}
   if old['type']=='PATTERN_EXPECTATION':updated.update(type='FACT',structured_value=None)
   evidence_id=store.evidence('dashboard_edit','home_assistant_dashboard',{'statement':updated['statement'],'subject':updated['subject'],'record_id':rid,'operation':'explicit_correction'})
   updated.update(source_class='USER_EXPLICIT',confidence=max(.95,old['confidence']),correction=True,updated_at=stamp,last_confirmed_at=stamp,evidence_refs=sorted(set(old['evidence_refs']+[evidence_id])),dashboard_edited=True)
  # Block old candidate replays, including re-derived statistics for a removed/overridden pattern.
  for anchor in anchors(old):store.db.execute('insert or replace into memory_controls values(?,?,?,?)',(anchor,rid,action,stamp))
  if view=='consolidated':
   if action=='remove':updated.update(status='RETIRED',updated_at=stamp,dashboard_removed=True)
   store._put(updated,'DASHBOARD_'+action.upper())
  else:
   state='DELETED' if action=='remove' else 'NEW'
   if action=='remove':updated['dashboard_removed']=True
   store.db.execute('update inbox set state=?,data=?,error=NULL where id=?',(state,js(updated),rid))
  store.db.execute('insert into audit values(?,?,?,?,?)',(uuid.uuid4().hex,rid if view=='inbox' else None,stamp,'DASHBOARD_'+action.upper(),js({'record_id':rid,'view':view,'actor':'home_assistant_dashboard','before':old,'after':updated})))
 store.render_wiki(store.path.parent/'memory_v1_wiki')
 if getattr(service,'on_change',None):service.on_change()
 return {'action':action,'id':rid,'view':view,'message':'Saved' if action=='edit' else 'Removed from active memory; audit history retained'}
