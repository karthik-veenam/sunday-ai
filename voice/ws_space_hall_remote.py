"""Use icon-only remote controls with more vertical breathing room."""
import ws_fix_hall_remote_position as runner

def space_remote(config):
 cards=config['views'][0]['cards'][0]['cards']
 tv=next(c for c in cards if c.get('type')=='custom:mod-card' and any(x.get('entity')=='media_player.bravia_kd_65x75k' for x in c.get('card',{}).get('cards',[])))
 popup=tv['card']['cards'][1]['chips'][0]['tap_action']['browser_mod']['data']
 outer=popup['content']; content=outer['card']
 header,pad,shortcuts,volume,playback=content['cards']
 # Leave direction pad dimensions and styles intact.
 def icon_only(obj):
  if isinstance(obj,dict):
   if obj.get('type')=='button' and obj.get('tap_action',{}).get('action')=='perform-action':
    obj['show_name']=False
    obj.setdefault('card_mod',{}).setdefault('style','')
    obj['card_mod']['style']+='''
      ha-card { height: 66px !important; border-radius: 18px; }
      ha-state-icon, ha-icon, #icon { --mdc-icon-size: 26px !important; --ha-icon-size: 26px !important; width: 26px !important; height: 26px !important; }
    '''
   for v in list(obj.values()):icon_only(v)
  elif isinstance(obj,list):
   for v in obj:icon_only(v)
 for group in (shortcuts,volume,playback):icon_only(group)
 # Each group gets its own spacing, instead of several tightly stacked bars.
 content['card_mod']['style']+='\n#root { gap: 22px !important; } #root > * { margin-top: 0 !important; margin-bottom: 0 !important; }'
 # Card styles work on their own hosts even when stack styles are not inherited.
 volume['card_mod']['style']+='\nha-card { margin: 14px 0 !important; padding: 6px 2px; }'
 for button in shortcuts['cards']:
  button['card_mod']['style']+='\nha-card { margin-top: 8px; margin-bottom: 8px; }'
 for button in playback['cards']:
  button['card_mod']['style']+='\nha-card { margin-top: 8px; margin-bottom: 4px; }'
 outer['card_mod']['style']+='\nha-card { padding-top: 24px; padding-bottom: 26px; }'
 return config

runner.fix=space_remote
if __name__=='__main__':runner.asyncio.run(runner.main())
