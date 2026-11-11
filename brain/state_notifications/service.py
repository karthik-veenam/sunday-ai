"""Explicit Telegram state rules owned/executed by HA, independent of learned proactivity."""
import asyncio,copy,hashlib,json,math,os,re,time,uuid
from pathlib import Path
PREFIX='sunday_state_notify_'
LEGACY='fridge_any_door_open_telegram'
MARKER='Sunday device-state notification v1\n'
FIELDS={'id','name','entity_ids','attribute','condition','value','from_value','for_seconds','cooldown_seconds','message','enabled'}
DOMAINS=set('light switch fan climate cover media_player humidifier water_heater vacuum lock sensor binary_sensor button number select remote input_boolean input_number input_select person device_tracker zone weather calendar alarm_control_panel'.split())
CONDITIONS={'change','equals','above','below'}
PRIVATE=re.compile('password|token|secret|authorization|api_key|latitude|longitude|gps',re.I)
def digest(data):return hashlib.sha256(json.dumps(data,sort_keys=True,default=str).encode()).hexdigest()
class Conflict(ValueError):pass

def eligible(eid):return bool(re.fullmatch(r'[a-z_]+\.[a-z0-9_]+',eid)) and eid.split('.')[0] in DOMAINS

def normalize(value,home):
 if not isinstance(value,dict) or set(value)-FIELDS:raise ValueError('Unknown notification fields')
 d=copy.deepcopy(value);ids=d.get('entity_ids',[])
 if not isinstance(ids,list) or not 1<=len(ids)<=40 or len(set(ids))!=len(ids):raise ValueError('Choose 1–40 distinct entities')
 for eid in ids:
  if not isinstance(eid,str) or not eligible(eid) or eid not in home.entities:raise ValueError('Choose a known device or sensor entity')
 name=d.get('name') or home.entities[ids[0]].get('name') or ids[0]
 if not isinstance(name,str) or not 1<=len(name.strip())<=140:raise ValueError('Notification name must be 1–140 characters')
 attribute=d.get('attribute') or ''
 if not isinstance(attribute,str) or attribute and (not re.fullmatch(r'[a-zA-Z0-9_]{1,80}',attribute) or PRIVATE.search(attribute)):raise ValueError('Unsupported device attribute')
 condition=d.get('condition','equals')
 if condition not in CONDITIONS:raise ValueError('Choose a valid state condition')
 v=d.get('value','');from_value=d.get('from_value','')
 if not isinstance(v,(str,int,float,bool)) or len(str(v))>160:raise ValueError('State value is too long')
 if not isinstance(from_value,(str,int,float,bool)) or len(str(from_value))>160:raise ValueError('Previous value is too long')
 if condition=='equals' and str(v)=='':raise ValueError('Specify the state to notify about')
 if condition in {'above','below'}:
  try:v=float(v)
  except (TypeError,ValueError):raise ValueError('Threshold must be a number')
  if not math.isfinite(v):raise ValueError('Threshold must be finite')
 elif attribute:
  for key,raw in [('value',v),('from_value',from_value)]:
   if isinstance(raw,str):
    try:parsed=json.loads(raw)
    except ValueError:parsed=raw
    if isinstance(parsed,(str,int,float,bool)):d[key]=parsed
  v=d.get('value',v);from_value=d.get('from_value',from_value)
 for key,maximum in [('for_seconds',86400),('cooldown_seconds',604800)]:
  val=d.get(key,0)
  if type(val) is not int or not 0<=val<=maximum:raise ValueError(key+' is outside the allowed range')
 message=d.get('message','')
 if not isinstance(message,str) or len(message)>1000:raise ValueError('Message must be at most 1000 characters')
 if type(d.get('enabled',True)) is not bool:raise ValueError('Enabled must be true or false')
 return {'id':d.get('id'),'name':name.strip(),'entity_ids':ids,'attribute':attribute,'condition':condition,'value':v,'from_value':from_value,'for_seconds':d.get('for_seconds',0),'cooldown_seconds':d.get('cooldown_seconds',0),'message':message,'enabled':d.get('enabled',True)}

