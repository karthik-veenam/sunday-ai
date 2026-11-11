"""Trusted factual pre-send checks. UNKNOWN is never evidence of absence."""
from .store import digest
from datetime import datetime
from zoneinfo import ZoneInfo

def keys(facts):
    return {(f.get('subject',{}).get('id'),f.get('property')):f for f in facts if isinstance(f,dict)}

def quiet(service):
    h=datetime.fromtimestamp(service.store.clock(),ZoneInfo(service.config.timezone)).hour;a=service.config.quiet_start;b=service.config.quiet_end
    return a<=h<b if a<b else h>=a or h<b

async def recheck(service,row):
    if not service.in_scope(row.get('data',{})):return {'eligible':False,'reason':'Outside primary room scope'}
    d=row['data'];wake=d['wake'];s=service.store;kind=wake.get('wake_type');p=d['proposal']
    presence='UNKNOWN'
    if service.presence and service.source_allowed('presence'):
        try:presence=(await service.presence.get_presence(force=True)).presence
        except Exception:pass
    if wake.get('diagnostic')=='telegram-release-v1':
        evidence=s.get('audit',wake['evidence_refs'][0])
        okay=wake.get('authority')=='USER_EXPLICIT' and evidence and evidence['data'].get('kind')=='explicit_telegram_smoke_authorization' and p['message']=='Sunday proactive Telegram delivery test ✅'
        return {'eligible':bool(okay),'reason':'Explicit fixed diagnostic' if okay else 'Invalid diagnostic authority','presence':presence,'context':{'fresh':bool(okay),'healthy':bool(okay),'quiet':quiet(service)}}
    if kind=='COMMITMENT_DUE':
        refs=wake.get('commitment_refs',[]);source=s.get('temporal',refs[0]) if refs else None
        okay=bool(source and source['state'] in ('DONE','PROCESSING') and source['data'].get('user_request_id') and source['data'].get('authority')=='USER_EXPLICIT' and wake.get('authority')=='USER_EXPLICIT' and (source['data'].get('commitment') or {}).get('message')==p['message'])
        return {'eligible':okay,'reason':'Explicit commitment remains current' if okay else 'Commitment cancelled or changed','presence':presence,'context':{'fresh':okay,'healthy':okay,'quiet':quiet(service)},'source_ref':source['id'] if source else None}
    if kind=='EXTERNAL_CONDITION' and wake.get('authority')=='USER_EXPLICIT' and wake.get('commitment_refs'):
        source=s.get('temporal',wake['commitment_refs'][0])
        watch=next((s.get('watches',ref) for ref in wake['evidence_refs'] if ref.startswith('watches:')),None)
        if not source or source['state'] not in ('PENDING','DONE') or source['data'].get('authority')!='USER_EXPLICIT' or not source['data'].get('user_request_id') or (source['data'].get('commitment') or {}).get('message')!=p['message'] or not watch or watch['state']!='CLOSED' or watch['data'].get('closure_reason')!='condition met':
            return {'eligible':False,'reason':'Explicit conditional commitment is cancelled, changed or unsupported'}
        if not service.world or not service.world.connected:return {'eligible':False,'reason':'Conditional fact provider unavailable'}
        w=watch['data'];await service.world.fresh_entities([w['subject']],max_age=0);fact=service.world.store.get(w['subject'],w['trigger_conditions']['property'])
        if not fact or fact.get('availability') in ('unknown','unavailable') or fact.get('value')!=w['trigger_conditions']['equals']:return {'eligible':False,'reason':'Explicit condition no longer holds'}
        return {'eligible':True,'reason':'Explicit conditional commitment and key fact revalidated','presence':presence,'context':{'fresh':True,'healthy':True,'quiet':quiet(service)},'source_ref':source['id'],'fact_guard':[{'subject':w['subject'],'property':w['trigger_conditions']['property'],'value':fact['value']}]}
    if kind=='EXPECTATION_VIOLATED':
        source=next((s.get('temporal',ref) for ref in wake.get('evidence_refs',[]) if ref.startswith('temporal:')),None)
        if not source or source['state']!='VIOLATED':return {'eligible':False,'reason':'Expectation resolved/cancelled'}
        e=source['data'];m=service.expectations.memories(e['source_memory_id'])
        if not m or not service.expectations.eligible(m) or m['updated_at']!=e['source_memory_version']:return {'eligible':False,'reason':'Expectation memory policy/version changed'}
        evidence=await service.recheck(e)
        if not evidence.get('fresh') or not evidence.get('healthy') or evidence.get('presence')=='UNKNOWN':return {'eligible':False,'reason':'Expectation evidence UNKNOWN or stale'}
        if e['expectation_kind']=='arrival':
            supported=evidence.get('arrival_occurred') is False and evidence.get('complete_history') is True and evidence.get('presence') in ('HOME','AWAY')
        else:supported=evidence.get('active') is True and evidence.get('session_start')==e.get('session_start')
        if not supported:return {'eligible':False,'reason':'Expectation is satisfied or violation no longer established','expectation_evidence':evidence}
        source_now=s.get('temporal',source['id']);m=service.expectations.memories(e['source_memory_id'])
        if source_now['state']!='VIOLATED' or not m or not service.expectations.eligible(m) or m['updated_at']!=e['source_memory_version']:return {'eligible':False,'reason':'Expectation changed during revalidation'}
        # Presence's final direct read prevents a slow history query from retaining an old HOME result.
        if e['expectation_kind']=='arrival':
            try:presence=(await service.presence.get_presence(person=e['subject'],force=True)).presence
            except Exception:presence='UNKNOWN'
            if presence not in ('HOME','AWAY'):return {'eligible':False,'reason':'Office arrival or UNKNOWN before dispatch'}
        context={'fresh':True,'healthy':True,'quiet':quiet(service)}
        return {'eligible':True,'reason':'Expectation violation independently revalidated','context':context,'presence':presence,'source_ref':source['id'],'memory_version':m['updated_at'],'expectation_evidence':evidence}
    if not service.world or not service.world.connected:return {'eligible':False,'reason':'Factual provider unavailable'}
    entities=wake.get('entities',[])[:12]
    original=keys(d.get('gate_context',{}).get('world',[]))
    if entities:await service.world.fresh_entities(entities,max_age=0)
    context=await service.context(subject=wake.get('subject'),wake=wake)
    if getattr(service,'situations',None):
        incident=s.get('incidents',wake['incident_id'])
        if incident and incident['data'].get('schema_version')==2:
            assessments=incident['data'].get('assessments',[])
            if not assessments or assessments[-1]['outcome'] not in {'TELL','URGENT'} or incident['data'].get('notification_state')=='DELIVERED':return {'eligible':False,'reason':'V2 attention no longer justifies an interruption'}
    current=keys(context.get('world',[]))
    for key,fact in original.items():
        now=current.get(key)
        if not now or now.get('availability') in ('unknown','unavailable') or now.get('value')!=fact.get('value'):return {'eligible':False,'reason':'Key factual condition changed or became UNKNOWN'}
    # Recovered executions or health providers no longer justify an older anomaly notice.
    for ref in wake.get('evidence_refs',[]):
        table=ref.split(':',1)[0]
        if table in {'executions','health','watches','temporal'}:
            source=s.get(table,ref)
            if not source or source['state'] in {'CLEAN_CONFIRMED_HA_STATE','HEALTHY','RESOLVED','CANCELLED','SATISFIED'}:return {'eligible':False,'reason':'Underlying source resolved or cancelled'}
    if not original and not any(str(ref).startswith(('executions:','health:','watches:','temporal:')) for ref in wake.get('evidence_refs',[])):
        return {'eligible':False,'reason':'No independently recheckable key evidence'}
    guard=[{'subject':f.get('subject',{}).get('id'),'property':f.get('property'),'value':f.get('value'),'availability':f.get('availability')} for f in context.get('world',[])[:16]]
    return {'eligible':True,'reason':'Relevant facts refreshed and unchanged','context':{k:context[k] for k in ('fresh','healthy','quiet')},'presence':presence,'fact_guard':guard}

