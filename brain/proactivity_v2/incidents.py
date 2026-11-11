"""Correlate bounded activity across short episodes, restarts and notification paths."""
from proactivity_v1.store import digest
class Incidents:
 def __init__(self,store,home=None):self.store=store;self.home=home
 def add(self,e):
  s=self.store;t=s.clock();subject=e.get('subject',{});eid=subject.get('id');area=subject.get('area') or subject.get('area_id');c=e.get('correlation',{});anchor=c.get('request_id') or c.get('parent_context_id') or c.get('ha_context_id')
  # Idle closure is persistent; an incident never spans an entire day.
  dc=(getattr(self.home,'entities',{}).get(eid,{}).get('attributes',{}).get('device_class') if self.home else subject.get('device_class'))
  consequential=dc in {'smoke','gas','carbon_monoxide','safety','heat','moisture'} and e.get('change',{}).get('new')=='on'
  candidates=[]
  for row in s.rows('incidents',('OPEN','NOTIFIED'),1000):
   d=row['data']
   if d.get('schema_version')!=2:continue
   if t-d['latest_event_at']>2700 or t-d['first_event_at']>10800:
    s.change('incidents',row['id'],state='RESOLVED',data=d|{'resolution':'Activity window ended','resolved_at':t});continue
   if consequential and d.get('notification_state')=='DELIVERED':continue
   if eid in d['entities'] or area and area in d['areas'] or anchor and anchor in d.get('correlation_anchors',[]):candidates.append(row)
  row=max(candidates,key=lambda r:r['updated']) if candidates else None
  if row is None:
   identity=s.put('incidents',{'schema_version':2,'first_event_at':t,'latest_event_at':t,'entities':[],'areas':[],'event_refs':[],'transitions':[],'current_state':{},'correlation_anchors':[],'explanations':[],'assessments':[],'notification_state':'NONE','feedback':[]},dedupe=['v2-situation',e['event_id']],state='OPEN');row=s.get('incidents',identity)
  d=row['data'];d['latest_event_at']=t
  if eid and eid not in d['entities']:d['entities']=(d['entities']+[eid])[-32:]
  if area and area not in d['areas']:d['areas']=(d['areas']+[area])[-8:]
  if anchor:d['correlation_anchors']=list(dict.fromkeys(d['correlation_anchors']+[anchor]))[-32:]
  d['event_refs']=list(dict.fromkeys(d['event_refs']+[e['event_id']]))[-128:]
  transition={'event_id':e['event_id'],'at':e.get('occurred_at'),'subject':eid,**e.get('change',{})}
  d['transitions']=(d['transitions']+[transition])[-64:]
  d['current_state'][str(eid)+':'+str(e.get('change',{}).get('property','state'))]=e.get('change',{}).get('new')
  s.change('incidents',row['id'],data=d)
  return row['id']
 def find(self,events):
  refs={e['event_id'] for e in events}
  for raw in self.store.db.execute("select * from incidents where json_extract(data,'$.schema_version')=2 order by updated desc limit 1000"):
   row=self.store.decode(raw)
   if row['data'].get('schema_version')==2 and refs.intersection(row['data'].get('event_refs',[])):return row
  return None
 def assess(self,identity,result):
  row=self.store.get('incidents',identity)
  if not row:return
  d=row['data'];d['assessments']=(d.get('assessments',[])+[result])[-16:];d['importance']=result['outcome'];d['explanations']=result.get('explanation_refs',[])
  self.store.change('incidents',identity,data=d)
 def feedback(self,identity,signal,ref):
  if signal not in {'engaged','useful','dismissed','unwanted','ignored'} or not ref:raise ValueError('Invalid feedback')
  row=self.store.get('incidents',identity)
  if not row:raise ValueError('Unknown incident')
  d=row['data'];feedback={'signal':signal,'source_ref':ref,'at':self.store.clock()}
  if any(f.get('source_ref')==ref for f in d.get('feedback',[])):return
  d['feedback']=(d.get('feedback',[])+[feedback])[-32:]
  self.store.change('incidents',identity,data=d);self.store.audit('incident_feedback',{'incident_id':identity,**feedback,'policy_effect':'Evidence only; one ignored message never changes notification policy.'})