def build(rule):
 attribute=rule['attribute'];condition=rule['condition'];trigger={'trigger':'numeric_state' if condition in {'above','below'} else 'state','entity_id':rule['entity_ids']}
 if attribute:trigger['attribute']=attribute
 if condition in {'above','below'}:trigger[condition]=rule['value']
 elif condition=='equals':trigger['to']=rule['value']
 else:trigger['to']=None  # State changes, not incidental attribute refreshes.
 if rule['from_value']!='' and condition not in {'above','below'}:trigger['from']=rule['from_value']
 if rule['for_seconds']:trigger['for']={'seconds':rule['for_seconds']}
 old="trigger.from_state.attributes.get("+json.dumps(attribute)+")" if attribute else 'trigger.from_state.state'
 new="trigger.to_state.attributes.get("+json.dumps(attribute)+")" if attribute else 'trigger.to_state.state'
 unavailable={'unknown','unavailable'}
 target_explicit=condition=='equals' and str(rule['value']).lower() in unavailable
 previous_explicit=str(rule['from_value']).lower() in unavailable
 guard="{{ trigger.from_state is not none and trigger.to_state is not none"
 guard+=' and '+old+' != '+new
 if not previous_explicit:guard+=" and trigger.from_state.state not in ['unknown','unavailable']"
 if not target_explicit:guard+=" and trigger.to_state.state not in ['unknown','unavailable']"
 guard+=' }}'
 # Custom message is a quoted Jinja literal, never executable template text.
 message=rule['message'] or '{device}: {new} (was {old}) at {time}.'
 rendered='{{ '+json.dumps(message,ensure_ascii=False)+' | replace("{device}", trigger.to_state.name) | replace("{entity}", trigger.entity_id) | replace("{attribute}", '+json.dumps(attribute or 'state')+') | replace("{new}", '+new+' | string) | replace("{old}", '+old+' | string) | replace("{time}", now().strftime("%I:%M:%S %p")) }}'
 actions=[{'action':'script.sunday_announce','data':{'channel':'telegram','message':rendered}}]
 cooldown=rule['cooldown_seconds']
 if cooldown:actions.append({'delay':{'seconds':cooldown}})
 config={'id':rule['id'],'alias':'Sunday notify — '+rule['name'],'description':MARKER+json.dumps(rule,ensure_ascii=False,sort_keys=True),'triggers':[trigger],'conditions':[{'condition':'template','value_template':guard}],'actions':actions,'initial_state':rule['enabled'],'mode':'single' if cooldown else 'queued','max_exceeded':'silent'}
 if not cooldown:config['max']=100
 return config

def decode(config):
 desc=config.get('description','')
 if desc.startswith(MARKER):
  d=json.loads(desc[len(MARKER):]);d['id']=config['id'];return d
 if config.get('id')==LEGACY:
  triggers=config.get('triggers',[]);actions=config.get('actions',[])
  if not (len(actions)==1 and actions[0].get('action')=='script.sunday_announce' and actions[0].get('data',{}).get('channel')=='telegram' and all(t.get('from')=='off' and t.get('to')=='on' for t in triggers)):raise ValueError('Existing fridge automation has unsupported custom changes; leave it untouched')
  ids=[]
  for t in triggers:
   v=t.get('entity_id',[]);ids.extend([v] if isinstance(v,str) else v)
  return {'id':LEGACY,'name':'Fridge / freezer door opened','entity_ids':ids,'attribute':'','condition':'equals','value':'on','from_value':'off','for_seconds':0,'cooldown_seconds':0,'message':'','enabled':True}
 raise ValueError('This automation is not a Sunday notification')

