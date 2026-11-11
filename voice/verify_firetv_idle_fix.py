import asyncio,json,aiohttp
async def main():
 t=json.load(open('/home/djpi/sunday/config.json'))['ha_token']
 entities=json.load(open('/home/djpi/bedroom-dashboards/rani-firetv-entities.json'))
 async with aiohttp.ClientSession(headers={'Authorization':'Bearer '+t},timeout=aiohttp.ClientTimeout(total=20)) as s:
  for _ in range(30):
   try:
    values=[]
    for entity in entities.values():
     async with s.get('http://localhost:8123/api/states/'+entity) as r:
      d=await r.json();values.append(d.get('state'))
    if len(values)==2 and all(v and v not in ['unknown','unavailable'] for v in values):break
   except (aiohttp.ClientError,ValueError):pass
   await asyncio.sleep(2)
  else:raise RuntimeError('Fire TV entities not ready')
  print('HA states before command:',dict(zip(entities.values(),values)),flush=True)
  async with s.get('http://192.168.88.116:8009/apps/FireTVRemote') as r:
   text=await r.text();print('Phone remote service before command:','stopped' if '<state>stopped</state>' in text else 'running',flush=True)
  async with s.post('http://localhost:8123/api/services/remote/send_command',json={'entity_id':entities['remote'],'command':'right'}) as r:
   print('HA button with automatic service recovery:',r.status,flush=True)
   if r.status!=200:print((await r.text())[:300])
   assert r.status==200
  for entity in entities.values():
   async with s.get('http://localhost:8123/api/states/'+entity) as r:
    d=await r.json();assert d['state']!='unavailable';print('Verified',entity,d['state'],flush=True)
asyncio.run(main())
