"""Show three live fridge zone controls directly on both Kitchen dashboards."""
import asyncio,copy,datetime,json
from pathlib import Path
import aiohttp
ZONES=[('Refrigerator','number.fridge_refrigerator_temperature','mdi:fridge-outline'),('Freezer','number.fridge_freezer_temperature','mdi:snowflake'),('Magic Zone','number.fridge_magic_zone_temperature','mdi:thermometer')]

async def main():
 token=json.load(open('/home/djpi/sunday/config.json'))['ha_token']
 async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
  async with s.ws_connect('ws://localhost:8123/api/websocket') as w:
   await w.receive_json();await w.send_json({'type':'auth','access_token':token});assert (await w.receive_json())['type']=='auth_ok'
   seq=0
   async def call(payload):
    nonlocal seq
    seq+=1;await w.send_json(dict(payload,id=seq))
    while True:
     r=await w.receive_json()
     if r.get('id')==seq:
      assert r.get('success'),r.get('error')
      return r.get('result')
   states={e['entity_id']:e for e in await call({'type':'get_states'})}
   for label,entity,icon in ZONES:
    assert entity in states and states[entity]['state'] not in ('unknown','unavailable'),entity+' unavailable'
    # Exercise the actual HA number service without changing the current setting.
    await call({'type':'call_service','domain':'number','service':'set_value','target':{'entity_id':entity},'service_data':{'value':float(states[entity]['state'])}})
    print('Validated number service at current setting:',entity,states[entity]['state'],flush=True)
   paths=['dashboard-kitchen','dashboard-common-areas']
   originals={p:await call({'type':'lovelace/config','url_path':p}) for p in paths}
   backup=Path('/home/djpi/ha-backups')/('before-inline-fridge-temperatures-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'.json');backup.write_text(json.dumps(originals,indent=2)+'\n')
   updated=copy.deepcopy(originals)
   for path,config in updated.items():
    view=config['views'][0] if path=='dashboard-kitchen' else next(v for v in config['views'] if v.get('path')=='kitchen')
    cards=view['cards'][0]['cards'];idx=next(i for i,c in enumerate(cards) if c.get('entity')=='sensor.fridge_refrigerator_target_temp_level')
    header=cards[idx]
    header['secondary']="{{ 'Door open' if is_state('binary_sensor.fridge_refrigerator_door_status','on') or is_state('binary_sensor.fridge_freezer_door_status','on') else ('Both doors closed' if is_state('binary_sensor.fridge_refrigerator_door_status','off') and is_state('binary_sensor.fridge_freezer_door_status','off') else 'Door status unavailable') }}"
    controls=[{'type':'tile','entity':entity,'name':label,'icon':icon,'color':'blue','features_position':'inline','features':[{'type':'numeric-input','style':'buttons'}],'tap_action':{'action':'none'},'icon_tap_action':{'action':'none'},'card_mod':{'style':'ha-card { background: #f9fbfe; border: 1px solid #d6e0eb; border-radius: 14px; --primary-text-color: #233247; --secondary-text-color: #576b82; --control-number-buttons-background-color: #e5edf6; }'}} for label,entity,icon in ZONES]
    cards[idx+1:idx+1]=controls
   assert [v for v in originals['dashboard-common-areas']['views'] if v['path']!='kitchen']==[v for v in updated['dashboard-common-areas']['views'] if v['path']!='kitchen']
   for path,config in updated.items():
    await call({'type':'lovelace/config/save','url_path':path,'config':config})
    assert await call({'type':'lovelace/config','url_path':path})==config
    print('Saved and verified inline controls:',path)
   print('Backup:',backup)
if __name__=='__main__':asyncio.run(main())
