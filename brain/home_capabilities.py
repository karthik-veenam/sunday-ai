"""Bounded control metadata derived from live HA state and service definitions."""
import re
from home_presentation import ordinary,friendly

ATTRS={
 'climate':('hvac_modes','temperature','target_temp_low','target_temp_high','current_temperature','min_temp','max_temp','target_temp_step','fan_mode','fan_modes','preset_mode','preset_modes','swing_mode','swing_modes'),
 'fan':('percentage','percentage_step','preset_mode','preset_modes','oscillating','direction'),
 'light':('brightness','supported_color_modes','color_temp_kelvin','min_color_temp_kelvin','max_color_temp_kelvin'),
 'cover':('current_position',),'media_player':('volume_level','is_volume_muted','source','source_list'),
 'input_number':('min','max','step','unit_of_measurement'), 'input_select':('options',),
 'switch':(),'input_boolean':(),'select':('options',),'number':('min','max','step','unit_of_measurement'),
}
def small(v):
 if isinstance(v,list):return v[:16]
 if isinstance(v,str):return v[:160]
 return v

def service_fields(definition):
 out=[]
 for name,spec in definition.get('fields',{}).items():
  if isinstance(spec,dict) and 'fields' in spec:out.extend(service_fields(spec))
  else:out.append(name)
 return out

def allowed_by_target(definition,e):
 targets=definition.get('target',{}).get('entity',[])
 if isinstance(targets,dict):targets=[targets]
 if not targets:return True
 for target in targets:
  domains=target.get('domain',[]);domains=[domains] if isinstance(domains,str) else domains
  if domains and e['domain'] not in domains:continue
  masks=target.get('supported_features',[]);masks=[masks] if isinstance(masks,int) else masks
  features=e.get('attributes',{}).get('supported_features',0)
  if not masks or any(features & mask == mask for mask in masks):return True
 return False

def device_controls(model,rows,services,limit=16):
 from home_tools import ALLOWED
 catalog={item['domain']:item.get('services',{}) for item in services};out=[]
 for e in rows:
  domain=e['domain']
  if domain not in ATTRS or e.get('disabled_by') or (domain not in ('input_number','input_select','input_boolean') and not ordinary(e)):continue
  attrs=e.get('attributes',{});actions={}
  for name in sorted(ALLOWED.get(domain,set())):
   definition=catalog.get(domain,{}).get(name)
   if definition is None or not allowed_by_target(definition,e):continue
   fields=service_fields(definition)
   if domain=='light' and attrs.get('supported_color_modes')==['onoff']:
    fields=[f for f in fields if f in ('transition','flash')]
   actions[name]={'fields':fields[:18]}
  if not actions:continue
  out.append({'entity_id':e['entity_id'],'name':friendly(e,model),'domain':domain,'state':e['state'],'available':e['available'],
              'reported_state_only':bool(attrs.get('assumed_state')),
              'settings':{k:small(attrs[k]) for k in ATTRS[domain] if k in attrs},'actions':actions})
  if len(out)>=limit:break
 return out

STOP={'what','which','are','is','the','my','our','in','room','all','list','show','me','automation','automations','routine','routines','enable','disable','turn','on','off','please','available','exists','exist'}
def automation_catalog(model,query='',area=None,all_areas=False):
 q=set(re.findall(r'[a-z0-9]+',query.lower()))-STOP
 areas=set(model.area_ids(query,area));out=[]
 for eid,e in model.entities.items():
  if e['domain']!='automation' or e.get('disabled_by'):continue
  p=model.programs.get(eid,{})
  names=[e['name'],*e.get('aliases',[])];words=set(re.findall(r'[a-z0-9]+',' '.join(names).lower()))
  linked={model.entities[x].get('area_id') for x in p.get('related_entities',[]) if x in model.entities}-{None}
  named=bool(q) and q<=words
  if q and not named:continue
  if not all_areas and not named and e.get('area_id') not in areas and linked and not linked.intersection(areas):continue
  if not all_areas and not named and e.get('area_id') and e['area_id'] not in areas:continue
  out.append({'entity_id':eid,'name':e['name'],'enabled':e['state']=='on','state':e['state'],'available':e['available'],
              'areas':sorted(linked|({e['area_id']} if e.get('area_id') else set())),
              'description':str(p.get('source',{}).get('description',''))[:180],
              'actions':{'enable':'automation.turn_on','disable':'automation.turn_off'},
})
 return sorted(out,key=lambda item:item['entity_id'])
