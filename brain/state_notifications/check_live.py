"""Exercise only a disabled, impossible-state temporary rule. Never send test Telegram."""
import hashlib,json,time
from pathlib import Path
import requests
ROOT=Path('/home/djpi/sunday2-rebuild')
c=json.loads((ROOT/'config.json').read_text());ha=json.loads(Path('/home/djpi/sunday/config.json').read_text());headers={'Authorization':'Bearer '+c['daemon_token']};hh={'Authorization':'Bearer '+ha['ha_token']};base='http://127.0.0.1:8090';hab='http://127.0.0.1:8123'
def req(path,body=None):
 r=requests.get(base+path,headers=headers,timeout=30) if body is None else requests.post(base+path,headers=headers,json=body,timeout=30);r.raise_for_status();return r.json()
def fridge():
 r=requests.get(hab+'/api/config/automation/config/fridge_any_door_open_telegram',headers=hh,timeout=5);r.raise_for_status();return r.json()
before=fridge();initial=req('/v1/notifications');assert len(initial['rules'])==1 and initial['rules'][0]['rule']['enabled'];rid=None
try:
 rule={'name':'Temporary disabled validation','entity_ids':['fan.roka_s_room_roka_fan'],'condition':'equals','value':'__sunday_validation_never_match__','for_seconds':1,'cooldown_seconds':2,'message':'Validation only','enabled':False}
 created=req('/v1/notifications/manage',{'action':'save','revision':initial['revision'],'rule':rule});row=next(r for r in created['rules'] if r['rule']['name']==rule['name']);rid=row['rule']['id'];assert row['rule']['enabled'] is False
 print('Live HA disabled rule creation and reload confirmed')
 updated=req('/v1/notifications/manage',{'action':'save','revision':created['revision'],'rule':{'id':rid,'cooldown_seconds':3}});row=next(r for r in updated['rules'] if r['rule']['id']==rid);assert row['rule']['cooldown_seconds']==3 and row['rule']['enabled'] is False;print('Live update preserved disabled state')
 removed=req('/v1/notifications/manage',{'action':'remove','id':rid,'revision':updated['revision']});assert not any(r['rule']['id']==rid for r in removed['rules']);rid=None;print('Temporary rule removed')
 assert fridge()==before;end=req('/v1/notifications');assert len(end['rules'])==1 and end['rules'][0]['rule']['enabled'];print('Original fridge/freezer rule configuration unchanged and enabled; no Telegram test or appliance command sent')
finally:
 if rid:
  current=req('/v1/notifications');req('/v1/notifications/manage',{'action':'remove','id':rid,'revision':current['revision']})
