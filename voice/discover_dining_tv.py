import asyncio,json,aiohttp
async def main():
 token=json.load(open('/home/djpi/sunday/config.json'))['ha_token']
 async with aiohttp.ClientSession() as s:
  async with s.ws_connect('ws://localhost:8123/api/websocket') as w:
   await w.receive_json();await w.send_json({'type':'auth','access_token':token});assert (await w.receive_json())['type']=='auth_ok'
   for i,p in enumerate([{'type':'config_entries/flow/progress'},{'type':'config_entries/get','domain':'androidtv_remote'},{'type':'get_states'}],1):
    await w.send_json(dict(p,id=i))
    while True:
     r=await w.receive_json()
     if r.get('id')==i:break
    if i<3:print(json.dumps(r))
    else:print('Media players:',[{k:e.get(k) for k in ['entity_id','state']}|{'name':e.get('attributes',{}).get('friendly_name')} for e in r.get('result',[]) if e['entity_id'].startswith('media_player.')])
asyncio.run(main())
