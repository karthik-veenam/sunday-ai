import asyncio,json
from pathlib import Path
import aiohttp
async def main():
 token=json.load(open('/home/djpi/sunday/config.json'))['ha_token'];entry=Path('/home/djpi/ha-backups/lavna-tuya-entry-id.txt').read_text()
 async with aiohttp.ClientSession(headers={'Authorization':'Bearer '+token},timeout=aiohttp.ClientTimeout(total=30)) as s:
  async with s.get('http://localhost:8123/api/diagnostics/config_entry/'+entry) as r:
   print('Diagnostic response:',r.status);r.raise_for_status();d=await r.json()
  def walk(obj):
   if isinstance(obj,dict):
    if 'category' in obj and ('name' in obj or 'product_name' in obj):
     print('Device:',{k:obj.get(k) for k in ['name','category','product_name','online']})
     print('Capability codes:',list(obj.get('status',{})) if isinstance(obj.get('status'),dict) else [])
    for k,v in obj.items():
     if k in ['device','devices','device_map']:print('Diagnostic device section:',k,'count:',len(v) if hasattr(v,'__len__') else None)
     walk(v)
   elif isinstance(obj,list):
    for v in obj:walk(v)
  walk(d)
asyncio.run(main())
