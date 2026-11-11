"""Fire TV controls with optional power routing to the attached television."""
import asyncio,time
from homeassistant.components.media_player import MediaPlayerEntity,MediaPlayerEntityFeature,MediaPlayerState,MediaPlayerDeviceClass
from homeassistant.helpers.device_registry import DeviceInfo
async def async_setup_entry(hass,entry,async_add_entities):async_add_entities([FirePlayer(entry)])
class FirePlayer(MediaPlayerEntity):
 _attr_has_entity_name=True
 _attr_name=None
 _attr_device_class=MediaPlayerDeviceClass.TV
 _attr_should_poll=True
 _attr_assumed_state=True
 _attr_state=MediaPlayerState.IDLE
 _attr_supported_features=MediaPlayerEntityFeature.TURN_ON|MediaPlayerEntityFeature.TURN_OFF|MediaPlayerEntityFeature.PLAY|MediaPlayerEntityFeature.PAUSE|MediaPlayerEntityFeature.SELECT_SOURCE|MediaPlayerEntityFeature.VOLUME_STEP
 def __init__(self,entry):
  self.entry=entry;self.client=entry.runtime_data
  self._last_power_at=0;self._power_lock=asyncio.Lock()
  self._attr_unique_id=entry.entry_id+'_media'
  self._attr_device_info=DeviceInfo(identifiers={('firetv_lan',entry.data['host'])})
 @property
 def source_list(self):return list(self.client.apps)
 @property
 def available(self):return self.client.available
 async def async_update(self):
  await self.client.refresh()
  if target:=self.entry.options.get('tv_media_player'):
   if self._power_lock.locked() or (self._last_power_at and time.monotonic()-self._last_power_at<30):return
   state=self.hass.states.get(target)
   self._attr_state=MediaPlayerState.IDLE if state and state.state not in ['off','unknown','unavailable'] else MediaPlayerState.OFF
 async def _turn_on(self):
  await self.client.wake_service()
  if self.entry.options.get('tv_media_player'):
   # Reproduce the physically verified standby/wake sequence. HOME alone
   # does not reliably re-assert HDMI-CEC while the stick is already awake.
   await asyncio.sleep(1)
   await self.client.send('sleep')
   await asyncio.sleep(4)
   await self.client.wake_service()
   await asyncio.sleep(2)
  await self.client.send('home')
  self._attr_state=MediaPlayerState.IDLE;self._last_power_at=time.monotonic();self.async_write_ha_state()
 async def _turn_off(self):
  if target:=self.entry.options.get('tv_remote'):
   await self.hass.services.async_call('remote','send_command',{'entity_id':target,'command':'POWER'},blocking=True)
  else:await self.client.send('sleep')
  self._attr_state=MediaPlayerState.OFF;self._last_power_at=time.monotonic();self.async_write_ha_state()
 async def async_turn_on(self):
  if self._power_lock.locked():return
  async with self._power_lock:await self._turn_on()
 async def async_turn_off(self):
  if self._power_lock.locked():return
  async with self._power_lock:await self._turn_off()
 async def async_toggle(self):
  if self._power_lock.locked():return
  async with self._power_lock:
   if self._attr_state==MediaPlayerState.OFF:
    await self._turn_on();return
   if target:=self.entry.options.get('tv_media_player'):
    state=self.hass.states.get(target)
    if state is None or state.state in ['off','unknown','unavailable']:
     await self._turn_on();return
   await self._turn_off()
 async def async_media_play_pause(self):await self.client.send('play_pause')
 async def async_media_play(self):await self.client.send('play_pause')
 async def async_media_pause(self):await self.client.send('play_pause')
 async def async_volume_up(self):await self.client.send('volume_up')
 async def async_volume_down(self):await self.client.send('volume_down')
 async def async_select_source(self,source):await self.client.send('app:'+self.client.apps.get(source,source))
