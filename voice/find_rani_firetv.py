import asyncio,json,aiohttp
from pathlib import Path
async def main():
 t=json.load(open('/home/djpi/sunday/config.json'))['ha_token']
 async with aiohttp.ClientSession() as s:
  async with s.ws_connect('ws://localhost:8123/api/websocket') as w:
   await w.receive_json();await w.send_json({'type':'auth','access_token':t});assert (await w.receive_json())['type']=='auth_ok'
   out={}
   for i,(key,typ,kw) in enumerate([('devices','config/device_registry/list',{}),('entities','config/entity_registry/list',{}),('states','get_states',{}),('hall','lovelace/config',{'url_path':'dashboard-hall'}),('rani','lovelace/config',{'url_path':'dashboard-rani'})],1):
    await w.send_json(dict(type=typ,id=i,**kw));r=await w.receive_json();assert r['success'],r.get('error');out[key]=r['result']
   targets=[d for d in out['devices'] if any(k in ((d.get('name') or '')+' '+(d.get('name_by_user') or '')+' '+(d.get('model') or '')).lower() for k in ['fire','rani','tv'])]
   print('DEVICES',json.dumps([{k:d.get(k) for k in ['id','name','name_by_user','manufacturer','model','area_id']} for d in targets]))
   ids={d['id'] for d in targets};ents=[e for e in out['entities'] if e.get('device_id') in ids and e['entity_id'].startswith(('media_player.','remote.'))]
   print('ENTITIES',json.dumps([{k:e.get(k) for k in ['entity_id','platform','device_id','disabled_by']} for e in ents]))
   eids={e['entity_id'] for e in ents};print('STATES',json.dumps([s for s in out['states'] if s['entity_id'] in eids]))
   Path('/home/djpi/ha-backups/rani-firetv-inventory.json').write_text(json.dumps(out,indent=2))
asyncio.run(main())
