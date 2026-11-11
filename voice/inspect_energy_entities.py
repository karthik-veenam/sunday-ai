import asyncio,json,aiohttp
async def main():
 token=json.load(open('/home/djpi/sunday/config.json'))['ha_token']
 async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
  async with s.ws_connect('ws://localhost:8123/api/websocket') as w:
   await w.receive_json();await w.send_json({'type':'auth','access_token':token});assert (await w.receive_json())['type']=='auth_ok'
   result=[]
   for i,p in enumerate([{'type':'get_states'},{'type':'config/entity_registry/list'},{'type':'energy/get_prefs'},{'type':'lovelace/dashboards/list'}],1):
    await w.send_json(dict(p,id=i))
    while True:
     r=await w.receive_json()
     if r.get('id')==i:break
    result.append(r.get('result'))
   states=result[0] or []
   for e in states:
    a=e.get('attributes',{})
    if a.get('device_class') in ['power','energy','current','voltage'] or a.get('unit_of_measurement') in ['W','kW','Wh','kWh','A','V']:
     print(json.dumps({'entity':e['entity_id'],'state':e['state'],'name':a.get('friendly_name'),'unit':a.get('unit_of_measurement'),'device_class':a.get('device_class'),'state_class':a.get('state_class')},ensure_ascii=False))
   print('DISABLED CANDIDATES:',[{k:e.get(k) for k in ['entity_id','disabled_by','original_name']} for e in (result[1] or []) if e.get('disabled_by') and any(t in e['entity_id'] for t in ['power','energy','current','voltage','consumption'])])
   print('ENERGY PREFS:',json.dumps(result[2]))
   print('DASHBOARDS:',[{k:d.get(k) for k in ['url_path','title']} for d in (result[3] or [])])
asyncio.run(main())
