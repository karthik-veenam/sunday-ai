"""Loopback-only Sherpa verifier. Saved/live request audio stays in memory."""
import asyncio,io,json,re,time,wave,hmac,os
import tuning
from pathlib import Path
import numpy as np
import sentencepiece as sp
import sherpa_onnx
from aiohttp import web
ROOT=Path('/home/djpi/sunday-sherpa-verifier')
MODEL=Path('/home/djpi/sunday-verifier-research-20261027/sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01')
CONFIG=Path('/home/djpi/sunday/wake_verifier.json')
MODEL_ID='sherpa-gigaspeech-3.3M-int8'
def normalize(s):return ' '.join(re.sub('[^a-z0-9]+',' ',s.casefold()).split())
class Engine:
 def __init__(self,score=3,threshold=.1,max_active_paths=None):
  if max_active_paths is None:max_active_paths=int(os.environ.get("SUNDAY_SHERPA_MAX_ACTIVE_PATHS","12"))
  if isinstance(max_active_paths,bool) or not isinstance(max_active_paths,int) or max_active_paths<1:raise ValueError("max_active_paths must be a positive integer")
  self.max_active_paths=max_active_paths
  values=tuning.validate(score,threshold);self.score=values['boost'];self.threshold=values['threshold']
  self.spm=sp.SentencePieceProcessor(model_file=str(MODEL/'bpe.model'))
  self.kws=sherpa_onnx.KeywordSpotter(tokens=str(MODEL/'tokens.txt'),encoder=str(MODEL/'encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx'),decoder=str(MODEL/'decoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx'),joiner=str(MODEL/'joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx'),keywords_file=str(ROOT/'keywords.txt'),num_threads=1,keywords_score=self.score,keywords_threshold=self.threshold,max_active_paths=self.max_active_paths)
  self.previous=None;self.extra='';self.labels={};self.aliases=[]
 def update_aliases(self):
  aliases=json.loads(CONFIG.read_text())['aliases']
  if not isinstance(aliases,list) or not aliases or any(not isinstance(a,str) or not normalize(a) for a in aliases):raise ValueError('Invalid aliases')
  aliases=[normalize(a) for a in aliases]
  if aliases!=self.previous:
   self.labels={'SUNDAY':'sunday'};lines=[]
   for i,a in enumerate(aliases):
    if a=='sunday':continue
    label='ALIAS_'+str(i);lines.append(' '.join(self.spm.encode(a.upper(),out_type=str))+' @'+label);self.labels[label]=a
   self.extra='/'.join(lines);self.previous=aliases;self.aliases=aliases
 def infer(self,pcm):
  self.update_aliases();started=time.perf_counter();stream=self.kws.create_stream(self.extra or None)
  stream.accept_waveform(16000,pcm.astype(np.float32)/32768);stream.accept_waveform(16000,np.zeros(10560,np.float32));stream.input_finished();alias=None
  while self.kws.is_ready(stream):
   self.kws.decode_stream(stream);hit=self.kws.get_result(stream)
   if hit:
    alias=self.labels.get(hit)
    if alias not in self.aliases:raise ValueError('Unknown keyword result')
    break
  return {'provider':'sherpa','model':MODEL_ID,'accepted':alias is not None,'matched_alias':alias,'text':'','keyword_result':'matched' if alias else 'no_match','compute_ms':round((time.perf_counter()-started)*1000,1)}
def decode_wav(data):
 with wave.open(io.BytesIO(data)) as w:
  if (w.getframerate(),w.getnchannels(),w.getsampwidth())!=(16000,1,2) or not 1<=w.getnframes()<=160000:raise ValueError('Expected 16 kHz mono PCM16, max 10 seconds')
  pcm=np.frombuffer(w.readframes(w.getnframes()),'<i2')
  if len(pcm)!=w.getnframes():raise ValueError('Truncated WAV')
  return pcm

def create_app(engine):
 lock=asyncio.Lock();settings_lock=asyncio.Lock();state={"engine":engine}
 async def health(request):return web.json_response({'status':'ok','provider':'sherpa','model':MODEL_ID,'score':state['engine'].score,'threshold':state['engine'].threshold,'max_active_paths':state['engine'].max_active_paths,'busy':lock.locked()})
 async def inference(request):
  if lock.locked():return web.json_response({'error':'Verifier busy'},status=503)
  try:
   reader=await request.multipart();audio=None
   async for field in reader:
    if field.name=='file':audio=bytes(await field.read())
   if audio is None:raise ValueError('Missing WAV')
   pcm=decode_wav(audio)
   async with lock:result=await asyncio.to_thread(state["engine"].infer,pcm)
   return web.json_response(result,headers={'X-Sunday-Early-Stop':'keyword:'+result['matched_alias'] if result['accepted'] else 'no_keyword'})
  except (ValueError,wave.Error,EOFError):return web.json_response({'error':'Invalid audio/configuration'},status=400)
  except Exception:return web.json_response({'error':'Sherpa inference unavailable'},status=503)
 async def configure(request):
  expected=json.loads(Path('/home/djpi/sunday2-rebuild/config.json').read_text())['daemon_token']
  supplied=request.headers.get('Authorization','').removeprefix('Bearer ')
  if not expected or not hmac.compare_digest(expected,supplied):raise web.HTTPUnauthorized()
  try:
   data=await request.json()
   if not isinstance(data,dict) or set(data)!={'boost','threshold'}:raise ValueError('Provide boost and threshold.')
   values=tuning.validate(data['boost'],data['threshold'])
   async with settings_lock:
    current=state['engine']
    if values=={'boost':current.score,'threshold':current.threshold}:
     tuning.save(values)
     return web.json_response({'ok':True,**values,'changed':False})
    def prepare():
     candidate=Engine(values['boost'],values['threshold'],current.max_active_paths);candidate.infer(np.zeros(28160,np.int16));return candidate
    candidate=await asyncio.to_thread(prepare) # Existing engine keeps serving while replacement warms.
    tuning.save(values)
    state['engine']=candidate
   return web.json_response({'ok':True,**values,'changed':True})
  except (ValueError,TypeError) as e:return web.json_response({'error':str(e)},status=400)
  except Exception:return web.json_response({'error':'Could not apply tuning; previous engine remains active.'},status=503)
 app=web.Application(client_max_size=512*1024);app.router.add_get('/health',health);app.router.add_post('/inference',inference);app.router.add_post('/settings',configure);return app
if __name__=='__main__':
 selected=tuning.read();engine=Engine(selected['boost'],selected['threshold']);engine.infer(np.zeros(28160,np.int16)) # Warm before exposing readiness; no verdicts/datasets.
 web.run_app(create_app(engine),host='127.0.0.1',port=8184,access_log=None)
