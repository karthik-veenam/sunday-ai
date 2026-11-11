"""Twelve synthetic shadow E2E fixtures. All DBs/providers/clocks are isolated."""
import asyncio,json,tempfile
from pathlib import Path
from datetime import datetime
from unittest.mock import AsyncMock
from config import Config
from memory_v1.service import MemoryService
from .service import Service
from .expectations import Expectations
class Clock:
 def __init__(self):self.t=datetime.fromisoformat('2026-10-19T02:30:00+00:00').timestamp()
 def __call__(self):return self.t
class Models:
 def __init__(self,attention='NORMAL',outcome='IGNORE'):self.calls=[];self.attention=attention;self.outcome=outcome
 async def call(self,kind,packet,ref):
  self.calls.append(kind)
  if kind=='attention':return 'decision: '+self.attention+'\nreason: Supplied provenance and presence determine relevance.','fixture-attention'
  p={'operation':self.outcome,'reason':'Supported contextual follow-up.','confidence':.9}
  if self.outcome in ('TELL_USER','ASK_USER'):p.update(message='No office today? 👀' if self.outcome=='ASK_USER' else 'The device session may be unintended.',destination='telegram',already_known=False,consequence_of_silence='The user may miss relevant contextual information.',time_sensitive=False,useful_now=True,can_resolve_silently=False,interruption_cost='low')
  return json.dumps({'outcomes':[p],'reason':'Bounded synthetic evidence.'}),'fixture-cognition'
def ev(identity='e',request=None,automation=False):
 return {'event_id':identity,'event_type':'DEVICE_STATE_CHANGED','occurred_at':'2026-10-19T02:30:00+00:00','observed_at':'2026-10-19T02:30:00+00:00','subject':{'id':'fan.generic','kind':'entity'},'change':{'property':'state','old':'off','new':'on'},'provenance':{'initiator':{'type':'HA_AUTOMATION' if automation else 'UNKNOWN'},'attribution':'CONFIRMED' if automation else 'UNKNOWN','evidence':[]},'correlation':{'request_id':request}}
def strong(version='v1',minute=640):
 return {'memory_id':'fixture-memory','type':'PATTERN_EXPECTATION','status':'ACTIVE','confidence':.9,'updated_at':version,'created_at':version,'last_confirmed_at':version,'subject':'Resident office expectation','statement':'Synthetic supported weekday arrival pattern.','structured_value':{'group':['presence_session','Resident','OFFICE','Monday','room'],'statistics':{'strong_enough':True,'sample_count':20,'last_recalculation':'2026-10-19T02:30:00+00:00'},'evidence_window':{'end':'2026-10-18T02:30:00+00:00'},'temporal_boundaries':{'arrival_after_local_minutes':minute}},'source_class':'HISTORICAL_PATTERN','evidence_refs':['fixture-evidence'],'supersedes':[],'superseded_by':None,'people':['Resident'],'entities':[],'areas':['room'],'tags':['pattern']}
