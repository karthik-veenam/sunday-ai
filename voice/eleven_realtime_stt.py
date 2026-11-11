"""Wake-gated Scribe streaming. No idle microphone stream or action on partial text."""
import asyncio,base64,json,time,os
from pathlib import Path
from collections import deque
import aiohttp
import numpy as np
CONFIG=Path('/home/djpi/sunday-alive/stt_streaming.json')
URL='wss://api.elevenlabs.io/v1/speech-to-text/realtime'
PARAMS={'model_id':'scribe_v2_realtime','audio_format':'pcm_16000','language_code':'eng','commit_strategy':'manual','keyterms':'Sunday','include_timestamps':'false'}
def enabled():
 try:return json.loads(CONFIG.read_text()).get('enabled') is True
 except (OSError,ValueError,TypeError):return False
class LiveError(RuntimeError):pass

def cleanup_enabled():
 from stt_cleanup import SOURCE_CONFIG,MIC_CONFIG,CONFIG as OLD
 if SOURCE_CONFIG.exists():
  settings=json.loads(SOURCE_CONFIG.read_text());mic=json.loads(MIC_CONFIG.read_text()).get('source','body') if MIC_CONFIG.exists() else 'body'
  return settings.get('sources',{}).get(mic,False) is True,mic
 return (json.loads(OLD.read_text()).get('enabled') is True if OLD.exists() else False),'legacy'
