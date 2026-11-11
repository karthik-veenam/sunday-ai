import asyncio,json,aiohttp
async def main():
 token=json.load(open('/home/djpi/sunday/config.json'))['ha_token']
 async with aiohttp.ClientSession(headers={'Authorization':'Bearer '+token},timeout=aiohttp.ClientTimeout(total=25)) as s:
  async def state(entity):
   async with s.get('http://localhost:8123/api/states/'+entity) as r:return (await r.json()).get('state')
  async def action(service):
   async with s.post('http://localhost:8123/api/services/media_player/'+service,json={'entity_id':'media_player.rani_fire_tv'}) as r:
    print(service,'HTTP',r.status,flush=True)
    if r.status!=200:print((await r.text())[:200],flush=True)
    assert r.status==200
  for _ in range(30):
   try:
    if await state('media_player.rani_fire_tv') not in [None,'unknown','unavailable'] and await state('media_player.swtv_2')=='on':break
   except (aiohttp.ClientError,ValueError):pass
   await asyncio.sleep(2)
  else:raise RuntimeError('Waiting for the TV connections to finish loading')
  try:
   await action('toggle');assert await state('media_player.rani_fire_tv')=='off'
   print('Shared power button routed to Nokia Off.',flush=True)
   await asyncio.sleep(4)
   await action('toggle');assert await state('media_player.rani_fire_tv') in ['idle','on']
   print('Shared power button completed explicit wake + Home. TV should now be on.',flush=True)
  except Exception:
   await action('turn_on')
   raise
asyncio.run(main())
