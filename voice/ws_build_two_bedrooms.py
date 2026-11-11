"""Assign newly exposed Hogar bedrooms to areas and create two matching dashboards."""
import asyncio,copy,datetime,json
from pathlib import Path
import aiohttp

ROOMS={
 'sian':{'title':'SiAn','area_name':'SiAn Room','area_id':'sian_room','device_identifier':'hogar_sian_bedroom','path':'dashboard-sian','lights':[('hogar_sian_10_1','Mirror Light','mdi:mirror-rectangle'),('hogar_sian_10_2','Spots 1','mdi:spotlight-beam'),('hogar_sian_10_3','Spots 2','mdi:spotlight-beam'),('hogar_sian_10_5','Foot Lamp','mdi:floor-lamp'),('hogar_sian_10_6','Cove','mdi:led-strip-variant')],'fan':'hogar_sian_10_9'},
 'rani':{'title':'Rani','area_name':"Rani's Bedroom",'device_identifier':'hogar_rani_bedroom','path':'dashboard-rani','lights':[('hogar_rani_11_5','Foot Lamp','mdi:floor-lamp'),('hogar_rani_11_6','Cove','mdi:led-strip-variant'),('hogar_rani_11_7','Spots 1','mdi:spotlight-beam'),('hogar_rani_12_0','Spots 2','mdi:spotlight-beam')],'fan':'hogar_rani_11_9'},
}

def heading(name):
 return {'type':'heading','heading':name,'heading_style':'title','card_mod':{'style':':host { --primary-text-color: #101820; --ha-heading-card-title-color: #101820; --ha-heading-card-title-font-weight: 700; } .title, .heading, h1, h2, p { color: #101820 !important; font-weight: 700 !important; }'}}

def chip(entity,icon,content,color):
 return {'type':'template','entity':entity,'icon':icon,'content':content,'icon_color':color,'tap_action':{'action':'more-info'}}

