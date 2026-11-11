"""Bounded, source-linked settings for explicitly named household programs.

Every association comes from registered names/aliases and actual program entity
references. No room, automation name or device ID is a built-in routing rule.
"""
import re
import json

ENTITY=re.compile(r'\b[a-z_]+\.[a-z0-9_]+\b')
HELPERS={'input_number','input_boolean','input_select'}
ATTRS={'min','max','step','unit_of_measurement','options','temperature','min_temp','max_temp','target_temp_step','hvac_modes'}

def tokens(value):return set(re.findall(r'[a-z0-9]+',str(value).lower()))

def refs(value):
    if isinstance(value,dict):return set().union(*(refs(x) for x in value.values())) if value else set()
    if isinstance(value,list):return set().union(*(refs(x) for x in value)) if value else set()
    return set(ENTITY.findall(value)) if isinstance(value,str) else set()

def written_targets(value):
    result=set()
    if isinstance(value,dict):
        if value.get('action') or value.get('service'):
            result |= refs(value.get('target',{}))
            result |= refs(value.get('data',{}).get('entity_id',[])) if isinstance(value.get('data'),dict) else set()
            result |= refs(value.get('entity_id',[]))
        for child in value.values():result |= written_targets(child)
    elif isinstance(value,list):
        for child in value:result |= written_targets(child)
    return result

def reference_paths(value,eid,path=''):
    result=[]
    if isinstance(value,dict):
        for k,v in value.items():result.extend(reference_paths(v,eid,path+'.'+str(k) if path else str(k)))
    elif isinstance(value,list):
        for i,v in enumerate(value):result.extend(reference_paths(v,eid,path+'.'+str(i)))
    elif isinstance(value,str) and eid in refs(value):result.append({'path':path,'expression':value[:250]})
    return result

def relevant_controls(model,query,area=None):
    if not query.strip() or not getattr(model,'programs',None):return {}
    q=tokens(query)
    areas=model.area_ids(query) if area is None else model.area_ids(query,area)
    area_words=set()
    for aid,a in model.areas.items():
        for name in (aid,a.get('name',''),*a.get('aliases',[]),*model.household.get('area_names',{}).get(aid,[])):
            area_words |= tokens(name)
    area_words |= {'room','our','my','the','automation','script','scene'}
    matched=[]
    for p in model.programs.values():
        e=model.entities.get(p['entity_id'],{})
        if e.get('disabled_by'):continue
        names=[p.get('name',''),*e.get('aliases',[])]
        cores=[tokens(name)-area_words for name in names]
        exact=any(tokens(name) and tokens(name)<=q for name in names)
        overlap=max((len(core) for core in cores if core and core<=q),default=0)
        # A setting may be requested without naming its owning automation.
        # Match registered helper names, then retain only source-linked inputs.
        source=p.get('source',{});written=written_targets(source)
        setting_match=False
        for eid in p.get('related_entities',[]):
            helper=model.entities.get(eid,{})
            if helper.get('disabled_by') or helper.get('domain') not in HELPERS or eid in written:continue
            for name in (helper.get('name',''),*helper.get('aliases',[])):
                core=tokens(name)-area_words
                if len(core)>=2 and core<=q and reference_paths(source,eid):setting_match=True
        if not exact and overlap<2 and not setting_match:continue
        if e.get('area_id') not in areas and not exact:continue
        matched.append((overlap,p))
    matched.sort(key=lambda pair:(-pair[0],pair[1]['entity_id']))
    result=[]
    for _,p in matched[:4]:
        source=p.get('source',{});written=written_targets(source);settings=[];internal=[]
        for eid in p.get('related_entities',[]):
            e=model.entities.get(eid)
            if not e or e.get('disabled_by'):continue
            domain=e['domain'];attrs=e.get('attributes',{})
            if domain not in HELPERS and not (domain=='climate' and e.get('platform') in {'generic_thermostat','generic_hygrostat','template'}):continue
            item={'entity_id':eid,'name':e['name'],'domain':domain,'state':e['state'],'available':e['available'],
                  'attributes':{k:v for k,v in attrs.items() if k in ATTRS},'references':reference_paths(source,eid)[:3]}
            if eid in written:
                internal.append({'entity_id':eid,'name':e['name'],'state':e['state'],'role':'written by the program; not presumed a user setting'})
            else:settings.append(item)
        if settings or internal:
            result.append({'entity_id':p['entity_id'],'name':p.get('name'),
                           'description':str(source.get('description',''))[:600],'enabled':p.get('enabled'),
                           'settings':settings[:10],'state_helpers':internal[:6]})
    # Keep context bounded even for programs with many helper references.
    for program in result:
        if len(json.dumps(program,default=str))>7000:
            for setting in program['settings']:setting['references']=setting['references'][:1]
    while len(result)>1 and len(json.dumps(result,default=str))>7500:result.pop()
    return {'programs':result,'meaning':'Source-linked program settings are distinct from downstream physical-device settings. Use the helper the program actually reads for the requested setting; inspect the source if its role is unclear.'} if result else {}
