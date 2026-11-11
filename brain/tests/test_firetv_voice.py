import unittest
from unittest.mock import AsyncMock,patch
from types import SimpleNamespace as NS
import firetv_voice_tools as m
import home_tools
class Tests(unittest.IsolatedAsyncioTestCase):
 async def run_case(self,states,delivery=None,synth=None,stick="on"):
  values=iter(states)
  async def state(eid):return {"state":stick if eid==m.FIRETV_POWER else next(values)}
  ha=NS(actions_enabled=True,get_state=AsyncMock(side_effect=state),call_service=AsyncMock(return_value='ok'))
  with patch.object(home_tools,'_model',NS(ha=ha)),patch.object(m,'synthesize',synth or AsyncMock(return_value=bytes(3200))) as sy,patch.object(m,'deliver',delivery or AsyncMock(return_value=3200)) as de,patch.object(m,'_sleep',AsyncMock()) as sleep:
   result=await m.execute({'request':'Play a movie trailer on YouTube'})
  return result,ha,sy,de,sleep
 async def test_already_on_no_power_or_boot_delay(self):
  r,h,s,d,p=await self.run_case(['on','on']);self.assertTrue(r['delivered_to_alexa']);h.call_service.assert_not_awaited();p.assert_not_awaited();d.assert_awaited_once();self.assertFalse(r['playback_confirmed'])
 async def test_cold_boot_once_then_handoff(self):
  r,h,s,d,p=await self.run_case(['off','on','on']);self.assertTrue(r['delivered_to_alexa']);h.call_service.assert_awaited_once_with('switch','turn_on',{'entity_id':m.PROJECTOR});p.assert_awaited_once_with(30)
 async def test_unknown_power_no_action(self):
  r,h,s,d,p=await self.run_case(['unavailable']);self.assertFalse(r['delivered_to_alexa']);s.assert_not_awaited();d.assert_not_awaited();h.call_service.assert_not_awaited()
 async def test_synthesis_fails_before_house_actions(self):
  r,h,s,d,p=await self.run_case(['off'],synth=AsyncMock(side_effect=RuntimeError()));self.assertFalse(r['delivered_to_alexa']);h.call_service.assert_not_awaited();d.assert_not_awaited()
 async def test_failed_boot_no_handoff(self):
  r,h,s,d,p=await self.run_case(['off','off']);self.assertFalse(r['delivered_to_alexa']);d.assert_not_awaited()
 async def test_audio_failure_never_replays(self):
  r,h,s,d,p=await self.run_case(['on','on'],delivery=AsyncMock(side_effect=RuntimeError()));self.assertEqual(r['status'],'handoff_outcome_unknown');d.assert_awaited_once()
 async def test_wake_firetv_if_not_ready(self):
  r,h,s,d,p=await self.run_case(['on','unavailable','on']);self.assertTrue(r['delivered_to_alexa']);h.call_service.assert_awaited_once_with('remote','turn_on',{'entity_id':m.REMOTE})

 async def test_firetv_plug_off_is_enabled(self):
  r,h,s,d,p=await self.run_case(['on','on'],stick='off');self.assertTrue(r['delivered_to_alexa']);h.call_service.assert_awaited_once_with('switch','turn_on',{'entity_id':m.FIRETV_POWER})
 async def test_cold_start_checks_after_30_then_waits_for_remote(self):
  r,h,s,d,p=await self.run_case(['off','on','unavailable','unavailable','unavailable','on'])
  self.assertTrue(r['delivered_to_alexa'])
  self.assertEqual([a.args[0] for a in p.await_args_list],[30,2,2]);d.assert_awaited_once()
 async def test_remote_never_ready_sends_no_audio(self):
  r,h,s,d,p=await self.run_case(['on','unavailable']+['unavailable']*15)
  self.assertEqual(r['status'],'fire_tv_not_ready');d.assert_not_awaited()
 async def test_repeated_model_call_sends_once(self):
  token=m.current_output.set(NS())
  try:
   r,h,s,d,p=await self.run_case(['on','on'])
   self.assertTrue(r['delivered_to_alexa'])
   with patch.object(m,'synthesize',AsyncMock()) as sy,patch.object(m,'deliver',AsyncMock()) as de:
    duplicate=await m.execute({'request':'Same request rephrased'})
   self.assertTrue(duplicate['duplicate_blocked']);sy.assert_not_awaited();de.assert_not_awaited()
  finally:m.current_output.reset(token)
 async def test_uncertain_audio_dispatch_also_blocks_repetition(self):
  token=m.current_output.set(NS())
  try:
   r,h,s,d,p=await self.run_case(['on','on'],delivery=AsyncMock(side_effect=TimeoutError()))
   self.assertEqual(r['status'],'handoff_outcome_unknown')
   duplicate=await m.execute({'request':'Retry'})
   self.assertTrue(duplicate['duplicate_blocked']);d.assert_awaited_once()
  finally:m.current_output.reset(token)
 async def test_new_user_turn_can_retry(self):
  for _ in range(2):
   token=m.current_output.set(NS())
   try:
    r,h,s,d,p=await self.run_case(['on','on']);self.assertTrue(r['delivered_to_alexa']);d.assert_awaited_once()
   finally:m.current_output.reset(token)
