import asyncio,copy,datetime,json
from pathlib import Path
import aiohttp
def heading(name):
 return {'type':'heading','heading':name,'heading_style':'title','card_mod':{'style':':host { --primary-text-color: #101820; --ha-heading-card-title-color: #101820; --ha-heading-card-title-font-weight: 700; }'}}

SW='switch.mom_s_geyser_socket_1'
POWER='sensor.mom_s_geyser_power'
ENERGY='sensor.mom_s_geyser_total_energy'
async def main():
 token=json.load(open('/home/djpi/sunday/config.json'))['ha_token']
 async with aiohttp.ClientSession() as session:
  async with session.ws_connect('ws://localhost:8123/api/websocket') as ws:
   await ws.receive_json();await ws.send_json({'type':'auth','access_token':token});assert (await ws.receive_json())['type']=='auth_ok'
   seq=0
   async def call(type,**kw):
    nonlocal seq
    seq+=1;await ws.send_json(dict(type=type,id=seq,**kw))
    while True:
     msg=await ws.receive_json()
     if msg.get('id')==seq:
      assert msg.get('success'),msg.get('error')
      return msg.get('result')
   old=await call('lovelace/config',url_path='dashboard-rani')
   registry=await call('config/entity_registry/list')
   devices=await call('config/device_registry/list')
   areas=await call('config/area_registry/list')
   states={s['entity_id']:s for s in await call('get_states')}
   for e in [SW,POWER,ENERGY]:assert e in states
   ent=next(e for e in registry if e['entity_id']==SW)
   device=next(d for d in devices if d['id']==ent['device_id'])
   assert device['name']=="Mom's Geyser"
   area=next(a for a in areas if a['name']=="Rani's Bedroom")
   backup=Path('/home/djpi/ha-backups')/('before-rani-geyser-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'.json')
   backup.write_text(json.dumps({'dashboard':old,'device':device},indent=2))
   config=copy.deepcopy(old)
   cards=config['views'][0]['cards'][0]['cards']
   assert 'Mom’s Geyser' not in json.dumps(config,ensure_ascii=False),'Already added'
   status="{{ 'On' if is_state('"+SW+"','on') else ('Off' if is_state('"+SW+"','off') else 'Unavailable') }}"
   color="{{ 'amber' if is_state('"+SW+"','on') else 'blue-grey' }}"
   cards[0]['chips'].append({'type':'template','entity':SW,'icon':'mdi:water-boiler','content':'Geyser · '+status,'icon_color':color,'tap_action':{'action':'more-info'}})
   header={'type':'custom:mushroom-template-card','entity':SW,'primary':'Mom’s Geyser','secondary':status,'icon':'mdi:water-boiler','icon_color':color,'tap_action':{'action':'more-info'},'card_mod':{'style':'ha-card { background: transparent; border: none; box-shadow: none; padding-right: 60px; }'}}
   button={'type':'custom:mushroom-chips-card','alignment':'end','chips':[{'type':'template','entity':SW,'icon':'mdi:power','icon_color':color,'tap_action':{'action':'toggle'},'hold_action':{'action':'more-info'}}],'card_mod':{'style':':host { position: absolute; top: 20px; right: 18px; width: 40px; } ha-card { --chip-background: #e6edf5; --chip-border-width: 0; --chip-height: 40px; }'}}
   metrics=[]
   for entity,label,icon,unit in [(POWER,'Live power','mdi:lightning-bolt','W'),(ENERGY,'Total energy','mdi:counter','kWh')]:
    metrics.append({'type':'custom:mushroom-template-card','entity':entity,'primary':"{{ (states('"+entity+"') + ' "+unit+"') if is_number(states('"+entity+"')) else 'Unavailable' }}",'secondary':label,'icon':icon,'icon_color':'blue-grey','tap_action':{'action':'more-info'},'card_mod':{'style':'ha-card { background: #edf2f8; border: none; box-shadow: none; border-radius: 14px; }'}})
   card={'type':'custom:mod-card','card_mod':{'style':"ha-card { position: relative; padding: 6px 10px 12px; border-radius: 18px; border: 1px solid #d6e0eb; box-shadow: none; background: {{ '#fff7e9' if is_state('"+SW+"','on') else '#f9fbfe' }}; --primary-text-color: #101820; --secondary-text-color: #516174; }"},'card':{'type':'vertical-stack','cards':[header,{'type':'grid','columns':2,'square':False,'cards':metrics},button]}}
   pos=next(i for i,c in enumerate(cards) if c.get('heading')=='Lights')
   cards[pos:pos]=[heading('Hot Water'),card]
   await call('config/device_registry/update',device_id=device['id'],area_id=area['area_id'])
   await call('lovelace/config/save',url_path='dashboard-rani',config=config)
   assert await call('lovelace/config',url_path='dashboard-rani')==config
   Path('/home/djpi/bedroom-dashboards/rani.json').write_text(json.dumps(config,indent=2)+'\n')
   after={s['entity_id']:s for s in await call('get_states')}
   print('Saved and read-back verified Rani dashboard. Geyser state:',after[SW]['state'])
   print('Power:',after[POWER]['state'],'W; total energy:',after[ENERGY]['state'],'kWh')
   print('Assigned device to:',area['name'])
   print('Backup:',backup)
asyncio.run(main())
