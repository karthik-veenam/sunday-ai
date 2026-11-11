"""Add a red animated door alert inline with the fridge header."""
import asyncio,copy,datetime,json
from pathlib import Path
import aiohttp
DOORS=['binary_sensor.fridge_refrigerator_door_status','binary_sensor.fridge_freezer_door_status']

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
   paths=['dashboard-kitchen','dashboard-common-areas']
   original={p:await call({'type':'lovelace/config','url_path':p}) for p in paths}
   backup=Path('/home/djpi/ha-backups')/('before-fridge-door-alert-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'.json');backup.write_text(json.dumps(original,indent=2)+'\n')
   updated=copy.deepcopy(original)
   for path,config in updated.items():
    view=config['views'][0] if path=='dashboard-kitchen' else next(v for v in config['views'] if v.get('path')=='kitchen')
    cards=view['cards'][0]['cards']
    group=next(c for c in cards if c.get('type')=='custom:mod-card' and c.get('card',{}).get('cards',[{}])[0].get('entity')=='sensor.fridge_refrigerator_target_temp_level')
    header=group['card']['cards'][0]
    header['secondary']="{% set f = states('binary_sensor.fridge_refrigerator_door_status') %}{% set z = states('binary_sensor.fridge_freezer_door_status') %}{{ 'Both doors open' if f == 'on' and z == 'on' else ('Fridge door open' if f == 'on' else ('Freezer door open' if z == 'on' else ('Both doors closed' if f == 'off' and z == 'off' else 'Door status unavailable'))) }}"
    header['icon_color']="{{ 'red' if is_state('binary_sensor.fridge_refrigerator_door_status','on') or is_state('binary_sensor.fridge_freezer_door_status','on') else ('blue' if states('sensor.fridge_refrigerator_target_temp_level') not in ['unavailable','unknown'] else 'grey') }}"
    header['card_mod']['style']+='\nha-card { padding-right: 58px; }'
    alert={'type':'custom:mushroom-chips-card','alignment':'end','chips':[{'type':'conditional','conditions':[{'condition':'or','conditions':[{'condition':'state','entity':d,'state':'on'} for d in DOORS]}],'chip':{'type':'template','entity':DOORS[0],'icon':'mdi:fridge-alert-outline','icon_color':'red','tap_action':copy.deepcopy(header['tap_action'])}}],'card_mod':{'style': ''':host {
      position: absolute; right: 12px; top: 14px; z-index: 3;
      --chip-height: 38px; --chip-icon-size: 24px;
      --chip-background: #ffe5e5; --chip-border-color: #f1aaaa;
      --chip-icon-color: #c62828; --primary-text-color: #c62828;
      width: 40px;
     }
     ha-card { padding: 0; background: transparent; border: none; box-shadow: none; }
     mushroom-template-chip { animation: door-alert-pulse 1.8s ease-in-out infinite; }
     @keyframes door-alert-pulse {
       0%, 100% { opacity: 1; filter: drop-shadow(0 0 0 rgba(198,40,40,0)); }
       50% { opacity: .55; filter: drop-shadow(0 0 4px rgba(198,40,40,.4)); }
     }
     @media (prefers-reduced-motion: reduce) { mushroom-template-chip { animation: none; } }
    '''}}
    # The conditional wrapper has a shadow boundary, so animate the chips host
    # only when an open-door condition is true; closed doors leave no badge.
    alert['card_mod']['style']+='''
    :host { animation: {{ 'door-alert-pulse 1.8s ease-in-out infinite' if is_state('binary_sensor.fridge_refrigerator_door_status','on') or is_state('binary_sensor.fridge_freezer_door_status','on') else 'none' }}; }
    @media (prefers-reduced-motion: reduce) { :host { animation: none !important; } }
    '''
    header_container={'type':'custom:mod-card','card':{'type':'vertical-stack','cards':[header,alert]},'card_mod':{'style':'ha-card { position: relative; background: transparent; border: none; box-shadow: none; }'}}
    group['card']['cards'][0]=header_container
   assert [v for v in original[paths[1]]['views'] if v['path']!='kitchen']==[v for v in updated[paths[1]]['views'] if v['path']!='kitchen']
   for path,config in updated.items():
    await call({'type':'lovelace/config/save','url_path':path,'config':config})
    assert await call({'type':'lovelace/config','url_path':path})==config
    print('Saved and verified:',path)
   print('Backup:',backup)
if __name__=='__main__':asyncio.run(main())