async def scenario(label):
 with tempfile.TemporaryDirectory() as directory:
  root=Path(directory);clock=Clock();cfg=Config(proactive_cognition_enabled=True,ha_attention_enabled=True,expectation_engine_enabled=True,self_watch_enabled=True,memory_write_enabled=True)
  if label in 'DEFGK':cfg.memory_read_enabled=True;cfg.proactive_memory_read_enabled=True
  memory=MemoryService(root/'memory.db',cfg);models=Models();service=Service(root/'proactive.db',cfg,memory=memory,clock=clock,models=models);service.context=AsyncMock(return_value={'fresh':True,'healthy':True,'quiet':False,'world':[],'history':[],'memory':[],'threads':[]})
  try:
   if label in 'ABC':
    models.attention='NOTEWORTHY' if label=='B' else 'NORMAL';models.outcome='TELL_USER';event=ev(automation=label=='C');i=service.episodes.add(event);clock.t+=10
    if i:await service.close_episode(i)
    for wake in service.store.rows('wakes',('PENDING',)):await service.cognize(wake)
    assert ('cognition' in models.calls)==(label=='B')
   elif label in 'DEFGK':
    m=strong();memory.store.evidence('fixture','synthetic',{'synthetic':True,'sample_count':20},'fixture-evidence')
    with memory.store.db:memory.store._put(m,'SYNTHETIC_FIXTURE_ONLY')
    ids=service.scan_expectations();assert len(ids)==1
    if label=='G':
     m=strong('v2',700)
     with memory.store.db:memory.store._put(m,'SYNTHETIC_UPDATE')
     new=service.scan_expectations();assert service.store.get('temporal',ids[0])['state']=='CANCELLED';assert service.store.get('temporal',new[0])['due']>service.store.get('temporal',ids[0])['due']
    else:
     if label=='K':
      await service.close();service=Service(root/'proactive.db',cfg,memory=memory,clock=clock,models=models);service.context=AsyncMock(return_value={'fresh':True,'healthy':True,'quiet':False})
     service.recheck=AsyncMock(return_value={'fresh':label!='F','healthy':label!='F','presence':'OFFICE' if label=='D' else 'UNKNOWN' if label=='F' else 'HOME','complete_history':label!='F','arrival_occurred':label=='D'});models.outcome='ASK_USER';clock.t=service.store.get('temporal',ids[0])['due'];await service.scheduler.run_due(service.temporal);await service.scheduler.run_due(service.temporal)
     for wake in service.store.rows('wakes',('PENDING',)):await service.cognize(wake)
     assert len(service.store.rows('wakes'))==(1 if label in 'EK' else 0)
   elif label=='H':
    identity=service.commitment(clock()+5,'reminder','Call the dentist.','user-request',['user-request']);clock.t+=5;await service.scheduler.run_due(service.temporal);assert service.store.get('temporal',identity)['data']['authority']=='USER_EXPLICIT';assert models.calls==[];assert service.store.rows('proposals')[0]['data']['proposal']['message']=='Call the dentist.'
   elif label=='I':
    i=service.self_recheck(clock()+5,'fan.generic',['unresolved-evidence']);clock.t+=5;await service.scheduler.run_due(service.temporal)
    for wake in service.store.rows('wakes',('PENDING',)):await service.cognize(wake)
    assert service.store.rows('wakes')[0]['data']['authority']=='SUNDAY_SELF';assert models.calls==['cognition']
   elif label=='J':
    receipt={'action_id':'a','request_id':'r','domain':'light','service':'turn_on','targets':['light.generic']};i=service.execution.intent(receipt);service.execution.result('a',unknown=True);clock.t+=9;await service.scheduler.run_due(service.temporal);assert service.store.rows('wakes')[0]['data']['no_retry'];assert len(service.store.rows('executions'))==1
   elif label=='L':
    models.attention='NOTEWORTHY';models.outcome='TELL_USER';i=service.episodes.add(ev(request='r'));clock.t+=10;await service.close_episode(i);receipt={'action_id':'a','request_id':'r','domain':'light','service':'turn_on','targets':['light.generic']};a=service.execution.intent(receipt);service.execution.result('a',unknown=True);service.execution.due(a)
    for wake in service.store.rows('wakes',('PENDING',)):await service.cognize(wake)
    assert len(service.store.rows('wakes'))==1;assert len(service.store.rows('proposals'))<=1
   proposals=service.store.rows('proposals');assert all(r['data']['external_effect'] is False for r in proposals);assert not cfg.proactive_tell_enabled and not cfg.proactive_ask_enabled and not cfg.proactive_act_enabled
   return {'fixture':label,'passed':True,'model_calls':models.calls,'wake_count':len(service.store.rows('wakes')),'temporal':[{'kind':r['data']['kind'],'state':r['state']} for r in service.store.rows('temporal')],'proposed_effects':[r['data'] for r in proposals],'external_effects':0,'production_memory_touched':False}
  finally:await service.close();memory.close()
async def run(output=None):
 results=[await scenario(label) for label in 'ABCDEFGHIJKL'];report={'isolated':True,'all_passed':all(x['passed'] for x in results),'fixtures':results,'external_effects':0}
 with tempfile.TemporaryDirectory() as directory:
  from .store import Store
  from .policy import Gates
  store=Store(Path(directory)/'examples.db');gates=Gates(store,Config(),lambda action:True)
  wake={'authority':'USER_EXPLICIT','subject':'light.fixture','evidence_refs':['synthetic-explicit-user-action'],'incident_id':'synthetic-action-incident'}
  proposed={'operation':'ACT','reason':'Synthetic explicit target-specific request; no real device exists.','confidence':1.0,'action':{'domain':'light','service':'turn_off','entity_ids':['light.fixture'],'data':{}}}
  gate=gates.evaluate(wake,proposed,{'fresh':True,'healthy':True,'action_authorized':True});report['would_act_example']={'isolated_gate_fixture':True,'proposal':proposed,'gate':gate,'external_effects':0};store.close()
 if output:Path(output).write_text(json.dumps(report,indent=2,ensure_ascii=False))
 return report
