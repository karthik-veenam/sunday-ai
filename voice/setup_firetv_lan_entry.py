import asyncio,json,aiohttp
from pathlib import Path
async def main():
 t=json.load(open('/home/djpi/sunday/config.json'))['ha_token']
 d=json.load(open('/home/djpi/firetv-lan/rani-private.json'))
 async with aiohttp.ClientSession(headers={'Authorization':'Bearer '+t}) as s:
  for _ in range(25):
   try:
    async with s.post('http://localhost:8123/api/config/config_entries/flow',json={'handler':'firetv_lan'}) as r:
     flow=await r.json()
     if flow.get('type')=='form':break
   except (aiohttp.ClientError,ValueError):pass
   await asyncio.sleep(2)
  else:raise RuntimeError('Fire TV integration not loaded')
  async with s.post('http://localhost:8123/api/config/config_entries/flow/'+flow['flow_id'],json=d) as r:
   result=await r.json();print('Fire TV setup:',result.get('type'),result.get('errors'),result.get('reason'))
   assert result.get('type')=='create_entry'
   Path('/home/djpi/bedroom-dashboards/rani-firetv-entry.json').write_text(json.dumps({'entry_id':result['result']['entry_id']})+'\n')
asyncio.run(main())
