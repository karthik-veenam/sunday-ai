"""HA-controlled voice-input gate. Enabled mode never authorizes a device action."""
import asyncio
import aiohttp
ENTITY='input_boolean.sunday_no_listening'
class ListeningMode:
 def __init__(self,base,token):
  self.base=base.rstrip('/');self.headers={'Authorization':'Bearer '+token}
  self.muted=True;self.generation=0;self._muted=asyncio.Event();self._muted.set()
 def apply(self,state):
  value=state!='off' # Missing/unavailable helper fails closed.
  if value!=self.muted:
   self.generation+=1;self.muted=value
   if value:self._muted.set()
   else:self._muted.clear()
   print('[Listening mode] Voice input '+('paused' if value else 'enabled'),flush=True)
 async def wait_muted(self):await self._muted.wait()
 async def snapshot(self,session):
  async with session.get(self.base+'/api/states/'+ENTITY,headers=self.headers,timeout=aiohttp.ClientTimeout(total=5)) as r:
   r.raise_for_status();self.apply((await r.json()).get('state'))
 async def initialize(self):
  try:
   async with aiohttp.ClientSession() as s:await self.snapshot(s)
  except Exception:self.apply(None)
  print('[Listening mode] Ready: '+('paused' if self.muted else 'enabled'),flush=True)
 async def watch(self):
  while True:
   try:
    async with aiohttp.ClientSession() as s:
     async with s.ws_connect(self.base.replace('http:','ws:').replace('https:','wss:')+'/api/websocket',heartbeat=20) as w:
      await w.receive_json();await w.send_json({'type':'auth','access_token':self.headers['Authorization'][7:]})
      if (await w.receive_json()).get('type')!='auth_ok':raise RuntimeError('HA authentication failed')
      await w.send_json({'id':1,'type':'subscribe_events','event_type':'state_changed'})
      await self.snapshot(s)
      async for msg in w:
       if msg.type!=aiohttp.WSMsgType.TEXT:break
       obj=msg.json();data=obj.get('event',{}).get('data',{})
       if data.get('entity_id')==ENTITY:self.apply((data.get('new_state') or {}).get('state'))
   except asyncio.CancelledError:raise
   except Exception as error:print('[Listening mode] HA control unavailable:',type(error).__name__,flush=True)
   self.apply(None);await asyncio.sleep(2)
