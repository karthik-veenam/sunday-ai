import asyncio,json,aiohttp
async def main():
 token=json.load(open('/home/djpi/sunday/config.json'))['ha_token']
 async with aiohttp.ClientSession() as s:
  async with s.get('http://localhost:8123/api/states/media_player.bravia_kd_65x75k',headers={'Authorization':'Bearer '+token}) as r:
   r.raise_for_status();e=await r.json()
   print(json.dumps({k:e['attributes'].get(k) for k in ['source','source_list','app_id','app_name','supported_features']},indent=2))
asyncio.run(main())
