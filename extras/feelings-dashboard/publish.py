"""Publish one HA storage dashboard and one static module; no production service restart."""
import asyncio,sys,json,hashlib,subprocess,urllib.request
from pathlib import Path
sys.path.insert(0,'/home/djpi/sunday')
from central_ac_inching import HA
ROOT=Path(__file__).parent
async def main():
 config=json.loads((ROOT/'dashboard.json').read_text());path='sunday-feelings';resource='/local/sunday-feelings-card.js?v=20261029-3'
 async with HA() as h:
  dashboards=await h.call('lovelace/dashboards/list');existing=next((d for d in dashboards if d['url_path']==path),None)
  if existing:
   previous=await h.call('lovelace/config',url_path=path);(ROOT/'dashboard-existing-before.json').write_text(json.dumps(previous,indent=2)+'\n')
  resources=await h.call('lovelace/resources');current=next((x for x in resources if x.get('url','').split('?')[0]=='/local/sunday-feelings-card.js'),None)
  if current:await h.call('lovelace/resources/update',resource_id=current['id'],url=resource,res_type='module')
  else:await h.call('lovelace/resources/create',url=resource,res_type='module')
  if not existing:existing=await h.call('lovelace/dashboards/create',url_path=path,title='Sunday Feelings',icon='mdi:emoticon-happy-outline',show_in_sidebar=True,require_admin=True)
  await h.call('lovelace/config/save',url_path=path,config=config)
  readback=await h.call('lovelace/config',url_path=path);assert readback==config
  assert all(v['type']=='panel' and len(v['cards'])==1 for v in readback['views'])
  after=await h.call('lovelace/dashboards/list')
  for before in json.loads((ROOT/'dashboards-before.json').read_text()):assert before in after,'An existing dashboard changed'
  finalresources=await h.call('lovelace/resources')
  for before in json.loads((ROOT/'resources-before.json').read_text()):assert before in finalresources,'An existing resource changed'
  result=await h.call('call_service',domain='rest_command',service='sunday_feelings',service_data={},return_response=True);response=result['response'];assert response['status']==200
  data=response['content'];data=json.loads(data) if isinstance(data,str) else data
  (ROOT/'published-dashboard.json').write_text(json.dumps(readback,indent=2)+'\n');(ROOT/'live-ha-data.json').write_text(json.dumps(data,indent=2)+'\n')
  info={'url_path':'/sunday-feelings/feelings','dashboard':next(d for d in after if d['url_path']==path),'resource':resource,'existing_dashboards_preserved':len(dashboards),'existing_resources_preserved':len(resources),'endpoint':'cached Alive status','auto_refresh_seconds':30,'hidden_or_disconnected_polling':False,'new_model_calls':0,'read_only':True}
  (ROOT/'deployment.json').write_text(json.dumps(info,indent=2)+'\n')
 for path,expected in json.loads((ROOT/'source-hashes-before.json').read_text()).items():assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==expected,path
 units=['sunday.service','sunday2.service','sunday-stackchan-gateway.service','sunday-verifier.service','house.service','hogar-mqtt-bridge.service','cron.service'];after=subprocess.check_output(['systemctl','show',*units,'-p','Id','-p','MainPID','-p','ActiveState'],text=True)
 assert after==(ROOT/'services-before.txt').read_text(),'A protected service changed'
 (ROOT/'services-after.txt').write_text(after)
 print('Sunday Feelings published at /sunday-feelings/feelings; all existing dashboards/resources and service PIDs preserved.')
asyncio.run(main())
