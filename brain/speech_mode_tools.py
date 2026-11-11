"""Model-selected speech language mode; affects the next utterance only."""
import asyncio,json,os,tempfile
from pathlib import Path
CONFIG=Path('/home/djpi/sunday-alive/stt_streaming.json')
_lock=asyncio.Lock()
DEFINITIONS=[{'name':'set_telugu_mode','description':
 'Switch Sunday speech recognition mode. Telugu mode on uses accurate complete-audio Scribe with a Telugu hint; off restores live English STT. '
 'Use on when the user wants to speak Telugu; use off for an explicit off/English request or a clear return to English conversation. '
 'Do not switch off for occasional English words, device names, quoted speech or an ambiguous short acknowledgment in a Telugu conversation. '
 'Choose semantically from the conversation; never claim a change before successful tool confirmation. Changes affect the next utterance. status reads current mode.',
 'input_schema':{'type':'object','properties':{'action':{'type':'string','enum':['on','off','status']}},'required':['action'],'additionalProperties':False}}]
def read():
 try:
  d=json.loads(CONFIG.read_text())
  if not isinstance(d,dict):raise ValueError('Invalid speech configuration')
  return d
 except FileNotFoundError:return {'version':1,'enabled':True}
def status():
 d=read();live=d.get('enabled') is True;tel=d.get('telugu_mode') is True and not live
 return {'telugu_mode':tel,'recognition':'live' if live else 'after_speech','language_hint':'tel' if tel else 'eng'}
def context():
 return 'Speech conversation mode: '+json.dumps(status())+'. Use set_telugu_mode when the user requests Telugu or clearly returns to English. Keep Telugu mode for mixed Telugu-English speech; occasional English words are not a language switch. Mode changes apply to the next utterance.'
async def execute(inputs):
 action=inputs.get('action')
 if action not in ('on','off','status'):return {'ok':False,'error':'Invalid action'}
 async with _lock:
  try:
   if action!='status':
    d=read();d.update(enabled=action=='off',telugu_mode=action=='on')
    fd,path=tempfile.mkstemp(prefix='.speech-mode-',dir=CONFIG.parent)
    try:
     with os.fdopen(fd,'w') as f:json.dump(d,f);f.flush();os.fsync(f.fileno())
     os.replace(path,CONFIG)
    finally:
     if os.path.exists(path):os.unlink(path)
   return {'ok':True,**status(),'effective':'next_utterance'}
  except (OSError,ValueError):return {'ok':False,'error':'Speech mode could not be read or saved'}
