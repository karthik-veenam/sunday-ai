import asyncio,copy,datetime,json
from pathlib import Path
import aiohttp
ROOT=Path('/home/djpi')
PATHS=['dashboard-roka','dashboard-rani','dashboard-sian','dashboard-hall','dashboard-common-areas']
async def main():
 token=json.load(open(ROOT/'sunday/config.json'))['ha_token']
 async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=35)) as s:
  async with s.ws_connect('ws://127.0.0.1:8123/api/websocket') as w:
   await w.receive_json();await w.send_json({'type':'auth','access_token':token});assert (await w.receive_json())['type']=='auth_ok';i=0
   async def call(kind,**kw):
    nonlocal i
    i+=1;await w.send_json(dict(id=i,type=kind,**kw))
    while True:
     r=await w.receive_json()
     if r.get('id')==i:
      assert r.get('success'),r.get('error');return r.get('result')
   original={p:await call('lovelace/config',url_path=p) for p in PATHS};changed=copy.deepcopy(original)
   total=0
   for path,config in changed.items():
    found=[]
    def walk(x):
     if isinstance(x,dict):
      action=x.get('tap_action',{});target=action.get('target',{}).get('entity_id')
      if x.get('type')=='button' and target=='media_player.bravia_kd_65x75k' and action.get('perform_action') in ['media_player.volume_up','media_player.volume_down']:
       command='VolumeUp' if action['perform_action'].endswith('volume_up') else 'VolumeDown'
       x['tap_action']={'action':'perform-action','perform_action':'remote.send_command','target':{'entity_id':'remote.bravia_kd_65x75k'},'data':{'command':command,'num_repeats':1}}
       found.append(command)
      for value in x.values():walk(value)
     elif isinstance(x,list):
      for value in x:walk(value)
    walk(config)
    assert sorted(found)==['VolumeDown','VolumeUp'],(path,found)
    # Reversing the two action changes must reproduce the whole original config.
    restored=copy.deepcopy(config)
    def restore(x):
     if isinstance(x,dict):
      a=x.get('tap_action',{})
      if a.get('target',{}).get('entity_id')=='remote.bravia_kd_65x75k' and a.get('data',{}).get('command') in ['VolumeUp','VolumeDown'] and a.get('data',{}).get('num_repeats')==1:
       service='media_player.volume_up' if a['data']['command']=='VolumeUp' else 'media_player.volume_down'
       x['tap_action']={'action':'perform-action','perform_action':service,'target':{'entity_id':'media_player.bravia_kd_65x75k'}}
      for y in x.values():restore(y)
     elif isinstance(x,list):
      for y in x:restore(y)
    restore(restored);assert restored==original[path],path
    total+=len(found)
   stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S');(ROOT/f'ha-backups/before-sony-volume-keys-{stamp}.json').write_text(json.dumps(original,indent=2))
   for path in PATHS:
    assert await call('lovelace/config',url_path=path)==original[path]
    await call('lovelace/config/save',url_path=path,config=changed[path]);assert await call('lovelace/config',url_path=path)==changed[path]
   print('Verified',total,'Sony volume buttons across',len(PATHS),'dashboards; only the two volume actions changed in each. Fire TV untouched.')
asyncio.run(main())
