import asyncio,copy,datetime,json
from pathlib import Path
import aiohttp
async def main():
 t=json.load(open('/home/djpi/sunday/config.json'))['ha_token']
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
   old=await call('lovelace/config',url_path='dashboard-rani');cfg=copy.deepcopy(old)
   cards=cfg['views'][0]['cards'][0]['cards']
   card=next(c for c in cards if 'browser_mod.popup' in json.dumps(c))
   header,controls=card['card']['cards']
   assert len(controls['chips'])==1,'Power button already added'
   old_remote=copy.deepcopy(controls['chips'][0])
   controls['chips'].insert(0,{'type':'template','entity':'remote.rani_fire_tv','icon':'mdi:power','icon_color':'#174d83','content':'TV On','tap_action':{'action':'perform-action','perform_action':'remote.send_command','target':{'entity_id':'remote.rani_fire_tv'},'data':{'command':'home'}},'hold_action':{'action':'none'}})
   controls['card_mod']['style']=controls['card_mod']['style'].replace('width: 40px;','width: 148px;')
   header['card_mod']['style']=header['card_mod']['style'].replace('padding-right: 52px','padding-right: 164px')
   header['media_controls']=[x for x in header.get('media_controls',[]) if x!='on_off']
   assert controls['chips'][1]==old_remote,'Remote popup changed'
   assert {i for i,(a,b) in enumerate(zip(old['views'][0]['cards'][0]['cards'],cards)) if a!=b}=={cards.index(card)},'Unrelated cards changed'
   area=next(a for a in await call('config/area_registry/list') if a['name']=="Rani's Bedroom")
   entry=json.load(open('/home/djpi/bedroom-dashboards/rani-nokia-entry.json'))['entry_id']
   registry=await call('config/entity_registry/list')
   for did in {e['device_id'] for e in registry if e.get('config_entry_id')==entry and e.get('device_id')}:
    await call('config/device_registry/update',device_id=did,area_id=area['area_id'],name_by_user='Rani Nokia TV')
   backup=Path('/home/djpi/ha-backups/rani-before-nokia-tv-on-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'.json');backup.write_text(json.dumps(old,indent=2))
   await call('lovelace/config/save',url_path='dashboard-rani',config=cfg)
   assert await call('lovelace/config',url_path='dashboard-rani')==cfg
   Path('/home/djpi/bedroom-dashboards/rani.json').write_text(json.dumps(cfg,indent=2)+'\n')
   print('Saved and verified TV On action beside remote icon. Existing popup unchanged. Nokia assigned to Rani area.')
asyncio.run(main())
