import asyncio,copy,datetime,json
from pathlib import Path
import aiohttp
ROOT=Path('/home/djpi')
CSS='''
/* One-row status strip: labels are carried by icons, values stay compact. */
:host {
 --chip-height: 32px;
 --chip-spacing: 4px;
 --chip-font-size: 11px;
 --chip-icon-size: 16px;
 --chip-padding: 0 0.2em;
}
ha-card { padding: 8px 4px; }
.chip-container {
 display: flex !important;
 flex-wrap: nowrap !important;
 align-items: center;
 justify-content: center;
 gap: 4px !important;
}
.chip-container > * { min-width: 0; flex: 0 1 auto; }
mushroom-template-chip:nth-child(1) { --chip-border-radius: 16px 0 0 16px; margin-inline-end: -4px; }
mushroom-template-chip:nth-child(2) { --chip-border-radius: 0; margin-inline-end: -4px; }
mushroom-template-chip:nth-child(3) { --chip-border-radius: 0 16px 16px 0; }
'''
async def main():
 token=json.load(open(ROOT/'sunday/config.json'))['ha_token']
 async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=40)) as s:
  async with s.ws_connect('ws://127.0.0.1:8123/api/websocket') as w:
   await w.receive_json();await w.send_json({'type':'auth','access_token':token});assert (await w.receive_json())['type']=='auth_ok';n=0
   async def call(kind,**kw):
    nonlocal n
    n+=1;await w.send_json(dict(id=n,type=kind,**kw))
    while True:
     r=await w.receive_json()
     if r.get('id')==n:
      assert r.get('success'),r.get('error');return r.get('result')
   paths=['dashboard-roka','dashboard-rani','dashboard-sian'];originals={p:await call('lovelace/config',url_path=p) for p in paths};updates=copy.deepcopy(originals);found=0
   for config in updates.values():
    for view in config['views']:
     headers=[]
     def walk(card):
      if not isinstance(card,dict):return
      if card.get('type')=='custom:mushroom-chips-card' and any(c.get('icon')=='mdi:lightbulb-group-outline' and c.get('tap_action',{}).get('action')=='none' for c in card.get('chips',[])):
       headers.append(card)
       for chip in card['chips']:
        entity=chip.get('entity','');icon=chip.get('icon','')
        if entity.startswith('climate.'):
         chip['content']="{% set s = states('"+entity+"') %}{% set t = state_attr('"+entity+"','temperature') %}{{ ((t | float | round(1) | string).removesuffix('.0')) ~ '°' if s not in ['off','unknown','unavailable'] and is_number(t) else '' }}"
         chip['icon_color']="{{ 'blue' if states('"+entity+"') not in ['off','unknown','unavailable'] else 'grey' }}"
        elif entity.startswith('fan.') or entity.startswith('media_player.') or ('geyser' in entity):
         chip['content']=''
         color='teal' if entity.startswith('fan.') else ('blue' if entity.startswith('media_player.') else 'orange')
         chip['icon_color']="{{ '"+color+"' if states('"+entity+"') not in ['off','standby','unknown','unavailable'] else 'grey' }}"
        elif entity.startswith('binary_sensor.') and 'Balcony' in chip.get('content',''):
         chip['content']=chip['content'].replace('Balcony · ','')
       mod=card.setdefault('card_mod',{});style=mod.get('style','')
       if isinstance(style,str):
        style=style.split('/* Compact connected lights')[0].split('/* One-row status strip')[0]
        mod['style']=style+'\n'+CSS
       else:style['.']=style.get('.','')+'\n'+CSS
      for child in card.get('cards',[]):walk(child)
      if isinstance(card.get('card'),dict):walk(card['card'])
     for card in view.get('cards',[]):walk(card)
     assert len(headers)==1,(view.get('path'),len(headers));found+=1
   stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S');(ROOT/f'ha-backups/three-dashboards-before-single-row-{stamp}.json').write_text(json.dumps(originals,indent=2))
   for path in paths:
    assert await call('lovelace/config',url_path=path)==originals[path]
    await call('lovelace/config/save',url_path=path,config=updates[path]);assert await call('lovelace/config',url_path=path)==updates[path]
   print('Saved and verified single-row headers in',found,'views.')
asyncio.run(main())
