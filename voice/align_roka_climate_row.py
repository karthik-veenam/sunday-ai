import asyncio,copy,datetime,json
from pathlib import Path
import aiohttp
ROOT=Path('/home/djpi')
CONTRAST='''
{% set active = is_state('input_boolean.bedroom_auto_climate', 'on') and is_state('automation.bedroom_auto_climate', 'on') and (now().hour >= 21 or now().hour < 12) and states('sensor.lumi_lumi_sensor_ht_agl02_temperature') not in ['unknown', 'unavailable'] %}
{% if active %}
:host {
  --primary-text-color: #153e37;
  --secondary-text-color: #3b655d;
  --ha-card-header-color: #153e37;
  --rgb-primary-text-color: 21, 62, 55;
  --rgb-secondary-text-color: 59, 101, 93;
  --disabled-text-color: #627f78;
  --state-climate-cool-color: #258b75;
  --state-input_boolean-on-color: #258b75;
  --control-number-buttons-background-color: #b2ded2;
  --control-number-buttons-color: #153e37;
  --control-number-buttons-focus-color: #258b75;
}
ha-card {
  color: #153e37;
  --primary-text-color: #153e37;
  --secondary-text-color: #3b655d;
  --ha-card-header-color: #153e37;
  --rgb-primary-text-color: 21, 62, 55;
  --rgb-secondary-text-color: 59, 101, 93;
  --disabled-text-color: #627f78;
  --control-number-buttons-background-color: #b2ded2;
  --control-number-buttons-color: #153e37;
}
{% endif %}
'''
async def main():
 token=json.loads((ROOT/'sunday/config.json').read_text())['ha_token']
 async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25)) as s:
  async with s.ws_connect('ws://127.0.0.1:8123/api/websocket') as w:
   await w.receive_json();await w.send_json({'type':'auth','access_token':token});assert (await w.receive_json())['type']=='auth_ok'
   counter=0
   async def call(kind,**args):
    nonlocal counter
    counter+=1;await w.send_json(dict(id=counter,type=kind,**args))
    while True:
     r=await asyncio.wait_for(w.receive_json(),15)
     if r.get('id')==counter:
      assert r.get('success'),r.get('error');return r.get('result')
   old=await call('lovelace/config',url_path='dashboard-roka');new=copy.deepcopy(old);found=[]
   for view in new['views']:
    if view.get('path') != 'sunday':
     continue
    for index,row in enumerate(view.get('cards',[])):
     if row.get('type') != 'horizontal-stack' or not any(c.get('entity')=='climate.bedroom_auto_target' for c in row.get('cards',[])):
      continue
     right=next(c for c in row['cards'] if c.get('type')=='vertical-stack')
     right.setdefault('card_mod',{})['style']=':host { height: 100%; } #root { height: 100%; } #root > * { flex: 1 1 0 !important; min-height: 0; }'
     for tile in right['cards']:
      tile.setdefault('card_mod',{}).setdefault('style','')
      tile['card_mod']['style']+='\n:host { height: 100%; } ha-card { height: 100%; box-sizing: border-box; }\n'
     view['cards'][index]={
      'type':'custom:mod-card',
      'card_mod':{'style':{
       '.':'ha-card { background: none; border: none; box-shadow: none; padding: 0; }',
       'hui-horizontal-stack-card $':'#root { align-items: stretch !important; }'
      }},
      'card':row
     }
     found.append(index)
   assert len(found)==1,found
   stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S');(ROOT/f'ha-backups/roka-before-climate-row-alignment-{stamp}.json').write_text(json.dumps(old,indent=2))
   assert await call('lovelace/config',url_path='dashboard-roka')==old
   await call('lovelace/config/save',url_path='dashboard-roka',config=new)
   assert await call('lovelace/config',url_path='dashboard-roka')==new
   print('Saved and verified: climate row stretches right-hand tiles to the same bottom edge.')
asyncio.run(main())
