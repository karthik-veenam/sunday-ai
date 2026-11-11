"""Capture a single physical remote key from Rani's existing Tuya IR hub."""
import base64,json,time
from pathlib import Path
from tinytuya.Contrib import IRRemoteControlDevice as IR
cfg=json.load(open('/home/djpi/rani-tuya-ir/local-config.json'))
d=IR(cfg['hub_id'],cfg['ip'],cfg['local_key'],version=cfg['version'],control_type=1,connection_timeout=2,connection_retry_limit=2)
d.set_socketPersistent(True);d.set_socketTimeout(1)
found=None
try:
 d.study_end();d.study_start();print('LISTENING: point the physical remote at Rani IR and press Power once.',flush=True)
 deadline=time.monotonic()+45;last_heartbeat=time.monotonic()
 while time.monotonic()<deadline:
  response=d.receive()
  if isinstance(response,dict):
   for key in ['202',str(IR.DP_LEARNED_REPORT),str(IR.DP_LEARNED_ID)]:
    value=response.get('dps',{}).get(key)
    if isinstance(value,str) and value:
     raw=base64.b64decode(value,validate=True)
     if len(raw)>12 and len(raw)%2==0:found=value;break
  if found:break
  if time.monotonic()-last_heartbeat>5:
   d.heartbeat(nowait=True);last_heartbeat=time.monotonic()
finally:
 d.study_end();d.close()
if found:
 path=Path('/home/djpi/firetv-lan/nokia-power-ir.json');path.write_text(json.dumps({'base64_code':found})+'\n')
 print('CAPTURED: Nokia power signal saved; timing samples:',len(base64.b64decode(found))//2,flush=True)
else:print('No IR signal captured in the listening window.',flush=True)
