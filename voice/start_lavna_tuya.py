import asyncio,json,aiohttp
from pathlib import Path
async def main():
 token=json.load(open('/home/djpi/sunday/config.json'))['ha_token'];headers={'Authorization':'Bearer '+token}
 async with aiohttp.ClientSession(headers=headers,timeout=aiohttp.ClientTimeout(total=30)) as s:
  async with s.post('http://localhost:8123/api/config/config_entries/flow',json={'handler':'tuya','show_advanced_options':False}) as r:
   r.raise_for_status();flow=await r.json()
  print('Initial step:',flow.get('step_id'),'type:',flow.get('type'))
  if flow.get('type')=='form' and flow.get('step_id')=='user':
   async with s.post('http://localhost:8123/api/config/config_entries/flow/'+flow['flow_id'],json={'user_code':'DegQtvYMWI'}) as r:
    r.raise_for_status();flow=await r.json()
  path=Path('/home/djpi/ha-backups/lavna-tuya-setup-flow.json');path.touch(mode=0o600);path.write_text(json.dumps(flow))
  print('Next step:',flow.get('step_id'),'type:',flow.get('type'),'errors:',flow.get('errors'),'reason:',flow.get('reason'))
  print('Description placeholders:',json.dumps(flow.get('description_placeholders',{})))
  print('Schema:',json.dumps(flow.get('data_schema',[])))
asyncio.run(main())
