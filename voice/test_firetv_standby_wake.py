import asyncio,json,aiohttp
async def main():
 d=json.load(open('/home/djpi/firetv-lan/rani-private.json'));base='https://'+d['host']+':8080'
 headers={'X-Api-Key':'0987654321','X-Client-Token':d['token'],'User-Agent':'okhttp/4.10.0'}
 async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=7)) as s:
  # Ensure the phone-remote endpoint is available before requesting standby.
  async with s.post('http://'+d['host']+':8009/apps/FireTVRemote') as r:print('Prepare remote service:',r.status,flush=True)
  await asyncio.sleep(1)
  async with s.post(base+'/v1/FireTV?action=sleep',ssl=False,headers=headers,json={'keyActionType':'keyDownUp'}) as r:print('Fire TV standby:',r.status,(await r.text())[:100],flush=True)
  await asyncio.sleep(4)
  async with s.post('http://'+d['host']+':8009/apps/FireTVRemote') as r:print('Fire TV wake:',r.status,flush=True)
  await asyncio.sleep(2)
  async with s.post(base+'/v1/FireTV?action=home',ssl=False,headers=headers,json={'keyActionType':'keyDownUp'}) as r:print('Fire TV Home / HDMI wake:',r.status,(await r.text())[:100],flush=True)
asyncio.run(main())
