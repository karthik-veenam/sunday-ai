"""Improve Hall Test titles and replace its legacy status banner."""
import asyncio,copy,datetime,json
from pathlib import Path
import aiohttp

def polish(config):
 root=config['views'][0]['cards'][0]
 cards=root['cards']
 assert cards[0]['type']=='markdown'
 def indicator(icon,content,color,entity):
  return {'type':'template','icon':icon,'content':content,'icon_color':color,'entity':entity,'tap_action':{'action':'more-info'}}
 chips=[
  indicator('mdi:snowflake',"AC · {% if is_state('climate.hall_ac','off') %}Off{% elif is_state('climate.hall_ac','unavailable') or is_state('climate.hall_ac','unknown') %}Unavailable{% else %}{{ state_attr('climate.hall_ac','temperature') }}°{% endif %}","{{ 'blue' if is_state('climate.hall_ac','cool') else 'grey' }}",'climate.hall_ac'),
  indicator('mdi:fan',"Fan · {{ 'On' if is_state('fan.hall_fan','on') else ('Off' if is_state('fan.hall_fan','off') else 'Unavailable') }}","{{ 'teal' if is_state('fan.hall_fan','on') else 'grey' }}",'fan.hall_fan'),
  indicator('mdi:television',"TV · {{ 'Off' if is_state('media_player.bravia_kd_65x75k','off') or is_state('media_player.bravia_kd_65x75k','standby') else ('Unavailable' if is_state('media_player.bravia_kd_65x75k','unavailable') or is_state('media_player.bravia_kd_65x75k','unknown') else 'On') }}","{{ 'blue' if states('media_player.bravia_kd_65x75k') not in ['off','standby','unavailable','unknown'] else 'grey' }}",'media_player.bravia_kd_65x75k'),
  indicator('mdi:lightbulb-group-outline',"{{ expand('light.hall_hall_tv_cove','light.hall_hall_other_spots','light.hall_hall_plant_spots') | selectattr('state','eq','on') | list | count }} lights on","{{ 'amber' if expand('light.hall_hall_tv_cove','light.hall_hall_other_spots','light.hall_hall_plant_spots') | selectattr('state','eq','on') | list | count > 0 else 'grey' }}",'light.hall_hall_tv_cove'),
 ]
 cards[0]={'type':'custom:mushroom-chips-card','alignment':'center','chips':chips,'card_mod':{'style': ''':host {
   --chip-background: #e7eef7;
   --chip-border-color: transparent;
   --chip-box-shadow: none;
   --chip-height: 36px;
   --chip-spacing: 8px;
   --chip-font-size: 12px;
   --chip-font-weight: 600;
   --primary-text-color: #233247;
 }
 ha-card {
   padding: 12px 8px;
   background: #f9fbfe;
   border: 1px solid #d6e0eb;
   border-radius: 14px;
   box-shadow: 0 2px 8px rgba(43,65,91,0.06);
 }
 .chip-container { gap: 8px; }
 '''}}
 row=next(c for c in cards if c.get('type')=='horizontal-stack')
 ac=row['cards'][0]['card']
 assert ac['type']=='thermostat'
 ac['card_mod']['style']+='''
 :host { --primary-text-color: #101820; --ha-card-header-color: #101820; }
 .header, .name, .title, [slot="name"], ha-card .header {
   color: #101820 !important;
   font-weight: 700 !important;
 }
 '''
 heading=row['cards'][1]['card']['cards'][0]
 assert heading['heading']=='Fans'
 heading['card_mod']={'style': ''':host {
   --primary-text-color: #101820;
   --ha-heading-card-title-color: #101820;
   --ha-heading-card-title-font-weight: 700;
 }
 .title, .heading, h1, h2, p { color: #101820 !important; font-weight: 700 !important; }
 '''}
 return config

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
   original=await call({'type':'lovelace/config','url_path':'dashboard-hall-test'})
   backup=Path('/home/djpi/ha-backups')/('hall-test-before-status-polish-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'.json')
   backup.write_text(json.dumps(original,indent=2)+'\n')
   updated=polish(copy.deepcopy(original))
   before=original['views'][0]['cards'][0]['cards'];after=updated['views'][0]['cards'][0]['cards']
   assert before[2:]==after[2:], 'TV or lights changed'
   await call({'type':'lovelace/config/save','url_path':'dashboard-hall-test','config':updated})
   assert await call({'type':'lovelace/config','url_path':'dashboard-hall-test'})==updated
   print('Saved and verified Hall Test title and status improvements. Backup:',backup)
if __name__=='__main__':asyncio.run(main())
