"""Targeted, bounded HA tools shared by voice and text."""
import json
import re
from output import current_output

_model=None

def configure(model):
    global _model
    _model=model

DEFINITIONS=[
 {'name':'home_query','description':'Discover home devices in the default devices view, with compact control_capabilities. Use view=programs for automation names, IDs and enabled states; scope=all lists across rooms with pagination. Disabled/off automations remain discoverable. Enabling is distinct from executing actions. A named automation/script query also returns source-linked program_controls, including the setting helpers it reads and separate internal state helpers. Change the requested program setting rather than substituting a downstream device. Use a device or sensor reading entity_id as the query for current state. Room sensor is a display grouping, so choose the entity_id on the requested reading. Use view=entities explicitly for helpers, diagnostics, capability/control planning or technical inventories. Resolve exact targets and inspect automation/script/scene definitions. Inspect history/traces for actual causes; definitions alone do not prove execution. Use area for explicitly requested other rooms. Refresh after configuration changes.','input_schema':{'type':'object','properties':{'operation':{'type':'string','enum':['discover','areas','resolve','state','inspect','history','traces','services','refresh']},'query':{'type':'string'},'area':{'type':'string'},'domain':{'type':'string'},'limit':{'type':'integer','minimum':1,'maximum':100},'offset':{'type':'integer','minimum':0,'maximum':1000},'view':{'type':'string','enum':['devices','entities','programs']},'scope':{'type':'string','enum':['context','all']},'hours':{'type':'integer','minimum':1,'maximum':48},'run_id':{'type':'string'}},'required':['operation']}},
 {'name':'home_action','description':'Execute one target-specific HA household service. Use entity IDs, settings and actions already supplied in current control_capabilities directly. Do not rediscover the target or query services/state merely to repeat provided information. Query only missing or ambiguous details; the executor validates current targets, parameters and services before sending. Automation turn_on enables future triggers; turn_off disables future triggers, not run-now. Never substitute automation.trigger to enable. Disable preserves current runs unless stop_actions=true is explicitly requested. Returns service completion separately from observed state; never retry uncertain execution. For script variables use data.variables. For a group, explicitly pass the resolved entity set, excluding exceptions.','input_schema':{'type':'object','properties':{'domain':{'type':'string'},'service':{'type':'string'},'entity_ids':{'type':'array','items':{'type':'string'},'minItems':1,'maxItems':40,'uniqueItems':True},'data':{'type':'object'}},'required':['domain','service','entity_ids']}}
]
ALLOWED={'automation':{'turn_on','turn_off','toggle'},'light':{'turn_on','turn_off','toggle'},'switch':{'turn_on','turn_off','toggle'},'fan':{'turn_on','turn_off','toggle','set_percentage','set_preset_mode','oscillate','set_direction'},'climate':{'turn_on','turn_off','set_temperature','set_hvac_mode','set_fan_mode','set_preset_mode','set_swing_mode'},'cover':{'open_cover','close_cover','stop_cover','set_cover_position'},'media_player':{'turn_on','turn_off','volume_set','volume_mute','media_play','media_pause','media_stop','select_source'},'script':{'turn_on'},'scene':{'turn_on'},'input_boolean':{'turn_on','turn_off','toggle'},'input_number':{'set_value'},'input_select':{'select_option'},'number':{'set_value'},'select':{'select_option'},'button':{'press'},'input_button':{'press'}}

async def services_for(model):
    loader=getattr(model,'service_definitions',None)
    return await loader() if loader else await model.ha.request('GET','/api/services')

