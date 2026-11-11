import asyncio,json,os,aiohttp
from pathlib import Path
async def main():
 async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as s:
  async with s.post('https://192.168.88.116:8080/v1/FireTV/pin/verify',ssl=False,headers={'X-Api-Key':'0987654321'},json={'pin':'2893'}) as r:
   data=await r.json(content_type=None);print('Pairing HTTP:',r.status)
   if r.status!=200:print('Pairing result:',data);return
   token=data.get('description')
   if not token or token=='OK':print('No pairing token returned');return
   path=Path('/home/djpi/firetv-lan/rani-private.json')
   path.write_text(json.dumps({'host':'192.168.88.116','token':token,'name':'Rani Fire TV'}));path.chmod(0o600)
   headers={'X-Api-Key':'0987654321','X-Client-Token':token,'User-Agent':'okhttp/4.10.0'}
  async with s.get('https://192.168.88.116:8080/v1/FireTV/appsV2',ssl=False,headers=headers) as r:
   text=await r.text();print('App list HTTP:',r.status)
   if r.status==200:
    Path('/home/djpi/firetv-lan/rani-apps.json').write_text(text)
    try:
     obj=json.loads(text)
     print('App response type:',type(obj).__name__)
     if isinstance(obj,dict):print('App response fields:',list(obj))
    except ValueError:print('App list is not JSON')
  async with s.post('https://192.168.88.116:8080/v1/FireTV?action=dpad_right',ssl=False,headers=headers,json={'keyActionType':'keyDownUp'}) as r:print('Right navigation HTTP:',r.status,(await r.text())[:150])
asyncio.run(main())
