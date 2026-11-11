"""Add a browser-local Sony remote popup to the Hall TV card."""
import asyncio,copy,datetime,json
from pathlib import Path
import aiohttp

REMOTE='remote.bravia_kd_65x75k'
TV='media_player.bravia_kd_65x75k'

def button(name,icon,command=None,service=None,data=None):
 action={'action':'perform-action','perform_action':service or 'remote.send_command','target':{'entity_id':TV if service else REMOTE}}
 if command: action['data']={'command':command}
 if data: action['data']=data
 return {'type':'button','name':name,'icon':icon,'show_state':False,'tap_action':action,'hold_action':{'action':'none'},'card_mod':{'style':'ha-card { background: #edf3fa; color: #233247; border: 1px solid #d6e0eb; border-radius: 14px; --primary-text-color: #233247; --state-icon-color: #2875bc; }'}}

def grid(cards):return {'type':'grid','columns':3,'square':False,'cards':cards}
def spacer():return {'type':'custom:mod-card','card':{'type':'markdown','content':''},'card_mod':{'style':'ha-card { visibility: hidden; }'}}

def add_remote(config):
 cards=config['views'][0]['cards'][0]['cards']
 idx=next(i for i,c in enumerate(cards) if c.get('entity')==TV and c['type']=='custom:mushroom-media-player-card')
 tv=cards[idx]
 popup={'type':'vertical-stack','cards':[
  grid([button('Home','mdi:home','Home'),button('Up','mdi:chevron-up','Up'),button('Back','mdi:arrow-u-left-top','Return')]),
  grid([button('Left','mdi:chevron-left','Left'),button('OK','mdi:circle-outline','Confirm'),button('Right','mdi:chevron-right','Right')]),
  grid([button('Menu','mdi:menu','ActionMenu'),button('Down','mdi:chevron-down','Down'),button('Exit','mdi:close','Exit')]),
  grid([button('Volume −','mdi:volume-minus',service='media_player.volume_down'),button('Mute','mdi:volume-off','Mute'),button('Volume +','mdi:volume-plus',service='media_player.volume_up')]),
  grid([button('Previous','mdi:skip-previous',service='media_player.media_previous_track'),button('Play / Pause','mdi:play-pause',service='media_player.media_play_pause'),button('Next','mdi:skip-next',service='media_player.media_next_track')]),
  {'type':'tile','entity':TV,'name':'TV power','icon':'mdi:power','tap_action':{'action':'perform-action','perform_action':'media_player.toggle','target':{'entity_id':TV}}}
 ],'card_mod':{'style':':host { --primary-text-color: #233247; --secondary-text-color: #576b82; --ha-card-background: #f9fbfe; --card-background-color: #f9fbfe; --divider-color: #d6e0eb; }'}}
 remote_button={'type':'custom:mushroom-chips-card','alignment':'end','chips':[{'type':'action','icon':'mdi:remote-tv','content':'Remote','tap_action':{'action':'fire-dom-event','browser_mod':{'service':'browser_mod.popup','data':{'title':'Hall TV Remote','content':popup,'dismissable':True}}}}],'card_mod':{'style':':host { --chip-background: rgba(255,255,255,0.22); --chip-border-color: rgba(255,255,255,0.35); --chip-height: 36px; --primary-text-color: #fff; --chip-icon-color: #fff; } ha-card { padding: 0 12px 10px; }'}}
 # Template chips display both an icon and the explicit Remote label.
 remote_button['chips'][0]['type']='template'
 remote_button['chips'][0]['icon_color']='white'
 outer_style=tv['card_mod']['style']
 tv['card_mod']['style']='ha-card { background: transparent !important; border: none; box-shadow: none; } :host { --primary-text-color: #fff; --secondary-text-color: #edf6ff; --rgb-primary-text-color: 255,255,255; --rgb-secondary-text-color: 237,246,255; }'
 cards[idx]={'type':'custom:mod-card','card_mod':{'style':outer_style+'\nha-card { overflow: hidden; border-radius: 12px; border: 1px solid var(--ha-card-border-color); }'},'card':{'type':'vertical-stack','cards':[tv,remote_button]}}
 return config

async def main():
 token=json.load(open('/home/djpi/sunday/config.json'))['ha_token']
 async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
  async with session.ws_connect('ws://localhost:8123/api/websocket') as ws:
   await ws.receive_json();await ws.send_json({'type':'auth','access_token':token});assert (await ws.receive_json())['type']=='auth_ok'
   seq=0
   async def call(payload):
    nonlocal seq
    seq+=1;await ws.send_json(dict(payload,id=seq))
    while True:
     r=await ws.receive_json()
     if r.get('id')==seq:
      assert r.get('success'),r.get('error')
      return r.get('result')
   states=await call({'type':'get_states'})
   assert any(s['entity_id']==REMOTE for s in states), 'Sony remote entity missing'
   services=await call({'type':'get_services'})
   assert 'popup' in services.get('browser_mod',{}), 'Browser Mod popup service unavailable'
   paths=['dashboard-hall','dashboard-hall-test','dashboard-common-areas']
   originals={p:await call({'type':'lovelace/config','url_path':p}) for p in paths}
   backup=Path('/home/djpi/ha-backups')/('before-hall-tv-remote-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'.json')
   backup.write_text(json.dumps(originals,indent=2)+'\n')
   updated={p:copy.deepcopy(c) for p,c in originals.items()}
   add_remote(updated['dashboard-hall'])
   add_remote(updated['dashboard-hall-test'])
   common=updated['dashboard-common-areas']; idx=next(i for i,v in enumerate(common['views']) if v.get('path')=='hall')
   previous=common['views'][idx]
   replacement=copy.deepcopy(updated['dashboard-hall']['views'][0]);replacement.update(path=previous['path'],title=previous['title'])
   common['views'][idx]=replacement
   assert all(v==common['views'][i] for i,v in enumerate(originals['dashboard-common-areas']['views']) if i!=idx)
   for p,c in updated.items():
    await call({'type':'lovelace/config/save','url_path':p,'config':c})
    assert await call({'type':'lovelace/config','url_path':p})==c
    print('Saved and verified:',p)
   print('Backup:',backup)
if __name__=='__main__':asyncio.run(main())
