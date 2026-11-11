"""Optional diagnostic forwarding; observes verdicts without touching wake/audio code."""
import asyncio,json,logging
from collections import deque
from pathlib import Path
import aiohttp
LOG=Path('/home/djpi/sunday-wake-verifier/verdicts.jsonl')
CONFIG=Path('/home/djpi/sunday2-rebuild/config.json')
DEBUG_CONFIG=Path('/home/djpi/sunday/debug_telegram.json')
TOGGLE='input_boolean.sunday_rejected_wakes_to_telegram'
class Tail:
 def __init__(self,path):
  self.path=Path(path);self.identity=None;self.offset=0
  if self.path.exists():
   s=self.path.stat();self.identity=(s.st_dev,s.st_ino);self.offset=s.st_size
 def read(self):
  try:
   s=self.path.stat();identity=(s.st_dev,s.st_ino)
   if identity!=self.identity or s.st_size<self.offset:self.identity=identity;self.offset=0
   records=[]
   with self.path.open('rb') as f:
    f.seek(self.offset)
    for _ in range(100):
     line=f.readline(65537)
     if not line:break
     if not line.endswith(b'\n'):break
     self.offset=f.tell()
     try:
      value=json.loads(line)
      if isinstance(value,dict):records.append(value)
     except (ValueError,UnicodeError):pass
   return records
  except FileNotFoundError:return []
class Forwarder:
 def __init__(self,session):self.session=session;self.seen=deque(maxlen=256)
 async def toggle_on(self,c):
  async with self.session.get('http://127.0.0.1:8123/api/states/'+TOGGLE,headers={'Authorization':'Bearer '+c['ha_token']}) as r:
   return r.status==200 and (await r.json()).get('state')=='on'
 async def send(self,c,text):
  # Exceptions are intentionally never printed: provider URLs can contain credentials.
  debug=json.loads(DEBUG_CONFIG.read_text())
  async with self.session.post('https://api.telegram.org/bot'+debug['telegram_bot_token']+'/sendMessage',json={'chat_id':debug['telegram_chat_id'],'text':text,'disable_web_page_preview':True}) as r:
   data=await r.json();return r.status==200 and data.get('ok') is True
 async def handle(self,record):
  if record.get('mode')!='enforce' or record.get('accepted') is not False or record.get('error'):return
  eid=record.get('event_id')
  if not isinstance(eid,str) or eid in self.seen:return
  self.seen.append(eid)
  try:
   c=json.loads(CONFIG.read_text())
   if not await self.toggle_on(c):return
   heard=str(record.get('text') or '').strip()[:1200] or '[no speech recognized]'
   detail='Sherpa: no accepted keyword' if record.get('provider')=='sherpa' else 'Whisper heard: '+heard
   stamp=str(record.get('t',''))[:32]
   if not await self.send(c,'🚫 Wake rejected\n'+detail+('\n'+stamp if stamp else '')):logging.warning('Rejected-wake notification was not accepted; no automatic resend.')
  except asyncio.CancelledError:raise
  except Exception:logging.warning('Rejected-wake notification unavailable; wake behavior unaffected.')
async def forward_loop():
 tail=Tail(LOG)
 async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as session:
  forwarder=Forwarder(session)
  while True:
   for record in tail.read():await forwarder.handle(record)
   await asyncio.sleep(.5)
async def main():
 from aliases import create_app
 from aiohttp import web
 runner=web.AppRunner(create_app());await runner.setup();await web.TCPSite(runner,'127.0.0.1',8097).start()
 try:await forward_loop()
 finally:await runner.cleanup()
if __name__=='__main__':asyncio.run(main())
