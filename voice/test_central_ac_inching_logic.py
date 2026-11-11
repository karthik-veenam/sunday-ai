from pathlib import Path
from types import SimpleNamespace
from datetime import datetime,timezone
import yaml,jinja2
all_automations=yaml.safe_load(Path('/home/pi/homeassistant/automations.yaml').read_text())
manager=next(a for a in all_automations if a.get('id')=='central_ac_inching_schedule')
expiry=next(a for a in all_automations if a.get('id')=='central_ac_inching_turn_off')
env=jinja2.Environment(undefined=jinja2.StrictUndefined)
now=datetime.now(timezone.utc)
def context(ac='cool',enabled='on',armed='on',deadline=None,trigger='startup',previous=None):
 values={'climate.hall_ac':ac,'input_boolean.central_ac_inching':enabled,'input_boolean.central_ac_inching_armed':armed,'input_datetime.central_ac_inching_deadline':deadline or now.isoformat()}
 def ts(v,default=0):
  try:return v.timestamp() if isinstance(v,datetime) else datetime.fromisoformat(v).timestamp()
  except (ValueError,TypeError):return default
 return dict(states=lambda e:values.get(e,'unknown'),is_state=lambda e,s:values.get(e)==s,as_timestamp=ts,now=lambda:now,trigger=SimpleNamespace(id=trigger,from_state=SimpleNamespace(state=previous) if previous else None))
def render(template,c):return env.from_string(template).render(**c).strip()=='True'
def branch(c):
 for i,x in enumerate(manager['actions'][0]['choose']):
  if render(x['conditions'],c):return i
 return None
assert branch(context(trigger='ac_on',previous='off'))==1
assert branch(context(trigger='ac_on',previous='unavailable')) is None
assert branch(context(trigger='startup')) is None
assert branch(context(trigger='settings'))==1
assert branch(context(enabled='off',trigger='settings'))==0
assert branch(context(ac='off',trigger='ac_off'))==0
assert branch(context(ac='unavailable',trigger='startup')) is None
assert branch(context(armed='off',trigger='ac_on',previous='off'))==1
assert expiry['actions']==[{'action':'climate.turn_off','target':{'entity_id':'climate.hall_ac'}}]
condition=expiry['conditions'][-1]['value_template']
assert render(condition,context(deadline='2020-01-01 00:00:00'))
assert not render(condition,context(deadline='2099-01-01 00:00:00'))
assert not render(condition,context(deadline='unavailable'))
assert manager['mode']=='queued'
print('Passed: each off/on session starts afresh; settings reset; off/disabled cancels; startup and reconnect preserve pending deadline; past/future/invalid deadline checks; correct AC shutoff target.')
