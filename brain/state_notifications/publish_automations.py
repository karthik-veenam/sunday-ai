"""Move the explicit notification editor to its own Automations dashboard."""
import asyncio,copy,json,time
from pathlib import Path
import aiohttp
ROOT=Path('/home/djpi/sunday2-rebuild')
async def move_notifications(call):
 dashboards=await call('lovelace/dashboards/list');hub=await call('lovelace/config',url_path='sunday-hub')
 target=next((d for d in dashboards if d['url_path']=='device-automations'),None)
 automation=await call('lovelace/config',url_path='device-automations') if target else {'title':'Automations','views':[]}
 b=ROOT/'deployment/before-automations-dashboard-20261023';b.mkdir(exist_ok=True)
 backup=b/('dashboards-'+str(time.time_ns())+'.json');backup.write_text(json.dumps({'dashboards':dashboards,'sunday':hub,'automations':automation},indent=2));backup.chmod(0o600)
 notification=next((v for v in hub['views'] if v.get('path')=='notifications'),None)
 if not any(v.get('path')=='notifications' for v in automation['views']):
  view=copy.deepcopy(notification) if notification else {'title':'Device notifications','path':'notifications','icon':'mdi:bell-ring-outline','type':'panel','cards':[{'type':'custom:sunday-notification-card'}]}
  view['title']='Device notifications';automation['views'].append(view)
 if not target:await call('lovelace/dashboards/create',url_path='device-automations',title='Automations',icon='mdi:robot-outline',show_in_sidebar=True,require_admin=True)
 elif not target.get('show_in_sidebar'):await call('lovelace/dashboards/update',dashboard_id=target['id'],show_in_sidebar=True)
 await call('lovelace/config/save',url_path='device-automations',config=automation)
 saved=await call('lovelace/config',url_path='device-automations');assert any(v.get('path')=='notifications' for v in saved['views'])
 if notification:
  hub['views']=[v for v in hub['views'] if v.get('path')!='notifications']
  await call('lovelace/config/save',url_path='sunday-hub',config=hub)
 print('Automations dashboard: /device-automations/notifications; notification editor removed from Sunday')
async def main():
 c=json.loads(Path('/home/djpi/sunday/config.json').read_text())
 async with aiohttp.ClientSession() as session:
  async with session.ws_connect('http://127.0.0.1:8123/api/websocket') as ws:
   await ws.receive_json();await ws.send_json({'type':'auth','access_token':c['ha_token']});assert (await ws.receive_json())['type']=='auth_ok';counter=0
   async def call(kind,**kw):
    nonlocal counter
    counter+=1;await ws.send_json({'id':counter,'type':kind,**kw})
    while True:
     r=await ws.receive_json()
     if r.get('id')==counter:
      assert r.get('success'),r.get('error');return r.get('result')
   await move_notifications(call)
asyncio.run(main()) if __name__=='__main__' else None
