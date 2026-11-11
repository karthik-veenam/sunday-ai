"""Isolated fake-Telegram release fixtures. No real transport/HA/model effects."""
import asyncio,json,tempfile
from pathlib import Path
from datetime import datetime
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from config import Config
from .service import Service
from .fixtures import strong
from .store import digest

class Clock:
 def __init__(self):self.t=datetime.fromisoformat('2026-10-19T06:00:00+00:00').timestamp()
 def __call__(self):return self.t
class API:
 def __init__(self):self.calls=[];self.error=None
 async def call(self,method,**data):
  self.calls.append((method,data))
  if self.error:raise self.error
  return {'message_id':1000+len(self.calls)}
 async def close(self):pass
class Models:
 def __init__(self):self.outcome='ASK_USER';self.calls=[]
 async def call(self,kind,packet,ref):
  self.calls.append(kind);return json.dumps({'outcomes':[proposal(self.outcome)],'reason':'Isolated supported fixture.'}),'fixture-usage'
def proposal(operation='TELL_USER',**kw):
 p={'operation':operation,'reason':'Fresh supported information is useful now.','confidence':.95,'message':'No office today?' if operation=='ASK_USER' else 'The device session may be unintended.','destination':'telegram','already_known':False,'consequence_of_silence':'The user may miss a relevant issue.','time_sensitive':False,'useful_now':True,'can_resolve_silently':False,'interruption_cost':'low'};p.update(kw);return p
class Harness:
 def __init__(self,path,clock=None,api=None):
  self.path=Path(path);self.clock=clock or Clock();self.api=api or API();self.cfg=Config(proactive_cognition_enabled=True,ha_attention_enabled=True,expectation_engine_enabled=True,self_watch_enabled=True,telegram_enabled=True,telegram_chat_id='10001',telegram_users={'10001':'Karthik'},proactive_tell_enabled=True,proactive_ask_enabled=True)
  self.models=Models();self.service=Service(self.path,self.cfg,clock=self.clock,models=self.models);self.s=self.service.store
  self.presence=NS(get_presence=AsyncMock(return_value=NS(presence='HOME')));self.service.presence=self.presence;self.service.context=AsyncMock(return_value={'fresh':True,'healthy':True,'quiet':False,'world':[]})
  self.telegram=NS(api=self.api,primary_person='Karthik');self.service.bind_telegram(self.telegram)
  self.d=self.service.delivery
 async def proposed(self,op='TELL_USER',incident='fixture',authority='SUNDAY_SELF',kind='HA_NOTEWORTHY',evidence=None,**kw):
  ref=self.s.audit('fixture_evidence',{'synthetic':True},'evidence:'+incident)
  wid=self.s.wake(kind,authority,'Karthik' if kind=='EXPECTATION_VIOLATED' else 'fan.fixture',evidence or [ref],incident,**kw)
  row=self.s.get('wakes',wid);p=proposal(op)
  if op=='ACT':p={'operation':'ACT','reason':'Synthetic target; no real device exists.','confidence':1.0,'action':{'domain':'light','service':'turn_off','entity_ids':['light.fixture'],'data':{}}}
  result=await self.service.apply(row,{'outcomes':[p],'reason':'Fixture cognition.'},{'fresh':True,'healthy':True,'quiet':False,'world':[]})
  pid=result[0]['proposal_id'] if result else 'proposals:'+digest([row['data']['incident_id'],'ACT' if op=='ACT' else 'communication']);receipt=self.s.get('deliveries','deliveries:'+digest(['telegram-incident',row['data']['incident_id']]))
  return pid,receipt['id'] if receipt else None
 async def office(self):
  pattern=strong();self.service.expectations.allowed=lambda:True;self.service.expectations.memories=lambda mid:pattern
  e={'kind':'EXPECTATION_BOUNDARY','expectation_kind':'arrival','source_memory_id':pattern['memory_id'],'source_memory_version':pattern['updated_at'],'subject':'Karthik','day':'2026-10-19'}
  tid=self.s.put('temporal',e,dedupe='office-boundary',state='VIOLATED')
  self.service.recheck=AsyncMock(return_value={'fresh':True,'healthy':True,'presence':'HOME','arrival_occurred':False,'complete_history':True,'refs':['synthetic-history']})
  return await self.proposed('ASK_USER',kind='EXPECTATION_VIOLATED',evidence=[tid])
 async def conditional(self):
  tid=self.s.put('temporal',{'kind':'CONDITIONAL_COMMITMENT','authority':'USER_EXPLICIT','user_request_id':'fixture-user-request','commitment':{'message':'The fixture fan is off.'}},dedupe='conditional-user-request',state='PENDING')
  wid=self.service.watches.create('fan.fixture',{'property':'state','equals':'off'},'Explicit fixture condition',self.clock()+60,authority='USER_EXPLICIT',evidence=['fixture-user-request'],commitment_ref=tid)
  wakes=self.service.watches.event('fan.fixture','state','off','fixture-transition');wake=self.s.get('wakes',wakes[0]);p=proposal(message='The fixture fan is off.')
  result=await self.service.apply(wake,{'outcomes':[p],'reason':'Explicit condition fired.'},{'fresh':True,'healthy':True,'quiet':False})
  self.service.world=NS(connected=True,bus=NS(consumers=[]),fresh_entities=AsyncMock(return_value={'fan.fixture':{'state':'off'}}),store=NS(get=lambda *args:{'value':'off','availability':'available'}))
  return result[0]['proposal_id'],self.s.rows('deliveries')[0]['id'],tid
 async def close(self):await self.service.close()

