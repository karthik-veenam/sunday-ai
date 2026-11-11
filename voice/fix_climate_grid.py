import asyncio,copy,datetime,json
from pathlib import Path
import aiohttp
ROOT=Path('/home/djpi')
async def main():
 token=json.loads((ROOT/'sunday/config.json').read_text())['ha_token']
 async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
  async with s.ws_connect('ws://127.0.0.1:8123/api/websocket') as w:
   await w.receive_json();await w.send_json({'type':'auth','access_token':token});assert (await w.receive_json())['type']=='auth_ok'
   i=0
   async def call(kind,**args):
    nonlocal i
    i+=1;await w.send_json(dict(id=i,type=kind,**args))
    while True:
     r=await w.receive_json()
     if r.get('id')==i:
      assert r.get('success'),r.get('error');return r.get('result')
   old=await call('lovelace/config',url_path='dashboard-roka');new=copy.deepcopy(old)
   backup=json.loads(next((ROOT/'ha-backups').glob('roka-before-climate-row-alignment-*.json')).read_text())
   original_view=next(v for v in backup['views'] if v.get('path')=='sunday')
   row=copy.deepcopy(next(c for c in original_view['cards'] if c.get('type')=='horizontal-stack' and any(x.get('entity')=='climate.bedroom_auto_target' for x in c.get('cards',[]))))
   thermostat=row['cards'][0];tiles=row['cards'][1]['cards']
   for tile in tiles:
    tile.setdefault('card_mod',{}).setdefault('style','')
    tile['card_mod']['style']+='\nha-card { height: 100%; box-sizing: border-box; }\n'
   replacement={'type':'custom:mod-card','card_mod':{'style':{
    '.':'ha-card { background: none; border: none; box-shadow: none; padding: 0; }',
    'hui-grid-card $':'''#root {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      grid-template-rows: repeat(3, minmax(0, 1fr));
      gap: var(--horizontal-stack-card-gap, 8px);
      align-items: stretch;
    }
    #root > hui-thermostat-card { grid-column: 1; grid-row: 1 / span 3; }
    #root > hui-tile-card { grid-column: 2; min-height: 0; }
    '''}},'card':{'type':'grid','columns':2,'square':False,'cards':[thermostat,*tiles]}}
   view=next(v for v in new['views'] if v.get('path')=='sunday');found=[]
   for index,c in enumerate(view['cards']):
    if c.get('type')=='custom:mod-card' and c.get('card',{}).get('type')=='horizontal-stack' and any(x.get('entity')=='climate.bedroom_auto_target' for x in c['card'].get('cards',[])):
     view['cards'][index]=replacement;found.append(index)
   assert len(found)==1,found
   stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S');(ROOT/f'ha-backups/roka-before-grid-fix-{stamp}.json').write_text(json.dumps(old,indent=2))
   assert await call('lovelace/config',url_path='dashboard-roka')==old
   await call('lovelace/config/save',url_path='dashboard-roka',config=new)
   assert await call('lovelace/config',url_path='dashboard-roka')==new
   print('Grid configuration saved and verified.')
asyncio.run(main())
