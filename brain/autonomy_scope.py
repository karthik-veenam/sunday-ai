"""Room scope for background processing; reactive device control is independent."""
import re
DOMAINS=set('light switch fan climate cover media_player humidifier water_heater vacuum lock sensor binary_sensor automation script scene button number select remote camera device_tracker person assist_satellite input_boolean input_number input_select'.split())
ENTITY=re.compile(r'\b([a-z_]+\.[a-z0-9_]+)\b')
def enabled(config):return bool(getattr(config,'autonomy_primary_room_only',False))
def entity_allowed(home,config,eid,seen=None,purpose=None):
 policy=getattr(home,'input_policy',None)
 if policy:return policy.entity(purpose,eid,seen) if purpose else any(policy.entity(p,eid,seen) for p in ('memory','proactivity'))
 if not enabled(config):return True
 if not home:return False
 primary=home.household.get('primary_area');seen=set(seen or ())
 if eid in seen:return False
 seen.add(eid)
 if eid.startswith('person:'):
  return eid[7:].casefold() in {str(p).casefold() for p in [home.household.get('primary_person'),*home.household.get('occupants',{}).get(primary,[])] if p}
 # Configured resident location identities are personal context, not room devices.
 residents={str(p).casefold() for p in [home.household.get('primary_person'),*home.household.get('occupants',{}).get(primary,[])] if p}
 for name,cfg in home.household.get('people',{}).items():
  if str(name).casefold() in residents and cfg.get('ha_person')==eid:return True
 e=home.entities.get(eid)
 if not e:return False
 if e.get('domain') in {'automation','script','scene'}:
  linked=home.programs.get(eid,{}).get('related_entities',[])
  return bool(linked) and all(entity_allowed(home,config,x,seen) for x in linked)
 return e.get('area_id')==primary

def allowed(home,config,data,purpose=None):
 policy=getattr(home,'input_policy',None)
 if policy:return policy.allows(purpose,data) if purpose else any(policy.allows(p,data) for p in ('memory','proactivity'))
 if not enabled(config):return True
 if not home:return False
 primary=home.household.get('primary_area')
 aliases={str(x).casefold() for x in [primary,*home.household.get('area_names',{}).get(primary,[])]}
 if isinstance(data,dict):
  for k,v in data.items():
   if k in {'area','area_id','areas'} and v:
    vals=v if isinstance(v,list) else [v]
    if any(str(a).casefold() not in aliases for a in vals):return False
   if not allowed(home,config,v):return False
  return True
 if isinstance(data,(tuple,list)):return all(allowed(home,config,v) for v in data)
 if isinstance(data,str):
  if data.startswith('person:') and not entity_allowed(home,config,data):return False
  for match in ENTITY.finditer(data):
   eid=match.group(1)
   if eid.split('.')[0] in DOMAINS and not entity_allowed(home,config,eid):return False
 return True
