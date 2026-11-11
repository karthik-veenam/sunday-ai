import sys,asyncio,json,subprocess,hashlib
from pathlib import Path
sys.path.insert(0,'/home/djpi/sunday')
from central_ac_inching import HA
ROOT=Path('/home/djpi/sunday-verifier-notifications')
async def main():
 async with HA() as h:
  await h.service('rest_command','reload',{})
  result=await h.call('call_service',domain='rest_command',service='sunday_wake_aliases',service_data={'action':'list'},return_response=True)
  assert result['response']['status']==200,result
  body=result['response']['content'];body=json.loads(body) if isinstance(body,str) else body
  assert 'sunday' in body['aliases'];print('HA list endpoint:',len(body['aliases']),'aliases')
  # Verify the full Add path with an existing alias: it must be idempotent, no restart.
  pid=subprocess.check_output(['systemctl','show','sunday-verifier.service','-p','MainPID','--value'],text=True)
  r=await h.call('call_service',domain='rest_command',service='sunday_wake_aliases',service_data={'action':'add','alias':'Sunday!'},return_response=True)
  b=r['response']['content'];b=json.loads(b) if isinstance(b,str) else b;assert r['response']['status']==200 and b['changed'] is False
  assert subprocess.check_output(['systemctl','show','sunday-verifier.service','-p','MainPID','--value'],text=True)==pid
  resources=await h.call('lovelace/resources')
  if not any('/sunday-wake-alias-card.js' in r.get('url','') for r in resources):await h.call('lovelace/resources/create',res_type='module',url='/local/sunday-wake-alias-card.js?v=1')
  c=await h.call('lovelace/config',url_path='sunday-hub');(ROOT/'alias-dashboard-backup/dashboard.json').write_text(json.dumps(c,indent=2));found=False
  for v in c['views']:
   if 'input_boolean.sunday_rejected_wakes_to_telegram' in json.dumps(v):
    if 'custom:sunday-wake-alias-card' not in json.dumps(v):v['cards'].insert(2,{'type':'custom:sunday-wake-alias-card'})
    found=True
  assert found;await h.call('lovelace/config/save',url_path='sunday-hub',config=c)
 print('Alias list/Add compatibility verified and dashboard card published. No test alias added.')
asyncio.run(main())
