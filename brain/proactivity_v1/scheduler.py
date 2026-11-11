"""One durable nearest-wake scheduler, shared by proactive temporal work."""
import asyncio,math
from .store import AUTH
class Scheduler:
 def __init__(self,store):self.store=store;self.changed=asyncio.Event()
 def add(self,at,kind,authority,payload,dedupe):
  if authority not in AUTH or not isinstance(at,(int,float)) or isinstance(at,bool) or not math.isfinite(at):raise ValueError('Invalid temporal authority/time')
  if kind not in {'EXPECTATION_BOUNDARY','WATCH_TIMEOUT','SELF_RECHECK','COMMITMENT_DUE','EXECUTION_CHECK','HEALTH_CHECK','EXPECTATION_SCAN','EPISODE_CLOSE','TELEGRAM_DELIVERY_RETRY'}:raise ValueError('Invalid temporal type')
  identity=self.store.put('temporal',{'kind':kind,'authority':authority,**payload},dedupe=dedupe,due=float(at));self.changed.set();return identity
 def cancel(self,identity,reason='cancelled'):
  row=self.store.get('temporal',identity)
  if row and row['state'] in ('PENDING','PROCESSING'):
   self.store.change('temporal',identity,state='CANCELLED',data=row['data']|{'closure_reason':reason});self.changed.set();return True
  return False
 def nearest(self):
  r=self.store.db.execute("select min(at) from (select due as at from temporal where state='PENDING' union all select lease as at from temporal where state='PROCESSING')").fetchone();return r[0] if r else None
 async def run_due(self,handler):
  self.store.recover();rows=self.store.db.execute("select id from temporal where state='PENDING' and due<=? order by due,id limit 100",(self.store.clock(),)).fetchall()
  for (identity,) in rows:
   if not self.store.claim('temporal',identity):continue
   row=self.store.get('temporal',identity)
   try:
    late=self.store.clock()-row['due']
    if late>86400 and row['data']['authority']!='USER_EXPLICIT':result={'state':'STALE','reason':'Overdue self work requires a fresh source; not replayed.'}
    elif late>86400 and row['data']['kind']=='COMMITMENT_DUE':result={'state':'EXPIRED','reason':'Explicit commitment exceeded its bounded overdue delivery policy.'}
    else:result=await handler(row) or {}
    latest=self.store.get('temporal',identity)
    if latest['state']=='PROCESSING':self.store.change('temporal',identity,state=result.get('state','DONE'),data=latest['data']|{'result':result})
   except asyncio.CancelledError:raise
   except Exception as error:self.store.change('temporal',identity,state='ERROR',data=row['data']|{'error_type':type(error).__name__})
 async def loop(self,handler):
  while True:
   self.changed.clear()
   try:await self.run_due(handler);due=self.nearest()
   except asyncio.CancelledError:raise
   except Exception as error:
    self.last_error=type(error).__name__;await asyncio.sleep(5);continue
   if self.changed.is_set():continue
   # No polling heartbeat: a new earlier wake or nearest due time wakes this task.
   if due is None:await self.changed.wait()
   else:
    try:await asyncio.wait_for(self.changed.wait(),timeout=max(.01,min(300,due-self.store.clock())))
    except asyncio.TimeoutError:pass
