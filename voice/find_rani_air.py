import asyncio,json,aiohttp
from pathlib import Path
async def main():
 token=json.load(open('/home/djpi/sunday/config.json'))['ha_token']
 async with aiohttp.ClientSession() as s:
  async with s.ws_connect('ws://localhost:8123/api/websocket') as w:
   await w.receive_json();await w.send_json({'type':'auth','access_token':token});assert (await w.receive_json())['type']=='auth_ok'
   out={}
   for i,(key,p) in enumerate([('devices',{'type':'config/device_registry/list'}),('entities',{'type':'config/entity_registry/list'}),('states',{'type':'get_states'}),('dashboard',{'type':'lovelace/config','url_path':'dashboard-rani'})],1):
    await w.send_json(dict(p,id=i))
    while True:
     r=await w.receive_json()
     if r.get('id')==i:break
    assert r.get('success'),r.get('error');out[key]=r['result']
   selected=[d for d in out['devices'] if any(t in ((d.get('name') or '')+' '+(d.get('name_by_user') or '')).lower() for t in ['rani','air'])]
   ids={d['id'] for d in selected}
   print('DEVICES:',[{k:d.get(k) for k in ['id','name','name_by_user','manufacturer','model','area_id']} for d in selected])
   ents=[e for e in out['entities'] if e.get('device_id') in ids or any(t in e['entity_id'].lower() for t in ['rani','air'])]
   print('ENTITIES:',[{k:e.get(k) for k in ['entity_id','name','original_name','device_id','disabled_by','platform']} for e in ents])
   eids={e['entity_id'] for e in ents};print('STATES:',[e for e in out['states'] if e['entity_id'] in eids])
   p=Path('/home/djpi/ha-backups/rani-air-inventory.json');p.write_text(json.dumps(out,indent=2))
asyncio.run(main())
