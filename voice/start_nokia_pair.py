import asyncio,json,aiohttp
from pathlib import Path
async def main():
 t=json.load(open('/home/djpi/sunday/config.json'))['ha_token']
 async with aiohttp.ClientSession(headers={'Authorization':'Bearer '+t},timeout=aiohttp.ClientTimeout(total=35)) as s:
  async with s.post('http://localhost:8123/api/config/config_entries/flow',json={'handler':'androidtv_remote'}) as r:d=await r.json();assert d.get('type')=='form',d
  async with s.post('http://localhost:8123/api/config/config_entries/flow/'+d['flow_id'],json={'host':'192.168.88.115'}) as r:
   result=await r.json();print('Nokia pairing:',result.get('type'),result.get('step_id'),result.get('errors'),result.get('reason'))
   if result.get('step_id')=='pair':Path('/home/djpi/bedroom-dashboards/rani-nokia-pair-flow.json').write_text(json.dumps({'flow_id':result['flow_id']})+'\n')
asyncio.run(main())
