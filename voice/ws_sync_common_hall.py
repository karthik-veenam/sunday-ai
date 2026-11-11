"""Sync production Hall into Common Areas without changing its other tabs."""
import asyncio,copy,datetime,json
from pathlib import Path
import aiohttp

async def main():
 token=json.load(open('/home/djpi/sunday/config.json'))['ha_token']
 async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
  async with s.ws_connect('ws://localhost:8123/api/websocket') as w:
   await w.receive_json(); await w.send_json({'type':'auth','access_token':token}); assert (await w.receive_json())['type']=='auth_ok'
   seq=0
   async def call(payload):
    nonlocal seq
    seq+=1; await w.send_json(dict(payload,id=seq))
    while True:
     r=await w.receive_json()
     if r.get('id')==seq:
      assert r.get('success'),r.get('error')
      return r.get('result')
   hall=await call({'type':'lovelace/config','url_path':'dashboard-hall'})
   original=await call({'type':'lovelace/config','url_path':'dashboard-common-areas'})
   backup=Path('/home/djpi/ha-backups')/('common-areas-before-hall-sync-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'.json')
   backup.write_text(json.dumps(original,indent=2)+'\n')
   updated=copy.deepcopy(original)
   indices=[i for i,v in enumerate(updated['views']) if v.get('path')=='hall']
   assert len(indices)==1
   idx=indices[0]; previous=updated['views'][idx]
   replacement=copy.deepcopy(hall['views'][0])
   for k in ('path','title'):
    replacement[k]=previous[k]
   updated['views'][idx]=replacement
   assert all(v==updated['views'][i] for i,v in enumerate(original['views']) if i!=idx)
   await call({'type':'lovelace/config/save','url_path':'dashboard-common-areas','config':updated})
   assert await call({'type':'lovelace/config','url_path':'dashboard-common-areas'})==updated
   print('Updated and verified Common Areas Hall tab; other tabs unchanged. Backup:',backup)
if __name__=='__main__':asyncio.run(main())
