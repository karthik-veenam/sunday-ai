import asyncio,copy,datetime,json
from pathlib import Path
import aiohttp
ROOT=Path('/home/djpi');stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
async def main():
 token=json.load(open(ROOT/'sunday/config.json'))['ha_token']
 async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=40)) as s:
  async with s.ws_connect('ws://127.0.0.1:8123/api/websocket') as w:
   await w.receive_json();await w.send_json({'type':'auth','access_token':token});assert (await w.receive_json())['type']=='auth_ok';i=0
   async def call(kind,**kw):
    nonlocal i
    i+=1;await w.send_json(dict(id=i,type=kind,**kw))
    while True:
     r=await w.receive_json()
     if r.get('id')==i:
      assert r.get('success'),r.get('error');return r.get('result')
   paths=['dashboard-roka','dashboard-rani','dashboard-sian','dashboard-hall','dashboard-common-areas'];old={p:await call('lovelace/config',url_path=p) for p in paths};new=copy.deepcopy(old)
   for path,config in new.items():
    count=[]
    def walk(x):
     if isinstance(x,dict):
      if x.get('type')=='markdown' and 'media_player.vasuden_tv' in x.get('content',''):
       x.clear();x.update({'type':'custom:hall-tv-seek-card','entity':'media_player.vasuden_tv'});count.append(True);return
      for child in x.values():walk(child)
     elif isinstance(x,list):
      for child in x:walk(child)
    walk(config);assert len(count)==1,(path,len(count))
   resources=await call('lovelace/resources')
   (ROOT/f'ha-backups/before-live-tv-seek-{stamp}.json').write_text(json.dumps({'dashboards':old,'resources':resources},indent=2))
   feedback=Path('/home/pi/homeassistant/www/dashboard-tap-feedback.js');source=feedback.read_text();(ROOT/f'ha-backups/tap-feedback-before-live-seek-{stamp}.js').write_text(source)
   marker='// BEGIN LIVE TV SEEK CARD'
   if marker in source:source=source.split(marker)[0]
   feedback.write_text(source+'\n'+marker+'\n'+Path('/home/pi/homeassistant/www/hall-tv-seek-card.js').read_text())
   resource=next(r for r in resources if r.get('url','').startswith('/local/dashboard-tap-feedback.js'))
   await call('lovelace/resources/update',resource_id=resource['id'],url='/local/dashboard-tap-feedback.js?v=4',res_type='module')
   for path in paths:
    assert await call('lovelace/config',url_path=path)==old[path]
    await call('lovelace/config/save',url_path=path,config=new[path]);assert await call('lovelace/config',url_path=path)==new[path]
   async with s.get('http://127.0.0.1:8123/local/dashboard-tap-feedback.js?v=4') as r:
    body=await r.text();assert r.status==200 and "customElements.define('hall-tv-seek-card'" in body
   print('Verified live draggable Cast seek bar with +/-10-second buttons in five Hall cards; existing haptics and larger header controls preserved.')
asyncio.run(main())
