"""Fire TV phone-remote protocol, independent of developer mode."""
import asyncio,time
from urllib.parse import quote
import aiohttp
from homeassistant.const import Platform
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
NAV={'up':'dpad_up','down':'dpad_down','left':'dpad_left','right':'dpad_right','select':'select','home':'home','back':'back','menu':'menu','volume_up':'volume_up','volume_down':'volume_down','mute':'mute','sleep':'sleep','power':'power'}
class RemoteServiceStopped(HomeAssistantError):
 """A connection could not be established; no command was delivered."""
class Client:
 def __init__(self,hass,data):
  self.hass=hass;self.host=data['host'];self.token=data['token'];self.session=async_get_clientsession(hass,verify_ssl=False)
  self.available=True;self.apps={};self._lock=asyncio.Lock();self._poll_lock=asyncio.Lock()
  self._last_refresh=0;self._last_apps=None;self._auth_failed=False
 def headers(self):return {'X-Api-Key':'0987654321','X-Client-Token':self.token,'User-Agent':'okhttp/4.10.0'}
 async def request(self,path,method='POST',payload=None):
  try:
   async with self.session.request(method,'https://'+self.host+':8080'+path,headers=self.headers(),json=payload,timeout=aiohttp.ClientTimeout(total=6)) as r:
    if r.status in [401,403]:
     self.available=False;self._auth_failed=True
     raise HomeAssistantError('Fire TV pairing expired; pair the remote again')
    if not 200<=r.status<300:raise HomeAssistantError('Fire TV rejected the request (HTTP '+str(r.status)+')')
    self.available=True;self._auth_failed=False
    if not await r.text():return None
    try:return await r.json(content_type=None)
    except ValueError:return None
  except aiohttp.ClientConnectorError as err:
   # A refused TCP connection means no command was delivered, so waking and retrying is safe.
   raise RemoteServiceStopped('Fire TV phone-remote service is stopped') from err
  except (aiohttp.ClientError,asyncio.TimeoutError) as err:
   self._last_refresh=0
   raise HomeAssistantError('Fire TV command connection failed') from err
 async def wake_service(self):
  try:
   async with self.session.request('POST','http://'+self.host+':8009/apps/FireTVRemote',timeout=aiohttp.ClientTimeout(total=5)) as r:
    if r.status not in [200,201,204]:raise HomeAssistantError('Fire TV remote service did not start')
  except (aiohttp.ClientError,asyncio.TimeoutError) as err:
   self.available=False;self._last_refresh=0
   raise HomeAssistantError('Cannot reach Fire TV') from err
  deadline=time.monotonic()+12
  while True:
   try:
    await self.request('/v1/FireTV/appsV2',method='GET');return
   except RemoteServiceStopped:
    if time.monotonic()>=deadline:raise HomeAssistantError('Fire TV remote service is taking too long to start')
    await asyncio.sleep(.7)
 async def send(self,command):
  if command in NAV:
   path='/v1/FireTV?action='+NAV[command];payload={'keyActionType':'keyDownUp'}
  elif command=='play_pause':path='/v1/media?action=play';payload=None
  elif command in ['rewind','fast_forward']:
   path='/v1/media?action=scan';payload={'direction':'back' if command=='rewind' else 'forward','keyAction':{'keyActionType':'keyDownUp'}}
  elif command.startswith('app:') and command[4:]:path='/v1/FireTV/app/'+quote(command[4:],safe='');payload=None
  else:raise HomeAssistantError('Unsupported Fire TV command: '+command)
  async with self._lock:
   try:await self.request(path,payload=payload)
   except RemoteServiceStopped:
    await self.wake_service()
    await self.request(path,payload=payload)
 async def refresh(self):
  # Presence is distinct from the phone-remote service running. Polling never wakes the screen.
  async with self._poll_lock:
   if self._last_refresh and time.monotonic()-self._last_refresh<15:return self._last_apps
   try:
    response=await self.request('/v1/FireTV/appsV2',method='GET')
    if isinstance(response,list):self.apps={a['name']:a['appId'] for a in response if isinstance(a,dict) and 'name' in a and 'appId' in a}
    self._last_apps=response
   except HomeAssistantError:
    if self._auth_failed:self.available=False
    else:
     try:
      async with self.session.request('GET','http://'+self.host+':8009/apps/FireTVRemote',timeout=aiohttp.ClientTimeout(total=4)) as r:
       self.available=r.status==200 and 'FireTVRemote' in await r.text()
     except (aiohttp.ClientError,asyncio.TimeoutError):self.available=False
   self._last_refresh=time.monotonic()
   return self._last_apps
async def async_setup_entry(hass,entry):
 entry.runtime_data=Client(hass,entry.data)
 await hass.config_entries.async_forward_entry_setups(entry,[Platform.REMOTE,Platform.MEDIA_PLAYER])
 return True
async def async_unload_entry(hass,entry):return await hass.config_entries.async_unload_platforms(entry,[Platform.REMOTE,Platform.MEDIA_PLAYER])