def build(room,entities,states,palette,status_style,background):
 fan=entities[room['fan']]['entity_id']
 lights=[(entities[u]['entity_id'],name,icon) for u,name,icon in room['lights']]
 expression="expand("+','.join(repr(e) for e,_,_ in lights)+") | selectattr('state','eq','on') | list | count"
 status={'type':'custom:mushroom-chips-card','alignment':'center','card_mod':copy.deepcopy(status_style),'chips':[
  chip(fan,'mdi:fan',"Fan · {{ 'On' if is_state('"+fan+"','on') else ('Off' if is_state('"+fan+"','off') else 'Unavailable') }}","{{ 'teal' if is_state('"+fan+"','on') else 'grey' }}"),
  chip(lights[0][0],'mdi:lightbulb-group-outline','{{ '+expression+' }} lights on',"{{ 'amber' if "+expression+" > 0 else 'grey' }}")
 ]}
 fan_card={'type':'tile','entity':fan,'name':'Ceiling Fan','icon':'mdi:fan','color':'blue','tap_action':{'action':'toggle'},'features':[{'type':'fan-preset-modes'}]}
 light_cards=[]
 for entity,name,icon in lights:
  light_cards.append({'type':'custom:mushroom-light-card','entity':entity,'name':name,'icon':icon,'icon_color':'amber','show_brightness_control':'brightness' in states[entity]['attributes'].get('supported_color_modes',[]),'collapsible_controls':True,'tap_action':{'action':'toggle'},'hold_action':{'action':'more-info'}})
 light_cards.append({'type':'custom:mushroom-template-card','primary':'All Off','secondary':'Room lights','icon':'mdi:lightbulb-off-outline','icon_color':'blue-grey','tap_action':{'action':'perform-action','perform_action':'light.turn_off','target':{'entity_id':[e for e,_,_ in lights]}}})
 root={'type':'vertical-stack','card_mod':copy.deepcopy(palette),'cards':[status,heading('Fans'),fan_card,heading('Lights'),{'type':'grid','columns':2,'square':False,'cards':light_cards}]}
 return {'title':room['title'],'views':[{'title':room['title'],'path':'bedroom','icon':'mdi:bed-king-outline','type':'panel','background':background,'cards':[root]}]}

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
     result=await w.receive_json()
     if result.get('id')==seq:
      assert result.get('success'),result.get('error')
      return result.get('result')
   roka=await call({'type':'lovelace/config','url_path':'dashboard-roka'})
   hall=await call({'type':'lovelace/config','url_path':'dashboard-hall'})
   common=await call({'type':'lovelace/config','url_path':'dashboard-common-areas'})
   areas=await call({'type':'config/area_registry/list'})
   devices=await call({'type':'config/device_registry/list'})
   registry=await call({'type':'config/entity_registry/list'})
   states={e['entity_id']:e for e in await call({'type':'get_states'})}
   entities={e['unique_id']:e for e in registry if e.get('platform')=='mqtt'}
   needed={r['fan'] for r in ROOMS.values()}|{u for r in ROOMS.values() for u,_,_ in r['lights']}
   assert needed <= set(entities),'MQTT discovery missing: '+str(needed-set(entities))
   for unique in needed:
    entity=entities[unique]['entity_id'];assert entity in states and states[entity]['state'] not in ['unknown','unavailable'],entity+' not reporting live state'
   dashboards=await call({'type':'lovelace/dashboards/list'})
   existing={d['url_path']:d for d in dashboards}
   stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
   backup=Path('/home/djpi/ha-backups')/('before-two-bedroom-dashboards-'+stamp+'.json')
   backup_data={'areas':areas,'devices':devices,'entities':[e for e in registry if e.get('unique_id') in needed],'dashboard-roka':roka}
   for r in ROOMS.values():
    if r['path'] in existing:backup_data[r['path']]=await call({'type':'lovelace/config','url_path':r['path']})
   backup.write_text(json.dumps(backup_data,indent=2)+'\n')
   palette=hall['views'][0]['cards'][0]['card_mod'];status_style=hall['views'][0]['cards'][0]['cards'][0]['card_mod'];background=hall['views'][0]['background']
   for key,room in ROOMS.items():
    area=next((a for a in areas if a['area_id']==room.get('area_id') or a['name']==room['area_name']),None)
    if area is None:
     area=await call({'type':'config/area_registry/create','name':room['area_name'],'icon':'mdi:bed-king-outline'});areas.append(area)
    area_id=area['area_id']
    room_device_ids={entities[u]['device_id'] for u in needed if u.startswith('hogar_'+key+'_')}
    assert len(room_device_ids)==1
    for did in room_device_ids:await call({'type':'config/device_registry/update','device_id':did,'area_id':area_id})
    for u in needed:
     if u.startswith('hogar_'+key+'_'):
      await call({'type':'config/entity_registry/update','entity_id':entities[u]['entity_id'],'area_id':area_id})
    config=build(room,entities,states,palette,status_style,background)
    folder=Path('/home/djpi/bedroom-dashboards');folder.mkdir(exist_ok=True)
    (folder/(key+'.json')).write_text(json.dumps(config,indent=2)+'\n')
    if room['path'] not in existing:
     await call({'type':'lovelace/dashboards/create','url_path':room['path'],'title':room['title'],'icon':'mdi:bed-king-outline','show_in_sidebar':True,'require_admin':False})
    await call({'type':'lovelace/config/save','url_path':room['path'],'config':config})
    assert await call({'type':'lovelace/config','url_path':room['path']})==config
    print('Saved and verified:',room['path'],'Area:',area['name'],'Devices:',len(room['lights'])+1)
   after_registry=await call({'type':'config/entity_registry/list'})
   after_areas={e['entity_id']:e.get('area_id') for e in after_registry}
   for room in ROOMS.values():
    aid=next(a['area_id'] for a in areas if a['name']==room['area_name'])
    for unique in [room['fan'],*[u for u,_,_ in room['lights']]]:assert after_areas[entities[unique]['entity_id']]==aid
   assert await call({'type':'lovelace/config','url_path':'dashboard-roka'})==roka,'RoKa changed'
   assert await call({'type':'lovelace/config','url_path':'dashboard-common-areas'})==common,'Common Areas changed'
   print('Verified all 11 live entities and bedroom area assignments. RoKa and Common Areas unchanged.')
   print('Backup:',backup)
asyncio.run(main())
