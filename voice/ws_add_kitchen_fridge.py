"""Add an integrated read-only fridge summary to both production Kitchen views."""
import asyncio,copy,datetime,json
from pathlib import Path
import aiohttp

TEMP={'Fridge':'sensor.fridge_refrigerator_target_temp_level','Freezer':'sensor.fridge_freezer_target_temp_level','Magic Zone':'sensor.fridge_vt_room_target_temp_level'}
DOORS={'Fridge door':'binary_sensor.fridge_refrigerator_door_status','Freezer door':'binary_sensor.fridge_freezer_door_status'}
MODES={'Power Cool':'binary_sensor.fridge_quick_refrigerating_mode','Turbo Icing':'binary_sensor.fridge_quick_freezing_mode','Auto Set':'binary_sensor.fridge_intelligence_mode','Holiday':'binary_sensor.fridge_holiday_mode'}

def temp(entity):
 return "{% set v = states('"+entity+"') %}{{ v.split('℃')[0] ~ '°C' if '℃' in v else 'Unavailable' }}"

def popup(root_style):
 cards=[]
 for label,entity in TEMP.items():
  cards.append({'type':'custom:mushroom-template-card','entity':entity,'primary':label,'secondary':'Set to '+temp(entity),'icon':'mdi:snowflake' if label=='Freezer' else 'mdi:fridge-outline','icon_color':'blue','tap_action':{'action':'more-info'}})
 cards.append({'type':'grid','columns':2,'square':False,'cards':[{'type':'custom:mushroom-template-card','entity':entity,'primary':label,'secondary':"{{ 'Open' if is_state('"+entity+"','on') else ('Closed' if is_state('"+entity+"','off') else 'Unavailable') }}",'icon':'mdi:door-open','icon_color':"{{ 'amber' if is_state('"+entity+"','on') else 'teal' }}",'tap_action':{'action':'more-info'}} for label,entity in DOORS.items()]})
 cards.append({'type':'grid','columns':2,'square':False,'cards':[{'type':'custom:mushroom-template-card','entity':entity,'primary':label,'secondary':"{{ 'On' if is_state('"+entity+"','on') else ('Off' if is_state('"+entity+"','off') else 'Unavailable') }}",'icon':'mdi:beach' if label=='Holiday' else 'mdi:snowflake','icon_color':"{{ 'blue' if is_state('"+entity+"','on') else 'grey' }}",'tap_action':{'action':'more-info'}} for label,entity in MODES.items()]})
 return {'type':'vertical-stack','cards':cards,'card_mod':copy.deepcopy(root_style)}

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
   states=await call({'type':'get_states'});known={e['entity_id'] for e in states}
   assert set(TEMP.values())|set(DOORS.values())|set(MODES.values()) <= known
   paths=['dashboard-kitchen','dashboard-common-areas']
   original={p:await call({'type':'lovelace/config','url_path':p}) for p in paths}
   backup=Path('/home/djpi/ha-backups')/('before-kitchen-fridge-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'.json')
   backup.write_text(json.dumps(original,indent=2)+'\n')
   updated=copy.deepcopy(original)
   common=updated[paths[1]];view=next(v for v in common['views'] if v.get('path')=='kitchen')
   root=view['cards'][0];cards=root['cards']
   assert not any(c.get('entity')==TEMP['Fridge'] for c in cards)
   label=copy.deepcopy(next(c for c in cards if c.get('heading')=='Lights'));label['heading']='Fridge'
   summary="Set: Fridge "+temp(TEMP['Fridge'])+" · Freezer "+temp(TEMP['Freezer'])+"\n{% set d = ['binary_sensor.fridge_refrigerator_door_status','binary_sensor.fridge_freezer_door_status'] %}{{ 'Door open' if expand(d) | selectattr('state','eq','on') | list | count > 0 else ('Both doors closed' if expand(d) | selectattr('state','eq','off') | list | count == 2 else 'Door status unavailable') }}"
   fridge={'type':'custom:mushroom-template-card','entity':TEMP['Fridge'],'primary':'Haier Fridge','secondary':summary,'multiline_secondary':True,'icon':'mdi:fridge-outline','icon_color':"{{ 'amber' if is_state('binary_sensor.fridge_refrigerator_door_status','on') or is_state('binary_sensor.fridge_freezer_door_status','on') else ('blue' if states('sensor.fridge_refrigerator_target_temp_level') not in ['unavailable','unknown'] else 'grey') }}",'tap_action':{'action':'fire-dom-event','browser_mod':{'service':'browser_mod.popup','data':{'title':'Haier Fridge','content':popup(root['card_mod'])}}},'hold_action':{'action':'more-info'},'card_mod':{'style':'ha-card { background: linear-gradient(135deg, #f9fbfe, #eaf3fc); border: 1px solid #d6e0eb; border-radius: 14px; }'}}
   idx=next(i for i,c in enumerate(cards) if c.get('heading')=='Lights');cards[idx:idx]=[label,fridge]
   # Match the standalone dashboard to the already-approved Kitchen design.
   standalone=updated[paths[0]]['views'][0];route=standalone['path'];icon=standalone.get('icon')
   standalone.clear();standalone.update(copy.deepcopy(view));standalone['path']=route
   if icon:standalone['icon']=icon
   before_other=[v for v in original[paths[1]]['views'] if v.get('path')!='kitchen']
   assert before_other==[v for v in common['views'] if v.get('path')!='kitchen']
   for path,c in updated.items():
    await call({'type':'lovelace/config/save','url_path':path,'config':c})
    assert await call({'type':'lovelace/config','url_path':path})==c
    print('Saved and verified:',path)
   print('Backup:',backup)
if __name__=='__main__':asyncio.run(main())
