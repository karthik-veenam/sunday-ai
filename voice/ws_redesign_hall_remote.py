"""A compact remote with a unified direction pad and grouped controls."""
import copy
import ws_fix_hall_remote_position as runner

BASE='''ha-card {
 height: 54px; box-sizing: border-box; border: none; border-radius: 16px;
 background: #e7eef6; color: #294765; box-shadow: none;
 --primary-text-color: #294765; --state-icon-color: #294765;
 --ha-card-border-width: 0px;
}
ha-state-icon { --mdc-icon-size: 23px; width: 23px; height: 23px; }
.name { font-size: 10px !important; font-weight: 600; letter-spacing: 0.2px; }
'''

def redesign(config):
 cards=config['views'][0]['cards'][0]['cards']
 wrapper=next(c for c in cards if c.get('type')=='custom:mod-card' and any(x.get('entity')=='media_player.bravia_kd_65x75k' for x in c.get('card',{}).get('cards',[])))
 data=wrapper['card']['cards'][1]['chips'][0]['tap_action']['browser_mod']['data']
 old=data['content']
 buttons={}
 def collect(obj):
  if isinstance(obj,dict):
   if obj.get('type') in ('button','tile') and 'tap_action' in obj:buttons[obj['name']]=obj
   for v in obj.values():collect(v)
  elif isinstance(obj,list):
   for v in obj:collect(v)
 collect(old)
 assert len(buttons)==16
 def key(name,label=None,style='',icon=None):
  oldbutton=buttons[name]
  b={'type':'button','name':label if label is not None else name,'icon':icon or oldbutton['icon'],'show_state':False,'show_name':label!='','tap_action':copy.deepcopy(oldbutton['tap_action']),'hold_action':{'action':'none'},'card_mod':{'style':BASE+style}}
  return b
 def grid(items,cols=3):return {'type':'grid','columns':cols,'square':False,'cards':items}
 def blank():return {'type':'button','show_name':False,'show_icon':False,'tap_action':{'action':'none'},'card_mod':{'style':'ha-card { visibility: hidden; height: 56px; border: none; box-shadow: none; }'}}
 def surface(card,style):return {'type':'custom:mod-card','card':card,'card_mod':{'style':style}}
 dstyle='ha-card { height: 56px; background: transparent; border-radius: 50%; } ha-state-icon { --mdc-icon-size: 30px; width: 30px; height: 30px; }'
 dpad=grid([blank(),key('Up','',dstyle),blank(),key('Left','',dstyle),key('OK','OK','ha-card { height: 56px; border-radius: 50%; background: #2869a8; --primary-text-color: white; --state-icon-color: white; box-shadow: 0 4px 12px rgba(40,105,168,0.25); }','mdi:check'),key('Right','',dstyle),blank(),key('Down','',dstyle),blank()])
 pad=surface(dpad,'''ha-card {
   width: 220px; max-width: 100%; margin: 8px auto 16px;
   padding: 12px; box-sizing: border-box; border-radius: 50%;
   background: linear-gradient(145deg, #edf3f9, #dce7f2);
   border: 1px solid #d3dfed;
   box-shadow: inset 0 1px 2px rgba(255,255,255,0.9), 0 6px 18px rgba(40,68,100,0.07);
 }''')
 title={'type':'custom:mushroom-template-card','primary':'Sony BRAVIA','secondary':"{{ 'Connected · TV on' if states('media_player.bravia_kd_65x75k') not in ['off','standby','unknown','unavailable'] else ('TV off' if states('media_player.bravia_kd_65x75k') in ['off','standby'] else 'TV unavailable') }}",'icon':'mdi:television','icon_color':'blue','tap_action':{'action':'none'},'card_mod':{'style':'ha-card { background: transparent; border: none; box-shadow: none; padding: 0; --primary-text-color: #1e3248; --secondary-text-color: #6e8093; }'}}
 power=key('TV power','','ha-card { height: 44px; width: 44px; border-radius: 50%; background: #f8e8e8; --state-icon-color: #b44d52; }')
 header=surface({'type':'horizontal-stack','cards':[title,power],'card_mod':{'style':'#root { display: grid; grid-template-columns: minmax(0, 1fr) 44px; align-items: center; gap: 10px; }'}},'ha-card { background: transparent; border: none; box-shadow: none; padding: 0 0 12px; }')
 shortcuts=grid([key('Home','Home'),key('Back','Back'),key('Menu','Menu'),key('Exit','Exit')],4)
 volume=surface(grid([key('Volume −','',icon='mdi:volume-minus'),key('Mute','Mute'),key('Volume +','',icon='mdi:volume-plus')]),'ha-card { background: #e7eef6; border: 1px solid #dae4ef; border-radius: 18px; padding: 2px; box-shadow: none; }')
 playback=grid([key('Previous',''),key('Play / Pause','','ha-card { background: #dce9f7; --state-icon-color: #2869a8; }'),key('Next','')])
 content={'type':'vertical-stack','cards':[header,pad,shortcuts,volume,playback],'card_mod':{'style':':host { --primary-text-color: #233247; --secondary-text-color: #6e8093; --ha-card-background: #f5f8fc; --card-background-color: #f5f8fc; --divider-color: #d6e0eb; } #root { gap: 12px; }'}}
 data['content']=surface(content,'ha-card { padding: 16px; max-width: 360px; margin: 0 auto; background: #f5f8fc; border: 1px solid #dce5ef; border-radius: 26px; box-shadow: 0 8px 32px rgba(24,51,81,0.08); }')
 data['title']='TV Remote'
 data['popup_styles']=[{'style':'all','styles':':host { --popup-background-color: #f5f8fc; --popup-header-background-color: #f5f8fc; --primary-text-color: #233247; --secondary-text-color: #6e8093; --ha-dialog-surface-background: #f5f8fc; --mdc-theme-surface: #f5f8fc; --mdc-theme-on-surface: #233247; }'}]
 def actions(obj):
  result=[]
  if isinstance(obj,dict):
   if obj.get('tap_action',{}).get('action')=='perform-action':result.append(json_key(obj['tap_action']))
   for v in obj.values():result.extend(actions(v))
  elif isinstance(obj,list):
   for v in obj:result.extend(actions(v))
  return result
 assert sorted(actions(old))==sorted(actions(data['content'])), 'Remote actions changed'
 return config

def json_key(value):
 import json
 return json.dumps(value,sort_keys=True)

runner.fix=redesign
if __name__=='__main__':runner.asyncio.run(runner.main())
