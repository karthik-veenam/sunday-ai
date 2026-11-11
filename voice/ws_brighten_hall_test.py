"""Apply a light palette only to Hall Test's content."""
import asyncio
import copy
import datetime
import json
from pathlib import Path
import aiohttp

PALETTE = {
 'primary-color': '#2875bc', 'accent-color': '#2875bc',
 'primary-background-color': '#e8eef5', 'secondary-background-color': '#eef3f8',
 'ha-card-background': '#f9fbfe', 'card-background-color': '#f9fbfe',
 'paper-card-background-color': '#f9fbfe',
 'primary-text-color': '#233247', 'secondary-text-color': '#576b82',
 'disabled-text-color': '#75869a', 'disabled-color': '#a5b3c3',
 'divider-color': '#d6e0eb', 'ha-card-border-color': '#d6e0eb',
 'ha-card-border-radius': '12px',
 'ha-card-box-shadow': '0 2px 8px rgba(43, 65, 91, 0.06)',
 'state-inactive-color': '#73869b', 'state-icon-color': '#73869b',
 'state-icon-unavailable-color': '#a5b3c3',
 'ha-heading-card-title-color': '#233247',
 'ha-heading-card-subtitle-color': '#576b82',
 'control-button-background-color': '#e2eaf3',
 'control-button-background-opacity': '1',
 'control-button-icon-color': '#345b80',
 'control-circular-slider-background': '#dce5ef',
 'control-circular-slider-color': '#2875bc',
 'shape-color': 'rgba(96, 125, 156, 0.14)',
 'chip-background': '#e5edf6', 'chip-border-color': '#d2ddea',
 'rgb-primary-text-color': '35, 50, 71',
 'rgb-secondary-text-color': '87, 107, 130',
}

def brighten(config):
 view = config['views'][0]
 view['background'] = 'linear-gradient(155deg, #edf4fa 0%, #e2ebf5 55%, #eef2f8 100%) fixed'
 root = view['cards'][0]
 root['card_mod'] = {'style': ':host {\n' + ''.join(f'  --{k}: {v};\n' for k,v in PALETTE.items()) + '  color: #233247;\n  color-scheme: light;\n}\n#root { padding: 8px; }'}
 cards = root['cards']
 # Keep the TV artwork/blue gradient and its existing controls readable.
 tv = next(c for c in cards if c.get('entity') == 'media_player.bravia_kd_65x75k')
 tv['card_mod']['style'] += '\n:host { --primary-text-color: #ffffff; --secondary-text-color: #edf6ff; --rgb-primary-text-color: 255, 255, 255; --rgb-secondary-text-color: 237, 246, 255; }'
 row = next(c for c in cards if c.get('type') == 'horizontal-stack')
 # Remove dark-theme chip component defaults inside the light fan subcards.
 for group in row['cards'][1]['card']['cards'][1:]:
  chips = group['card']['cards'][1]
  chips['card_mod']['style'] += '\nha-card { --ha-card-background: #e3ebf5; --card-background-color: #e3ebf5; --chip-background: #e3ebf5; }'
 return config

async def main():
 token=json.load(open('/home/djpi/sunday/config.json'))['ha_token']
 async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
  async with s.ws_connect('ws://localhost:8123/api/websocket') as w:
   await w.receive_json(); await w.send_json({'type':'auth','access_token':token}); assert (await w.receive_json())['type']=='auth_ok'
   seq=0
   async def call(payload):
    nonlocal seq
    seq+=1; await w.send_json(dict(payload,id=seq))
    while True:
     r=await w.receive_json()
     if r.get('id')==seq:
      assert r.get('success'),r.get('error')
      return r.get('result')
   original=await call({'type':'lovelace/config','url_path':'dashboard-hall-test'})
   path=Path('/home/djpi/ha-backups') / ('hall-test-before-light-palette-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'.json')
   path.write_text(json.dumps(original,indent=2)+'\n')
   updated=brighten(copy.deepcopy(original))
   def behavior(obj):
    if isinstance(obj,dict):
     return {k:behavior(v) for k,v in obj.items() if k not in ('card_mod','background')}
    if isinstance(obj,list): return [behavior(v) for v in obj]
    return obj
   assert behavior(original)==behavior(updated), 'Behavior or layout changed'
   await call({'type':'lovelace/config/save','url_path':'dashboard-hall-test','config':updated})
   assert await call({'type':'lovelace/config','url_path':'dashboard-hall-test'})==updated
   print('Saved and verified Hall Test light palette. Backup:',path)

if __name__=='__main__': asyncio.run(main())
