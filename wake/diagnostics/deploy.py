import sys,asyncio,json,hashlib,subprocess,copy
from pathlib import Path
sys.path.insert(0,'/home/djpi/sunday')
from central_ac_inching import HA
ROOT=Path('/home/djpi/sunday-verifier-notifications');FILES=[Path('/home/djpi/sunday')/n for n in ['wake.py','mic.py','main.py','wake_verifier.json']]
async def main():
 hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in FILES}
 pids={n:subprocess.check_output(['systemctl','show',n,'-p','MainPID','--value'],text=True).strip() for n in ['sunday.service','sunday2.service','sunday-verifier.service']}
 async with HA() as h:
  helpers=await h.call('input_boolean/list')
  if not any(x['id']=='sunday_rejected_wakes_to_telegram' for x in helpers):
   x=await h.call('input_boolean/create',name='Sunday rejected wakes to Telegram',icon='mdi:microphone-off');assert x['id']=='sunday_rejected_wakes_to_telegram'
  c=await h.call('lovelace/config',url_path='sunday-hub');(ROOT/'dashboard.before.json').write_text(json.dumps(c,indent=2));changed=False
  for view in c['views']:
   cards=view.get('cards',[])
   for i,card in enumerate(cards):
    if 'input_boolean.sunday_transcripts_to_telegram' in json.dumps(card):
     if 'input_boolean.sunday_rejected_wakes_to_telegram' not in json.dumps(view):
      cards.insert(i+1,{'type':'entities','title':'Rejected wake transcripts','show_header_toggle':False,'entities':[{'entity':'input_boolean.sunday_rejected_wakes_to_telegram','name':'Send rejected Whisper transcripts to my Telegram'}]});changed=True
     break
  assert changed,'Expected transcription card not found, or feature already added'
  await h.call('lovelace/config/save',url_path='sunday-hub',config=c)
  states=await h.call('get_states');assert next(s['state'] for s in states if s['entity_id']=='input_boolean.sunday_rejected_wakes_to_telegram')=='off'
 for p in FILES:assert hashlib.sha256(p.read_bytes()).hexdigest()==hashes[str(p)]
 for name,pid in pids.items():assert subprocess.check_output(['systemctl','show',name,'-p','MainPID','--value'],text=True).strip()==pid
 (ROOT/'deployment.json').write_text(json.dumps({'helper':'input_boolean.sunday_rejected_wakes_to_telegram','default':'off','dashboard':'sunday-hub Wake feedback','wake_files_unchanged':hashes,'existing_processes_unchanged':pids},indent=2));print('Helper off and dashboard card installed. Wake files and services unchanged.')
asyncio.run(main())
