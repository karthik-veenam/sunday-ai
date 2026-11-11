import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from . import Client
class Flow(config_entries.ConfigFlow,domain='firetv_lan'):
 VERSION=1
 @staticmethod
 @callback
 def async_get_options_flow(config_entry):return PowerOptions()
 async def async_step_user(self,user_input=None):
  errors={}
  if user_input:
   await self.async_set_unique_id(user_input['host'])
   self._abort_if_unique_id_configured()
   client=Client(self.hass,user_input)
   response=await client.refresh()
   if response is None:errors['base']='cannot_connect'
   else:return self.async_create_entry(title=user_input.get('name','Rani Fire TV'),data=user_input)
  return self.async_show_form(step_id='user',data_schema=vol.Schema({vol.Required('host'):str,vol.Required('token'):str,vol.Required('name',default='Rani Fire TV'):str}),errors=errors)

class PowerOptions(config_entries.OptionsFlow):
 async def async_step_init(self,user_input=None):
  if user_input is not None:return self.async_create_entry(title='',data=user_input)
  return self.async_show_form(step_id='init',data_schema=vol.Schema({vol.Optional('tv_media_player'):str,vol.Optional('tv_remote'):str}))
