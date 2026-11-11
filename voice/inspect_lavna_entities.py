import asyncio,json
from pathlib import Path
import aiohttp
async def main():
 token=json.load(open('/home/djpi/sunday/config.json'))['ha_token'];entry=Path('/home/djpi/ha-backups/lavna-tuya-entry-id.txt').read_text()
 async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
  async with s.ws_connect('ws://localhost:8123/api/websocket') as w:
   await w.receive_json();await w.send_json({'type':'auth','access_token':token});assert (await w.receive_json())['type']=='auth_ok'
   results=[]
   for i,p in enumerate([{'type':'config_entries/get','domain':'tuya'},{'type':'config/device_registry/list'},{'type':'config/entity_registry/list'},{'type':'get_states'}],1):
    await w.send_json(dict(p,id=i))
    while True:
     r=await w.receive_json()
     if r.get('id')==i:break
    assert r.get('success'),r.get('error');results.append(r['result'])
   print('Entry:',[{k:e.get(k) for k in ['state','reason']} for e in results[0] if e['entry_id']==entry])
   devices=[d for d in results[1] if entry in d.get('config_entries',[])];ids={d['id'] for d in devices}
   print('Devices:',[{k:d.get(k) for k in ['id','name','manufacturer','model','model_id']} for d in devices])
   entities=[e for e in results[2] if e.get('config_entry_id')==entry or e.get('device_id') in ids];eids={e['entity_id'] for e in entities}
   print('Entities:',[{k:e.get(k) for k in ['entity_id','original_name','disabled_by','entity_category']} for e in entities])
   print('States:',[{k:e.get(k) for k in ['entity_id','state']} for e in results[3] if e['entity_id'] in eids])
   Path('/home/djpi/ha-backups/lavna-device-ids.json').write_text(json.dumps([d['id'] for d in devices]))
asyncio.run(main())
