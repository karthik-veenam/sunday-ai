import unittest,json
import test_fast_mode as fixtures
from output import TurnOutput,current_output

class GroupLatencyTests(unittest.IsolatedAsyncioTestCase):
 asyncSetUp=fixtures.PlanningTests.asyncSetUp
 asyncTearDown=fixtures.PlanningTests.asyncTearDown
 async def run_fast(self,text):
  async def emit(e):pass
  o=TurnOutput('group-latency',emit);token=current_output.set(o)
  try:return await self.f.attempt(self.m,text,'group',o.request_id,'voice',True,False,'all')
  finally:current_output.reset(token)
 async def test_all_lights_one_dispatch_only_targets_changes(self):
  for e in self.m.entities.values():
   if e['domain']=='light':e['state']='on'
  self.m.entities['light.b']['state']='off'
  r=await self.run_fast('all lights off')
  self.assertEqual(r['model_calls'],0);self.assertEqual(len(self.m.ha.calls),1)
  self.assertEqual(set(self.m.ha.calls[0][2]['entity_id']),{'light.a','light.c'})
  self.assertEqual(len(r['plan']),3);self.assertEqual(len(r['dispatch_plan']),1)
  self.assertIn('execution',r['stage_ms'])
 async def test_exclusions_preserved_in_single_dispatch(self):
  r=await self.run_fast('all lights except Moon on')
  self.assertEqual(len(self.m.ha.calls),1)
  self.assertEqual(set(self.m.ha.calls[0][2]['entity_id']),{'light.b','light.c'})
 async def test_already_correct_group_sends_nothing(self):
  r=await self.run_fast('all lights off')
  self.assertFalse(self.m.ha.calls);self.assertEqual(r['execution'][0]['status'],'already_correct')
 async def test_fresh_unavailable_target_blocks_entire_group(self):
  original=self.m.ha.get_state
  async def changed(e):
   value=dict(await original(e))
   if e=='light.b':value['state']='unavailable'
   return value
  self.m.ha.get_state=changed
  r=await self.run_fast('all lights on')
  self.assertFalse(self.m.ha.calls);self.assertEqual(r['response_class'],'rejection')
 async def test_failed_bulk_never_retries_or_falls_back(self):
  async def fail(*args):self.m.ha.calls.append(args);return 'unknown'
  self.m.ha.call_service=fail
  r=await self.run_fast('all lights on')
  self.assertEqual(len(self.m.ha.calls),1);self.assertTrue(r['handled']);self.assertEqual(r['response_class'],'execution_failure')
 async def test_mixed_and_ordered_operations_stay_separate(self):
  r=await self.run_fast('Moon on and Cove on')
  self.assertEqual(len(self.m.ha.calls),2);self.assertEqual(len(r['dispatch_plan']),2)
 async def test_sequenced_facade_dispatches_even_when_already_off(self):
  self.m.household['always_dispatch_entities']=['light.a']
  r=await self.run_fast('all lights off')
  self.assertEqual(len(self.m.ha.calls),1)
  self.assertEqual(self.m.ha.calls[0][2]['entity_id'],['light.a'])
 async def test_facade_opt_in_does_not_bypass_fresh_unavailable_guard(self):
  self.m.household['always_dispatch_entities']=['light.a']
  original=self.m.ha.get_state
  async def offline(e):
   value=dict(await original(e))
   if e=='light.a':value['state']='unavailable'
   return value
  self.m.ha.get_state=offline
  await self.run_fast('Moon off')
  self.assertFalse(self.m.ha.calls)
if __name__=='__main__':unittest.main()
