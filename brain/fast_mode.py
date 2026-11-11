"""Conservative deterministic grammar and planner. No provider imports or calls."""
from dataclasses import dataclass, field, asdict, replace
from db_schema import ensure_v1
import re, json, time, sqlite3, statistics, asyncio, os
from difflib import SequenceMatcher
from home_model import norm, safe
from home_presentation import ordinary, friendly
from presence import contextual_text

@dataclass
class Clause:
    operation: str
    targets: list = field(default_factory=list)
    target_category: str = ''
    scope: str = 'primary_context'
    property: str = ''
    value: float | str | None = None
    unit: str = ''
    relative_direction: str = ''
    relative_amount: float | None = None
    quantifier: str = 'NAMED'
    include: list = field(default_factory=list)
    exclude: list = field(default_factory=list)
    exception_actions: list = field(default_factory=list)

@dataclass
class Intent:
    intent: str
    operations: list

class Miss(ValueError): pass
class Ambiguous(Miss):
    def __init__(self, phrase, candidates): self.phrase,self.candidates=phrase,candidates

CATEGORIES={'devices':'everything','device':'everything','light':'light','lights':'light','fan':'fan','fans':'fan','ac':'climate','air conditioner':'climate','air conditioners':'climate','outlet':'switch','outlets':'switch'}

def clean(text):
    t=text.lower().strip().rstrip('.!?').strip()
    t=re.sub(r"^(?:hey sunday[, ]+|sunday[, ]+)", '', t)
    t=re.sub(r'^(?:can|could|would) you\s+', '', t)
    t=re.sub(r'\b(?:please|just)\b',' ',t)
    t=re.sub(r'\s+for me$', '', t)
    return re.sub(r'\s+',' ',t).strip()

