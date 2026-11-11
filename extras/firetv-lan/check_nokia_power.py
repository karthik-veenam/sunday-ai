import asyncio,sys
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
sys.path.insert(0,'/config')
from custom_components.firetv_lan import media_player as m
class Client:
 available=True;apps={}
 def __init__(self):self.events=[]
 async def send(self,c):self.events.append(c)
 async def wake_service(self):self.events.append('wake_service')
 async def refresh(self):pass
class Services:
 def __init__(self):self.calls=[]
 async def async_call(self,*args,**kwargs):self.calls.append((args,kwargs))
class States:
 state='on'
 def get(self,k):return SimpleNamespace(state=self.state)
async def main():
 c=Client();e=SimpleNamespace(entry_id='test',runtime_data=c,data={'host':'192.0.2.1'},options={'tv_remote':'remote.nokia','tv_media_player':'media_player.nokia'})
 player=m.FirePlayer(e);player.hass=SimpleNamespace(states=States(),services=Services());player.async_write_ha_state=lambda:None
 await player.async_toggle();assert player._attr_state==m.MediaPlayerState.OFF and not c.events
 assert player.hass.services.calls[-1][0]==('remote','send_command',{'entity_id':'remote.nokia','command':'POWER'})
 await player.async_update();assert player._attr_state==m.MediaPlayerState.OFF
 with patch.object(m.asyncio,'sleep',AsyncMock()) as pause:
  await player.async_toggle()
  assert c.events==['wake_service','sleep','wake_service','home']
  assert [call.args[0] for call in pause.await_args_list]==[1,4,2]
 assert player._attr_state==m.MediaPlayerState.IDLE
 # Repeated taps while the entire wake sequence is running do not enqueue another cycle.
 c.events=[];player._attr_state=m.MediaPlayerState.OFF
 await player._power_lock.acquire()
 try:await player.async_toggle();await player.async_turn_on();await player.async_turn_off()
 finally:player._power_lock.release()
 assert not c.events and len(player.hass.services.calls)==1
 player.hass.states.state='unavailable';player._last_power_at=0
 await player.async_update();assert player._attr_state==m.MediaPlayerState.OFF
 with patch.object(m.asyncio,'sleep',AsyncMock()):await player.async_toggle()
 assert c.events==['wake_service','sleep','wake_service','home']
 await player.async_media_play_pause();assert c.events[-1]=='play_pause'
 print('PASS: Nokia Off; exact verified standby/wake/Home order and delays; stale status; repeated-tap guard; offline Nokia wake; Fire TV playback. No actual commands.')
asyncio.run(main())
