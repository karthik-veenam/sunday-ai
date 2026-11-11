import asyncio,json,aiohttp
async def main():
 token=json.load(open('/home/djpi/sunday/config.json'))['ha_token']
 async with aiohttp.ClientSession(headers={'Authorization':'Bearer '+token}) as s:
  for _ in range(22):
   async with s.get('http://localhost:8123/api/states/climate.rani_air') as r:
    if r.status==200:
     data=await r.json()
     if data['state'] not in ['unavailable','unknown']:
      print(json.dumps({k:data.get(k) for k in ['entity_id','state','attributes']}));return
   await asyncio.sleep(2)
  print('Entity not ready with known state')
asyncio.run(main())
