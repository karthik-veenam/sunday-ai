"""Read-only display model. No HA actions, model calls, or persistent transcript history."""
import math,re,time,unicodedata,html
from datetime import datetime
FIELDS={'title':39,'line1':127,'line2':95,'room1':95,'room2':95,'timer1':63,'media1':95,'media2':95,'sleep1':63,'sleep2':127,'alert1':63,'alert2':95}
def clean(value,n=127):
 s=re.sub(r'<[^>]*>',' ',html.unescape(str(value or '')));s=re.sub(r'(?i)(?:Bearer\s+\S+|(?:sk[-_]|gsk[-_])[A-Za-z0-9_-]{16,})','[private]',s)
 s=unicodedata.normalize('NFKD',s).encode('ascii','ignore').decode();s=s.replace('"','').replace('\\','');return ' '.join(s.split())[:n]
def speech_text(value,partial=False):
 text=str(value or '')
 if any(ord(c)>127 and c.isalpha() for c in unicodedata.normalize('NFKD',text)):return 'Speech detected' if partial else 'Transcript received'
 return clean(text) or ('Speech detected' if partial else 'Transcript received')
def numeric(v):
 try:x=float(v);return x if math.isfinite(x) else None
 except (ValueError,TypeError):return None
def epoch(v):
 try:return datetime.fromisoformat(v.replace('Z','+00:00')).timestamp()
 except (ValueError,TypeError,AttributeError):return 0
