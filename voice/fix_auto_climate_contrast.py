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
   def walk(x):
    if isinstance(x,dict):
     if (x.get('type')=='thermostat' and x.get('entity')=='climate.bedroom_auto_target') or (x.get('type')=='tile' and x.get('entity')=='input_boolean.bedroom_auto_climate'):
      x['card_mod']['style']+=CONTRAST;found.append(x['entity'])
     for v in x.values():walk(v)
    elif isinstance(x,list):
     for v in x:walk(v)
   for v in new['views']:
    if v.get('path')=='sunday':walk(v)
   assert len(found)==2,found
   stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S');(ROOT/f'ha-backups/roka-before-auto-contrast-{stamp}.json').write_text(json.dumps(old,indent=2))
   assert await call('lovelace/config',url_path='dashboard-roka')==old
   await call('lovelace/config/save',url_path='dashboard-roka',config=new)
   assert await call('lovelace/config',url_path='dashboard-roka')==new
   print('Saved and verified Auto Climate and Auto tile contrast corrections.')
asyncio.run(main())
