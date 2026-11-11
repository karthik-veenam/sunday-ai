"""Conversational model choice, independent of background model configuration."""
import asyncio,json,os,tempfile
from pathlib import Path
FILE=Path('/home/djpi/sunday-alive/reply_model.json')
CHOICES={'luna':{'label':'Luna (Fast)','model':'gpt-6-luna','provider':'openai'},'haiku':{'label':'Claude Haiku 5.5','model':'claude-haiku-5-5','provider':'anthropic'},'nano':{'label':'GPT-5.4 nano','model':'gpt-5.4-nano','provider':'openai'}}
GENERAL_SOURCES=frozenset(('voice','telegram','ha','lva'))
class ReplyModels:
 def __init__(self,config):
  self.config=config;self.lock=asyncio.Lock();self.selected='luna'
  try:
   data=json.loads(FILE.read_text())
   if data.get('selected') in CHOICES:self.selected=data['selected']
  except FileNotFoundError:pass
 def available(self,key):return bool(getattr(self.config,'anthropic_api_key' if CHOICES[key]['provider']=='anthropic' else 'openai_api_key',''))
 def status(self):return {'selected':self.selected,**CHOICES[self.selected],'scope':'general_replies_only','choices':[{'id':k,**v,'available':self.available(k)} for k,v in CHOICES.items()]}
 async def select(self,key):
  if key not in CHOICES:raise ValueError('Unknown reply model')
  if not self.available(key):raise ValueError('Credentials for this model are not configured')
  async with self.lock:
   def save():
    fd,name=tempfile.mkstemp(dir=FILE.parent,prefix='.reply-model-')
    try:
     with os.fdopen(fd,'w') as f:json.dump({'version':1,'selected':key},f);f.flush();os.fsync(f.fileno())
     os.replace(name,FILE)
    finally:
     if os.path.exists(name):os.unlink(name)
   await asyncio.to_thread(save);self.selected=key
  return self.status()
 def apply(self,agent,source):
  backend=getattr(agent,'_backend',None)
  if backend is None or not hasattr(backend,'_voice_model'):return
  if source in GENERAL_SOURCES:
   model=CHOICES[self.selected]['model'];backend._voice_model=model;backend._chat_model=model
  else:
   backend._voice_model=getattr(self.config,'openai_voice_model','') or 'gpt-6-luna';backend._chat_model=getattr(self.config,'openai_chat_model','') or 'gpt-6-luna'