class Service:
 def __init__(self,home,config,path):self.home=home;self.ha=home.ha;self.config=config;self.path=Path(path);self.lock=asyncio.Lock()
 async def snapshot(self,inventory=True):
  await self.home.refresh();states=await self.ha.get_states();owned={}
  for state in states:
   if not state.get('entity_id','').startswith('automation.'):continue
   rid=state.get('attributes',{}).get('id','')
   if isinstance(rid,str) and (rid==LEGACY or rid.startswith(PREFIX)):owned[rid]=state
  records=[]
  for rid,state in sorted(owned.items()):
   try:config=await self.ha.request('GET','/api/config/automation/config/'+rid)
   except RuntimeError as error:
    if 'HTTP 404' in str(error) or 'HTTP 400' in str(error):continue
    raise
   rule=decode(config);rule['enabled']=state['state']=='on';records.append({'rule':rule,'revision':digest([config,state['state']]),'automation_entity':state['entity_id'],'last_triggered':state.get('attributes',{}).get('last_triggered'),'imported_fridge':rid==LEGACY})
  result={'rules':records,'revision':digest([(r['rule']['id'],r['revision']) for r in records]),'telegram_available':bool(self.config.telegram_enabled),'note':'Explicit device notifications run in Home Assistant and arrive through Sunday Telegram. They do not enroll a device in learned proactivity or memory.'}
  if inventory:
   by_id={s['entity_id']:s for s in states}
   result['entities']=[{'id':eid,'name':e.get('name') or eid,'area':self.home.areas.get(e.get('area_id'),{}).get('name') or e.get('area_id') or 'Unassigned','state':by_id.get(eid,{}).get('state',e.get('state')),'attributes':sorted(k for k in e.get('attributes',{}) if not PRIVATE.search(k))} for eid,e in sorted(self.home.entities.items()) if eligible(eid)]
  return result
 def backup(self,rid,config,actor):
  directory=self.path/'notification_backups';directory.mkdir(parents=True,exist_ok=True)
  p=directory/(rid+'-'+str(time.time_ns())+'.json');fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
  with os.fdopen(fd,'w') as f:json.dump({'actor':actor,'config':config},f,indent=2)
 async def wait_for(self,rid,enabled):
  for _ in range(30):
   states=await self.ha.get_states();found=next((s for s in states if s.get('attributes',{}).get('id')==rid),None)
   if (enabled is None and found is None) or (found is not None and enabled is not None and (found['state']=='on')==enabled):return True
   await asyncio.sleep(.2)
  return False
 async def manage(self,body,actor='dashboard',require_revision=True):
  if not isinstance(body,dict):raise ValueError('Expected a notification change')
  if not self.ha.actions_enabled:raise ValueError('Configuration changes are disabled in this runtime')
  async with self.lock:
   snap=await self.snapshot(False)
   if require_revision and body.get('revision')!=snap['revision']:raise Conflict('Notifications changed elsewhere. Reload and retry.')
   action=body.get('action');patch=body.get('rule') or {};rid=body.get('id') or patch.get('id')
   existing=next((r for r in snap['rules'] if r['rule']['id']==rid),None)
   if rid and (not re.fullmatch(r'[a-zA-Z0-9_]+',rid) or not existing):raise ValueError('Unknown Sunday notification rule')
   if action not in {'save','toggle','remove'}:raise ValueError('Unknown notification action')
   if action!='save' and not existing:raise ValueError('Choose an existing rule')
   if action=='toggle' and type(body.get('enabled')) is not bool:raise ValueError('Enabled must be true or false')
   original=await self.ha.request('GET','/api/config/automation/config/'+rid) if existing else None
   if action=='remove':
    self.backup(rid,original,actor)
    await self.ha.request('POST','/api/services/automation/turn_off',{'entity_id':existing['automation_entity'],'stop_actions':True})
    await self.ha.request('DELETE','/api/config/automation/config/'+rid)
   else:
    data={**(existing['rule'] if existing else {}),**patch}
    if action=='toggle':data['enabled']=body['enabled']
    data['id']=rid or PREFIX+uuid.uuid4().hex[:12];rule=normalize(data,self.home)
    if original:self.backup(rid,original,actor)
    # Pure enable/disable preserves the exact imported fridge wording and trigger configuration.
    if action=='toggle' and rid==LEGACY:
     config=copy.deepcopy(original);config['initial_state']=rule['enabled'];config['description']=MARKER+json.dumps(rule,ensure_ascii=False,sort_keys=True)
    else:config=build(rule)
    if existing:await self.ha.request('POST','/api/services/automation/turn_off',{'entity_id':existing['automation_entity'],'stop_actions':True})
    try:await self.ha.request('POST','/api/config/automation/config/'+rule['id'],config)
    except Exception:
     # Restore the previous enabled state after a rejected configuration update; no device commands.
     if existing and existing['rule']['enabled']:await self.ha.request('POST','/api/services/automation/turn_on',{'entity_id':existing['automation_entity']})
     raise
   confirmed=await self.wait_for(rid if action=='remove' else rule['id'],None if action=='remove' else rule['enabled'])
   result=await self.snapshot();result['status']=('removed' if action=='remove' else 'saved') if confirmed else 'pending_confirmation';return result
