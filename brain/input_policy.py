"""Persistent, purpose-specific input enrollment. Never authorizes device commands."""
import copy,hashlib,json,os,re,tempfile,time
from pathlib import Path
PURPOSES=('memory','proactivity')
MEMORY_DEVICES={'light','switch','fan','media_player','humidifier','vacuum','climate','water_heater','cover','lock','sensor','binary_sensor','input_boolean','number','input_number'}
SOURCES={
 'devices':('Device states and sensors','Memory collects selected device sessions and numeric observations; proactivity uses its own selected states and sensors.',(True,True)),
 'presence':('Your location / zones','Configured personal home, work and away presence.',(True,True)),
 'conversation':('Your conversations','Durable facts you share; relevant conversation context for proactivity.',(True,True)),
 'history':('Historical evidence','Recorder sessions for learning; relevant past events for proactivity.',(True,True)),
 'automations':('HA automations','Selected automation activity and attributed automation effects.',(False,True)),
 'programs':('HA scripts and scenes','Activity from selected scripts and scenes.',(False,True)),
 'actions':('Sunday action outcomes','Results and anomalies from actions Sunday carried out.',(False,True)),
 'inferences':('Sunday conclusions','Stage evidence-linked conclusions from proactivity as memory.',(True,False)),
 'learned_memory':('Learned memories and expectations','Use selected learned facts and patterns to inform proactivity.',(False,True)),
 'household':('Household profile','Configured household members and room context.',(False,True)),
 'health':('Service health','Persistent provider and connection failures as proactive inputs.',(False,True)),
}
class Conflict(ValueError):pass
class InputPolicy:
 def __init__(self,path,home,config):
  self.path=Path(path);self.home=home;self.config=config;self.data=None;self._revision=None;self._areas_cache={}
  if self.path.exists():
   data=json.loads(self.path.read_text())
   if data.get('version')!=1:raise ValueError('Unsupported input selection version')
   self.data=data
 @property
 def revision(self):
  if self._revision is None:self._revision=hashlib.sha256(json.dumps(self.data,sort_keys=True).encode()).hexdigest()
  return self._revision
 def initialize(self):
  if self.data is not None:return
  from autonomy_scope import entity_allowed
  world=getattr(self.home,'world',None);selected=world.selected() if world else set(self.home.entities)
  sources={k:{p:bool(support[i]) for i,p in enumerate(PURPOSES)} for k,(_,_,support) in SOURCES.items()}
  sources['automations']['proactivity']=bool(self.config.ha_automation_attention_enabled)
  sources['learned_memory']['proactivity']=bool(self.config.memory_read_enabled and self.config.proactive_memory_read_enabled)
  entities={}
  for eid,e in self.home.entities.items():
   if e.get('domain') in {'person','device_tracker','zone'}:continue
   use=entity_allowed(self.home,self.config,eid) and (eid in selected or e.get('domain') in {'automation','script','scene'})
   entities[eid]={p:bool(use and (p!='memory' or e.get('domain') in MEMORY_DEVICES)) for p in PURPOSES}
  self.data={'version':1,'sources':sources,'entities':entities,'updated':time.time()};self._write(self.data)
 def _write(self,data):
  self.path.parent.mkdir(parents=True,exist_ok=True);fd,name=tempfile.mkstemp(dir=self.path.parent,prefix='.input-policy-')
  try:
   with os.fdopen(fd,'w') as out:json.dump(data,out,indent=2);out.write('\n');out.flush();os.fsync(out.fileno())
   os.replace(name,self.path)
  finally:
   if os.path.exists(name):os.unlink(name)
 def source(self,purpose,source):return bool(self.data and self.data['sources'].get(source,{}).get(purpose,False))
 def resident(self,value):
  household=self.home.household;area=household.get('primary_area')
  return str(value).casefold() in {str(p).casefold() for p in [household.get('primary_person'),*household.get('occupants',{}).get(area,[])] if p}
 def entity(self,purpose,eid,seen=None):
  if self.data is None:return False
  if eid.startswith('person:'):return self.source(purpose,'presence') and self.resident(eid[7:])
  for name,cfg in self.home.household.get('people',{}).items():
   if cfg.get('ha_person')==eid:return self.source(purpose,'presence') and self.resident(name)
  e=self.home.entities.get(eid)
  if not e:return False
  program=e.get('domain') in {'automation','script','scene'}
  if not self.source(purpose,('automations' if e.get('domain')=='automation' else 'programs') if program else 'devices'):return False
  if not self.data['entities'].get(eid,{}).get(purpose,False):return False
  if program:
   seen=set(seen or ())
   if eid in seen:return False
   seen.add(eid);linked=self.home.programs.get(eid,{}).get('related_entities',[])
   return bool(linked) and all(self.entity(purpose,x,seen) for x in linked)
  return True
 def allows(self,purpose,data):
  from autonomy_scope import ENTITY,DOMAINS
  if isinstance(data,dict):
   typ=data.get('event_type','');kind=data.get('kind');wake=data.get('wake_type')
   source=None
   if typ=='PRESENCE_CHANGED' or kind=='presence_session':source='presence'
   elif typ.startswith('SUNDAY_ACTION_') or wake=='EXECUTION_ANOMALY':source='actions'
   elif typ=='AUTOMATION_TRIGGERED':source='automations'
   elif typ in {'SCRIPT_EXECUTED','SCENE_ACTIVATED'}:source='programs'
   elif typ in {'DEVICE_STATE_CHANGED','DEVICE_PROPERTY_CHANGED','SENSOR_VALUE_CHANGED','AVAILABILITY_CHANGED'}:source='devices'
   elif wake=='SYSTEM_DEGRADATION' or kind=='HEALTH_CHECK':source='health'
   elif wake=='EXPECTATION_VIOLATED':source='learned_memory'
   if source and not self.source(purpose,source):return False
   if purpose=='memory':
    if (kind in {'device_session','presence_session'} or data.get('source_class')=='HISTORICAL_PATTERN') and not self.source(purpose,'history'):return False
    if data.get('source_class') in {'USER_EXPLICIT','USER_INFERRED'} and not self.source(purpose,'conversation'):return False
    if data.get('source_class') in {'SUNDAY_INFERENCE','EXECUTION_OUTCOME'} and not self.source(purpose,'inferences'):return False
   if purpose=='proactivity' and not self.source(purpose,'automations'):
    provenance=data.get('provenance') or {}
    if any(provenance.get(k,{}).get('type')=='HA_AUTOMATION' for k in ('initiator','via')) or data.get('correlation',{}).get('automation_id'):return False
   if purpose=='proactivity' and not self.source(purpose,'programs'):
    provenance=data.get('provenance') or {}
    if any(provenance.get(k,{}).get('type') in {'HA_SCRIPT','HA_SCENE'} for k in ('initiator','via')) or data.get('correlation',{}).get('script_id'):return False
   for k,v in data.items():
    if k in {'semantic_name','service','domain'}:continue
    if k in {'area','area_id','areas'} and v:
     values=v if isinstance(v,list) else [v]
     normalize=lambda value:re.sub(r'[^a-z0-9]+',' ',str(value).casefold()).strip()
     cache_key=(purpose,self.revision,getattr(self.home,'refreshed',0))
     names=self._areas_cache.get(cache_key)
     if names is None:
      selected_areas={e.get('area_id') for eid,e in self.home.entities.items() if self.entity(purpose,eid)}
      aliases=self.home.household.get('area_names',{});names=set()
      for area in selected_areas:
       if area:names.update(normalize(x) for x in [area,self.home.areas.get(area,{}).get('name',area),*aliases.get(area,[])])
      self._areas_cache={cache_key:names}
     if any(normalize(a) not in names for a in values):return False
     continue
    if not self.allows(purpose,v):return False
   return True
  if isinstance(data,(list,tuple)):return all(self.allows(purpose,v) for v in data)
  if isinstance(data,str):
   if data.startswith('person:') and not self.entity(purpose,data):return False
   return all(self.entity(purpose,m.group(1)) for m in ENTITY.finditer(data) if m.group(1) in self.home.entities or m.group(1).split('.')[0] in DOMAINS)
  return True
 def change(self,body):
  if not isinstance(body,dict) or not isinstance(body.get('changes'),list) or not 1<=len(body['changes'])<=5000:raise ValueError('Supply a non-empty changes list')
  if body.get('revision')!=self.revision:raise Conflict('Inputs changed elsewhere. Reload and try again.')
  data=copy.deepcopy(self.data)
  for change in body['changes']:
   if not isinstance(change,dict) or set(change)!={'kind','id','purpose','enabled'}:raise ValueError('Invalid input change')
   purpose=change['purpose'];key=change['id'];value=change['enabled'];kind=change['kind']
   if purpose not in PURPOSES or type(value) is not bool:raise ValueError('Invalid purpose or value')
   if kind=='source':
    if key not in SOURCES or not SOURCES[key][2][PURPOSES.index(purpose)]:raise ValueError('Unsupported source/purpose')
    data['sources'][key][purpose]=value
   elif kind=='entity':
    if key not in self.home.entities or self.home.entities[key].get('domain') in {'person','device_tracker','zone'}:raise ValueError('Unknown selectable device entity')
    if purpose=='memory' and value and self.home.entities[key].get('domain') not in MEMORY_DEVICES:raise ValueError('This entity does not provide supported session states or numeric observations')
    data['entities'].setdefault(key,{p:False for p in PURPOSES})[purpose]=value
   else:raise ValueError('Unknown input kind')
  if sum(any(v.values()) for v in data['entities'].values())>1500:raise ValueError('Select at most 1500 device entities across both purposes')
  data['updated']=time.time();self._write(data);self.data=data;self._revision=None;self._areas_cache.clear()
 def snapshot(self):
  groups={}
  for eid,e in self.home.entities.items():
   if e.get('domain') in {'person','device_tracker','zone'}:continue
   device=e.get('device_id');key=device or 'entity:'+eid;d=self.home.devices.get(device,{})
   group=groups.setdefault(key,{'id':key,'name':d.get('name_by_user') or d.get('name') or e.get('name') or eid,'area':e.get('area_id'),'area_name':self.home.areas.get(e.get('area_id'),{}).get('name') or e.get('area_id') or 'Unassigned','entities':[]})
   values=self.data['entities'].get(eid,{p:False for p in PURPOSES})
   group['entities'].append({'id':eid,'name':e.get('name') or eid,'domain':e.get('domain'),'state':e.get('state'),'supported':{p:(p!='memory' or e.get('domain') in MEMORY_DEVICES) for p in PURPOSES},'selected':dict(values),'effective':{p:self.entity(p,eid) for p in PURPOSES}})
  return {'revision':self.revision,'updated':self.data['updated'],'primary_area':self.home.household.get('primary_area'),'sources':[{'id':key,'name':name,'description':desc,'supported':{p:support[i] for i,p in enumerate(PURPOSES)},'selected':self.data['sources'][key]} for key,(name,desc,support) in SOURCES.items()],'devices':sorted(groups.values(),key=lambda g:(not any(any(e['selected'].values()) for e in g['entities']),g['area']!=self.home.household.get('primary_area'),g['area_name'],g['name'].casefold())),'features':{'memory_learning':bool(self.config.memory_write_enabled),'proactivity':bool(self.config.proactive_cognition_enabled),'expectations':bool(self.config.expectation_engine_enabled)},'note':'Selections apply to background memory/proactivity. Explicit device commands remain available. New entities start unchecked. Existing memories are retained.'}
