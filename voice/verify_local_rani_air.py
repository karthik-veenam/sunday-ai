import asyncio,json,aiohttp
async def main():
 t=json.load(open('/home/djpi/sunday/config.json'))['ha_token']
 async with aiohttp.ClientSession(headers={'Authorization':'Bearer '+t}) as s:
  for _ in range(28):
   try:
    async with s.get('http://localhost:8123/api/states/climate.rani_air',timeout=aiohttp.ClientTimeout(total=3)) as r:
     d=await r.json()
     if r.status==200 and d.get('attributes',{}).get('min_temp')==18 and d['state'] not in ['unknown','unavailable']:
      break
   except (aiohttp.ClientError,asyncio.TimeoutError):pass
   await asyncio.sleep(2)
  else:raise RuntimeError('Local AC entity not ready')
  async with s.post('http://localhost:8123/api/services/climate/set_temperature',json={'entity_id':'climate.rani_air','temperature':24}) as r:
   assert r.status==200,(r.status,await r.text())
  async with s.get('http://localhost:8123/api/states/climate.rani_air') as r:
   d=await r.json();assert d['state']=='cool' and d['attributes']['temperature']==24
   print('Live HA local service verified:',d['entity_id'],d['state'],d['attributes']['temperature'],'°C, fan:',d['attributes']['fan_mode'])
asyncio.run(main())
