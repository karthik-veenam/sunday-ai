import asyncio,copy,datetime,json
from pathlib import Path
import aiohttp
ROOT=Path('/home/djpi')
async def main():
 token=json.loads((ROOT/'sunday/config.json').read_text())['ha_token']
 async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25)) as s:
  async with s.ws_connect('ws://127.0.0.1:8123/api/websocket') as w:
   await w.receive_json();await w.send_json({'type':'auth','access_token':token});assert (await w.receive_json())['type']=='auth_ok'
   i=0
   async def call(kind,**args):
    nonlocal i
    i+=1;await w.send_json(dict(id=i,type=kind,**args))
    while True:
     r=await asyncio.wait_for(w.receive_json(),15)
     if r.get('id')==i:
      assert r.get('success'),r.get('error');return r.get('result')
   old=await call('lovelace/config',url_path='dashboard-roka');new=copy.deepcopy(old);found=[]
   def walk(x):
    if isinstance(x,dict):
     if x.get('type')=='tile' and x.get('entity')=='input_boolean.bedroom_auto_climate':
      x['tap_action']={'action':'toggle'};x['icon_tap_action']={'action':'toggle'}
      style=x.get('card_mod',{}).get('style','')
      # The toggle indicates enabled/disabled even outside scheduled operating hours.
      import re
      style=re.sub(r"\{% set active = .*? %\}","{% set active = is_state('input_boolean.bedroom_auto_climate', 'on') %}",style)
      x['card_mod']['style']=style
      found.append(x)
     for value in x.values():walk(value)
    elif isinstance(x,list):
     for value in x:walk(value)
   for view in new['views']:
    if view.get('path')=='sunday':walk(view)
   assert len(found)==1,len(found)
   stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S');(ROOT/f'ha-backups/roka-before-auto-toggle-{stamp}.json').write_text(json.dumps(old,indent=2))
   assert await call('lovelace/config',url_path='dashboard-roka')==old
   await call('lovelace/config/save',url_path='dashboard-roka',config=new)
   assert await call('lovelace/config',url_path='dashboard-roka')==new
   print('Saved and verified: Auto tile and icon toggle Auto Climate; background indicates enabled state.')
asyncio.run(main())
