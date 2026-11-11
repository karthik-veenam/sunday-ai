"""Correct remote proportions and native button typography."""
import ws_fix_hall_remote_position as runner

def refine(config):
 cards=config['views'][0]['cards'][0]['cards']
 tv=next(c for c in cards if c.get('type')=='custom:mod-card' and any(x.get('entity')=='media_player.bravia_kd_65x75k' for x in c.get('card',{}).get('cards',[])))
 data=tv['card']['cards'][1]['chips'][0]['tap_action']['browser_mod']['data']
 outer=data['content'];content=outer['card'];header,pad,shortcuts,volume,playback=content['cards']
 # Give the pad equal outer dimensions and an explicitly square inner grid.
 pad['card_mod']['style']='''ha-card {
  width: 224px !important; height: 224px !important;
  max-width: 100%; margin: 4px auto 12px;
  padding: 12px; box-sizing: border-box; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  background: #e4edf6; border: 1px solid #d0dfed;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.9);
 }
 ha-card > * { width: 100%; }
 '''
 pad['card']['square']=True
 # The Mushroom chip can anchor itself reliably in the header wrapper.
 title=header['card']['cards'][0]
 power_action=header['card']['cards'][1]['tap_action']
 title['secondary']='Hall TV'
 title['card_mod']['style']+='\nha-card { padding: 0 52px 0 0 !important; }'
 power={'type':'custom:mushroom-chips-card','chips':[{'type':'action','icon':'mdi:power','tap_action':power_action}],'card_mod':{'style': ''':host { position: absolute; right: 0; top: 6px; width: 40px; height: 40px; --chip-height: 40px; --chip-icon-size: 22px; --chip-background: #f8e8e8; --chip-icon-color: #b44d52; --primary-text-color: #b44d52; }
 ha-card { padding: 0; border: none; box-shadow: none; }
 '''}}
 header['card']['cards']=[title,power]
 header['card']['card_mod']={'style':'#root { position: relative; gap: 0; } #root > * { margin: 0 !important; }'}
 header['card']['type']='vertical-stack'
 header['card_mod']['style']='ha-card { position: relative; background: transparent; border: none; box-shadow: none; padding: 0 0 8px; }'
 # Let the pad and transport icons speak for themselves; labels remain in name
 # for accessibility even where show_name is false.
 def buttons(obj):
  if isinstance(obj,dict):
   if obj.get('type')=='button' and obj.get('tap_action',{}).get('action')=='perform-action':
    obj.setdefault('card_mod',{}).setdefault('style','')
    obj['card_mod']['style']+='''
      ha-card { --mdc-icon-size: 24px; --ha-icon-size: 24px; }
      ha-state-icon, ha-icon, #icon { --mdc-icon-size: 24px !important; --ha-icon-size: 24px !important; width: 24px !important; height: 24px !important; }
      #name, .name { font-size: 11px !important; line-height: 16px !important; font-weight: 600 !important; }
    '''
   for v in list(obj.values()):buttons(v)
  elif isinstance(obj,list):
   for v in obj:buttons(v)
 buttons(content)
 ok=pad['card']['cards'][4]
 ok['show_name']=False
 ok['name']='OK'
 ok['card_mod']['style']+='\nha-card { color: white !important; --primary-text-color: white !important; --state-icon-color: white !important; }'
 # Compact circular controls instead of a large middle mute label.
 volume['card']['cards'][1]['show_name']=False
 content['card_mod']['style']+='\n#root { gap: 10px !important; }'
 outer['card_mod']['style']='ha-card { padding: 18px 16px; max-width: 328px; margin: 0 auto; background: #f5f8fc; border: 1px solid #dce5ef; border-radius: 24px; box-shadow: 0 8px 32px rgba(24,51,81,0.08); }'
 return config

runner.fix=refine
if __name__=='__main__':runner.asyncio.run(runner.main())
