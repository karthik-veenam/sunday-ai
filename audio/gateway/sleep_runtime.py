import asyncio,json,os,tempfile,time
from pathlib import Path
from aiohttp import ClientSession,ClientTimeout
from sleep_policy import SleepPolicy
FILE=Path('/home/djpi/sunday-alive/sleep.json')
class SleepRuntime:
 def __init__(self,hub):
  self.hub=hub;self.event=asyncio.Event();self.lock=asyncio.Lock();self.cached=None;self.last_sent_night=None;self.errors=0
  try:
   settings=json.loads(FILE.read_text());assert settings.get('version')==1
   assert settings.get('force_night') is None or type(settings['force_night']) is bool
   assert type(settings.get('auto_night',False)) is bool
   assert isinstance(settings.get('stay_awake_until',0),(int,float))
  except FileNotFoundError:settings={}
  except Exception:settings={};self.errors+=1
  self.policy=SleepPolicy(settings,time.time(),time.monotonic())
 def interact(self):self.policy.interact(time.time(),time.monotonic());self.event.set()
 def _save(self,settings):
  FILE.parent.mkdir(parents=True,exist_ok=True)
  fd,name=tempfile.mkstemp(dir=FILE.parent,prefix='.sleep-')
  try:
   with os.fdopen(fd,'w') as f:json.dump({'version':1,**settings},f);f.write('\n');f.flush();os.fsync(f.fileno())
   os.chmod(name,0o600);os.replace(name,FILE)
  finally:
   if os.path.exists(name):os.unlink(name)
 async def control(self,name,on):
  async with self.lock:
   if name=='night':self.policy.settings['force_night']=on
   elif name=='stay_awake':self.policy.settings['stay_awake_until']=time.time()+3600 if on else 0
   else:raise ValueError('sleep control')
   await asyncio.to_thread(self._save,dict(self.policy.settings))
  self.event.set()
 async def run(self):
  cfg=json.loads(Path('/home/djpi/sunday2-rebuild/config.json').read_text());headers={'Authorization':'Bearer '+cfg['daemon_token']}
  old_settings=None;posted=0;command_at=0;last_state=None
  async with ClientSession(timeout=ClientTimeout(total=.8)) as session:
   while True:
    self.event.clear()
    try:
     t=self.hub.sleep_telemetry or {};at=self.hub.sleep_telemetry_at
     connected=self.hub.dev is not None and not self.hub.dev.closed and time.time()-at<20
     body=t.get('st');night=t.get('n')==1 if t else None
     busy=self.hub.playing>0 or self.hub.brain_state in ('wake','transcribing','thinking') or self.hub.last_state in ('wake','listening','thinking','speaking')
     snapshot=self.policy.evaluate(time.time(),time.monotonic(),connected,body,night,t.get('lux'),time.time()-at,busy)
     self.cached=snapshot
     # A single render arbiter carries sleepy eyes. No manual face override.
     expr='Sleepy' if snapshot['state'] in ('Drowsy','Temporarily awake') else None
     if self.hub.alive.sleep_expression!=expr:
      self.hub.alive.sleep_expression=expr;await self.hub.sync_alive()
     if connected:
      desired=snapshot['desired_night']
      if night!=desired and (self.last_sent_night!=desired or time.time()-command_at>10):
       await self.hub.send_device_cmd('night','1' if desired else '0');self.last_sent_night=desired;command_at=time.time()
      if desired and not busy and body!='sleeping':await self.hub.send_state('sleeping')
      elif not desired and not busy and body=='sleeping':await self.hub.send_state('idle')
     settings=json.dumps(self.policy.settings,sort_keys=True)
     if settings!=old_settings:
      async with self.lock:
       await asyncio.to_thread(self._save,dict(self.policy.settings));old_settings=json.dumps(self.policy.settings,sort_keys=True)
     if self.hub.ha:self.hub.ha.sleep_status(snapshot)
     if time.time()-posted>=15 or snapshot['state']!=last_state:
      async with session.post('http://127.0.0.1:8090/v1/alive/sleep',headers=headers,json=snapshot) as response:
       if response.status!=200:raise RuntimeError('sleep snapshot rejected')
       await response.read()
      posted=time.time();last_state=snapshot['state']
    except asyncio.CancelledError:raise
    except Exception as error:
     self.errors+=1
     if self.errors<=3 or self.errors%60==0:print("[sleep]",type(error).__name__,str(error)[:120],flush=True)
    try:await asyncio.wait_for(self.event.wait(),timeout=5)
    except asyncio.TimeoutError:pass
