import asyncio,copy,datetime,json
from pathlib import Path
import aiohttp
async def main():
 token=json.load(open('/home/djpi/sunday/config.json'))['ha_token']
 async with aiohttp.ClientSession() as s:
  async with s.ws_connect('ws://localhost:8123/api/websocket') as w:
   await w.receive_json();await w.send_json({'type':'auth','access_token':token});assert (await w.receive_json())['type']=='auth_ok'
   seq=0
   async def call(t,**kw):
    nonlocal seq
    seq+=1;await w.send_json(dict(type=t,id=seq,**kw))
    while True:
     r=await w.receive_json()
     if r.get('id')==seq:
      assert r.get('success'),r.get('error');return r.get('result')
   hall=await call('lovelace/config',url_path='dashboard-hall')
   rani=await call('lovelace/config',url_path='dashboard-rani')
   hall_row=next(c for c in hall['views'][0]['cards'][0]['cards'] if c.get('type')=='horizontal-stack' and 'climate.hall_ac' in json.dumps(c))
   cfg=copy.deepcopy(rani);cards=cfg['views'][0]['cards'][0]['cards']
   fan=copy.deepcopy(next(c for c in cards if c.get('entity')=='fan.rani_s_bedroom_rani_s_bedroom_fan'))
   fan_heading=copy.deepcopy(next(c for c in cards if c.get('heading')=='Fans'))
   ac=copy.deepcopy(hall_row['cards'][0])
   ac['card']['entity']='climate.rani_air';ac['card']['name']='Rani Air'
   # The same dial and Off/Cool buttons as Hall; other modes remain in more-info.
   fan_panel=copy.deepcopy(hall_row['cards'][1])
   fan['card_mod']={'style':'ha-card { background: #f3f5f8; border: 1px solid #dce2ea; box-shadow: none; --primary-text-color: #202b38; }'}
   fan_panel['card']['cards']=[fan_heading,fan]
   fan_panel['card']['card_mod']={'style':':host { display: block; width: 100%; }'}
   row={'type':'horizontal-stack','cards':[ac,fan_panel]}
   old_positions=[i for i,c in enumerate(cards) if c.get('heading') in ['Air Conditioner','Fans'] or c.get('entity') in ['climate.rani_air','fan.rani_s_bedroom_rani_s_bedroom_fan']]
   assert old_positions==list(range(1,5)),old_positions
   cards[1:5]=[row]
   hall_chip=copy.deepcopy(next(c for c in hall['views'][0]['cards'][0]['cards'][0]['chips'] if c.get('entity')=='climate.hall_ac'))
   hall_chip=json.loads(json.dumps(hall_chip).replace('climate.hall_ac','climate.rani_air'))
   for i,c in enumerate(cards[0]['chips']):
    if c.get('entity')=='climate.rani_air':cards[0]['chips'][i]=hall_chip
   backup=Path('/home/djpi/ha-backups/rani-before-hall-ac-style-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'.json')
   backup.write_text(json.dumps(rani,indent=2))
   await call('lovelace/config/save',url_path='dashboard-rani',config=cfg)
   assert await call('lovelace/config',url_path='dashboard-rani')==cfg
   assert await call('lovelace/config',url_path='dashboard-hall')==hall
   assert cards[2:]==rani['views'][0]['cards'][0]['cards'][5:]
   Path('/home/djpi/bedroom-dashboards/rani.json').write_text(json.dumps(cfg,indent=2)+'\n')
   print('Verified Hall thermostat design copied to Rani; AC and fan share an equal-height row. Geyser/lights unchanged. No appliance commands sent.')
asyncio.run(main())