async def execute(name,inputs):
    m=_model
    if m is None: return json.dumps({'error':'Home Model unavailable'})
    try:
        await m.refresh(force=inputs.get('operation')=='refresh')
        if name=='home_action': return json.dumps(await action(m,inputs),default=str)
        op=inputs['operation']; query=inputs.get('query',''); area=inputs.get('area'); domain=inputs.get('domain')
        if op=='refresh': result={'refreshed':True,'source_gaps':m.errors}
        elif op=='areas': result=list(m.areas.values())
        elif op=='discover':
            await m.refresh_current_states()
            rows=m.search(query,area,domain,1000); offset=inputs.get('offset',0); limit=inputs.get('limit',35)
            if inputs.get('view')=='programs':
                from home_capabilities import automation_catalog
                items=automation_catalog(m,query,area,inputs.get('scope')=='all')
                result={'total':len(items),'offset':offset,'automations':items[offset:offset+limit],'more':len(items)>offset+limit,'semantics':'Enable permits future triggers, not run-now. Disable prevents future triggers; stop_actions separately controls existing runs.'}
            elif inputs.get('view','devices')=='entities':
                result={'total':len(rows),'offset':offset,'entities':rows[offset:offset+limit],'more':len(rows)>offset+limit}
            else:
                from home_presentation import devices
                items=devices(m,rows)
                result={'devices':items[offset:offset+limit],'more':len(items)>offset+limit,
                        'view':'everyday devices; internal controls and diagnostics omitted'}
                from home_controls import relevant_controls
                controls=relevant_controls(m,query,area)
                if controls:result['program_controls']=controls
                from home_capabilities import device_controls
                try:result['control_capabilities']=device_controls(m,rows,await services_for(m))
                except Exception:result['control_capabilities_unavailable']=True
        elif op=='services':
            if not domain and query:domain=m.resolve(query,area)['domain']
            if not domain: raise ValueError('Specify domain')
            services=await services_for(m); result=next((x for x in services if x['domain']==domain),{})
        elif op in ('resolve','state','history','traces','inspect'):
            e=m.resolve(query,area,domain); eid=e['entity_id']
            if op=='resolve': result=e
            elif op=='state': result=await m.ha.get_state(eid)
            elif op=='history':
                provider=getattr(m,'history',None)
                result=provider.projection(await provider.query('history',eid,'last '+str(inputs.get('hours',12))+' hours')) if provider else (await m.ha.get_history(eid,inputs.get('hours',12)))[-100:]
            elif op=='inspect':
                selected=m.programs.get(eid)
                result={'entity':e,'programs':[selected] if selected else m.related([eid],8),'source_gaps':m.errors}
            else:
                p=m.programs.get(eid)
                if not p or p['domain'] not in ('automation','script'): raise ValueError('Resolve an automation or script for traces')
                fields={'domain':p['domain'],'item_id':p['id'] if p['domain']=='automation' else eid.split('.',1)[1]}
                if inputs.get('run_id'): fields['run_id']=inputs['run_id']
                provider=getattr(m,'history',None)
                if provider:
                    operation=p['domain']+'_trace' if 'run_id' in fields else p['domain']+'_runs'
                    result=provider.projection(await provider.query(operation,eid,options={'run_id':fields['run_id']} if 'run_id' in fields else {}))
                else:result=await m.ha.ws_command('trace/get' if 'run_id' in fields else 'trace/list',**fields)
        else: raise ValueError('Unknown query operation')
        encoded=json.dumps(result,default=str)
        if len(encoded)>22000: return json.dumps({'truncated':True,'reason':'Result too large; narrow query or inspect a specific entity/program','preview':encoded[:14000]})
        return encoded
    except Exception as e: return json.dumps({'error':str(e) if isinstance(e,ValueError) else type(e).__name__,'note':'No inference of successful action or actual cause.'})

def action_data(inputs):
    data=dict(inputs.get('data') or {})
    if inputs['domain']=='automation' and inputs['service']=='turn_off':
        data.setdefault('stop_actions',False)
        if not isinstance(data['stop_actions'],bool):raise ValueError('stop_actions must be boolean')
    return data

