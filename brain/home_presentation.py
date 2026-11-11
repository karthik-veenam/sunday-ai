"""Human-scale home evidence. Technical discovery remains an explicit view."""
import re

ACTUATORS = {'climate','fan','light','switch','cover','media_player','humidifier','vacuum','water_heater','lock','assist_satellite'}
HELPER_PLATFORMS = {'template','generic_thermostat','generic_hygrostat','group','statistics','derivative','integration','threshold','utility_meter'}
ENVIRONMENT = {'temperature','humidity','pressure','atmospheric_pressure','illuminance','carbon_dioxide','pm25','air_quality','moisture'}
KINDS = {'climate':'air conditioner','fan':'fan','light':'light','switch':'outlet','cover':'cover','media_player':'media device','humidifier':'humidifier','assist_satellite':'voice assistant','vacuum':'vacuum','water_heater':'water heater','lock':'lock'}

def ordinary(e):
    return not (e.get('entity_category') in ('config','diagnostic') or e.get('hidden_by') or e.get('disabled_by') or (e.get('platform') in HELPER_PLATFORMS and not e.get('everyday_device')))

def friendly(e, model):
    name=str(e['name'])
    aid=e.get('area_id'); area=model.areas.get(aid,{})
    device=model.devices.get(e.get('device_id'),{})
    prefixes=[device.get('name_by_user') or device.get('name'),area.get('name')]+model.household.get('area_names',{}).get(aid,[])
    for prefix in sorted((p for p in prefixes if p),key=len,reverse=True):
        candidate=re.sub(r'^'+re.escape(prefix)+r'(?:[\s:_-]+)', '', name,flags=re.I).strip()
        if candidate:
            if re.fullmatch(r'(?:socket|outlet|relay|channel|media player)(?:\s+\d+)?',candidate,re.I):
                name=(device.get('name_by_user') or device.get('name') or name).strip()
            else:name=candidate
    return name

def devices(model, rows):
    """Keep separate actuator endpoints on multi-channel devices; attach sensors.

    One HA device can be a whole hub or power strip. Never conflate its separate
    controllable endpoints, and never count its battery/firmware as extra gadgets.
    """
    groups={}
    assistant_devices={e.get('device_id') for e in rows if e['domain']=='assist_satellite'}-{None}
    actuator_devices={e.get('device_id') for e in rows if e['domain'] in ACTUATORS and ordinary(e)}-{None}
    triggered_devices=set()
    for p in model.programs.values():
        source=p.get('source',{})
        def collect(v):
            if isinstance(v,dict):
                for k,x in v.items():
                    if k=='device_id':triggered_devices.update(x if isinstance(x,list) else [x])
                    collect(x)
            elif isinstance(v,list):
                for x in v:collect(x)
        collect(source.get('triggers',source.get('trigger',[])))
    for e in rows:
        domain=e['domain'];did=e.get('device_id');attrs=e.get('attributes',{});device=model.devices.get(did,{})
        if device.get('entry_type')=='service':continue
        if did in assistant_devices:
            if domain=='assist_satellite':
                groups[did]={'name':device.get('name_by_user') or device.get('name') or e['name'],'kind':'voice assistant','state':e['state'],'available':e['available'],'entity_ids':[e['entity_id']]}
            continue
        if ordinary(e) and domain in ACTUATORS:
            name=friendly(e,model);kind=KINDS[domain]
            if domain=='switch' and attrs.get('device_class')!='outlet':kind='switch'
            groups[e['entity_id']]={'name':name,'kind':kind,'state':e['state'],'available':e['available'],'entity_ids':[e['entity_id']]}
        elif ordinary(e) and domain in ('sensor','binary_sensor') and (attrs.get('device_class') in ENVIRONMENT or domain=='binary_sensor'):
            if did in actuator_devices:continue
            key=did or e['entity_id'];g=groups.setdefault(key,{'name':device.get('name_by_user') or ('Room sensor' if did else friendly(e,model)),'kind':'sensor','available':False,'readings':[],'entity_ids':[]})
            g['available'] |= e['available'];g['readings'].append({'entity_id':e['entity_id'],'name':friendly(e,model),'measurement':attrs.get('device_class'),'value':e['state'],'unit':attrs.get('unit_of_measurement')});g['entity_ids'].append(e['entity_id'])
    # Physical buttons often expose only battery/diagnostic state entities;
    # their presence comes from an actual automation device trigger, not a name rule.
    for did in triggered_devices-actuator_devices:
        peers=[e for e in rows if e.get('device_id')==did]
        if not peers or did in groups:continue
        d=model.devices.get(did,{})
        groups[did]={'name':d.get('name_by_user') or d.get('name') or 'Button','kind':'control device','available':any(e['available'] for e in peers),'entity_ids':[e['entity_id'] for e in peers]}
    return list(groups.values())
