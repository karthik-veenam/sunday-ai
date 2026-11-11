"""Match Kitchen and Dining in Common Areas to the approved Hall styling."""
import asyncio,copy,datetime,json
from pathlib import Path
import aiohttp

def heading(label):
 return {'type':'heading','heading':label,'heading_style':'title','card_mod':{'style':':host { --primary-text-color: #101820; --ha-heading-card-title-color: #101820; --ha-heading-card-title-font-weight: 700; } .title, .heading, h1, h2, p { color: #101820 !important; font-weight: 700 !important; }'}}

def indicator(entity,icon,content,color):
 return {'type':'template','entity':entity,'icon':icon,'content':content,'icon_color':color,'tap_action':{'action':'more-info'}}

def convert(view,hall):
 root=view['cards'][0]; old=root['cards']
 lights_grid=next(c for c in old if c.get('type')=='grid')
 lights=[copy.deepcopy(c) for c in lights_grid['cards'] if c.get('entity','').startswith('light.')]
 entities=','.join(repr(c['entity']) for c in lights)
 count="expand("+entities+") | selectattr('state','eq','on') | list | count"
 chips=[indicator(lights[0]['entity'],'mdi:lightbulb-group-outline','{{ '+count+' }} lights on',"{{ 'amber' if "+count+" > 0 else 'grey' }}")]
 controls=[]
 if view['path']=='kitchen':
  fan=copy.deepcopy(next(c for c in old if c.get('entity','').startswith('fan.')))
  entity=fan['entity']
  chips.insert(0,indicator(entity,'mdi:fan',"Fan · {{ 'On' if is_state('"+entity+"','on') else ('Off' if is_state('"+entity+"','off') else 'Unavailable') }}","{{ 'teal' if is_state('"+entity+"','on') else 'grey' }}"))
  # Retain the native fan preset selector and the existing haptic action.
  controls=[heading('Fans'),fan]
 else:
  balcony=copy.deepcopy(next(c for c in lights_grid['cards'] if c.get('entity','').startswith('binary_sensor.')))
  entity=balcony['entity']
  chips.append(indicator(entity,'mdi:door-sliding',"Balcony · {{ 'Open' if is_state('"+entity+"','on') else ('Closed' if is_state('"+entity+"','off') else 'Unavailable') }}","{{ 'amber' if is_state('"+entity+"','on') else 'teal' }}"))
  balcony['type']='custom:mushroom-entity-card';balcony['icon_color']='blue-grey'
  controls=[heading('Balcony'),balcony]
 for light in lights:
  light['type']='custom:mushroom-light-card'
  light.update(icon_color='amber',show_brightness_control=True,collapsible_controls=True)
 alloff=copy.deepcopy(next(c for c in old if c.get('entity','').startswith('button.')))
 alloff['type']='custom:mushroom-entity-card';alloff['icon_color']='blue-grey';alloff['name']='All Off'
 alloff.setdefault('tap_action',{'action':'perform-action','perform_action':'button.press','target':{'entity_id':alloff['entity']}})
 status=copy.deepcopy(hall['cards'][0]['cards'][0]);status['chips']=chips
 root['cards']=[status,*controls,heading('Lights'),{'type':'grid','columns':2,'square':False,'cards':[*lights,alloff]}]
 root['card_mod']=copy.deepcopy(hall['cards'][0]['card_mod'])
 view['background']=hall['background']
 return view

def entities_and_actions(value,result=None):
 if result is None: result={}
 if isinstance(value,dict):
  if 'entity' in value and value.get('type') not in ('template',):
   result[value['entity']]=value.get('tap_action')
  for k,v in value.items():
   if k!='chips': entities_and_actions(v,result)
 elif isinstance(value,list):
  for v in value: entities_and_actions(v,result)
 return result

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
   original=await call({'type':'lovelace/config','url_path':'dashboard-common-areas'})
   hall=next(v for v in original['views'] if v['path']=='hall')
   backup=Path('/home/djpi/ha-backups')/('common-areas-before-room-styling-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'.json')
   backup.write_text(json.dumps(original,indent=2)+'\n')
   updated=copy.deepcopy(original)
   count=0
   for view in updated['views']:
    if view.get('path') not in ('kitchen','dining'):continue
    previous=copy.deepcopy(view)
    convert(view,hall)
    before=entities_and_actions(previous);after=entities_and_actions(view)
    assert set(before)==set(after), 'Room entities changed'
    assert all(after[e]==a for e,a in before.items() if a is not None), 'Existing actions changed'
    count+=1
   assert count==2
   assert next(v for v in updated['views'] if v['path']=='hall')==hall
   await call({'type':'lovelace/config/save','url_path':'dashboard-common-areas','config':updated})
   assert await call({'type':'lovelace/config','url_path':'dashboard-common-areas'})==updated
   print('Saved and verified Kitchen and Dining styling in Common Areas. Hall unchanged. All room entities and existing actions retained.')
   print('Backup:',backup)
if __name__=='__main__':asyncio.run(main())
