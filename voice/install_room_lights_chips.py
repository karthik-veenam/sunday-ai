import asyncio,copy,datetime,json,re
from pathlib import Path
import aiohttp
ROOT=Path('/home/djpi')
DASHBOARDS=['dashboard-roka','dashboard-rani','dashboard-sian']
async def main():
 token=json.load(open(ROOT/'sunday/config.json'))['ha_token']
 async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=45)) as s:
  async with s.ws_connect('ws://127.0.0.1:8123/api/websocket') as w:
   await w.receive_json();await w.send_json({'type':'auth','access_token':token});assert (await w.receive_json())['type']=='auth_ok'
   i=0
   async def call(kind,**kwargs):
    nonlocal i
    i+=1;await w.send_json(dict(id=i,type=kind,**kwargs))
    while True:
     r=await w.receive_json()
     if r.get('id')==i:
      assert r.get('success'),r.get('error');return r.get('result')
   originals={path:await call('lovelace/config',url_path=path) for path in DASHBOARDS}
   updates=copy.deepcopy(originals);report=[]
   for path,config in updates.items():
    for view in config['views']:
     found=[]
     def walk(card):
      if not isinstance(card,dict):return
      if card.get('type')=='custom:mushroom-chips-card':
       for j,chip in enumerate(card.get('chips',[])):
        if chip.get('type')=='template' and 'lightbulb-group' in chip.get('icon','') and 'expand(' in chip.get('content',''):
         ids=list(dict.fromkeys(re.findall(r"['\"](light\.[a-z0-9_]+)['\"]",chip['content'])))
         assert ids,(path,view['path'])
         card['chips'][j]={'type':'room-lights','room':view.get('title',view['path']),'entities':ids}
         found.append(ids)
      for child in card.get('cards',[]):walk(child)
      if isinstance(card.get('card'),dict):walk(card['card'])
     for card in view.get('cards',[]):walk(card)
     assert len(found)==1,(path,view.get('path'),len(found))
     report.append({'dashboard':path,'view':view['path'],'light_count':len(found[0]),'entities':found[0]})
   assert len(report)==12,len(report)
   stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
   (ROOT/f'ha-backups/three-dashboards-before-lights-split-{stamp}.json').write_text(json.dumps(originals,indent=2))
   resources=await call('lovelace/resources');url='/local/room-lights-chip.js?v=1'
   if not any(x.get('url')==url for x in resources):await call('lovelace/resources/create',res_type='module',url=url)
   for path in DASHBOARDS:
    assert await call('lovelace/config',url_path=path)==originals[path],f'{path} edited concurrently'
    await call('lovelace/config/save',url_path=path,config=updates[path])
    assert await call('lovelace/config',url_path=path)==updates[path]
   (ROOT/'sunday/room-lights-split-inventory.json').write_text(json.dumps(report,indent=2))
   print('Saved and verified 12 view headers across RoKa, Rani, SiAn.')
   for row in report:print(row['dashboard'],row['view'],row['light_count'],'lights')
asyncio.run(main())
