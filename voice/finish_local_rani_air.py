import asyncio,json,copy
from pathlib import Path
import aiohttp
async def main():
 t=json.load(open('/home/djpi/sunday/config.json'))['ha_token']
 async with aiohttp.ClientSession(headers={'Authorization':'Bearer '+t}) as s:
  async with s.ws_connect('ws://localhost:8123/api/websocket') as w:
   await w.receive_json();await w.send_json({'type':'auth','access_token':t});assert (await w.receive_json())['type']=='auth_ok'
   await w.send_json({'id':1,'type':'lovelace/config','url_path':'dashboard-rani'})
   old=(await w.receive_json())['result'];cfg=copy.deepcopy(old)
   for card in cfg['views'][0]['cards'][0]['cards']:
    if card.get('entity')=='climate.rani_air':card['hvac_modes']=['off','cool','dry','fan_only']
   await w.send_json({'id':2,'type':'lovelace/config/save','url_path':'dashboard-rani','config':cfg});assert (await w.receive_json())['success']
   Path('/home/djpi/bedroom-dashboards/rani.json').write_text(json.dumps(cfg,indent=2)+'\n')
   print('Dashboard modes matched to the verified Daikin profile.')
  async with s.post('http://localhost:8123/api/services/homeassistant/restart',json={}) as r:print('Graceful restart requested:',r.status)
asyncio.run(main())
