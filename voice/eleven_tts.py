"""ElevenLabs PCM generation; playback stays with Sunday's existing audio backend."""
import json,re,time
from pathlib import Path
import aiohttp
CONFIG=Path('/home/djpi/sunday/tts_provider.json')
ENTRIES=Path('/home/pi/homeassistant/.storage/core.config_entries')
from voice_delivery import TAGS, TAG_RE, plain, delivery, tags_in
def settings():
 try:
  c=json.loads(CONFIG.read_text())
  if c.get('provider') not in ('ha','elevenlabs'):raise ValueError('Unknown TTS provider')
  return c
 except FileNotFoundError:return {'provider':'ha'}

class ElevenTTS:
 def __init__(self):self.session=None;self.key=None;self.key_at=0
 def api_key(self):
  if self.key and time.monotonic()-self.key_at<300:return self.key
  entries=json.loads(ENTRIES.read_text())['data']['entries']
  self.key=next(e['data']['api_key'] for e in entries if e['domain']=='elevenlabs');self.key_at=time.monotonic();return self.key
 async def synthesize_pcm(self,text,c):
  text=delivery(text,c.get('audio_tags',False))
  if not plain(text):return b''
  voice=c.get('voice_id','')
  if not re.fullmatch(r'[A-Za-z0-9]{10,80}',voice):raise ValueError('Invalid ElevenLabs voice ID')
  if self.session is None or self.session.closed:self.session=aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=40,connect=5,sock_read=20))
  body={'text':text,'model_id':c.get('model_id','eleven_v4_turbo'),'language_code':c.get('language_code','en'),'voice_settings':{'stability':c.get('stability',.5),'similarity_boost':c.get('similarity_boost',.75)}}
  print('[Speech delivery] '+json.dumps({'model':body['model_id'],'tags':tags_in(text),'stability':body['voice_settings']['stability']},separators=(',',':')),flush=True)
  started=time.perf_counter();chunks=[];first=None;size=0
  async with self.session.post('https://api.elevenlabs.io/v1/text-to-speech/'+voice+'/stream',params={'output_format':'pcm_22050'},headers={'xi-api-key':self.api_key()},json=body) as response:
   if response.status!=200:raise RuntimeError('ElevenLabs TTS HTTP '+str(response.status))
   async for chunk in response.content.iter_chunked(16384):
    if first is None:first=time.perf_counter()-started
    size+=len(chunk)
    if size>4*1024*1024:raise RuntimeError('ElevenLabs audio exceeds body playback limit')
    chunks.append(chunk)
  pcm=b''.join(chunks)
  if not pcm or len(pcm)%2:raise RuntimeError('ElevenLabs returned invalid PCM audio')
  print(f'[Latency] TTS elevenlabs {body["model_id"]}: first_audio={first:.3f}s total={time.perf_counter()-started:.3f}s audio={len(pcm)/44100:.3f}s',flush=True)
  return pcm
 async def close(self):
  if self.session is not None:await self.session.close()