async def audio_chunks(queue,processing,meta):
 async def raw():
  while True:
   item=await queue.get()
   if item is None:return
   yield item
 if not processing:
  async for item in raw():yield item
  return
 from stt_cleanup import FILTER
 proc=await asyncio.create_subprocess_exec('ffmpeg','-hide_banner','-loglevel','error','-threads','1','-filter_threads','1','-probesize','32','-analyzeduration','0','-f','s16le','-ar','16000','-ac','1','-i','pipe:0','-af',FILTER,'-flush_packets','1','-f','s16le','-ar','16000','-ac','1','pipe:1',stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
 counts=[0,0]
 async def write():
  try:
   async for item in raw():counts[0]+=len(item);proc.stdin.write(item);await proc.stdin.drain()
  finally:proc.stdin.close()
 writer=asyncio.create_task(write())
 try:
  while True:
   item=await proc.stdout.read(2560)
   if not item:break
   counts[1]+=len(item);yield item
  await writer;await asyncio.wait_for(proc.wait(),2)
  if proc.returncode or counts[0]!=counts[1]:raise LiveError('audio_cleanup_format_error')
  meta['cleanup_input_bytes']=counts[0];meta['cleanup_output_bytes']=counts[1]
 finally:
  writer.cancel();await asyncio.gather(writer,return_exceptions=True)
  if proc.returncode is None:proc.kill();await proc.wait()

class WarmConnection:
 """One ready socket; cloud receives microphone audio only during a voice turn."""
 def __init__(self,key):
  self.key=key;self.ws=None;self.session=None;self.active=None;self.task=None;self.ready=asyncio.Event();self.lock=asyncio.Lock();self.closed=False;self.connections=0
 def start(self):
  if self.task is None:self.task=asyncio.create_task(self.run())
 async def run(self):
  while not self.closed:
   try:
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None,sock_connect=4)) as session:
     self.session=session
     async with session.ws_connect(URL,params=PARAMS,headers={'xi-api-key':self.key()},timeout=5,heartbeat=10,max_msg_size=1024*1024) as ws:
      first=await ws.receive_json(timeout=5)
      if first.get('message_type')!='session_started':raise LiveError('session_rejected')
      config=first.get('config',{})
      if config.get('sample_rate',16000)!=16000 or config.get('audio_format','pcm_16000')!='pcm_16000':raise LiveError('session_audio_format_mismatch')
      if config.get('language_code') not in (None,'en','eng'):raise LiveError('session_language_mismatch')
      self.config=config;self.ws=ws;self.connections+=1;self.ready.set()
      print('[STT warm] connection ready',flush=True)
      async for message in ws:
       if message.type!=aiohttp.WSMsgType.TEXT:continue
       event=message.json();kind=event.get('message_type');active=self.active
       if kind and (kind.endswith('_error') or kind in ('error','quota_exceeded','invalid_request','rate_limited','auth_error','transcriber_error','unaccepted_terms','resource_exhausted','insufficient_audio_activity')):raise LiveError('provider_'+kind)
       if not active:continue
       meta,final,committed=active
       if kind=='partial_transcript':
        meta['partial_events']+=1
        if event.get('text') and time.monotonic()-meta.get('_display_at',0)>=.25:
         from display_event import publish
         publish('partial_transcript',event['text']);meta['_display_at']=time.monotonic()
        if event.get('text') and 'first_partial_ms' not in meta:meta['first_partial_ms']=round((time.perf_counter()-meta['_start'])*1000,1)
       elif kind=='committed_transcript':
        committed.append(event.get('text','').strip())
        if 'commit_at' in meta and not final.done():final.set_result(' '.join(t for t in committed if t).strip())
   except asyncio.CancelledError:raise
   except Exception as error:
    print('[STT warm] reconnect:',type(error).__name__,flush=True)
   finally:
    self.ready.clear();self.ws=None
    if self.active and not self.active[1].done():self.active[1].set_exception(LiveError('session_closed_before_commit'))
   if not self.closed:await asyncio.sleep(1)
 async def close(self):
  self.closed=True
  if self.task:self.task.cancel();await asyncio.gather(self.task,return_exceptions=True)
 async def transcribe(self,queue,processing,meta):
  async with self.lock:
   start=time.perf_counter();was_ready=self.ready.is_set()
   await asyncio.wait_for(self.ready.wait(),6)
   ws=self.ws
   final=asyncio.get_running_loop().create_future()
   meta.update(sent_audio_seconds=0.,chunks_sent=0,partial_events=0,session_ready_ms=round((time.perf_counter()-start)*1000,1),connection_reused=was_ready,connection_number=self.connections,session_config=self.config,_start=start)
   self.active=(meta,final,[]);success=False
   try:
    async for item in audio_chunks(queue,processing,meta):
     if final.done():await final
     if len(item)%2:raise LiveError('unaligned_pcm')
     await ws.send_json({'message_type':'input_audio_chunk','audio_base_64':base64.b64encode(item).decode(),'sample_rate':16000})
     meta['chunks_sent']+=1;meta['sent_audio_seconds']+=len(item)/32000
    meta['commit_at']=time.perf_counter()
    await ws.send_json({'message_type':'input_audio_chunk','audio_base_64':'','sample_rate':16000,'commit':True})
    result=await asyncio.wait_for(asyncio.shield(final),8)
    meta['commit_to_final_ms']=round((time.perf_counter()-meta['commit_at'])*1000,1);success=True
    return result
   finally:
    self.active=None;meta.pop('_start',None)
    if not final.done():final.cancel()
    elif not final.cancelled():final.exception()
    # Interrupted/uncommitted audio must never enter the next turn.
    if not success:
     self.ready.clear();await ws.close()

def start_warm(stt):
 if getattr(stt,'_warm_stt',None) is None:
  stt._warm_stt=WarmConnection(stt._eleven_key);stt._warm_stt.start()
 return stt._warm_stt

async def transcribe(queue,key,processing,meta):
 # Standalone callers retain a bounded lifecycle.
 warm=WarmConnection(lambda:key);warm.start()
 try:return await warm.transcribe(queue,processing,meta)
 finally:await warm.close()

