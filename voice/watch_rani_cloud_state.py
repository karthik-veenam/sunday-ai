import asyncio,datetime,json,aiohttp
async def main():
 token=json.load(open('/home/djpi/sunday/config.json'))['ha_token']
 url='http://localhost:8123/api/diagnostics/config_entry/01M4ZKPC4RP7QNHENHH0348J1K/device/853f92e62c25bb463d3f619d9cdb6111'
 previous=None
 def find(x):
  if isinstance(x,dict):
   if x.get('category')=='infrared_ac' and x.get('name')=='Rani Air':return x.get('status')
   for v in x.values():
    r=find(v)
    if r is not None:return r
  elif isinstance(x,list):
   for v in x:
    r=find(v)
    if r is not None:return r
 async with aiohttp.ClientSession(headers={'Authorization':'Bearer '+token}) as s:
  for _ in range(24):
   async with s.get(url) as r:
    if r.status==200:
     status=find(await r.json())
     if status!=previous:
      print(datetime.datetime.now().isoformat(timespec='seconds'),status,flush=True);previous=status
   await asyncio.sleep(2)
 print('Final cloud status:',previous,flush=True)
asyncio.run(main())