def final_check(service,row,check):
    """No awaits between this final race guard and durable DISPATCHING."""
    if not service.in_scope(row.get('data',{})):return False
    source=service.store.get('temporal',check.get('source_ref','')) if check.get('source_ref') else None
    if check.get('source_ref') and not source:return False
    if source:
        kind=row['data']['wake'].get('wake_type')
        if kind=='COMMITMENT_DUE':
            if source['state'] not in ('DONE','PROCESSING') or (source['data'].get('commitment') or {}).get('message')!=row['data']['proposal']['message']:return False
        elif kind=='EXTERNAL_CONDITION':
            if source['state'] not in ('PENDING','DONE') or (source['data'].get('commitment') or {}).get('message')!=row['data']['proposal']['message']:return False
        elif kind=='EXPECTATION_VIOLATED':
            e=source['data'];m=service.expectations.memories(e['source_memory_id'])
            if source['state']!='VIOLATED' or not m or not service.expectations.eligible(m) or m['updated_at']!=check.get('memory_version'):return False
    for fact in check.get('fact_guard',[]):
        if not service.world or not service.world.connected:return False
        now=service.world.store.get(fact['subject'],fact['property'])
        if not now or now.get('availability') in ('unknown','unavailable') or now.get('value')!=fact['value']:return False
    return True

def invalidate_event(service,event):
    # This synchronous hook also covers an arrival while async rechecks are in flight.
    change=event.get('change',{});subject=event.get('subject',{}).get('id','')
    if change.get('new')!='OFFICE':return
    for row in service.store.rows('proposals',('GATE_APPROVED',),100):
        wake=row['data'].get('wake',{})
        if wake.get('wake_type')!='EXPECTATION_VIOLATED':continue
        person=str(wake.get('subject','')).casefold();target=subject.removeprefix('person:').casefold()
        if person==target:
            incident=service.store.get('incidents',wake['incident_id'])
            if incident:service.store.change('incidents',incident['id'],state='RESOLVED',data=incident['data']|{'resolved_by':event['event_id'],'reason':'Office arrival before delivery'})
