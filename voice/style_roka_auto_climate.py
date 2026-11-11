import asyncio, copy, datetime, json
from pathlib import Path
import aiohttp
ROOT=Path('/home/djpi')
STYLE="""{% set enabled = is_state('input_boolean.bedroom_auto_climate', 'on') %}
{% set active = enabled and is_state('automation.bedroom_auto_climate', 'on') and (now().hour >= 21 or now().hour < 12) and states('sensor.lumi_lumi_sensor_ht_agl02_temperature') not in ['unknown', 'unavailable'] %}
ha-card {
  background: {{ 'linear-gradient(145deg, #eefbf7 0%, #cdeee5 100%)' if active else 'var(--ha-card-background, var(--card-background-color))' }};
  border: 1px solid {{ '#9ed7c7' if active else 'var(--divider-color)' }};
  box-shadow: {{ '0 3px 14px rgba(38, 145, 115, 0.10)' if active else 'none' }};
  {% if active %}
  --state-climate-cool-color: #258b75;
  --tile-color: #258b75;
  {% endif %}
  transition: background 300ms ease, box-shadow 300ms ease;
}
"""
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
   old=await call('lovelace/config',url_path='dashboard-roka');new=copy.deepcopy(old);found=[0,0]
   def walk(x):
    if isinstance(x,dict):
     if x.get('type')=='thermostat' and x.get('entity')=='climate.bedroom_auto_target':
      x['features']=[f for f in x.get('features',[]) if f.get('type')!='climate-hvac-modes']
      assert any(f.get('type')=='target-temperature' for f in x['features'])
      x.setdefault('card_mod',{})['style']=STYLE
      found[0]+=1
     if x.get('type')=='tile' and x.get('entity')=='input_boolean.bedroom_auto_climate':
      for key in ['tap_action','icon_tap_action','hold_action','double_tap_action']:
       x[key]={'action':'none'}
      x['hide_state']=True
      x.setdefault('card_mod',{})['style']=STYLE
      found[1]+=1
     for v in x.values():walk(v)
    elif isinstance(x,list):
     for v in x:walk(v)
   for v in new['views']:
    if v.get('path')=='sunday':walk(v)
   assert found==[1,1],found
   stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S');(ROOT/f'ha-backups/roka-before-auto-climate-indicator-{stamp}.json').write_text(json.dumps(old,indent=2))
   assert await call('lovelace/config',url_path='dashboard-roka')==old,'Concurrent edit'
   await call('lovelace/config/save',url_path='dashboard-roka',config=new)
   assert await call('lovelace/config',url_path='dashboard-roka')==new
   print('Saved and verified: no Off/Cool row, +/- retained, active background, Auto tile cannot toggle.')
asyncio.run(main())
