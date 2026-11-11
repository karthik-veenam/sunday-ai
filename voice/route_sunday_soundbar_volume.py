import asyncio,copy,datetime,json
from pathlib import Path
from central_ac_inching import HA
async def main():
 async with HA() as h:
  path='dashboard-roka';old=await h.call('lovelace/config',url_path=path);new=copy.deepcopy(old);counts=[]
  view=next(v for v in new['views'] if v.get('path')=='sunday')
  def walk(x):
   if isinstance(x,dict):
    if x.get('icon') in ['mdi:volume-minus','mdi:volume-plus']:
     a=x.get('tap_action',{});service=a.get('perform_action',a.get('service'))
     if service in ['media_player.volume_up','media_player.volume_down']:
      assert a.get('target',{}).get('entity_id')=='media_player.karthik_s_fire_tv',a
      a['target']['entity_id']='media_player.soundbar_2';counts.append(service)
    for v in x.values():walk(v)
   elif isinstance(x,list):
    for v in x:walk(v)
  walk(view);assert sorted(counts)==['media_player.volume_down','media_player.volume_up'],counts
  Path('/home/djpi/ha-backups/before-sunday-soundbar-volume-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'.json').write_text(json.dumps(old,indent=2))
  assert await h.call('lovelace/config',url_path=path)==old
  await h.call('lovelace/config/save',url_path=path,config=new);assert await h.call('lovelace/config',url_path=path)==new
  print('Verified Sunday remote Volume Up/Down now target SmartThings media_player.soundbar_2; other controls preserved.')
  entries=await h.call('config_entries/get');print('SmartThings integration',json.dumps([{k:e.get(k) for k in ['title','state','reason']} for e in entries if e.get('domain')=='smartthings']))
asyncio.run(main())
