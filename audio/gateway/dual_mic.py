"""Opt-in side-channel diagnostics. No STT/wake consumer and no servo commands."""
import asyncio,collections,json,os,struct,time,wave
from pathlib import Path
FILE=Path(os.environ.get('SA_DUAL_MIC_CONFIG','/home/djpi/sunday-alive/dual_mic.json'))
DEFAULT={'enabled':False,'spacing_mm':0.,'channel0_side':'Uncalibrated','center_offset_us':0.}
class DualMic:
 def __init__(self,root=None):
  self.file=Path(root) if root else FILE;self.cfg=dict(DEFAULT)
  try:self.cfg.update(json.loads(self.file.read_text()))
  except (OSError,ValueError):pass
  self.actual=False;self.error='';self.frames=collections.deque(maxlen=50);self.last_seq=None
  self.received=0;self.gaps=0;self.last_at=0.;self.last_work=0.;self.result={};self.pool=None;self.future=None;self.generation=0;self.saved=None
 def reset_audio(self):
  self.frames.clear();self.last_seq=None;self.last_at=0;self.result={};self.actual=False;self.generation+=1
 def control(self,name,value):
  cfg=dict(self.cfg)
  if name=='dual_mic':cfg['enabled']=value=='1'
  elif name=='dual_mic_spacing':
   v=float(value)
   if not 0<=v<=200:raise ValueError('Spacing must be measured, 0..200 mm; 0 means uncalibrated')
   cfg['spacing_mm']=v
  elif name=='dual_mic_channel':
   if value not in ('Uncalibrated','Left','Right'):raise ValueError('Invalid physical side')
   cfg['channel0_side']=value
  elif name=='dual_mic_offset':
   v=float(value)
   if not -500<=v<=500:raise ValueError('Invalid center offset')
   cfg['center_offset_us']=v
  else:raise ValueError('Unknown dual mic setting')
  tmp=self.file.with_suffix('.tmp');tmp.write_text(json.dumps(cfg));tmp.chmod(0o600);os.replace(tmp,self.file)
  was_actual=self.actual
  self.cfg=cfg;self.reset_audio();self.actual=was_actual;self.error=''
  if not cfg['enabled'] and self.pool:self.pool.shutdown(wait=False,cancel_futures=True);self.pool=None
 def ack(self,data):
  self.actual=data.get('enabled') is True;self.error=str(data.get('error') or '')[:80]
  if not self.actual:self.frames.clear();self.result={}
 def feed(self,packet,gated=False):
  if not self.cfg['enabled']:return
  if len(packet)!=5123 or packet[0]!=4:self.error='Bad stereo packet';return
  self.actual=True;self.received+=1
  if gated:self.frames.clear();self.result={'status':'PAUSED_SELF_AUDIO','direction':None};return
  if self.last_at and time.monotonic()-self.last_at>.2:self.frames.clear();self.result={}
  seq=int.from_bytes(packet[1:3],'big')
  if self.last_seq is not None and seq!=(self.last_seq+1)&65535:self.frames.clear();self.gaps+=1
  self.last_seq=seq;self.last_at=time.monotonic();self.frames.append(bytes(packet[3:]))
  now=time.monotonic()
  if len(self.frames)<6 or now-self.last_work<.5 or self.future is not None:return
  self.last_work=now
  if self.pool is None:
   from concurrent.futures import ThreadPoolExecutor
   self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='dual-mic',initializer=lambda:os.nice(10))
  generation=self.generation;blob=b''.join(list(self.frames)[-6:]);cfg=dict(self.cfg)
  loop=asyncio.get_running_loop();future=loop.run_in_executor(self.pool,self.compute,blob,cfg);self.future=future
  def done(f):
   if self.future is f:self.future=None
   try:result=f.result()
   except Exception as e:result={'status':'ANALYSIS_ERROR','direction':None,'error':type(e).__name__}
   if generation==self.generation and self.cfg['enabled']:self.result=result
  future.add_done_callback(done)
 @staticmethod
 def compute(blob,cfg):
  import numpy as np
  from dual_mic_dsp import analyze
  pcm=np.frombuffer(blob,dtype='<i2').reshape(-1,2);rms=np.sqrt(np.mean(pcm.astype(float)**2,axis=0));levels=(20*np.log10(np.maximum(rms,1e-6)/32768)).tolist()
  if not 5<=cfg['spacing_mm']<=200:return {'status':'MEASURE_MIC_SPACING','direction':None,'rms_dbfs':levels}
  side={'Left':'left','Right':'right'}.get(cfg['channel0_side'])
  r=analyze(pcm,16000,cfg['spacing_mm'],side,cfg['center_offset_us']);r.pop('windows',None);return r
 def status(self):
  if not self.cfg['enabled']:state='Stopping test' if self.actual else 'Off'
  elif self.error:state='Error: '+self.error
  elif not self.actual:state='Waiting for firmware'
  elif time.monotonic()-self.last_at>3:state='Waiting for stereo audio'
  else:state=self.result.get('direction') or self.result.get('status','Collecting')
  state={'MEASURE_MIC_SPACING':'Measure mic spacing','UNCALIBRATED_CHANNEL_ORDER':'Calibrate channel order','INSUFFICIENT_RELIABLE_AUDIO':'Audio too quiet or unclear','DUPLICATED_OR_DEGENERATE':'Channels look duplicated','AMBIGUOUS_OR_MOVING':'Uncertain or moving source','PAUSED_SELF_AUDIO':'Paused while Sunday speaks','ANALYSIS_ERROR':'Analysis error'}.get(state,state)
  return {**self.cfg,'actual_enabled':self.actual,'state':state,'received':self.received,'sequence_gaps':self.gaps,'result':dict(self.result),'saved_clip':self.saved,'rate':16000}
 def capture(self):
  if not self.cfg['enabled'] or not self.actual or len(self.frames)<6 or time.monotonic()-self.last_at>2:raise ValueError('Enable dual microphone test and wait for fresh audio first')
  folder=self.file.parent/'dual_mic_clips';folder.mkdir(mode=0o700,exist_ok=True)
  path=folder/(time.strftime('%Y%m%d-%H%M%S')+'-'+str(time.time_ns()%1000000000)+'.wav');blob=b''.join(list(self.frames))
  with wave.open(str(path),'wb') as w:w.setnchannels(2);w.setsampwidth(2);w.setframerate(16000);w.writeframes(blob)
  self.saved=str(path)
  path.chmod(0o600);path.with_suffix('.json').write_text(json.dumps(self.status(),indent=2))
  # Bound accumulated private recordings; only explicit button presses write audio.
  for old in sorted(folder.glob('*.wav'))[:-10]:old.unlink();old.with_suffix('.json').unlink(missing_ok=True)
  self.saved=str(path);return self.saved
