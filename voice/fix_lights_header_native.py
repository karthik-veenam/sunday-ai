import asyncio,copy,datetime,json
from pathlib import Path
import aiohttp
ROOT=Path('/home/djpi')
async def main():
 token=json.load(open(ROOT/'sunday/config.json'))['ha_token']
 async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=40)) as s:
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
   paths=['dashboard-roka','dashboard-rani','dashboard-sian']
   originals={p:await call('lovelace/config',url_path=p) for p in paths};updates=copy.deepcopy(originals);total=0
   for path,c in updates.items():
    for view in c['views']:
     found=[]
     def walk(card):
      if not isinstance(card,dict):return
      if card.get('type')=='custom:mushroom-chips-card':
       chips=card.get('chips',[])
       for j,chip in enumerate(chips):
        if chip.get('type')=='room-lights':
         ids=chip['entities'];expr='expand('+', '.join(repr(x) for x in ids)+") | selectattr('state','eq','on') | list | count"
         count={'type':'template','entity':ids[0],'icon':'mdi:lightbulb-group-outline','content':'{{ '+expr+' }}','icon_color':"{{ 'amber' if ("+expr+") > 0 else 'grey' }}",'tap_action':{'action':'none'}}
         on={'type':'template','icon':'mdi:lightbulb-on-outline','icon_color':'amber','content':'','tap_action':{'action':'perform-action','perform_action':'light.turn_on','target':{'entity_id':ids}}}
         off={'type':'template','icon':'mdi:lightbulb-off-outline','icon_color':'grey','content':'','tap_action':{'action':'perform-action','perform_action':'light.turn_off','target':{'entity_id':ids}}}
         chips[j:j+1]=[count,on,off]
         css=f'''
/* Compact connected lights count / all on / all off. */
:host {{ --chip-height: 34px; --chip-spacing: 6px; --chip-font-size: 12px; }}
.chip-container {{ gap: 6px; }}
mushroom-template-chip:nth-child({j+1}) {{ --chip-border-radius: 18px 0 0 18px; margin-inline-end: -6px; }}
mushroom-template-chip:nth-child({j+2}) {{ --chip-border-radius: 0; margin-inline-end: -6px; }}
mushroom-template-chip:nth-child({j+3}) {{ --chip-border-radius: 0 18px 18px 0; }}
'''
         mod=card.setdefault('card_mod',{});style=mod.get('style','')
         if isinstance(style,str):mod['style']=style+'\n'+css
         else:style['.']=style.get('.','')+'\n'+css
         found.append(ids)
         break
      for child in card.get('cards',[]):walk(child)
      if isinstance(card.get('card'),dict):walk(card['card'])
     for card in view.get('cards',[]):walk(card)
     assert len(found)==1,(path,view.get('path'),len(found));total+=1
   stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S');(ROOT/f'ha-backups/three-dashboards-before-native-lights-{stamp}.json').write_text(json.dumps(originals,indent=2))
   for path in paths:
    assert await call('lovelace/config',url_path=path)==originals[path]
    await call('lovelace/config/save',url_path=path,config=updates[path])
    assert await call('lovelace/config',url_path=path)==updates[path]
   print('Saved and verified native count/on/off chips in',total,'views. No new frontend resource required.')
asyncio.run(main())
