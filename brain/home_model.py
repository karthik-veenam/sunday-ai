"""Refreshable HA evidence index. No learned memory or model-generated facts."""
import asyncio
import hashlib
import json
import re
import time
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent / "vendor"))
import yaml

ENTITY = re.compile(r'\b[a-z_]+\.[a-z0-9_]+\b')
DOMAINS = {'lights': 'light', 'light': 'light', 'fan': 'fan', 'fans': 'fan', 'ac': 'climate', 'air conditioner': 'climate', 'temperature': 'sensor', 'humidity': 'sensor'}

def norm(s):
    return re.sub(r'[^a-z0-9]+', ' ', str(s).lower()).strip()

def snapshot_relevant(query,model):
    q=norm(query)
    if re.search(r'\b(?:capital|write|email|translate|summarize|poem|joke|essay|code)\b',q):return False
    if re.search(r'\b(?:room|bedroom|area|house|home|automations?|routines?|helpers?|scripts?|scenes?|programs?|devices|lights|light|fan|fans|ac|thermostat|temperature|humidity|heating|cooling|brightness|curtains|blinds|tv)\b',q):return True
    for entity in model.entities.values():
        for name in (entity.get('name',''),*entity.get('aliases',[])):
            n=norm(name)
            if len(n)>=2 and ' '+n+' ' in ' '+q+' ':return True
    return False

def safe(value):
    """Do not return authentication/configuration secrets embedded in programs."""
    if isinstance(value, dict):
        return {k: ('[redacted]' if any(x in str(k).lower() for x in ('token','password','secret','authorization','api_key')) else safe(v)) for k,v in value.items()}
    if isinstance(value, list): return [safe(v) for v in value]
    if isinstance(value, str):
        if 'Bearer ' in value or 'api.telegram.org/bot' in value: return '[redacted]'
    return value

