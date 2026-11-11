"""Pre-render the configured wake chime once; send PCM directly to the local audio gateway."""
import asyncio,json,subprocess,time

def audit(trace,stage,**extra):
 if trace:
  now=time.monotonic_ns()
  print('[Wake audit] '+json.dumps({**trace,'stage':stage,'at_ns':now,'since_accepted_ms':round((now-trace['accepted_ns'])/1e6,3),**extra}),flush=True)

class CachedWakeChime:
 def __init__(self,path,connector=asyncio.open_connection):
  self.connector=connector
  self.pcm=subprocess.run(['ffmpeg','-v','error','-i',path,'-f','s16le','-ar','22050','-ac','1','pipe:1'],capture_output=True,check=True,timeout=5).stdout
  if not self.pcm or len(self.pcm)>22050*2*5:raise ValueError('invalid wake chime length')
  print('[Wake chime] cached PCM: '+str(len(self.pcm))+' bytes',flush=True)
 async def play(self,trace):
  writer=None
  try:
   audit(trace,'direct_connect')
   reader,writer=await asyncio.wait_for(self.connector('127.0.0.1',8772),2)
   header={'rate':22050,'channels':1,'fmt':'S16_LE','wake_trace':trace}
   writer.write((json.dumps(header)+'\n').encode());writer.write(self.pcm)
   await writer.drain();writer.write_eof()
   audit(trace,'direct_audio_sent',bytes=len(self.pcm))
   reply=await asyncio.wait_for(reader.readline(),5)
   if reply.strip()!=b'OK':raise RuntimeError('gateway did not acknowledge wake chime')
   audit(trace,'direct_return',status='acknowledged')
  except Exception as error:
   # No retry after a possible send: an uncertain acknowledgement must not duplicate the chime.
   audit(trace,'direct_failure',error=type(error).__name__)
  finally:
   if writer:
    writer.close();await writer.wait_closed()
