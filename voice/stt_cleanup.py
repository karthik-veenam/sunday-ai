"""Mild local cleanup of completed command PCM; never processes the wake stream."""
import asyncio,json,time
from pathlib import Path
CONFIG=Path('/home/djpi/sunday/stt_audio_cleanup.json')
SOURCE_CONFIG=Path('/home/djpi/sunday-alive/stt_cleanup.json')
MIC_CONFIG=Path('/home/djpi/sunday-alive/microphone.json')
# Preserve speech bandwidth; no resampling, silence trimming or automatic gain boost.
FILTER='highpass=f=80,afftdn=nr=6:nf=-50:tn=1,alimiter=limit=0.95:level=false:latency=true'
async def clean(pcm):
 started=time.perf_counter();meta={'applied':False,'profile':'mild-v1','input_samples':len(pcm)//2}
 try:
  if SOURCE_CONFIG.exists():
   config=json.loads(SOURCE_CONFIG.read_text())
   source=json.loads(MIC_CONFIG.read_text()).get('source','body') if MIC_CONFIG.exists() else 'body'
   meta['microphone']=source
   enabled=config.get('sources',{}).get(source,False) is True
  else:
   config=json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
   enabled=config.get('enabled') is True
  if not enabled:return pcm,{**meta,'reason':'disabled','elapsed_ms':round((time.perf_counter()-started)*1000,1)}
  if len(pcm)%2 or len(pcm)<3200:return pcm,{**meta,'reason':'short_or_invalid_pcm'}
  process=await asyncio.create_subprocess_exec('ffmpeg','-hide_banner','-loglevel','error','-threads','1','-filter_threads','1','-f','s16le','-ar','16000','-ac','1','-i','pipe:0','-af',FILTER,'-f','s16le','-ar','16000','-ac','1','pipe:1',stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
  try:
   output,error=await asyncio.wait_for(process.communicate(pcm),timeout=2)
  except BaseException:
   if process.returncode is None:process.kill()
   await process.communicate()
   raise
  if process.returncode or len(output)!=len(pcm):raise RuntimeError('Cleanup output failed format/length validation')
  meta.update(applied=True,output_samples=len(output)//2,elapsed_ms=round((time.perf_counter()-started)*1000,1))
  return output,meta
 except asyncio.CancelledError:raise
 except Exception as error:
  return pcm,{**meta,'reason':type(error).__name__,'elapsed_ms':round((time.perf_counter()-started)*1000,1)}
