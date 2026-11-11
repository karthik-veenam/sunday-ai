import asyncio,json,aiohttp
async def main():
 token=json.load(open('/home/djpi/sunday/config.json'))['ha_token']
 async with aiohttp.ClientSession(headers={'Authorization':'Bearer '+token}) as s:
  async with s.post('http://localhost:8123/api/config/config_entries/flow',json={'handler':'tuya_ir_ac','show_advanced_options':False}) as r:
   d=await r.json();print('Flow:',r.status,d.get('type'),d.get('message'))
   if d.get('type')!='form':return
  async with s.post('http://localhost:8123/api/config/config_entries/flow/'+d['flow_id'],json={'tuya_entry_id':'01M4ZKPC4RP7QNHENHH0348J1K','device_id':'d75a423c7fd8bd9987hv1k'}) as r:
   d=await r.json();print('Setup:',r.status,d.get('type'),d.get('errors'),d.get('reason'))
asyncio.run(main())
