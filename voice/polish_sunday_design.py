import asyncio,copy,datetime,json
from pathlib import Path
import aiohttp
ROOT=Path('/home/djpi')
LIGHTS=['light.moon','light.dashboard','light.top_light','light.panels_room','light.roka_s_room_roka_cove','light.roka_s_room_roka_spots','light.roka_s_room_roka_light_1','light.roka_s_room_roka_light_2','light.roka_s_room_roka_foot_lamp']
count="expand("+', '.join(repr(x) for x in LIGHTS)+") | selectattr('state','eq','on') | list | count"
def chip(entity,icon,content,color):
 return {'type':'template','entity':entity,'icon':icon,'content':content,'icon_color':color,'tap_action':{'action':'more-info'}}
header={'type':'custom:mushroom-chips-card','alignment':'center','chips':[
 chip('sensor.lumi_lumi_sensor_ht_agl02_temperature','mdi:thermometer',"{% set t = states('sensor.lumi_lumi_sensor_ht_agl02_temperature') %}{{ (t | float | round(1)) ~ '°C' if is_number(t) else '— °C' }}",'orange'),
 chip('sensor.lumi_lumi_sensor_ht_agl02_humidity','mdi:water-percent',"{% set h = states('sensor.lumi_lumi_sensor_ht_agl02_humidity') %}{{ (h | float | round(0) | int) ~ '%' if is_number(h) else '— %' }}",'blue'),
 chip('climate.bedroom_ac','mdi:snowflake',"{% set s = states('climate.bedroom_ac') %}{% set t = state_attr('climate.bedroom_ac','temperature') %}AC · {{ 'Off' if s == 'off' else ('Unavailable' if s in ['unknown','unavailable'] else ((t | float | round(1)) ~ '°' if is_number(t) else 'On')) }}","{{ 'blue' if states('climate.bedroom_ac') not in ['off','unknown','unavailable'] else 'grey' }}"),
 chip('light.moon','mdi:lightbulb-group-outline','{{ '+count+' }} lights on',"{{ 'amber' if ("+count+") > 0 else 'grey' }}")
], 'card_mod':{'style':''' :host {
 --chip-height: 34px;
 --chip-spacing: 8px;
 --chip-font-size: 12px;
 --chip-font-weight: 600;
 --chip-background: var(--secondary-background-color);
 --chip-border-color: var(--divider-color);
 --chip-box-shadow: none;
}
ha-card {
 padding: 10px 8px;
 border: 1px solid var(--divider-color);
 border-radius: 14px;
 background: var(--ha-card-background, var(--card-background-color));
 box-shadow: none;
}
.chip-container { gap: 8px; }
'''}}
async def main():
 token=json.loads((ROOT/'sunday/config.json').read_text())['ha_token']
 async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
  async with s.ws_connect('ws://127.0.0.1:8123/api/websocket') as w:
   await w.receive_json();await w.send_json({'type':'auth','access_token':token});assert (await w.receive_json())['type']=='auth_ok'
   counter=0
   async def call(kind,**args):
    nonlocal counter
    counter+=1;await w.send_json(dict(id=counter,type=kind,**args))
    while True:
     r=await w.receive_json()
     if r.get('id')==counter:
      assert r.get('success'),r.get('error');return r.get('result')
   old=await call('lovelace/config',url_path='dashboard-roka');new=copy.deepcopy(old)
   view=next(v for v in new['views'] if v.get('path')=='sunday')
   headers=[i for i,c in enumerate(view['cards']) if c.get('type')=='markdown' and 'lumi_lumi_sensor_ht_agl02_temperature' in c.get('content','')]
   assert len(headers)==1,headers
   view['cards'][headers[0]]=header
   changes={'headings':0,'surfaces':0}
   def append_style(card,css):
    mod=card.setdefault('card_mod',{});style=mod.get('style','')
    if isinstance(style,str):mod['style']=style+'\n'+css
    else:style['.']=style.get('.','')+'\n'+css
   def walk(card):
    if not isinstance(card,dict):return
    kind=card.get('type','')
    if kind=='heading':
     card.pop('icon',None);card['heading_style']='title'
     append_style(card,''' :host {
 --ha-heading-card-title-font-weight: 700;
 --ha-heading-card-title-font-size: 18px;
}
.title, .heading, h1, h2, p { font-weight: 700 !important; }
''');changes['headings']+=1
    if kind in ['thermostat','tile','custom:mushroom-light-card','custom:mushroom-entity-card','custom:mushroom-select-card']:
     append_style(card,'''ha-card { border-radius: 14px; box-sizing: border-box; }
''');changes['surfaces']+=1
    if kind=='grid':
     append_style(card,':host { --grid-card-gap: 8px; }')
    # Only dashboard layout children; popup/remote content and actions are untouched.
    for child in card.get('cards',[]):walk(child)
    if isinstance(card.get('card'),dict):walk(card['card'])
   for card in view['cards']:walk(card)
   # Verify no service actions or entities were changed in existing controls.
   def behavior(x):
    if isinstance(x,dict):return {k:behavior(v) for k,v in x.items() if k not in ['card_mod','icon','heading_style']}
    if isinstance(x,list):return [behavior(v) for v in x]
    return x
   before=next(v for v in old['views'] if v.get('path')=='sunday')
   for i,(a,b) in enumerate(zip(before['cards'],view['cards'])):
    if i != headers[0]:assert behavior(a)==behavior(b),f'Unexpected behavioral change in card {i}'
   assert all(a==b for a,b in zip(old['views'],new['views']) if a.get('path')!='sunday')
   stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S');(ROOT/f'ha-backups/roka-before-sunday-polish-{stamp}.json').write_text(json.dumps(old,indent=2))
   assert await call('lovelace/config',url_path='dashboard-roka')==old,'Concurrent edit'
   await call('lovelace/config/save',url_path='dashboard-roka',config=new)
   assert await call('lovelace/config',url_path='dashboard-roka')==new
   print('Saved and verified Sunday polish:',changes,'; layouts and existing control actions preserved.')
asyncio.run(main())
