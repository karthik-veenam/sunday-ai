import asyncio,json,aiohttp
async def main():
 token=json.load(open('/home/djpi/sunday/config.json'))['ha_token']
 async with aiohttp.ClientSession(headers={'Authorization':'Bearer '+token}) as s:
  url='http://localhost:8123/api/diagnostics/config_entry/01M4ZKPC4RP7QNHENHH0348J1K/device/853f92e62c25bb463d3f619d9cdb6111'
  async with s.get(url) as r:
   print('HTTP',r.status)
   data=await r.json()
   def walk(obj):
    if isinstance(obj,dict):
     for k,v in obj.items():
      if k in ['category','status','status_range','function','online','name','model','support_local']:print(k,json.dumps(v))
      elif isinstance(v,(dict,list)):walk(v)
    elif isinstance(obj,list):
     for v in obj:walk(v)
   walk(data)
asyncio.run(main())