def parse(text):
    t=clean(text).replace('but not ', 'excluding ')
    # A complete OFF instruction may be followed by a non-conditional reason.
    # Parse the instruction normally so negation, exceptions and ambiguity keep
    # their original meaning. Never strip a negative command or an ON conflict.
    reason=re.fullmatch(r"(.+?) (?:because )?i (?:don't|do not) need (?:it|them)(?: anymore| any more| now)?",t)
    if reason:
        try:
            command=parse(reason[1])
            if command.operations and all(c.operation=='OFF' for c in command.operations):return command
        except Miss:pass
    if re.search(r'\b(?:notify|notifications?|alerts?|reminders?|proactivity|proactiveness|memory|memories)\b',t): raise Miss('assistant configuration is not a device command')
    if re.search(r"\b(?:not|don't|never|unless|if|when|before|after|then|why|because|maybe|or)\b",t): raise Miss('unsupported composition')
    query=re.fullmatch(r"is (?:the )?(.+?) (on|off|running)",t)
    if query:return Intent('query',[Clause('QUERY',[query[1]],property='state',value=query[2])])
    query=re.fullmatch(r"(?:what's|what is) (?:the )?(.+?) (temperature|brightness)",t)
    if query:return Intent('query',[Clause('QUERY',[query[1]],property=query[2])])
    exclusion=[]; exceptions=[]
    lead=re.fullmatch(r'leave (.+?) alone and (.+)',t)
    if lead: exclusion=[lead[1]];t=lead[2]
    exc=re.fullmatch(r'(.+?) but leave (.+?) (on|off)',t)
    if exc: t=exc[1];exceptions=[{'operation':exc[3].upper(),'target':exc[2]}]
    else:
        exc=re.fullmatch(r'(.+?) (?:except|excluding|apart from|but) (.+?)(?: (on|off))?',t)
        if exc:
            t=exc[1]+(' '+exc[3] if exc[3] else '');exclusion += [exc[2]]
    def one(s):
        op=None; prop='';val=None;unit=''
        # Action concepts and grammatical positions are independent of device names.
        s=re.sub(r'^(?:turn|switch|shut|power)\s+', '',s)
        match=re.fullmatch(r'(on|off|toggle|open|close) (.+)',s)
        if match: op,s=match[1].upper(),match[2]
        if op is None:
            match=re.fullmatch(r'(.+?) (on|off)',s)
            if match: s,op=match[1],match[2].upper()
        if op is None:
            match=re.fullmatch(r'(kill|start|activate|run) (.+)',s)
            if match:
                op,s={'kill':'OFF','start':'ON','activate':'ON','run':'ON'}[match[1]],match[2]
                if match[1] in ('activate','run'):prop='explicit_program'
        if op is None:
            match=re.fullmatch(r'(?:set |make |dim )?(.+?) (?:to )?(-?\d+(?:\.\d+)?)\s*(%|percent|degrees|°c|°f)?',s)
            if match:
                s,val,unit=match[1],float(match[2]),match[3] or '';op='SET'
                unit={'%':'percent','degrees':'degrees'}.get(unit,unit)
                p=re.fullmatch(r'(.+?) (brightness|temperature|speed|volume)',s)
                if p:s,prop=p[1],p[2]
        if op is None:
            match=re.fullmatch(r'(?:set )?(.+?) (?:hvac )?mode (?:to )?([a-z_]+)',s)
            if match:s,op,prop,val=match[1],'SET','hvac_mode',match[2]
        if op is None:
            match=re.fullmatch(r'(increase|raise|lower|decrease|reduce) (.+)',s)
            if match: op,s=('INCREASE' if match[1] in ('increase','raise') else 'DECREASE'),match[2]
            else:
                match=re.fullmatch(r'make (.+?) (faster|slower)',s)
                if match: s,op=match[1],('INCREASE' if match[2]=='faster' else 'DECREASE');prop='speed'
            if op:
                p=re.fullmatch(r'(.+?) (brightness|speed|volume|temperature)',s)
                if p:s,prop=p[1],p[2]
        if not op: raise Miss('unsupported grammar')
        scope='primary_context'
        scoped=re.fullmatch(r'(.+?) (?:in (?:the )?(.+)|here)',s)
        if scoped:
            s=scoped[1];scope=scoped[2] or 'primary_context'
            if scope in ('our room','my room','this room'):scope='primary_context'
        s=re.sub(r'\bthe\b','',s);s=re.sub(r'\s+',' ',s).strip()
        group=re.fullmatch(r'(?:(?:all|every|other) )?(.+)',s)
        category=CATEGORIES.get(group[1],'')
        quant='ALL' if re.match(r'^(?:all |every |other |everything\b)',s) else 'NAMED'
        if s=='everything':category='everything';quant='ALL'
        targets=[] if quant=='ALL' else [x.strip() for x in re.split(r',\s*| and ',s) if x.strip()]
        if quant=='ALL' and not category: raise Miss('unsupported group')
        if not targets and not category: raise Miss('missing target')
        if any(re.search(r'\b(on|off|turn|set|switch|make|kill|leave)\b',x) for x in targets):raise Miss('unsupported composition')
        return Clause(op,targets,category if quant=='ALL' else '',scope,prop,val,unit,
                      op if op in ('INCREASE','DECREASE') else '',None,quant)
    try: clauses=[one(t)]
    except Miss:
        if ' and ' not in t: raise
        clauses=[one(x) for x in t.split(' and ')]
    if (exclusion or exceptions) and (len(clauses)!=1 or clauses[0].quantifier!='ALL'):raise Miss('unsupported composition')
    clauses[0].exclude=exclusion;clauses[0].exception_actions=exceptions
    return Intent('control',clauses)

