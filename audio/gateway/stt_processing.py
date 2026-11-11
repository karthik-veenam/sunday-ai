"""Hot, per-microphone completed-command cleanup settings; no wake-stream changes."""
import asyncio,json,os,tempfile
from pathlib import Path
FILE=Path('/home/djpi/sunday-alive/stt_cleanup.json')
DEFAULTS={'airhug':False,'body':True}
_LOCK=asyncio.Lock()
def settings():
 try:
  data=json.loads(FILE.read_text());values=data['sources']
  return {k:values.get(k,default) is True for k,default in DEFAULTS.items()}
 except FileNotFoundError:return dict(DEFAULTS)
def enabled(source):return settings().get(source,False)
async def set_enabled(source,on):
 if source not in DEFAULTS or type(on) is not bool:raise ValueError('Invalid cleanup setting')
 def save():
  values=settings();values[source]=on
  fd,name=tempfile.mkstemp(dir=FILE.parent,prefix='.stt-cleanup-')
  try:
   with os.fdopen(fd,'w') as f:json.dump({'version':1,'profile':'mild-v1','sources':values},f);f.flush();os.fsync(f.fileno())
   os.replace(name,FILE)
  finally:
   if os.path.exists(name):os.unlink(name)
 async with _LOCK:await asyncio.to_thread(save)
