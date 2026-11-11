"""Local audio measurements and VAD. No audio recording, network, or wake decisions."""
import asyncio,collections,json,math,os,time
from pathlib import Path
import numpy as np
try:import webrtcvad
except ImportError:webrtcvad=None
CONFIG=Path('/home/djpi/sunday-alive/adaptive_audio.json')
STATE=Path('/home/djpi/sunday-alive/audio_level.json')
_current=None
_blocked_until=0.
def suppress(seconds):
 global _blocked_until
 _blocked_until=max(_blocked_until,time.monotonic()+max(0,seconds))
def settings():
 try:return json.loads(CONFIG.read_text())
 except (OSError,ValueError):return {'mode':'off'}
def dbfs(rms):return round(20*math.log10(max(.01,float(rms))/32768),1)
class VoiceDetector:
 def __init__(self):self.vad=webrtcvad.Vad(1) if webrtcvad else None
 def ratio(self,audio):
  if self.vad is None:return None
  a=np.asarray(audio,dtype='<i2');n=len(a)//320
  if not n:return 0.
  return sum(self.vad.is_speech(a[i*320:(i+1)*320].tobytes(),16000) for i in range(n))/n
class Tracker:
 def __init__(self):
  self.detector=VoiceDetector();self.noise=collections.deque(maxlen=375);self.floor=50.;self.level=50.;self.frames=0;self.errors=0;self.costs=collections.deque(maxlen=1000);self.last_floor=None;self.started=time.time();self.ratio=0.;self.suppressed=False
 def observe(self,audio):
  start=time.perf_counter()
  try:
   f=np.asarray(audio,dtype=np.float32);rms=float(np.sqrt(np.mean(f*f)));ratio=self.detector.ratio(audio)
   self.ratio=ratio;self.frames+=1;self.suppressed=time.monotonic()<_blocked_until
   if not self.suppressed:
    self.level=.85*self.level+.15*rms
    if ratio==0.:
     self.noise.append(rms);self.last_floor=time.time()
     # Update quantile at 1Hz rather than sorting on every frame.
     if len(self.noise)<15 or self.frames%12==0:self.floor=float(np.percentile(self.noise,30))
  except Exception:self.errors+=1
  finally:self.costs.append((time.perf_counter()-start)*1000)
 def snapshot(self):
  source='unknown'
  try:source=json.loads(Path('/home/djpi/sunday-alive/microphone.json').read_text()).get('source','unknown')
  except (OSError,ValueError):pass
  return {'at':time.time(),'started_at':self.started,'source':source,'mode':settings().get('mode','off'),'vad_available':self.detector.vad is not None,'baseline_ready':len(self.noise)>=15,'baseline_samples':len(self.noise),'baseline_updated_at':self.last_floor,'noise_floor_rms':round(self.floor,1),'noise_floor_dbfs':dbfs(self.floor),'sound_rms':round(self.level,1),'sound_dbfs':dbfs(self.level),'speech_ratio':self.ratio,'self_audio_excluded':self.suppressed,'frames':self.frames,'errors':self.errors,'processing_p95_ms':round(float(np.percentile(self.costs,95)),3) if self.costs else None,'processing_max_ms':round(max(self.costs),3) if self.costs else None,'unit_note':'dBFS is relative microphone level, not calibrated room dB SPL. Ambient floor excludes detected speech; sound level includes other voices/TV.'}
class Gate:
 def __init__(self,mic,speech_threshold=400,silence_threshold=200):
  self.tracker=getattr(mic,'audio_tracker',None);self.detector=VoiceDetector();self.mode=settings().get('mode','off');self.start_fixed=speech_threshold;self.end_fixed=silence_threshold;self.frames=0;self.vad_frames=0;self.last={}
 def decide(self,audio):
  f=np.asarray(audio,dtype=np.float32);rms=float(np.sqrt(np.mean(f*f)))
  ready=bool(self.tracker and len(self.tracker.noise)>=15);floor=self.tracker.floor if ready else 50.
  start=max(100.,min(float(self.start_fixed),floor*2.5+30));end=max(70.,min(float(self.end_fixed),floor*1.5+20))
  ratio=self.detector.ratio(audio);active=self.mode=='adaptive' and ratio is not None
  starts=(ratio>=.25 and rms>=start) if active else rms>=self.start_fixed
  continues=(ratio>=.25 and rms>=end) if active else rms>=self.end_fixed
  self.frames+=1;self.vad_frames+=int(bool(ratio));self.last={'mode':self.mode,'active':active,'noise_floor_rms':round(floor,1),'baseline_ready':ready,'speech_start_rms':round(start,1),'speech_end_rms':round(end,1),'last_speech_ratio':ratio,'frames':self.frames,'speech_frames':self.vad_frames}
  return starts,continues
async def publish(tracker):
 while True:
  data=tracker.snapshot()
  def write():
   tmp=STATE.with_suffix('.tmp');tmp.write_text(json.dumps(data));tmp.chmod(0o600);os.replace(tmp,STATE)
  try:await asyncio.to_thread(write)
  except OSError:tracker.errors+=1
  await asyncio.sleep(5)
def attach(mic):
 global _current
 tracker=Tracker();mic.audio_tracker=tracker;_current=tracker
 mic.audio_level_task=asyncio.create_task(publish(tracker))
 return tracker
