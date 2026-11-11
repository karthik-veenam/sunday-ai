"""Prepare Hall's established remote UI for a Fire TV, without publishing."""
import copy,json
from pathlib import Path
TV='media_player.RANI_FIRE_TV_PENDING'
card=json.load(open('/home/djpi/rani-tuya-ir/hall-tv-card-reference.json'))
card=json.loads(json.dumps(card).replace('media_player.bravia_kd_65x75k',TV).replace('remote.bravia_kd_65x75k',TV).replace('Sony BRAVIA','Fire TV').replace('Hall TV','Rani TV'))
keys={'Up':'KEYCODE_DPAD_UP','Down':'KEYCODE_DPAD_DOWN','Left':'KEYCODE_DPAD_LEFT','Right':'KEYCODE_DPAD_RIGHT','Confirm':'KEYCODE_DPAD_CENTER','Home':'KEYCODE_HOME','Return':'KEYCODE_BACK','ActionMenu':'KEYCODE_MENU','Exit':'KEYCODE_ESCAPE','Mute':'KEYCODE_VOLUME_MUTE'}
def walk(v):
 if isinstance(v,dict):
  if v.get('perform_action')=='remote.send_command':
   old=v['data']['command'];assert old in keys
   v['perform_action']='androidtv.adb_command';v['data']['command']='input keyevent '+keys[old]
  if v.get('perform_action')=='media_player.play_media':
   old=v['data']['media_content_id']
   package='com.amazon.firetv.youtube' if 'youtube' in old else 'in.startv.hotstar'
   v['perform_action']='media_player.select_source';v['data']={'source':package}
  for x in list(v.values()):walk(x)
 elif isinstance(v,list):
  for x in v:walk(x)
walk(card)
assert 'bravia' not in json.dumps(card).lower()
folder=Path('/home/djpi/bedroom-dashboards');folder.mkdir(exist_ok=True)
p=folder/'rani-firetv-card-draft.json';p.write_text(json.dumps(card,indent=2)+'\n')
print('Draft prepared with Hall styling, Fire TV key mapping and app shortcut mapping. Not published; awaits device identification and authorization.')
