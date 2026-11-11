"""Two explicit wake profiles; HA changes take effect only in wake-listening loop."""
import asyncio,json,os
from pathlib import Path
from listening_mode import ListeningMode
ENTITY='input_select.sunday_wake_model'
CURRENT='Current — November 7'
PREVIOUS='Previous — V5/V6 + verifier'
PROFILES={CURRENT:('/home/djpi/openwakeword-models/sunday_candidate_20261107/sunday.onnx',.99),PREVIOUS:('/home/djpi/openwakeword-models/sunday_v5_v6_filter/sunday.onnx',.99)}
class WakeProfile(ListeningMode):
 def __init__(self,base,token,wake):
  super().__init__(base,token);self.wake=wake;self.requested=None;self.applied=None
 def apply(self,state):
  if state in PROFILES:self.requested=state
 async def snapshot(self,session):
  import aiohttp
  async with session.get(self.base+'/api/states/'+ENTITY,headers=self.headers,timeout=aiohttp.ClientTimeout(total=5)) as r:
   r.raise_for_status();self.apply((await r.json()).get('state'))
 async def initialize(self):
  import aiohttp
  async with aiohttp.ClientSession() as s:await self.snapshot(s)
 async def apply_pending(self):
  selected=self.requested
  if selected is None or selected==self.applied:return False
  path,threshold=PROFILES[selected]
  # Load first; failed loads never replace a working detector.
  if self.wake.model_path!=path:
   model,key=await asyncio.to_thread(self.wake._build_model,path)
  else:model,key=self.wake.model,self.wake.key
  if selected==PREVIOUS:
   p=self.wake.verifier.CONFIG;cfg=json.loads(p.read_text())
   if cfg.get('provider')!='sherpa':raise ValueError('Previous profile requires existing Sherpa verifier')
   if cfg.get('enabled') is not True:
    cfg['enabled']=True;tmp=p.with_suffix('.profile.tmp');tmp.write_text(json.dumps(cfg,indent=2)+'\n');os.chmod(tmp,0o600);os.replace(tmp,p)
  self.wake.model,self.wake.key=model,key
  self.wake.model_path,self.wake.threshold=path,threshold
  self.applied=selected
  print('[Wake profile] Applied '+selected+' threshold='+str(threshold),flush=True)
  return True
 async def watch(self):
  # Same authenticated reconnecting state stream, scoped to this selector.
  import aiohttp
  while True:
   try:
    async with aiohttp.ClientSession() as s:
     async with s.ws_connect(self.base.replace('http:','ws:').replace('https:','wss:')+'/api/websocket',heartbeat=20) as w:
      await w.receive_json();await w.send_json({'type':'auth','access_token':self.headers['Authorization'][7:]})
      if (await w.receive_json()).get('type')!='auth_ok':raise RuntimeError('HA authentication failed')
      await w.send_json({'id':1,'type':'subscribe_events','event_type':'state_changed'});await self.snapshot(s)
      async for msg in w:
       if msg.type!=aiohttp.WSMsgType.TEXT:break
       data=msg.json().get('event',{}).get('data',{})
       if data.get('entity_id')==ENTITY:self.apply((data.get('new_state') or {}).get('state'))
   except asyncio.CancelledError:raise
   except Exception as error:print('[Wake profile] Control unavailable; retaining loaded model:',type(error).__name__,flush=True)
   await asyncio.sleep(2)
