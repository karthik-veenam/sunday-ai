import asyncio, copy, datetime, json
from pathlib import Path
import aiohttp
ROOT=Path('/home/djpi')
STYLE="""{% set on = states('climate.bedroom_ac') not in ['off', 'unknown', 'unavailable'] %}
ha-card {
  background: {{ 'linear-gradient(145deg, #e0f5ff 0%, #99dcfa 100%)' if on else 'var(--ha-card-background, var(--card-background-color))' }};
  border: 1px solid {{ '#83cff0' if on else 'var(--divider-color)' }};
  box-shadow: {{ '0 4px 16px rgba(56, 165, 215, 0.15)' if on else 'none' }};
  {% if on %}
  --primary-text-color: #144763;
  --secondary-text-color: #245d79;
  --tile-color: #168ac3;
  --card-mod-icon: mdi:snowflake;
  {% endif %}
  transition: background 250ms ease, box-shadow 250ms ease;
}
{% if on %}
ha-card::before {
  content: "";
  position: absolute;
  inset: 0;
  border-radius: inherit;
  pointer-events: none;
  background-image: radial-gradient(circle at 16% 24%, #ffffffb3 0 2px, transparent 3px), radial-gradient(circle at 83% 20%, #ffffffb3 0 3px, transparent 4px), radial-gradient(circle at 75% 75%, #ffffff99 0 2px, transparent 3px), radial-gradient(circle at 24% 82%, #ffffff88 0 3px, transparent 4px);
}
{% endif %}
"""
async def main():
 token=json.loads((ROOT/'sunday/config.json').read_text())['ha_token']
 async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25)) as s:
  async with s.ws_connect('ws://127.0.0.1:8123/api/websocket') as w:
   await w.receive_json(); await w.send_json({'type':'auth','access_token':token}); assert (await w.receive_json())['type']=='auth_ok'
   counter=0
   async def call(kind,**args):
    nonlocal counter
    counter+=1; await w.send_json(dict(id=counter,type=kind,**args))
    while True:
     r=await asyncio.wait_for(w.receive_json(),15)
     if r.get('id')==counter:
      assert r.get('success'),r.get('error');return r.get('result')
   old=await call('lovelace/config',url_path='dashboard-roka'); new=copy.deepcopy(old); found=[]
   def walk(x):
    if isinstance(x,dict):
     if x.get('type')=='tile' and x.get('entity')=='climate.bedroom_ac':
      x['hide_state']=True;x.pop('state_content',None);x['color']='light-blue';x.setdefault('card_mod',{})['style']=STYLE;found.append(x)
     for v in x.values():walk(v)
    elif isinstance(x,list):
     for v in x:walk(v)
   for view in new['views']:
    if view.get('path')=='sunday':walk(view)
   assert len(found)==1,len(found)
   stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S');(ROOT/f'ha-backups/roka-before-ac-artwork-{stamp}.json').write_text(json.dumps(old,indent=2))
   assert await call('lovelace/config',url_path='dashboard-roka')==old,'Concurrent edit'
   await call('lovelace/config/save',url_path='dashboard-roka',config=new)
   assert await call('lovelace/config',url_path='dashboard-roka')==new
   states=await call('get_states');state=next(x['state'] for x in states if x['entity_id']=='climate.bedroom_ac')
   print('Saved and verified Roka Sunday AC artwork. Current AC state:',state)
asyncio.run(main())
