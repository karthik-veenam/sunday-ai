import asyncio,copy,datetime,json
from pathlib import Path
import aiohttp
async def main():
 token=json.load(open('/home/djpi/sunday/config.json'))['ha_token'];entry=json.load(open('/home/djpi/bedroom-dashboards/rani-firetv-entry.json'))['entry_id']
 async with aiohttp.ClientSession(headers={'Authorization':'Bearer '+token},timeout=aiohttp.ClientTimeout(total=20)) as s:
  for _ in range(30):
   try:
    async with s.post('http://localhost:8123/api/config/config_entries/options/flow',json={'handler':entry}) as r:
     flow=await r.json()
     if flow.get('type')=='form':break
   except (aiohttp.ClientError,ValueError):pass
   await asyncio.sleep(2)
  else:raise RuntimeError('Power options not ready')
  async with s.post('http://localhost:8123/api/config/config_entries/options/flow/'+flow['flow_id'],json={'tv_media_player':'media_player.swtv_2','tv_remote':'remote.swtv'}) as r:
   result=await r.json();assert result.get('type')=='create_entry',result;print('Linked Fire TV power controls to Nokia.',flush=True)
  async with s.ws_connect('ws://localhost:8123/api/websocket') as w:
   await w.receive_json();await w.send_json({'type':'auth','access_token':token});assert (await w.receive_json())['type']=='auth_ok'
   seq=0
   async def call(typ,**kw):
    nonlocal seq
    seq+=1;await w.send_json(dict(type=typ,id=seq,**kw))
    while True:
     r=await w.receive_json()
     if r.get('id')==seq:
      assert r.get('success'),r.get('error');return r.get('result')
   old=await call('lovelace/config',url_path='dashboard-rani');cfg=copy.deepcopy(old)
   card=next(c for c in cfg['views'][0]['cards'][0]['cards'] if 'browser_mod.popup' in json.dumps(c))
   header,controls=card['card']['cards']
   old_remote=copy.deepcopy(controls['chips'][-1])
   assert controls['chips'][0].get('content')=='TV On'
   controls['chips']=controls['chips'][1:]
   controls['card_mod']['style']=controls['card_mod']['style'].replace('width: 148px;','width: 40px;')
   header['card_mod']['style']=header['card_mod']['style'].replace('padding-right: 164px','padding-right: 52px')
   header['media_controls']=['on_off','play_pause_stop'];header['collapsible_controls']=False
   assert controls['chips']==[old_remote]
   Path('/home/djpi/ha-backups/rani-before-unified-nokia-power-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'.json').write_text(json.dumps(old,indent=2))
   await call('lovelace/config/save',url_path='dashboard-rani',config=cfg)
   assert await call('lovelace/config',url_path='dashboard-rani')==cfg
   Path('/home/djpi/bedroom-dashboards/rani.json').write_text(json.dumps(cfg,indent=2)+'\n')
   print('Verified: separate TV On removed; normal card power restored; popup unchanged, with its existing power now routed to Nokia.',flush=True)
asyncio.run(main())
