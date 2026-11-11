import asyncio,json,aiohttp
from pathlib import Path
async def main():
 t=json.load(open('/home/djpi/sunday/config.json'))['ha_token']
 async with aiohttp.ClientSession() as s:
  async with s.ws_connect('ws://localhost:8123/api/websocket') as w:
   await w.receive_json();await w.send_json({'type':'auth','access_token':t});assert (await w.receive_json())['type']=='auth_ok'
   await w.send_json({'id':1,'type':'lovelace/config','url_path':'dashboard-mobile'});r=await w.receive_json();assert r['success'];d=r['result']
   Path('/home/djpi/ha-backups/sunday-dashboard-live.json').write_text(json.dumps(d,indent=2))
   print(json.dumps(d,indent=2)[:20000])
asyncio.run(main())
