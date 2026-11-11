import asyncio,json,os,tempfile
from pathlib import Path
FILE=Path('/home/djpi/sunday-alive/stt_streaming.json')
def live_enabled():
 try:return json.loads(FILE.read_text()).get('enabled') is True
 except (OSError,ValueError,TypeError):return False
async def set_mode(mode):
 if mode not in ('Live','After speech'):raise ValueError('Unknown STT mode')
 def save():
  fd,name=tempfile.mkstemp(dir=FILE.parent,prefix='.stt-mode-')
  try:
   with os.fdopen(fd,'w') as f:json.dump({'version':1,'enabled':mode=='Live'},f);f.flush();os.fsync(f.fileno())
   os.replace(name,FILE)
  finally:
   if os.path.exists(name):os.unlink(name)
 await asyncio.to_thread(save)
