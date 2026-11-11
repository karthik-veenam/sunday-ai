import asyncio,copy,datetime,json
from pathlib import Path
import aiohttp
ROOT=Path('/home/djpi')
async def main():
 token=json.load(open(ROOT/'sunday/config.json'))['ha_token']
 async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=35)) as s:
  async with s.ws_connect('ws://127.0.0.1:8123/api/websocket') as w:
   await w.receive_json();await w.send_json({'type':'auth','access_token':token});assert (await w.receive_json())['type']=='auth_ok';n=0
   async def call(kind,**kw):
    nonlocal n
    n+=1;await w.send_json(dict(id=n,type=kind,**kw))
    while True:
     r=await w.receive_json()
     if r.get('id')==n:
      assert r.get('success'),r.get('error');return r.get('result')
   paths=['dashboard-roka','dashboard-rani','dashboard-sian']
   originals={p:await call('lovelace/config',url_path=p) for p in paths}
   resources=await call('lovelace/resources')
   stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S');(ROOT/f'ha-backups/before-swipe-config-{stamp}.json').write_text(json.dumps({'dashboards':originals,'resources':resources},indent=2))
   for path,old in originals.items():
    new=copy.deepcopy(old)
    new.setdefault('swipe_nav',{}).update({'enable':True,'prevent_default':True,'swipe_amount':12,'animate':'none','wrap':True,'skip_hidden':True,'skip_subviews':True})
    assert await call('lovelace/config',url_path=path)==old
    await call('lovelace/config/save',url_path=path,config=new)
    assert await call('lovelace/config',url_path=path)==new
   resource=next(x for x in resources if '/hass-swipe-navigation/swipe-navigation.js' in x.get('url',''))
   url=resource['url'].split('&webview=')[0]+'&webview=2'
   await call('lovelace/resources/update',resource_id=resource['id'],url=url,res_type='module')
   print('Verified explicit touch-swipe configuration on all three dashboards and refreshed HACS resource version.')
asyncio.run(main())
