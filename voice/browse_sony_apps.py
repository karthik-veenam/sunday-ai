import asyncio,json,aiohttp
async def main():
 token=json.load(open('/home/djpi/sunday/config.json'))['ha_token']
 async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
  async with s.ws_connect('ws://localhost:8123/api/websocket') as w:
   await w.receive_json();await w.send_json({'type':'auth','access_token':token});assert (await w.receive_json())['type']=='auth_ok'
   await w.send_json({'id':1,'type':'media_player/browse_media','entity_id':'media_player.bravia_kd_65x75k','media_content_type':'apps','media_content_id':'apps'})
   while True:
    r=await w.receive_json()
    if r.get('id')==1:break
   print(json.dumps([{'title':c.get('title'),'id':c.get('media_content_id'),'type':c.get('media_content_type'),'thumbnail':c.get('thumbnail')} for c in r.get('result',{}).get('children',[]) if c.get('title') in ['YouTube','JioHotstar']],indent=2))
asyncio.run(main())
