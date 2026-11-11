import asyncio,json,shutil
from pathlib import Path
import aiohttp
async def main():
 c=json.loads(Path('/home/djpi/sunday/config.json').read_text())
 async with aiohttp.ClientSession() as session:
  async with session.ws_connect('http://127.0.0.1:8123/api/websocket') as ws:
   await ws.receive_json();await ws.send_json({'type':'auth','access_token':c['ha_token']});assert (await ws.receive_json())['type']=='auth_ok'
   count=0
   async def call(kind,**kw):
    nonlocal count
    count+=1;await ws.send_json({'id':count,'type':kind,**kw})
    while True:
     r=await ws.receive_json()
     if r.get('id')==count:
      assert r.get('success'),r.get('error');return r.get('result')
   await call('call_service',domain='rest_command',service='reload')
   path='sunday-inputs';dashboards=await call('lovelace/dashboards/list')
   if not any(d['url_path']==path for d in dashboards):await call('lovelace/dashboards/create',url_path=path,title='Sunday Inputs',icon='mdi:filter-cog-outline',show_in_sidebar=True,require_admin=True)
   resources=await call('lovelace/resources');url='/local/sunday-input-card.js?v=20261023-1'
   existing=next((r for r in resources if r['url'].split('?')[0]=='/local/sunday-input-card.js'),None)
   if existing:await call('lovelace/resources/update',resource_id=existing['id'],url=url,res_type='module')
   else:await call('lovelace/resources/create',url=url,res_type='module')
   await call('lovelace/config/save',url_path=path,config={'title':'Sunday Inputs','views':[{'title':'Inputs','path':'inputs','type':'panel','cards':[{'type':'custom:sunday-input-card'}]}]})
   result=await call('call_service',domain='rest_command',service='sunday_input_dashboard',return_response=True)
   response=result['response'];body=response['content'];body=json.loads(body) if isinstance(body,str) else body
   assert response['status']==200,response['status'];print('Dashboard ready: /sunday-inputs/inputs');print('Sources:',len(body['sources']),'Device groups:',len(body['devices']))
asyncio.run(main())
