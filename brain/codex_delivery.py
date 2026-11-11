"""Authenticated completion callback. Voice policy and durable no-replay receipt."""
import json
import aiohttp
from aiohttp import web
import codex_tools
async def voice_ready():
 try:
  async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2)) as session:
   async with session.get('http://127.0.0.1:8773/health') as response:
    h=await response.json()
    return h.get('ok') is True and not h.get('playing')
 except (aiohttp.ClientError,TimeoutError,ValueError):return False
async def send_voice(runtime,message):
 # Reuse Sunday's configured TTS and body speaker, rather than HA pipeline TTS.
 async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=75)) as session:
  async with session.post('http://127.0.0.1:8080/announce',json={'token':runtime.config.compatibility_token,'channel':'voice','message':message}) as response:
   return 'ok' if response.status==200 else 'unknown'
async def deliver(runtime,ident):
 with codex_tools.connect() as db:row=db.execute('SELECT * FROM jobs WHERE id=?',(ident,)).fetchone()
 if not row:raise web.HTTPNotFound()
 if row['delivery']!='pending':return {'status':row['delivery']}
 if runtime.active or not runtime.config.outbound_speech_enabled:raise web.HTTPConflict(text='Voice is busy or disabled.')
 satellite=runtime.config.default_satellite
 state=await runtime.ha.get_state(satellite)
 if state.get('state')!='idle' or runtime.active or not await voice_ready():raise web.HTTPConflict(text='Voice is busy or unavailable.')
 if runtime.active:raise web.HTTPConflict(text='A new conversation just started.')
 # Commit before dispatch. An ambiguous timeout must not repeat the announcement.
 with codex_tools.connect() as db:
  changed=db.execute("UPDATE jobs SET delivery='sending' WHERE id=? AND delivery='pending'",(ident,)).rowcount
 if not changed:return {'status':'already_claimed'}
 runtime.active[satellite]='codex:'+ident
 try:
  result=await send_voice(runtime,json.loads(row['result'])['speech'])
  status='sent' if result=='ok' else 'unknown'
 except Exception:status='unknown'
 finally:runtime.active.pop(satellite,None)
 with codex_tools.connect() as db:db.execute('UPDATE jobs SET delivery=? WHERE id=?',(status,ident))
 return {'status':status}
