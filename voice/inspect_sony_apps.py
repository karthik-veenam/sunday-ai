import asyncio,json,aiohttp
async def main():
 t=json.load(open('/home/djpi/sunday/config.json'))['ha_token']
 async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25)) as s:
  async with s.ws_connect('ws://127.0.0.1:8123/api/websocket') as w:
   await w.receive_json();await w.send_json({'type':'auth','access_token':t});assert (await w.receive_json())['type']=='auth_ok'
   await w.send_json({'id':1,'type':'media_player/browse_media','entity_id':'media_player.bravia_kd_65x75k','media_content_type':'apps','media_content_id':'apps'})
   while True:
    r=await w.receive_json()
    if r.get('id')==1:
     if not r.get('success'):print(json.dumps(r));return
     for x in r['result'].get('children',[]):print(json.dumps({k:x.get(k) for k in ['title','media_content_id','media_content_type']}))
     return
asyncio.run(main())
