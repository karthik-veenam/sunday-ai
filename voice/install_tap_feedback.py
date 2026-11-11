import asyncio,json,datetime
from pathlib import Path
import aiohttp
async def main():
 token=json.load(open('/home/djpi/sunday/config.json'))['ha_token']
 async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
  async with s.ws_connect('ws://127.0.0.1:8123/api/websocket') as w:
   await w.receive_json();await w.send_json({'type':'auth','access_token':token});assert (await w.receive_json())['type']=='auth_ok'
   i=0
   async def call(kind,**args):
    nonlocal i
    i+=1;await w.send_json(dict(id=i,type=kind,**args))
    while True:
     r=await w.receive_json()
     if r.get('id')==i:
      assert r.get('success'),r.get('error');return r.get('result')
   resources=await call('lovelace/resources')
   stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S');Path(f'/home/djpi/ha-backups/resources-before-tap-feedback-{stamp}.json').write_text(json.dumps(resources,indent=2))
   url='/local/dashboard-tap-feedback.js?v=1'
   if not any(r.get('url')==url for r in resources):await call('lovelace/resources/create',res_type='module',url=url)
   assert any(r.get('url')==url for r in await call('lovelace/resources'))
   async with s.get('http://127.0.0.1:8123'+url) as r:
    assert r.status==200
    assert '__roomTapFeedback' in await r.text()
   print('Registered feedback module and verified HA serves it.')
asyncio.run(main())
