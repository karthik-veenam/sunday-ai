"""Distinguish the freezer's short door from the fridge's tall door."""
import asyncio,copy,datetime,json
from pathlib import Path
import aiohttp

async def main():
 token=json.load(open('/home/djpi/sunday/config.json'))['ha_token']
 async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
  async with s.ws_connect('ws://localhost:8123/api/websocket') as w:
   await w.receive_json();await w.send_json({'type':'auth','access_token':token});assert (await w.receive_json())['type']=='auth_ok'
   seq=0
   async def call(payload):
    nonlocal seq
    seq+=1;await w.send_json(dict(payload,id=seq))
    while True:
     r=await w.receive_json()
     if r.get('id')==seq:
      assert r.get('success'),r.get('error')
      return r.get('result')
   paths=['dashboard-kitchen','dashboard-common-areas']
   original={p:await call({'type':'lovelace/config','url_path':p}) for p in paths}
   backup=Path('/home/djpi/ha-backups')/('before-freezer-door-size-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'.json');backup.write_text(json.dumps(original,indent=2)+'\n')
   updated=copy.deepcopy(original)
   def change(obj):
    count=0
    if isinstance(obj,dict):
     if obj.get('primary')=='Haier Fridge':
      obj['secondary']="{% set f = states('binary_sensor.fridge_refrigerator_door_status') %}{% set z = states('binary_sensor.fridge_freezer_door_status') %}{{ 'Fridge & freezer doors open' if f == 'on' and z == 'on' else ('Fridge door open' if f == 'on' else ('Freezer door open' if z == 'on' else ('Both doors closed' if f == 'off' and z == 'off' else 'Door status unavailable'))) }}"
     if obj.get('type')=='custom:mushroom-chips-card' and 'fridge-door-snap' in str(obj.get('card_mod')):
      style=obj['card_mod']['style']
      prefix="{% set freezer_only = is_state('binary_sensor.fridge_freezer_door_status','on') and not is_state('binary_sensor.fridge_refrigerator_door_status','on') %}\n"
      style=style.replace('width: 16px; height: 24px; top: 7px; right: 11px;',"width: 16px; height: {{ 16 if freezer_only else 24 }}px; top: {{ 11 if freezer_only else 7 }}px; right: 11px;")
      style=style.replace('width: 14px; height: 22px; top: 8px; right: 12px;',"width: 14px; height: {{ 14 if freezer_only else 22 }}px; top: {{ 12 if freezer_only else 8 }}px; right: 12px;")
      obj['card_mod']['style']=prefix+style
      count+=1
     for v in obj.values():count+=change(v)
    elif isinstance(obj,list):
     for v in obj:count+=change(v)
    return count
   for path,config in updated.items():
    view=config['views'][0] if path=='dashboard-kitchen' else next(v for v in config['views'] if v['path']=='kitchen')
    assert change(view)==1
    await call({'type':'lovelace/config/save','url_path':path,'config':config})
    assert await call({'type':'lovelace/config','url_path':path})==config
    print('Saved and verified:',path)
asyncio.run(main())