class Projection:
 def __init__(self,clock=time.time):
  self.clock=clock;self.states={};self.members=set();self.registry={};self.ha_ready=False;self.overlay=None;self.overlay_until=0;self.alert=('','');self.sleep={};self.jobs=[];self.timer_lengths={};self.timer_warned=set();self.voice_phase='idle'
 def show(self,title,text,seconds=7,priority=1):
  if self.overlay and self.clock()<self.overlay_until and priority<self.overlay[2]:return
  self.overlay=(title,text,priority);self.overlay_until=self.clock()+seconds
 def voice(self,event):
  typ=event.get('type');text=event.get('text','')
  if typ=='wake':self.overlay=None;self.voice_phase='listening';self.show('Listening','Go ahead...',25,5)
  elif typ=='partial_transcript' and self.voice_phase=='listening':
   display=speech_text(text,True);self.show('Hearing',display[-100:],25,5)
  elif typ=='transcript':self.overlay=None;self.voice_phase='thinking';self.show('You said',speech_text(text),12,3)
  elif typ=='transcribing':self.voice_phase='transcribing'
  elif typ=='thinking':self.voice_phase='thinking'
  elif typ=='announcement':self.notify('Sunday',text)
  elif typ=='idle':
   previous=self.voice_phase;self.voice_phase='idle'
   if previous in ('listening','transcribing'):
    self.overlay=None;self.overlay_until=0
    if self.ha_ready and self.states.get('input_boolean.sunday_no_listening',{}).get('state')=='off':self.show('Did not catch that','Please try again',5,3)
    else:self.overlay_until=0
   elif self.overlay and self.overlay[2]==3:self.overlay_until=min(self.overlay_until,self.clock()+3)
 def changed(self,eid,old,new):
  if new:self.states[eid]={**new,'entity_id':eid}
  else:self.states.pop(eid,None)
  if not old or not new:return
  a=new.get('attributes',{});name=a.get('friendly_name',eid.split('.')[-1].replace('_',' '))
  if eid.startswith('timer.') and (eid in self.members or 'sunday' in eid) and old.get('state')!='active' and new.get('state')=='active':self.show('Timer started',name,5,4)
  if eid.startswith('timer.') and old.get('state')=='active' and new.get('state')=='idle':
   # Only expiry with an elapsed finish timestamp; cancellation is not "done".
   finish=epoch(old.get('attributes',{}).get('finishes_at'))
   if finish and self.clock()>=finish:self.notify(name,'Timer finished')
  if eid.startswith('persistent_notification.') and new.get('state')!=old.get('state'):self.notify(a.get('title','Notice'),a.get('message',new.get('state')))
  if eid in self.members and eid.startswith('media_player.') and new.get('state') in ('playing','paused') and (old.get('state')!=new.get('state') or old.get('attributes',{}).get('media_title')!=a.get('media_title')):self.show('Now playing' if new['state']=='playing' else 'Paused',a.get('media_title') or name,7,1)
  if a.get('device_class')=='running' and old.get('state')=='on' and new.get('state')=='off':self.notify(name,'Finished')
 def notify(self,title,text):self.alert=(clean(title,63),clean(text,95));self.show(title,text,10,2)
 def action(self,data,observed):
  targets=data.get('targets',[]);targets=[t for t in targets if isinstance(t,str)]
  if not targets:return
  names=[self.states.get(t,{}).get('attributes',{}).get('friendly_name','Device') for t in targets];name=names[0] if len(names)==1 else str(len(names))+' devices'
  status=data.get('status','');failed=any(x in status.lower() for x in ['fail','error','reject','unavailable'])
  if failed:self.show('Did not complete',name+' could not be updated',10,4);return
  if status=='unknown':self.show('Not confirmed',name+' - response unknown',10,4);return
  if status not in ('service_completed','state_confirmed','confirmed'):return
  service=data.get('service','');expected={'turn_on':'on','turn_off':'off'}.get(service)
  changed=set(data.get('changed_entities',[]))
  def matches(target):
   e=self.states.get(target,{});state=e.get('state');attrs=e.get('attributes',{})
   if epoch(e.get('last_updated'))<observed-2:return False
   if expected=='on' and target.startswith('climate.'):return state not in (None,'off','unknown','unavailable')
   if expected=='on' and target.startswith('media_player.'):return state in ('on','idle','playing','paused')
   if expected:return state==expected
   intended=data.get('intended_values',{})
   return bool(intended) and all((state==v if k=='hvac_mode' else numeric(state)==numeric(v) if k=='value' and numeric(v) is not None else attrs.get(k)==v) for k,v in intended.items())
  matched=set(targets)<=changed and all(matches(t) for t in targets)
  detail=''
  if len(targets)==1:
   e=self.states.get(targets[0],{});a=e.get('attributes',{})
   if targets[0].startswith('climate.'):detail=' | '+e.get('state','')+(' '+str(a['temperature'])+' C' if a.get('temperature') is not None else '')
   elif targets[0].startswith('fan.') and a.get('percentage') is not None:detail=' | '+str(a['percentage'])+'%'
  confirmed=data.get('physical_success_confirmed') is True
  if confirmed or matched:self.show('Updated' if matched else 'Confirmed',name+(' - '+expected if expected else ' updated')+detail,8,4)
  else:self.show('Command sent',name+' - waiting for feedback',8,4)
 def packet(self):
  now=self.clock();p={k:'' for k in FIELDS};p.update(muted=1,ttl_ms=0,timer_seconds=0,timer_duration=0)
  mic=self.states.get('input_boolean.sunday_no_listening',{}).get('state');p['muted']=2 if not self.ha_ready or mic not in ('on','off') else int(mic=='on')
  room=[self.states[x] for x in self.members if x in self.states and self.states[x].get('state') not in ('unavailable','unknown') and not self.registry.get(x,{}).get('entity_category')]
  readings=[]
  for e in room:
   a=e.get('attributes',{});v=numeric(e.get('state'))
   if e['entity_id'].startswith('sensor.') and a.get('device_class')=='temperature' and v is not None:readings.append(f"{v:.1f} {a.get('unit_of_measurement','C')}")
  p['room1']='Room: '+(' / '.join(sorted(set(readings))) if readings and self.ha_ready else 'temperature unavailable')
  climates=[e for e in room if e['entity_id'].startswith('climate.')];targets=[e for e in room if e['entity_id'].startswith('input_number.') and 'target' in (e['entity_id']+' '+e.get('attributes',{}).get('friendly_name','')).lower()]
  if targets:p['room2']='Room target: '+str(targets[0]['state'])+' C'
  elif climates:p['room2']='AC: '+climates[0]['state']+' | Set '+str(climates[0].get('attributes',{}).get('temperature','?'))+' C'
  else:p['room2']='No climate reading available'
  timers=[]
  for eid,e in self.states.items():
   if self.ha_ready and eid.startswith('timer.') and (eid in self.members or 'sunday' in eid) and e.get('state') in ('active','paused'):
    a=e.get('attributes',{});end=epoch(a.get('finishes_at'))
    if e['state']=='active' and end>now:timers.append((end,a.get('friendly_name','Timer'),eid))
    elif e['state']=='paused':p['timer1']=a.get('friendly_name','Timer')+' - paused'
  for job in self.jobs:
   if job['kind']=='timer' and job['status']=='pending' and job['due']>now:timers.append((job['due'],'Sunday timer',job['id']))
  if timers:
   end,name,ident=min(timers);remaining=math.ceil(end-now);self.timer_lengths.setdefault((ident,end),remaining);p.update(timer1=name+(' +'+str(len(timers)-1)+' more' if len(timers)>1 else ''),timer_seconds=min(604800,remaining),timer_duration=min(604800,self.timer_lengths[(ident,end)]))
   if 0<remaining<=10 and (ident,end) not in self.timer_warned:
    self.timer_warned.add((ident,end));self.show('Timer almost done',name,min(5,remaining),1)
  # Bound state used only to preserve a countdown's visual starting length.
  active={(ident,end) for end,_,ident in timers};self.timer_lengths={k:v for k,v in self.timer_lengths.items() if k in active};self.timer_warned.intersection_update(active)
  media=[e for e in room if e['entity_id'].startswith('media_player.') and e.get('state') in ('playing','paused')]
  if media:
   e=media[0];a=e.get('attributes',{});p['media1']=a.get('media_title') or a.get('app_name') or a.get('friendly_name','TV');p['media2']=e['state'].capitalize()+' - '+str(a.get('media_artist') or a.get('source') or '')
  else:p['media1']='Nothing playing'
  s=self.sleep;p['sleep1']=str(s.get('state','Sleep status unavailable'))+(f" | Sleepy {s['sleepiness']:.0f}%" if isinstance(s.get('sleepiness'),(int,float)) else '');p['sleep2']=s.get('reason') or '; '.join(s.get('reasons',[]));p['alert1'],p['alert2']=self.alert
  if not self.ha_ready:p['room2']='Home Assistant unavailable';p['media1']='Media status unavailable';p['media2']=''
  if self.overlay and now<self.overlay_until:p.update(title=self.overlay[0],line1=self.overlay[1],ttl_ms=min(30000,int((self.overlay_until-now)*1000)))
  return {**p,**{k:clean(p[k],n) for k,n in FIELDS.items()}}

def validate_packet(value):
 keys=set(FIELDS)|{'muted','ttl_ms','timer_seconds','timer_duration'}
 if not isinstance(value,dict) or set(value)!=keys:raise ValueError('Invalid keys')
 for k,n in FIELDS.items():
  if not isinstance(value[k],str) or len(value[k])>n or any(ord(c)<32 or ord(c)>126 or c in ('"','\\') for c in value[k]):raise ValueError('Invalid text')
 for k,cap in [('muted',2),('ttl_ms',30000),('timer_seconds',604800),('timer_duration',604800)]:
  if type(value[k]) is not int or not 0<=value[k]<=cap:raise ValueError('Invalid number')
 return value
