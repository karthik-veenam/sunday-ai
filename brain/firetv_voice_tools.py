"""Sunday-to-native-Alexa handoff through HA's paired audio relay."""
import asyncio,base64,json,time
from pathlib import Path
import aiohttp
from output import current_output
PROJECTOR='switch.16amp_smart_plug_2_socket_1'
REMOTE='remote.karthik_s_fire_tv'
FIRETV_POWER='switch.wifi_socket_socket_3'
BOOT_SECONDS=30
READY_POLLS=15
_lock=asyncio.Lock()
_sleep=asyncio.sleep
DEFINITIONS=[{'name':'ask_fire_tv','description':
 'Send a short playback/search/app request to native Alexa on the RoKa projector Fire TV. '
 'Use this for requests to watch/play/find movies, shows, videos, or open Fire TV apps on the projector. '
 'For continue/resume requests, phrase the utterance as Resume the requested title from where I left off, including the app only if known; preserve any requested season/episode. Never substitute a plain Play command or invent a saved episode/position. Do not claim the app will honor resume without observing it. '
 'Only dispatch for the current playback request or an explicit retry; discussing or complaining about a previous action is not permission to replay it. '
 'Pass a concise Alexa utterance preserving requested title, app and qualifiers. This tool checks projector power, turns it on if off, waits for cold boot, wakes Fire TV if needed, then delivers native audio. '
 'Do not separately toggle power or discover device IDs first. Routine home controls still use home_action. '
 'If a title is ambiguous, use conversation context; do not invent titles. Not for configuring Sunday, unrelated Alexa skills, purchases or household-wide actions. '
 'Cold starts can take a minute; give one short natural acknowledgment when appropriate. '
 'Once this tool reports handed_to_alexa, finish your reply. Do not call it again to check playback or because playback_confirmed is false; false means unobserved, not failed. '
 'A successful handoff confirms delivery to Alexa, not successful playback. Never claim the requested show is playing without a fresh playback observation.',
 'input_schema':{'type':'object','properties':{'request':{'type':'string','minLength':1,'maxLength':240}},'required':['request'],'additionalProperties':False}}]
async def synthesize(text):
 # Reuse the configured Sunday voice, without playing the clip in the room.
 c=json.loads(Path('/home/djpi/sunday/tts_provider.json').read_text())
 entries=json.loads(Path('/home/pi/homeassistant/.storage/core.config_entries').read_text())['data']['entries']
 key=next(e['data']['api_key'] for e in entries if e['domain']=='elevenlabs')
 voice=c['voice_id'];body={'text':text,'model_id':c.get('model_id','eleven_v4_turbo'),'language_code':'en','voice_settings':{'stability':.5,'similarity_boost':.75}}
 async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25)) as session:
  async with session.post('https://api.elevenlabs.io/v1/text-to-speech/'+voice+'/stream',params={'output_format':'pcm_16000'},headers={'xi-api-key':key},json=body) as response:
   if response.status!=200:raise RuntimeError('Alexa speech preparation failed')
   pcm=await response.read()
 if not pcm or len(pcm)%2 or len(pcm)>480000:raise RuntimeError('Alexa request audio exceeds limit')
 return pcm
class Channel:
 def __init__(self,ha):self.ha=ha;self.session=None;self.ws=None;self.i=0
 async def __aenter__(self):
  self.session=aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
  try:
   self.ws=await self.session.ws_connect(self.ha.url+'/api/websocket');await self.ws.receive_json()
   await self.ws.send_json({'type':'auth','access_token':self.ha.token})
   if (await self.ws.receive_json()).get('type')!='auth_ok':raise RuntimeError('HA audio relay authentication failed')
   return self
  except BaseException:await self.session.close();raise
 async def call(self,kind,**data):
  self.i+=1;ident=self.i;await self.ws.send_json({'id':ident,'type':'firetv_lan/'+kind,**data})
  async with asyncio.timeout(10):
   while True:
    result=await self.ws.receive_json()
    if result.get('id')==ident:
     if not result.get('success'):raise RuntimeError('HA audio relay: '+result.get('error',{}).get('code','failed'))
     return result.get('result')
 async def __aexit__(self,*args):
  if self.ws:await self.ws.close()
  if self.session:await self.session.close()
