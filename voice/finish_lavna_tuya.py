import asyncio,json
from pathlib import Path
import aiohttp
async def main():
 token=json.load(open('/home/djpi/sunday/config.json'))['ha_token'];headers={'Authorization':'Bearer '+token}
 old=json.load(open('/home/djpi/ha-backups/lavna-tuya-setup-flow.json'))
 async with aiohttp.ClientSession(headers=headers,timeout=aiohttp.ClientTimeout(total=30)) as s:
  async with s.post('http://localhost:8123/api/config/config_entries/flow/'+old['flow_id'],json={}) as r:
   print('HTTP:',r.status)
   if r.status!=200:print('Flow could not be completed');return
   flow=await r.json()
  print('Setup result:',{k:flow.get(k) for k in ['type','step_id','reason','errors','title'] if k in flow})
  result=flow.get('result') or {}
  entry_id=result.get('entry_id') if isinstance(result,dict) else None
  if entry_id:
   p=Path('/home/djpi/ha-backups/lavna-tuya-entry-id.txt');p.write_text(entry_id)
   print('Integration entry created')
  elif flow.get('type')=='form':
   Path('/home/djpi/ha-backups/lavna-tuya-setup-flow.json').write_text(json.dumps(flow))
asyncio.run(main())