async def listen(stt,mic,silence_threshold=200,speech_threshold=400,silence_seconds=.8,max_seconds=25.,speech_start_timeout=20.,on_recorded=None):
 stt.last_audio=b'';stt.last_started_at=None;stt.last_error=None;stt.last_realtime=None
 mode=getattr(stt,'listening_mode',None);generation=mode.generation if mode else None
 if mode and mode.muted:return ''
 muted=lambda:bool(mode and (mode.muted or mode.generation!=generation))
 queue=asyncio.Queue(maxsize=96);request=None;pause=asyncio.create_task(mode.wait_muted()) if mode else None
 frames=[];pre=deque(maxlen=3);speech=False;quiet=0.;waited=0.;duration=0.;meta={'model':'scribe_v2_realtime','language':'eng','started_at':time.time()};endpoint=None
 try:
  processing,source=cleanup_enabled();meta.update(processing=processing,microphone=source,silence_threshold_seconds=silence_seconds)
  from adaptive_audio import Gate
  gate=Gate(mic,speech_threshold,silence_threshold)
  warm=start_warm(stt)
  mic.drain();print('[STT] Listening via ElevenLabs live',flush=True)
  while duration<max_seconds:
   read=asyncio.create_task(mic.read())
   try:
    waits={read}
    if request:waits.add(request)
    if pause:waits.add(pause)
    done,_=await asyncio.wait(waits,timeout=3,return_when=asyncio.FIRST_COMPLETED)
    if not done:raise LiveError('microphone_timeout')
    if muted():stt.last_audio=b'';return ''
    if request and request in done:
     await request;raise LiveError('unexpected_early_final')
    chunk=await read
   finally:
    if not read.done():read.cancel();await asyncio.gather(read,return_exceptions=True)
   seconds=len(chunk)/16000;duration+=seconds;raw=chunk.tobytes();rms=float(np.sqrt(np.mean(chunk.astype(np.float32)**2)))
   starts,continues=gate.decide(chunk)
   if not speech:
    pre.append(raw);waited+=seconds
    if starts:
     speech=True;frames.extend(pre);stt.last_started_at=time.monotonic()-sum(map(len,pre))/32000
     request=asyncio.create_task(warm.transcribe(queue,processing,meta))
     for item in pre:queue.put_nowait(item)
    elif waited>=speech_start_timeout:break
    continue
   frames.append(raw)
   try:queue.put_nowait(raw)
   except asyncio.QueueFull:raise LiveError('audio_upload_backpressure')
   quiet=quiet+seconds if not continues else 0.
   if quiet>=silence_seconds:break
  if muted():return ''
  stt.last_audio=b''.join(frames);endpoint=time.perf_counter()
  if on_recorded:await on_recorded()
  if not speech:return ''
  try:queue.put_nowait(None)
  except asyncio.QueueFull:raise LiveError('audio_upload_backpressure')
  waits={request}
  if pause:waits.add(pause)
  done,_=await asyncio.wait(waits,timeout=12,return_when=asyncio.FIRST_COMPLETED)
  if muted():stt.last_audio=b'';return ''
  if request not in done:raise LiveError('final_transcript_timeout')
  text=await request
  if muted():return ''
  meta['post_speech_ms']=round((time.perf_counter()-endpoint)*1000,1)
  print('[STT cleanup]',json.dumps({'applied':processing,'streaming':True,'elapsed_ms':0. if not processing else None,'input_samples':len(stt.last_audio)//2,'microphone':source,'reason':'live_filter' if processing else 'disabled'}),flush=True)
  print('[Latency] STT request (elevenlabs scribe_v2_realtime finalization): '+str(round(meta['post_speech_ms']/1000,3))+'s',flush=True)
  print('[STT] HA transcript:',repr(text),flush=True)
  if text:stt._forward(text)
  return text
 except asyncio.CancelledError:raise
 except Exception as error:
  stt.last_error=str(error) if isinstance(error,LiveError) else type(error).__name__;meta['error']=stt.last_error
  print('[STT] ElevenLabs live request failed:',stt.last_error,flush=True);return ''
 finally:
  tasks=[t for t in (request,pause) if t]
  for task in tasks:
   if not task.done():task.cancel()
  await asyncio.gather(*tasks,return_exceptions=True)
  if muted():stt.last_audio=b''
  if 'gate' in locals():meta['capture_gate']=gate.last
  meta.pop('commit_at',None);meta['sent_audio_seconds']=round(meta.get('sent_audio_seconds',0),3);stt.last_realtime=meta
  print('[STT realtime]',json.dumps(meta,sort_keys=True),flush=True)
