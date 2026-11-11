"""Compact body state/action contracts from the already-refreshed HA snapshot."""
import json,math,time
from datetime import datetime,timezone
from home_capabilities import device_controls
CONTROLS=('switch.sunday_body_head_motion','switch.sunday_body_night_mode','switch.sunday_body_camera_preview','switch.sunday_body_idle_sounds','switch.sunday_body_stay_awake_1_hour','number.sunday_body_volume','number.sunday_body_night_brightness','select.sunday_body_display','select.sunday_body_night_screen','select.sunday_body_led_colour_day','select.sunday_body_led_colour_night')
STATES={'activity':'select.sunday_body_face_state','expression':'select.sunday_body_expression','display':'select.sunday_body_display','night_mode':'binary_sensor.sunday_body_night_rendering_active','forced_night':'switch.sunday_body_night_mode','camera_preview':'switch.sunday_body_camera_preview'}
def brightness(value):
 try:lux=float(value)
 except (TypeError,ValueError):return None
 if not math.isfinite(lux) or lux<0:return None
 return {'lux':lux,'category':'very dark' if lux<5 else 'barely visible' if lux<=10 else 'dim' if lux<=20 else 'well lit','approximate':True}

async def snapshot(model,now=None):
 now=time.time() if now is None else now
 entities=getattr(model,'entities',{})
 rows=[e for eid,e in entities.items() if eid.split('.',1)[-1].startswith('sunday_body_') and not e.get('disabled_by')]
 observed=getattr(model,'states_observed_at',0)
 fresh=bool(observed and 0<=now-observed<30)
 known=bool(rows)
 available=[e for e in rows if e.get('available') and e.get('state') not in ('unavailable','unknown')]
 result={'connected':bool(available) if known and fresh else None,'fresh':fresh,'observed_at':datetime.fromtimestamp(observed,timezone.utc).isoformat() if observed else None}
 if not known:return {**result,'status':'Body entities are not available in the current home index.'}
 result['status']='online' if result['connected'] else 'offline' if result['connected'] is False else 'unknown; do not infer current body state'
 def value(eid):
  e=entities.get(eid,{})
  return e.get('state') if fresh and e.get('available') and e.get('state') not in ('unknown','unavailable') else None
 result.update({name:value(eid) for name,eid in STATES.items()})
 result['expression_note']='Auto preserves listening/thinking/speaking and local touch reactions. When Alive context is enabled, the emotional engine supplies the resting face and the brain may suggest a temporary reply expression through its private envelope, not an HA decoration call. Manual, camera and night constraints take priority.'
 result['brightness']=brightness(value('sensor.sunday_body_room_light'))
 # No HA request when HomeModel already loaded its service catalog for this turn.
 services=getattr(model,'services_cache',None)
 if services is None:
  result['controls_unavailable']='Service contracts have not been observed; do not invent actions.'
 else:
  controls=device_controls(model,[entities[eid] for eid in CONTROLS if eid in entities and not entities[eid].get('disabled_by')],services)
  result['controls']=[{'entity_id':c['entity_id'],'domain':c['domain'],'state':c['state'] if fresh else None,'available':bool(c['available'] and fresh),'settings':c['settings'],'actions':c['actions']} for c in controls]
 result['camera']={'tool':'take_picture','image_understanding':False,'capture_result':'A sent capture request does not prove the photo is already on screen.'}
 return result

async def prompt_context(model):
 return 'Current body snapshot (state and ready-to-use HA controls; no discovery needed for supplied targets):\n'+json.dumps(await snapshot(model),separators=(',',':'))
