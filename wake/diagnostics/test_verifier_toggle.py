import asyncio,json,unittest
from unittest.mock import Mock,AsyncMock
from test_verifier_strict import Tests as StrictTests,load
from types import SimpleNamespace
class ToggleTests(StrictTests):
 async def test_explicit_off_bypasses_without_audio_or_http(self):
  self.v.CONFIG.write_text(json.dumps({'enabled':False,'aliases':['sunday']}));self.v._call=Mock();self.v.window=Mock(side_effect=AssertionError('must not capture'))
  r=await self.verify();self.assertTrue(r['accepted']);self.assertEqual(r['record']['mode'],'bypass');self.assertEqual(r['record']['latency_ms'],0);self.v.window.assert_not_called();self.v._call.assert_not_called();self.ns['enqueue_negative'].assert_not_called()
 async def test_string_false_does_not_bypass(self):
  self.v.CONFIG.write_text(json.dumps({'enabled':'false','aliases':['sunday']}));self.v._call=Mock();r=await self.verify();self.assertFalse(r['accepted']);self.v._call.assert_not_called()
 async def test_bad_config_with_off_still_blocks(self):
  self.v.CONFIG.write_text(json.dumps({'enabled':False,'aliases':[]}));self.v._call=Mock();self.assertFalse((await self.verify())['accepted']);self.v._call.assert_not_called()
 async def test_live_config_change_applies_same_verifier_instance(self):
  self.v.CONFIG.write_text(json.dumps({'enabled':False,'aliases':['sunday']}));self.assertTrue((await self.verify())['accepted'])
  self.v.CONFIG.write_text(json.dumps({'enabled':True,'post_s':0,'timeout_s':.1,'aliases':['sunday']}));self.v._call=Mock(return_value={'text':'TV chatter','stop':'none','latency_ms':1});self.assertFalse((await self.verify())['accepted']);self.v._call.assert_called_once()
 async def test_detector_waits_for_verified_candidate(self):
  ticks=iter(range(0,100,3));clock=SimpleNamespace(monotonic=lambda:next(ticks),monotonic_ns=lambda:123456)
  D,_=load('WakeWordDetector',time=clock);d=D.__new__(D);d.model_path='/fake/sunday';d.threshold=.99;d._refractory_until=0;d._flush=lambda:None;d._process_chunk=lambda a:1;d.feedback=None
  d.verifier=SimpleNamespace(verify=AsyncMock(side_effect=[{'accepted':False,'record':{}},{'accepted':True,'record':{'latency_ms':1}}]))
  mic=SimpleNamespace(drain=Mock(),read=AsyncMock(side_effect=[self.pcm,self.pcm]));await d.wait_for_wake_word(mic);self.assertEqual(d.verifier.verify.await_count,2)
if __name__=='__main__':unittest.main()
