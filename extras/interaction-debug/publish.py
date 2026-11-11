import sys,asyncio,json,hashlib,subprocess
from pathlib import Path
sys.path.insert(0,'/home/djpi/sunday')
from central_ac_inching import HA
OUT=Path(__file__).parent
async def main():
 protected={n:subprocess.check_output(['systemctl','show',n,'-p','MainPID','--value'],text=True).strip() for n in ['sunday.service','sunday2.service','sunday-verifier.service','house.service','hogar-mqtt-bridge.service','cron.service']}
 source={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for base,names in [('/home/djpi/sunday',['wake.py','mic.py','main.py','ha_speech.py']),('/home/djpi/sunday2-rebuild',['agent.py','runtime.py','home_tools.py','output.py'])] for p in [Path(base)/name for name in names]}
 subprocess.run(['sudo','systemctl','restart','sunday-verifier-notifications.service'],check=True)
 import aiohttp
 token=json.loads(Path('/home/djpi/sunday2-rebuild/config.json').read_text())['daemon_token']
 async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=1)) as client:
  for _ in range(40):
   try:
    async with client.post('http://127.0.0.1:8097/v1/wake-aliases',headers={'Authorization':'Bearer '+token},json={'action':'list'}) as response:
     if response.status==200:break
   except Exception:pass
   await asyncio.sleep(.2)
  else:raise RuntimeError('Diagnostics API not ready')
 async with HA() as h:
  await h.service('rest_command','reload',{})
  r=await h.call('call_service',domain='rest_command',service='sunday_interaction_debug',service_data={},return_response=True)
  assert r['response']['status']==200,r
  data=r['response']['content'];data=json.loads(data) if isinstance(data,str) else data
  assert data['read_only'] and len(data['interactions'])<=3
  for res in await h.call('lovelace/resources'):
   if '/sunday-interaction-debug-card.js' in res.get('url',''):break
  else:await h.call('lovelace/resources/create',res_type='module',url='/local/sunday-interaction-debug-card.js?v=1')
  c=await h.call('lovelace/config',url_path='sunday-inputs');(OUT/'dashboard.before.json').write_text(json.dumps(c,indent=2))
  if not any(v.get('path')=='debug' for v in c['views']):c['views'].insert(2,{'title':'Debug','path':'debug','icon':'mdi:bug-outline','type':'panel','cards':[{'type':'custom:sunday-interaction-debug-card'}]})
  await h.call('lovelace/config/save',url_path='sunday-inputs',config=c)
 for p,sha in source.items():assert hashlib.sha256(Path(p).read_bytes()).hexdigest()==sha,p
 for n,pid in protected.items():assert subprocess.check_output(['systemctl','show',n,'-p','MainPID','--value'],text=True).strip()==pid,n
 (OUT/'deployment.json').write_text(json.dumps({'view':'/sunday-inputs/debug','interactions':len(data['interactions']),'request_path_sources_unchanged':source,'production_service_pids_unchanged':protected,'refresh':'Open or manual Refresh only'},indent=2));print('Debug view published; production request-path files/PIDs unchanged.')
asyncio.run(main())
