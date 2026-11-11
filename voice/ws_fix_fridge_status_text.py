import asyncio,copy,datetime,json
from pathlib import Path
import aiohttp
OPEN="is_state('binary_sensor.fridge_refrigerator_door_status','on') or is_state('binary_sensor.fridge_freezer_door_status','on')"
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
   originals={p:await call({'type':'lovelace/config','url_path':p}) for p in paths}
   backup=Path('/home/djpi/ha-backups')/('before-fridge-secondary-span-fix-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'.json');backup.write_text(json.dumps(originals,indent=2)+'\n')
   updated=copy.deepcopy(originals)
   color="{{ '#c62828' if "+OPEN+" else '#576b82' }}"
   def change(obj):
    count=0
    if isinstance(obj,dict):
     if obj.get('type')=='custom:mushroom-template-card' and obj.get('primary')=='Haier Fridge':
      existing=obj.get('card_mod',{}).get('style','')
      base=existing.get('.', '') if isinstance(existing,dict) else existing
      obj['card_mod']={'style':base+'\nspan[slot="secondary"] { color: '+color+' !important; }'}
      return 1
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
