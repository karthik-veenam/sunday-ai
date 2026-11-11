#!/usr/bin/python3
"""Explicit reversible verifier selection; no restarts or V5-only fallback."""
import argparse,json,os,tempfile,urllib.request
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('provider',choices=['whisper','sherpa']);a=p.parse_args()
url={'whisper':'http://127.0.0.1:8183','sherpa':'http://127.0.0.1:8184'}[a.provider]
with urllib.request.urlopen(url+'/health',timeout=3) as response:assert json.load(response)['status']=='ok','Verifier not ready'
path=Path('/home/djpi/sunday/wake_verifier.json');c=json.loads(path.read_text());c.update(provider=a.provider,url=url+'/inference',mode='enforce');fd,name=tempfile.mkstemp(dir=path.parent,prefix='.verifier-switch-')
try:
 with os.fdopen(fd,'w') as f:json.dump(c,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
 os.chmod(name,0o644);os.replace(name,path)
finally:
 if os.path.exists(name):os.unlink(name)
print(a.provider.title()+' selected for the next wake. Verification remains mandatory; no restart needed.')
