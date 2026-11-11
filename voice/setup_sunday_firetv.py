import asyncio,json,aiohttp
from pathlib import Path
async def main():
 t=json.load(open('/home/djpi/sunday/config.json'))['ha_token']
 async with aiohttp.ClientSession(headers={'Authorization':'Bearer '+t},timeout=aiohttp.ClientTimeout(total=55)) as s:
  async with s.post('http://localhost:8123/api/config/config_entries/flow',json={'handler':'androidtv','show_advanced_options':True}) as r:
   d=await r.json();assert d.get('type')=='form',d
  data={'host':'192.168.88.35','port':5555,'device_class':'firetv','more_options':{'adbkey':'/config/.storage/sunday_firetv_adbkey'}}
  async with s.post('http://localhost:8123/api/config/config_entries/flow/'+d['flow_id'],json=data) as r:
   d=await r.json();print('Setup result:',d.get('type'),'errors:',d.get('errors'),'reason:',d.get('reason'))
   if d.get('type')=='create_entry':
    print('Created native Fire TV integration:',d['result']['entry_id'])
    Path('/home/djpi/bedroom-dashboards/sunday-firetv-entry.json').write_text(json.dumps({'entry_id':d['result']['entry_id']})+'\n')
asyncio.run(main())