class Planner:
    threshold=.94
    margin=.12
    def __init__(self, model):self.m=model;self.candidates=[]
    def universe(self,scope):
        area=self.m.household['primary_area']
        if scope!='primary_context':
            matches=[k for k,v in self.m.areas.items() if norm(scope) in [norm(v.get('name','')),*map(norm,v.get('aliases',[]))]]
            if len(matches)!=1:raise Miss('missing Home Model metadata: area')
            area=matches[0]
        assistants={e.get('device_id') for e in self.m.entities.values() if e['domain']=='assist_satellite'}-{None}
        return [e for e in self.m.entities.values() if e.get('device_id') not in assistants and getattr(self.m,'devices',{}).get(e.get('device_id'),{}).get('entry_type')!='service' and e.get('area_id')==area and ordinary(e) and e['domain'] in ('light','switch','fan','climate','media_player','cover','script','scene')]
    def resolve(self,phrase,rows,binding=None):
        phrase=norm(re.sub(r'^the ', '',phrase)); ranked=[]
        for e in rows:
            names=[e['name'],*e.get('aliases',[])]
            if hasattr(self.m,'devices'):names.append(friendly(e,self.m))
            area=self.m.areas.get(e.get('area_id'),{})
            for name in list(names):
                for prefix in [area.get('name',''),*area.get('aliases',[])]:
                    if prefix and norm(name).startswith(norm(prefix)+' '):names.append(norm(name)[len(norm(prefix))+1:])
            score=max([1. if phrase==norm(n) else .94 if set(phrase.split())==set(norm(n).split()) else .95 if set(phrase.split()) and set(phrase.split())<=set(norm(n).split()) else min(.89,SequenceMatcher(None,phrase,norm(n)).ratio()) for n in names]+[0])
            if phrase in CATEGORIES and e['domain']==CATEGORIES[phrase]:score=max(score,.96)
            ranked.append({'entity_id':e['entity_id'],'name':e['name'],'score':round(score,4)})
        ranked.sort(key=lambda x:x['score'],reverse=True);self.candidates.append({'phrase':phrase,'candidates':ranked[:5]})
        chosen=(binding or {}).get(phrase) if isinstance(binding,dict) else binding
        if chosen and chosen in [x['entity_id'] for x in ranked if x['score']>=self.threshold]:return self.m.entities[chosen]
        if not ranked or ranked[0]['score']<self.threshold:raise Miss('low resolution confidence')
        close=[x for x in ranked if ranked[0]['score']-x['score']<self.margin]
        if len(close)>1:raise Ambiguous(phrase,close[:5])
        return self.m.entities[ranked[0]['entity_id']]
    def action(self,e,c):
        d=e['domain'];a=e.get('attributes',{});op=c.operation;data={}
        if not e.get('available',e.get('state') not in ('unknown','unavailable')):raise ValueError(e['name']+' is unavailable.')
        if op=='TOGGLE':
            if e['state'] not in ('on','off') or a.get('assumed_state'):raise Miss('unsafe toggle')
            op='OFF' if e['state']=='on' else 'ON'
        if op in ('ON','OFF'):
            if d in ('script','scene') and not c.property=='explicit_program':raise Miss('scripts/scenes require explicit run or activate')
            if d=='script':
                fields=getattr(self.m,'programs',{}).get(e['entity_id'],{}).get('source',{}).get('fields',{})
                if any(v.get('required') for v in fields.values() if isinstance(v,dict)):raise Miss('script requires variables')
            if d not in ('light','switch','fan','climate','media_player','script','scene') or (op=='OFF' and d in ('script','scene')):raise Miss('unsupported property')
            service='turn_'+op.lower()
        elif op=='QUERY':
            if c.property=='temperature' and not (d=='sensor' and a.get('device_class')=='temperature' or d=='climate' and isinstance(a.get('current_temperature'),(int,float))):raise Miss('unsupported property: temperature reading')
            if c.property=='brightness' and not (d=='light' and isinstance(a.get('brightness'),(int,float))):raise Miss('missing brightness state')
            if c.property=='state' and d in ('script','scene'):raise Miss('unsupported state query')
            return {'query':e['entity_id'],'property':c.property,'expected':c.value}
        elif op in ('INCREASE','DECREASE'):
            if a.get('assumed_state'):raise Miss('relative adjustment requires reliable state')
            sign=1 if op=='INCREASE' else -1
            if d=='fan' and c.property in ('','speed') and isinstance(a.get('percentage'),(float,int)):
                return self.action(e,replace(c,operation='SET',property='speed',value=a['percentage']+sign*a.get('percentage_step',10),unit='percent'))
            if d=='light' and c.property=='brightness' and isinstance(a.get('brightness'),(float,int)):
                return self.action(e,replace(c,operation='SET',value=round(a['brightness']*100/255)+sign*10,unit='percent'))
            if d=='media_player' and c.property=='volume' and isinstance(a.get('volume_level'),(float,int)):
                return self.action(e,replace(c,operation='SET',value=round(a['volume_level']*100)+sign*5,unit='percent'))
            raise Miss('unsupported property: relative adjustment')
        elif op=='SET':
            v=c.value
            if d=='climate' and c.property=='hvac_mode':
                if v not in a.get('hvac_modes',[]):raise ValueError('Unsupported climate mode.')
                return {'domain':d,'service':'set_hvac_mode','entity_ids':[e['entity_id']],'data':{'hvac_mode':v}}
            if d=='climate' and c.property in ('','temperature') and c.unit!='percent':
                if not int(a.get('supported_features',0))&1:raise Miss('missing Home Model metadata: target temperature support')
                if c.unit in ('°f','°c') and c.unit.upper()!=a.get('temperature_unit','°C').upper():raise Miss('unsupported unit')
                service='set_temperature';data={'temperature':v}
            elif d=='light' and c.property in ('','brightness') and c.unit=='percent':
                if not a.get('supported_color_modes') or a['supported_color_modes']==['onoff']:raise ValueError('That light does not report brightness support.')
                service='turn_on';data={'brightness_pct':v}
            elif d=='fan' and c.property in ('','speed') and c.unit=='percent':
                if not int(a.get('supported_features',0))&1:raise ValueError('That fan does not report speed support.')
                service='set_percentage';data={'percentage':v}
            elif d=='media_player' and c.property=='volume' and c.unit=='percent':
                if not int(a.get('supported_features',0))&4:raise ValueError('That player does not report volume support.')
                service='volume_set';data={'volume_level':v/100}
            else:raise Miss('unsupported property')
        elif op in ('OPEN','CLOSE'):
            if d!='cover' or a.get('device_class') not in ('blind','shade','curtain','awning'):raise Miss('sensitive or unknown cover')
            if not int(a.get('supported_features',0)) & (1 if op=='OPEN' else 2):raise Miss('missing cover capability metadata')
            service=op.lower()+'_cover'
        else:raise Miss('relative adjustments deferred')
        return {'domain':d,'service':service,'entity_ids':[e['entity_id']],'data':data}
    async def plan(self,ir,binding=None):
        from home_tools import prepare_action
        plan={};excluded=[];unavailable=[]
        for c in ir.operations:
            rows=self.universe(c.scope)
            if ir.intent=='query' and c.property=='temperature' and c.targets==['room']:
                rows=[e for e in self.m.entities.values() if e.get('area_id')==self.m.household['primary_area'] and e['domain']=='sensor' and e.get('attributes',{}).get('device_class')=='temperature' and ordinary(e)]
                if len(rows)!=1:raise Miss('ambiguous room temperature sensor')
                targets=rows
            else:targets=[self.resolve(x,rows,binding) for x in c.targets] if c.quantifier=='NAMED' else [e for e in rows if e['domain']==c.target_category or c.target_category=='everything' and e['domain'] in ('light','switch','fan','climate','media_player')]
            if not targets:raise Miss('missing Home Model metadata: empty group')
            omit={self.resolve(x,targets,binding)['entity_id'] for x in c.exclude}
            exceptions=[(self.resolve(x['target'],targets,binding),x['operation']) for x in c.exception_actions]
            for e in targets:
                eid=e['entity_id']
                if eid in omit:excluded.append(eid);continue
                if c.quantifier=='ALL' and not e.get('available',True):unavailable.append(eid);continue
                action=self.action(e,c)
                if eid in plan and plan[eid]!=action:raise Miss('conflicting actions')
                plan[eid]=action
            for e,op in exceptions:plan[e['entity_id']]=self.action(e,Clause(op))
        if not plan:raise ValueError('No available targets.')
        actions=list(plan.values())
        if len(actions)>40:raise Miss('unsupported composition: more than 40 targets')
        # Same validator as normal Sunday, for the entire plan BEFORE any writes.
        for action in actions:
            if 'query' not in action:await prepare_action(self.m,action)
        return actions,excluded,unavailable