async def scenario(label):
 with tempfile.TemporaryDirectory() as directory:
  h=Harness(Path(directory)/'proactive.db')
  try:
   if label in 'ACD':
    pid,did=await h.office()
    if label=='C':h.presence.get_presence=AsyncMock(side_effect=[NS(presence='HOME'),NS(presence='OFFICE')])
    if label=='D':h.service.recheck=AsyncMock(return_value={'fresh':False,'healthy':False,'presence':'UNKNOWN'})
    await h.d.process(did);expected='DELIVERED' if label=='A' else 'SUPPRESSED';assert h.s.get('deliveries',did)['state']==expected
   elif label in 'BE':
    h.d.revalidate=AsyncMock(return_value={'eligible':True,'presence':'AWAY' if label=='B' else 'HOME','context':{'fresh':True,'healthy':True,'quiet':False}})
    pid,did=await h.proposed();await h.d.process(did);assert h.s.get('deliveries',did)['data']['route']['channel']=='telegram'
   elif label=='F':
    pid,did,tid=await h.conditional();await h.d.process(did);assert h.s.get('deliveries',did)['state']=='DELIVERED';assert h.s.get('deliveries',did)['data']['authority']=='USER_EXPLICIT'
   elif label in 'GH':
    h.d.revalidate=AsyncMock(return_value={'eligible':True,'presence':'HOME','context':{'fresh':True,'healthy':True,'quiet':False}})
    pid,did=await h.proposed(incident='same-incident');await h.d.process(did)
    if label=='G':await h.proposed(incident='same-incident',kind='EXECUTION_ANOMALY');await h.d.process(did)
    else:
     api=h.api;clock=h.clock;await h.close();h=Harness(Path(directory)/'proactive.db',clock,api);h.d.recover();await h.d.process(did)
    assert len(h.api.calls)==1
   elif label=='I':
    h.d.revalidate=AsyncMock(return_value={'eligible':True,'presence':'UNKNOWN','context':{'fresh':True,'healthy':True,'quiet':False}})
    h.api.error=TimeoutError('Synthetic ambiguity');pid,did=await h.proposed();await h.d.process(did);await h.d.process(did);assert h.s.get('deliveries',did)['state']=='UNKNOWN';assert len(h.api.calls)==1
   else:
    pid,did=await h.proposed('ACT');assert did is None;assert len(h.api.calls)==0;assert not h.cfg.proactive_act_enabled
   if label!='J':assert h.s.get('deliveries',did)['data']['channel']=='telegram'
   return {'fixture':label,'passed':True,'fake_send_attempts':len(h.api.calls),'states':[r['state'] for r in h.s.rows('deliveries')],'authority':[r['data']['authority'] for r in h.s.rows('deliveries')],'ha_calls':0,'voice_calls':0,'real_external_effects':0}
  finally:await h.close()
async def run(output=None):
 rows=[await scenario(label) for label in 'ABCDEFGHIJ'];report={'isolated':True,'all_passed':True,'fixtures':rows,'real_external_effects':0}
 if output:Path(output).write_text(json.dumps(report,indent=2)+'\n')
 return report
