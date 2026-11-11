"""Authenticated alias editing, preserving verifier settings and matching semantics."""
import asyncio,hashlib,hmac,json,os,re,tempfile,time
from pathlib import Path
from alias_usage import AliasUsage
from aiohttp import web,ClientSession,ClientTimeout
CONFIG=Path('/home/djpi/sunday/wake_verifier.json')
ENV=Path('/home/djpi/sunday/wake_verifier.env')
AUTH=Path('/home/djpi/sunday2-rebuild/config.json')
AUDIT=Path('/home/djpi/sunday-verifier-notifications/alias-changes.jsonl')
def atomic(path,text):
 fd,name=tempfile.mkstemp(prefix='.'+path.name,dir=path.parent)
 try:
  with os.fdopen(fd,'w') as f:f.write(text);f.flush();os.fsync(f.fileno())
  os.chmod(name,0o644);os.replace(name,path)
 finally:
  if os.path.exists(name):os.unlink(name)
def normalize(value):
 if not isinstance(value,str) or not value.strip() or len(value)>80:raise ValueError('Enter a short alias, up to 80 characters.')
 if not re.fullmatch(r'[A-Za-z0-9\s.,!?\x27-]+',value):raise ValueError('Use English letters, numbers and spaces.')
 value=' '.join(re.sub('[^a-z0-9]+',' ',value.casefold()).split())
 if len(value)<2:raise ValueError('Enter at least two letters or numbers.')
 return value