FAST_SCHEMA = ('create table attempts(id text primary key, data text)',
               'create table pending(conversation text primary key, expires real, input text, candidates text)')

class FastMode:
    def __init__(self,path):
        self.db=sqlite3.connect(path);os.chmod(path,0o600)
        try:
            ensure_v1(self.db, FAST_SCHEMA, 'Fast Mode')
            self.db.execute('pragma journal_mode=WAL')
        except BaseException:
            self.db.close()
            raise
    def save(self,record):
        # Never persist arbitrary credential-bearing input or tool results.
        record=json.loads(json.dumps({k:v for k,v in record.items() if k!='historical_evidence'},default=str))
        def redact(v):
            if isinstance(v,str) and (re.search(r'(?i)bearer|api[_ -]?key|password|token|secret|api.telegram.org',v) or re.search(r'[A-Za-z0-9_-]{40,}',v)):return '[redacted]'
            if isinstance(v,dict):return {k:redact(x) for k,x in v.items()}
            if isinstance(v,list):return [redact(x) for x in v]
            return v
        self.db.execute('insert or replace into attempts values(?,?)',(record['id'],json.dumps(redact(record))));self.db.commit()
    async def attempt(self,model,text,conversation,request_id,channel,execute=False,read_only=False,stage="simple",presence=None,contextual_ambiguity=False):
        start=time.perf_counter();rec={'id':request_id,'channel':channel,'input':text,'decision':'FALLBACK','mode':'execute' if execute else 'shadow','model_calls':0,'plan':[],'reason':'','stage_ms':{}}
        rec['presence']={'person':presence.person,'presence':presence.presence} if presence else None
        planner=Planner(model);binding={};original=text
        try:
            pending=self.db.execute('select expires,input,candidates from pending where conversation=?',(conversation,)).fetchone()
            self.db.execute('delete from pending where conversation=?',(conversation,));self.db.commit()
            if pending and pending[0]>time.time() and execute:
                saved=json.loads(pending[1]) if pending[1].startswith('{') else {'text':pending[1],'bindings':{}}
                answer=clean(text);cs=json.loads(pending[2]);matches=[]
                for i,c in enumerate(cs):
                    names=[norm(c['name']),str(i+1),['first','second','third','fourth','fifth'][i]]
                    if answer.endswith(' one'):answer=answer[:-4]
                    if norm(answer) in names or norm(answer) in ('left','right') and norm(answer) in norm(c['name']).split():matches.append(c)
                if len(matches)==1:
                    text=saved['text'];binding=saved.get('bindings',{})
                    phrase=matches[0].get('phrase')
                    if phrase:binding[phrase]=matches[0]['entity_id']
                    else:binding=matches[0]['entity_id']
            try:parse_text=contextual_text(text,presence)
            except ValueError as error:raise Miss(str(error)) from error
            history=getattr(model,'history',None)
            hplan=history.plan_text(parse_text) if history else None
            if hplan:
                evidence=await history.query(**asdict(hplan));reply=history.fast_reply(evidence,hplan)
                rec.update(historical=True,historical_evidence=evidence,ir=asdict(hplan),decision='FALLBACK',reason='historical reasoning or coverage requires normal Sunday')
                if execute and reply:rec.update(handled=True,reply=reply,decision='EXECUTE',reason='',response_class='historical_fact')
                rec['stage_ms']['total']=(time.perf_counter()-start)*1000;self.save(rec);return rec
            t=time.perf_counter();ir=parse(parse_text);rec['ir']=asdict(ir);rec['stage_ms']['parse']=(time.perf_counter()-t)*1000
            t=time.perf_counter();await model.refresh()
            world=getattr(model,'world',None)
            world_ids=world.relevant_ids(parse_text) if world and ir.intent=='query' else []
            if world_ids:
                states=await world.fresh_entities(world_ids)
                for eid,state in states.items():
                    if eid in model.entities:model.entities[eid].update(state=state['state'],attributes=state['attributes'],available=state['state'] not in ('unknown','unavailable'))
                world.store.count('fast_mode_world_reads');world.store.commit();rec['world_read']=True
            else:await model.refresh_current_states()
            rec['stage_ms']['metadata']=(time.perf_counter()-t)*1000
            t=time.perf_counter();plan,excluded,unavailable=await planner.plan(ir,binding)
            rec.update(plan=plan,exclude=excluded,unavailable=unavailable,confidence=min((x['candidates'][0]['score'] for x in planner.candidates),default=1),decision='EXECUTE',capability_validation='passed')
            rec['stage_ms']['resolve_validate']=(time.perf_counter()-t)*1000
            # Stage C is a separate gate. Advanced plans remain shadow/fallback.
            eligible=len(plan)==1 and len(ir.operations)==1 and ir.operations[0].quantifier=='NAMED' and ir.operations[0].operation in ('ON','OFF','SET') and not read_only
            if stage in ('groups','all') and ir.intent=='control' and all(c.operation not in ('INCREASE','DECREASE') for c in ir.operations):eligible=not read_only
            if stage=='all' and ir.intent=='control':eligible=not read_only
            if execute and stage=='all' and ir.intent=='query':
                answers=[]
                for item in plan:
                    e=model.entities[item['query']];a=e.get('attributes',{});prop=item['property']
                    if prop=='brightness':
                        if not isinstance(a.get('brightness'),(float,int)):raise Miss('missing brightness state')
                        answers.append(f"{e['name']} is at {round(a['brightness']*100/255)}%.")
                    elif prop=='temperature':
                        value=a.get('current_temperature') if e['domain']=='climate' else e['state']
                        unit=a.get('temperature_unit','°C') if e['domain']=='climate' else a.get('unit_of_measurement','')
                        label=e['name'] if e['domain']=='climate' else 'The room'
                        answers.append(f"{label} is {value}{unit}.")
                    else:
                        label='HA reports ' if a.get('assumed_state') else ''
                        if e['domain']=='climate' and item.get('expected')=='running':
                            if a.get('hvac_action'):answers.append(f"{label}{e['name']} reports {a['hvac_action']}.")
                            else:answers.append(f"{label}{e['name']} is set to {e['state']}.")
                        else:answers.append(f"{label}{e['name']} is {e['state']}.")
                rec.update(handled=True,reply=' '.join(answers),response_class='state')
            elif execute and eligible:
                from output import current_output
                import home_tools
                output=current_output.get();results=[]
                output.execution_path='fast_mode'
                rec.update(handled=True,response_class='execution_unknown',reply='I couldn’t confirm that action. I won’t retry it automatically.',execution={'status':'dispatching'})
                rec['candidates']=planner.candidates;rec['stage_ms']['total']=(time.perf_counter()-start)*1000
                self.save(rec)
                dispatch_plan=plan
                # A single light-group intent has no sequencing dependency. HA can
                # dispatch independent lights together; keep mixed/ordered plans serial.
                if (len(ir.operations)==1 and ir.operations[0].quantifier=='ALL' and len(plan)>1
                        and plan[0].get('domain')=='light' and plan[0].get('service') in ('turn_on','turn_off')
                        and all(a.get('domain')=='light' and a.get('service')==plan[0]['service'] and not a.get('data') for a in plan)):
                    dispatch_plan=[{**plan[0],'entity_ids':list(dict.fromkeys(e for a in plan for e in a['entity_ids']))}]
                rec['dispatch_plan']=dispatch_plan
                execution_started=time.perf_counter()
                for action in dispatch_plan:
                    result=await output.tool('home_action',action,lambda:home_tools.execute('home_action',action))
                    results.append(json.loads(result))
                    if results[-1].get('status') not in ('already_correct','observed_expected_state','service_completed'):break
                rec['stage_ms']['execution']=(time.perf_counter()-execution_started)*1000
                rec['execution']=results
                ok=all(x.get('status') in ('already_correct','observed_expected_state','service_completed') for x in results)
                reply=('It’s already set that way.' if all(x.get('status')=='already_correct' for x in results) else 'Done.' if all(x.get('status')=='observed_expected_state' for x in results) else 'Command sent.') if ok else 'I couldn’t confirm that action. I won’t retry it automatically.'
                if ok and len(plan)==1 and ir.operations[0].operation=='SET':
                    data=plan[0]['data']
                    if 'temperature' in data:reply=f"Command sent: {data['temperature']:g}°{model.entities[plan[0]['entity_ids'][0]].get('attributes',{}).get('temperature_unit','°C').replace('°','')}."
                    elif any(k in data for k in ('brightness_pct','percentage')):
                        value=data.get('brightness_pct',data.get('percentage'));reply=f"Command sent: {value:g}%."
                if ok and unavailable:reply+=' Some devices are unavailable.'
                rec['response_class']='success' if ok else 'execution_failure';rec['handled']=True;rec['reply']=reply
            elif execute:rec.update(decision='FALLBACK',reason='outside initial production scope')
        except Ambiguous as e:
            rec.update(decision='CLARIFY',reason='ambiguous entity',clarification=e.candidates)
            if contextual_ambiguity:
                rec.update(decision='FALLBACK',reason='contextual ambiguity needs conversation and memory')
            elif execute and not read_only:
                self.db.execute('insert or replace into pending values(?,?,?,?)',(conversation,time.time()+120,json.dumps({'text':text,'bindings':binding if isinstance(binding,dict) else {}}),json.dumps([{**c,'phrase':e.phrase} for c in e.candidates])));self.db.commit()
                rec.update(handled=True,reply='Which one — '+ ' or '.join(x['name'] for x in e.candidates)+'?',response_class='clarification')
        except Miss as e:rec.update(decision='FALLBACK',reason=str(e))
        except ValueError as e:
            rec.update(decision='REJECT',reason=str(e))
            if execute and not read_only:rec.update(handled=True,reply=str(e),response_class='rejection')
        except asyncio.CancelledError:
            rec.update(cancelled=True,candidates=planner.candidates)
            rec['stage_ms']['total']=(time.perf_counter()-start)*1000
            self.save(rec)
            raise
        except Exception as e:
            if rec.get('handled'):rec.update(reason='execution outcome unknown: '+type(e).__name__)
            else:rec.update(decision='FALLBACK',reason='metadata/validation unavailable: '+type(e).__name__)
        rec['fallback_category']=classify(rec.get('reason',''),original) if rec['decision']=='FALLBACK' else None
        rec['candidates']=planner.candidates;rec['stage_ms']['total']=(time.perf_counter()-start)*1000
        self.save(rec);return rec
    def metrics(self):
        rows=[json.loads(r[0]) for r in self.db.execute('select data from attempts')];n=len(rows)
        lat=sorted(r['stage_ms']['total'] for r in rows)
        hits=sum(bool(r.get('handled')) and r.get('response_class') not in ('execution_failure','execution_unknown') for r in rows)
        reasons=[r.get('reason','') for r in rows]
        return {'fast_path_attempts':n,'fast_path_hits':hits,'hit_rate':hits/n if n else 0,
            'decisions':{d:sum(r['decision']==d for r in rows) for d in ('EXECUTE','CLARIFY','FALLBACK','REJECT')},
            'parse_failures':sum('grammar' in x or 'composition' in x for x in reasons),
            'resolution_failures':sum('resolution' in x or 'ambiguous entity' in x for x in reasons),
            'capability_failures':sum(r['decision']=='REJECT' or 'property' in r.get('reason','') for r in rows),
            'execution_failures':sum(r.get('response_class') in ('execution_failure','execution_unknown') for r in rows),
            'median_ms':statistics.median(lat) if lat else None,
            'p95_ms':lat[min(len(lat)-1,int(len(lat)*.95))] if lat else None,
            'model_calls_avoided':sum(bool(r.get('handled')) and r.get('model_calls')==0 for r in rows),
            'false_fast_rate':'unmeasured until labelled real-command shadow review',
            'fallback_corpus':[{'input':r['input'],'reason':r['reason']} for r in rows if r['decision']=='FALLBACK']}


