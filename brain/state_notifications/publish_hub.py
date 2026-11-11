"""Copy existing Sunday views into one hub; keep original URLs as hidden backups."""
import asyncio,copy,json,os
from pathlib import Path
import aiohttp
ROOT=Path('/home/djpi/sunday2-rebuild');BACKUP=ROOT/'deployment/before-notification-hub-20261023'
SOURCES=[('dashboard-mobile',[('bedroom','home','Home','mdi:home-assistant')]),('sunday-inputs',[('inputs','inputs','Inputs','mdi:filter-cog-outline')]),('sunday-memory',[('inbox','memory-inbox','Memory inbox','mdi:inbox'),('consolidated','memories','Memories','mdi:brain')]),('sunday-costs',[('overview','costs','Costs','mdi:chart-donut'),('usage','usage','Usage & models','mdi:chart-line')]),('sunday-wake-feedback',[('review','wake','Wake feedback','mdi:microphone-message')]),('device-network',[('status','network','Network','mdi:lan-connect')]),('dashboard-qwen',[('ask','local','Local model','mdi:chat-processing-outline')])]
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
   dashboards=await call('lovelace/dashboards/list');configs={}
   for path,_ in SOURCES:configs[path]=await call('lovelace/config',url_path=path)
   backup=BACKUP/'ha-dashboards.json'
   if not backup.exists():backup.write_text(json.dumps({'dashboards':dashboards,'configs':configs},indent=2));backup.chmod(0o600)
   resources=await call('lovelace/resources');url='/local/sunday-notification-card.js?v=20261023-search2';existing=next((r for r in resources if r['url'].split('?')[0]=='/local/sunday-notification-card.js'),None)
   if existing:await call('lovelace/resources/update',resource_id=existing['id'],url=url,res_type='module')
   else:await call('lovelace/resources/create',url=url,res_type='module')
   await call('call_service',domain='rest_command',service='reload')
   routes={}
   for path,views in SOURCES:
    for old,new,_,_ in views:routes['/'+path+'/'+old]='/sunday-hub/'+new
    routes['/'+path]='/sunday-hub/'+views[0][1]
   def rewrite(value):
    if isinstance(value,list):return [rewrite(v) for v in value]
    if isinstance(value,dict):return {k:rewrite(v) for k,v in value.items()}
    if isinstance(value,str):
     for old,new in sorted(routes.items(),key=lambda p:-len(p[0])):
      if value==old:return new
    return value
   views=[]
   for path,definitions in SOURCES:
    for old,new,title,icon in definitions:
     source=next(v for v in configs[path]['views'] if v.get('path')==old);v=rewrite(copy.deepcopy(source));v.update(path=new,title=title,icon=icon);views.append(v)
   exists=next((d for d in dashboards if d['url_path']=='sunday-hub'),None)
   if not exists:await call('lovelace/dashboards/create',url_path='sunday-hub',title='Sunday',icon='mdi:account-voice',show_in_sidebar=True,require_admin=True)
   await call('lovelace/config/save',url_path='sunday-hub',config={'title':'Sunday','views':views})
   # Hide old entries only after the new hub has been saved and can be read back.
   actual=await call('lovelace/config',url_path='sunday-hub');assert len(actual['views'])==len(views)
   for dashboard in dashboards:
    if dashboard['url_path'] in configs and dashboard.get('show_in_sidebar'):await call('lovelace/dashboards/update',dashboard_id=dashboard['id'],show_in_sidebar=False)
   result=await call('call_service',domain='rest_command',service='sunday_notification_dashboard',return_response=True);response=result['response'];body=response['content'];body=json.loads(body) if isinstance(body,str) else body;assert response['status']==200
   from publish_automations import move_notifications
   await move_notifications(call)
   print('Sunday hub ready:',[v['title'] for v in views]);print('Notification rules:',[(r['rule']['name'],r['rule']['enabled']) for r in body['rules']]);print('Old URLs retained; one Sunday sidebar entry.')
asyncio.run(main())
