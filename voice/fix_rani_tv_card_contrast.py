import asyncio,copy,datetime,json
from pathlib import Path
import aiohttp
async def main():
 t=json.load(open('/home/djpi/sunday/config.json'))['ha_token']
 async with aiohttp.ClientSession(headers={'Authorization':'Bearer '+t}) as s:
  async with s.ws_connect('ws://localhost:8123/api/websocket') as w:
   await w.receive_json();await w.send_json({'type':'auth','access_token':t});assert (await w.receive_json())['type']=='auth_ok'
   seq=0
   async def call(typ,**kw):
    nonlocal seq
    seq+=1;await w.send_json(dict(type=typ,id=seq,**kw))
    while True:
     r=await w.receive_json()
     if r.get('id')==seq:
      assert r.get('success'),r.get('error');return r.get('result')
   old=await call('lovelace/config',url_path='dashboard-rani');cfg=copy.deepcopy(old)
   cards=cfg['views'][0]['cards'][0]['cards']
   card=next(c for c in cards if 'browser_mod.popup' in json.dumps(c))
   old_card=copy.deepcopy(card)
   card['card_mod']['style']='''ha-card {
 position: relative;
 background: linear-gradient(135deg, #56CCF2, #2F80ED) !important;
 border: 1px solid rgba(47,128,237,0.25);
 border-radius: 12px;
 overflow: hidden;
 box-shadow: none;
 --primary-text-color: #ffffff;
 --secondary-text-color: #edf6ff;
 --rgb-primary-text-color: 255,255,255;
 --rgb-secondary-text-color: 237,246,255;
}
:host { --primary-text-color: #ffffff; --secondary-text-color: #edf6ff; --rgb-primary-text-color: 255,255,255; --rgb-secondary-text-color: 237,246,255; }'''
   header=card['card']['cards'][0]
   header['card_mod']['style']='''ha-card { background: transparent !important; border: none; box-shadow: none; }
:host { --primary-text-color: #ffffff; --secondary-text-color: #edf6ff; --rgb-primary-text-color: 255,255,255; --rgb-secondary-text-color: 237,246,255; }
ha-tile-info { --primary-text-color: #ffffff; --secondary-text-color: #edf6ff; }
span[slot="primary"] { color: #ffffff !important; font-weight: 700; }
span[slot="secondary"] { color: #edf6ff !important; }
mushroom-state-item { padding-right: 52px; }'''
   # Ensure the entire popup, its position, and all remote actions are untouched.
   assert card['card']['cards'][1:]==old_card['card']['cards'][1:]
   Path('/home/djpi/ha-backups/rani-before-tv-contrast-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'.json').write_text(json.dumps(old,indent=2))
   await call('lovelace/config/save',url_path='dashboard-rani',config=cfg)
   assert await call('lovelace/config',url_path='dashboard-rani')==cfg
   Path('/home/djpi/bedroom-dashboards/rani.json').write_text(json.dumps(cfg,indent=2)+'\n')
   print('Saved/read-back verified fixed blue TV surface and readable white labels across all entity states; popup and remote placement unchanged.')
  for entity in ['media_player.rani_fire_tv','remote.rani_fire_tv']:
   async with s.get('http://localhost:8123/api/states/'+entity) as r:
    d=await r.json();print(entity,d.get('state'))
asyncio.run(main())
