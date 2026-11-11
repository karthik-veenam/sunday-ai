import asyncio,copy,datetime,json
from pathlib import Path
import aiohttp
async def main():
 token=json.load(open('/home/djpi/sunday/config.json'))['ha_token']
 async with aiohttp.ClientSession() as s:
  async with s.ws_connect('ws://localhost:8123/api/websocket') as w:
   await w.receive_json();await w.send_json({'type':'auth','access_token':token});assert (await w.receive_json())['type']=='auth_ok'
   seq=0
   async def call(t,**kw):
    nonlocal seq
    seq+=1;await w.send_json(dict(type=t,id=seq,**kw))
    while True:
     r=await w.receive_json()
     if r.get('id')==seq:
      assert r.get('success'),r.get('error')
      return r.get('result')
   old=await call('lovelace/config',url_path='dashboard-rani')
   Path('/home/djpi/ha-backups/rani-before-geyser-simplification-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'.json').write_text(json.dumps(old,indent=2))
   cfg=copy.deepcopy(old)
   cards=cfg['views'][0]['cards'][0]['cards']
   found=0
   for c in cards:
    inner=c.get('card',{}).get('cards',[])
    if inner and inner[0].get('entity')=='switch.mom_s_geyser_socket_1':
     inner[0]['primary']='Geyser'
     c['card']['cards']=[x for x in inner if x.get('type')!='grid']
     found+=1
   assert found==1
   assert 'sensor.mom_s_geyser_' not in json.dumps(cfg)
   await call('lovelace/config/save',url_path='dashboard-rani',config=cfg)
   assert await call('lovelace/config',url_path='dashboard-rani')==cfg
   Path('/home/djpi/bedroom-dashboards/rani.json').write_text(json.dumps(cfg,indent=2)+'\n')
   print('Verified: Geyser title, status and power control retained; power/energy readings removed.')
asyncio.run(main())