class HomeModel:
    def __init__(self, ha, household, config_path=None, ttl=60):
        self.ha, self.household = ha, household
        self.config_path = Path(config_path) if config_path else None
        self.ttl, self.refreshed = ttl, 0
        self.areas, self.devices, self.entities, self.programs = {}, {}, {}, {}
        self.errors=[]
        self.services_cache=None;self.services_loaded_at=0.0
        self.lock=asyncio.Lock()

    def load_programs(self):
        if not self.config_path: return {}, ['HA YAML source path not configured']
        root=self.config_path.resolve()
        class Loader(yaml.SafeLoader): pass
        def include(loader,node):
            path=(Path(loader.name).parent/loader.construct_scalar(node)).resolve()
            if not path.is_relative_to(root): raise ValueError('Include outside HA source root')
            with path.open() as f: return yaml.load(f,Loader=Loader)
        Loader.add_constructor('!include',include)
        Loader.add_constructor('!secret',lambda l,n:'[redacted]')
        programs={}; errors=[]
        for domain, filename in [('automation','automations.yaml'),('script','scripts.yaml'),('scene','scenes.yaml')]:
            try:
                with (root/filename).open() as f: data=yaml.load(f,Loader=Loader) or {}
                rows=enumerate(data) if isinstance(data,list) else data.items()
                for key,source in rows:
                    if not isinstance(source,dict): continue
                    identifier=str(source.get('id',key)); eid=domain+'.'+str(key)
                    if domain in ('automation','scene'):
                        eid=next((e for e,v in self.entities.items() if v['domain']==domain and str(v['attributes'].get('id'))==identifier), next((e for e,v in self.entities.items() if v['domain']==domain and v['name']==source.get('alias',source.get('name'))),eid))
                    if eid in programs:
                        errors.append('Duplicate program entity mapping: '+eid)
                        eid=domain+'.source_'+identifier
                    text=json.dumps(source,default=str)
                    programs[eid]={'entity_id':eid,'domain':domain,'id':identifier,'name':source.get('alias',source.get('name',key)), 'source':safe(source),'fingerprint':hashlib.sha256(text.encode()).hexdigest(), 'related_entities':sorted(set(ENTITY.findall(text))), 'enabled':self.entities.get(eid,{}).get('state'),'source_path':filename}
            except Exception as e: errors.append(filename+': '+type(e).__name__)
        return programs,errors

    async def refresh(self, force=False):
        if force:self.services_cache=None
        if not force and time.monotonic()-self.refreshed < self.ttl: return
        async with self.lock:
            if not force and time.monotonic()-self.refreshed < self.ttl: return
            areas,devices,registry,states=await asyncio.gather(self.ha.ws_command('config/area_registry/list'),self.ha.ws_command('config/device_registry/list'),self.ha.ws_command('config/entity_registry/list'),self.ha.get_states())
            primary=self.household['primary_area']; common=self.household.get('common_areas',[])
            self.areas={a['area_id'] if 'area_id' in a else a['id']:{**a,'relevance':'primary' if a.get('area_id',a.get('id'))==primary else 'common' if a.get('area_id',a.get('id')) in common else 'other','occupants':self.household.get('occupants',{}).get(a.get('area_id',a.get('id')),[])} for a in areas}
            self.devices={d['id']:d for d in devices}
            reg={e['entity_id']:e for e in registry}; entities={}
            # HA can expose the registry before integrations publish their first
            # state. Keep enabled metadata so the next state refresh sees them.
            seen={state['entity_id'] for state in states}
            states=list(states)+[{'entity_id':eid,'state':'unavailable','attributes':
                {'friendly_name':r.get('name') or r.get('original_name') or eid,
                 'device_class':r.get('device_class') or r.get('original_device_class')},
                'last_changed':None,'last_updated':None}
                for eid,r in reg.items() if eid not in seen and not r.get('disabled_by')]
            for state in states:
                eid=state['entity_id']; r=reg.get(eid,{}); device=self.devices.get(r.get('device_id'),{})
                aliases=list(r.get('aliases') or [])+self.household.get('entity_aliases',{}).get(eid,[])
                entities[eid]={'entity_id':eid,'name':r.get('name') or state.get('attributes',{}).get('friendly_name',eid),'aliases':aliases,'domain':eid.split('.')[0],'entity_category':r.get('entity_category'),'platform':r.get('platform'),'everyday_device':eid in self.household.get('everyday_device_entities',[]),'hidden_by':r.get('hidden_by'),'disabled_by':r.get('disabled_by'),'device_id':r.get('device_id'),'area_id':r.get('area_id') or device.get('area_id') or self.household.get('device_area_names',{}).get(device.get('name_by_user') or device.get('name')) or self.household.get('entity_area_overrides',{}).get(eid),'state':state['state'],'attributes':safe(state.get('attributes',{})), 'available':state['state'] not in ('unavailable','unknown'),'last_changed':state.get('last_changed'),'last_updated':state.get('last_updated'),'context':state.get('context')}
            self.entities=entities
            self.programs,self.errors=await asyncio.to_thread(self.load_programs)
            # Resolve device/area triggers and transitive script/scene references.
            for p in self.programs.values():
                def refs(v):
                    if isinstance(v,dict):
                        for k,x in v.items():
                            if k in ('device_id','area_id'):
                                vals=x if isinstance(x,list) else [x]
                                p['related_entities'].extend(e for e,s in entities.items() if s.get(k) in vals)
                            refs(x)
                    elif isinstance(v,list):
                        for x in v: refs(x)
                refs(p['source']); p['related_entities']=sorted(set(p['related_entities']))
            for eid,p in self.programs.items():
                linked_areas={entities[e]['area_id'] for e in p['related_entities'] if e in entities and entities[e]['area_id']}
                if eid in entities and not entities[eid]['area_id'] and self.household['primary_area'] in linked_areas:
                    entities[eid]['area_id']=self.household['primary_area']
            self.refreshed=time.monotonic()

    async def refresh_current_states(self):
        # Configuration/discovery may be cached; a present-tense answer must use
        # a new state snapshot, including changes since the last conversation.
        states={s['entity_id']:s for s in await self.ha.get_states()}
        for eid,e in self.entities.items():
            observed=states.get(eid)
            if observed is None:
                e.update(state='unavailable',available=False)
                continue
            if eid in self.programs:self.programs[eid]['enabled']=observed['state']
            e.update(state=observed['state'],available=observed['state'] not in ('unknown','unavailable'),
                     attributes=safe(observed.get('attributes',{})),last_changed=observed.get('last_changed'),last_updated=observed.get('last_updated'),context=observed.get('context'))

        self.states_observed_at=time.time()

    def area_ids(self,query, area=None):
        q=' '+norm(query)+' '; found=[]
        for aid,a in self.areas.items():
            names=[aid,a['name']]+a.get('aliases',[])+self.household.get('area_names',{}).get(aid,[])
            if any(' '+norm(n)+' ' in q for n in names) or area and norm(area) in [norm(n) for n in names]: found.append(aid)
        if area and not found: raise ValueError('Unknown area')
        return found or [self.household['primary_area']]

    def search(self, query='', area=None, domain=None, limit=20):
        areas=self.area_ids(query,area); q=norm(query); tokens=set(q.split())
        domain=domain or next((d for word,d in DOMAINS.items() if ' '+word+' ' in ' '+q+' '),None)
        ranked=[]
        for e in self.entities.values():
            if domain and e['domain']!=domain: continue
            names=[e['entity_id'],e['name']]+e['aliases']
            exact=any(norm(n)==q for n in names)
            meaningful=tokens-{'what','which','do','you','have','in','our','my','the','room','devices','right','now','is','are','running','everything'}
            score=max((len(meaningful & set(norm(n).split())) for n in names),default=0)
            # Explicit exact names/IDs can target outside normal room context.
            if e['area_id'] not in areas and not exact: continue
            if score or not q or domain or area or any(w in tokens for w in ('devices','running','everything','room')):
                ranked.append((100 if exact else score,e))
        ranked.sort(key=lambda x:(-x[0], x[1]['domain'] not in ('light','fan','climate','switch','cover','media_player'), x[1]['entity_id']))
        return [e for _,e in ranked[:limit]]

    def resolve(self, target, area=None, domain=None):
        q=norm(target.removeprefix('the ')); rows=self.search(target,area,domain,100)
        exact=[e for e in rows if q in [norm(n) for n in [e['entity_id'],e['name']]+e['aliases']]]
        if len(exact)==1: return exact[0]
        # Domain-only natural requests are resolved in the primary/named area.
        if q in DOMAINS: rows=[e for e in rows if e['domain']==DOMAINS[q]]
        elif not exact:
            rows=[e for e in rows if set(q.split()) <= set(norm(e['name']).split()) or any(q==norm(a) for a in e['aliases'])]
        else: rows=exact
        if len(rows)!=1: raise ValueError(json.dumps({'error':'ambiguous' if rows else 'not_found','candidates':[{'entity_id':e['entity_id'],'name':e['name'],'area_id':e['area_id']} for e in rows[:12]]}))
        return rows[0]

    def related(self, ids, limit=12):
        selected=set(ids)
        devices={self.entities[e].get('device_id') for e in selected if e in self.entities} - {None}
        selected.update(e for e,v in self.entities.items() if v.get('device_id') in devices)
        out=[p for eid,p in self.programs.items() if eid in selected or selected.intersection(p['related_entities'])][:limit]
        for _ in range(4):
            called={e for p in out for e in p['related_entities'] if e.startswith(('script.','scene.'))}
            extra=[p for eid,p in self.programs.items() if eid in called and p not in out]
            if not extra: break
            out.extend(extra[:max(0,limit-len(out))])
        return out[:limit]

    async def service_definitions(self):
        if self.services_cache is None or time.monotonic()-self.services_loaded_at>30:
            self.services_cache=await self.ha.request('GET','/api/services')
            self.services_loaded_at=time.monotonic()
        return self.services_cache

    def room_snapshot(self):
        import math
        from home_presentation import ordinary
        primary=self.household['primary_area']
        data={'area_id':primary,'name':(self.household.get('area_names',{}).get(primary) or [primary])[0],
              'observed_at':getattr(self,'states_observed_at',None),'environment':{}}
        for kind in ('temperature','humidity','illuminance'):
            candidates=[e for e in self.entities.values() if e.get('area_id')==primary and e['domain']=='sensor' and ordinary(e) and e.get('attributes',{}).get('device_class')==kind]
            readings=[]
            for e in candidates:
                try:value=float(e['state'])
                except (ValueError,TypeError):continue
                if not e.get('available') or not math.isfinite(value):continue
                readings.append({'value':value,'unit':e['attributes'].get('unit_of_measurement'),
                    'entity_id':e['entity_id'],'last_updated':e.get('last_updated')})
            status='available' if len(readings)==1 else 'multiple_sources' if readings else 'temporarily_unavailable' if candidates else 'not_configured'
            data['environment'][kind]={'status':status,'readings':readings}
        data['instruction']='Use these current room readings directly for basic questions. Temperature is measured air temperature, not an AC target. Do not search again when one available reading answers the question. Unavailable/multiple sources require care; never invent a value.'
        return data

    async def context(self,text):
        try:
            await self.refresh()
            from home_presentation import devices
            await self.refresh_current_states()
            rows=self.search(text,limit=150)
            facts={k:v for k,v in self.household.items() if k in ('primary_person','relationships')}
            primary=self.household['primary_area']
            room_names=self.household.get('area_names',{}).get(primary,[])
            facts['shared_room']={'name':room_names[0] if room_names else self.areas.get(primary,{}).get('name',primary),'residents':self.household.get('occupants',{}).get(primary,[])}
            data={'household':facts,'room_snapshot':self.room_snapshot(),'relevant_devices':devices(self,rows)[:35],
                  'related_programs':[{'entity_id':p['entity_id'],'name':p['name']} for p in self.related([e['entity_id'] for e in rows[:12]],6)]}
            from home_capabilities import device_controls,automation_catalog
            try:data['control_capabilities']=device_controls(self,rows,await self.service_definitions())
            except Exception:data['control_capabilities_unavailable']=True
            for program in data['related_programs']:
                ent=self.entities.get(program['entity_id'],{})
                if ent.get('domain')=='automation':
                    program.update(state=ent.get('state'),actions={'enable':'automation.turn_on','disable':'automation.turn_off'})
            if re.search(r'\b(?:automations?|routines?)\b',norm(text)):
                catalog=automation_catalog(self,query=text if re.search(r'\b(?:enable|disable|turn|switch)\b',norm(text)) else '',all_areas=bool(re.search(r'\ball\b',norm(text))))
                data['automation_catalog']=catalog[:30];data['automation_catalog_more']=len(catalog)>30
            encoded=json.dumps(data,default=str)
            while len(encoded)>20000 and data.get('control_capabilities'):
                data['control_capabilities'].pop();data['control_capabilities_truncated']=True;encoded=json.dumps(data,default=str)
            while len(encoded)>24000 and data.get('automation_catalog'):
                data['automation_catalog'].pop();data['automation_catalog_more']=True;encoded=json.dumps(data,default=str)
            while len(encoded)>24000 and data['relevant_devices']:
                data['relevant_devices'].pop(); data['truncated']=True; encoded=json.dumps(data,default=str)
            return encoded
        except Exception as e: return json.dumps({'household':self.household,'home_evidence_unavailable':type(e).__name__})
