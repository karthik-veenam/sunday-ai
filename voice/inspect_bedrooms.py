import asyncio,json,aiohttp
from pathlib import Path
async def main():
 token=json.load(open('/home/djpi/sunday/config.json'))['ha_token']
 async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
  async with s.ws_connect('ws://localhost:8123/api/websocket') as w:
   await w.receive_json();await w.send_json({'type':'auth','access_token':token});assert (await w.receive_json())['type']=='auth_ok'
   seq=0
   async def call(p):
    nonlocal seq
    seq+=1;await w.send_json(dict(p,id=seq))
    while True:
     r=await w.receive_json()
     if r.get('id')==seq:
      assert r.get('success'),r.get('error');return r.get('result')
   out={}
   for key,p in [('areas',{'type':'config/area_registry/list'}),('devices',{'type':'config/device_registry/list'}),('entities',{'type':'config/entity_registry/list'}),('states',{'type':'get_states'}),('dashboards',{'type':'lovelace/dashboards/list'})]:out[key]=await call(p)
   for path in ['dashboard-roka','dashboard-mobile','dashboard-common-areas']:
    out[path]=await call({'type':'lovelace/config','url_path':path})
   path=Path('/home/djpi/ha-backups/bedroom-dashboard-inventory.json');path.write_text(json.dumps(out,indent=2))
   print('Areas:',out['areas'])
   print('Dashboards:',[{k:d.get(k) for k in ['title','url_path']} for d in out['dashboards']])
   devices={d['id']:d for d in out['devices']}
   for e in out['entities']:
    d=devices.get(e.get('device_id'),{});a=e.get('area_id') or d.get('area_id');eid=e['entity_id']
    if any(t in (eid+' '+str(a)+' '+str(d.get('name'))).lower() for t in ['roka','rani','sian','room_9','room_10','room_11']):
     print('Entity:',json.dumps({k:e.get(k) for k in ['entity_id','name','original_name','disabled_by']}|{'area':a,'device':d.get('name')},ensure_ascii=False))
   for key in ['dashboard-roka','dashboard-mobile']:
    print(key,json.dumps(out[key],ensure_ascii=False)[:14000])
asyncio.run(main())
