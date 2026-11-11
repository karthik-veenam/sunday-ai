"""Local simulated sleep policy. Pure decisions; no model, camera or appliance calls."""
from datetime import datetime
from zoneinfo import ZoneInfo
import math
TZ=ZoneInfo('Asia/Kolkata')
class SleepPolicy:
 def __init__(self,settings=None,wall=0,mono=0):
  self.settings=dict(force_night=None,stay_awake_until=0,auto_night=False,**(settings or {})) if not settings else {'force_night':settings.get('force_night'),'stay_awake_until':settings.get('stay_awake_until',0),'auto_night':settings.get('auto_night',False)}
  self.started=mono;self.last_activity=mono;self.last_interaction_at=None;self.was_asleep=False
 def interact(self,wall,mono):self.last_activity=mono;self.last_interaction_at=wall
 def evaluate(self,wall,mono,connected,body,night,lux,lux_age,busy):
  local=datetime.fromtimestamp(wall,TZ);hour=local.hour+local.minute/60
  in_night=hour>=22 or hour<7
  deep_night=hour<6
  quiet=max(0,mono-self.last_activity)
  valid_lux=isinstance(lux,(int,float)) and not isinstance(lux,bool) and math.isfinite(lux) and lux>=0 and lux_age<=20
  dark=valid_lux and lux<10
  if self.settings['force_night'] is None and connected and isinstance(night,bool):self.settings['force_night']=night
  force=self.settings['force_night'] is True
  until=self.settings['stay_awake_until']
  stay=isinstance(until,(int,float)) and wall<until<=wall+7200
  if not in_night:self.settings['auto_night']=False
  # Circadian estimate, explicitly simulated. Darkness alone cannot trigger daytime sleep.
  if hour<6:base=.94
  elif hour<7:base=.8
  elif hour<9:base=.4
  elif hour<20:base=.15
  elif hour<22:base=.45
  else:base=.75
  pressure=max(0,min(1,base+(.08 if dark else 0)+min(.08,quiet/1800*.08)-(.22 if busy else 0)))
  natural_ready=in_night and not busy and ((dark and quiet>=180) or (quiet>=900 and (deep_night or not valid_lux)))
  if not force and not stay and natural_ready:self.settings['auto_night']=True
  auto=self.settings['auto_night'] and in_night and not stay
  desired=bool(force or auto)
  why=[]
  if force:why.append('Forced Night Mode is on.')
  elif stay:why.append('Stay awake is active until '+datetime.fromtimestamp(until,TZ).strftime('%I:%M %p').lstrip('0')+'.')
  elif desired:why.append('Natural night sleep: it is late and the room has been quiet.')
  elif busy:why.append('She is listening, thinking or speaking with you.')
  elif in_night:
   if not valid_lux:why.append('It is late; room light is unavailable, so she waits for 15 minutes of quiet.')
   elif not dark:why.append('It is late but the room is still lit; she is waiting for darkness and quiet.')
   else:why.append('It is late and dark; waiting for 3 minutes of quiet before sleeping.')
  else:why.append('It is daytime; natural sleep is not due.')
  if not connected:state='Offline';why.insert(0,'The body is disconnected; its current sleep state is unknown.')
  elif busy:state='Temporarily awake' if desired else 'Engaged'
  elif desired:state='Asleep' if body=='sleeping' else 'Falling asleep'
  elif body=='sleeping':state='Waking up'
  else:state='Drowsy' if pressure>=.65 and not stay else 'Awake'
  woken=self.was_asleep and busy
  if connected and body=='sleeping':self.was_asleep=True
  if not desired and not busy:self.was_asleep=False
  return {'state':state,'sleepiness':round(pressure*100,1),'sleepiness_basis':'Simulated estimate from local time, light and quiet time; not a biological measurement.',
   'reasons':why,'reason':why[0],'clock':local.strftime('%I:%M %p').lstrip('0'),'timezone':'Asia/Kolkata','night_window':'22:00–07:00',
   'lux':lux if valid_lux else None,'quiet_seconds':round(quiet),'quiet_required_seconds':180 if dark else 900,
   'force_night':force,'stay_awake':stay,'stay_awake_until':until if stay else None,'actual_night':night if connected else None,
   'desired_night':desired,'automatic_night':bool(auto and not force),'woken_from_sleep':woken,'last_interaction_at':self.last_interaction_at,
   'observed_since':wall-max(0,mono-self.started),'updated_at':wall}
