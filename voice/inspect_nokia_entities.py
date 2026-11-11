import asyncio,json,aiohttp
from pathlib import Path
async def main():
 t=json.load(open('/home/djpi/sunday/config.json'))['ha_token'];entry=json.load(open('/home/djpi/bedroom-dashboards/rani-nokia-entry.json'))['entry_id']
 async with aiohttp.ClientSession() as s:
  async with s.ws_connect('ws://localhost:8123/api/websocket') as w:
   await w.receive_json();await w.send_json({'type':'auth','access_token':t});assert (await w.receive_json())['type']=='auth_ok'
   await w.send_json({'type':'config/entity_registry/list','id':1});r=await w.receive_json();entities=[e for e in r['result'] if e.get('config_entry_id')==entry]
   await w.send_json({'type':'get_states','id':2});r=await w.receive_json();ids={e['entity_id'] for e in entities}
   for state in r['result']:
    if state['entity_id'] in ids:print(json.dumps({k:state[k] for k in ['entity_id','state','attributes']}))
   Path('/home/djpi/bedroom-dashboards/rani-nokia-entities.json').write_text(json.dumps([{'entity_id':e['entity_id'],'device_id':e.get('device_id')} for e in entities],indent=2)+'\n')
asyncio.run(main())
