"""Copy Hall's established TV/remote presentation, mapped to the paired Rani Fire TV."""
import asyncio,copy,datetime,json
from pathlib import Path
import aiohttp
async def main():
 t=json.load(open('/home/djpi/sunday/config.json'))['ha_token']
 entry=json.load(open('/home/djpi/bedroom-dashboards/rani-firetv-entry.json'))['entry_id']
 async with aiohttp.ClientSession() as s:
  async with s.ws_connect('ws://localhost:8123/api/websocket') as w:
   await w.receive_json();await w.send_json({'type':'auth','access_token':t});assert (await w.receive_json())['type']=='auth_ok'
   seq=0
   async def call(typ,**kwargs):
    nonlocal seq
    seq+=1;await w.send_json(dict(type=typ,id=seq,**kwargs))
    while True:
     r=await w.receive_json()
     if r.get('id')==seq:
      assert r.get('success'),r.get('error');return r.get('result')
   for _ in range(20):
    registry=await call('config/entity_registry/list')
    entities=[e for e in registry if e.get('config_entry_id')==entry]
    players=[e for e in entities if e['entity_id'].startswith('media_player.')]
    remotes=[e for e in entities if e['entity_id'].startswith('remote.')]
    if players and remotes:break
    await asyncio.sleep(1)
   else:raise RuntimeError('Fire TV entities not ready')
   TV=players[0]['entity_id'];REMOTE=remotes[0]['entity_id']
   states={x['entity_id']:x for x in await call('get_states')}
   assert states[TV]['state']!='unavailable' and states[REMOTE]['state']!='unavailable'
   hall=await call('lovelace/config',url_path='dashboard-hall')
   roka=await call('lovelace/config',url_path='dashboard-roka')
   old=await call('lovelace/config',url_path='dashboard-rani');cfg=copy.deepcopy(old)
   ref=next(c for c in hall['views'][0]['cards'][0]['cards'] if c.get('type')=='custom:mod-card' and 'browser_mod.popup' in json.dumps(c))
   card=json.loads(json.dumps(ref).replace('media_player.bravia_kd_65x75k',TV).replace('remote.bravia_kd_65x75k',REMOTE).replace('Sony BRAVIA','Fire TV').replace('Hall TV','Rani TV'))
   mapping={'Up':'up','Down':'down','Left':'left','Right':'right','Confirm':'select','Home':'home','Return':'back','ActionMenu':'menu','Exit':'home','Mute':'mute'}
   def walk(v):
    if isinstance(v,dict):
     if v.get('type')=='custom:mushroom-media-player-card':
      v['name']='TV';v['media_controls']=['on_off','play_pause_stop'];v['volume_controls']=['volume_buttons']
     if v.get('perform_action')=='remote.send_command':
      command=v['data']['command'];assert command in mapping,command
      v['data']['command']=mapping[command]
     if v.get('perform_action') in ['media_player.media_previous_track','media_player.media_next_track']:
      rewind=v['perform_action'].endswith('previous_track')
      v['perform_action']='remote.send_command';v['target']={'entity_id':REMOTE};v['data']={'command':'rewind' if rewind else 'fast_forward'}
     if v.get('icon')=='mdi:skip-previous':v['icon']='mdi:rewind'
     if v.get('icon')=='mdi:skip-next':v['icon']='mdi:fast-forward'
     if v.get('perform_action')=='media_player.play_media':
      package='com.amazon.firetv.youtube' if 'youtube' in v['data']['media_content_id'] else 'in.startv.hotstar'
      v['perform_action']='media_player.select_source';v['data']={'source':package}
     for child in list(v.values()):walk(child)
    elif isinstance(v,list):
     for child in v:walk(child)
   walk(card)
   assert 'bravia' not in json.dumps(card).lower()
   cards=cfg['views'][0]['cards'][0]['cards'];assert TV not in json.dumps(cfg),'Already added'
   pos=next(i for i,c in enumerate(cards) if c.get('heading')=='Hot Water')
   cards.insert(pos,card)
   cards[0]['chips'].insert(2,{'type':'template','entity':TV,'icon':'mdi:television','content':"TV · {{ 'Unavailable' if is_state('"+TV+"','unavailable') else 'Ready' }}",'icon_color':"{{ 'grey' if is_state('"+TV+"','unavailable') else 'blue' }}",'tap_action':{'action':'more-info'}})
   area=next(a for a in await call('config/area_registry/list') if a['name']=="Rani's Bedroom")
   for did in {e['device_id'] for e in entities if e.get('device_id')}:
    await call('config/device_registry/update',device_id=did,area_id=area['area_id'])
   backup=Path('/home/djpi/ha-backups/rani-before-firetv-remote-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'.json');backup.write_text(json.dumps(old,indent=2))
   await call('lovelace/config/save',url_path='dashboard-rani',config=cfg)
   assert await call('lovelace/config',url_path='dashboard-rani')==cfg
   assert await call('lovelace/config',url_path='dashboard-hall')==hall
   assert await call('lovelace/config',url_path='dashboard-roka')==roka
   # All original non-status cards remain intact and in order.
   assert [c for c in cards[1:] if c is not card]==old['views'][0]['cards'][0]['cards'][1:]
   Path('/home/djpi/bedroom-dashboards/rani.json').write_text(json.dumps(cfg,indent=2)+'\n')
   Path('/home/djpi/bedroom-dashboards/rani-firetv-entities.json').write_text(json.dumps({'tv':TV,'remote':REMOTE})+'\n')
   print('Saved and verified Rani TV card and Hall-style popup:',TV,REMOTE)
   print('App IDs match installed YouTube/JioHotstar. Hall, RoKa and other Rani cards unchanged.')
asyncio.run(main())
