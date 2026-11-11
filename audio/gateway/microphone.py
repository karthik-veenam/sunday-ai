"""Select one live input before the shared wake/STT stream. Never store audio."""
import asyncio,json,os,tempfile,time
from pathlib import Path
LABELS={'body':'Body microphone','airhug':'AIRHUG USB microphone'}
SOURCE='alsa_input.usb-Generic_AIRHUG_21_AIRHUG_21-00.analog-stereo'
class Microphone:
 def __init__(self,hub):
  self.hub=hub;self.path=Path(os.environ.get('SUNDAY_MIC_CONFIG',str(Path(os.environ.get('SUNDAY_AUDIO_HOME',str(Path.home()/'sunday-audio'))).parent/'sunday-alive/microphone.json')))
  try:self.selected=json.loads(self.path.read_text())['source'];assert self.selected in LABELS
  except FileNotFoundError:self.selected='body'
  except Exception:self.selected='body'
  self.active='body' if self.selected=='body' else None;self.proc=None;self.task=None;self.lock=asyncio.Lock();self.frames=0;self.last_frame=0;self.error=None
 def status(self):return {'selected':LABELS[self.selected],'active':LABELS.get(self.active),'usb_frames':self.frames,'last_usb_frame_at':self.last_frame,'error':self.error,'speaker':'Body speaker'}
 def publish(self):
  if self.hub.ha:self.hub.ha.runtime_status()
 def save(self,value):
  self.path.parent.mkdir(parents=True,exist_ok=True);fd,name=tempfile.mkstemp(dir=self.path.parent,prefix='.mic-')
  try:
   with os.fdopen(fd,'w') as f:json.dump({'version':1,'source':value},f);f.flush();os.fsync(f.fileno())
   os.replace(name,self.path)
  finally:
   if os.path.exists(name):os.unlink(name)
 async def open_usb(self):
  env=dict(os.environ,PULSE_SERVER='unix:/run/user/%s/pulse/native'%os.getuid(),XDG_RUNTIME_DIR='/run/user/%s'%os.getuid())
  proc=await asyncio.create_subprocess_exec('/usr/bin/parec','--device='+SOURCE,'--raw','--format=s16le','--rate=16000','--channels=1','--latency-msec=20',stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,env=env)
  try:frame=await asyncio.wait_for(proc.stdout.readexactly(2560),3);return proc,frame
  except BaseException:
   if proc.returncode is None:proc.terminate()
   await proc.wait();raise
 def reset(self):
  self.hub.pending.clear();self.hub.primed=False
  if self.hub.hp is not None:self.hub.hp.reset()
 async def stop_usb(self):
  if self.task:
   self.task.cancel()
   try:await self.task
   except asyncio.CancelledError:pass
   self.task=None
  if self.proc:
   if self.proc.returncode is None:self.proc.terminate()
   await self.proc.wait();self.proc=None
 async def select(self,label,restore=False):
  value=next((k for k,v in LABELS.items() if v==label),None)
  if value is None:raise ValueError('Unknown microphone')
  async with self.lock:
   if self.hub.playing or self.hub.last_state in ('wake','listening','thinking','speaking'):raise RuntimeError('Wait until the current conversation has finished')
   if value==self.selected and self.active==value:return
   if value=='body' and (self.hub.dev is None or self.hub.dev.closed):raise RuntimeError('Body microphone is disconnected')
   proc=None
   try:
    if value=='airhug':proc,frame=await self.open_usb()
    if not restore:await asyncio.to_thread(self.save,value)
   except Exception as error:
    if proc:
     if proc.returncode is None:proc.terminate()
     await proc.wait()
    self.error='Could not activate '+LABELS[value]+': '+type(error).__name__;self.publish();raise
   await self.stop_usb();self.reset();self.selected=value;self.active=value;self.error=None
   if value=='airhug':
    self.proc=proc;self.accept(frame);self.task=asyncio.create_task(self.read_usb())
   self.publish()
 def accept(self,frame):
  self.frames+=1;self.last_frame=time.time();self.hub.pending.append(frame)
 async def read_usb(self):
  try:
   while True:self.accept(await asyncio.wait_for(self.proc.stdout.readexactly(2560),3))
  except asyncio.CancelledError:raise
  except Exception:
   self.active=None;self.reset();self.error='AIRHUG stream unavailable; reconnect it or select the body microphone.';self.publish()
 async def run(self):
  try:
   while True:
    if self.selected=='airhug' and self.active is None:
     try:await self.select(LABELS['airhug'],restore=True)
     except Exception:pass
    await asyncio.sleep(3)
  finally:await self.stop_usb()
