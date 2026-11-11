import unittest,json
import test_fast_mode as fixtures
entity=fixtures.entity

class FullModeTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.PlanningTests.asyncSetUp
    asyncTearDown=fixtures.PlanningTests.asyncTearDown
    async def full(self,text):
        from output import TurnOutput,current_output
        async def emit(e):pass
        import time
        o=TurnOutput(str(time.time_ns()),emit);tok=current_output.set(o)
        try:return await self.f.attempt(self.m,text,'full',o.request_id,'test',True,False,'all')
        finally:current_output.reset(tok)
    async def test_full_group_exclusion_execution(self):
        r=await self.full('all lights except Moon on');self.assertTrue(r['handled']);self.assertEqual(r['model_calls'],0)
        self.assertEqual({e for c in self.m.ha.calls for e in c[2]['entity_id']},{'light.b','light.c'})
    async def test_full_exception_execution(self):
        r=await self.full('all lights off but leave Moon on');self.assertTrue(r['handled']);self.assertEqual(len(self.m.ha.calls),1);self.assertEqual(self.m.ha.calls[0][2]['entity_id'],['light.a'])
    async def test_full_multiple_execution(self):
        r=await self.full('Moon on and Cove on');self.assertTrue(r['handled']);self.assertEqual(len(self.m.ha.calls),2)
    async def test_full_relative_execution(self):
        r=await self.full('increase fan speed');self.assertTrue(r['handled']);self.assertEqual(self.m.ha.calls[0][2]['percentage'],60)
    async def test_full_toggle(self):
        r=await self.full('toggle Moon');self.assertTrue(r['handled']);self.assertEqual(self.m.ha.calls[0][1],'turn_on')
    async def test_full_cover(self):
        e=entity('blind','Window','cover');e['attributes']={'device_class':'blind','supported_features':3};self.m.entities[e['entity_id']]=e
        r=await self.full('open Window');self.assertTrue(r['handled']);self.assertEqual(self.m.ha.calls[0][1],'open_cover')
    async def test_full_media_volume(self):
        e=entity('player','Stereo','media_player');e['attributes']={'supported_features':4};self.m.entities[e['entity_id']]=e
        r=await self.full('Stereo volume 20%');self.assertTrue(r['handled']);self.assertEqual(self.m.ha.calls[0][2]['volume_level'],.2)
    async def test_full_program(self):
        e=entity('program','Relax','script');self.m.entities[e['entity_id']]=e
        r=await self.full('run Relax');self.assertTrue(r['handled']);self.assertEqual(self.m.ha.calls[0][0],'script')
    async def test_full_climate_mode(self):
        r=await self.full('AC mode cool');self.assertTrue(r['handled']);self.assertEqual(self.m.ha.calls[0][2]['hvac_mode'],'cool')
    async def test_full_live_query(self):
        r=await self.full('is the fan on?');self.assertTrue(r['handled']);self.assertFalse(self.m.ha.calls);self.assertIn('off',r['reply'])
    async def test_full_temperature_query(self):
        e=entity('temp','Room Temperature','sensor');e['state']='28.5';e['attributes']={'device_class':'temperature','unit_of_measurement':'°C'};self.m.entities[e['entity_id']]=e
        r=await self.full("what's the room temperature?");self.assertTrue(r['handled']);self.assertIn('28.5°C',r['reply']);self.assertFalse(self.m.ha.calls)
    async def test_full_brightness_query(self):
        self.m.entities['light.a']['attributes']['brightness']=51
        r=await self.full("what's Moon brightness?");self.assertTrue(r['handled']);self.assertIn('20%',r['reply'])
    async def test_full_climate_temperature_is_reading(self):
        self.m.entities['climate.t']['state']='cool';self.m.entities['climate.t']['attributes']['current_temperature']=27
        r=await self.full("what's AC temperature?");self.assertTrue(r['handled']);self.assertIn('27',r['reply']);self.assertNotIn('cool°',r['reply'])
    async def test_full_invalid_temperature_query_falls_back(self):
        r=await self.full("what's fan temperature?");self.assertEqual(r['decision'],'FALLBACK');self.assertFalse(r.get('handled'))
    async def test_full_failure_never_continues(self):
        async def fail(*a):self.m.ha.calls.append(a);return 'unknown'
        self.m.ha.call_service=fail
        r=await self.full('Moon on and Cove on');self.assertTrue(r['handled']);self.assertEqual(len(self.m.ha.calls),1);self.assertEqual(r['response_class'],'execution_failure')

    async def test_all_devices_synonyms_share_everything_universe(self):
        from fast_mode import parse,Planner
        expected=await Planner(self.m).plan(parse('everything off'))
        for text in ('Turn all devices off','turn off all the devices','every device off','all devices here off','all devices in our room off'):
            with self.subTest(text=text):
                result=await Planner(self.m).plan(parse(text))
                self.assertEqual(result,expected)
        self.assertFalse(self.m.ha.calls)
    async def test_all_devices_exclusion_execution(self):
        r=await self.full('all devices except AC off')
        self.assertTrue(r['handled']);self.assertEqual(r['model_calls'],0)
        self.assertNotIn('climate.t',[e for a in r['plan'] for e in a['entity_ids']])
    async def test_all_devices_preserves_assistant_exclusion(self):
        from fast_mode import parse,Planner
        e=entity('speaker','Assistant','media_player');e['device_id']='assistant'
        satellite=entity('sat','Assistant','assist_satellite');satellite['device_id']='assistant'
        self.m.entities[e['entity_id']]=e;self.m.entities[satellite['entity_id']]=satellite
        plan,_,_=await Planner(self.m).plan(parse('all devices off'))
        self.assertNotIn(e['entity_id'],[a['entity_ids'][0] for a in plan])
