"""Compose a single fridge card with three equal inline zone controls."""
import asyncio,copy,datetime,json
from pathlib import Path
import aiohttp
ENTITIES=['number.fridge_refrigerator_temperature','number.fridge_freezer_temperature','number.fridge_magic_zone_temperature']
NAMES=['Fridge','Freezer','Magic Zone']

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
   backup=Path('/home/djpi/ha-backups')/('before-fridge-three-column-card-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'.json')
   backup.write_text(json.dumps(originals,indent=2)+'\n')
   updated=copy.deepcopy(originals)
   for path,config in updated.items():
    view=config['views'][0] if path=='dashboard-kitchen' else next(v for v in config['views'] if v.get('path')=='kitchen')
    cards=view['cards'][0]['cards']
    idx=next(i for i,c in enumerate(cards) if c.get('entity')=='sensor.fridge_refrigerator_target_temp_level')
    header=cards[idx];zones=cards[idx+1:idx+4]
    assert [z.get('entity') for z in zones]==ENTITIES
    header['card_mod']={'style':'ha-card { background: transparent; border: none; box-shadow: none; padding-bottom: 4px; }'}
    for zone,name in zip(zones,NAMES):
     zone['name']=name;zone['vertical']=True;zone['features_position']='bottom'
     zone['card_mod']={'style':'''ha-card {
       height: 100%; box-sizing: border-box;
       background: #edf3fa; border: 1px solid #d7e3f0; border-radius: 12px;
       box-shadow: none; --primary-text-color: #172b40; --secondary-text-color: #345b80;
       --control-number-buttons-background-color: #dfeaf6;
       --control-number-buttons-border-radius: 10px;
       --ha-card-header-font-size: 13px;
     }
     .content { padding: 10px 4px !important; }
     '''}
    grouped={'type':'custom:mod-card','card':{'type':'vertical-stack','cards':[header,{'type':'grid','columns':3,'square':False,'cards':zones}]},'card_mod':{'style':'''ha-card {
      padding: 4px 10px 12px; background: linear-gradient(135deg, #f9fbfe, #eaf3fc);
      border: 1px solid #d6e0eb; border-radius: 16px;
      box-shadow: 0 2px 8px rgba(43,65,91,0.06);
     }'''}}
    cards[idx:idx+4]=[grouped]
   assert [v for v in originals[paths[1]]['views'] if v.get('path')!='kitchen']==[v for v in updated[paths[1]]['views'] if v.get('path')!='kitchen']
   for path,c in updated.items():
    await call({'type':'lovelace/config/save','url_path':path,'config':c})
    assert await call({'type':'lovelace/config','url_path':path})==c
    print('Saved and verified:',path)
   print('Backup:',backup)
if __name__=='__main__':asyncio.run(main())
