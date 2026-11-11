import asyncio,json,aiohttp
async def main():
 t=json.load(open('/home/djpi/sunday/config.json'))['ha_token'];entity='media_player.swtv_2'
 async with aiohttp.ClientSession(headers={'Authorization':'Bearer '+t},timeout=aiohttp.ClientTimeout(total=15)) as s:
  async def call(service):
   async with s.post('http://localhost:8123/api/services/media_player/'+service,json={'entity_id':entity}) as r:
    print(service,'HTTP',r.status,flush=True);assert r.status==200
  async def wait_for_state(desired,tries=10):
   for _ in range(tries):
    async with s.get('http://localhost:8123/api/states/'+entity) as r:d=await r.json()
    if d['state']==desired:print('Nokia reported:',desired,flush=True);return True
    await asyncio.sleep(1)
   print('Last Nokia state:',d['state'],flush=True);return False
  try:
   await call('turn_off');off=await wait_for_state('off')
  finally:
   await call('turn_on')
  on=await wait_for_state('on',15)
  print('Power test:',{'standby_verified':off,'wake_verified':on},flush=True)
asyncio.run(main())