async def prepare_action(m,inputs):
    domain,service=inputs['domain'],inputs['service']; ids=inputs['entity_ids']; data=action_data(inputs)
    if service not in ALLOWED.get(domain,set()): raise ValueError('Unsupported household service; no action sent')
    if any(k in data for k in ('entity_id','device_id','area_id','target')): raise ValueError('Targets must be explicit entity_ids')
    if any(e not in m.entities or e.split('.')[0]!=domain for e in ids): raise ValueError('Unknown entity or domain mismatch')
    before=await __import__('asyncio').gather(*(m.ha.get_state(e) for e in ids))
    if any(e['state'] in ('unavailable','unknown') for e in before): raise ValueError('A target is unavailable; no action sent')
    for e in before:
        attrs=e.get('attributes',{})
        if domain in ('select','input_select') and service=='select_option':
            if not isinstance(data.get('option'),str) or data['option'] not in attrs.get('options',[]):
                raise ValueError('Unsupported option')
        if domain in ('number','input_number') and service=='set_value':
            import math
            value=data.get('value')
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
                raise ValueError('Value must be a finite number')
            if not attrs.get('min',float('-inf'))<=value<=attrs.get('max',float('inf')):
                raise ValueError('Out of range value')
        for field,options in [('hvac_mode','hvac_modes'),('fan_mode','fan_modes'),('preset_mode','preset_modes'),('swing_mode','swing_modes'),('source','source_list')]:
            if field in data and data[field] not in attrs.get(options,[]): raise ValueError('Unsupported '+field)
        for field,low,high in [('brightness_pct',0,100),('percentage',0,100),('volume_level',0,1),('position',0,100),('temperature',attrs.get('min_temp',7),attrs.get('max_temp',35))]:
            if field in data and (not isinstance(data[field],(int,float)) or not low<=data[field]<=high): raise ValueError('Out of range '+field)
        if domain=='light' and any(k in data for k in ('brightness','brightness_pct')) and attrs.get('supported_color_modes')==['onoff']: raise ValueError('On/off-only light')
    services=await services_for(m)
    definition=next((x.get('services',{}).get(service) for x in services if x['domain']==domain),None)
    if definition is None: raise ValueError('HA service unavailable')
    from home_capabilities import service_fields
    allowed_fields=set(service_fields(definition))
    if set(data)-allowed_fields: raise ValueError('Unknown service fields: '+', '.join(sorted(set(data)-allowed_fields)))
    return before

AFTER_READ_TIMEOUT=1.0

async def action(m,inputs):
    output=current_output.get()
    if output and output.read_only: return {'status':'blocked_read_only'}
    if not m.ha.actions_enabled: return {'status':'blocked_actions_disabled'}
    before=await prepare_action(m,inputs)
    domain,service=inputs['domain'],inputs['service']; ids=inputs['entity_ids']; data=action_data(inputs)
    always_dispatch=set(getattr(m,'household',{}).get('always_dispatch_entities',[]))
    if not data and service in ('turn_on','turn_off') and not always_dispatch.intersection(ids) and all(e['state']==('on' if service=='turn_on' else 'off') for e in before): return {'status':'already_correct','observed':before}
    dispatch_ids=ids
    if domain=='light' and not data and service in ('turn_on','turn_off'):
        expected='on' if service=='turn_on' else 'off'
        dispatch_ids=[e['entity_id'] for e in before if e['state']!=expected or e['entity_id'] in always_dispatch]
    result=await m.ha.call_service(domain,service,{'entity_id':dispatch_ids,**data})
    if result!='ok': return {'status':'unknown_or_failed','service_completed':False,'result':result,'retry_safe':False}
    try:
        after=await __import__('asyncio').wait_for(__import__('asyncio').gather(*(m.ha.get_state(e) for e in ids)),timeout=AFTER_READ_TIMEOUT)
    except __import__('asyncio').TimeoutError:
        # HA can stay busy for seconds after an IR/cloud command (chained automations). Do not hold the reply for it.
        return {'status':'service_completed','service_completed':True,'observed_pending':True,'physical_success_confirmed':False,
                'note':'Command accepted by Home Assistant; the resulting state was not observed in time. Say it was sent. Do not state new settings or claim it worked.'}
    expected={'turn_on':'on','turn_off':'off'}.get(service) if domain in ('light','switch','fan','input_boolean','automation') else None
    return {'status':'observed_expected_state' if expected and all(e['state']==expected for e in after) else 'service_completed','service_completed':True,'observed':after,'physical_success_confirmed':False,'note':'Observed HA state may lag; service completion alone is not proof of physical success.'}
