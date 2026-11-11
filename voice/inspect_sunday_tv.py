import asyncio,json,aiohttp
from pathlib import Path
async def main():
 t=json.load(open('/home/djpi/sunday/config.json'))['ha_token']
 async with aiohttp.ClientSession() as s:
  async with s.ws_connect('ws://localhost:8123/api/websocket') as w:
   await w.receive_json();await w.send_json({'type':'auth','access_token':t});assert (await w.receive_json())['type']=='auth_ok'
   out={}
   async def call(i,typ,**kw):
    await w.send_json(dict(id=i,type=typ,**kw))
    while True:
     r=await w.receive_json()
     if r.get('id')==i:
      assert r['success'],r.get('error');return r.get('result')
   out['dashboards']=await call(1,'lovelace/dashboards/list')
   out['entities']=await call(2,'config/entity_registry/list')
   out['devices']=await call(3,'config/device_registry/list')
   out['states']=await call(4,'get_states')
   print('DASHBOARDS',[(d['url_path'],d['title']) for d in out['dashboards']])
   for st in out['states']:
    text=st['entity_id']+' '+st.get('attributes',{}).get('friendly_name','')
    if any(v in text.lower() for v in ['projector','fire_tv','firetv']):print(json.dumps({k:st[k] for k in ['entity_id','state','attributes']}))
   for d in out['devices']:
    if any(v in ((d.get('name') or '')+' '+(d.get('name_by_user') or '')).lower() for v in ['projector','fire tv','firetv']):print('DEVICE',{k:d.get(k) for k in ['id','name','name_by_user','manufacturer','model','area_id']})
   Path('/home/djpi/ha-backups/sunday-tv-inventory.json').write_text(json.dumps(out,indent=2))
asyncio.run(main())
