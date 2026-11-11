import asyncio,json,aiohttp,time
async def main():
 token=json.load(open('/home/djpi/sunday/config.json'))['ha_token']
 async with aiohttp.ClientSession() as s:
  async with s.ws_connect('ws://localhost:8123/api/websocket') as w:
   await w.receive_json();await w.send_json({'type':'auth','access_token':token});assert (await w.receive_json())['type']=='auth_ok'
   await w.send_json({'id':1,'type':'subscribe_events','event_type':'call_service'});assert (await w.receive_json())['success']
   print('LISTENING for dashboard power actions',flush=True)
   end=time.monotonic()+180
   while time.monotonic()<end:
    try:r=await asyncio.wait_for(w.receive_json(),end-time.monotonic())
    except asyncio.TimeoutError:break
    event=r.get('event',{});d=event.get('data',{})
    if d.get('domain') in ['media_player','remote','script','firetv_lan']:
     data=d.get('service_data',{})
     print(json.dumps({'time':event.get('time_fired'),'domain':d.get('domain'),'service':d.get('service'),'data':{k:data.get(k) for k in ['entity_id','command','device_id'] if k in data}}),flush=True)
asyncio.run(main())
