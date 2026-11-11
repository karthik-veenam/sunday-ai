"""Hot switch for command speech boundaries; never changes wake detection."""
import asyncio,json,os,tempfile
from pathlib import Path
FILE=Path('/home/djpi/sunday-alive/adaptive_audio.json')
def enabled():
 try:return json.loads(FILE.read_text()).get('mode')=='adaptive'
 except (OSError,ValueError):return False
async def set_enabled(on):
 if type(on) is not bool:raise ValueError('Expected boolean')
 def save():
  fd,name=tempfile.mkstemp(dir=FILE.parent,prefix='.adaptive-')
  try:
   with os.fdopen(fd,'w') as f:json.dump({'mode':'adaptive' if on else 'off'},f);f.flush();os.fsync(f.fileno())
   os.replace(name,FILE)
  finally:
   if os.path.exists(name):os.unlink(name)
 await asyncio.to_thread(save)