def classify(reason,text):
    if any(w in clean(text).split() for w in ('cozy','movie','best','nicer','gloomy','bright')):return 'semantic request'
    if 'ambiguous' in reason:return 'ambiguous entity'
    if 'composition' in reason:return 'unsupported composition'
    if 'grammar' in reason:return 'unsupported grammar'
    if 'confidence' in reason:return 'low resolution confidence'
    if 'property' in reason or 'relative' in reason:return 'unsupported property'
    if 'metadata' in reason:return 'missing Home Model metadata'
    return 'other'

def compare_shadow(record, events):
    """Compare only observed home_action calls. Absence requires human review."""
    if record.get('mode')!='shadow' or record.get('decision')!='EXECUTE':return {'status':'not_applicable'}
    if any('query' in a for a in record['plan']):return {'status':'query_requires_review'}
    def canonical(actions):
        return sorted((a['domain'],a['service'],e,json.dumps(a.get('data') or {},sort_keys=True)) for a in actions for e in a.get('entity_ids',[]))
    actual=[]
    for e in events:
        if e['name']!='home_action':continue
        try:result=json.loads(e['result'])
        except (ValueError,TypeError):continue
        if result.get('status') in ('service_completed','observed_expected_state','already_correct','unknown_or_failed'):actual.append(e['arguments'])
    if not actual:return {'status':'no_action_evidence_requires_review'}
    expected=canonical(record['plan']);observed=canonical(actual)
    return {'status':'match' if expected==observed else 'difference_requires_review','expected':expected,'observed':observed}
