import asyncio,copy,datetime,json
from pathlib import Path
import aiohttp
ROOT=Path('/home/djpi');ENTITY='media_player.bravia_kd_65x75k'
COLORS="""{% set tv_on = states('media_player.bravia_kd_65x75k') not in ['off','standby','unknown','unavailable'] %}
:host {
 --primary-text-color: {{ '#ffffff' if tv_on else '#233247' }};
 --secondary-text-color: {{ '#edf6ff' if tv_on else '#52657b' }};
 --rgb-primary-text-color: {{ '255,255,255' if tv_on else '35,50,71' }};
 --rgb-secondary-text-color: {{ '237,246,255' if tv_on else '82,101,123' }};
}
"""
async def main():
 token=json.load(open(ROOT/'sunday/config.json'))['ha_token']
 async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=35)) as s:
  async with s.ws_connect('ws://127.0.0.1:8123/api/websocket') as w:
   await w.receive_json();await w.send_json({'type':'auth','access_token':token});assert (await w.receive_json())['type']=='auth_ok';i=0
   async def call(kind,**kw):
    nonlocal i
    i+=1;await w.send_json(dict(id=i,type=kind,**kw))
    while True:
     r=await w.receive_json()
     if r.get('id')==i:
      assert r.get('success'),r.get('error');return r.get('result')
   paths=['dashboard-roka','dashboard-rani','dashboard-sian'];old={p:await call('lovelace/config',url_path=p) for p in paths};new=copy.deepcopy(old)
   for path,c in new.items():
    found=[]
    def walk(x):
     if not isinstance(x,dict):return
     if x.get('type')=='custom:mod-card' and x.get('card',{}).get('type')=='vertical-stack':
      children=x['card'].get('cards',[])
      if children and children[0].get('type')=='custom:mushroom-media-player-card' and children[0].get('entity')==ENTITY:
       player=children[0];controls=next(a for a in children if a.get('type')=='custom:mushroom-chips-card' and any(z.get('icon')=='mdi:remote-tv' for z in a.get('chips',[])))
       assert not any(z.get('icon')=='mdi:power' for z in controls['chips'])
       controls['chips'].insert(0,{'type':'template','entity':ENTITY,'icon':'mdi:power','icon_color':"{{ 'blue' if states('"+ENTITY+"') not in ['off','standby','unknown','unavailable'] else 'grey' }}",'tap_action':{'action':'perform-action','perform_action':'media_player.toggle','target':{'entity_id':ENTITY}},'hold_action':{'action':'none'}})
       controls['card_mod']['style']=controls['card_mod']['style'].replace('width: 40px','width: 88px')+'\n.chip-container { flex-wrap: nowrap !important; gap: 8px; }\n'
       player['media_controls']=[a for a in player.get('media_controls',[]) if a!='on_off']
       player['card_mod']['style']='ha-card { background: transparent !important; border: none; box-shadow: none; }\n'+COLORS+'\nmushroom-state-item { padding-right: 100px; }\n'
       x['card_mod']['style']+='\n'+COLORS
       found.append(True)
       return
     for child in x.get('cards',[]):walk(child)
     if isinstance(x.get('card'),dict):walk(x['card'])
    for view in c['views']:
     if view.get('path')=='hall':
      for card in view.get('cards',[]):walk(card)
    assert len(found)==1,(path,len(found))
    # All other views must be byte-for-byte identical in configuration.
    for a,b in zip(old[path]['views'],c['views']):
     if a.get('path')!='hall':assert a==b
   stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S');(ROOT/f'ha-backups/before-hall-tv-header-power-{stamp}.json').write_text(json.dumps(old,indent=2))
   for path in paths:
    assert await call('lovelace/config',url_path=path)==old[path]
    await call('lovelace/config/save',url_path=path,config=new[path]);assert await call('lovelace/config',url_path=path)==new[path]
   print('Verified always-visible TV header power button, adjacent remote icon, and off-state text colors in all three Hall views.')
asyncio.run(main())
