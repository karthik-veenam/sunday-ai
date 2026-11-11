import asyncio,copy,json,datetime
from pathlib import Path
from central_ac_inching import HA
async def main():
 async with HA() as h:
  await h.service('automation','reload',{})
  paths=['dashboard-roka','dashboard-rani','dashboard-sian','dashboard-hall','dashboard-common-areas']
  old={p:await h.call('lovelace/config',url_path=p) for p in paths};resources=await h.call('lovelace/resources');stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S');Path('/home/djpi/ha-backups/before-central-ac-inching-dashboards-'+stamp+'.json').write_text(json.dumps({'dashboards':old,'resources':resources},indent=2))
  if not any('/local/central-ac-inching-card.js' in r['url'] for r in resources):await h.call('lovelace/resources/create',url='/local/central-ac-inching-card.js?v=1',res_type='module')
  for path,original in old.items():
   config=copy.deepcopy(original);found=[]
   def walk(x):
    if isinstance(x,dict):
     card=x.get('card',{})
     if x.get('type')=='custom:mod-card' and card.get('type')=='thermostat' and card.get('entity')=='climate.hall_ac':
      card['card_mod']['style']+='\n/* Timer occupies the original more-info position. */\nha-icon-button { visibility: hidden; }\n.header { padding-right: 36px; }\n'
      chip={'type':'custom:mushroom-chips-card','alignment':'end','chips':[{'type':'template','entity':'input_boolean.central_ac_inching','icon':"{{ 'mdi:timer-check-outline' if is_state('input_boolean.central_ac_inching','on') else 'mdi:timer-outline' }}",'icon_color':"{{ 'blue' if is_state('input_boolean.central_ac_inching','on') else 'blue-grey' }}",'content':'','tap_action':{'action':'fire-dom-event','browser_mod':{'service':'browser_mod.popup','data':{'title':'Central AC · Auto-off','content':{'type':'custom:central-ac-inching-card'}}}}}], 'card_mod':{'style':':host { position: absolute; top: 6px; right: 2px; z-index: 2; --chip-height: 40px; --chip-icon-size: 25px; --chip-background: #e6eff8; --chip-border-color: transparent; --chip-box-shadow: none; } ha-card { border: none; box-shadow: none; background: transparent; }'}}
      x['card']={'type':'vertical-stack','cards':[card,chip],'card_mod':{'style':':host { display: block; position: relative; width: calc(100% - 20px); max-width: 260px; } #root { position: relative; }'}}
      x['card_mod']['style']+='\nha-card > hui-vertical-stack-card { display: block; width: calc(100% - 20px); max-width: 260px; }\n';found.append(1);return
     for v in x.values():walk(v)
    elif isinstance(x,list):
     for v in x:walk(v)
   walk(config);assert len(found)==1,(path,len(found));assert await h.call('lovelace/config',url_path=path)==original;await h.call('lovelace/config/save',url_path=path,config=config);assert await h.call('lovelace/config',url_path=path)==config
  states=await h.call('get_states');print([(s['entity_id'],s['state']) for s in states if s['entity_id'].startswith('automation.central_ac') or s['entity_id']=='climate.hall_ac'])
  print('Timer control installed in all five Hall copies.')
asyncio.run(main())
