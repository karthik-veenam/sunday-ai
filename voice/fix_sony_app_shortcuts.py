import asyncio,copy,datetime,json
from pathlib import Path
import aiohttp
ROOT=Path('/home/djpi')
async def main():
 token=json.load(open(ROOT/'sunday/config.json'))['ha_token']
 async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=40),headers={'Authorization':'Bearer '+token}) as s:
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
    found=[]
    def walk(x):
     if isinstance(x,dict):
      a=x.get('tap_action',{})
      if a.get('perform_action')=='media_player.play_media' and a.get('target',{}).get('entity_id')=='media_player.bravia_kd_65x75k':
       data=a.get('data',{});media=data.get('media_content_id','')
       if 'hotstar' in media.lower():data['media_content_id']='JioHotstar';found.append('JioHotstar')
       elif 'youtube' in media.lower():data['media_content_id']='YouTube';found.append('YouTube')
      for value in x.values():walk(value)
     elif isinstance(x,list):
      for value in x:walk(value)
    walk(config);assert sorted(found)==['JioHotstar','YouTube'],(path,found)
   stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S');(ROOT/f'ha-backups/before-sony-app-shortcut-names-{stamp}.json').write_text(json.dumps(old,indent=2))
   for path in paths:
    assert await call('lovelace/config',url_path=path)==old[path]
    await call('lovelace/config/save',url_path=path,config=new[path]);assert await call('lovelace/config',url_path=path)==new[path]
   print('Saved and verified JioHotstar and YouTube app-name shortcuts across five Sony remote copies.')
   async with s.get('http://127.0.0.1:8123/api/states/media_player.bravia_kd_65x75k') as r:state=await r.json()
   if state['state']=='on':
    await call('call_service',domain='media_player',service='play_media',service_data={'entity_id':'media_player.bravia_kd_65x75k','media_content_type':'app','media_content_id':'JioHotstar'})
    print('Live JioHotstar launch action completed successfully.')
   else:print('TV is off; launch not tested to avoid waking it.')
asyncio.run(main())
