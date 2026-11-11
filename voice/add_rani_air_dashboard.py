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
      assert r.get('success'),r.get('error')
      return r.get('result')
   registry=await call('config/entity_registry/list')
   entry=next(e for e in registry if e.get('unique_id')=='tuya_ir_ac_d75a423c7fd8bd9987hv1k')
   entity=entry['entity_id']
   states={x['entity_id']:x for x in await call('get_states')}
   state=states[entity];assert state['state'] not in ['unknown','unavailable']
   old=await call('lovelace/config',url_path='dashboard-rani')
   area=next(a for a in await call('config/area_registry/list') if a['name']=="Rani's Bedroom")
   stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
   Path('/home/djpi/ha-backups/rani-before-air-'+stamp+'.json').write_text(json.dumps(old,indent=2))
   cfg=copy.deepcopy(old);cards=cfg['views'][0]['cards'][0]['cards']
   assert entity not in json.dumps(cfg),'Already added'
   style=copy.deepcopy(next(c['card_mod'] for c in cards if c.get('type')=='heading'))
   heading={'type':'heading','heading':'Air Conditioner','heading_style':'title','card_mod':style}
   card={'type':'custom:mushroom-climate-card','entity':entity,'name':'Rani Air','icon':'mdi:air-conditioner','show_temperature_control':True,'collapsible_controls':False,'hvac_modes':['off','cool','auto','dry','fan_only','heat'],'tap_action':{'action':'more-info'},'card_mod':{'style':'ha-card { background: #f9fbfe; border: 1px solid #d6e0eb; border-radius: 18px; box-shadow: none; --primary-text-color: #101820; --secondary-text-color: #516174; }'}}
   cards[1:1]=[heading,card]
   cards[0]['chips'].insert(0,{'type':'template','entity':entity,'icon':'mdi:snowflake','content':"AC · {{ 'Off' if is_state('"+entity+"','off') else ('Unavailable' if states('"+entity+"') in ['unknown','unavailable'] else 'On') }}",'icon_color':"{{ 'grey' if is_state('"+entity+"','off') else 'blue' }}",'tap_action':{'action':'more-info'}})
   await call('config/device_registry/update',device_id=entry['device_id'],area_id=area['area_id'])
   await call('lovelace/config/save',url_path='dashboard-rani',config=cfg)
   assert await call('lovelace/config',url_path='dashboard-rani')==cfg
   Path('/home/djpi/bedroom-dashboards/rani.json').write_text(json.dumps(cfg,indent=2)+'\n')
   print('Saved and verified:',entity,'state:',state['state'],'target:',state['attributes'].get('temperature'),'fan:',state['attributes'].get('fan_mode'))
   print('Assigned to Rani. Existing cards preserved. No live AC commands issued.')
asyncio.run(main())
