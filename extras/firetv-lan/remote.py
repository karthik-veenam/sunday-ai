from homeassistant.components.remote import RemoteEntity
from homeassistant.helpers.device_registry import DeviceInfo
async def async_setup_entry(hass,entry,async_add_entities):async_add_entities([FireRemote(entry)])
class FireRemote(RemoteEntity):
 _attr_has_entity_name=True
 _attr_name=None
 _attr_should_poll=True
 _attr_is_on=True
 def __init__(self,entry):
  self.client=entry.runtime_data
  self._attr_unique_id=entry.entry_id+'_remote'
  self._attr_device_info=DeviceInfo(identifiers={('firetv_lan',entry.data['host'])},name=entry.data['name'],manufacturer='Amazon',model='Fire TV (Vega OS)')
 @property
 def available(self):return self.client.available
 async def async_update(self):await self.client.refresh()
 async def async_send_command(self,command,**kwargs):
  for _ in range(kwargs.get('num_repeats',1)):
   for item in command:await self.client.send(item)
 async def async_turn_on(self,**kwargs):
  await self.client.wake_service();await self.client.send('home')
 async def async_turn_off(self,**kwargs):await self.client.send('sleep')