async def deliver(ha,pcm):
 async with Channel(ha) as channel:
  opened=await channel.call('voice_start',entity_id=REMOTE);sid=opened['session_id'];sent=0
  try:
   for i in range(0,len(pcm),2560):
    chunk=pcm[i:i+2560]
    await channel.call('voice_audio',session_id=sid,audio=base64.b64encode(chunk).decode());sent+=len(chunk)
    await _sleep(len(chunk)/32000)
   await channel.call('voice_audio',session_id=sid,audio=base64.b64encode(bytes(6400)).decode());await _sleep(.2)
  finally:await channel.call('voice_stop',session_id=sid)
 return sent
async def execute(inputs):
 output=current_output.get()
 previous=getattr(output,"firetv_handoff_receipt",None) if output else None
 if previous:
  return {**previous,"duplicate_blocked":True,"note":"Already attempted audio delivery in this turn. No additional request was sent. Finish your reply; do not retry without a new explicit user request."}
 import home_tools
 ha=getattr(home_tools._model,'ha',None)
 if not ha or not ha.actions_enabled:return {'status':'disabled','delivered_to_alexa':False}
 if _lock.locked():return {'status':'busy','delivered_to_alexa':False,'note':'Another Fire TV request is already being handled.'}
 async with _lock:
  powered=False;dispatch_started=False
  try:
   async with asyncio.timeout(115):
    state=await ha.get_state(PROJECTOR)
    if state.get('state') not in ('on','off'):return {'status':'projector_power_unknown','delivered_to_alexa':False}
    # Prepare before changing household power, so a TTS failure has no side effects.
    pcm=await synthesize(inputs['request'])
    stick_power=await ha.get_state(FIRETV_POWER)
    stick_started=stick_power.get('state')=='off'
    if stick_started:
     if await ha.call_service('switch','turn_on',{'entity_id':FIRETV_POWER})!='ok':return {'status':'fire_tv_power_not_confirmed','delivered_to_alexa':False}
    if state['state']=='off':
     if await ha.call_service('switch','turn_on',{'entity_id':PROJECTOR})!='ok':return {'status':'projector_power_not_confirmed','delivered_to_alexa':False}
     powered=True;await _sleep(BOOT_SECONDS)
     if (await ha.get_state(PROJECTOR)).get('state')!='on':return {'status':'projector_not_ready','delivered_to_alexa':False}
    if stick_started and state['state']=='on':await _sleep(30)
    remote=await ha.get_state(REMOTE)
    if remote.get('state') in ('unavailable','unknown','off'):
     if await ha.call_service('remote','turn_on',{'entity_id':REMOTE})!='ok':return {'status':'fire_tv_not_ready','delivered_to_alexa':False,'projector_powered_on':powered}
     for _ in range(READY_POLLS):
      if (await ha.get_state(REMOTE)).get('state')=='on':break
      await _sleep(2)
     else:return {'status':'fire_tv_not_ready','delivered_to_alexa':False,'projector_powered_on':powered}
    dispatch_started=True
    if output:output.firetv_handoff_receipt={'status':'handoff_outcome_unknown','delivered_to_alexa':None,'playback_confirmed':False}
    sent=await deliver(ha,pcm)
    result={'status':'handed_to_alexa','delivered_to_alexa':True,'projector_powered_on':powered,'audio_seconds':round(sent/32000,2),'playback_confirmed':False,'note':'Request sent once to Alexa. Do not retry. Playback is unobserved, not failed.'}
    if output:output.firetv_handoff_receipt=result
    return result
  except asyncio.CancelledError:raise
  except Exception as error:
   return {'status':'handoff_outcome_unknown' if dispatch_started else 'not_delivered','delivered_to_alexa':None if dispatch_started else False,'projector_powered_on':powered,'error_type':type(error).__name__,'note':'Do not retry automatically after audio dispatch; Alexa may already have acted.'}
