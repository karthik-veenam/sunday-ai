import asyncio,json,aiohttp
from pathlib import Path
async def main():
 t=json.load(open('/home/djpi/sunday/config.json'))['ha_token']
 async with aiohttp.ClientSession() as s:
  async with s.ws_connect('ws://127.0.0.1:8123/api/websocket') as w:
   await w.receive_json();await w.send_json({'type':'auth','access_token':t});await w.receive_json();await w.send_json({'id':1,'type':'media_player/browse_media','entity_id':'media_player.bravia_kd_65x75k','media_content_type':'apps','media_content_id':'apps'})
   r=await w.receive_json();assert r['success'];Path('/home/djpi/sunday/sony-installed-apps.json').write_text(json.dumps(r['result'],indent=2))
   for x in r['result'].get('children',[]):print(x['title'])
asyncio.run(main())
