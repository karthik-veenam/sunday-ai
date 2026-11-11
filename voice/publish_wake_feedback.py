import asyncio,json
from pathlib import Path
import aiohttp
async def main():
 token=json.loads(Path('/home/djpi/sunday/config.json').read_text())['ha_token']
 async with aiohttp.ClientSession() as session:
  async with session.ws_connect('ws://localhost:8123/api/websocket') as ws:
   await ws.receive_json();await ws.send_json({'type':'auth','access_token':token});assert (await ws.receive_json())['type']=='auth_ok';seq=0
   async def call(kind,**kwargs):
    nonlocal seq
    seq+=1;await ws.send_json(dict(id=seq,type=kind,**kwargs))
    while True:
     r=await ws.receive_json()
     if r.get('id')==seq:
      if not r.get('success'):raise RuntimeError(r.get('error'))
      return r.get('result')
   await call('call_service',domain='rest_command',service='reload',service_data={})
   result=await call('call_service',domain='rest_command',service='sunday_wake_feedback',service_data={},return_response=True);assert result['response']['status']==200;print('Feedback HA data HTTP',result['response']['status'])
   url='/local/sunday-wake-feedback-card.js?v=20261023'
   resources=await call('lovelace/resources')
   if not any(r.get('url')==url for r in resources):await call('lovelace/resources/create',url=url,res_type='module')
   old=await call('lovelace/config',url_path='sunday-wake-feedback');Path('/home/djpi/sunday/deployment/before-recovery-20261023/feedback-dashboard.json').write_text(json.dumps(old,indent=2))
   new={'views':[{'title':'Wake feedback','path':'review','cards':[{'type':'custom:sunday-wake-feedback-card'}]}]}
   await call('lovelace/config/save',url_path='sunday-wake-feedback',config=new);assert await call('lovelace/config',url_path='sunday-wake-feedback')==new
   print('Feedback dashboard now uses local HA connection; no tunnel dependency')
asyncio.run(main())
