"""Passive queued collection. One SQLite worker, no speech/action ownership."""
import asyncio,concurrent.futures,json,math,time,uuid
from datetime import datetime,timezone
from pathlib import Path
from .store import Store,classify
SESSION_DOMAINS={'light','switch','fan','climate','media_player','humidifier','water_heater','vacuum','cover','lock','binary_sensor','input_boolean','sensor','number','input_number'}

def stamp(value):
 try:return datetime.fromisoformat(str(value).replace('Z','+00:00')).timestamp()
 except (ValueError,TypeError):return 0.
class Service:
 def __init__(self,path,home,world,config,memory=None,busy=lambda:False):
  self.path=Path(path);self.home=home;self.world=world;self.config=config;self.memory=memory;self.busy=busy
  self.pool=None;self.store=None;self.task=None;self.monitor=None;self.analysis_task=None;self.queue=asyncio.Queue(maxsize=4096);self.ids=set();self.errors=0;self.dropped=0;self.last_error=None;self.job={'status':'IDLE'};self.started=False;self.revision=None
 async def work(self,fn,*args):return await asyncio.get_running_loop().run_in_executor(self.pool,fn,*args)
 def selections(self):
  policy=getattr(self.home,'input_policy',None)
  if not policy or not self.config.memory_write_enabled:
   self.world.pattern_dependencies=set();self.event_sources={};return []
  result=[]
  for eid,e in self.home.entities.items():
   domain=e.get('domain')
   if domain not in SESSION_DOMAINS or not policy.entity('memory',eid):continue
   mode={'climate':'enabled time','media_player':'powered / reported active time','sensor':'numeric observations','number':'numeric observations','input_number':'numeric observations','cover':'reported open time','lock':'reported locked time'}.get(domain,'reported active time')
   result.append({'id':eid,'name':e.get('name') or eid,'domain':domain,'mode':mode,'area':e.get('area_id'),'unit':e.get('attributes',{}).get('unit_of_measurement'),'assumed_state':bool(e.get('attributes',{}).get('assumed_state'))})
  if policy.source('memory','presence'):
   for row in self.world.store.db.execute("select data from facts where subject like 'person:%' and property='presence'"):
    f=json.loads(row[0]);sid=f['subject']['id']
    if policy.entity('memory',sid):result.append({'id':sid,'name':f['subject'].get('semantic_name') or sid[7:],'domain':'presence','mode':'coarse location visits','unit':None})
  from .semantics import controller_links
  dependencies=[]
  for desc in result:
   desc['usage_subject']=None
   link=controller_links(self.home,desc['id']) if desc['domain'] not in ('presence','sensor','number','input_number','input_boolean') else None
   if not link:continue
   source='usage:'+desc['id']+':'+link['entity_id']
   desc.update(usage_subject=source,usage_control=link,usage_learning_allowed=policy.entity('memory',link['entity_id']))
   dependencies.append({'id':source,'name':link['name'],'domain':'input_boolean','mode':'controller state dependency',
                        'internal':True,'owner':desc['id'],'source_entity':link['entity_id']})
  self.world.pattern_dependencies={d['source_entity'] for d in dependencies}
  self.event_sources={}
  for desc in result+dependencies:self.event_sources.setdefault(desc.get('source_entity',desc['id']),[]).append(desc['id'])
  return result+dependencies
 def snapshots(self,descriptors):
  now=time.time();rows=[]
  for d in descriptors:
   source=d.get('source_entity',d['id'])
   f=self.world.store.get(source,'presence' if d['domain']=='presence' else 'state')
   value=f.get('value') if f and f.get('availability')=='available' else None
   if f is None and d.get('internal'):
    entity=self.home.entities.get(source,{})
    value=entity.get('state') if entity.get('available') else None
   if d['domain']=='presence' and f and now-stamp(f.get('observed_at'))>getattr(self.config,'presence_max_age_seconds',3600):value=None
   rows.append((d['id'],value,now,'current-snapshot',False))
  return rows
 async def start(self):
  if self.started:return
  self.path.parent.mkdir(parents=True,exist_ok=True);self.pool=concurrent.futures.ThreadPoolExecutor(max_workers=1,thread_name_prefix='device-patterns')
  self.store=await self.work(Store,self.path,self.config.timezone)
  previous=await self.work(self.store.meta,'last_job',{'status':'IDLE'})
  self.job={**previous,'status':'INTERRUPTED','phase':'Analysis interrupted by restart'} if previous.get('status') in ('RUNNING','QUEUED') else previous
  descriptors=self.selections();self.ids={d['id'] for d in descriptors};observations=self.snapshots(descriptors)
  await self.work(self._sync,descriptors,observations,bool(self.world.connected),True)
  self.world.bus.subscribe(self.on_event);self.task=asyncio.create_task(self.writer(),name='device-pattern-writer');self.monitor=asyncio.create_task(self.loop(),name='device-pattern-health');self.started=True
  self.repair_task=asyncio.create_task(self.backfill_controllers(descriptors),name='device-pattern-controller-history')
  self.dependency_revision=tuple(d['id'] for d in descriptors if d.get('internal'))
 async def backfill_controllers(self,descriptors):
  from urllib.parse import urlencode
  try:
   await self.queue.join()
   changed=False
   for descriptor in descriptors:
    if not descriptor.get('internal'):continue
    if not self.home.input_policy.source('memory','history'):continue
    record=await self.work(self.store.get,descriptor['id'])
    if record.get('history_imported') and not record.get('restart_pending'):continue
    owner=await self.work(self.store.get,descriptor['owner']);start=max(owner['enrolled'],time.time()-90*86400);until=record['enabled_since']
    if record.get('history_imported') and record.get('restart_pending'):
     pending=record['restart_pending'];start=max(start,pending.get('open_start') or pending['last_at'])
    query=urlencode({'end_time':datetime.fromtimestamp(until,timezone.utc).isoformat(),'filter_entity_id':descriptor['source_entity'],
                     'minimal_response':'true','no_attributes':'true','significant_changes_only':'true'})
    path='/api/history/period/'+datetime.fromtimestamp(start,timezone.utc).isoformat()+'?'+query
    data=await self.home.ha.request_bounded('GET',path,max_bytes=2000000)
    rows=next((x for x in data if x and x[0].get('entity_id')==descriptor['source_entity']),[])
    if not rows:continue
    await self.work(self.store.import_controller_history,descriptor['id'],rows,start,until);changed=True
   if changed or await self.work(self.store.meta,'analysis_version',0)<3:
    # Rebuild corrected evidence only. This maintenance does not run memory models.
    await self.queue.join()
    await self.work(self.store.analyze,'pattern-semantics-'+uuid.uuid4().hex,time.time())
  except Exception as error:
   self.last_error={'type':type(error).__name__,'message':'Controller pattern history: '+str(error)[:180]}
 def _sync(self,descriptors,observations,connected,restarting=False):
  now=time.time();self.store.select(descriptors,now,restarting)
  if not connected:self.store.disconnected(now)
  else:
   for args in observations:self.store.observe(*args)
  self.store.refresh_rollups(now)
  self.store.set_meta('last_checkpoint',now);self.store.db.commit()
 def on_event(self,event):
  sid=event.subject.get('id');prop=event.change.get('property')
  targets=getattr(self,'event_sources',{}).get(sid,[sid] if sid in self.ids else [])
  if not targets or prop not in ('state','presence','availability'):return
  value=event.change.get('new')
  if prop=='availability':
   if value=='available':return # Next state/snapshot establishes a new partial interval.
   value=None
  at=stamp(event.occurred_at)
  if not at or at>time.time()+60:return
  for target in targets:self.enqueue(('observe',(target,value,at,event.event_id,True)))
 def enqueue(self,item):
  try:self.queue.put_nowait(item)
  except asyncio.QueueFull:self.dropped+=1
 def refresh_selection(self):
  if not self.started:return
  descriptors=self.selections();self.ids={d['id'] for d in descriptors};self.revision=getattr(self.home,'input_policy',None).revision
  self.enqueue(('sync',(descriptors,self.snapshots(descriptors),bool(self.world.connected),False)))
  revision=tuple(d['id'] for d in descriptors if d.get('internal'))
  if revision!=getattr(self,'dependency_revision',()) and (not getattr(self,'repair_task',None) or self.repair_task.done()):
   self.dependency_revision=revision;self.repair_task=asyncio.create_task(self.backfill_controllers(descriptors),name='device-pattern-controller-history')
 def _batch(self,items):
  for kind,args in items:
   if kind=='sync':self._sync(*args)
   else:self.store.observe(*args)
  self.store.db.commit()
 async def writer(self):
  while True:
   item=await self.queue.get();items=[item]
   while len(items)<128 and not self.queue.empty():items.append(self.queue.get_nowait())
   try:await self.work(self._batch,items)
   except Exception as error:self.errors+=1;self.last_error={'type':type(error).__name__,'message':str(error)[:200]}
   finally:
    for _ in items:self.queue.task_done()
 async def loop(self):
  last_dropped=0
  while True:
   await asyncio.sleep(30)
   if self.dropped!=last_dropped:
    await self.work(self.store.disconnected,time.time());last_dropped=self.dropped
   self.refresh_selection()
 async def snapshot(self,subject=None,day=None):
  if subject and len(subject)>200:raise ValueError('Invalid subject')
  data=await self.work(self.store.snapshot,time.time(),subject,day)
  data.update(enabled=True,connected=bool(self.world.connected),queue_depth=self.queue.qsize(),errors=self.errors,dropped=self.dropped,last_error=self.last_error,job=dict(self.job),selection_revision=getattr(self.home,'input_policy',None).revision,learning_enabled=bool(self.memory and self.config.memory_write_enabled),selection_note='Uses effective Memory inputs from Sunday Inputs. Proactivity permission is separate. Existing history remains when an input is disabled.')
  return data
 def analyze(self):
  if self.analysis_task and not self.analysis_task.done():return self.job
  self.job={'id':uuid.uuid4().hex,'status':'QUEUED','phase':'Waiting for current collection to finish','started':time.time()};self.analysis_task=asyncio.create_task(self._analyze(),name='device-pattern-analysis');return self.job
 async def _analyze(self):
  try:
   await self.queue.join();self.job.update(status='RUNNING',phase='Calculating sessions and day/week/month patterns')
   result=await self.work(self.store.analyze,self.job['id'],time.time())
   self.job.update(result,status='COMPLETED',phase='Patterns updated',finished=time.time(),memory={'status':'OBSERVATION_WEEK','candidates':0})
   if result['strong_patterns']:
    self.job.update(status='RUNNING',phase='Reviewing useful patterns for memory')
    self.job['memory']=await self.learn()
    self.job.update(status='COMPLETED',phase='Patterns updated',finished=time.time())
  except asyncio.CancelledError:raise
  except Exception as error:self.job.update(status='ERROR',phase='Analysis failed',error_type=type(error).__name__,message=str(error)[:240],finished=time.time())
  finally:
   await self.work(self.store.set_meta,'last_job',dict(self.job));await self.work(self.store.db.commit)
 async def learn(self):
  policy=getattr(self.home,'input_policy',None)
  if not self.memory or not self.config.memory_write_enabled or not policy.source('memory','inferences') or not policy.source('memory','history'):return {'status':'DISABLED_BY_MEMORY_INPUTS','candidates':0}
  if self.busy():return {'status':'DEFERRED_WHILE_REPLYING','candidates':0}
  data=await self.work(self.store.snapshot,time.time());patterns=[p for p in data['patterns'] if p['strong_enough'] and p['subject'] in self.ids][:6]
  if not patterns:return {'status':'INSUFFICIENT_EVIDENCE','candidates':0}
  revision=policy.revision
  from .learning import interpret
  return await interpret(self,patterns,revision)
 async def close(self):
  if getattr(self,'repair_task',None):self.repair_task.cancel();await asyncio.gather(self.repair_task,return_exceptions=True)
  self.world.pattern_dependencies=set()
  if self.on_event in self.world.bus.consumers:self.world.bus.consumers.remove(self.on_event)
  if self.monitor:self.monitor.cancel();await asyncio.gather(self.monitor,return_exceptions=True)
  if self.analysis_task and not self.analysis_task.done():self.analysis_task.cancel();await asyncio.gather(self.analysis_task,return_exceptions=True)
  if self.task:
   try:await asyncio.wait_for(self.queue.join(),5)
   except asyncio.TimeoutError:pass
   self.task.cancel();await asyncio.gather(self.task,return_exceptions=True)
  if self.store:await self.work(self.store.close)
  if self.pool:self.pool.shutdown(wait=False)