async def reload_verifier():
 p=await asyncio.create_subprocess_exec('sudo','-n','systemctl','restart','sunday-verifier.service',stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
 try:code=await asyncio.wait_for(p.wait(),15)
 except BaseException:
  if p.returncode is None:p.kill();await p.wait()
  raise
 if code:raise RuntimeError('Verifier reload failed')
 async with ClientSession(timeout=ClientTimeout(total=1)) as session:
  for _ in range(16):
   try:
    async with session.get('http://127.0.0.1:8183/health') as r:
     if r.status==200 and (await r.json()).get('status')=='ok':return
   except Exception:pass
   await asyncio.sleep(.25)
 raise RuntimeError('Verifier did not become healthy')
import sys
if '/home/djpi/sunday-sherpa-verifier' not in sys.path:sys.path.insert(0,'/home/djpi/sunday-sherpa-verifier')
import tuning as sherpa_tuning
class AliasManager:
 def __init__(self,reload=reload_verifier):self.lock=asyncio.Lock();self.reload=reload;self.usage=AliasUsage()
 def snapshot(self):
  c=json.loads(CONFIG.read_text());aliases=c['aliases']
  return {'verifier_enabled':c.get('enabled',True) is not False,'sherpa_tuning':sherpa_tuning.read(),'provider':c.get('provider','whisper'),'usage':self.usage.snapshot(),'aliases':aliases,'audio_ctx':c.get('audio_ctx',128),'timeout_s':c.get('timeout_s',1.5),'revision':hashlib.sha256(json.dumps(aliases).encode()).hexdigest(),'matching':'Contains the phrase as whole words; case and punctuation ignored.'}
 async def add(self,value):
  alias=normalize(value)
  async with self.lock:
   old=CONFIG.read_text();c=json.loads(old)
   if alias in c['aliases']:return {**self.snapshot(),'changed':False,'message':'Already in the alias list.'}
   if len(c['aliases'])>=128:raise ValueError('The alias list is full.')
   previous_env=ENV.read_text() if ENV.exists() else None
   c['aliases'].append(alias)
   try:
    atomic(ENV,'SUNDAY_ALIASES="'+'|'.join(c['aliases'])+'"\n')
    atomic(CONFIG,json.dumps(c,indent=2)+'\n')
    await self.reload()
   except BaseException:
    atomic(CONFIG,old)
    if previous_env is None:ENV.unlink(missing_ok=True)
    else:atomic(ENV,previous_env)
    try:await self.reload()
    except Exception:pass
    raise
   with AUDIT.open('a') as f:f.write(json.dumps({'at':time.time(),'action':'add','alias':alias})+'\n')
   return {**self.snapshot(),'changed':True,'message':'Alias added and active.'}
 async def remove(self,value):
  alias=normalize(value)
  if alias=='sunday':raise ValueError('Sunday is the core wake word. You can remove the alternative aliases.')
  async with self.lock:
   old=CONFIG.read_text();c=json.loads(old)
   if alias not in c['aliases']:return {**self.snapshot(),'changed':False,'message':'Alias is already removed.'}
   previous_env=ENV.read_text() if ENV.exists() else None
   c['aliases']=[a for a in c['aliases'] if a!=alias]
   if not c['aliases']:raise ValueError('Keep the core Sunday wake word.')
   try:
    atomic(ENV,'SUNDAY_ALIASES="'+'|'.join(c['aliases'])+'"\n')
    atomic(CONFIG,json.dumps(c,indent=2)+'\n')
    await self.reload()
   except BaseException:
    atomic(CONFIG,old)
    if previous_env is None:ENV.unlink(missing_ok=True)
    else:atomic(ENV,previous_env)
    try:await self.reload()
    except Exception:pass
    raise
   with AUDIT.open('a') as f:f.write(json.dumps({'at':time.time(),'action':'remove','alias':alias})+'\n')
   return {**self.snapshot(),'changed':True,'message':'Alias removed. Applies to the next wake attempt.'}
 async def set_provider(self,provider):
  targets={'whisper':'http://127.0.0.1:8183','sherpa':'http://127.0.0.1:8184'}
  if provider not in targets:raise ValueError('Choose whisper or sherpa.')
  async with self.lock:
   async with ClientSession(timeout=ClientTimeout(total=2)) as client:
    async with client.get(targets[provider]+'/health') as response:
     if response.status!=200 or (await response.json()).get('status')!='ok':raise ValueError('Selected verifier is not ready; current verifier unchanged.')
   c=json.loads(CONFIG.read_text());c.update(provider=provider,url=targets[provider]+'/inference',mode='enforce');atomic(CONFIG,json.dumps(c,indent=2)+'\n')
   try:
    with AUDIT.open('a') as f:f.write(json.dumps({'at':time.time(),'action':'set_provider','provider':provider})+'\n')
   except OSError:pass
   return {**self.snapshot(),'message':provider.title()+' selected. Applies to the next wake; verification remains mandatory.'}
 async def set_tuning(self,boost,threshold):
  values=sherpa_tuning.validate(boost,threshold)
  async with self.lock:
   token=json.loads(AUTH.read_text())['daemon_token']
   async with ClientSession(timeout=ClientTimeout(total=25)) as client:
    async with client.post('http://127.0.0.1:8184/settings',headers={'Authorization':'Bearer '+token},json=values) as response:
     result=await response.json()
     if response.status!=200 or not result.get('ok'):raise ValueError(result.get('error','Tuning was not applied.'))
   with AUDIT.open('a') as f:f.write(json.dumps({'at':time.time(),'action':'set_sherpa_tuning',**values})+'\n')
   return {**self.snapshot(),'message':'Sherpa tuning applied. New wake attempts use these settings; verifier selection is unchanged.'}
 async def set_enabled(self,value):
  if value not in ('on','off'):raise ValueError('Verifier setting must be on or off.')
  async with self.lock:
   c=json.loads(CONFIG.read_text());c['enabled']=value=='on'
   atomic(CONFIG,json.dumps(c,indent=2)+'\n')
   try:
    with AUDIT.open('a') as f:f.write(json.dumps({'at':time.time(),'action':'set_verifier_enabled','enabled':c['enabled']})+'\n')
   except OSError:pass
   return {**self.snapshot(),'message':'Wake verifier '+value.upper()+'. Applies to the next wake attempt.'}
 async def set_context(self,value):
  if isinstance(value,bool) or not isinstance(value,int) or not 32<=value<=1500:raise ValueError('Audio context must be a whole number from 32 to 1500.')
  async with self.lock:
   c=json.loads(CONFIG.read_text())
   if c.get('audio_ctx',128)==value:return {**self.snapshot(),'changed':False,'message':'Audio context is already set to '+str(value)+'.'}
   c['audio_ctx']=value;atomic(CONFIG,json.dumps(c,indent=2)+'\n')
   try:
    with AUDIT.open('a') as f:f.write(json.dumps({'at':time.time(),'action':'set_context','audio_ctx':value})+'\n')
   except OSError:pass
   return {**self.snapshot(),'changed':True,'message':'Audio context saved. Applies to the next wake attempt.'}
def create_app(manager=None,token=None):
 manager=manager or AliasManager()
 @web.middleware
 async def auth(request,handler):
  expected=token if token is not None else json.loads(AUTH.read_text()).get('daemon_token','')
  supplied=request.headers.get('Authorization','').removeprefix('Bearer ')
  if not expected or not hmac.compare_digest(supplied,expected):raise web.HTTPUnauthorized()
  return await handler(request)
 async def handle(request):
  try:
   data=await request.json()
   if not isinstance(data,dict) or set(data)-{'action','alias','audio_ctx','provider','boost','threshold'}:raise ValueError('Invalid request.')
   action=data.get('action','list')
   if action=='list':return web.json_response(await asyncio.to_thread(manager.snapshot))
   if action=='set_sherpa_tuning':return web.json_response(await manager.set_tuning(data.get('boost'),data.get('threshold')))
   if action=='set_provider':return web.json_response(await manager.set_provider(data.get('provider')))
   if action=='set_verifier_enabled':return web.json_response(await manager.set_enabled(data.get('alias')))
   if action=='set_context':return web.json_response(await manager.set_context(data.get('audio_ctx')))
   if action=='add':return web.json_response(await manager.add(data.get('alias')))
   if action=='remove':return web.json_response(await manager.remove(data.get('alias')))
   raise ValueError('Unknown action.')
  except (ValueError,TypeError) as e:return web.json_response({'error':str(e)},status=400)
  except Exception:return web.json_response({'error':'Could not apply the alias. Previous settings restored.'},status=503)
 import sys
 if '/home/djpi/sunday-interaction-debug' not in sys.path:sys.path.insert(0,'/home/djpi/sunday-interaction-debug')
 from collector import Collector
 collector=Collector();debug_lock=asyncio.Lock()
 async def debug(request):
  async with debug_lock:
   try:return web.json_response(await asyncio.to_thread(collector.snapshot))
   except Exception:return web.json_response({'error':'Debug snapshot unavailable; Sunday is unaffected.'},status=503)
 if '/home/djpi/sunday' not in sys.path:sys.path.insert(0,'/home/djpi/sunday')
 from negative_dataset import Dataset
 dataset=Dataset()
 dataset_lock=asyncio.Lock()
 async def negatives(request):
  try:
   data=await request.json()
   if not isinstance(data,dict) or set(data)-{'action','id','offset','review'}:raise ValueError('Invalid request.')
   action=data.get('action','list')
   async with dataset_lock:
    if action=='list':result=await asyncio.to_thread(dataset.listing,data.get('offset',0),20,data.get('review','all'))
    elif action=='audio':result=await asyncio.to_thread(dataset.audio,data.get('id'))
    elif action=='review':result=await asyncio.to_thread(dataset.review,data.get('id'),data.get('review'))
    elif action=='delete':result=await asyncio.to_thread(dataset.delete,data.get('id'))
    else:raise ValueError('Unknown action.')
   return web.json_response(result)
  except (ValueError,TypeError) as e:return web.json_response({'error':str(e)},status=400)
  except Exception:return web.json_response({'error':'Negative dataset unavailable; wake processing is unaffected.'},status=503)
 app=web.Application(middlewares=[auth],client_max_size=4096);app.router.add_post('/v1/wake-aliases',handle);app.router.add_post('/v1/interaction-debug',debug);app.router.add_post('/v1/wake-negatives',negatives);return app
